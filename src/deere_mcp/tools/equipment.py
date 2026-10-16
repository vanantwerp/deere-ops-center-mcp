"""Equipment tools: machines and implements from John Deere's Equipment API."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, link, page_dict, tool_errors

ITEM_LIMIT_MIN = 1
ITEM_LIMIT_MAX = 5000


def _name_of(value: Any) -> Any:
    """Deere sometimes returns a plain string and sometimes an object with a `name`."""
    if isinstance(value, dict):
        return value.get("name")
    return value


def _summarize(item: dict[str, Any], *, keep_raw: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": item.get("id"),
        "principalId": item.get("principalId"),
        "name": item.get("name"),
        "category": _name_of(item.get("category") or item.get("equipmentCategory")),
        "type": _name_of(item.get("type")),
        "archived": item.get("archived"),
        "uri": link(item, "self"),
    }
    if keep_raw:
        # An `embed` was requested; do not throw away the embedded data.
        summary["raw"] = drop_links(item)
    return summary


def register(mcp: MCPServer) -> None:
    """Register the equipment tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_equipment(
        org_id: Annotated[str, Field(description="Organization ID, from jd_list_organizations.")],
        category: Annotated[
            Literal["Machine", "Implement"] | None,
            Field(description="Only machines or only implements."),
        ] = None,
        archived: Annotated[
            bool | None,
            Field(description="True for archived equipment only, False for active only."),
        ] = None,
        ids: Annotated[list[str] | None, Field(description="Only these equipment IDs.")] = None,
        serial_numbers: Annotated[
            list[str] | None, Field(description="Only equipment with these serial numbers.")
        ] = None,
        principal_ids: Annotated[
            list[str] | None, Field(description="Only equipment with these principal IDs.")
        ] = None,
        capable_of: Annotated[
            Literal["Connectivity", "!Connectivity"] | None,
            Field(
                description="'Connectivity' for connected equipment, '!Connectivity' for the rest."
            ),
        ] = None,
        organization_role_type: Annotated[
            Literal["Controlling", "NonControlling"] | None,
            Field(description="Whether this organization controls the equipment or only sees it."),
        ] = None,
        embed: Annotated[
            Literal["devices", "equipment", "icon", "pairingDetails"] | None,
            Field(description="Ask Deere to embed extra data in each item; returned under 'raw'."),
        ] = None,
        item_limit: Annotated[
            int | None,
            Field(description="Page size, 1 to 5000. Deere's default applies when omitted."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """List the machines and implements in an organization.

        Returns id, principalId, name, category, type, archived and uri for each item. `org_id`
        comes from jd_list_organizations. The principalId of a machine is what the
        jd_get_machine_* tools (alerts, engine hours, hours of operation, location history,
        device state reports) take as `principal_id`; `id` is what jd_get_equipment takes. When
        `embed` is set, the full record with the embedded data is added to each item under 'raw'.
        """
        if item_limit is not None and not ITEM_LIMIT_MIN <= item_limit <= ITEM_LIMIT_MAX:
            raise ToolError(f"item_limit must be between {ITEM_LIMIT_MIN} and {ITEM_LIMIT_MAX}.")
        client = app(ctx).client
        url = client.equipment_url(
            "equipment",
            query={
                "organizationIds": org_id,
                "ids": ids or None,
                "serialNumbers": serial_numbers or None,
                "principalIds": principal_ids or None,
                "categories": category,
                "capableOf": capable_of,
                "organizationRole.type": organization_role_type,
                "archived": archived,
                "embed": embed,
                "itemLimit": item_limit,
            },
        )
        result = await client.get_all(url)
        keep_raw = embed is not None
        items = [_summarize(item, keep_raw=keep_raw) for item in result.items]
        return page_dict(replace(result, items=items), "equipment")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_equipment(
        equipment_id: Annotated[
            str, Field(description="Equipment ID (the 'id' field from jd_list_equipment).")
        ],
        embed: Annotated[
            Literal["devices", "equipment", "pairingDetails", "icon", "offsets", "capabilities"]
            | None,
            Field(description="Ask Deere to embed extra data in the record."),
        ] = None,
        *,
        ctx: Context[AppContext],
    ) -> dict[str, Any]:
        """Get the full record for one machine or implement.

        Returns everything Deere has on the piece of equipment, including any data requested
        with `embed`. `equipment_id` is the 'id' of an item from jd_list_equipment; the
        principalId shown there is a different identifier and is only for the jd_get_machine_*
        tools.
        """
        client = app(ctx).client
        url = client.equipment_url("equipment", equipment_id, query={"embed": embed})
        record = await client.get_json(url)
        if not isinstance(record, dict):
            return {"data": record}
        return drop_links(record)
