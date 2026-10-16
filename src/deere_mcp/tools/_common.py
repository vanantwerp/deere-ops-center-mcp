"""Helpers shared by every tool module."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from deere_mcp.auth import AuthError
from deere_mcp.client import DeereError, PageResult
from deere_mcp.config import Settings

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from deere_mcp.server import AppContext

P = ParamSpec("P")
R = TypeVar("R")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# For the tools that create or replace a file in the output folder. AI apps read these hints
# when deciding whether to ask before running a tool, so a tool that writes must say so.
WRITES_FILES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)


def app(ctx: Context[AppContext]) -> AppContext:
    """The settings and Deere client the server's lifespan created."""
    return ctx.request_context.lifespan_context


def drop_links(record: Mapping[str, Any]) -> dict[str, Any]:
    """A shallow copy of a Deere record without its ``links`` list."""
    return {key: value for key, value in record.items() if key != "links"}


def link(record: Mapping[str, Any], rel: str) -> str | None:
    """The ``uri`` of the record's link with this ``rel``, or None."""
    links = record.get("links")
    if not isinstance(links, list):
        return None
    for item in links:
        if isinstance(item, Mapping) and item.get("rel") == rel:
            uri = item.get("uri")
            return uri if isinstance(uri, str) else None
    return None


def page_dict(result: PageResult, key: str = "items") -> dict[str, Any]:
    """Wrap a paged result for a tool: the items, a count, and whether the page cap cut it short."""
    out: dict[str, Any] = {
        key: result.items,
        "count": len(result.items),
        "truncated": result.truncated,
    }
    if result.truncated:
        out["note"] = (
            f"Result cut short after {result.pages} pages; "
            "narrow the query or raise DEERE_PAGE_CAP."
        )
    return out


def _text(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def tool_errors(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Turn Deere, sign-in and network failures into ToolError so the model reads the message."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except (DeereError, AuthError) as exc:
            raise ToolError(_text(exc)) from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Network error talking to John Deere: {_text(exc)}") from exc

    return wrapper


def resolve_output_path(settings: Settings, filename: str) -> Path:
    """The path for `filename` inside the output folder; refuses anything that lands outside."""
    output_dir = settings.output_dir.expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        base = output_dir.resolve()
    except OSError as exc:
        raise ToolError(
            f"Could not use the output folder {output_dir} ({exc.strerror or exc}). "
            "Check DEERE_OUTPUT_DIR."
        ) from exc
    not_a_file = "The filename must name a file, not a folder, for example 'fields.csv'."
    if not filename or not filename.strip():
        raise ToolError(not_a_file)
    try:
        candidate = (base / filename).resolve()
    except (OSError, ValueError, RuntimeError):
        raise ToolError("That filename is not valid.") from None
    if candidate == base:
        raise ToolError(not_a_file)
    if not candidate.is_relative_to(base):
        raise ToolError(
            f"Files can only be saved inside the output folder {base}. "
            "Use a plain file name such as 'fields.csv'."
        )
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(
            f"Could not create the folder for that file ({exc.strerror or exc})."
        ) from exc
    return candidate
