"""Tests for the ``deere-mcp`` command line: argument handling, exit codes, setup's merge,
and adding the server to Claude Desktop and Claude Code."""

from __future__ import annotations

import builtins
import getpass
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from deere_mcp import auth, cli, clients, paths
from deere_mcp.auth import AuthError, Tokens, TokenStore
from deere_mcp.config import ENV_NAMES, write_config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings folder at a temporary directory and clear every DEERE_* variable."""
    folder = tmp_path / "config"
    monkeypatch.setattr(paths, "config_dir", lambda: paths._make_private_dir(folder))
    for name in ENV_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    return folder


@dataclass
class FakeMachine:
    """A stand-in computer: its own home folder, programs on PATH and Claude Code calls."""

    user_home: Path
    appdata: Path
    programs: dict[str, str]
    claude_calls: list[list[str]] = field(default_factory=list)
    claude_reply: tuple[int, str, str] = (0, "Added stdio MCP server", "")

    def install_uv(self) -> str:
        self.programs["uvx"] = str(self.user_home / ".local" / "bin" / "uvx")
        return self.programs["uvx"]

    def install_claude_code(self) -> str:
        self.programs["claude"] = str(self.user_home / ".local" / "bin" / "claude")
        return self.programs["claude"]

    def desktop_config(self) -> Path:
        """Where Claude Desktop keeps its config on the platform running the tests."""
        if sys.platform == "darwin":
            folder = self.user_home / "Library" / "Application Support" / "Claude"
        elif sys.platform == "win32":
            folder = self.appdata / "Claude"
        else:
            folder = self.user_home / ".config" / "Claude"
        return folder / "claude_desktop_config.json"

    def install_claude_desktop(self) -> Path:
        path = self.desktop_config()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@pytest.fixture(autouse=True)
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    """Keep every test here away from this computer's real Claude Desktop, Claude Code and uv.

    By default uv is installed, Claude Desktop and Claude Code are not, no terminal is
    attached (so nothing is asked), and the real sign-in is replaced by one that fails the
    test: a test that signs in must patch ``auth.login`` itself.
    """

    real_login = auth.login

    async def no_real_login(settings: Any, store: Any, **kwargs: Any) -> Tokens:
        if not (settings.client_id and settings.client_secret):
            # The real one refuses before any network or browser use; keep that behaviour.
            return await real_login(settings, store, **kwargs)
        raise AssertionError("a test reached the real browser sign-in")

    monkeypatch.setattr(auth, "login", no_real_login)
    user_home = tmp_path / "home"
    appdata = user_home / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(user_home / "AppData" / "Local"))
    monkeypatch.delenv(clients.SOURCE_ENV, raising=False)
    monkeypatch.delenv("UV", raising=False)
    fake = FakeMachine(user_home=user_home, appdata=appdata, programs={})
    fake.install_uv()
    monkeypatch.setattr(cli, "_which", lambda program: fake.programs.get(program))
    monkeypatch.setattr(cli, "_terminal_attached", lambda: False)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        fake.claude_calls.append(command)
        return subprocess.CompletedProcess(command, *fake.claude_reply)

    register = clients.register_with_claude_code
    monkeypatch.setattr(
        clients,
        "register_with_claude_code",
        lambda entry, **kwargs: register(entry, run=fake_run, **kwargs),
    )
    return fake


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "deere-mcp 0.1.0" in capsys.readouterr().out


def test_config_shows_paths_and_defaults(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert str(home / "config.toml") in out
    assert "not signed in" in out
    assert "http://localhost:9090/callback" in out
    assert "organization preference" in out


def test_login_command_prints_signed_in(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home / "config.toml", {"client_id": "id", "client_secret": "secret"})

    async def fake_login(settings: Any, store: Any, **kwargs: Any) -> Tokens:
        return Tokens(access_token="a", refresh_token="r", expires_at=1.0)

    monkeypatch.setattr(auth, "login", fake_login)
    assert cli.main(["login"]) == 0
    assert capsys.readouterr().out.strip() == "Signed in."


def test_login_without_credentials_fails_plainly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["login"]) == 1
    assert "deere-mcp setup" in capsys.readouterr().err


def test_logout_with_nothing_saved(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["logout"]) == 0
    assert "No saved sign-in found." in capsys.readouterr().out


def test_logout_removes_saved_tokens(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = TokenStore(home / "tokens.json")
    store.save(Tokens(access_token="a", refresh_token=None, expires_at=0))
    assert cli.main(["logout"]) == 0
    assert "Signed out." in capsys.readouterr().out
    assert not store.path.exists()


def test_setup_merges_existing_config_and_signs_in(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = home / "config.toml"
    write_config(config, {"client_id": "old-id", "client_secret": "old-secret", "page_cap": "7"})
    monkeypatch.setattr(builtins, "input", lambda prompt="": "new-id")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")  # Enter keeps the saved secret
    seen: dict[str, Any] = {}

    async def fake_login(settings: Any, store: Any, **kwargs: Any) -> Tokens:
        seen["client_id"] = settings.client_id
        seen["client_secret"] = settings.client_secret
        return Tokens(access_token="a", refresh_token="r", expires_at=1.0)

    monkeypatch.setattr(auth, "login", fake_login)

    assert cli.main(["setup"]) == 0

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data == {"client_id": "new-id", "client_secret": "old-secret", "page_cap": "7"}
    assert seen == {"client_id": "new-id", "client_secret": "old-secret"}
    out = capsys.readouterr().out
    assert "Redirect URI must be exactly: http://localhost:9090/callback" in out
    assert "Signed in." in out
    assert "old-secret" not in out


def test_setup_requires_both_values(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")
    assert cli.main(["setup"]) == 1
    assert "Both the Application ID and the Secret" in capsys.readouterr().err
    assert not (home / "config.toml").exists()


def test_invalid_setting_is_reported_not_raised(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEERE_UOM", "FURLONGS")
    assert cli.main(["config"]) == 1
    err = capsys.readouterr().err
    assert "DEERE_UOM" in err
    assert "FURLONGS" in err


# Adding the server to the AI apps ------------------------------------------------------


@pytest.fixture
def fake_login(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Saved credentials and a sign-in that always works; returns the prompts shown."""
    write_config(home / "config.toml", {"client_id": "id", "client_secret": "secret"})
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")

    async def login(settings: Any, store: Any, **kwargs: Any) -> Tokens:
        return Tokens(access_token="a", refresh_token="r", expires_at=1.0)

    monkeypatch.setattr(auth, "login", login)
    return answer(monkeypatch, [])


