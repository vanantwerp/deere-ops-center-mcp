"""Token storage, discovery, refresh, the callback listener, login and logout."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from deere_mcp import auth
from deere_mcp.auth import (
    AuthError,
    CallbackListener,
    Tokens,
    TokenStore,
    discover,
    evaluate_callback,
    refresh_tokens,
    with_redirect,
)
from deere_mcp.config import Settings
from tests.helpers import (
    DISCOVERY_PATH,
    REVOKE_PATH,
    TOKEN_PATH,
    RouteHandler,
    deere_list,
    json_response,
)

# --- TokenStore -----------------------------------------------------------------------------


def test_token_store_round_trip(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "nested" / "tokens.json")
    assert store.load() is None
    tokens = Tokens("a", "r", expires_at=1234.5, scope="ag2 org1")
    store.save(tokens)
    assert store.load() == tokens
    store.save(Tokens("b", None, expires_at=1.0))
    assert store.load() == Tokens("b", None, expires_at=1.0)
    assert [p.name for p in store.path.parent.iterdir()] == ["tokens.json"]
    store.delete()
    assert store.load() is None
    store.delete()  # deleting twice is fine


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_token_file_is_owner_only(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "private" / "tokens.json")
    store.save(Tokens("a", "r", expires_at=time.time()))
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path.parent).st_mode) == 0o700


def test_damaged_token_file_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text("{not json", encoding="utf-8")
    assert TokenStore(path).load() is None
    path.write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
    assert TokenStore(path).load() is None


def test_expires_within() -> None:
    now = time.time()
    assert Tokens("a", None, expires_at=now + 30).expires_within(60)
    assert not Tokens("a", None, expires_at=now + 3600).expires_within(60)


# --- discovery and refresh ------------------------------------------------------------------


@pytest.mark.anyio
async def test_discovery_is_fetched_once() -> None:
    routes = RouteHandler().add_oauth()
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        first = await discover(http)
        second = await discover(http)
    assert first == second
    assert first.token.endswith(TOKEN_PATH)
    assert first.revocation is not None and first.revocation.endswith(REVOKE_PATH)
    assert [r.url.path for r in routes.requests] == [DISCOVERY_PATH]


@pytest.mark.anyio
async def test_refresh_keeps_old_refresh_token_when_absent(settings: Settings) -> None:
    store = TokenStore(settings.token_path)
    routes = RouteHandler().add_oauth(
        httpx.Response(200, json={"access_token": "new", "expires_in": 100})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        new = await refresh_tokens(http, settings, store, Tokens("old", "keep-me", 0.0))
    assert new.access_token == "new"
    assert new.refresh_token == "keep-me"
    assert 90 < new.expires_at - time.time() <= 100
    assert store.load() == new


@pytest.mark.anyio
async def test_refresh_uses_new_refresh_token_when_sent(settings: Settings) -> None:
    store = TokenStore(settings.token_path)
    routes = RouteHandler().add_oauth(
        httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 9})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        new = await refresh_tokens(http, settings, store, Tokens("old", "r1", 0.0))
    assert new.refresh_token == "r2"
    token_request = routes.requests[-1]
    assert token_request.method == "POST"
    assert parse_qs(token_request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["r1"],
    }


@pytest.mark.anyio
async def test_refresh_rejected_raises_auth_error(settings: Settings) -> None:
    routes = RouteHandler().add_oauth(
        httpx.Response(400, json={"error": "invalid_grant", "error_description": "expired"})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        with pytest.raises(AuthError, match="invalid_grant"):
            await refresh_tokens(
                http, settings, TokenStore(settings.token_path), Tokens("a", "r", 0)
            )


@pytest.mark.anyio
async def test_missing_credentials_say_run_setup(settings: Settings) -> None:
    bare = replace(settings, client_id=None, client_secret=None)
    routes = RouteHandler().add_oauth()
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        with pytest.raises(AuthError, match="Run `deere-mcp setup` first."):
            await refresh_tokens(http, bare, TokenStore(bare.token_path), Tokens("a", "r", 0))
        with pytest.raises(AuthError, match="Run `deere-mcp setup` first."):
            await auth.login(bare, TokenStore(bare.token_path), open_browser=False, http=http)


# --- callback evaluation --------------------------------------------------------------------


def test_callback_with_wrong_state_is_rejected() -> None:
    outcome = evaluate_callback("GET", "/callback?code=abc&state=wrong", "code", "right")
    assert outcome.status == 400
    assert outcome.code is None
    assert not outcome.finished


def test_callback_without_state_is_rejected() -> None:
    outcome = evaluate_callback("GET", "/callback?code=abc", "code", "right")
    assert outcome.status == 400 and not outcome.finished


def test_callback_with_right_state_returns_code() -> None:
    outcome = evaluate_callback("GET", "/callback?code=abc&state=right", "code", "right")
    assert outcome.status == 200
    assert outcome.code == "abc"
    assert outcome.finished
    assert "close this window" in outcome.page


def test_callback_error_finishes_with_error() -> None:
    outcome = evaluate_callback(
        "GET", "/callback?error=access_denied&error_description=no&state=right", "code", "right"
    )
    assert outcome.finished and outcome.code is None
    assert outcome.error == "access_denied: no"
    assert "access_denied" not in outcome.page  # nothing from the URL is echoed into the page


def test_callback_other_paths_and_phases() -> None:
    assert evaluate_callback("GET", "/favicon.ico", "code", "s").status == 404
    assert evaluate_callback("POST", "/callback?state=s&code=c", "code", "s").status == 405
    assert evaluate_callback("GET", "/callback?code=c&state=s", None, None).status == 409
    returned = evaluate_callback("GET", "/callback", "return", None)
    assert returned.status == 200 and returned.finished


def test_with_redirect_appends_callback() -> None:
    link = with_redirect(
        "https://connections.deere.com/connections/abc/select-organizations?x=1",
        "http://localhost:9090/callback",
    )
    parts = urlsplit(link)
    assert parts.netloc == "connections.deere.com"
    assert parse_qs(parts.query) == {"x": ["1"], "redirect_uri": ["http://localhost:9090/callback"]}


# --- the real listener over a socket --------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.anyio
async def test_listener_ignores_wrong_state_then_accepts_right_one() -> None:
    async with CallbackListener(0) as listener:
        base = f"http://127.0.0.1:{listener.port}/callback"
        waiting = asyncio.create_task(listener.wait_for_code("good-state", timeout=5))
        await asyncio.sleep(0)
        async with httpx.AsyncClient(trust_env=False) as browser:
            bad = await browser.get(base, params={"code": "evil", "state": "bad-state"})
            assert bad.status_code == 400
            assert not waiting.done()
            good = await browser.get(base, params={"code": "real", "state": "good-state"})
            assert good.status_code == 200
            assert "close this window" in good.text
        assert await waiting == "real"


@pytest.mark.anyio
async def test_listener_times_out() -> None:
    async with CallbackListener(0) as listener:
        with pytest.raises(AuthError, match="Timed out"):
            await listener.wait_for_code("s", timeout=0.05)


@pytest.mark.anyio
async def test_listener_port_in_use_is_a_plain_error() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        with pytest.raises(AuthError, match="DEERE_CALLBACK_PORT"):
            async with CallbackListener(port):
                pass


# --- full login and logout ------------------------------------------------------------------


def _s256(verifier: str) -> str:
    """The PKCE challenge for `verifier`, worked out here rather than by the code under test."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _wait_for_line(lines: list[str], prefix: str) -> str:
    for _ in range(500):
        for line in lines:
            if line.startswith(prefix):
                return line
        await asyncio.sleep(0.01)
    raise AssertionError(f"no line starting with {prefix!r} in {lines!r}")


