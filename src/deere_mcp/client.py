"""The one HTTP client for John Deere's APIs: auth headers, refresh, host allowlist, paging."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from deere_mcp.auth import DEERE_ACCEPT, NOT_SET_UP, Tokens, TokenStore, refresh_tokens
from deere_mcp.config import Settings, is_allowed_host

logger = logging.getLogger(__name__)

MAX_MESSAGE = 500
REFRESH_MARGIN = 60.0  # refresh proactively when the token expires within this many seconds
MAX_REDIRECTS = 5


def _trim(text: str, limit: int = MAX_MESSAGE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class DeereError(Exception):
    """A failed Deere request, with a readable message of at most 500 characters."""

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        self.message = _trim(message)
        super().__init__(self.message)


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    truncated: bool  # True when the page cap stopped the walk
    pages: int


_HINTS = {
    401: "The John Deere sign-in has expired or was revoked. Run `deere-mcp login`.",
    403: (
        "John Deere refused access. If every request is refused, the application's API access "
        "has not been granted yet (developer.deere.com, the application's Access tab). If only "
        "one organization's data is refused, connect the application to it at "
        "connections.deere.com, or run `deere-mcp login` and complete the organization step."
    ),
    404: "Not found. Check the ID; IDs come from the list tools.",
    429: "Too many requests to John Deere. Wait a minute and try again.",
}


def _deere_text(response: httpx.Response) -> str:
    """Deere's own error text from a failed response, if there is any readable text."""
    content_type = response.headers.get("content-type", "").lower()
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        found: list[str] = []
        errors = data.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict) and item.get("message"):
                    found.append(str(item["message"]))
                elif isinstance(item, str):
                    found.append(item)
        for key in ("message", "error_description", "error", "detail", "title"):
            value = data.get(key)
            if isinstance(value, str) and value and value not in found:
                found.append(value)
        if found:
            return "; ".join(found)
    if "html" in content_type:
        return ""
    if data is not None:
        return str(data)
    try:
        return response.text
    except Exception:  # undecodable body: nothing readable to pass on
        return ""


def error_from_response(response: httpx.Response) -> DeereError:
    status = response.status_code
    reason = response.reason_phrase or ""
    parts = [f"John Deere returned {status} {reason}".rstrip() + "."]
    hint = _HINTS.get(status)
    if hint:
        parts.append(hint)
    text = _trim(_deere_text(response), MAX_MESSAGE)
    if text:
        parts.append(f"Deere said: {text}")
    return DeereError(status, " ".join(parts))


def _render_query(query: Mapping[str, Any] | None) -> str:
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = [v for v in value if v is not None]
            if not items:
                continue
            rendered = ",".join(
                ("true" if v else "false") if isinstance(v, bool) else str(v) for v in items
            )
        else:
            rendered = str(value)
        pairs.append((key, rendered))
    return urlencode(pairs, safe=",") if pairs else ""


def _build(base: str, segments: tuple[str, ...], query: Mapping[str, Any] | None) -> str:
    quoted: list[str] = []
    for segment in segments:
        text = str(segment)
        if text.strip() in ("", ".", ".."):
            raise DeereError(None, f"An ID or path part was empty or invalid ({text!r}).")
        quoted.append(quote(text, safe=""))
    url = base + ("/" + "/".join(quoted) if quoted else "")
    rendered = _render_query(query)
    return f"{url}?{rendered}" if rendered else url


def _next_page(data: Mapping[str, Any]) -> str | None:
    for item in data.get("links") or []:
        if isinstance(item, Mapping) and item.get("rel") == "nextPage":
            uri = item.get("uri")
            if isinstance(uri, str) and uri:
                return uri
    return None


