"""Tools for reading field operations (planting, applications, harvest, tillage) and totals."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, link, page_dict, tool_errors


def _operation(record: dict[str, Any]) -> dict[str, Any]:
    """Keep every agronomic field of an operation; add the two helper fields."""
    item = drop_links(record)
    item["measurements_url"] = link(record, "measurementTypes")
    item["shapefile_available"] = link(record, "shapeFileAsync") is not None
    return item


def register(mcp: MCPServer) -> None:
    """Register the field operation tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_field_operations(
        org_id: Annotated[str, Field(description="Organization ID from jd_list_organizations.")],
        field_id: Annotated[str, Field(description="Field ID from jd_list_fields.")],
        ctx: Context[AppContext],
        crop_season: Annotated[
            int | None,
            Field(description="Crop season year to return, for example 2024. Omit for all."),
        ] = None,
        operation_type: Annotated[
            Literal["APPLICATION", "HARVEST", "SEEDING", "TILLAGE"] | None,
            Field(description="Only operations of this type. Omit for all types."),
        ] = None,
        start_date: Annotated[
            str | None,
            Field(
                description="Start of a date window, ISO 8601 (for example 2024-05-01T00:00:00Z)."
            ),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(description="End of a date window, ISO 8601 (for example 2024-05-07T23:59:59Z)."),
        ] = None,
    ) -> dict[str, Any]:
        """List the field operations recorded on one field: tillage, seeding, application, harvest.

        Returns `operations`, each with `fieldOperationType`, `cropSeason`, `cropName`,
        `startDate`, `endDate`, `products`, `varieties`, `fieldOperationMachines` and the
        measurement totals Deere recorded (area, total and average material, seeding rate,
        yield), plus `measurements_url` and `shapefile_available`. Get `org_id` from
        jd_list_organizations and `field_id` from jd_list_fields; each operation's `id` is the
        `operation_id` for jd_get_field_operation_measurements and the shapefile download.
        One crop season per call is the normal use (`crop_season`). `start_date` and `end_date`
        form a date filter that Deere limits to a 7-day window per request.
        """
        client = app(ctx).client
        url = client.url(
            "organizations",
            org_id,
            "fields",
            field_id,
            "fieldOperations",
            query={
                "cropSeason": crop_season,
                "fieldOperationType": operation_type,
                "startDate": start_date,
                "endDate": end_date,
                "embed": "measurementTypes",
            },
        )
        result = await client.get_all(url)
        operations = [_operation(op) for op in result.items]
        return page_dict(replace(result, items=operations), "operations")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_field_operation_measurements(
        operation_id: Annotated[
            str, Field(description="Field operation ID from jd_list_field_operations.")
        ],
        ctx: Context[AppContext],
        measurement_type: Annotated[
            str | None,
            Field(
                description="One measurement type name as listed by this tool, for example "
                "HarvestYield. Omit to list every measurement type for the operation."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get the measurement totals of one field operation.

        Returns the measurements Deere recorded for the operation (area, material and rate
        totals and averages, yield) as `measurements`, or the single measurement object when
        `measurement_type` is given. The `operation_id` comes from the `id` of an item returned
        by jd_list_field_operations.
        """
        client = app(ctx).client
        segments = ["fieldOperations", operation_id, "measurementTypes"]
        if measurement_type:
            segments.append(measurement_type)
        data = await client.get_json(client.url(*segments))
        if isinstance(data, dict):
            values = data.get("values")
            if isinstance(values, list):
                return {
                    "measurements": [drop_links(v) if isinstance(v, dict) else v for v in values]
                }
            return drop_links(data)
        if isinstance(data, list):
            return {"measurements": data}
        raise ToolError("Deere returned an unexpected response for these measurements.")
