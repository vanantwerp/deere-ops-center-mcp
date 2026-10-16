"""Read-only passthrough for Deere endpoints that have no dedicated tool."""

from typing import Annotated, Any
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Image
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, tool_errors

MAX_TEXT_CHARS = 20_000

# Content types whose bytes are useless as text; the model is told instead of getting mojibake.
BINARY_PREFIXES = ("video/", "audio/", "font/")
BINARY_TYPES = frozenset(
    {
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "application/pdf",
    }
)


def resolve_target(base_url: str, url: str) -> str:
    """Return an absolute URL: absolute input is kept, a path is joined to the base URL."""
    url = url.strip()
    if not url:
        raise ToolError("url is required: an absolute Deere URL or a path such as /organizations.")
    try:
        has_scheme = bool(urlsplit(url).scheme)
    except ValueError as exc:
        raise ToolError(f"That is not a valid web address ({exc}).") from None
    if has_scheme:
        # Scheme and host checks happen in the client, which is the one place that enforces them.
        return url
    base = urlsplit(base_url)
    base_path = base.path.rstrip("/")
    path = "/" + url.lstrip("/")
    if base_path and (path == base_path or path.startswith(base_path + "/")):
        # The caller already included the base path (for example /platform/organizations).
        return f"{base.scheme}://{base.netloc}{path}"
    return base_url.rstrip("/") + path


def register(mcp: MCPServer) -> None:
    """Register the passthrough tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get(
        url: Annotated[
            str,
            Field(
                description="Absolute Deere URL, or a path such as /organizations/123/fields "
                "that is joined to the configured API base URL."
            ),
        ],
        accept: Annotated[
            str | None,
            Field(description="Accept header to send; the Deere v3 JSON type when omitted."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> Image | dict[str, Any]:
        """Make a read-only GET request to any John Deere API endpoint.

        Use this for Deere endpoints that no dedicated tool covers; follow the `links[].uri`
        values from other results to get there, and take IDs from the list tools. Only Deere
        hosts are allowed and only GET is ever sent. JSON comes back as the parsed object (lists
        are wrapped as {"data": [...]}), images come back as an image, and any other text comes
        back as {"content_type", "text", "truncated"} cut to 20,000 characters. Pass
        accept="application/vnd.deere.axiom.v3.image+json" to fetch a measurement map image.
        """
        context = app(ctx)
        target = resolve_target(context.settings.base_url, url)
        response = await context.client.request("GET", target, accept=accept)

        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()

        if 300 <= response.status_code < 400:
            return {
                "status": response.status_code,
                "location": response.headers.get("location"),
                "note": "Deere answered with a redirect; it was not followed.",
            }

        if media_type.startswith("image/"):
            return Image(data=response.content, format=media_type.split("/", 1)[1])

        if media_type == "application/json" or media_type.endswith("+json"):
            try:
                parsed = response.json()
            except ValueError:
                pass  # Declared JSON but not parseable; show it as text below.
            else:
                return parsed if isinstance(parsed, dict) else {"data": parsed}

        if media_type in BINARY_TYPES or media_type.startswith(BINARY_PREFIXES):
            return {
                "content_type": content_type,
                "text": "",
                "truncated": False,
                "note": f"Binary content ({len(response.content)} bytes) is not returned.",
            }

        text = response.text
        return {
            "content_type": content_type,
            "text": text[:MAX_TEXT_CHARS],
            "truncated": len(text) > MAX_TEXT_CHARS,
        }
