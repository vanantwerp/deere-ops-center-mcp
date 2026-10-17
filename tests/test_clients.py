"""Tests for adding the server to Claude Desktop and Claude Code (``deere_mcp.clients``)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from deere_mcp import clients
from deere_mcp.clients import ClientConfigError, ServerEntry

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and symlinks")
SOURCE = "https://example.test/deere.zip"
NOON = datetime(2026, 10, 16, 12, 30, 45)


def make_entry(env: dict[str, str] | None = None) -> ServerEntry:
    return ServerEntry("/opt/uv/bin/uvx", ["--from", SOURCE, "deere-mcp"], env or {})


def which_from(programs: dict[str, str]) -> Any:
    return lambda name: programs.get(name)


def write_json(path: Path, data: Any) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=4).encode("utf-8")
    path.write_bytes(raw)
    return raw


def set_mtime(path: Path, seconds: float) -> None:
    os.utime(path, (seconds, seconds))


# resolve_source ------------------------------------------------------------------------


def test_source_defaults_to_the_main_branch_zip() -> None:
    assert clients.resolve_source(None, {}) == clients.DEFAULT_SOURCE
    assert clients.DEFAULT_SOURCE == (
        "https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip"
    )


def test_source_flag_beats_environment() -> None:
    env = {"DEERE_SOURCE": "https://example.test/env.zip"}
    assert clients.resolve_source("https://example.test/flag.zip", env) == (
        "https://example.test/flag.zip"
    )
    assert clients.resolve_source(None, env) == "https://example.test/env.zip"
    assert clients.resolve_source("  ", env) == "https://example.test/env.zip"


def test_local_folder_source_is_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(tmp_path)
    resolved = clients.resolve_source("checkout", {})
    assert Path(resolved).is_absolute()
    assert Path(resolved) == checkout.resolve()
    assert clients.resolve_source(None, {"DEERE_SOURCE": "checkout"}) == resolved


# build_entry ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_uv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide the uv that runs these tests, so discovery tests see only what they set up."""
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nohome"))


def test_entry_prefers_uvx(tmp_path: Path) -> None:
    uvx, uv = str(tmp_path / "uvx"), str(tmp_path / "uv")
    entry = clients.build_entry(SOURCE, platform="darwin", which=which_from({"uvx": uvx, "uv": uv}))
    assert entry == ServerEntry(uvx, ["--from", SOURCE, "deere-mcp"], {})
    assert entry.as_json() == {"command": uvx, "args": ["--from", SOURCE, "deere-mcp"]}


def test_entry_falls_back_to_uv_tool_run(tmp_path: Path) -> None:
    uv = str(tmp_path / "uv")
    entry = clients.build_entry(SOURCE, platform="linux", which=which_from({"uv": uv}))
    assert entry.command == uv
    assert entry.args == ["tool", "run", "--from", SOURCE, "deere-mcp"]


def test_entry_command_is_always_absolute() -> None:
    entry = clients.build_entry(SOURCE, platform="linux", which=which_from({"uvx": "bin/uvx"}))
    assert Path(entry.command).is_absolute()
    assert entry.command == os.path.abspath("bin/uvx")


def test_entry_without_uv_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ClientConfigError, match="uv was not found"):
        clients.build_entry(SOURCE, which=which_from({}), env={}, home=tmp_path)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_entry_finds_uvx_beside_the_running_uv(tmp_path: Path) -> None:
    """`uv run` sets UV to its own path; uvx lives next to it even when PATH lacks the folder."""
    uv = _executable(tmp_path / "bin" / "uv")
    uvx = _executable(tmp_path / "bin" / "uvx")
    entry = clients.build_entry(
        SOURCE, platform="linux", which=which_from({}), env={"UV": str(uv)}, home=tmp_path / "h"
    )
    assert entry.command == str(uvx)


