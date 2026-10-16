"""Tools that save data to files in the configured output folder.

Everything written here goes through `resolve_output_path`, so a file can never land outside the
folder set by `DEERE_OUTPUT_DIR` (by default a `deere-mcp` folder in the user's Documents).
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.client import DeereError
from deere_mcp.config import Settings
from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, resolve_output_path, tool_errors

logger = logging.getLogger(__name__)

# Seconds to wait before each new attempt while Deere is still preparing a shapefile. Each delay
# is used at most once, and polling also stops once `max_wait_seconds` would be exceeded. Tests
# patch this to zeros.
POLL_DELAYS: tuple[float, ...] = (5, 10, 20, 40)

# What to tell the model to do when the file is still being prepared.
RETRY_AFTER_SECONDS = 60

# Shapefiles can be tens of megabytes: generous total time, short time to connect.
DOWNLOAD_TIMEOUT = httpx.Timeout(600.0, connect=30.0)
CHUNK_SIZE = 64 * 1024

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def new_download_client() -> httpx.AsyncClient:
    """Return a plain HTTP client for pre-signed download links.

    It deliberately shares nothing with `DeereClient`: no Authorization header, no Deere
    cookies. The pre-signed link carries its own permission and may be on a non-Deere host
    (for example cloud storage), so Deere credentials must never be sent to it.
    """
    return httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)


def _safe_name(value: str) -> str:
    """Make an ID safe to use inside a default file name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value) or "file"


def _target_path(settings: Settings, filename: str) -> Path:
    """Resolve `filename` inside the output folder (the helper creates folders and checks it)."""
    path = resolve_output_path(settings, filename)
    if path.is_dir():
        raise ToolError("The filename must name a file, not a folder.")
    return path


