"""Tools for listing the products (chemicals, fertilizers, varieties, mixes) of an organization."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, link, page_dict, tool_errors

SEGMENTS = {
    "CHEMICAL": "chemicals",
    "FERTILIZER": "fertilizers",
    "VARIETY": "varieties",
    "TANK_MIX": "tankMixes",
    "DRY_BLEND": "dryBlends",
}


def register(mcp: MCPServer) -> None:
    """Register the product tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_products(
        org_id: Annotated[str, Field(description="Organization ID from jd_list_organizations.")],
        product_type: Annotated[
            Literal["CHEMICAL", "FERTILIZER", "VARIETY", "TANK_MIX", "DRY_BLEND"],
            Field(description="Which kind of product to list."),
        ],
        ctx: Context[AppContext],
        status: Annotated[
            Literal["AVAILABLE", "ARCHIVED", "ALL"] | None,
            Field(description="Filter by availability. Omit for Deere's default."),
        ] = None,
    ) -> dict[str, Any]:
        """List one kind of product defined in an organization.

        Returns `products`, each with `id`, `name`, `category`, `type`, `cropName`,
        `companyName`, `archived` and `uri`. One call lists one `product_type`: chemicals,
        fertilizers, seed varieties, tank mixes or dry blends. Get `org_id` from
        jd_list_organizations. Use it to see what the organization has defined, for example
        which seed varieties or chemicals exist.
        """
        client = app(ctx).client
        url = client.url("organizations", org_id, SEGMENTS[product_type], query={"status": status})
        result = await client.get_all(url)
        products = [
            {
                "id": p.get("id") or p.get("erid"),
                "name": p.get("name"),
                "category": p.get("category"),
                "type": p.get("type"),
                "cropName": p.get("cropName"),
                "companyName": p.get("companyName"),
                "archived": p.get("archived"),
                "uri": link(p, "self"),
            }
            for p in result.items
        ]
        return page_dict(replace(result, items=products), "products")
