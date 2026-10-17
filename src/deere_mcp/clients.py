"""Add the server to Claude Desktop and Claude Code, so nobody edits JSON by hand.

Nothing here prints; ``cli.py`` reports what happened. Failures raise ``ClientConfigError``
with a plain message (or, for Claude Code, come back as ``(False, message)``).

Claude Desktop's config file can hold other servers' secrets, so its contents are never
logged, printed or put in an error message. The file is changed in place (a temporary file
in the same folder, then ``os.replace``), keeps its permissions, and is backed up first.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

# A plain zip of the main branch: uvx installs it without git, which most Windows PCs lack.
DEFAULT_SOURCE = "https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip"
DEFAULT_NAME = "deere"
SOURCE_ENV = "DEERE_SOURCE"
CONFIG_FILENAME = "claude_desktop_config.json"
CLAUDE_CODE_TIMEOUT = 60.0

Which = Callable[[str], str | None]


class ClientConfigError(Exception):
    """An AI app could not be configured; the message says why and what to do instead."""


@dataclass(frozen=True)
class ServerEntry:
    """How an AI app starts the server: an absolute program path, its arguments, extra env."""

    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        """The ``mcpServers`` value for Claude Desktop; ``env`` is left out when empty."""
        data: dict[str, Any] = {"command": self.command, "args": list(self.args)}
        if self.env:
            data["env"] = dict(self.env)
        return data


@dataclass(frozen=True)
class WriteResult:
    """What ``write_claude_desktop_entry`` did to the file."""

    path: Path
    action: Literal["created", "updated", "unchanged"]
    backup: Path | None


def resolve_source(explicit: str | None, env: Mapping[str, str]) -> str:
    """Where ``uvx`` gets the server: the flag, else ``DEERE_SOURCE``, else ``DEFAULT_SOURCE``.

    A local folder (a development checkout) is allowed and is made absolute, because the AI
    app starts the server from its own working folder, not the one setup ran in.
    """
    for candidate in (explicit, env.get(SOURCE_ENV)):
        value = (candidate or "").strip()
        if not value:
            continue
        local = Path(value).expanduser()
        if _is_dir(local):
            return str(local.resolve())
        return value
    return DEFAULT_SOURCE


def build_entry(
    source: str,
    *,
    platform: str = sys.platform,
    which: Which = shutil.which,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> ServerEntry:
    """The entry that starts the server through ``uvx`` (or ``uv tool run``) from `source`.

    The program path is absolute: Claude Desktop does not see the shell's PATH. Symlinks are
    kept as they are (not resolved), so a package-manager upgrade of uv does not break it.

    ``uvx`` is looked for on PATH first, then beside the ``uv`` that started this process
    (``uv run`` sets the ``UV`` variable to its own path), then in uv's default install
    folder. A terminal whose PATH lacks uv's folder is common right after installing uv.
    """
    entry_env = {"PYTHONUTF8": "1"} if platform == "win32" else {}
    uvx = _find_uv_program("uvx", platform, which, env, home)
    if uvx:
        return ServerEntry(uvx, ["--from", source, "deere-mcp"], entry_env)
    uv = _find_uv_program("uv", platform, which, env, home)
    if uv:
        return ServerEntry(uv, ["tool", "run", "--from", source, "deere-mcp"], entry_env)
    raise ClientConfigError(
        "uv was not found, so no AI app was changed. If uv is installed, run this again from "
        "a terminal where `uv --version` works; otherwise install uv (see the README) and "
        "close and reopen this window."
    )


def _find_uv_program(
    name: str,
    platform: str,
    which: Which,
    env: Mapping[str, str] | None,
    home: Path | None,
) -> str | None:
    """Absolute path of ``uvx`` or ``uv``: PATH, then next to the running uv, then ~/.local/bin."""
    found = which(name)
    if found:
        return os.path.abspath(found)
    exe = ".exe" if platform == "win32" else ""
    environment = os.environ if env is None else env
    running_uv = environment.get("UV")
    candidates: list[Path] = []
    if running_uv:
        candidates.append(Path(running_uv).with_name(name + exe))
    base = Path.home() if home is None else home
    candidates.append(base / ".local" / "bin" / (name + exe))
    for candidate in candidates:
        if _is_file(candidate) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return None


def claude_desktop_config_path(
    *,
    platform: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    home: Path | None = None,
) -> Path | None:
    """Claude Desktop's ``claude_desktop_config.json``, or None when Claude Desktop is absent.

    Returns an existing file first (the most recently modified one when there are several),
    else the first candidate whose folder exists (installed, no config yet), else None.
    """
    candidates = _desktop_candidates(platform, env, Path.home() if home is None else home)
    existing = [path for path in candidates if _is_file(path)]
    if existing:
        # max() keeps the first of equal values, so ties go to the earlier candidate.
        return max(existing, key=_mtime)
    for path in candidates:
        if _is_dir(path.parent):
            return path
    return None


def _desktop_candidates(platform: str, env: Mapping[str, str], home: Path) -> list[Path]:
    if platform == "darwin":
        return [home / "Library" / "Application Support" / "Claude" / CONFIG_FILENAME]
    if platform == "win32":
        appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        candidates = [appdata / "Claude" / CONFIG_FILENAME]
        # Microsoft Store and WinGet installs keep the file in the package's private copy
        # of AppData (anthropics/claude-code#26073).
        packages = local / "Packages"
        if _is_dir(packages):
            with contextlib.suppress(OSError):
                for package in sorted(packages.glob("Claude_*")):
                    roaming = package / "LocalCache" / "Roaming"
                    candidates.append(roaming / "Claude" / CONFIG_FILENAME)
        return candidates
    return [home / ".config" / "Claude" / CONFIG_FILENAME]


def write_claude_desktop_entry(
    path: Path,
    entry: ServerEntry,
    *,
    name: str = DEFAULT_NAME,
    now: Callable[[], datetime] = datetime.now,
) -> WriteResult:
    """Add or replace ``mcpServers[name]`` in Claude Desktop's config, keeping everything else.

    A missing file is created. An existing file is backed up beside itself before it is
    changed, and is left untouched when it cannot be read or when nothing would change.
    """
    wanted = entry.as_json()
    if not path.exists() and not path.is_symlink():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(path, _dump({"mcpServers": {name: wanted}}))
        except OSError as exc:
            raise ClientConfigError(f"Could not create {path} ({_reason(exc)}).") from None
        return WriteResult(path, "created", None)

    data = _read_config(path, name)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ClientConfigError(
            f'In {path}, "mcpServers" is not a JSON object, so the file was left untouched. '
            + _fix_by_hand(name)
        )
    if servers.get(name) == wanted:
        return WriteResult(path, "unchanged", None)
    servers[name] = wanted

    try:
        backup = _backup(path, now())
    except OSError as exc:
        raise ClientConfigError(
            f"Could not back up {path} ({_reason(exc)}), so it was left untouched."
        ) from None
    try:
        _replace_file(path, _dump(data))
    except OSError as exc:
        raise ClientConfigError(
            f"Could not write {path} ({_reason(exc)}). The original is unchanged; "
            f"a copy is at {backup}."
        ) from None
    return WriteResult(path, "updated", backup)


def has_claude_desktop_entry(path: Path, *, name: str = DEFAULT_NAME) -> bool | None:
    """Whether the config at `path` has ``mcpServers[name]``; None when it cannot be read."""
    if not path.exists():
        return False
    try:
        data = _read_config(path, name)
    except ClientConfigError:
        return None
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and name in servers


def claude_code_available(
    which: Which = shutil.which,
    *,
    platform: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    home: Path | None = None,
) -> str | None:
    """The absolute path of the ``claude`` command (Claude Code), or None.

    PATH first. On Windows, the Code tab in Claude Desktop runs its own copy of Claude Code,
    which is not on PATH and does not pick up Claude Desktop's servers, so without this the
    server never reaches that tab. That copy lives in Claude Desktop's folder.
    """
    found = which("claude")
    if found:
        return os.path.abspath(found)
    if platform != "win32":
        return None
    base = Path.home() if home is None else home
    return _desktop_claude_code(_desktop_candidates(platform, env, base))


def _desktop_claude_code(config_paths: Sequence[Path]) -> str | None:
    """The newest ``claude.exe`` under ``<Claude Desktop folder>/claude-code``, or None.

    The layout is ``claude-code/<version>/<build>/claude.exe``; the version folder decides,
    then the newest file, and up to two folder levels are searched in case the layout moves.
    """
    found: list[tuple[tuple[int, ...], float, Path]] = []
    for config in config_paths:
        root = config.parent / "claude-code"
        if not _is_dir(root):
            continue
        with contextlib.suppress(OSError):
            for exe in [*root.glob("*/claude.exe"), *root.glob("*/*/claude.exe")]:
                if _is_file(exe):
                    version = exe.relative_to(root).parts[0]
                    found.append((_version_key(version), _mtime(exe), exe))
    if not found:
        return None
    return os.path.abspath(max(found, key=lambda item: item[:2])[2])


def _version_key(name: str) -> tuple[int, ...]:
    """``"2.1.299"`` as ``(2, 1, 299)``; a name that is not a version sorts first."""
    parts = name.split(".")
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else ()


def claude_code_add_command(
    entry: ServerEntry, *, name: str = DEFAULT_NAME, claude: str = "claude"
) -> list[str]:
    """The ``claude mcp add`` command that registers `entry` for every project (user scope).

    Checked against ``claude mcp add --help`` (Claude Code 2.1.299): ``-s/--scope``,
    ``-t/--transport`` (stdio is the default and is accepted), and ``-e/--env KEY=value``.
    ``--env`` takes several values, so it comes after the name and ``--`` ends it.
    """
    env_flags = [part for key, value in entry.env.items() for part in ("--env", f"{key}={value}")]
    return [
        claude,
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
        name,
        *env_flags,
        "--",
        entry.command,
        *entry.args,
    ]


def register_with_claude_code(
    entry: ServerEntry,
    *,
    name: str = DEFAULT_NAME,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    claude: str | None = None,
    typed: str = "claude",
) -> tuple[bool, str]:
    """Run ``claude mcp add``; return ``(ok, one-line message)``. Never raises.

    `claude` is the command's path (default: found on PATH). `typed` is how the person runs
    it in a terminal, for the commands to run by hand: ``claude`` when it is on PATH, else
    its full path. Claude Code refuses to add a name that already exists, so an existing
    user-scope entry is removed and added again.
    """
    program = claude or claude_code_available() or "claude"
    add = claude_code_add_command(entry, name=name, claude=program)
    by_hand = _display_claude(claude_code_add_command(entry, name=name, claude=typed))

    ok, detail = _run_claude(run, add)
    if ok:
        return True, f'added "{name}" for all your projects (user scope)'
    if "already exists" not in detail:
        return False, f"could not add the server ({detail}). Add it by hand with: {by_hand}"

    remove = [program, "mcp", "remove", "--scope", "user", name]
    removed, detail = _run_claude(run, remove)
    if removed:
        ok, detail = _run_claude(run, add)
        if ok:
            return True, f'replaced the existing "{name}" entry (user scope)'
        return False, f"could not add the server ({detail}). Add it by hand with: {by_hand}"
    remove_by_hand = _display_claude([typed, *remove[1:]])
    return False, (
        f'could not replace the existing "{name}" entry ({detail}). '
        f"Replace it by hand: {remove_by_hand}, then {by_hand}"
    )


# Helpers -------------------------------------------------------------------------------


def _run_claude(
    run: Callable[..., subprocess.CompletedProcess[str]], command: Sequence[str]
) -> tuple[bool, str]:
    try:
        result = run(
            list(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_CODE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Claude Code did not finish within {CLAUDE_CODE_TIMEOUT:.0f} seconds"
    except Exception as exc:  # a broken Claude Code must never break setup
        return False, f"could not run {command[0]}: {_reason(exc)}"
    if result.returncode == 0:
        return True, ""
    return False, _last_line(result.stderr) or _last_line(result.stdout) or (
        f"exit code {result.returncode}"
    )


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _display_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def _display_claude(command: Sequence[str]) -> str:
    """A Claude Code command to paste.

    PowerShell runs a program given by its path only after ``&``. That path goes in single
    quotes (``'`` doubled) so a ``'`` or ``$`` in it stays literal; the rest of the command is
    quoted as before.
    """
    if os.name != "nt" or command[0] == "claude":
        return _display_command(command)
    program = command[0].replace("'", "''")
    return f"& '{program}' {_display_command(command[1:])}"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


def _fix_by_hand(name: str) -> str:
    return (
        "Correct it in a text editor, or add the "
        f'"{name}" entry by hand as shown under "Manual configuration" in the README.'
    )


def _read_config(path: Path, name: str) -> dict[str, Any]:
    """Parse the config as a JSON object. Messages never quote the file's contents."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClientConfigError(
            f"Could not read {path} ({_reason(exc)}), so it was left untouched."
        ) from None
    try:
        text = raw.decode("utf-8-sig")  # tolerates a byte order mark from Windows editors
    except UnicodeDecodeError:
        raise ClientConfigError(
            f"{path} is not UTF-8 text, so it was left untouched. " + _fix_by_hand(name)
        ) from None
    if not text.strip():
        return {}  # an empty file (for example made with `touch`) holds no settings to keep
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # `from None`: the exception's .doc attribute is the whole file.
        raise ClientConfigError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}), "
            "so it was left untouched. " + _fix_by_hand(name)
        ) from None
    if not isinstance(data, dict):
        raise ClientConfigError(
            f"{path} does not hold a JSON object, so it was left untouched. " + _fix_by_hand(name)
        )
    return data


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _backup(path: Path, moment: datetime) -> Path:
    """Copy `path` to ``<name>.bak-YYYYmmdd-HHMMSS`` beside it, never over an older backup."""
    base = f"{path.name}.bak-{moment:%Y%m%d-%H%M%S}"
    backup = path.with_name(base)
    counter = 1
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{base}-{counter}")
    shutil.copy2(path, backup)
    return backup


def _replace_file(path: Path, text: str) -> None:
    """Replace `path` atomically through a temporary file in the same folder.

    Unlike ``paths.write_private_file`` this keeps the file's existing permissions (a new
    file gets the usual ones for the user's umask) and writes through a symlink rather than
    replacing it, because the file belongs to Claude Desktop.
    """
    target = path.resolve() if path.is_symlink() else path
    mode = _file_mode(target)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _file_mode(path: Path) -> int | None:
    if os.name == "nt":
        return None
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