def _write_bytes(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise ToolError(f"Could not write {path.name}: {exc.strerror or exc}") from exc


def _presigned_location(response: httpx.Response) -> str:
    """Return the pre-signed download address from a redirect response, or raise ToolError."""
    location = response.headers.get("location", "")
    parts = urlsplit(location)
    if parts.scheme != "https" or not parts.netloc:
        raise ToolError("Deere did not give a usable download link for this shapefile.")
    return location


async def _download(location: str, path: Path) -> int:
    """Stream the pre-signed `location` to `path` and return the number of bytes written.

    The data goes to a `.part` file beside the target first, so an interrupted download never
    leaves a half-written file under the real name. The link carries a signature, so it is never
    logged or put into an error message.
    """
    partial = path.with_name(path.name + ".part")
    written = 0
    try:
        async with new_download_client() as http, http.stream("GET", location) as response:
            if not response.is_success:
                raise ToolError(
                    f"The download link returned status {response.status_code}. "
                    "It may have expired; call this tool again to get a fresh link."
                )
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    handle.write(chunk)
                    written += len(chunk)
        os.replace(partial, path)
    except httpx.HTTPError as exc:
        logger.warning("Shapefile download failed: %s", type(exc).__name__)
        raise ToolError(
            f"The download from the link Deere provided failed ({type(exc).__name__}). Try again."
        ) from exc
    except OSError as exc:
        raise ToolError(f"Could not write {path.name}: {exc.strerror or exc}") from exc
    finally:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
    return written


def register(mcp: MCPServer) -> None:
    """Register the file tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_download_operation_shapefile(
        operation_id: Annotated[
            str,
            Field(description="Field operation ID, from jd_list_field_operations."),
        ],
        shape_type: Annotated[
            Literal["Point", "Polygon"] | None,
            Field(description="Geometry to export. Omit to let Deere choose its default."),
        ] = None,
        resolution: Annotated[
            Literal["EachSection", "EachSensor", "OneHertz"] | None,
            Field(
                description=(
                    "How densely to sample the operation: per machine section, per sensor, or "
                    "once per second. Omit to let Deere choose its default."
                )
            ),
        ] = None,
        filename: Annotated[
            str | None,
            Field(
                description=(
                    "File name to save as, inside the output folder. "
                    "Defaults to operation-<operation_id>.zip."
                )
            ),
        ] = None,
        max_wait_seconds: Annotated[
            int,
            Field(
                ge=0,
                le=600,
                description="How long to wait for Deere to prepare the file before giving up.",
            ),
        ] = 120,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Download the point-by-point shapefile for one field operation and save it to disk.

        The file is a zip of ESRI shapefiles (GPS points or polygons with yield, rate, moisture
        and similar values at each location), meant for GIS software or Python. This tool saves
        the zip in the output folder and returns its path and size; it does not return the
        contents, because the files are large. Operation IDs come from
        jd_list_field_operations (only operations with shapefile_available true have one).

        Deere prepares the file in the background, so this tool may wait a little. If Deere is
        still preparing it when max_wait_seconds runs out, the result has status "preparing";
        call the tool again after retry_after_seconds. On success the status is "saved".
        """
        client = app(ctx).client
        settings = app(ctx).settings

        # Resolve the destination first so a bad filename fails before any request is made.
        path = _target_path(settings, filename or f"operation-{_safe_name(operation_id)}.zip")
        url = client.url(
            "fieldOps",
            operation_id,
            query={"shapeType": shape_type, "resolution": resolution},
        )

        waited = 0.0
        delays = iter(POLL_DELAYS)
        while True:
            try:
                response = await client.request("GET", url, follow_redirects=False)
            except DeereError as exc:
                if exc.status == 406:
                    raise ToolError(
                        "Deere cannot generate a shapefile for this operation."
                    ) from exc
                raise

            if response.status_code == 202:
                delay = next(delays, None)
                if delay is None or waited + delay > max_wait_seconds:
                    return {
                        "status": "preparing",
                        "retry_after_seconds": RETRY_AFTER_SECONDS,
                        "message": (
                            "Deere is still preparing this shapefile. Nothing was saved yet. "
                            f"Call this tool again in about {RETRY_AFTER_SECONDS} seconds."
                        ),
                    }
                await asyncio.sleep(delay)
                waited += delay
                continue

            if response.status_code in _REDIRECT_STATUSES:
                size = await _download(_presigned_location(response), path)
                return {"status": "saved", "path": str(path), "bytes": size}

            raise ToolError(
                f"Deere returned an unexpected response (status {response.status_code}) "
                "while preparing the shapefile."
            )

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_save_file(
        filename: Annotated[
            str,
            Field(
                description=(
                    "Name of the file to create, for example 'north-80-yield.csv'. A relative "
                    "path such as 'reports/2024.csv' is allowed."
                )
            ),
        ],
        content: Annotated[str, Field(description="The full text to write to the file.")],
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Save text, such as a CSV or a summary the assistant has assembled, to a file.

        Use this when the user wants a result kept on their computer. Files can only be created
        inside the configured output folder (DEERE_OUTPUT_DIR); any filename that points outside
        it is refused. An existing file with the same name is replaced. Returns the full path
        and the size in bytes.
        """
        path = _target_path(app(ctx).settings, filename)
        data = content.encode("utf-8")
        _write_bytes(path, data)
        return {"path": str(path), "bytes": len(data)}

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_export_field_operations(
        org_id: Annotated[str, Field(description="Organization ID, from jd_list_organizations.")],
        field_id: Annotated[str, Field(description="Field ID, from jd_list_fields.")],
        seasons: Annotated[
            list[Annotated[int, Field(ge=1900, le=2100)]],
            Field(
                min_length=1,
                max_length=20,
                description="Crop seasons (years) to export, for example [2022, 2023, 2024].",
            ),
        ],
        filename: Annotated[
            str | None,
            Field(
                description=(
                    "File name to save as, inside the output folder. "
                    "Defaults to field-<field_id>-operations.json."
                )
            ),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Save every field operation for one field across several seasons to a JSON file.

        Use this for multi-season history (for example five years of planting, applications
        and harvests) instead of reading each season into the conversation. Each operation
        includes its dates, crop, products, varieties and measurement totals such as area,
        rate and yield. The JSON file holds org_id, field_id and a "seasons" object mapping
        each year to its list of operations. This tool returns the file path and the number of
        operations per season, not the operations themselves; use jd_list_field_operations to
        read a single season directly. Organization and field IDs come from
        jd_list_organizations and jd_list_fields. If a season is listed in truncated_seasons,
        it had more results than the page cap allows and the file holds only the first part.
        """
        client = app(ctx).client
        settings = app(ctx).settings
        name = filename or f"field-{_safe_name(field_id)}-operations.json"
        path = _target_path(settings, name)

        per_season: dict[str, list[dict[str, Any]]] = {}
        truncated: list[str] = []
        for season in dict.fromkeys(seasons):
            url = client.url(
                "organizations",
                org_id,
                "fields",
                field_id,
                "fieldOperations",
                query={"cropSeason": season, "embed": "measurementTypes"},
            )
            result = await client.get_all(url)
            per_season[str(season)] = [drop_links(op) for op in result.items]
            if result.truncated:
                truncated.append(str(season))

        document = {
            "org_id": org_id,
            "field_id": field_id,
            "seasons": per_season,
            "truncated_seasons": truncated,
        }
        _write_bytes(path, json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"))

        response: dict[str, Any] = {
            "path": str(path),
            "seasons": {season: len(ops) for season, ops in per_season.items()},
            "truncated_seasons": truncated,
        }
        if truncated:
            response["note"] = (
                f"Seasons {', '.join(truncated)} were cut short by the page cap; "
                "narrow the request or raise DEERE_PAGE_CAP."
            )
        return response
