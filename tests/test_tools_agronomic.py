"""Tests for the organization, field, operation, guidance, product, operator and plan tools."""

from typing import Any

import httpx
import pytest

from tests.helpers import RouteHandler, deere_list, json_response

BASE = "/platform"
CONNECTIONS_URL = "https://connections.deere.com/connections/abc123/select-organizations"


def _link(rel: str, uri: str) -> dict[str, str]:
    return {"rel": rel, "uri": uri}


def _text(result: Any) -> str:
    return result.content[0].text


@pytest.mark.anyio
async def test_all_agronomic_tools_are_listed_and_read_only(mcp_client):
    names = {
        "jd_list_organizations",
        "jd_list_fields",
        "jd_get_field",
        "jd_list_field_operations",
        "jd_get_field_operation_measurements",
        "jd_list_guidance_lines",
        "jd_get_guidance_line",
        "jd_list_products",
        "jd_list_operators",
        "jd_list_work_plans",
        "jd_get_work_plan",
    }
    async with mcp_client(RouteHandler()) as client:
        listing = await client.list_tools()
    tools = {tool.name: tool for tool in listing.tools}
    assert names <= set(tools)
    for name in names:
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True
        assert "ctx" not in tools[name].input_schema.get("properties", {})


@pytest.mark.anyio
async def test_list_organizations_reports_connected_flag(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations",
        json_response(
            deere_list(
                [
                    {
                        "id": "1",
                        "name": "Home Farm",
                        "type": "customer",
                        "member": True,
                        "links": [
                            _link("self", "https://sandboxapi.deere.com/platform/organizations/1")
                        ],
                    },
                    {
                        "id": "2",
                        "name": "Rented Ground",
                        "type": "customer",
                        "member": False,
                        "links": [_link("connections", CONNECTIONS_URL)],
                    },
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_organizations", {})

    assert not result.is_error
    data = result.structured_content
    assert data["count"] == 2
    assert data["truncated"] is False
    home, rented = data["organizations"]
    assert home == {
        "id": "1",
        "name": "Home Farm",
        "type": "customer",
        "member": True,
        "connected": True,
    }
    assert rented["connected"] is False
    assert rented["manage_connections_url"] == CONNECTIONS_URL
    assert "links" not in rented


@pytest.mark.anyio
async def test_list_fields_returns_id_name_archived(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/fields",
        json_response(
            deere_list(
                [
                    {
                        "id": "f1",
                        "name": "North 80",
                        "archived": False,
                        "links": [_link("boundaries", "https://sandboxapi.deere.com/x")],
                    },
                    {"id": "f2", "name": "Old Pasture", "archived": True, "links": []},
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_fields", {"org_id": "1"})

    data = result.structured_content
    assert data["fields"] == [
        {"id": "f1", "name": "North 80", "archived": False},
        {"id": "f2", "name": "Old Pasture", "archived": True},
    ]
    assert data["count"] == 2


@pytest.mark.anyio
async def test_path_ids_are_percent_encoded(mcp_client):
    raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_paths.append(request.url.raw_path.split(b"?")[0])
        return json_response(deere_list([]))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_fields", {"org_id": "1/2"})

    assert not result.is_error
    assert raw_paths == [b"/platform/organizations/1%2F2/fields"]


@pytest.mark.anyio
async def test_list_stops_at_page_cap_and_says_so(mcp_client, settings):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        number = len(requests)
        next_uri = f"https://sandboxapi.deere.com/platform/organizations/1/fields?page={number + 1}"
        field = {"id": f"f{number}", "name": f"Field {number}", "archived": False}
        return json_response(deere_list([field], next_uri=next_uri))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_fields", {"org_id": "1"})

    data = result.structured_content
    assert data["truncated"] is True
    assert data["count"] == settings.page_cap == len(requests)
    assert "cut short" in data["note"]


@pytest.mark.anyio
async def test_get_field_includes_active_boundary(mcp_client):
    boundaries_uri = "https://sandboxapi.deere.com/platform/organizations/1/fields/f1/boundaries"
    handler = (
        RouteHandler()
        .add(
            "GET",
            f"{BASE}/organizations/1/fields/f1",
            json_response(
                {
                    "id": "f1",
                    "name": "North 80",
                    "archived": False,
                    "links": [_link("self", "https://x"), _link("boundaries", boundaries_uri)],
                }
            ),
        )
        .add(
            "GET",
            f"{BASE}/organizations/1/fields/f1/boundaries",
            json_response(
                deere_list(
                    [
                        {"id": "b-old", "active": False, "area": {"valueAsDouble": 70.0}},
                        {
                            "id": "b-new",
                            "active": True,
                            "area": {"valueAsDouble": 79.5},
                            "links": [_link("self", "https://x")],
                        },
                    ]
                )
            ),
        )
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_field", {"org_id": "1", "field_id": "f1"})

    data = result.structured_content
    assert data["name"] == "North 80"
    assert "links" not in data
    assert data["boundaries_url"] == boundaries_uri
    assert data["boundary"] == {"id": "b-new", "active": True, "area": {"valueAsDouble": 79.5}}


@pytest.mark.anyio
async def test_get_field_survives_a_failed_boundary_fetch(mcp_client):
    boundaries_uri = "https://sandboxapi.deere.com/platform/organizations/1/fields/f1/boundaries"
    handler = (
        RouteHandler()
        .add(
            "GET",
            f"{BASE}/organizations/1/fields/f1",
            json_response(
                {"id": "f1", "name": "North 80", "links": [_link("boundaries", boundaries_uri)]}
            ),
        )
        .add(
            "GET",
            f"{BASE}/organizations/1/fields/f1/boundaries",
            json_response({"message": "boundary service unavailable"}, status=500),
        )
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_field", {"org_id": "1", "field_id": "f1"})

    assert not result.is_error
    data = result.structured_content
    assert data["name"] == "North 80"
    assert "boundary service unavailable" in data["boundary"]["error"]


@pytest.mark.anyio
async def test_get_field_without_boundaries_link_has_null_boundary(mcp_client):
    handler = RouteHandler().add(
        "GET", f"{BASE}/organizations/1/fields/f1", json_response({"id": "f1", "links": []})
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_field", {"org_id": "1", "field_id": "f1"})

    data = result.structured_content
    assert data["boundary"] is None
    assert data["boundaries_url"] is None
    assert len(handler.requests) == 1


@pytest.mark.anyio
async def test_list_field_operations_sends_query_and_keeps_agronomic_data(mcp_client):
    operations = [
        {
            "id": "op1",
            "fieldOperationType": "HARVEST",
            "cropSeason": "2023",
            "cropName": "CORN",
            "startDate": "2023-10-01T14:00:00Z",
            "endDate": "2023-10-04T22:00:00Z",
            "varieties": [{"name": "DKC62-70", "id": "v1"}],
            "products": [],
            "fieldOperationMachines": [{"machineId": "m1"}],
            "measurementTypes": [
                {"@type": "HarvestYieldResult", "area": {"value": 79.5, "unit": "ac"}},
                {"@type": "HarvestMoisture", "value": 17.2},
            ],
            "links": [
                _link("self", "https://sandboxapi.deere.com/platform/fieldOperations/op1"),
                _link(
                    "measurementTypes",
                    "https://sandboxapi.deere.com/platform/fieldOperations/op1/measurementTypes",
                ),
                _link("shapeFileAsync", "https://sandboxapi.deere.com/platform/fieldOps/op1"),
            ],
        },
        {
            "id": "op2",
            "fieldOperationType": "SEEDING",
            "cropSeason": "2023",
            "cropName": "CORN",
            "links": [_link("self", "https://sandboxapi.deere.com/platform/fieldOperations/op2")],
        },
    ]
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/fields/f1/fieldOperations",
        json_response(deere_list(operations)),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_field_operations",
            {
                "org_id": "1",
                "field_id": "f1",
                "crop_season": 2023,
                "operation_type": "HARVEST",
                "start_date": "2023-10-01T00:00:00Z",
                "end_date": "2023-10-07T00:00:00Z",
            },
        )

    params = handler.requests[0].url.params
    assert params["embed"] == "measurementTypes"
    assert params["cropSeason"] == "2023"
    assert params["fieldOperationType"] == "HARVEST"
    assert params["startDate"] == "2023-10-01T00:00:00Z"
    assert params["endDate"] == "2023-10-07T00:00:00Z"

    data = result.structured_content
    assert data["count"] == 2
    harvest, seeding = data["operations"]
    assert "links" not in harvest
    assert harvest["cropName"] == "CORN"
    assert harvest["varieties"] == [{"name": "DKC62-70", "id": "v1"}]
    assert harvest["fieldOperationMachines"] == [{"machineId": "m1"}]
    assert harvest["measurementTypes"][0]["area"] == {"value": 79.5, "unit": "ac"}
    assert harvest["measurements_url"].endswith("/fieldOperations/op1/measurementTypes")
    assert harvest["shapefile_available"] is True
    assert seeding["shapefile_available"] is False
    assert seeding["measurements_url"] is None


@pytest.mark.anyio
async def test_list_field_operations_omits_unset_filters(mcp_client):
    handler = RouteHandler().add(
        "GET", f"{BASE}/organizations/1/fields/f1/fieldOperations", json_response(deere_list([]))
    )
    async with mcp_client(handler) as client:
        await client.call_tool("jd_list_field_operations", {"org_id": "1", "field_id": "f1"})

    params = handler.requests[0].url.params
    assert dict(params) == {"embed": "measurementTypes"}


@pytest.mark.anyio
async def test_get_field_operation_measurements_single_and_list(mcp_client):
    handler = (
        RouteHandler()
        .add(
            "GET",
            f"{BASE}/fieldOperations/op1/measurementTypes",
            json_response(
                deere_list(
                    [
                        {"@type": "HarvestYieldResult", "links": [_link("self", "https://x")]},
                        {"@type": "HarvestMoisture"},
                    ]
                )
            ),
        )
        .add(
            "GET",
            f"{BASE}/fieldOperations/op1/measurementTypes/HarvestYieldResult",
            json_response(
                {
                    "@type": "HarvestYieldResult",
                    "averageYield": {"value": 215.3},
                    "links": [_link("self", "https://x")],
                }
            ),
        )
    )
    async with mcp_client(handler) as client:
        listed = await client.call_tool(
            "jd_get_field_operation_measurements", {"operation_id": "op1"}
        )
        single = await client.call_tool(
            "jd_get_field_operation_measurements",
            {"operation_id": "op1", "measurement_type": "HarvestYieldResult"},
        )

    assert listed.structured_content == {
        "measurements": [{"@type": "HarvestYieldResult"}, {"@type": "HarvestMoisture"}]
    }
    assert single.structured_content == {
        "@type": "HarvestYieldResult",
        "averageYield": {"value": 215.3},
    }


@pytest.mark.anyio
async def test_deere_403_surfaces_as_tool_error_with_message(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/9/fields",
        json_response({"message": "Organization 9 is not connected to this application"}, 403),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_list_fields", {"org_id": "9"})

    assert result.is_error
    assert "not connected to this application" in _text(result)


@pytest.mark.anyio
async def test_list_guidance_lines_query_and_shape(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/fields/f1/guidanceLines",
        json_response(
            deere_list(
                [
                    {
                        "@type": "GuidanceLine",
                        "id": "g1",
                        "name": "AB 0",
                        "archived": False,
                        "shapes": [{"points": 2}],
                        "links": [_link("self", "https://sandboxapi.deere.com/g1")],
                    },
                    {"@type": "GuidanceLine", "erid": "e2", "name": "Curve", "archived": True},
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        plain = await client.call_tool(
            "jd_list_guidance_lines",
            {"org_id": "1", "field_id": "f1", "status": "available", "record_filter": "active"},
        )
        shaped = await client.call_tool(
            "jd_list_guidance_lines", {"org_id": "1", "field_id": "f1", "embed_shapes": True}
        )

    first = handler.requests[0].url.params
    assert first["status"] == "available"
    assert first["recordFilter"] == "active"
    assert "embed" not in first
    assert handler.requests[1].url.params["embed"] == "shapes"

    lines = plain.structured_content["guidance_lines"]
    assert lines[0] == {
        "id": "g1",
        "erid": None,
        "name": "AB 0",
        "type": "GuidanceLine",
        "archived": False,
        "uri": "https://sandboxapi.deere.com/g1",
    }
    assert lines[1]["id"] == "e2"
    assert lines[1]["erid"] == "e2"
    assert "raw" not in lines[0]
    raw = shaped.structured_content["guidance_lines"][0]["raw"]
    assert raw["shapes"] == [{"points": 2}]
    assert "links" not in raw


@pytest.mark.anyio
async def test_get_guidance_line_embeds_shapes_on_request(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/fields/f1/guidanceLines/g1",
        json_response({"id": "g1", "name": "AB 0", "links": [_link("self", "https://x")]}),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_get_guidance_line",
            {"org_id": "1", "field_id": "f1", "guidance_line_id": "g1", "embed_shapes": True},
        )

    assert handler.requests[0].url.params["embed"] == "shapes"
    assert result.structured_content == {"id": "g1", "name": "AB 0"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("product_type", "segment"),
    [
        ("CHEMICAL", "chemicals"),
        ("FERTILIZER", "fertilizers"),
        ("VARIETY", "varieties"),
        ("TANK_MIX", "tankMixes"),
        ("DRY_BLEND", "dryBlends"),
    ],
)
async def test_list_products_uses_the_segment_for_the_type(mcp_client, product_type, segment):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/{segment}",
        json_response(
            deere_list(
                [
                    {
                        "erid": "p1",
                        "name": "Product One",
                        "category": "HERBICIDE",
                        "type": "CHEMICAL",
                        "cropName": "CORN",
                        "companyName": "Acme",
                        "archived": False,
                        "links": [_link("self", "https://sandboxapi.deere.com/p1")],
                    }
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_products",
            {"org_id": "1", "product_type": product_type, "status": "AVAILABLE"},
        )

    assert handler.requests[0].url.params["status"] == "AVAILABLE"
    assert result.structured_content["products"] == [
        {
            "id": "p1",
            "name": "Product One",
            "category": "HERBICIDE",
            "type": "CHEMICAL",
            "cropName": "CORN",
            "companyName": "Acme",
            "archived": False,
            "uri": "https://sandboxapi.deere.com/p1",
        }
    ]


@pytest.mark.anyio
async def test_list_operators(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/operators",
        json_response(
            deere_list(
                [
                    {
                        "id": "o1",
                        "name": "Sam",
                        "archived": False,
                        "dateModified": "2024-03-01T00:00:00Z",
                        "links": [_link("self", "https://sandboxapi.deere.com/o1")],
                    }
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_operators", {"org_id": "1", "record_filter": "ACTIVE"}
        )

    assert handler.requests[0].url.params["recordFilter"] == "ACTIVE"
    assert result.structured_content["operators"] == [
        {
            "id": "o1",
            "name": "Sam",
            "archived": False,
            "dateModified": "2024-03-01T00:00:00Z",
            "uri": "https://sandboxapi.deere.com/o1",
        }
    ]


@pytest.mark.anyio
async def test_list_work_plans_query_and_summary(mcp_client):
    handler = RouteHandler().add(
        "GET",
        f"{BASE}/organizations/1/workPlans",
        json_response(
            deere_list(
                [
                    {
                        "erid": "w1",
                        "location": {"fieldUri": "https://sandboxapi.deere.com/fields/f1"},
                        "workType": {"instanceDomainId": "dtiSeeding"},
                        "year": 2025,
                        "workStatus": "PLANNED",
                        "workOrder": "Plant corn",
                        "instructions": "North to south",
                        "sequenceNumber": 3,
                        "operations": [{"id": "x"}],
                        "assignments": [{"id": "a"}, {"id": "b"}],
                        "links": [_link("self", "https://x")],
                    },
                    {"erid": "w2"},
                ]
            )
        ),
    )
    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_list_work_plans",
            {
                "org_id": "1",
                "year": 2025,
                "work_type": "dtiSeeding",
                "work_status": "PLANNED",
                "field_ids": ["f1", "f2"],
            },
        )

    params = handler.requests[0].url.params
    assert params["year"] == "2025"
    assert params["workType"] == "dtiSeeding"
    assert params["workStatus"] == "PLANNED"
    assert params["fieldIds"] == "f1,f2"

    plans = result.structured_content["work_plans"]
    assert plans[0] == {
        "erid": "w1",
        "fieldUri": "https://sandboxapi.deere.com/fields/f1",
        "workType": "dtiSeeding",
        "year": 2025,
        "workStatus": "PLANNED",
        "workOrder": "Plant corn",
        "instructions": "North to south",
        "sequenceNumber": 3,
        "operations": [{"id": "x"}],
        "assignmentCount": 2,
    }
    assert plans[1]["fieldUri"] is None
    assert plans[1]["workType"] is None
    assert plans[1]["assignmentCount"] is None


@pytest.mark.anyio
async def test_get_work_plan_strips_links_and_quotes_erid(mcp_client):
    raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_paths.append(request.url.raw_path)
        return json_response({"erid": "a/b", "year": 2025, "links": [_link("self", "https://x")]})

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_get_work_plan", {"org_id": "1", "erid": "a/b"})

    assert raw_paths == [b"/platform/organizations/1/workPlans/a%2Fb"]
    assert result.structured_content == {"erid": "a/b", "year": 2025}
