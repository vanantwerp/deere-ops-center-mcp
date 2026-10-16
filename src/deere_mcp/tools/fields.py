"""Tools for listing fields and reading one field with its active boundary."""

from dataclasses import replace
from typing import Annotated, Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.auth import AuthError
from deere_mcp.client import DeereError
from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, link, page_dict, tool_errors

OrgId = Annotated[str, Field(description="Organization ID from jd_list_organizations.")]
FieldId = Annotated[str, Field(description="Field ID from jd_list_fields.")]


def _active_boundary(boundaries: Any) -> dict[str, Any] | None:
    """Pick the active boundary from a Deere boundaries list, else the first one."""
    values = boundaries.get("values") if isinstance(boundaries, dict) else None
    if not isinstance(values, list):
        return None
    records = [v for v in values if isinstance(v, dict)]
    if not records:
        return None
    chosen = next((v for v in records if v.get("active") is True), records[0])
    return drop_links(chosen)


def register(mcp: MCPServer) -> None:
    """Register the field tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_fields(org_id: OrgId, ctx: Context[AppContext]) -> dict[str, Any]:
        """List the fields in one organization.

        Returns `fields`, each with `id`, `name` and `archived`. Get `org_id` from
        jd_list_organizations. The field `id` is the `field_id` used by jd_get_field,
        jd_list_field_operations, jd_list_guidance_lines and jd_list_work_plans (as `field_ids`).
        """
        client = app(ctx).client
        result = await client.get_all(client.url("organizations", org_id, "fields"))
        fields = [
            {"id": f.get("id"), "name": f.get("name"), "archived": f.get("archived")}
            for f in result.items
        ]
        return page_dict(replace(result, items=fields), "fields")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_field(
        org_id: OrgId, field_id: FieldId, ctx: Context[AppContext]
    ) -> dict[str, Any]:
        """Get one field's details together with its active boundary.

        Returns the field record plus `boundary` (the active boundary with its geometry, or
        null when the field has none, or `{"error": ...}` if the boundary could not be fetched)
        and `boundaries_url`. Get `org_id` from jd_list_organizations and `field_id` from
        jd_list_fields.
        """
        client = app(ctx).client
        field = await client.get_json(client.url("organizations", org_id, "fields", field_id))
        if not isinstance(field, dict):
            raise ToolError("Deere returned an unexpected response for this field.")
        result = drop_links(field)
        boundaries_url = link(field, "boundaries")
        boundary: dict[str, Any] | None = None
        if boundaries_url is not None:
            try:
                boundary = _active_boundary(await client.get_json(boundaries_url))
            except (DeereError, AuthError, httpx.HTTPError) as exc:
                boundary = {"error": str(exc)}
        result["boundary"] = boundary
        result["boundaries_url"] = boundaries_url
        return result