@pytest.mark.anyio
async def test_login_flow_with_organization_connection(settings: Settings) -> None:
    port = _free_port()
    settings = replace(settings, callback_port=port)
    store = TokenStore(settings.token_path)
    redirect_uri = f"http://localhost:{port}/callback"
    token_forms: list[dict[str, list[str]]] = []

    def token_endpoint(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        token_forms.append(form)
        if form["grant_type"] == ["refresh_token"]:
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-acc-789",
                    "refresh_token": "tok-ref-012",
                    "expires_in": 9,
                },
            )
        return httpx.Response(
            200,
            json={"access_token": "tok-acc-123", "refresh_token": "tok-ref-456", "expires_in": 9},
        )

    connections = "https://connections.deere.com/connections/id/select-organizations"
    routes = RouteHandler().add_oauth(token_endpoint)
    routes.add(
        "GET",
        "/platform/organizations",
        json_response(
            deere_list(
                [
                    {"id": "1", "name": "Home", "member": True, "links": []},
                    {
                        "id": "2",
                        "name": "Other",
                        "member": True,
                        "links": [{"rel": "connections", "uri": connections}],
                    },
                ]
            )
        ),
    )
    lines: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        task = asyncio.create_task(
            auth.login(settings, store, open_browser=False, http=http, echo=lines.append)
        )
        auth_url = await _wait_for_line(lines, "https://signin.johndeere.com")
        query = parse_qs(urlsplit(auth_url).query)
        assert query["response_type"] == ["code"]
        assert query["client_id"] == ["id"]
        assert query["redirect_uri"] == [redirect_uri]
        assert query["scope"] == ["ag2 eq1 org1 work1 offline_access"]
        assert query["prompt"] == ["login"]
        assert query["code_challenge_method"] == ["S256"]
        challenge = query["code_challenge"][0]
        state = query["state"][0]
        assert len(state) >= 32

        callback = f"http://127.0.0.1:{port}/callback"
        async with httpx.AsyncClient(trust_env=False) as browser:
            forged = await browser.get(callback, params={"code": "x", "state": "forged"})
            assert forged.status_code == 400
            first = await browser.get(callback, params={"code": "the-code", "state": state})
            assert first.status_code == 200
            link = await _wait_for_line(lines, "https://connections.deere.com")
            assert parse_qs(urlsplit(link).query)["redirect_uri"] == [redirect_uri]
            assert not task.done()
            back = await browser.get(callback)
            assert back.status_code == 200
        tokens = await asyncio.wait_for(task, 5)

    # The token left behind is the one refreshed after the organization step.
    assert tokens.access_token == "tok-acc-789" and tokens.refresh_token == "tok-ref-012"
    assert store.load() == tokens
    exchange, renewal = token_forms
    # PKCE: the code exchange carries the verifier behind the challenge the browser was sent.
    verifier = exchange.pop("code_verifier")[0]
    assert 43 <= len(verifier) <= 128
    assert _s256(verifier) == challenge
    assert exchange == {
        "grant_type": ["authorization_code"],
        "code": ["the-code"],
        "redirect_uri": [redirect_uri],
    }
    assert renewal == {"grant_type": ["refresh_token"], "refresh_token": ["tok-ref-456"]}
    org_request = next(r for r in routes.requests if r.url.path == "/platform/organizations")
    assert org_request.headers["authorization"] == "Bearer tok-acc-123"
    assert not any(
        "tok-acc" in line or "tok-ref" in line or "secret" in line or verifier in line
        for line in lines
    )


