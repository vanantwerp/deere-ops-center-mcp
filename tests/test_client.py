"""DeereClient: URLs, the host allowlist, paging, errors and token refresh."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from deere_mcp.auth import Tokens, TokenStore
from deere_mcp.client import DeereClient, DeereError
from deere_mcp.config import Settings
from tests.helpers import RouteHandler, deere_list, json_response

pytestmark = pytest.mark.anyio

ORGS = "/platform/organizations"


@asynccontextmanager
async def make_client(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> AsyncIterator[DeereClient]:
    client = DeereClient(
        settings, TokenStore(settings.token_path), transport=httpx.MockTransport(handler)
    )
    try:
        yield client
    finally:
        await client.aclose()


def _client(settings: Settings) -> DeereClient:
    return DeereClient(settings, TokenStore(settings.token_path))


async def test_url_quotes_every_segment(settings: Settings) -> None:
    client = _client(settings)
    url = client.url("organizations", "1/2", "fields", "a b?c#d")
    assert url == ("https://sandboxapi.deere.com/platform/organizations/1%2F2/fields/a%20b%3Fc%23d")
    assert httpx.URL(url).raw_path.startswith(b"/platform/organizations/1%2F2/fields")
    assert (
        client.equipment_url("equipment", "9") == "https://equipmentapi.deere.com/isg/equipment/9"
    )
    await client.aclose()


async def test_url_rejects_empty_and_dot_segments(settings: Settings) -> None:
    client = _client(settings)
    for bad in ("", "..", "."):
        with pytest.raises(DeereError):
            client.url("organizations", bad, "fields")
    await client.aclose()


async def test_query_rendering(settings: Settings) -> None:
    client = _client(settings)
    url = client.url(
        "x",
        query={
            "skip": None,
            "archived": False,
            "lastKnown": True,
            "ids": ["1", "2", "3"],
            "itemLimit": 50,
            "startDate": "2024-05-01T00:00:00Z",
        },
    )
    params = httpx.URL(url).params
    assert "skip" not in params
    assert params["archived"] == "false"
    assert params["lastKnown"] == "true"
    assert params["ids"] == "1,2,3"
    assert "ids=1,2,3" in url
    assert params["itemLimit"] == "50"
    assert params["startDate"] == "2024-05-01T00:00:00Z"
    assert client.url("x", query={"a": None}) == "https://sandboxapi.deere.com/platform/x"
    await client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x",
        "https://sandboxapi.deere.com.evil.example/x",
        "http://sandboxapi.deere.com/platform/x",
        "https://deere.com.evil/x",
    ],
)
async def test_host_allowlist_refuses_before_sending(
    settings: Settings, saved_tokens: Tokens, url: str
) -> None:
    routes = RouteHandler()
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="Refused"):
            await client.get_json(url)
    assert routes.requests == []


async def test_allowed_hosts_get_auth_and_unit_headers(
    settings: Settings, saved_tokens: Tokens
) -> None:
    from dataclasses import replace

    settings = replace(settings, uom="METRIC", yield_pref="MASS")
    routes = RouteHandler()
    routes.add("GET", ORGS, json_response(deere_list([{"id": "1"}])))
    routes.add("GET", "/v1/thing", json_response({"ok": True}))
    async with make_client(settings, routes) as client:
        await client.get_json(client.url("organizations"))
        await client.get_json("https://other.deere.com/v1/thing", accept="application/json")
    first, second = routes.requests
    assert first.headers["authorization"] == "Bearer access-1"
    assert first.headers["accept"] == "application/vnd.deere.axiom.v3+json"
    assert first.headers["accept-uom-system"] == "METRIC"
    assert first.headers["accept-yield-preference"] == "MASS"
    assert second.headers["accept"] == "application/json"


async def test_unit_headers_absent_when_not_configured(
    settings: Settings, saved_tokens: Tokens
) -> None:
    routes = RouteHandler().add("GET", ORGS, json_response(deere_list([])))
    async with make_client(settings, routes) as client:
        await client.get_json(client.url("organizations"))
    assert "accept-uom-system" not in routes.requests[0].headers
    assert "accept-yield-preference" not in routes.requests[0].headers


async def test_get_all_follows_next_page(settings: Settings, saved_tokens: Tokens) -> None:
    base = "https://sandboxapi.deere.com/platform/organizations"

    def pages(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        next_uri = f"{base}?page={page + 1}" if page < 2 else None
        return json_response(deere_list([{"id": str(page)}], next_uri))

    routes = RouteHandler().add("GET", ORGS, pages)
    async with make_client(settings, routes) as client:
        result = await client.get_all(f"{base}?page=1")
    assert [item["id"] for item in result.items] == ["1", "2"]
    assert result.pages == 2
    assert result.truncated is False


async def test_get_all_stops_at_page_cap(settings: Settings, saved_tokens: Tokens) -> None:
    base = "https://sandboxapi.deere.com/platform/organizations"

    def endless(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return json_response(deere_list([{"id": str(page)}], f"{base}?page={page + 1}"))

    routes = RouteHandler().add("GET", ORGS, endless)
    async with make_client(settings, routes) as client:
        result = await client.get_all(base)
    assert settings.page_cap == 3
    assert result.pages == 3
    assert result.truncated is True
    assert len(result.items) == 3
    assert len(routes.requests) == 3


async def test_get_all_refuses_next_page_off_deere(
    settings: Settings, saved_tokens: Tokens
) -> None:
    routes = RouteHandler().add(
        "GET", ORGS, json_response(deere_list([{"id": "1"}], "https://evil.example/next"))
    )
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="Refused"):
            await client.get_all(client.url("organizations"))


async def test_non_json_body_is_a_deere_error(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler().add(
        "GET",
        ORGS,
        httpx.Response(200, text="<html>oops</html>", headers={"content-type": "text/html"}),
    )
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="not JSON"):
            await client.get_json(client.url("organizations"))


async def test_error_status_message_is_readable_and_trimmed(
    settings: Settings, saved_tokens: Tokens
) -> None:
    long_text = "Organization not connected. " + "x" * 2000
    routes = RouteHandler().add(
        "GET", ORGS, json_response({"errors": [{"message": long_text}]}, status=403)
    )
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError) as exc:
            await client.get_json(client.url("organizations"))
    assert exc.value.status == 403
    assert len(exc.value.message) <= 500
    assert len(str(exc.value)) <= 500
    assert "403" in str(exc.value)
    assert "Organization not connected." in str(exc.value)


async def test_not_signed_in(settings: Settings) -> None:
    routes = RouteHandler()
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="Not signed in"):
            await client.get_json(client.url("organizations"))
    assert routes.requests == []


def _token_endpoint(calls: list[httpx.Request], body: dict) -> Callable:
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=body)

    return respond


async def test_401_refreshes_once_retries_and_saves(
    settings: Settings, saved_tokens: Tokens
) -> None:
    token_calls: list[httpx.Request] = []

    def orgs(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer access-1":
            return json_response({"message": "expired"}, status=401)
        return json_response(deere_list([{"id": "1"}]))

    routes = RouteHandler().add("GET", ORGS, orgs)
    routes.add_oauth(_token_endpoint(token_calls, {"access_token": "access-2", "expires_in": 3600}))
    async with make_client(settings, routes) as client:
        data = await client.get_json(client.url("organizations"))
    assert data["values"] == [{"id": "1"}]
    assert len(token_calls) == 1
    form = token_calls[0].content.decode()
    assert "grant_type=refresh_token" in form and "refresh_token=refresh-1" in form
    assert token_calls[0].headers["authorization"].startswith("Basic ")
    saved = TokenStore(settings.token_path).load()
    assert saved is not None
    assert saved.access_token == "access-2"
    assert saved.refresh_token == "refresh-1"  # kept: the response had none
    api_calls = [r for r in routes.requests if r.url.path == ORGS]
    assert [r.headers["authorization"] for r in api_calls] == ["Bearer access-1", "Bearer access-2"]


async def test_second_401_is_an_error_not_a_loop(settings: Settings, saved_tokens: Tokens) -> None:
    token_calls: list[httpx.Request] = []
    routes = RouteHandler().add("GET", ORGS, json_response({"message": "nope"}, status=401))
    routes.add_oauth(_token_endpoint(token_calls, {"access_token": "access-2", "expires_in": 3600}))
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError) as exc:
            await client.get_json(client.url("organizations"))
    assert exc.value.status == 401
    assert "deere-mcp login" in str(exc.value)
    assert len(token_calls) == 1
    assert len([r for r in routes.requests if r.url.path == ORGS]) == 2


async def test_proactive_refresh_near_expiry(settings: Settings) -> None:
    store = TokenStore(settings.token_path)
    store.save(Tokens("old-access", "refresh-1", expires_at=time.time() + 30))
    token_calls: list[httpx.Request] = []
    routes = RouteHandler().add("GET", ORGS, json_response(deere_list([])))
    routes.add_oauth(
        _token_endpoint(
            token_calls,
            {"access_token": "new-access", "refresh_token": "refresh-2", "expires_in": 43200},
        )
    )
    async with make_client(settings, routes) as client:
        await client.get_json(client.url("organizations"))
    api_calls = [r for r in routes.requests if r.url.path == ORGS]
    assert [r.headers["authorization"] for r in api_calls] == ["Bearer new-access"]
    assert len(token_calls) == 1
    saved = store.load()
    assert saved is not None and saved.refresh_token == "refresh-2"
    assert not saved.expires_within(3600)


async def test_concurrent_401s_refresh_only_once(settings: Settings, saved_tokens: Tokens) -> None:
    import asyncio

    token_calls: list[httpx.Request] = []

    def orgs(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer access-1":
            return json_response({}, status=401)
        return json_response(deere_list([]))

    routes = RouteHandler().add("GET", ORGS, orgs)
    routes.add_oauth(_token_endpoint(token_calls, {"access_token": "access-2", "expires_in": 3600}))
    async with make_client(settings, routes) as client:
        await asyncio.gather(*(client.get_json(client.url("organizations")) for _ in range(5)))
    assert len(token_calls) == 1


async def test_refresh_failure_says_to_log_in(settings: Settings, saved_tokens: Tokens) -> None:
    from deere_mcp.auth import AuthError

    routes = RouteHandler().add("GET", ORGS, json_response({}, status=401))
    routes.add_oauth(httpx.Response(400, json={"error": "invalid_grant"}))
    async with make_client(settings, routes) as client:
        with pytest.raises(AuthError, match="deere-mcp login"):
            await client.get_json(client.url("organizations"))


async def test_get_bytes_returns_body_and_type(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler().add(
        "GET",
        "/platform/map",
        httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}),
    )
    async with make_client(settings, routes) as client:
        body, content_type = await client.get_bytes(client.url("map"), accept="image/png")
    assert body == b"\x89PNG"
    assert content_type == "image/png"
    assert routes.requests[0].headers["accept"] == "image/png"


async def test_request_returns_redirect_without_following(
    settings: Settings, saved_tokens: Tokens
) -> None:
    routes = RouteHandler().add(
        "GET",
        "/platform/fieldOps/1",
        httpx.Response(307, headers={"location": "https://files.example.com/x.zip"}),
    )
    async with make_client(settings, routes) as client:
        response = await client.request("GET", client.url("fieldOps", "1"))
    assert response.status_code == 307
    assert len(routes.requests) == 1


async def test_followed_redirect_off_deere_is_refused(
    settings: Settings, saved_tokens: Tokens
) -> None:
    routes = RouteHandler().add(
        "GET", "/platform/a", httpx.Response(302, headers={"location": "https://evil.example/b"})
    )
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="Refused"):
            await client.request("GET", client.url("a"), follow_redirects=True)
    assert len(routes.requests) == 1


async def test_get_json_follows_a_redirect_on_a_deere_host(
    settings: Settings, saved_tokens: Tokens
) -> None:
    routes = (
        RouteHandler()
        .add(
            "GET",
            "/platform/old",
            httpx.Response(302, headers={"location": "https://sandboxapi.deere.com/platform/new"}),
        )
        .add("GET", "/platform/new", json_response({"id": "moved"}))
    )
    async with make_client(settings, routes) as client:
        data = await client.get_json(client.url("old"))
    assert data == {"id": "moved"}
    assert len(routes.requests) == 2


async def test_logout_while_running_is_noticed(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler().add("GET", ORGS, json_response(deere_list([{"id": "1"}])))
    async with make_client(settings, routes) as client:
        await client.get_json(client.url("organizations"))
        TokenStore(settings.token_path).delete()  # what `deere-mcp logout` does
        with pytest.raises(DeereError, match="Not signed in"):
            await client.get_json(client.url("organizations"))
    assert len(routes.requests) == 1


async def test_malformed_url_is_a_readable_error(settings: Settings, saved_tokens: Tokens) -> None:
    routes = RouteHandler()
    async with make_client(settings, routes) as client:
        with pytest.raises(DeereError, match="not a valid web address"):
            await client.get_json("https://sandboxapi.deere.com:99x/platform/organizations")
    assert routes.requests == []
