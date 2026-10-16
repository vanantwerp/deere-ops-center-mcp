"""Tests for the equipment, machine health and passthrough tools."""

import base64
import json
from typing import Any

import httpx
import pytest

from deere_mcp.tools.passthrough import MAX_TEXT_CHARS, resolve_target
from tests.helpers import RouteHandler, deere_list, json_response

pytestmark = pytest.mark.anyio

SELF = "https://equipmentapi.deere.com/isg/equipment/e1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

TOOL_NAMES = {
    "jd_list_equipment",
    "jd_get_equipment",
    "jd_get_machine_alerts",
    "jd_get_machine_engine_hours",
    "jd_get_machine_hours_of_operation",
    "jd_get_machine_location_history",
    "jd_get_machine_device_state_reports",
    "jd_get",
}


def body(result: Any) -> dict[str, Any]:
    """The JSON a tool returned: structured content when present, else the text block."""
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def test_tools_are_listed_read_only_and_hide_ctx(mcp_client) -> None:
    async with mcp_client(RouteHandler()) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert TOOL_NAMES <= set(listed)
    for name in TOOL_NAMES:
        tool = listed[name]
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert "ctx" not in tool.input_schema["properties"]


# --- equipment -------------------------------------------------------------------------------


async def test_list_equipment_builds_query_and_shapes_items(mcp_client, saved_tokens) -> None:
    items = [
        {
            "id": "e1",
            "principalId": "p1",
            "name": "8R 370",
            "category": "Machine",
            "type": {"name": "Tractor"},
            "archived": False,
            "serialNumber": "dropped",
            "links": [{"rel": "self", "uri": SELF}],
        },
        {
            "id": "e2",
            "principalId": "p2",
            "name": "Planter",
            "equipmentCategory": "Implement",
            "type": "Planter",
            "archived": True,
            "links": [],
        },
    ]
    handler = RouteHandler().add("GET", "/isg/equipment", json_response(deere_list(items)))
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_equipment",
            {
                "org_id": "42",
                "category": "Machine",
                "archived": False,
                "ids": ["e1", "e2"],
                "serial_numbers": ["SN1", "SN2"],
                "principal_ids": ["p1", "p2"],
                "capable_of": "Connectivity",
                "organization_role_type": "Controlling",
                "item_limit": 100,
            },
        )
    assert not result.is_error
    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request.url.host == "equipmentapi.deere.com"
    assert request.headers["authorization"] == f"Bearer {saved_tokens.access_token}"
    assert dict(request.url.params) == {
        "organizationIds": "42",
        "ids": "e1,e2",
        "serialNumbers": "SN1,SN2",
        "principalIds": "p1,p2",
        "categories": "Machine",
        "capableOf": "Connectivity",
        "organizationRole.type": "Controlling",
        "archived": "false",
        "itemLimit": "100",
    }
    assert result.structured_content == {
        "equipment": [
            {
                "id": "e1",
                "principalId": "p1",
                "name": "8R 370",
                "category": "Machine",
                "type": "Tractor",
                "archived": False,
                "uri": SELF,
            },
            {
                "id": "e2",
                "principalId": "p2",
                "name": "Planter",
                "category": "Implement",
                "type": "Planter",
                "archived": True,
                "uri": None,
            },
        ],
        "count": 2,
        "truncated": False,
    }


async def test_list_equipment_sends_only_the_org_by_default(mcp_client) -> None:
    handler = RouteHandler().add("GET", "/isg/equipment", json_response(deere_list([])))
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_equipment", {"org_id": "42"})
    assert result.structured_content["equipment"] == []
    assert dict(handler.requests[0].url.params) == {"organizationIds": "42"}


async def test_list_equipment_keeps_embedded_data_under_raw(mcp_client) -> None:
    item = {
        "id": "e1",
        "principalId": "p1",
        "name": "8R 370",
        "devices": [{"id": "d1"}],
        "links": [{"rel": "self", "uri": SELF}],
    }
    handler = RouteHandler().add("GET", "/isg/equipment", json_response(deere_list([item])))
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_equipment", {"org_id": "42", "embed": "devices"})
    assert handler.requests[0].url.params["embed"] == "devices"
    first = result.structured_content["equipment"][0]
    assert first["id"] == "e1"
    assert first["raw"] == {
        "id": "e1",
        "principalId": "p1",
        "name": "8R 370",
        "devices": [{"id": "d1"}],
    }


@pytest.mark.parametrize("item_limit", [0, 5001])
async def test_list_equipment_rejects_item_limit_out_of_range(mcp_client, item_limit: int) -> None:
    handler = RouteHandler()
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_equipment", {"org_id": "42", "item_limit": item_limit}
        )
    assert result.is_error
    assert "item_limit" in result.content[0].text
    assert handler.requests == []


