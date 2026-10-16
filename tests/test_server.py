"""The assembled server: lifespan, tool listing, error conversion, and the shared tool helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.client import PageResult
from deere_mcp.config import Settings
from deere_mcp.server import SERVER_INSTRUCTIONS, AppContext, create_server
from deere_mcp.tools import MODULES
from deere_mcp.tools._common import (
    READ_ONLY,
    app,
    drop_links,
    link,
    page_dict,
    resolve_output_path,
    tool_errors,
)
from tests.helpers import RouteHandler, deere_list, json_response

FIELDS = "/platform/organizations/1%2F2/fields"


@pytest.mark.anyio
async def test_server_connects_and_lists_tools(settings: Settings) -> None:
    server = create_server(settings, transport=httpx.MockTransport(RouteHandler()))
    async with Client(server, raise_exceptions=True) as client:
        listed = await client.list_tools()
    names = [tool.name for tool in listed.tools]
    assert len(names) == len(set(names))
    for tool in listed.tools:
        assert tool.name.startswith("jd_")
        assert tool.description
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert "ctx" not in (tool.input_schema.get("properties") or {})


@pytest.mark.anyio
async def test_unknown_tool_is_an_error(mcp_client: Any) -> None:
    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool("jd_no_such_tool", {})
    assert result.is_error
    assert "jd_no_such_tool" in result.content[0].text


def test_eleven_tool_modules_are_registered() -> None:
    assert len(MODULES) == 11
    assert all(callable(module.register) for module in MODULES)
    assert "jd_list_organizations" in SERVER_INSTRUCTIONS


def _add_probe_tools(server: Any) -> None:
    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_probe_fields(
        org_id: Annotated[str, Field(description="Organization ID.")],
        archived: bool | None = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Probe tool used by the tests."""
        client = app(ctx).client
        url = client.url("organizations", org_id, "fields", query={"archived": archived})
        result = await client.get_all(url)
        items = [{**drop_links(f), "self": link(f, "self")} for f in result.items]
        return page_dict(PageResult(items, result.truncated, result.pages), "fields")

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_probe_settings(*, ctx: Context[AppContext]) -> dict[str, Any]:
        """Probe tool used by the tests."""
        return {"base_url": app(ctx).settings.base_url}


@pytest.mark.anyio
async def test_lifespan_client_reaches_tools(make_server: Any) -> None:
    routes = RouteHandler().add(
        "GET",
        FIELDS,
        json_response(
            deere_list(
                [{"id": "f1", "name": "North 80", "links": [{"rel": "self", "uri": "https://x"}]}]
            )
        ),
    )
    server = make_server(routes)
    _add_probe_tools(server)
    async with Client(server, raise_exceptions=True) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        schema = listed["jd_probe_fields"].input_schema
        assert set(schema["properties"]) == {"org_id", "archived"}
        assert schema["required"] == ["org_id"]
        result = await client.call_tool("jd_probe_fields", {"org_id": "1/2", "archived": False})
        settings_result = await client.call_tool("jd_probe_settings", {})
    assert not result.is_error
    assert result.structured_content == {
        "fields": [{"id": "f1", "name": "North 80", "self": "https://x"}],
        "count": 1,
        "truncated": False,
    }
    request = routes.requests[0]
    assert b"/organizations/1%2F2/fields" in request.url.raw_path
    assert request.url.params["archived"] == "false"
    assert request.headers["authorization"] == "Bearer access-1"
    assert settings_result.structured_content == {
        "base_url": "https://sandboxapi.deere.com/platform"
    }


@pytest.mark.anyio
async def test_deere_error_becomes_readable_tool_error(make_server: Any) -> None:
    routes = RouteHandler().add(
        "GET", FIELDS, json_response({"errors": [{"message": "Not connected"}]}, status=403)
    )
    server = make_server(routes)
    _add_probe_tools(server)
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("jd_probe_fields", {"org_id": "1/2"})
    assert result.is_error
    text = result.content[0].text
    assert "403" in text and "Not connected" in text


@pytest.mark.anyio
async def test_not_signed_in_becomes_tool_error(settings: Settings) -> None:
    server = create_server(settings, transport=httpx.MockTransport(RouteHandler()))
    _add_probe_tools(server)
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("jd_probe_fields", {"org_id": "1"})
    assert result.is_error
    assert "deere-mcp login" in result.content[0].text


def test_page_dict_adds_note_only_when_truncated() -> None:
    full = page_dict(PageResult([{"id": 1}], truncated=False, pages=1), "fields")
    assert full == {"fields": [{"id": 1}], "count": 1, "truncated": False}
    cut = page_dict(PageResult([{"id": 1}, {"id": 2}], truncated=True, pages=3))
    assert cut["items"] == [{"id": 1}, {"id": 2}]
    assert cut["count"] == 2 and cut["truncated"] is True
    assert cut["note"] == (
        "Result cut short after 3 pages; narrow the query or raise DEERE_PAGE_CAP."
    )


def test_drop_links_and_link() -> None:
    record = {
        "id": "1",
        "links": [{"rel": "self", "uri": "u1"}, {"rel": "boundaries", "uri": "u2"}],
    }
    assert drop_links(record) == {"id": "1"}
    assert record["links"]  # the original is untouched
    assert link(record, "boundaries") == "u2"
    assert link(record, "missing") is None
    assert link({"id": "2"}, "self") is None


def test_resolve_output_path_stays_inside(settings: Settings, tmp_path: Path) -> None:
    path = resolve_output_path(settings, "fields.csv")
    out = settings.output_dir.resolve()
    assert path == out / "fields.csv"
    nested = resolve_output_path(settings, "2024/ops.json")
    assert nested == out / "2024" / "ops.json" and nested.parent.is_dir()
    for bad in ("../escape.txt", str(tmp_path / "elsewhere.txt"), "", ".", "a/../../x"):
        with pytest.raises(ToolError):
            resolve_output_path(settings, bad)