@pytest.mark.anyio
async def test_login_keeps_exchanged_tokens_when_the_refresh_fails(settings: Settings) -> None:
    port = _free_port()
    settings = replace(settings, callback_port=port)
    store = TokenStore(settings.token_path)

    def token_endpoint(request: httpx.Request) -> httpx.Response:
        if parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={"access_token": "tok-acc-123", "refresh_token": "tok-ref-456", "expires_in": 9},
        )

    connections = "https://connections.deere.com/connections/id/select-organizations"
    routes = RouteHandler().add_oauth(token_endpoint)
    routes.add(
        "GET",
        "/platform/organizations",
        json_response(
            deere_list(
                [
                    {
                        "id": "2",
                        "member": True,
                        "links": [{"rel": "connections", "uri": connections}],
                    }
                ]
            )
        ),
    )
    lines: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        task = asyncio.create_task(
            auth.login(settings, store, open_browser=False, http=http, echo=lines.append)
        )
        auth_url = await _wait_for_line(lines, "https://signin.johndeere.com")
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        callback = f"http://127.0.0.1:{port}/callback"
        async with httpx.AsyncClient(trust_env=False) as browser:
            await browser.get(callback, params={"code": "c", "state": state})
            await _wait_for_line(lines, "https://connections.deere.com")
            await browser.get(callback)
        tokens = await asyncio.wait_for(task, 5)

    assert tokens.access_token == "tok-acc-123" and tokens.refresh_token == "tok-ref-456"
    assert store.load() == tokens
    report = next(line for line in lines if line.startswith("Could not renew the sign-in"))
    assert "deere-mcp login" in report and "403" in report