async def test_get_equipment_quotes_the_id_and_drops_links(mcp_client) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        record = {"id": "e/1", "name": "8R 370", "links": [{"rel": "self", "uri": SELF}]}
        return json_response(record)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_get_equipment", {"equipment_id": "e/1", "embed": "capabilities"}
        )
    assert result.structured_content == {"id": "e/1", "name": "8R 370"}
    assert seen[0].url.raw_path.startswith(b"/isg/equipment/e%2F1?")
    assert seen[0].url.params["embed"] == "capabilities"


# --- machine health --------------------------------------------------------------------------

START = "2026-10-01T00:00:00Z"
END = "2026-10-07T00:00:00Z"

HEALTH_CASES = [
    pytest.param(
        "jd_get_machine_alerts",
        "alerts",
        {"start_date": START, "end_date": END, "exclude_acknowledged": True},
        {"startDate": START, "endDate": END, "excludeAcknowledged": "true"},
        "alerts",
        id="alerts",
    ),
    pytest.param(
        "jd_get_machine_engine_hours",
        "engineHours",
        {"last_known": True},
        {"lastKnown": "true"},
        "engine_hours",
        id="engine-hours",
    ),
    pytest.param(
        "jd_get_machine_hours_of_operation",
        "hoursOfOperation",
        {
            "org_id": "77",
            "start_date": START,
            "end_date": END,
            "detailed_state": "WORKING",
            "summarize_duration": "DAILY",
        },
        {
            "organizationId": "77",
            "startDate": START,
            "endDate": END,
            "detailedState": "WORKING",
            "summarizeDuration": "DAILY",
        },
        "hours_of_operation",
        id="hours-of-operation",
    ),
    pytest.param(
        "jd_get_machine_location_history",
        "locationHistory",
        {"start_date": START, "end_date": END, "last_known": False},
        {"startDate": START, "endDate": END, "lastKnown": "false"},
        "locations",
        id="location-history",
    ),
    pytest.param(
        "jd_get_machine_device_state_reports",
        "deviceStateReports",
        {"start_date": START, "end_date": END, "last_known": True},
        {"startDate1": START, "endDate1": END, "lastKnown": "true"},
        "device_state_reports",
        id="device-state-reports",
    ),
]


@pytest.mark.parametrize(("tool", "resource", "args", "expected_params", "key"), HEALTH_CASES)
async def test_machine_health_tools_query_names_and_output_key(
    mcp_client,
    tool: str,
    resource: str,
    args: dict[str, Any],
    expected_params: dict[str, str],
    key: str,
) -> None:
    records = [{"id": "r1", "value": 5, "links": [{"rel": "self", "uri": "https://x.deere.com/r"}]}]
    handler = RouteHandler().add(
        "GET", f"/platform/machines/p1/{resource}", json_response(deere_list(records))
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(tool, {"principal_id": "p1", **args})
    assert not result.is_error
    assert len(handler.requests) == 1
    assert dict(handler.requests[0].url.params) == expected_params
    assert result.structured_content == {
        key: [{"id": "r1", "value": 5}],
        "count": 1,
        "truncated": False,
    }


async def test_device_state_reports_use_the_numbered_date_parameters(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET", "/platform/machines/p1/deviceStateReports", json_response(deere_list([]))
    )
    async with mcp_client(handler) as client:
        await client.call_tool(
            "jd_get_machine_device_state_reports",
            {"principal_id": "p1", "start_date": START, "end_date": END},
        )
    params = handler.requests[0].url.params
    assert params["startDate1"] == START
    assert params["endDate1"] == END
    assert "startDate" not in params
    assert "endDate" not in params


async def test_machine_health_quotes_the_principal_id(mcp_client) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(deere_list([]))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_machine_alerts", {"principal_id": "p/1"})
    assert not result.is_error
    assert seen[0].url.raw_path.startswith(b"/platform/machines/p%2F1/alerts")


async def test_machine_health_reports_when_the_page_cap_cuts_the_walk_short(mcp_client) -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        next_uri = f"https://sandboxapi.deere.com/platform/machines/p1/alerts?page={page + 1}"
        return json_response(deere_list([{"id": f"a{page}"}], next_uri=next_uri))

    handler = RouteHandler().add("GET", "/platform/machines/p1/alerts", pages)
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_machine_alerts", {"principal_id": "p1"})
    data = body(result)
    assert data["truncated"] is True
    assert data["count"] == len(data["alerts"]) == 3  # the test settings cap paging at 3 pages
    assert "note" in data


async def test_machine_health_deere_error_becomes_a_tool_error(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET",
        "/platform/machines/p1/locationHistory",
        json_response({"message": "Date range too large"}, status=400),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_machine_location_history", {"principal_id": "p1"})
    assert result.is_error
    assert result.content[0].text.strip()


# --- jd_get passthrough ----------------------------------------------------------------------


async def test_jd_get_refuses_hosts_outside_deere(mcp_client) -> None:
    handler = RouteHandler()
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "https://evil.example/x"})
    assert result.is_error
    assert handler.requests == []


