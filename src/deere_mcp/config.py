"""Settings: environment variables first, then ``config.toml``, then defaults."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from deere_mcp import paths

DEFAULT_BASE_URL = "https://sandboxapi.deere.com/platform"
DEFAULT_CALLBACK_PORT = 9090
DEFAULT_PAGE_CAP = 20
DEFAULT_LOG_LEVEL = "WARNING"

ALLOWED_HOSTS = frozenset(
    {"sandboxapi.deere.com", "partnerapi.deere.com", "equipmentapi.deere.com"}
)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
UOM_VALUES = ("ENGLISH", "METRIC")
YIELD_VALUES = ("VOLUME", "MASS")

# Setting name -> environment variable. Config file keys are the setting names.
ENV_NAMES: dict[str, str] = {
    "client_id": "DEERE_CLIENT_ID",
    "client_secret": "DEERE_CLIENT_SECRET",
    "base_url": "DEERE_BASE_URL",
    "uom": "DEERE_UOM",
    "yield": "DEERE_YIELD",
    "callback_port": "DEERE_CALLBACK_PORT",
    "page_cap": "DEERE_PAGE_CAP",
    "output_dir": "DEERE_OUTPUT_DIR",
    "log_level": "DEERE_LOG_LEVEL",
}


@dataclass(frozen=True)
class Settings:
    """Everything the server and the sign-in commands need, already validated."""

    client_id: str | None
    client_secret: str | None
    base_url: str
    uom: Literal["ENGLISH", "METRIC"] | None
    yield_pref: Literal["VOLUME", "MASS"] | None
    callback_port: int
    page_cap: int
    output_dir: Path
    token_path: Path
    config_path: Path
    log_level: str

    @property
    def redirect_uri(self) -> str:
        """The sign-in callback address that must be registered with the Deere application."""
        return f"http://localhost:{self.callback_port}/callback"


def is_allowed_host(host: str | None) -> bool:
    """True for John Deere API hosts: the three known ones and anything ending in .deere.com."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host in ALLOWED_HOSTS or host.endswith(".deere.com")


def read_config(path: Path) -> dict[str, Any]:
    """Read ``config.toml``; a missing file is an empty mapping. Raises ValueError if unreadable."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read the settings file {path}: {exc}") from exc
    return data


def _toml_string(value: str) -> str:
    out = ['"']
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return _toml_string(str(value))


def write_config(path: Path, values: Mapping[str, str]) -> None:
    """Write `values` as a flat TOML file, owner-only where supported, replacing the file."""
    lines = ["# Deere Ops Center MCP settings. Environment variables override these values."]
    for key, value in values.items():
        if value is None:
            continue
        if not key.replace("_", "").isalnum():
            raise ValueError(f"Not a valid setting name: {key!r}")
        lines.append(f"{key} = {_toml_value(value)}")
    paths.write_private_file(path, "\n".join(lines) + "\n")


class _Source:
    """Looks a setting up in the environment, then the config file, remembering where it was."""

    def __init__(self, env: Mapping[str, str], file_values: Mapping[str, Any], config_path: Path):
        self._env = env
        self._file = file_values
        self._config_path = config_path

    def get(self, key: str) -> tuple[str | None, str]:
        env_name = ENV_NAMES[key]
        raw_env = self._env.get(env_name)
        if raw_env is not None and raw_env.strip():
            return raw_env.strip(), env_name
        if key in self._file and self._file[key] is not None:
            raw = self._file[key]
            if isinstance(raw, (dict, list)):
                raise ValueError(f"'{key}' in {self._config_path} must be a single value.")
            text = str(raw).strip()
            if text:
                return text, f"'{key}' in {self._config_path}"
        return None, env_name


def _choice(value: str | None, label: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    upper = value.upper()
    if upper not in allowed:
        options = " or ".join(allowed)
        raise ValueError(f"{label} must be {options}, not {value!r}.")
    return upper


def _int(value: str | None, label: str, default: int, low: int, high: int, what: str) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{label} must be {what}, not {value!r}.") from None
    if not low <= number <= high:
        raise ValueError(f"{label} must be {what}, not {value!r}.")
    return number


def _base_url(value: str | None, label: str) -> str:
    if value is None:
        return DEFAULT_BASE_URL
    url = value.rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"{label} must be an https:// address, not {value!r}.")
    if not is_allowed_host(parts.hostname):
        raise ValueError(f"{label} must be a John Deere address (*.deere.com), not {value!r}.")
    if parts.query or parts.fragment:
        raise ValueError(f"{label} must not contain '?' or '#': {value!r}.")
    return url


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    config_path: Path | None = None,
    token_path: Path | None = None,
) -> Settings:
    """Build Settings from the environment, ``config.toml`` and defaults, in that order.

    Raises ValueError with a plain message when a value is invalid.
    """
    env = os.environ if env is None else env
    config_path = config_path if config_path is not None else paths.config_file()
    token_path = token_path if token_path is not None else paths.token_file()
    source = _Source(env, read_config(config_path), config_path)

    client_id, _ = source.get("client_id")
    client_secret, _ = source.get("client_secret")
    base_url = _base_url(*source.get("base_url"))
    uom = _choice(*source.get("uom"), UOM_VALUES)
    yield_pref = _choice(*source.get("yield"), YIELD_VALUES)
    port_value, port_label = source.get("callback_port")
    callback_port = _int(
        port_value, port_label, DEFAULT_CALLBACK_PORT, 1, 65535, "a whole number from 1 to 65535"
    )
    cap_value, cap_label = source.get("page_cap")
    page_cap = _int(
        cap_value, cap_label, DEFAULT_PAGE_CAP, 1, 10_000, "a whole number of 1 or more"
    )
    output_value, _ = source.get("output_dir")
    output_dir = (
        Path(output_value).expanduser() if output_value is not None else paths.default_output_dir()
    )
    log_level = _choice(*source.get("log_level"), LOG_LEVELS) or DEFAULT_LOG_LEVEL

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        uom=cast(Literal["ENGLISH", "METRIC"] | None, uom),
        yield_pref=cast(Literal["VOLUME", "MASS"] | None, yield_pref),
        callback_port=callback_port,
        page_cap=page_cap,
        output_dir=output_dir,
        token_path=token_path,
        config_path=config_path,
        log_level=log_level,
    )
