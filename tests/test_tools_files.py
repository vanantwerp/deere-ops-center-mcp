"""Tests for the file tools: shapefile download, save file, and multi-season export."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from deere_mcp.tools import files
from tests.helpers import RouteHandler, deere_list, json_response

pytestmark = pytest.mark.anyio

SHAPEFILE_PATH = "/platform/fieldOps/op1"
PRESIGNED = "https://deere-files.storage.example.net/exports/op1.zip?X-Amz-Signature=abc123"
ZIP_BYTES = bytes(range(256)) * 1000  # about 256 KB, so it arrives in several chunks
FIELD_OPS_PATH = "/platform/organizations/1/fields/2/fieldOperations"


# --- helpers -------------------------------------------------------------------------------


def sequence(*factories: Callable[[], httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer with each factory's response in turn, then keep repeating the last one."""
    remaining = list(factories)

    def respond(request: httpx.Request) -> httpx.Response:
        factory = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return factory()

    return respond


def preparing() -> httpx.Response:
    return httpx.Response(202)


def redirect_to(location: str = PRESIGNED) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(307, headers={"location": location})


class Storage:
    """Stands in for the pre-signed download host and records what it was asked."""

    def __init__(self) -> None:
        self.body = ZIP_BYTES
        self.status = 200
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            self.status, content=self.body, headers={"content-type": "application/zip"}
        )


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Storage:
    store = Storage()
    monkeypatch.setattr(
        files,
        "new_download_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(store), follow_redirects=True),
    )
    return store


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "POLL_DELAYS", (0, 0, 0, 0))


def error_text(result) -> str:
    assert result.is_error
    return result.content[0].text


# --- registration --------------------------------------------------------------------------


async def test_tools_are_listed_without_ctx_in_the_schema(mcp_client) -> None:
    async with mcp_client(RouteHandler()) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in ("jd_download_operation_shapefile", "jd_save_file", "jd_export_field_operations"):
        assert name in listed
        assert listed[name].description
        assert "ctx" not in listed[name].input_schema["properties"]
    assert listed["jd_save_file"].input_schema["required"] == ["filename", "content"]


# --- jd_save_file --------------------------------------------------------------------------


async def test_save_file_writes_inside_the_output_folder(mcp_client, settings) -> None:
    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool(
            "jd_save_file", {"filename": "yield.csv", "content": "field,bu\nNorth 80,212\n"}
        )

    assert not result.is_error
    path = Path(result.structured_content["path"])
    assert path == settings.output_dir.resolve() / "yield.csv"
    assert path.read_text(encoding="utf-8") == "field,bu\nNorth 80,212\n"
    assert result.structured_content["bytes"] == len("field,bu\nNorth 80,212\n")


async def test_save_file_counts_bytes_not_characters_and_allows_subfolders(
    mcp_client, settings
) -> None:
    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool(
            "jd_save_file", {"filename": "reports/2024/notes.txt", "content": "Café"}
        )

    assert not result.is_error
    assert result.structured_content["bytes"] == 5
    path = settings.output_dir.resolve() / "reports" / "2024" / "notes.txt"
    assert path.read_bytes() == "Café".encode()


async def test_save_file_replaces_an_existing_file(mcp_client, settings) -> None:
    async with mcp_client(RouteHandler()) as client:
        await client.call_tool("jd_save_file", {"filename": "a.txt", "content": "first"})
        await client.call_tool("jd_save_file", {"filename": "a.txt", "content": "second"})

    assert (settings.output_dir / "a.txt").read_text() == "second"


@pytest.mark.parametrize("filename", ["../escape.txt", "sub/../../escape.txt"])
async def test_save_file_refuses_paths_that_climb_out(mcp_client, settings, filename) -> None:
    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool("jd_save_file", {"filename": filename, "content": "x"})

    assert result.is_error
    assert not (settings.output_dir.parent / "escape.txt").exists()
    assert not (settings.output_dir / "escape.txt").exists()


async def test_save_file_refuses_an_absolute_path_outside_the_folder(
    mcp_client, settings, tmp_path
) -> None:
    outside = tmp_path / "outside.txt"

    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool("jd_save_file", {"filename": str(outside), "content": "x"})

    assert result.is_error
    assert not outside.exists()


