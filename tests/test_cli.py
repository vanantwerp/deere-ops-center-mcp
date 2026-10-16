"""Tests for the ``deere-mcp`` command line: argument handling, exit codes, setup's merge."""

from __future__ import annotations

import builtins
import getpass
import tomllib
from pathlib import Path
from typing import Any

import pytest

from deere_mcp import auth, cli, paths
from deere_mcp.auth import Tokens, TokenStore
from deere_mcp.config import ENV_NAMES, write_config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings folder at a temporary directory and clear every DEERE_* variable."""
    folder = tmp_path / "config"
    monkeypatch.setattr(paths, "config_dir", lambda: paths._make_private_dir(folder))
    for name in ENV_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    return folder


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
