"""The ``deere-mcp`` command: serve (default), setup, login, logout, config."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from deere_mcp import __version__, paths
from deere_mcp.auth import AuthError, TokenStore
from deere_mcp.config import ENV_NAMES, Settings, load_settings, read_config, write_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deere-mcp",
        description="Read-only John Deere Operations Center data for AI assistants (MCP).",
    )
    parser.add_argument("--version", action="version", version=f"deere-mcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("serve", help="run the MCP server over stdio (the default)")
    sub.add_parser("setup", help="save your Application ID and Secret, then sign in")
    sub.add_parser("login", help="sign in to John Deere in the browser")
    sub.add_parser("logout", help="cancel the sign-in at John Deere and delete it here")
    sub.add_parser("config", help="show where settings are kept and the current settings")
    return parser


def _serve() -> int:
    from deere_mcp.server import create_server

    # Nothing may be written to stdout here: it carries the MCP protocol.
    create_server(load_settings()).run()
    return 0


def _login(settings: Settings) -> int:
    from deere_mcp import auth

    asyncio.run(auth.login(settings, TokenStore(settings.token_path), echo=print))
    print("Signed in.")
    return 0


def _logout() -> int:
    from deere_mcp import auth

    settings = load_settings()
    existed = asyncio.run(auth.logout(settings, TokenStore(settings.token_path)))
    print("Signed out." if existed else "No saved sign-in found.")
    return 0


def _setup() -> int:
    config_path = paths.config_file()
    settings = load_settings(config_path=config_path)
    existing = read_config(config_path)
    old_id = str(existing.get("client_id") or "").strip()
    old_secret = str(existing.get("client_secret") or "").strip()

    print("Set up Deere Ops Center MCP.")
    print("You need the Application ID and Secret from developer.deere.com (My Applications).")
    print(f"The application's Redirect URI must be exactly: {settings.redirect_uri}")
    print()
    prompt = f"Application ID [{old_id}]: " if old_id else "Application ID: "
    client_id = input(prompt).strip() or old_id
    secret_prompt = (
        "Secret (typing is hidden; press Enter to keep the saved one): "
        if old_secret
        else "Secret (typing is hidden): "
    )
    client_secret = getpass.getpass(secret_prompt).strip() or old_secret
    if not client_id or not client_secret:
        raise ValueError("Both the Application ID and the Secret are needed. Run setup again.")

    values = {key: str(value) for key, value in existing.items() if not isinstance(value, dict)}
    values["client_id"] = client_id
    values["client_secret"] = client_secret
    write_config(config_path, values)
    print(f"Saved to {config_path}")
    for key in ("client_id", "client_secret"):
        if os.environ.get(ENV_NAMES[key], "").strip():
            print(
                f"Note: {ENV_NAMES[key]} is set in the environment and overrides the saved value."
            )
    print()
    return _login(load_settings(config_path=config_path))


def _show_config() -> int:
    settings = load_settings()
    store = TokenStore(settings.token_path)
    config_state = "exists" if settings.config_path.exists() else "not created yet"
    token_state = "signed in" if store.load() is not None else "not signed in"
    rows = [
        ("Config file", f"{settings.config_path} ({config_state})"),
        ("Token file", f"{settings.token_path} ({token_state})"),
        ("Output folder", str(settings.output_dir)),
        ("Application ID", settings.client_id or "not set"),
        ("Secret", "set" if settings.client_secret else "not set"),
        ("Base URL", settings.base_url),
        ("Units (DEERE_UOM)", settings.uom or "not set (organization preference)"),
        ("Yield (DEERE_YIELD)", settings.yield_pref or "not set (organization preference)"),
        ("Callback port", f"{settings.callback_port} (Redirect URI {settings.redirect_uri})"),
        ("Page cap", str(settings.page_cap)),
        ("Log level", settings.log_level),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label + ':':<{width + 1}} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``deere-mcp`` console script."""
    args = _build_parser().parse_args(argv)
    command = args.command or "serve"
    try:
        if command == "serve":
            return _serve()
        if command == "setup":
            return _setup()
        if command == "login":
            return _login(load_settings())
        if command == "logout":
            return _logout()
        if command == "config":
            return _show_config()
    except (AuthError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except EOFError:
        print("No input received. Run this command in a terminal.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    raise AssertionError(f"unknown command {command}")  # argparse rejects unknown commands
