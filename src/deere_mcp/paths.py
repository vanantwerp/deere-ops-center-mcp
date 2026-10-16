"""Where the server keeps its settings, sign-in tokens and output files."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import platformdirs

APP_NAME = "deere-mcp"


def _make_private_dir(path: Path) -> Path:
    """Create `path` (and parents) if missing; the leaf gets mode 0o700 where supported."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def config_dir() -> Path:
    """The per-user settings folder, created on demand with owner-only permissions.

    macOS ``~/Library/Application Support/deere-mcp``, Windows ``%LOCALAPPDATA%\\deere-mcp``,
    Linux ``~/.config/deere-mcp``.
    """
    # On Windows this is %LOCALAPPDATA%\deere-mcp, not the roaming profile, so the secret and
    # tokens stay on this machine. macOS and Linux are unaffected by that choice.
    path = Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))
    return _make_private_dir(path)


def default_output_dir() -> Path:
    """The default folder files are saved into: a ``deere-mcp`` folder in Documents."""
    return Path(platformdirs.user_documents_dir()) / APP_NAME


def config_file() -> Path:
    """The ``config.toml`` file inside the settings folder."""
    return config_dir() / "config.toml"


def token_file() -> Path:
    """The ``tokens.json`` file inside the settings folder."""
    return config_dir() / "tokens.json"


def write_private_file(path: Path, text: str) -> None:
    """Atomically replace `path` with `text`, readable only by the owner where supported.

    The data goes to a temporary file in the same folder (created with mode 0o600), is
    flushed to disk, and is then renamed over the target, so a crash never leaves a
    half-written file behind.
    """
    if not path.parent.exists():
        _make_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