class DeereClient:
    """Talks to John Deere. Every request goes through `request`, which enforces the rules."""

    EQUIPMENT_BASE = "https://equipmentapi.deere.com/isg"

    def __init__(
        self,
        settings: Settings,
        store: TokenStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self.store = store
        self._timeout = timeout
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)
        self._tokens: Tokens | None = None
        self._lock = asyncio.Lock()

    # -- URLs ------------------------------------------------------------------------------

    def url(self, *segments: str, query: Mapping[str, Any] | None = None) -> str:
        """``base_url/seg1/seg2?query``; each segment is percent-quoted, so IDs cannot add paths."""
        return _build(self.settings.base_url, segments, query)

    def equipment_url(self, *segments: str, query: Mapping[str, Any] | None = None) -> str:
        """The same as `url`, against the equipment API."""
        return _build(self.EQUIPMENT_BASE, segments, query)

    # -- tokens ----------------------------------------------------------------------------

    async def _current_tokens(self) -> Tokens:
        if self._tokens is not None and not self.store.path.exists():
            self._tokens = None  # `deere-mcp logout` removed the sign-in while we were running
        if self._tokens is None:
            self._tokens = self.store.load()
        if self._tokens is None:
            if not self.settings.client_id or not self.settings.client_secret:
                raise DeereError(None, f"Not set up yet. {NOT_SET_UP}")
            raise DeereError(None, "Not signed in. Run `deere-mcp login`.")
        if self._tokens.expires_within(REFRESH_MARGIN):
            return await self._refresh(self._tokens)
        return self._tokens

    async def _refresh(self, stale: Tokens) -> Tokens:
        """Refresh once, even if several requests need it at the same moment."""
        async with self._lock:
            current = self._tokens
            if current is not None and current.access_token != stale.access_token:
                return current  # another request already refreshed
            stored = self.store.load()
            if (
                stored is not None
                and stored.access_token != stale.access_token
                and not stored.expires_within(REFRESH_MARGIN)
            ):
                self._tokens = stored  # a `deere-mcp login` in a terminal saved newer tokens
                return stored
            base = stored if stored is not None and stored.refresh_token else stale
            self._tokens = await refresh_tokens(self._http, self.settings, self.store, base)
            return self._tokens

    # -- requests --------------------------------------------------------------------------

    @staticmethod
    def _check_host(url: str) -> None:
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
        except ValueError as exc:
            raise DeereError(None, f"That is not a valid web address ({exc}).") from None
        if parts.scheme != "https" or not is_allowed_host(hostname):
            raise DeereError(
                None,
                f"Refused to contact {parts.scheme}://{parts.hostname or '?'}: only https "
                "John Deere addresses (*.deere.com) are allowed.",
            )

    def _headers(self, tokens: Tokens, accept: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {tokens.access_token}",
            "Accept": accept or DEERE_ACCEPT,
        }
        if self.settings.uom:
            headers["Accept-UOM-System"] = self.settings.uom
        if self.settings.yield_pref:
            headers["Accept-Yield-Preference"] = self.settings.yield_pref
        return headers

    async def _send(
        self, method: str, url: str, tokens: Tokens, accept: str | None
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, url, headers=self._headers(tokens, accept))
        except httpx.TimeoutException as exc:
            raise DeereError(
                None, f"John Deere did not answer within {self._timeout:g} seconds. Try again."
            ) from exc
        except httpx.InvalidURL as exc:
            raise DeereError(None, f"That is not a valid web address ({exc}).") from exc
        except httpx.HTTPError as exc:
            raise DeereError(
                None, f"Could not reach John Deere ({type(exc).__name__}: {exc})."
            ) from exc
        logger.debug("%s %s -> %s", method, urlsplit(url).path, response.status_code)
        return response

    async def _send_authorized(self, method: str, url: str, accept: str | None) -> httpx.Response:
        tokens = await self._current_tokens()
        response = await self._send(method, url, tokens, accept)
        if response.status_code == 401:
            logger.info("John Deere answered 401; renewing the sign-in and retrying once.")
            tokens = await self._refresh(tokens)
            response = await self._send(method, url, tokens, accept)
        return response

    async def request(
        self,
        method: str,
        url: str,
        *,
        accept: str | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Send one request to John Deere and return the response (status < 400).

        Checks the host allowlist (also on every redirect hop), adds the Authorization,
        Accept and unit headers, refreshes proactively near expiry and once on a 401, and
        raises DeereError for any status >= 400.
        """
        self._check_host(url)
        response = await self._send_authorized(method, url, accept)
        hops = 0
        while follow_redirects and response.is_redirect and hops < MAX_REDIRECTS:
            location = response.headers.get("location")
            if not location:
                break
            url = urljoin(str(response.url), location)
            self._check_host(url)
            hops += 1
            response = await self._send_authorized(
                "GET" if response.status_code == 303 else method, url, accept
            )
        if response.status_code >= 400:
            raise error_from_response(response)
        return response

    async def get_json(self, url: str, *, accept: str | None = None) -> Any:
        response = await self.request("GET", url, accept=accept, follow_redirects=True)
        try:
            return response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "unknown")
            raise DeereError(
                response.status_code,
                f"Deere returned a response that was not JSON (content type {content_type}, "
                f"{len(response.content)} bytes).",
            ) from None

    async def get_bytes(self, url: str, *, accept: str) -> tuple[bytes, str]:
        response = await self.request("GET", url, accept=accept, follow_redirects=True)
        return response.content, response.headers.get("content-type", "")

    async def get_all(self, url: str) -> PageResult:
        """Collect ``values`` from every page, following ``nextPage`` links up to the page cap."""
        items: list[dict[str, Any]] = []
        pages = 0
        next_url: str | None = url
        truncated = False
        while next_url is not None:
            if pages >= self.settings.page_cap:
                truncated = True
                break
            data = await self.get_json(next_url)
            pages += 1
            if isinstance(data, list):
                values: Any = data
                next_url = None
            elif isinstance(data, dict):
                values = data.get("values", [])
                next_url = _next_page(data)
            else:
                raise DeereError(None, "Deere returned a list response in an unexpected format.")
            if not isinstance(values, list):
                raise DeereError(None, "Deere returned a list response in an unexpected format.")
            items.extend(v for v in values if isinstance(v, dict))
        return PageResult(items=items, truncated=truncated, pages=pages)

    async def aclose(self) -> None:
        await self._http.aclose()