@pytest.mark.anyio
async def test_login_without_connections_link_finishes_after_code(settings: Settings) -> None:
    port = _free_port()
    settings = replace(settings, callback_port=port)
    routes = RouteHandler().add_oauth(
        httpx.Response(200, json={"access_token": "acc", "refresh_token": "ref", "expires_in": 60})
    )
    routes.add("GET", "/platform/organizations", json_response(deere_list([{"id": "1"}])))
    lines: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        task = asyncio.create_task(
            auth.login(
                settings,
                TokenStore(settings.token_path),
                open_browser=False,
                http=http,
                echo=lines.append,
            )
        )
        auth_url = await _wait_for_line(lines, "https://signin.johndeere.com")
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        async with httpx.AsyncClient(trust_env=False) as browser:
            await browser.get(
                f"http://127.0.0.1:{port}/callback", params={"code": "c", "state": state}
            )
        tokens = await asyncio.wait_for(task, 5)
    assert tokens.access_token == "acc"
    assert not any(line.startswith("https://connections") for line in lines)


CONNECTIONS_LINK = "https://connections.deere.com/connections/id/select-organizations"


def _org(name: str, *, member: bool, connected: bool) -> dict:
    links = [] if connected else [{"rel": "connections", "uri": CONNECTIONS_LINK}]
    return {"id": name.lower(), "name": name, "member": member, "links": links}