@pytest.mark.parametrize("filename", ["", "."])
async def test_save_file_refuses_a_name_that_is_the_folder_itself(mcp_client, filename) -> None:
    async with mcp_client(RouteHandler()) as client:
        result = await client.call_tool("jd_save_file", {"filename": filename, "content": "x"})

    assert "file, not a folder" in error_text(result)


# --- jd_download_operation_shapefile -------------------------------------------------------


async def test_shapefile_polls_then_downloads_without_deere_credentials(
    mcp_client, settings, storage, no_wait
) -> None:
    handler = RouteHandler().add(
        "GET", SHAPEFILE_PATH, sequence(preparing, preparing, redirect_to())
    )

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile",
            {"operation_id": "op1", "shape_type": "Point", "resolution": "OneHertz"},
        )

    assert not result.is_error
    expected = settings.output_dir.resolve() / "operation-op1.zip"
    assert result.structured_content == {
        "status": "saved",
        "path": str(expected),
        "bytes": len(ZIP_BYTES),
    }
    assert expected.read_bytes() == ZIP_BYTES
    # No half-written ".part" file is left beside the finished one.
    assert [p.name for p in settings.output_dir.iterdir()] == ["operation-op1.zip"]

    # Three calls to Deere: still preparing twice, then the redirect. Each one is signed in
    # and carries the query exactly as Deere names it.
    assert len(handler.requests) == 3
    for request in handler.requests:
        assert request.headers["authorization"].lower().startswith("bearer ")
        assert request.url.params["shapeType"] == "Point"
        assert request.url.params["resolution"] == "OneHertz"

    # The pre-signed host got one plain request and never saw a Deere credential.
    assert len(storage.requests) == 1
    download = storage.requests[0]
    assert download.url.host == "deere-files.storage.example.net"
    assert download.url.params["X-Amz-Signature"] == "abc123"
    assert "authorization" not in download.headers
    assert "cookie" not in download.headers


async def test_shapefile_leaves_out_unset_query_parameters(mcp_client, storage, no_wait) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(redirect_to()))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    assert not result.is_error
    params = handler.requests[0].url.params
    assert "shapeType" not in params
    assert "resolution" not in params


async def test_shapefile_uses_the_requested_filename(
    mcp_client, settings, storage, no_wait
) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(redirect_to()))

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile",
            {"operation_id": "op1", "filename": "2024/north-80-harvest.zip"},
        )

    assert not result.is_error
    saved = settings.output_dir.resolve() / "2024" / "north-80-harvest.zip"
    assert Path(result.structured_content["path"]) == saved
    assert saved.read_bytes() == ZIP_BYTES


async def test_shapefile_still_preparing_is_a_result_not_an_error(
    mcp_client, settings, storage, no_wait
) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(preparing))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    assert not result.is_error
    assert result.structured_content["status"] == "preparing"
    assert result.structured_content["retry_after_seconds"] == 60
    assert result.structured_content["message"]
    # The first call plus one retry for each configured delay, then it stops.
    assert len(handler.requests) == 1 + len(files.POLL_DELAYS)
    assert storage.requests == []
    assert not settings.output_dir.exists() or list(settings.output_dir.iterdir()) == []


async def test_shapefile_with_no_wait_makes_a_single_call_and_never_sleeps(
    mcp_client, storage
) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(preparing))

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile", {"operation_id": "op1", "max_wait_seconds": 0}
        )

    assert result.structured_content["status"] == "preparing"
    assert len(handler.requests) == 1


async def test_shapefile_stops_polling_when_the_wait_budget_runs_out(
    mcp_client, monkeypatch
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(files, "asyncio", SimpleNamespace(sleep=fake_sleep))
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(preparing))

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile", {"operation_id": "op1", "max_wait_seconds": 30}
        )

    # Default delays are 5, 10, 20, 40. After 5 + 10 the next 20 would pass 30, so it stops.
    assert files.POLL_DELAYS == (5, 10, 20, 40)
    assert slept == [5, 10]
    assert len(handler.requests) == 3
    assert result.structured_content["status"] == "preparing"


