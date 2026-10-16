"""Tools for listing the machine operators of an organization."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, link, page_dict, tool_errors


def register(mcp: MCPServer) -> None:
    """Register the operator tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_operators(
        org_id: Annotated[str, Field(description="Organization ID from jd_list_organizations.")],
        ctx: Context[AppContext],
        record_filter: Annotated[
            Literal["ACTIVE", "ALL", "ARCHIVED"] | None,
            Field(description="Which operators to return. Omit for Deere's default."),
        ] = None,
    ) -> dict[str, Any]:
        """List the machine operators defined in an organization.

        Returns `operators`, each with `id`, `name`, `archived`, `dateModified` and `uri`.
        Get `org_id` from jd_list_organizations.
        """
        client = app(ctx).client
        url = client.url(
            "organizations", org_id, "operators", query={"recordFilter": record_filter}
        )
        result = await client.get_all(url)
        operators = [
            {
                "id": o.get("id"),
                "name": o.get("name"),
                "archived": o.get("archived"),
                "dateModified": o.get("dateModified"),
                "uri": link(o, "self"),
            }
            for o in result.items
        ]
        return page_dict(replace(result, items=operators), "operators")