def test_entry_finds_uvx_in_the_default_install_folder(tmp_path: Path) -> None:
    uvx = _executable(tmp_path / ".local" / "bin" / "uvx")
    entry = clients.build_entry(
        SOURCE, platform="linux", which=which_from({}), env={}, home=tmp_path
    )
    assert entry.command == str(uvx)


def test_entry_ignores_a_uv_hint_that_points_nowhere(tmp_path: Path) -> None:
    with pytest.raises(ClientConfigError, match="uv was not found"):
        clients.build_entry(
            SOURCE, which=which_from({}), env={"UV": str(tmp_path / "gone" / "uv")}, home=tmp_path
        )


def test_windows_entry_sets_utf8(tmp_path: Path) -> None:
    uvx = str(tmp_path / "uvx.exe")
    entry = clients.build_entry(SOURCE, platform="win32", which=which_from({"uvx": uvx}))
    assert entry.env == {"PYTHONUTF8": "1"}
    assert entry.as_json()["env"] == {"PYTHONUTF8": "1"}


# claude_desktop_config_path ------------------------------------------------------------


def mac_config(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def test_mac_existing_file_is_found(tmp_path: Path) -> None:
    write_json(mac_config(tmp_path), {})
    found = clients.claude_desktop_config_path(platform="darwin", env={}, home=tmp_path)
    assert found == mac_config(tmp_path)


def test_mac_folder_without_file_is_used(tmp_path: Path) -> None:
    mac_config(tmp_path).parent.mkdir(parents=True)
    found = clients.claude_desktop_config_path(platform="darwin", env={}, home=tmp_path)
    assert found == mac_config(tmp_path)
    assert not found.exists()


def test_nothing_installed_gives_none(tmp_path: Path) -> None:
    for platform in ("darwin", "linux", "win32"):
        env = {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
        assert clients.claude_desktop_config_path(platform=platform, env=env, home=tmp_path) is None


def test_linux_path(tmp_path: Path) -> None:
    expected = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
    expected.parent.mkdir(parents=True)
    assert clients.claude_desktop_config_path(platform="linux", env={}, home=tmp_path) == expected


def windows_env(tmp_path: Path) -> dict[str, str]:
    return {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}


def store_config(tmp_path: Path, package: str = "Claude_pzs8sxrjxfjjc") -> Path:
    return (
        tmp_path
        / "Local"
        / "Packages"
        / package
        / "LocalCache"
        / "Roaming"
        / "Claude"
        / "claude_desktop_config.json"
    )


def test_windows_appdata_file(tmp_path: Path) -> None:
    normal = tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    write_json(normal, {})
    found = clients.claude_desktop_config_path(
        platform="win32", env=windows_env(tmp_path), home=tmp_path
    )
    assert found == normal


def test_windows_appdata_defaults_to_home(tmp_path: Path) -> None:
    normal = tmp_path / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    normal.parent.mkdir(parents=True)
    assert clients.claude_desktop_config_path(platform="win32", env={}, home=tmp_path) == normal


def test_windows_store_install_is_found(tmp_path: Path) -> None:
    (tmp_path / "Roaming" / "Claude").mkdir(parents=True)  # folder only, no file
    store = store_config(tmp_path)
    write_json(store, {})
    found = clients.claude_desktop_config_path(
        platform="win32", env=windows_env(tmp_path), home=tmp_path
    )
    assert found == store


def test_windows_store_folder_without_file(tmp_path: Path) -> None:
    store = store_config(tmp_path)
    store.parent.mkdir(parents=True)
    found = clients.claude_desktop_config_path(
        platform="win32", env=windows_env(tmp_path), home=tmp_path
    )
    assert found == store


@pytest.mark.parametrize("newer", ["normal", "store"])
def test_windows_newest_of_two_files_wins(tmp_path: Path, newer: str) -> None:
    normal = tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    store = store_config(tmp_path)
    write_json(normal, {})
    write_json(store, {})
    set_mtime(normal, 2_000_000_000 if newer == "normal" else 1_000_000_000)
    set_mtime(store, 2_000_000_000 if newer == "store" else 1_000_000_000)
    found = clients.claude_desktop_config_path(
        platform="win32", env=windows_env(tmp_path), home=tmp_path
    )
    assert found == (normal if newer == "normal" else store)


# write_claude_desktop_entry ------------------------------------------------------------


def test_create_new_file_and_folder(tmp_path: Path) -> None:
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result == clients.WriteResult(path, "created", None)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.loads(text) == {"mcpServers": {"deere": make_entry().as_json()}}
    assert [p.name for p in path.parent.iterdir()] == ["claude_desktop_config.json"]


def test_merge_keeps_other_settings_and_servers_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    original = {
        "globalShortcut": "Ctrl+Space",
        "mcpServers": {"other": {"command": "x", "env": {"TOKEN": "s3cret-other"}}},
        "preferences": {"theme": "dark"},
    }
    raw = write_json(path, original)

    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)

    backup = tmp_path / "claude_desktop_config.json.bak-20261016-123045"
    assert result == clients.WriteResult(path, "updated", backup)
    assert backup.read_bytes() == raw
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == ["globalShortcut", "mcpServers", "preferences"]
    assert data["globalShortcut"] == "Ctrl+Space"
    assert data["preferences"] == {"theme": "dark"}
    assert data["mcpServers"] == {
        "other": original["mcpServers"]["other"],
        "deere": make_entry().as_json(),
    }
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "claude_desktop_config.json",
        "claude_desktop_config.json.bak-20261016-123045",
    ]


