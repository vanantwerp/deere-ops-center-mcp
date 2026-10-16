"""Signing in to John Deere: OAuth discovery, the browser login, refresh, logout, token storage."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
import socket
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from deere_mcp.config import Settings, is_allowed_host
from deere_mcp.paths import write_private_file

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://signin.johndeere.com/oauth2/aus78tnlaysMraFhC1t7/.well-known/oauth-authorization-server"
SCOPES = "ag2 eq1 org1 work1 offline_access"
DEERE_ACCEPT = "application/vnd.deere.axiom.v3+json"
CONNECTIONS_HOST = "connections.deere.com"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
LOGIN_TIMEOUT = 600.0  # seconds to wait for each browser step
DEFAULT_EXPIRES_IN = 3600.0
NOT_SET_UP = "Run `deere-mcp setup` first."

Echo = Callable[[str], None]


class AuthError(Exception):
    """A sign-in problem, with a message meant for the person or the model to read."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float  # unix seconds
    scope: str | None = None

    def expires_within(self, seconds: float) -> bool:
        """True if the access token has expired or will within `seconds`."""
        return time.time() + seconds >= self.expires_at


class TokenStore:
    """The saved sign-in, as a JSON file readable only by the owner where supported."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Tokens | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(
                "Could not read the saved sign-in file %s (%s).", self.path, type(exc).__name__
            )
            return None
        try:
            data = json.loads(raw)
            access = data["access_token"]
            refresh = data.get("refresh_token")
            if not isinstance(access, str) or not access:
                raise ValueError("access_token")
            return Tokens(
                access_token=access,
                refresh_token=refresh if isinstance(refresh, str) and refresh else None,
                expires_at=float(data.get("expires_at", 0)),
                scope=data.get("scope") if isinstance(data.get("scope"), str) else None,
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("The saved sign-in file %s is damaged; sign in again.", self.path)
            return None

    def save(self, tokens: Tokens) -> None:
        write_private_file(self.path, json.dumps(asdict(tokens), indent=2) + "\n")

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Endpoints:
    authorization: str
    token: str
    revocation: str | None


_discovery_cache: Endpoints | None = None


def reset_discovery_cache() -> None:
    """Forget the cached discovery document (for tests)."""
    global _discovery_cache
    _discovery_cache = None


def _https_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or urlsplit(value).scheme != "https":
        raise AuthError(f"John Deere's sign-in service gave an unusable {name}.")
    return value


async def discover(http: httpx.AsyncClient) -> Endpoints:
    """Fetch Deere's OAuth endpoints once per process."""
    global _discovery_cache
    if _discovery_cache is not None:
        return _discovery_cache
    try:
        response = await http.get(DISCOVERY_URL, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise AuthError(
            f"Could not reach John Deere's sign-in service ({type(exc).__name__}). "
            "Check the internet connection and try again."
        ) from exc
    if response.status_code >= 400:
        raise AuthError(
            f"John Deere's sign-in service answered {response.status_code} "
            "to the discovery request."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise AuthError(
            "John Deere's sign-in service returned something that was not JSON."
        ) from exc
    if not isinstance(data, dict):
        raise AuthError("John Deere's sign-in service returned an unexpected discovery document.")
    revocation = data.get("revocation_endpoint")
    endpoints = Endpoints(
        authorization=_https_url(data.get("authorization_endpoint"), "authorization address"),
        token=_https_url(data.get("token_endpoint"), "token address"),
        revocation=_https_url(revocation, "revocation address") if revocation else None,
    )
    _discovery_cache = endpoints
    return endpoints


def _credentials(settings: Settings) -> tuple[str, str]:
    if not settings.client_id or not settings.client_secret:
        raise AuthError(NOT_SET_UP)
    return settings.client_id, settings.client_secret


def _oauth_error_text(response: httpx.Response) -> str:
    """Deere's OAuth error code and description, never the rest of the body."""
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if not isinstance(data, dict):
        return f"HTTP {response.status_code}"
    parts = [str(data[k]) for k in ("error", "error_description") if data.get(k)]
    return ": ".join(parts)[:300] if parts else f"HTTP {response.status_code}"


def _tokens_from_response(data: Any, previous_refresh: str | None) -> Tokens:
    if not isinstance(data, dict):
        raise AuthError("John Deere's sign-in service returned an unexpected token response.")
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise AuthError("John Deere's sign-in service did not return an access token.")
    refresh = data.get("refresh_token")
    try:
        expires_in = float(data.get("expires_in", DEFAULT_EXPIRES_IN))
    except (TypeError, ValueError):
        expires_in = DEFAULT_EXPIRES_IN
    scope = data.get("scope")
    return Tokens(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) and refresh else previous_refresh,
        expires_at=time.time() + expires_in,
        scope=scope if isinstance(scope, str) else None,
    )


async def _token_request(
    http: httpx.AsyncClient, settings: Settings, form: dict[str, str], action: str
) -> httpx.Response:
    client_id, client_secret = _credentials(settings)
    endpoints = await discover(http)
    try:
        return await http.post(
            endpoints.token,
            data=form,
            auth=httpx.BasicAuth(client_id, client_secret),
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise AuthError(
            f"Could not reach John Deere to {action} ({type(exc).__name__}). Try again."
        ) from exc


async def refresh_tokens(
    http: httpx.AsyncClient, settings: Settings, store: TokenStore, tokens: Tokens
) -> Tokens:
    """Renew the access token, keep the old refresh token if Deere sends none, and save."""
    if not tokens.refresh_token:
        raise AuthError("The John Deere sign-in has expired. Run `deere-mcp login`.")
    response = await _token_request(
        http,
        settings,
        {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
        "renew the sign-in",
    )
    if response.status_code >= 400:
        raise AuthError(
            f"John Deere would not renew the sign-in ({_oauth_error_text(response)}). "
            "Run `deere-mcp login`."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise AuthError(
            "John Deere's sign-in service returned something that was not JSON."
        ) from exc
    new_tokens = _tokens_from_response(data, previous_refresh=tokens.refresh_token)
    store.save(new_tokens)
    logger.info("Renewed the John Deere sign-in.")
    return new_tokens


async def exchange_code(
    http: httpx.AsyncClient, settings: Settings, code: str, redirect_uri: str
) -> Tokens:
    """Trade the one-time code from the browser callback for tokens."""
    response = await _token_request(
        http,
        settings,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        "finish signing in",
    )
    if response.status_code >= 400:
        raise AuthError(
            f"John Deere did not accept the sign-in ({_oauth_error_text(response)}). "
            "Check the Application ID, Secret and Redirect URI, then run `deere-mcp login` again."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise AuthError(
            "John Deere's sign-in service returned something that was not JSON."
        ) from exc
    return _tokens_from_response(data, previous_refresh=None)


def authorization_url(endpoint: str, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"


def with_redirect(uri: str, redirect_uri: str) -> str:
    """Append ``redirect_uri=<callback>`` to a connections link, keeping its own query."""
    parts = urlsplit(uri)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "redirect_uri"
    ]
    query.append(("redirect_uri", redirect_uri))
    return urlunsplit(parts._replace(query=urlencode(query)))


# --- the local callback listener -------------------------------------------------------------

PAGE_DONE = "Signed in to John Deere. You can close this window and go back to the terminal."
PAGE_RETURNED = "Organization access saved. You can close this window and go back to the terminal."
PAGE_FAILED = "Sign-in did not finish. Check the terminal for details. You can close this window."
PAGE_BAD_STATE = (
    "This sign-in response does not belong to the sign-in that is running, so it was ignored. "
    "Use the link printed in the terminal, or run deere-mcp login again."
)
PAGE_NO_CODE = "No sign-in code was received. Run deere-mcp login again."
PAGE_IDLE = "No sign-in is waiting for this page. You can close this window."
PAGE_NOT_FOUND = "Not found."


@dataclass(frozen=True)
class CallbackOutcome:
    """What the listener does with one browser request."""

    status: int
    page: str
    code: str | None = None
    error: str | None = None
    finished: bool = False  # True when this request completes the step being waited for


def evaluate_callback(
    method: str, target: str, phase: Literal["code", "return"] | None, expected_state: str | None
) -> CallbackOutcome:
    """Decide how to answer one request to the local listener. Pure, so it is easy to test.

    In the "code" phase only ``GET /callback`` with the right ``state`` counts; anything else
    is answered and ignored, so a stale or forged request cannot finish the sign-in. In the
    "return" phase (after the organization-connection page) any ``GET /callback`` finishes.
    """
    parts = urlsplit(target)
    if parts.path != CALLBACK_PATH:
        return CallbackOutcome(404, PAGE_NOT_FOUND)
    if method != "GET":
        return CallbackOutcome(405, PAGE_NOT_FOUND)
    if phase is None:
        return CallbackOutcome(409, PAGE_IDLE)
    if phase == "return":
        return CallbackOutcome(200, PAGE_RETURNED, finished=True)
    params = parse_qs(parts.query)
    state = (params.get("state") or [""])[0]
    if not expected_state or not hmac.compare_digest(state.encode(), expected_state.encode()):
        return CallbackOutcome(400, PAGE_BAD_STATE)
    error = (params.get("error") or [""])[0]
    if error:
        description = (params.get("error_description") or [""])[0]
        text = f"{error}: {description}" if description else error
        return CallbackOutcome(200, PAGE_FAILED, error=text[:300], finished=True)
    code = (params.get("code") or [""])[0]
    if not code:
        return CallbackOutcome(400, PAGE_NO_CODE)
    return CallbackOutcome(200, PAGE_DONE, code=code, finished=True)


_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
}


def _render_page(status: int, message: str) -> bytes:
    body = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Deere Ops Center MCP</title>'
        '</head><body style="font-family:sans-serif;margin:3em"><p>'
        f"{message}</p></body></html>"
    ).encode()
    head = (
        f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    return head + body


class CallbackListener:
    """A tiny HTTP server on 127.0.0.1 that receives Deere's redirect back to this computer."""

    def __init__(self, port: int, host: str = CALLBACK_HOST) -> None:
        self._host = host
        self._requested_port = port
        self._server: asyncio.Server | None = None
        self._phase: Literal["code", "return"] | None = None
        self._state: str | None = None
        self._waiter: asyncio.Future[str | None] | None = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self._requested_port
        sockets = self._server.sockets
        chosen = next((s for s in sockets if s.family == socket.AF_INET), sockets[0])
        return int(chosen.getsockname()[1])

    async def start(self) -> None:
        # The redirect URI says "localhost", which some browsers resolve to ::1 first, so a
        # fixed port listens on both loopback addresses and falls back to IPv4 alone if the
        # IPv6 loopback is unavailable. An ephemeral port (0, used by tests) stays IPv4 only
        # so that `port` is unambiguous.
        hosts: list[str] = [self._host]
        if self._host == CALLBACK_HOST and self._requested_port != 0:
            hosts.append("::1")
        try:
            try:
                self._server = await asyncio.start_server(self._handle, hosts, self._requested_port)
            except OSError:
                if len(hosts) == 1:
                    raise
                self._server = await asyncio.start_server(
                    self._handle, hosts[:1], self._requested_port
                )
        except OSError as exc:
            raise AuthError(
                f"Could not listen on port {self._requested_port} for the sign-in "
                f"({exc.strerror}). "
                "Close the program using that port, or set DEERE_CALLBACK_PORT to another port "
                "and change the Redirect URI in your John Deere application to match."
            ) from exc

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
            self._server = None

    async def __aenter__(self) -> CallbackListener:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _arm(
        self, phase: Literal["code", "return"], state: str | None
    ) -> asyncio.Future[str | None]:
        self._phase = phase
        self._state = state
        self._waiter = asyncio.get_running_loop().create_future()
        return self._waiter

    async def _wait(
        self, waiter: asyncio.Future[str | None], timeout: float, what: str
    ) -> str | None:
        try:
            return await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            raise AuthError(f"Timed out waiting for {what}. Run `deere-mcp login` again.") from None
        finally:
            self._phase = None
            self._state = None
            self._waiter = None

    async def wait_for_code(self, state: str, timeout: float = LOGIN_TIMEOUT) -> str:
        """Wait for ``GET /callback?code=...&state=<state>`` and return the code."""
        waiter = self._arm("code", state)
        code = await self._wait(waiter, timeout, "the John Deere sign-in in the browser")
        assert code is not None
        return code

    def expect_return(self) -> asyncio.Future[str | None]:
        """Start accepting the browser's return from the organization-connection page."""
        return self._arm("return", None)

    async def wait_for_return(
        self, waiter: asyncio.Future[str | None], timeout: float = LOGIN_TIMEOUT
    ) -> None:
        await self._wait(waiter, timeout, "the organization step in the browser")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), 30)
            if not request_line:
                return
            for _ in range(100):  # read and discard headers
                line = await asyncio.wait_for(reader.readline(), 30)
                if line in (b"\r\n", b"\n", b""):
                    break
            parts = request_line.decode("latin-1").split()
            if len(parts) != 3:
                writer.write(_render_page(400, PAGE_NOT_FOUND))
                return
            method, target, _ = parts
            outcome = evaluate_callback(method, target, self._phase, self._state)
            writer.write(_render_page(outcome.status, outcome.page))
            await writer.drain()
            if outcome.status == 400 and self._phase == "code":
                logger.warning("Ignored a sign-in callback that did not match this sign-in.")
            waiter = self._waiter
            if outcome.finished and waiter is not None and not waiter.done():
                if outcome.error is not None:
                    waiter.set_exception(
                        AuthError(f"John Deere did not complete the sign-in ({outcome.error}).")
                    )
                else:
                    waiter.set_result(outcome.code)
        except (TimeoutError, ConnectionError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


# --- login / logout -------------------------------------------------------------------------


def _stderr_echo(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # a missing browser is not an error; the URL is printed anyway
        logger.debug("Could not open a browser.")


def _connections_link(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    for item in record.get("links") or []:
        if isinstance(item, dict) and item.get("rel") == "connections":
            uri = item.get("uri")
            return uri if isinstance(uri, str) and uri else None
    return None


async def find_connections_link(
    http: httpx.AsyncClient, settings: Settings, tokens: Tokens
) -> str | None:
    """Return the first organization 'connections' link, following pages up to the page cap."""
    url: str | None = f"{settings.base_url}/organizations"
    headers = {"Authorization": f"Bearer {tokens.access_token}", "Accept": DEERE_ACCEPT}
    for _ in range(settings.page_cap):
        if url is None or not is_allowed_host(urlsplit(url).hostname):
            return None
        response = await http.get(url, headers=headers)
        if response.status_code >= 400:
            raise AuthError(f"John Deere answered {response.status_code} to the organization list.")
        data = response.json()
        if not isinstance(data, dict):
            return None
        for org in data.get("values") or []:
            link = _connections_link(org)
            if link:
                return link
        url = None
        for item in data.get("links") or []:
            if isinstance(item, dict) and item.get("rel") == "nextPage":
                url = item.get("uri") if isinstance(item.get("uri"), str) else None
    return None


async def login(
    settings: Settings,
    store: TokenStore,
    *,
    open_browser: bool = True,
    http: httpx.AsyncClient | None = None,
    echo: Echo | None = None,
) -> Tokens:
    """Run the browser sign-in, save the tokens, and handle the organization-connection step.

    `echo` receives the lines meant for the person at the terminal (the CLI passes `print`);
    by default they go to stderr so nothing is ever written to stdout from here.
    """
    say = echo or _stderr_echo
    client_id, _ = _credentials(settings)
    own_http = http is None
    http = http or httpx.AsyncClient(timeout=30.0)
    try:
        endpoints = await discover(http)
        redirect_uri = settings.redirect_uri
        state = secrets.token_urlsafe(32)
        async with CallbackListener(settings.callback_port) as listener:
            url = authorization_url(endpoints.authorization, client_id, redirect_uri, state)
            say("Sign in to John Deere in your browser. If no browser opens, visit this link:")
            say(url)
            if open_browser:
                _open_browser(url)
            code = await listener.wait_for_code(state, LOGIN_TIMEOUT)
            tokens = await exchange_code(http, settings, code, redirect_uri)
            store.save(tokens)

            try:
                connections = await find_connections_link(http, settings, tokens)
            except (AuthError, httpx.HTTPError, ValueError) as exc:
                logger.warning("Could not check organization access (%s).", type(exc).__name__)
                connections = None
            if connections:
                host = (urlsplit(connections).hostname or "").lower()
                if urlsplit(connections).scheme != "https" or host != CONNECTIONS_HOST:
                    say(
                        "John Deere asks you to choose which organizations this application may "
                        f"see. Visit https://{CONNECTIONS_HOST} to do that."
                    )
                else:
                    link = with_redirect(connections, redirect_uri)
                    say(
                        "John Deere asks you to choose which organizations this application may "
                        "see. Choose them in the browser and confirm. If no browser opens, visit:"
                    )
                    say(link)
                    waiter = listener.expect_return()
                    if open_browser:
                        _open_browser(link)
                    try:
                        await listener.wait_for_return(waiter, LOGIN_TIMEOUT)
                    except AuthError:
                        raise AuthError(
                            "Timed out waiting for the organization step. You are signed in; "
                            f"to finish, run `deere-mcp login` again or visit https://{CONNECTIONS_HOST}."
                        ) from None
            return tokens
    finally:
        if own_http:
            await http.aclose()


async def logout(
    settings: Settings, store: TokenStore, *, http: httpx.AsyncClient | None = None
) -> bool:
    """Revoke the saved refresh token at Deere (best effort) and delete the token file."""
    existed = store.path.exists()
    tokens = store.load()
    if tokens is not None and tokens.refresh_token:
        own_http = http is None
        client = http or httpx.AsyncClient(timeout=30.0)
        try:
            await _revoke(client, settings, tokens.refresh_token)
        finally:
            if own_http:
                await client.aclose()
    store.delete()
    return existed


async def _revoke(http: httpx.AsyncClient, settings: Settings, refresh_token: str) -> None:
    try:
        client_id, client_secret = _credentials(settings)
        endpoints = await discover(http)
        if endpoints.revocation is None:
            logger.warning(
                "John Deere offers no revocation address; removed the local sign-in only."
            )
            return
        response = await http.post(
            endpoints.revocation,
            data={"token": refresh_token, "token_type_hint": "refresh_token"},
            auth=httpx.BasicAuth(client_id, client_secret),
        )
        if response.status_code >= 400:
            logger.warning(
                "John Deere answered %s to the revocation request; "
                "removed the local sign-in anyway.",
                response.status_code,
            )
    except (AuthError, httpx.HTTPError) as exc:
        logger.warning(
            "Could not revoke the sign-in at John Deere (%s); removed the local sign-in anyway.",
            type(exc).__name__,
        )