async def test_shapefile_not_available_is_a_tool_error(mcp_client, storage, no_wait) -> None:
    handler = RouteHandler().add(
        "GET", SHAPEFILE_PATH, json_response({"message": "Not Acceptable"}, status=406)
    )

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    assert "Deere cannot generate a shapefile for this operation." in error_text(result)
    assert storage.requests == []


async def test_shapefile_other_deere_errors_show_deeres_message(
    mcp_client, storage, no_wait
) -> None:
    handler = RouteHandler().add(
        "GET", SHAPEFILE_PATH, json_response({"message": "Access denied for this org"}, status=403)
    )

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    assert "Access denied for this org" in error_text(result)


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(307),  # no Location header at all
        lambda: httpx.Response(307, headers={"location": "http://plain.example.net/x.zip"}),
        lambda: httpx.Response(307, headers={"location": "/relative/x.zip"}),
        lambda: httpx.Response(204),  # not something Deere should answer with
    ],
)
async def test_shapefile_rejects_unusable_answers(mcp_client, storage, no_wait, response) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(response))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    assert result.is_error
    assert storage.requests == []


async def test_shapefile_failed_download_saves_nothing_and_hides_the_signed_link(
    mcp_client, settings, storage, no_wait
) -> None:
    storage.status = 403
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(redirect_to()))

    async with mcp_client(handler) as client:
        result = await client.call_tool("jd_download_operation_shapefile", {"operation_id": "op1"})

    message = error_text(result)
    assert "403" in message
    assert "abc123" not in message
    assert list(settings.output_dir.iterdir()) == []


async def test_shapefile_refuses_a_filename_outside_the_folder_before_calling_deere(
    mcp_client, settings, storage, no_wait
) -> None:
    handler = RouteHandler().add("GET", SHAPEFILE_PATH, sequence(redirect_to()))

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile",
            {"operation_id": "op1", "filename": "../stolen.zip"},
        )

    assert result.is_error
    assert handler.requests == []
    assert storage.requests == []
    assert not (settings.output_dir.parent / "stolen.zip").exists()


async def test_shapefile_default_filename_cannot_escape_through_the_operation_id(
    mcp_client, settings, storage, no_wait
) -> None:
    requests: list[httpx.Request] = []

    def any_operation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return redirect_to()()

    async with mcp_client(any_operation) as client:
        result = await client.call_tool(
            "jd_download_operation_shapefile", {"operation_id": "../../x"}
        )

    assert not result.is_error
    saved = Path(result.structured_content["path"])
    assert saved.parent == settings.output_dir.resolve()
    assert saved.read_bytes() == ZIP_BYTES
    assert not list(settings.output_dir.parent.glob("x*"))
    # The ID is quoted into one path segment, so it cannot reach another Deere endpoint either.
    assert requests[0].url.raw_path.startswith(b"/platform/fieldOps/..%2F..%2Fx")


# --- jd_export_field_operations ------------------------------------------------------------


def operations_for_season(request: httpx.Request) -> httpx.Response:
    season = int(request.url.params["cropSeason"])
    return json_response(
        deere_list(
            [
                {
                    "id": f"op-{season}-1",
                    "fieldOperationType": "HARVEST",
                    "cropSeason": season,
                    "cropName": "CORN",
                    "varieties": [{"name": "DKC 62-70"}],
                    "measurementTypes": [{"@type": "HarvestYield", "value": 212.4}],
                    "links": [{"rel": "self", "uri": f"https://example.deere.com/op/{season}"}],
                },
                {"id": f"op-{season}-2", "fieldOperationType": "TILLAGE", "cropSeason": season},
            ]
        )
    )