def test_missing_mcp_servers_is_added(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    write_json(path, {"globalShortcut": ""})
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result.action == "updated"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "globalShortcut": "",
        "mcpServers": {"deere": make_entry().as_json()},
    }


def test_same_entry_is_unchanged_without_backup(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    raw = write_json(path, {"mcpServers": {"deere": make_entry().as_json()}})
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result == clients.WriteResult(path, "unchanged", None)
    assert path.read_bytes() == raw
    assert [p.name for p in tmp_path.iterdir()] == ["claude_desktop_config.json"]


def test_different_entry_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    write_json(path, {"mcpServers": {"deere": {"command": "uvx", "args": ["old"]}}})
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result.action == "updated"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["deere"] == make_entry().as_json()


def test_custom_name(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    clients.write_claude_desktop_entry(path, make_entry(), name="deere-dev")
    assert list(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]) == ["deere-dev"]


def test_backups_in_the_same_second_do_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    write_json(path, {"mcpServers": {}})
    first = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    second = clients.write_claude_desktop_entry(path, make_entry({"A": "1"}), now=lambda: NOON)
    assert first.backup is not None and second.backup is not None
    assert first.backup != second.backup
    assert second.backup.name == "claude_desktop_config.json.bak-20261016-123045-2"
    assert json.loads(first.backup.read_text(encoding="utf-8")) == {"mcpServers": {}}


@pytest.mark.parametrize(
    "content",
    [
        b'{"mcpServers": {"other": {"env": {"TOKEN": "s3cret-other"}}},,}',
        b"\xff\xfe not utf-8 s3cret-other",
        b'["s3cret-other"]',
    ],
    ids=["malformed", "not-utf8", "not-an-object"],
)
def test_unreadable_file_is_refused_and_left_untouched(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "claude_desktop_config.json"
    path.write_bytes(content)
    with pytest.raises(ClientConfigError) as excinfo:
        clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    message = str(excinfo.value)
    assert str(path) in message
    assert "left untouched" in message
    assert "by hand" in message
    assert "s3cret-other" not in message
    # A chained JSONDecodeError would carry the whole file in its .doc attribute.
    assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__
    assert path.read_bytes() == content
    assert [p.name for p in tmp_path.iterdir()] == ["claude_desktop_config.json"]


def test_mcp_servers_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    raw = write_json(path, {"mcpServers": ["deere"]})
    with pytest.raises(ClientConfigError, match='"mcpServers" is not a JSON object'):
        clients.write_claude_desktop_entry(path, make_entry())
    assert path.read_bytes() == raw
    assert [p.name for p in tmp_path.iterdir()] == ["claude_desktop_config.json"]


def test_byte_order_mark_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"other": {}}}).encode())
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result.action == "updated"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw)["mcpServers"] == {"other": {}, "deere": make_entry().as_json()}


