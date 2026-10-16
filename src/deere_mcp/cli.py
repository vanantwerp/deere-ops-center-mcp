"""The ``deere-mcp`` command: serve (default), setup, connect, login, logout, config."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shutil
import sys
from dataclasses import dataclass

from deere_mcp import __version__, clients, paths
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
    setup = sub.add_parser(
        "setup",
        help="save your Application ID and Secret, sign in, then add the server to Claude",
    )
    _add_client_options(setup)
    setup.add_argument(
        "--no-connect",
        action="store_true",
        help="only sign in; do not add the server to Claude Desktop or Claude Code",
    )
    connect = sub.add_parser(
        "connect",
        help="add the server to Claude Desktop and Claude Code (no sign-in)",
    )
    _add_client_options(connect)
    sub.add_parser("login", help="sign in to John Deere in the browser")
    sub.add_parser("logout", help="cancel the sign-in at John Deere and delete it here")
    sub.add_parser("config", help="show where settings are kept and the current settings")
    return parser


def _add_client_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        metavar="SOURCE",
        help=(
            "where uvx gets the server: a URL or a local folder "
            f"(default: ${clients.SOURCE_ENV}, else {clients.DEFAULT_SOURCE})"
        ),
    )
    parser.add_argument(
        "--name",
        default=clients.DEFAULT_NAME,
        help=f"the name the AI apps show for the server (default: {clients.DEFAULT_NAME})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="do not ask; add the server to every AI app that is found",
    )
    parser.add_argument(
        "--claude-desktop",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="add the server to Claude Desktop, or not (default: ask)",
    )
    parser.add_argument(
        "--claude-code",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="add the server to Claude Code, or not (default: ask)",
    )


def _serve() -> int:
    from deere_mcp.server import create_server

    # Nothing may be written to stdout here: it carries the MCP protocol.
    create_server(load_settings()).run()
    return 0


def _login(settings: Settings) -> int:
    """Sign in through the browser. The caller prints the closing line."""
    from deere_mcp import auth

    asyncio.run(auth.login(settings, TokenStore(settings.token_path), echo=print))
    return 0


def _logout() -> int:
    from deere_mcp import auth

    settings = load_settings()
    existed = asyncio.run(auth.logout(settings, TokenStore(settings.token_path)))
    print("Signed out." if existed else "No saved sign-in found.")
    return 0


def _setup(args: argparse.Namespace) -> int:
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
    _login(load_settings(config_path=config_path))  # a failed sign-in raises: exit code 1

    desktop_ready = False
    if not args.no_connect:
        print()
        desktop_ready = _connect_clients(args).desktop_ready
    print("Signed in. Quit and reopen Claude Desktop to finish." if desktop_ready else "Signed in.")
    return 0


# Adding the server to the AI apps --------------------------------------------------------

_TRY_AGAIN = 'Run "deere-mcp connect" to try again.'
_NO_TERMINAL = (
    'not changed because there is no terminal to ask. Run "deere-mcp connect --yes" to add it.'
)


def _which(program: str) -> str | None:
    """Find a program on PATH. Looked up at call time so tests can substitute it."""
    return shutil.which(program)


def _terminal_attached() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):  # replaced or closed stdin
        return False


def _ask(question: str) -> bool:
    """Ask a [Y/n] question. Empty means yes; no input, or three unclear answers, means no."""
    for _ in range(3):
        try:
            answer = input(question).strip().lower()
        except EOFError:
            print()
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")
    return False


def _wanted(flag: bool | None, args: argparse.Namespace, question: str) -> bool | None:
    """True to go ahead, False to skip, None when there is nobody to ask."""
    if flag is not None:
        return flag
    if args.yes:
        return True
    if _terminal_attached():
        return _ask(question)
    return None


@dataclass
class _ConnectOutcome:
    desktop_ready: bool = False  # Claude Desktop's config has the entry (new, updated or as-is)
    desktop_changed: bool = False
    failed: bool = False


def _connect_clients(args: argparse.Namespace) -> _ConnectOutcome:
    """Add the server to Claude Desktop and Claude Code, printing one line per app."""
    outcome = _ConnectOutcome()
    try:
        source = clients.resolve_source(args.source, os.environ)
        entry = clients.build_entry(source, which=_which)
    except clients.ClientConfigError as exc:
        print(f"{exc} {_TRY_AGAIN}", file=sys.stderr)
        outcome.failed = True
        return outcome

    desktop = clients.claude_desktop_config_path()
    if args.claude_desktop is False:
        print("Claude Desktop: skipped.")
    elif desktop is None:
        print("Claude Desktop: skipped, not found on this computer.")
    else:
        go = _wanted(args.claude_desktop, args, "Add the server to Claude Desktop? [Y/n] ")
        if go is None:
            print(f"Claude Desktop: found {desktop}; {_NO_TERMINAL}")
        elif not go:
            print("Claude Desktop: skipped.")
        else:
            try:
                result = clients.write_claude_desktop_entry(desktop, entry, name=args.name)
            except clients.ClientConfigError as exc:
                print(f"Claude Desktop: {exc} {_TRY_AGAIN}", file=sys.stderr)
                outcome.failed = True
            else:
                outcome.desktop_ready = True
                outcome.desktop_changed = result.action != "unchanged"
                if result.action == "created":
                    print(f"Claude Desktop: created {result.path}")
                elif result.action == "updated":
                    print(f"Claude Desktop: updated {result.path} (backup: {result.backup})")
                else:
                    print(f"Claude Desktop: unchanged, {result.path} already has this server.")

    claude = clients.claude_code_available(which=_which)
    if args.claude_code is False:
        print("Claude Code: skipped.")
    elif claude is None:
        print("Claude Code: skipped, the claude command was not found.")
    else:
        go = _wanted(args.claude_code, args, "Also add it to Claude Code? [Y/n] ")
        if go is None:
            print(f"Claude Code: found {claude}; {_NO_TERMINAL}")
        elif not go:
            print("Claude Code: skipped.")
        else:
            ok, message = clients.register_with_claude_code(entry, name=args.name, claude=claude)
            if ok:
                print(f"Claude Code: {message}")
            else:
                print(f"Claude Code: {message}", file=sys.stderr)
                outcome.failed = True
    return outcome


def _connect(args: argparse.Namespace) -> int:
    outcome = _connect_clients(args)
    if outcome.desktop_changed:
        print("Quit and reopen Claude Desktop to finish.")
    return 1 if outcome.failed else 0


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
        ("Claude Desktop config", _desktop_config_state()),
        ("Claude Code", _claude_code_state()),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label + ':':<{width + 1}} {value}")
    return 0


def _desktop_config_state() -> str:
    path = clients.claude_desktop_config_path()
    if path is None:
        return "not found"
    if not path.exists():
        return f"{path} (no config file yet)"
    name = clients.DEFAULT_NAME
    present = clients.has_claude_desktop_entry(path, name=name)
    if present is None:
        return f"{path} (could not be read)"
    return f'{path} ("{name}" entry present)' if present else f'{path} (no "{name}" entry)'


def _claude_code_state() -> str:
    claude = clients.claude_code_available(which=_which)
    return f"found ({claude})" if claude else "not found"


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``deere-mcp`` console script."""
    args = _build_parser().parse_args(argv)
    command = args.command or "serve"
    try:
        if command in ("setup", "connect"):
            args.name = args.name.strip()
            if not args.name:
                raise ValueError("--name needs a value, for example --name deere.")
        if command == "serve":
            return _serve()
        if command == "setup":
            return _setup(args)
        if command == "connect":
            return _connect(args)
        if command == "login":
            _login(load_settings())
            print("Signed in.")
            return 0
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
