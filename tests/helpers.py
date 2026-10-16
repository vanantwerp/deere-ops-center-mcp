"""Helpers for faking John Deere in tests. Import as ``from tests.helpers import ...``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

DEERE_CONTENT_TYPE = "application/vnd.deere.axiom.v3+json"
DISCOVERY_PATH = "/oauth2/aus78tnlaysMraFhC1t7/.well-known/oauth-authorization-server"
TOKEN_PATH = "/oauth2/aus78tnlaysMraFhC1t7/v1/token"
REVOKE_PATH = "/oauth2/aus78tnlaysMraFhC1t7/v1/revoke"
DISCOVERY_DOCUMENT = {
    "authorization_endpoint": "https://signin.johndeere.com/oauth2/aus78tnlaysMraFhC1t7/v1/authorize",
    "token_endpoint": "https://signin.johndeere.com" + TOKEN_PATH,
    "revocation_endpoint": "https://signin.johndeere.com" + REVOKE_PATH,
}

Responder = httpx.Response | Callable[[httpx.Request], httpx.Response]


def json_response(
    data: Any, status: int = 200, headers: Mapping[str, str] | None = None
) -> httpx.Response:
    """A Deere-style JSON response."""
    return httpx.Response(
        status, json=data, headers={"content-type": DEERE_CONTENT_TYPE, **(headers or {})}
    )


def deere_list(values: list[dict], next_uri: str | None = None) -> dict:
    """A Deere list body: ``values``, ``total`` and ``links`` (with ``nextPage`` when given)."""
    links = [{"rel": "self", "uri": "https://sandboxapi.deere.com/platform/self"}]
    if next_uri:
        links.append({"rel": "nextPage", "uri": next_uri})
    return {"values": values, "total": len(values), "links": links}


def _fresh(response: httpx.Response) -> httpx.Response:
    """A new copy of a canned response, so one route can answer many requests."""
    response.read()
    return httpx.Response(response.status_code, headers=response.headers, content=response.content)


class RouteHandler:
    """Callable for httpx.MockTransport. Matches on (METHOD, url.path) only; the query string is
    ignored for matching but every request is recorded so tests can assert on
    request.url.params and request.headers.

    ``url.path`` is percent-decoded, so an ID "1/2" sent as ``1%2F2`` matches a route added as
    ``/organizations/1/2/fields``; a route added in the encoded form (``1%2F2``) matches too.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], Responder] = {}

    def add(self, method: str, path: str, response: Responder) -> RouteHandler:
        """Answer ``method path`` (the full URL path) with `response`; returns self."""
        self._routes[(method.upper(), path)] = response
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method.upper(), request.url.path
        route = self._routes.get((method, path))
        if route is None:
            raw_path = request.url.raw_path.split(b"?", 1)[0].decode("ascii", "replace")
            route = self._routes.get((method, raw_path))
        if route is None:
            return httpx.Response(404, json={"message": f"no route for {method} {path}"})
        if isinstance(route, httpx.Response):
            return _fresh(route)
        return route(request)

    def add_oauth(self, token_response: Responder | None = None) -> RouteHandler:
        """Serve Deere's discovery document, and the token endpoint if a response is given."""
        self.add("GET", DISCOVERY_PATH, httpx.Response(200, json=DISCOVERY_DOCUMENT))
        if token_response is not None:
            self.add("POST", TOKEN_PATH, token_response)
        return self
