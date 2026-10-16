"""Tools for reading the guidance lines (AB lines and curves) stored on a field."""

from dataclasses import replace
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from deere_mcp.server import AppContext
from deere_mcp.tools._common import READ_ONLY, app, drop_links, link, page_dict, tool_errors

OrgId = Annotated[str, Field(description="Organization ID from jd_list_organizations.")]
FieldId = Annotated[str, Field(description="Field ID from jd_list_fields.")]
EmbedShapes = Annotated[
    bool,
    Field(description="Also return the line geometry (shapes). Larger responses; default false."),
]


def register(mcp: MCPServer) -> None:
    """Register the guidance line tools on the server."""

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_list_guidance_lines(
        org_id: OrgId,
        field_id: FieldId,
        ctx: Context[AppContext],
        status: Annotated[
            Literal["available", "archived", "all"] | None,
            Field(description="Filter by line status. Omit for Deere's default."),
        ] = None,
        record_filter: Annotated[
            Literal["active", "archived", "all"] | None,
            Field(description="Filter by record state. Omit for Deere's default."),
        ] = None,
        embed_shapes: EmbedShapes = False,
    ) -> dict[str, Any]:
        """List the guidance lines stored on one field.

        Returns `guidance_lines`, each with `id`, `erid`, `name`, `type`, `archived` and `uri`
        (the line's own address). With `embed_shapes` true each item also carries the full
        record, including its geometry, under `raw`. Get `org_id` from jd_list_organizations and
        `field_id` from jd_list_fields; the `id` of a line is the `guidance_line_id` for
        jd_get_guidance_line.
        """
        client = app(ctx).client
        url = client.url(
            "organizations",
            org_id,
            "fields",
            field_id,
            "guidanceLines",
            query={
                "status": status,
                "recordFilter": record_filter,
                "embed": "shapes" if embed_shapes else None,
            },
        )
        result = await client.get_all(url)
        lines: list[dict[str, Any]] = []
        for record in result.items:
            item: dict[str, Any] = {
                "id": record.get("id") or record.get("erid"),
                "erid": record.get("erid"),
                "name": record.get("name"),
                "type": record.get("@type"),
                "archived": record.get("archived"),
                "uri": link(record, "self"),
            }
            if embed_shapes:
                item["raw"] = drop_links(record)
            lines.append(item)
        return page_dict(replace(result, items=lines), "guidance_lines")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def jd_get_guidance_line(
        org_id: OrgId,
        field_id: FieldId,
        guidance_line_id: Annotated[
            str, Field(description="Guidance line ID from jd_list_guidance_lines.")
        ],
        ctx: Context[AppContext],
        embed_shapes: EmbedShapes = False,
    ) -> dict[str, Any]:
        """Get one guidance line on a field.

        Returns the full guidance line record, with its geometry when `embed_shapes` is true.
        Get `org_id` from jd_list_organizations, `field_id` from jd_list_fields and
        `guidance_line_id` from jd_list_guidance_lines.
        """
        client = app(ctx).client
        url = client.url(
            "organizations",
            org_id,
            "fields",
            field_id,
            "guidanceLines",
            guidance_line_id,
            query={"embed": "shapes" if embed_shapes else None},
        )
        record = await client.get_json(url)
        if not isinstance(record, dict):
            raise ToolError("Deere returned an unexpected response for this guidance line.")
        return drop_links(record)