async def test_jd_get_refuses_a_lookalike_host(mcp_client) -> None:
    handler = RouteHandler()
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "https://deere.com.evil.example/x"})
    assert result.is_error
    assert handler.requests == []


async def test_jd_get_returns_an_image_block_for_image_png(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET",
        "/platform/organizations/1/map",
        httpx.Response(200, content=PNG, headers={"content-type": "image/png"}),
    )
    accept = "application/vnd.deere.axiom.v3.image+json"
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/organizations/1/map", "accept": accept})
    assert not result.is_error
    assert len(result.content) == 1
    block = result.content[0]
    assert block.type == "image"
    assert block.mime_type == "image/png"
    assert base64.b64decode(block.data) == PNG
    assert handler.requests[0].headers["accept"] == accept


async def test_jd_get_wraps_a_json_list(mcp_client) -> None:
    handler = RouteHandler().add("GET", "/platform/things", json_response([{"id": 1}, {"id": 2}]))
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/things"})
    assert body(result) == {"data": [{"id": 1}, {"id": 2}]}


async def test_jd_get_returns_a_json_object_as_is(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET", "/platform/organizations/1", json_response({"id": "1", "name": "Farm"})
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_get", {"url": "https://sandboxapi.deere.com/platform/organizations/1"}
        )
    assert body(result) == {"id": "1", "name": "Farm"}


async def test_jd_get_joins_a_relative_path_to_the_base_url(mcp_client) -> None:
    handler = RouteHandler().add("GET", "/platform/organizations", json_response({"values": []}))
    async with mcp_client(handler) as client:
        for path in ("/organizations", "organizations", "/platform/organizations"):
            result = await client.call_tool("jd_get", {"url": path})
            assert not result.is_error, path
    assert [str(r.url) for r in handler.requests] == [
        "https://sandboxapi.deere.com/platform/organizations"
    ] * 3


async def test_jd_get_cuts_long_text_at_the_limit(mcp_client) -> None:
    text = "a" * (MAX_TEXT_CHARS + 500)
    handler = RouteHandler().add(
        "GET",
        "/platform/export.csv",
        httpx.Response(200, text=text, headers={"content-type": "text/csv; charset=utf-8"}),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/export.csv"})
    data = body(result)
    assert data["content_type"].startswith("text/csv")
    assert data["text"] == "a" * MAX_TEXT_CHARS
    assert data["truncated"] is True


async def test_jd_get_short_text_is_not_marked_truncated(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET",
        "/platform/note",
        httpx.Response(200, text="hello", headers={"content-type": "text/plain"}),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/note"})
    assert body(result) == {"content_type": "text/plain", "text": "hello", "truncated": False}


async def test_jd_get_does_not_dump_binary_content_as_text(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET",
        "/platform/archive",
        httpx.Response(
            200, content=b"PK\x03\x04" + b"\x00" * 64, headers={"content-type": "application/zip"}
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/archive"})
    data = body(result)
    assert data["text"] == ""
    assert "Binary" in data["note"]


async def test_jd_get_reports_a_redirect_without_following_it(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET",
        "/platform/fieldOps/9",
        httpx.Response(307, headers={"location": "https://files.example/signed"}),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/fieldOps/9"})
    data = body(result)
    assert data["status"] == 307
    assert data["location"] == "https://files.example/signed"
    assert len(handler.requests) == 1


async def test_jd_get_deere_error_becomes_a_tool_error(mcp_client) -> None:
    handler = RouteHandler().add(
        "GET", "/platform/organizations/9", json_response({"message": "Nope"}, status=403)
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "/organizations/9"})
    assert result.is_error
    assert result.content[0].text.strip()


async def test_jd_get_rejects_an_empty_url(mcp_client) -> None:
    handler = RouteHandler()
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get", {"url": "   "})
    assert result.is_error
    assert handler.requests == []


# --- resolve_target (pure) -------------------------------------------------------------------

BASE = "https://sandboxapi.deere.com/platform"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/organizations", f"{BASE}/organizations"),
        ("organizations/1?x=2", f"{BASE}/organizations/1?x=2"),
        ("/platform/organizations", f"{BASE}/organizations"),
        ("//evil.example/x", f"{BASE}/evil.example/x"),
        (
            "https://equipmentapi.deere.com/isg/equipment",
            "https://equipmentapi.deere.com/isg/equipment",
        ),
        ("https://evil.example/x", "https://evil.example/x"),
    ],
)
def test_resolve_target(given: str, expected: str) -> None:
    assert resolve_target(BASE, given) == expected
