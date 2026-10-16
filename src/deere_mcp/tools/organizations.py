"""Tools for listing the John Deere organizations the signed-in user can see."""

from dataclasses import replace
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, link, page_dict, tool_errors


def register(mcp: MCPServer) -> None:
    """Register the organization tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_organizations(ctx: Context[AppContext]) -> dict[str, Any]:
        """List the John Deere Operations Center organizations this sign-in can access.

        Returns `organizations`, each with `id`, `name`, `type`, `member` and `connected`.
        Start here: the `id` of an organization is the `org_id` that every other tool needs.
        `connected` is false when the application has not yet been connected to that
        organization; its data will be empty or return 403 until the user opens
        `manage_connections_url` (included only in that case) and approves access.
        """
        client = app(ctx).client
        result = await client.get_all(client.url("organizations"))
        organizations: list[dict[str, Any]] = []
        for org in result.items:
            connections_url = link(org, "connections")
            item: dict[str, Any] = {
                "id": org.get("id"),
                "name": org.get("name"),
                "type": org.get("type"),
                "member": org.get("member"),
                "connected": connections_url is None,
            }
            if connections_url is not None:
                item["manage_connections_url"] = connections_url
            organizations.append(item)
        return page_dict(replace(result, items=organizations), "organizations")
