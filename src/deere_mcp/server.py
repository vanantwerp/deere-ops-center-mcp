"""Builds the MCP server: settings, the Deere client in the lifespan, and every tool."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, cast

import httpx
from mcp.server import MCPServer

from deere_mcp import __version__
from deere_mcp.auth import TokenStore
from deere_mcp.client import DeereClient
from deere_mcp.config import Settings, load_settings

logger = logging.getLogger(__name__)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@dataclass
class AppContext:
    """What every tool gets through ``ctx.request_context.lifespan_context``."""

    settings: Settings
    client: DeereClient


SERVER_INSTRUCTIONS = (
    "Read-only access to the user's John Deere Operations Center data: organizations, fields "
    "and boundaries, field operations (tillage, seeding, application, harvest) with their "
    "measurement totals, equipment and machine health, work plans, operators, products and "
    "guidance lines. Nothing here can change data in Operations Center. Start with "
    "jd_list_organizations; take every other ID (fields, operations, equipment, machine "
    "principal IDs) from the list tools instead of guessing. List results stop at a page cap "
    "and then say truncated=true with a note; narrow the query (one season, a shorter date "
    "range) when that happens. Deere limits some date ranges per request: machine alerts at "
    "most 7 days, machine location history at most 1 month, and the field-operation "
    "start_date/end_date filter is a 7-day window. Dates are ISO 8601, for example "
    "2024-05-01T00:00:00Z. Use jd_get for Deere links in results that no dedicated tool "
    "covers. Files are only ever written into the user's configured output folder. If a "
    "tool says the sign-in expired or the app is not set up, tell the user to run "
    "`deere-mcp login` or `deere-mcp setup` in a terminal."
)


def create_server(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> MCPServer:
    """Build a server with every tool registered. `transport` lets tests mock Deere."""
    settings = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
        store = TokenStore(settings.token_path)
        client = DeereClient(settings, store, transport=transport)
        try:
            yield AppContext(settings=settings, client=client)
        finally:
            await client.aclose()

    server = MCPServer(
        "Deere Ops Center",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        lifespan=app_lifespan,
        log_level=cast(LogLevel, settings.log_level),
    )
    # httpx logs every request URL at INFO; keep its loggers quiet even at DEBUG.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Imported here, not at module level: every tool module imports AppContext from here.
    from deere_mcp.tools import register_all

    register_all(server)
    return server


def main() -> None:
    """Serve over stdio with settings from the environment and ``config.toml``."""
    create_server(load_settings()).run()