def _org_lists(*pages: list[dict]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer each organization list request with the next page of `pages`; the last repeats."""
    answers = iter(pages)
    latest: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal latest
        latest = next(answers, latest)
        return json_response(deere_list(latest))

    return handler


def _login_routes(*pages: list[dict]) -> RouteHandler:
    def token_endpoint(request: httpx.Request) -> httpx.Response:
        refreshed = parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]
        return httpx.Response(
            200,
            json={
                "access_token": "tok-acc-2" if refreshed else "tok-acc-1",
                "refresh_token": "tok-ref",
                "expires_in": 9,
            },
        )

    routes = RouteHandler().add_oauth(token_endpoint)
    routes.add("GET", "/platform/organizations", _org_lists(*pages))
    return routes


async def _login_to_the_chooser_step(
    settings: Settings, routes: RouteHandler, lines: list[str], *, come_back: bool
) -> Tokens:
    """Sign in, then (when the chooser opened) return from it or leave it open."""
    port = settings.callback_port
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        task = asyncio.create_task(
            auth.login(
                settings,
                TokenStore(settings.token_path),
                open_browser=False,
                http=http,
                echo=lines.append,
            )
        )
        auth_url = await _wait_for_line(lines, "https://signin.johndeere.com")
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        callback = f"http://127.0.0.1:{port}/callback"
        async with httpx.AsyncClient(trust_env=False) as browser:
            await browser.get(callback, params={"code": "c", "state": state})
            if come_back:
                await _wait_for_line(lines, "https://connections.deere.com")
                await browser.get(callback)
        return await asyncio.wait_for(task, 5)


@pytest.mark.anyio
async def test_login_skips_the_chooser_for_an_organization_the_user_cannot_connect(
    settings: Settings,
) -> None:
    settings = replace(settings, callback_port=_free_port())
    routes = _login_routes(
        [
            _org("Home", member=True, connected=True),
            _org("Neighbor", member=False, connected=False),
        ]
    )
    lines: list[str] = []
    tokens = await _login_to_the_chooser_step(settings, routes, lines, come_back=False)

    assert tokens.access_token == "tok-acc-1"
    assert not any(line.startswith("https://connections") for line in lines)
    assert any("Connected: Home." in line for line in lines)
    assert not any("Neighbor" in line for line in lines)
    assert [r.url.path for r in routes.requests].count("/platform/organizations") == 1


@pytest.mark.anyio
async def test_login_reports_connected_organizations_after_the_chooser(
    settings: Settings,
) -> None:
    settings = replace(settings, callback_port=_free_port())
    routes = _login_routes(
        [_org("Home", member=True, connected=True), _org("Barn", member=True, connected=False)],
        [_org("Home", member=True, connected=True), _org("Barn", member=True, connected=True)],
    )
    lines: list[str] = []
    tokens = await _login_to_the_chooser_step(settings, routes, lines, come_back=True)

    assert tokens.access_token == "tok-acc-2"
    report = next(line for line in lines if line.startswith("Organization access saved."))
    assert "Connected: Home, Barn." in report and "Not connected" not in report
    assert not any("different John Deere user" in line for line in lines)
    org_requests = [r for r in routes.requests if r.url.path == "/platform/organizations"]
    assert [r.headers["authorization"] for r in org_requests] == [
        "Bearer tok-acc-1",
        "Bearer tok-acc-2",  # the second read uses the renewed sign-in
    ]


@pytest.mark.anyio
async def test_login_says_what_to_do_when_the_chooser_left_an_organization_unconnected(
    settings: Settings,
) -> None:
    settings = replace(settings, callback_port=_free_port())
    still_open = [
        _org("Home", member=True, connected=True),
        _org("Barn", member=True, connected=False),
    ]
    routes = _login_routes(still_open, still_open)
    lines: list[str] = []
    tokens = await _login_to_the_chooser_step(settings, routes, lines, come_back=True)

    assert tokens.access_token == "tok-acc-2"
    report = next(line for line in lines if "Not connected: Barn." in line)
    assert "Connected: Home." in report
    assert "different John Deere user" in report
    assert "https://connections.deere.com" in report and "deere-mcp login" in report
    assert not any(line.startswith("Organization access saved.") for line in lines)


@pytest.mark.anyio
async def test_login_gives_up_on_a_closed_chooser_and_says_where_things_stand(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "CHOOSER_TIMEOUT", 0.1)
    settings = replace(settings, callback_port=_free_port())
    store = TokenStore(settings.token_path)
    routes = _login_routes(
        [_org("Home", member=True, connected=True), _org("Barn", member=True, connected=False)]
    )
    lines: list[str] = []
    with pytest.raises(AuthError) as excinfo:
        await _login_to_the_chooser_step(settings, routes, lines, come_back=False)

    message = str(excinfo.value)
    assert "Your sign-in is saved." in message
    assert "Connected: Home." in message and "Not connected: Barn." in message
    assert "deere-mcp login" in message
    assert store.load() is not None


@pytest.mark.anyio
async def test_logout_revokes_and_deletes(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler().add_oauth().add("POST", REVOKE_PATH, httpx.Response(200))
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        assert await auth.logout(settings, TokenStore(settings.token_path), http=http) is True
    assert not settings.token_path.exists()
    revoke = routes.requests[-1]
    assert revoke.url.path == REVOKE_PATH
    assert parse_qs(revoke.content.decode()) == {
        "token": ["refresh-1"],
        "token_type_hint": ["refresh_token"],
    }
    assert revoke.headers["authorization"].startswith("Basic ")


@pytest.mark.anyio
async def test_logout_ignores_revocation_failure(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler().add_oauth().add("POST", REVOKE_PATH, httpx.Response(500))
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        assert await auth.logout(settings, TokenStore(settings.token_path), http=http) is True
    assert not settings.token_path.exists()


@pytest.mark.anyio
async def test_logout_without_file_returns_false(settings: Settings) -> None:
    routes = RouteHandler()
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        assert await auth.logout(settings, TokenStore(settings.token_path), http=http) is False
    assert routes.requests == []


@pytest.mark.parametrize(
    ("refusal", "expected"),
    [
        (
            httpx.Response(403, json={"message": "nope"}),
            ["403", "connections.deere.com", "Access tab"],
        ),
        (
            httpx.Response(
                403, text="Forbidden", headers={"response-code-details": "ext_authz_denied"}
            ),
            ["Organizations Read", "Access tab"],
        ),
    ],
    ids=["from-deere", "from-gateway"],
)
@pytest.mark.anyio
async def test_login_reports_a_failed_organization_check(
    settings: Settings, refusal: httpx.Response, expected: list[str]
) -> None:
    port = _free_port()
    settings = replace(settings, callback_port=port)
    store = TokenStore(settings.token_path)
    routes = RouteHandler().add_oauth(
        httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 9})
    )
    routes.add("GET", "/platform/organizations", refusal)
    lines: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes)) as http:
        task = asyncio.create_task(
            auth.login(settings, store, open_browser=False, http=http, echo=lines.append)
        )
        auth_url = await _wait_for_line(lines, "https://signin.johndeere.com")
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        async with httpx.AsyncClient(trust_env=False) as browser:
            await browser.get(
                f"http://127.0.0.1:{port}/callback", params={"code": "c", "state": state}
            )
        tokens = await asyncio.wait_for(task, 5)

    assert store.load() == tokens  # signed in regardless
    report = next(line for line in lines if line.startswith("Could not check organization access"))
    for text in expected:
        assert text in report