def answer(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[str]:
    """Answer input() prompts in order (Enter once the list runs out); return the prompts."""
    prompts: list[str] = []
    queue = list(replies)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr(builtins, "input", fake_input)
    return prompts


DESKTOP_QUESTION = "Add the server to Claude Desktop? [Y/n] "
CODE_QUESTION = "Also add it to Claude Code? [Y/n] "


def last_line(text: str) -> str:
    return text.rstrip("\n").splitlines()[-1]


def test_connect_yes_without_claude_desktop(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["connect", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Claude Desktop: skipped, not found on this computer." in out
    assert "Claude Code: skipped, the claude command was not found." in out
    assert "Quit and reopen" not in out
    assert not machine.desktop_config().parent.exists()


def test_connect_yes_creates_claude_desktop_entry_and_adds_to_claude_code(
    home: Path, machine: FakeMachine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    claude = machine.install_claude_code()
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    assert cli.main(["connect", "--yes", "--source", str(checkout)]) == 0

    expected = {
        "command": machine.programs["uvx"],
        "args": ["--from", str(checkout.resolve()), "deere-mcp"],
    }
    if sys.platform == "win32":
        expected["env"] = {"PYTHONUTF8": "1"}
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"deere": expected}}
    assert len(machine.claude_calls) == 1
    assert machine.claude_calls[0][:8] == [
        claude,
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
        "deere",
    ]
    assert machine.claude_calls[0][-4:] == [machine.programs["uvx"], *expected["args"]]
    out = capsys.readouterr().out
    assert f"Claude Desktop: created {config}" in out
    assert 'Claude Code: added "deere" for all your projects (user scope)' in out
    assert last_line(out) == "Quit and reopen Claude Desktop to finish."


def test_connect_twice_is_unchanged(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    assert cli.main(["connect", "--yes"]) == 0
    before = config.read_bytes()
    capsys.readouterr()
    assert cli.main(["connect", "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"Claude Desktop: unchanged, {config} already has this server." in out
    assert "Quit and reopen" not in out
    assert config.read_bytes() == before
    assert [p.name for p in config.parent.iterdir()] == [config.name]


def test_connect_update_reports_backup(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert cli.main(["connect", "--yes", "--name", "deere-dev"]) == 0
    out = capsys.readouterr().out
    backups = [p for p in config.parent.iterdir() if p.name.startswith(f"{config.name}.bak-")]
    assert len(backups) == 1
    assert f"Claude Desktop: updated {config} (backup: {backups[0]})" in out
    assert list(json.loads(config.read_text(encoding="utf-8"))["mcpServers"]) == [
        "other",
        "deere-dev",
    ]
    assert '"command": "x"' not in out


def test_connect_without_terminal_skips_with_a_hint(
    home: Path,
    machine: FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    prompts = answer(monkeypatch, [])  # never called: no terminal is attached
    assert cli.main(["connect"]) == 0
    out = capsys.readouterr().out
    assert f"Claude Desktop: found {config}; not changed" in out
    assert 'Run "deere-mcp connect --yes" to add it.' in out
    assert "Claude Code: found" in out
    assert not config.exists()
    assert machine.claude_calls == []
    assert prompts == []


def test_connect_flags_turn_apps_off(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    assert cli.main(["connect", "--no-claude-desktop", "--no-claude-code"]) == 0
    out = capsys.readouterr().out
    assert "Claude Desktop: skipped." in out
    assert "Claude Code: skipped." in out
    assert not config.exists()
    assert machine.claude_calls == []


def test_connect_flag_adds_without_asking(
    home: Path, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = machine.install_claude_desktop()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, [])
    assert cli.main(["connect", "--claude-desktop", "--no-claude-code"]) == 0
    assert config.exists()
    assert prompts == []


def test_connect_reports_a_broken_config_and_leaves_it(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    config.write_text('{"mcpServers": {"other": {"env": {"KEY": "s3cret"}}}', encoding="utf-8")
    before = config.read_bytes()
    assert cli.main(["connect", "--yes"]) == 1
    captured = capsys.readouterr()
    assert str(config) in captured.err
    assert "left untouched" in captured.err
    assert captured.err.rstrip().endswith('Run "deere-mcp connect" to try again.')
    assert "s3cret" not in captured.out + captured.err
    assert config.read_bytes() == before


def test_connect_without_uv(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    machine.install_claude_desktop()
    machine.programs.clear()
    assert cli.main(["connect", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "uv was not found" in err
    assert err.rstrip().endswith('Run "deere-mcp connect" to try again.')
    assert not machine.desktop_config().exists()


def test_connect_reports_claude_code_failure(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    machine.install_claude_code()
    machine.claude_reply = (1, "", "Invalid scope")
    assert cli.main(["connect", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "Claude Code: could not add the server (Invalid scope)" in err
    assert "claude mcp add --scope user" in err


def test_setup_asks_and_adds_to_both_apps(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, ["", "", "y"])  # keep the ID, Enter for Desktop, y for Code

    assert cli.main(["setup"]) == 0

    assert prompts == ["Application ID [id]: ", DESKTOP_QUESTION, CODE_QUESTION]
    assert "deere" in json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
    assert len(machine.claude_calls) == 1
    out = capsys.readouterr().out
    assert f"Claude Desktop: created {config}" in out
    assert last_line(out) == "Signed in. Quit and reopen Claude Desktop to finish."


def test_setup_answering_no_changes_nothing(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, ["", "maybe", "n", "no"])

    assert cli.main(["setup"]) == 0

    assert prompts == ["Application ID [id]: ", DESKTOP_QUESTION, DESKTOP_QUESTION, CODE_QUESTION]
    assert not config.exists()
    assert machine.claude_calls == []
    out = capsys.readouterr().out
    assert "Please answer y or n." in out
    assert last_line(out) == "Signed in."


def test_three_unclear_answers_mean_no(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "my-app-id")

    assert cli.main(["setup"]) == 0

    assert not config.exists()
    assert capsys.readouterr().out.count("Please answer y or n.") == 3


def test_setup_only_asks_about_apps_that_are_installed(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    machine.install_claude_code()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, [])

    assert cli.main(["setup"]) == 0

    assert prompts == ["Application ID [id]: ", CODE_QUESTION]
    out = capsys.readouterr().out
    assert "Claude Desktop: skipped, not found on this computer." in out
    assert last_line(out) == "Signed in."


def test_setup_yes_does_not_ask(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, [])

    assert cli.main(["setup", "--yes"]) == 0

    assert prompts == ["Application ID [id]: "]
    assert config.exists()
    assert len(machine.claude_calls) == 1
    assert last_line(capsys.readouterr().out) == (
        "Signed in. Quit and reopen Claude Desktop to finish."
    )


def test_setup_no_connect_skips_the_apps(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    machine.install_claude_code()
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)
    prompts = answer(monkeypatch, [])

    assert cli.main(["setup", "--no-connect"]) == 0

    assert prompts == ["Application ID [id]: "]
    assert not config.exists()
    assert machine.claude_calls == []
    out = capsys.readouterr().out
    assert "Claude Desktop" not in out
    assert last_line(out) == "Signed in."


def test_setup_without_terminal_still_signs_in(
    home: Path, machine: FakeMachine, fake_login: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    config = machine.install_claude_desktop()
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert 'Run "deere-mcp connect --yes" to add it.' in out
    assert not config.exists()
    assert last_line(out) == "Signed in."


def test_setup_client_error_still_exits_zero(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()
    config.write_text("[1, 2", encoding="utf-8")

    assert cli.main(["setup", "--yes"]) == 0

    captured = capsys.readouterr()
    assert str(config) in captured.err
    assert captured.err.rstrip().endswith('Run "deere-mcp connect" to try again.')
    assert config.read_text(encoding="utf-8") == "[1, 2"
    assert last_line(captured.out) == "Signed in."


def test_setup_failed_login_exits_one_and_leaves_apps_alone(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = machine.install_claude_desktop()

    async def failing_login(settings: Any, store: Any, **kwargs: Any) -> Tokens:
        raise AuthError("Sign-in timed out.")

    monkeypatch.setattr(auth, "login", failing_login)
    assert cli.main(["setup", "--yes"]) == 1
    assert "Sign-in timed out." in capsys.readouterr().err
    assert not config.exists()


def test_setup_passes_source_and_name(
    home: Path,
    machine: FakeMachine,
    fake_login: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = machine.install_claude_desktop()
    monkeypatch.setenv(clients.SOURCE_ENV, "https://example.test/env.zip")
    assert cli.main(["setup", "--yes", "--name", "farm"]) == 0
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["farm"]
    assert entry["args"] == ["--from", "https://example.test/env.zip", "deere-mcp"]
    assert (
        cli.main(["connect", "--yes", "--name", "farm", "--source", "https://example.test/x.zip"])
        == 0
    )
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["farm"]
    assert entry["args"] == ["--from", "https://example.test/x.zip", "deere-mcp"]


def test_blank_name_is_refused(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["connect", "--name", " "]) == 1
    assert "--name" in capsys.readouterr().err


def test_config_shows_claude_desktop_and_claude_code(
    home: Path, machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    def lines() -> dict[str, str]:
        assert cli.main(["config"]) == 0
        rows = [line.split(":", 1) for line in capsys.readouterr().out.splitlines()]
        return {label: value.strip() for label, value in rows}

    shown = lines()
    assert shown["Claude Desktop config"] == "not found"
    assert shown["Claude Code"] == "not found"

    config = machine.install_claude_desktop()
    claude = machine.install_claude_code()
    shown = lines()
    assert shown["Claude Desktop config"] == f"{config} (no config file yet)"
    assert shown["Claude Code"] == f"found ({claude})"

    config.write_text(
        json.dumps({"mcpServers": {"other": {"env": {"K": "s3cret"}}}}), encoding="utf-8"
    )
    assert lines()["Claude Desktop config"] == f'{config} (no "deere" entry)'
    assert cli.main(["connect", "--yes"]) == 0
    capsys.readouterr()
    shown = lines()
    assert shown["Claude Desktop config"] == f'{config} ("deere" entry present)'
    config.write_text("{oops", encoding="utf-8")
    assert lines()["Claude Desktop config"] == f"{config} (could not be read)"
