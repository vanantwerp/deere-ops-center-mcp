"""Settings precedence and validation, config writing, and the CLI commands that use them."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from deere_mcp import cli, paths
from deere_mcp.config import DEFAULT_BASE_URL, load_settings, read_config, write_config


def _load(tmp_path: Path, env: dict[str, str] | None = None):
    return load_settings(
        env or {}, config_path=tmp_path / "config.toml", token_path=tmp_path / "tokens.json"
    )


def test_defaults_without_env_or_file(tmp_path: Path) -> None:
    s = _load(tmp_path)
    assert s.client_id is None and s.client_secret is None
    assert s.base_url == DEFAULT_BASE_URL
    assert s.uom is None and s.yield_pref is None
    assert s.callback_port == 9090
    assert s.page_cap == 20
    assert s.log_level == "WARNING"
    assert s.output_dir == paths.default_output_dir()
    assert s.token_path == tmp_path / "tokens.json"
    assert s.redirect_uri == "http://localhost:9090/callback"


def test_environment_beats_file_beats_default(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'client_id = "file-id"\nclient_secret = "file-secret"\nuom = "metric"\n'
        "callback_port = 9191\npage_cap = 5\n"
        'base_url = "https://partnerapi.deere.com/platform/"\n'
        'output_dir = "~/deere-out"\nyield = "MASS"\n',
        encoding="utf-8",
    )
    s = _load(
        tmp_path,
        {"DEERE_CLIENT_ID": "env-id", "DEERE_UOM": "ENGLISH", "DEERE_PAGE_CAP": "7"},
    )
    assert s.client_id == "env-id"  # environment wins
    assert s.client_secret == "file-secret"  # file fills the gap
    assert s.uom == "ENGLISH"
    assert s.yield_pref == "MASS"
    assert s.callback_port == 9191
    assert s.page_cap == 7
    assert s.base_url == "https://partnerapi.deere.com/platform"  # trailing slash removed
    assert s.output_dir == Path("~/deere-out").expanduser()
    assert s.log_level == "WARNING"  # default


def test_blank_environment_value_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('client_id = "file-id"\n', encoding="utf-8")
    assert _load(tmp_path, {"DEERE_CLIENT_ID": "  "}).client_id == "file-id"


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"DEERE_CALLBACK_PORT": "abc"}, "DEERE_CALLBACK_PORT"),
        ({"DEERE_CALLBACK_PORT": "70000"}, "65535"),
        ({"DEERE_UOM": "IMPERIAL"}, "ENGLISH or METRIC"),
        ({"DEERE_YIELD": "WEIGHT"}, "VOLUME or MASS"),
        ({"DEERE_PAGE_CAP": "0"}, "DEERE_PAGE_CAP"),
        ({"DEERE_LOG_LEVEL": "LOUD"}, "DEERE_LOG_LEVEL"),
        ({"DEERE_BASE_URL": "http://sandboxapi.deere.com/platform"}, "https://"),
        ({"DEERE_BASE_URL": "https://evil.example/platform"}, "deere.com"),
    ],
)
def test_invalid_values_raise_value_error(
    tmp_path: Path, env: dict[str, str], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment.replace(".", r"\.").replace("*", r"\*")):
        _load(tmp_path, env)


def test_invalid_file_value_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("callback_port = -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="config.toml"):
        _load(tmp_path)


def test_unreadable_toml_is_a_value_error(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("client_id = \n", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not read"):
        _load(tmp_path)


def test_write_config_round_trip_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"
    write_config(path, {"client_id": "abc", "client_secret": 'se"cr\\et\n', "uom": "METRIC"})
    assert read_config(path) == {
        "client_id": "abc",
        "client_secret": 'se"cr\\et\n',
        "uom": "METRIC",
    }
    s = load_settings({}, config_path=path, token_path=tmp_path / "t.json")
    assert s.client_secret == 'se"cr\\et'  # surrounding whitespace is stripped on load
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]  # no temp files left


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "config_dir", lambda: home)
    monkeypatch.setattr(paths, "default_output_dir", lambda: tmp_path / "docs")
    for name in (
        "DEERE_CLIENT_ID",
        "DEERE_CLIENT_SECRET",
        "DEERE_BASE_URL",
        "DEERE_UOM",
        "DEERE_YIELD",
        "DEERE_CALLBACK_PORT",
        "DEERE_PAGE_CAP",
        "DEERE_OUTPUT_DIR",
        "DEERE_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    return home


def test_cli_setup_saves_credentials_and_runs_login(
    config_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(config_home / "config.toml", {"uom": "ENGLISH"})
    monkeypatch.setattr("builtins.input", lambda prompt="": "my-app-id")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "my-secret")
    logged_in = []

    def fake_login(settings):
        logged_in.append(settings)
        print("Signed in.")
        return 0

    monkeypatch.setattr(cli, "_login", fake_login)
    assert cli.main(["setup", "--no-connect"]) == 0
    saved = read_config(config_home / "config.toml")
    assert saved == {"uom": "ENGLISH", "client_id": "my-app-id", "client_secret": "my-secret"}
    assert logged_in[0].client_id == "my-app-id"
    out = capsys.readouterr().out
    assert str(config_home / "config.toml") in out
    assert "my-secret" not in out
    assert "http://localhost:9090/callback" in out


def test_cli_setup_requires_both_values(
    config_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    assert cli.main(["setup"]) == 1
    assert "Application ID" in capsys.readouterr().err


def test_cli_config_shows_paths_but_not_the_secret(
    config_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(config_home / "config.toml", {"client_id": "app-1", "client_secret": "s3cr3t"})
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert str(config_home / "config.toml") in out
    assert str(config_home / "tokens.json") in out
    assert "app-1" in out
    assert "s3cr3t" not in out


def test_cli_bad_setting_prints_one_line_and_fails(
    config_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEERE_UOM", "furlongs")
    assert cli.main(["config"]) == 1
    err = capsys.readouterr().err
    assert err.strip().count("\n") == 0
    assert "DEERE_UOM" in err


def test_cli_logout_without_saved_sign_in(
    config_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["logout"]) == 0
    assert capsys.readouterr().out.strip() == "No saved sign-in found."


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