def test_empty_file_is_treated_as_empty_settings(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    path.write_bytes(b"")
    result = clients.write_claude_desktop_entry(path, make_entry(), now=lambda: NOON)
    assert result.action == "updated"
    assert result.backup is not None and result.backup.read_bytes() == b""
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"deere": make_entry().as_json()}
    }


def test_non_ascii_text_is_kept_readable(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    write_json(path, {"note": "Grüße"})
    clients.write_claude_desktop_entry(path, make_entry())
    assert '"Grüße"' in path.read_text(encoding="utf-8")


@POSIX_ONLY
def test_permissions_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    write_json(path, {})
    path.chmod(0o644)
    clients.write_claude_desktop_entry(path, make_entry())
    assert path.stat().st_mode & 0o777 == 0o644


@POSIX_ONLY
def test_new_file_is_not_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    umask = os.umask(0o022)
    try:
        clients.write_claude_desktop_entry(path, make_entry())
    finally:
        os.umask(umask)
    assert path.stat().st_mode & 0o777 == 0o644


@POSIX_ONLY
def test_symlinked_config_is_written_through(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "claude.json"
    write_json(real, {"keep": True})
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir()
    path.symlink_to(real)
    clients.write_claude_desktop_entry(path, make_entry())
    assert path.is_symlink()
    assert json.loads(real.read_text(encoding="utf-8"))["keep"] is True


def test_has_entry(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    assert clients.has_claude_desktop_entry(path) is False
    write_json(path, {"mcpServers": {"other": {}}})
    assert clients.has_claude_desktop_entry(path) is False
    write_json(path, {"mcpServers": {"deere": {}}})
    assert clients.has_claude_desktop_entry(path) is True
    path.write_text("{not json", encoding="utf-8")
    assert clients.has_claude_desktop_entry(path) is None


# Claude Code ---------------------------------------------------------------------------


def test_claude_code_available(tmp_path: Path) -> None:
    claude = str(tmp_path / "claude")
    assert clients.claude_code_available(which=which_from({"claude": claude})) == claude
    assert clients.claude_code_available(which=which_from({})) is None


def bundled_claude(desktop_folder: Path, version: str, build: str = "abc123") -> Path:
    exe = desktop_folder / "claude-code" / version / build / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def windows_claude_code(tmp_path: Path) -> str | None:
    return clients.claude_code_available(
        which=which_from({}), platform="win32", env=windows_env(tmp_path), home=tmp_path
    )


def test_windows_finds_claude_desktops_copy(tmp_path: Path) -> None:
    desktop = tmp_path / "Roaming" / "Claude"
    bundled_claude(desktop, "2.1.9")
    newest = bundled_claude(desktop, "2.1.10")
    assert windows_claude_code(tmp_path) == str(newest)


def test_windows_finds_store_installs_copy(tmp_path: Path) -> None:
    newest = bundled_claude(store_config(tmp_path).parent, "2.1.299")
    assert windows_claude_code(tmp_path) == str(newest)


def test_windows_path_wins_over_desktops_copy(tmp_path: Path) -> None:
    bundled_claude(tmp_path / "Roaming" / "Claude", "2.1.299")
    claude = str(tmp_path / "bin" / "claude.exe")
    found = clients.claude_code_available(
        which=which_from({"claude": claude}),
        platform="win32",
        env=windows_env(tmp_path),
        home=tmp_path,
    )
    assert found == claude


def test_windows_without_claude_code(tmp_path: Path) -> None:
    (tmp_path / "Roaming" / "Claude" / "claude-code").mkdir(parents=True)
    assert windows_claude_code(tmp_path) is None


def test_desktops_copy_is_only_used_on_windows(tmp_path: Path) -> None:
    bundled_claude(tmp_path / "Library" / "Application Support" / "Claude", "2.1.299")
    found = clients.claude_code_available(which=which_from({}), platform="darwin", home=tmp_path)
    assert found is None


def test_add_command_shape() -> None:
    assert clients.claude_code_add_command(make_entry()) == [
        "claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
        "deere",
        "--",
        "/opt/uv/bin/uvx",
        "--from",
        SOURCE,
        "deere-mcp",
    ]


def test_add_command_with_env_and_name() -> None:
    command = clients.claude_code_add_command(
        make_entry({"PYTHONUTF8": "1"}), name="deere-dev", claude="/bin/claude"
    )
    assert command[:8] == [
        "/bin/claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
        "deere-dev",
    ]
    assert command[8:11] == ["--env", "PYTHONUTF8=1", "--"]
    assert command[11:] == ["/opt/uv/bin/uvx", "--from", SOURCE, "deere-mcp"]


class FakeRun:
    """Stands in for subprocess.run; answers with queued (returncode, stdout, stderr)."""

    def __init__(self, *replies: tuple[int, str, str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        self.kwargs.append(kwargs)
        code, out, err = self.replies.pop(0)
        return subprocess.CompletedProcess(command, code, out, err)


def test_register_success() -> None:
    run = FakeRun((0, "Added stdio MCP server deere", ""))
    ok, message = clients.register_with_claude_code(make_entry(), run=run, claude="/bin/claude")
    assert ok is True
    assert message == 'added "deere" for all your projects (user scope)'
    assert run.calls == [clients.claude_code_add_command(make_entry(), claude="/bin/claude")]
    assert run.kwargs[0]["timeout"] == 60
    assert run.kwargs[0]["capture_output"] is True
    assert run.kwargs[0]["check"] is False


def test_register_failure_gives_the_command_to_run_by_hand() -> None:
    run = FakeRun((1, "", "Something went wrong\nInvalid scope\n"))
    ok, message = clients.register_with_claude_code(make_entry(), run=run, claude="/bin/claude")
    assert ok is False
    assert "Invalid scope" in message
    assert "\n" not in message
    assert "claude mcp add --scope user --transport stdio deere -- /opt/uv/bin/uvx" in message


def test_register_never_raises() -> None:
    def missing(command: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    def slow(command: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    ok, message = clients.register_with_claude_code(make_entry(), run=missing, claude="/x/claude")
    assert ok is False
    assert "No such file or directory" in message
    assert "claude mcp add" in message
    ok, message = clients.register_with_claude_code(make_entry(), run=slow, claude="/x/claude")
    assert ok is False
    assert "60 seconds" in message


def test_register_replaces_an_existing_entry() -> None:
    run = FakeRun(
        (1, "", "MCP server deere already exists in user config\n"),
        (0, "Removed MCP server deere from user config", ""),
        (0, "Added stdio MCP server deere", ""),
    )
    ok, message = clients.register_with_claude_code(make_entry(), run=run, claude="/bin/claude")
    assert ok is True
    assert "replaced" in message
    add = clients.claude_code_add_command(make_entry(), claude="/bin/claude")
    assert run.calls == [add, ["/bin/claude", "mcp", "remove", "--scope", "user", "deere"], add]


def test_register_reports_a_failed_replacement() -> None:
    run = FakeRun(
        (1, "", "MCP server deere already exists in user config"),
        (1, "", "permission denied"),
    )
    ok, message = clients.register_with_claude_code(make_entry(), run=run, claude="/bin/claude")
    assert ok is False
    assert "permission denied" in message
    assert "claude mcp remove --scope user deere" in message
    assert "claude mcp add" in message