async def test_export_writes_one_json_file_with_every_season(mcp_client, settings) -> None:
    handler = RouteHandler().add("GET", FIELD_OPS_PATH, operations_for_season)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations",
            {"org_id": "1", "field_id": "2", "seasons": [2023, 2024]},
        )

    assert not result.is_error
    expected = settings.output_dir.resolve() / "field-2-operations.json"
    assert result.structured_content == {
        "path": str(expected),
        "seasons": {"2023": 2, "2024": 2},
        "truncated_seasons": [],
    }

    document = json.loads(expected.read_text(encoding="utf-8"))
    assert document["org_id"] == "1"
    assert document["field_id"] == "2"
    assert document["truncated_seasons"] == []
    assert list(document["seasons"]) == ["2023", "2024"]
    harvest = document["seasons"]["2024"][0]
    assert harvest["id"] == "op-2024-1"
    assert harvest["cropName"] == "CORN"
    assert harvest["varieties"] == [{"name": "DKC 62-70"}]
    assert harvest["measurementTypes"] == [{"@type": "HarvestYield", "value": 212.4}]
    assert "links" not in harvest

    # One request per season, each asking for the embedded totals.
    assert [r.url.params["cropSeason"] for r in handler.requests] == ["2023", "2024"]
    assert all(r.url.params["embed"] == "measurementTypes" for r in handler.requests)
    assert all(r.url.path == FIELD_OPS_PATH for r in handler.requests)


async def test_export_asks_for_each_season_once(mcp_client) -> None:
    handler = RouteHandler().add("GET", FIELD_OPS_PATH, operations_for_season)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations",
            {"org_id": "1", "field_id": "2", "seasons": [2024, 2024, 2023]},
        )

    assert not result.is_error
    assert [r.url.params["cropSeason"] for r in handler.requests] == ["2024", "2023"]


async def test_export_uses_the_requested_filename(mcp_client, settings) -> None:
    handler = RouteHandler().add("GET", FIELD_OPS_PATH, operations_for_season)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations",
            {"org_id": "1", "field_id": "2", "seasons": [2024], "filename": "exports/home.json"},
        )

    assert not result.is_error
    assert (settings.output_dir / "exports" / "home.json").is_file()


async def test_export_reports_seasons_cut_short_by_the_page_cap(mcp_client, settings) -> None:
    next_page = (
        "https://sandboxapi.deere.com/platform/organizations/1/fields/2/fieldOperations"
        "?cropSeason=2024&page=next"
    )

    def endless(request: httpx.Request) -> httpx.Response:
        return json_response(deere_list([{"id": "op"}], next_uri=next_page))

    handler = RouteHandler().add("GET", FIELD_OPS_PATH, endless)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations",
            {"org_id": "1", "field_id": "2", "seasons": [2024]},
        )

    assert not result.is_error
    # The test settings cap a walk at 3 pages, one record each.
    assert result.structured_content["seasons"] == {"2024": 3}
    assert result.structured_content["truncated_seasons"] == ["2024"]
    assert "2024" in result.structured_content["note"]
    saved = json.loads(Path(result.structured_content["path"]).read_text(encoding="utf-8"))
    assert saved["truncated_seasons"] == ["2024"]


async def test_export_refuses_a_filename_outside_the_folder_before_calling_deere(
    mcp_client, settings
) -> None:
    handler = RouteHandler().add("GET", FIELD_OPS_PATH, operations_for_season)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations",
            {"org_id": "1", "field_id": "2", "seasons": [2024], "filename": "../leak.json"},
        )

    assert result.is_error
    assert handler.requests == []
    assert not (settings.output_dir.parent / "leak.json").exists()


async def test_export_needs_at_least_one_season(mcp_client) -> None:
    handler = RouteHandler().add("GET", FIELD_OPS_PATH, operations_for_season)

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations", {"org_id": "1", "field_id": "2", "seasons": []}
        )

    assert result.is_error
    assert handler.requests == []


async def test_export_failure_from_deere_is_a_tool_error_and_writes_no_file(
    mcp_client, settings
) -> None:
    handler = RouteHandler().add(
        "GET", FIELD_OPS_PATH, json_response({"message": "Forbidden for this org"}, status=403)
    )

    async with mcp_client(handler) as client:
        result = await client.call_tool(
            "jd_export_field_operations", {"org_id": "1", "field_id": "2", "seasons": [2024]}
        )

    assert "Forbidden for this org" in error_text(result)
    assert not (settings.output_dir / "field-2-operations.json").exists()
