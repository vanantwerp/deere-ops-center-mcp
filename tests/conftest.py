"""Shared fixtures: anyio_backend, settings, saved_tokens, make_server, mcp_client."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.server import MCPServer

from deere_mcp.auth import Tokens, TokenStore, reset_discovery_cache
from deere_mcp.config import Settings
from deere_mcp.server import create_server

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _fresh_discovery() -> Iterator[None]:
    """Each test starts without a cached OAuth discovery document."""
    reset_discovery_cache()
    yield
    reset_discovery_cache()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        client_id="id",
        client_secret="secret",
        base_url="https://sandboxapi.deere.com/platform",
        uom=None,
        yield_pref=None,
        callback_port=9090,
        page_cap=3,
        output_dir=tmp_path / "out",
        token_path=tmp_path / "tokens.json",
        config_path=tmp_path / "config.toml",
        log_level="WARNING",
    )


@pytest.fixture
def saved_tokens(settings: Settings) -> Tokens:
    """A valid, unexpired sign-in saved at settings.token_path."""
    tokens = Tokens(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=time.time() + 3600,
        scope="ag2 eq1 org1 work1 offline_access",
    )
    TokenStore(settings.token_path).save(tokens)
    return tokens


@pytest.fixture
def make_server(settings: Settings, saved_tokens: Tokens) -> Callable[[Handler], MCPServer]:
    def _make(handler: Handler) -> MCPServer:
        return create_server(settings, transport=httpx.MockTransport(handler))

    return _make


@pytest.fixture
def mcp_client(make_server: Callable[[Handler], MCPServer]) -> Any:
    @asynccontextmanager
    async def _connect(handler: Handler) -> AsyncIterator[Client]:
        async with Client(make_server(handler), raise_exceptions=True) as client:
            yield client

    return _connect
