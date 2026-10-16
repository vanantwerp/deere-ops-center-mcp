"""Machine health tools: alerts, engine hours, hours of operation, location, device state."""

from dataclasses import replace
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, page_dict, tool_errors

PrincipalId = Annotated[
    str,
    Field(description="Machine principal ID: the 'principalId' of an item from jd_list_equipment."),
]
StartDate = Annotated[
    str | None,
    Field(description="Start of the window, ISO 8601 UTC, e.g. 2026-10-01T00:00:00Z."),
]
EndDate = Annotated[
    str | None,
    Field(description="End of the window, ISO 8601 UTC, e.g. 2026-10-07T00:00:00Z."),
]


async def _fetch(
    ctx: Context[AppContext],
    principal_id: str,
    resource: str,
    key: str,
    query: dict[str, Any],
) -> dict[str, Any]:
    """GET /machines/{principal_id}/{resource}, follow paging, strip links, wrap under `key`."""
    client = app(ctx).client
    url = client.url("machines", principal_id, resource, query=query)
    result = await client.get_all(url)
    clean = replace(result, items=[drop_links(item) for item in result.items])
    return page_dict(clean, key)


def register(mcp: MCPServer) -> None:
    """Register the machine_health tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_machine_alerts(
        principal_id: PrincipalId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        exclude_acknowledged: Annotated[
            bool | None,
            Field(description="True to leave out alerts the operator already acknowledged."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get machine alerts (diagnostic trouble codes) for one machine.

        Returns the alerts under 'alerts'. `principal_id` comes from jd_list_equipment. Deere
        allows at most 7 days between start_date and end_date in a single request; ask for a
        shorter window or make several calls.
        """
        query = {
            "startDate": start_date,
            "endDate": end_date,
            "excludeAcknowledged": exclude_acknowledged,
        }
        return await _fetch(ctx, principal_id, "alerts", "alerts", query)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_machine_engine_hours(
        principal_id: PrincipalId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        last_known: Annotated[
            bool | None,
            Field(description="True for only the most recent reading."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get engine hour readings for one machine.

        Returns the readings under 'engine_hours'. `principal_id` comes from jd_list_equipment.
        Use last_known=True for the current total, or a start_date/end_date window for history.
        """
        query = {"startDate": start_date, "endDate": end_date, "lastKnown": last_known}
        return await _fetch(ctx, principal_id, "engineHours", "engine_hours", query)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_machine_hours_of_operation(
        principal_id: PrincipalId,
        org_id: Annotated[
            str | None,
            Field(
                description="Organization ID, from jd_list_organizations. Narrows the result "
                "to that organization."
            ),
        ] = None,
        start_date: StartDate = None,
        end_date: EndDate = None,
        detailed_state: Annotated[
            str | None,
            Field(description="Deere's detailedState filter, passed through as given."),
        ] = None,
        summarize_duration: Annotated[
            str | None,
            Field(description="Deere's summarizeDuration option, passed through as given."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get how long a machine was working, idle or transporting over a period.

        Returns the records under 'hours_of_operation'. `principal_id` comes from
        jd_list_equipment and `org_id` from jd_list_organizations. Use start_date and end_date
        to bound the period; the detailed_state and summarize_duration filters are passed to
        Deere unchanged.
        """
        query = {
            "organizationId": org_id,
            "startDate": start_date,
            "endDate": end_date,
            "detailedState": detailed_state,
            "summarizeDuration": summarize_duration,
        }
        return await _fetch(ctx, principal_id, "hoursOfOperation", "hours_of_operation", query)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_machine_location_history(
        principal_id: PrincipalId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        last_known: Annotated[
            bool | None,
            Field(description="True for only the most recent position."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get GPS location points for one machine.

        Returns the points under 'locations'. `principal_id` comes from jd_list_equipment. Deere
        allows at most 1 month between start_date and end_date in a single request. For where the
        machine is now, prefer last_known=True instead of pulling history.
        """
        query = {"startDate": start_date, "endDate": end_date, "lastKnown": last_known}
        return await _fetch(ctx, principal_id, "locationHistory", "locations", query)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_machine_device_state_reports(
        principal_id: PrincipalId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        last_known: Annotated[
            bool | None,
            Field(description="True for only the most recent report."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get device state (terminal status) reports for one machine.

        Returns the reports under 'device_state_reports'. `principal_id` comes from
        jd_list_equipment. Use last_known=True for the latest report, or a start_date/end_date
        window for history.
        """
        # Deere's documented query names for this endpoint carry a literal "1" suffix.
        query = {"startDate1": start_date, "endDate1": end_date, "lastKnown": last_known}
        return await _fetch(ctx, principal_id, "deviceStateReports", "device_state_reports", query)
