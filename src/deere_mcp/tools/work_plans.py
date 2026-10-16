"""Tools for reading work plans (planned and completed jobs) of an organization."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, page_dict, tool_errors

OrgId = Annotated[str, Field(description="Organization ID from jd_list_organizations.")]


def _nested(record: dict[str, Any], key: str, inner: str) -> Any:
    """Read `record[key][inner]`, tolerating a missing or non-object `record[key]`."""
    value = record.get(key)
    return value.get(inner) if isinstance(value, dict) else None


def _assignment_count(record: dict[str, Any]) -> int | None:
    """Number of assignments on a work plan, whichever way Deere reports it."""
    count = record.get("assignmentCount")
    if isinstance(count, int):
        return count
    assignments = record.get("assignments")
    return len(assignments) if isinstance(assignments, list) else None


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    work_type = record.get("workType")
    return {
        "erid": record.get("erid"),
        "fieldUri": _nested(record, "location", "fieldUri"),
        "workType": work_type.get("instanceDomainId") if isinstance(work_type, dict) else work_type,
        "year": record.get("year"),
        "workStatus": record.get("workStatus"),
        "workOrder": record.get("workOrder"),
        "instructions": record.get("instructions"),
        "sequenceNumber": record.get("sequenceNumber"),
        "operations": record.get("operations"),
        "assignmentCount": _assignment_count(record),
    }


def register(mcp: MCPServer) -> None:
    """Register the work plan tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_work_plans(
        org_id: OrgId,
        ctx: Context[AppContext],
        year: Annotated[
            int | None, Field(description="Plan year, for example 2025. Omit for all years.")
        ] = None,
        work_type: Annotated[
            Literal["dtiTillage", "dtiSeeding", "dtiApplication", "dtiHarvest"] | None,
            Field(description="Only plans of this kind of work. Omit for all."),
        ] = None,
        work_status: Annotated[
            Literal["PLANNED", "IN_PROGRESS", "COMPLETED", "ALL"] | None,
            Field(description="Only plans in this state. Omit for Deere's default."),
        ] = None,
        field_ids: Annotated[
            list[str] | None,
            Field(description="Only plans for these field IDs from jd_list_fields."),
        ] = None,
    ) -> dict[str, Any]:
        """List the work plans of an organization, optionally for a year, kind of work or fields.

        Returns `work_plans`, each with `erid`, `fieldUri`, `workType`, `year`, `workStatus`,
        `workOrder`, `instructions`, `sequenceNumber`, `operations` and `assignmentCount`.
        Get `org_id` from jd_list_organizations; the `erid` of a plan is the `erid` for
        jd_get_work_plan.
        """
        client = app(ctx).client
        url = client.url(
            "organizations",
            org_id,
            "workPlans",
            query={
                "year": year,
                "workType": work_type,
                "workStatus": work_status,
                "fieldIds": field_ids,
            },
        )
        result = await client.get_all(url)
        plans = [_summary(plan) for plan in result.items]
        return page_dict(replace(result, items=plans), "work_plans")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_work_plan(
        org_id: OrgId,
        erid: Annotated[str, Field(description="Work plan `erid` from jd_list_work_plans.")],
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get the full record of one work plan.

        Returns everything Deere holds for the plan (field, work type, status, instructions,
        operations and assignments). Get `org_id` from jd_list_organizations and `erid` from
        jd_list_work_plans.
        """
        client = app(ctx).client
        plan = await client.get_json(client.url("organizations", org_id, "workPlans", erid))
        if not isinstance(plan, dict):
            raise ToolError("Deere returned an unexpected response for this work plan.")
        return drop_links(plan)
