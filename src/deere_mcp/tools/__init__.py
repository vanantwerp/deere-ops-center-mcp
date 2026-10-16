"""All tool modules. Each one exposes ``register(mcp)``."""

from mcp.server import MCPServer

from . import (
    equipment,
    field_operations,
    fields,
    files,
    guidance_lines,
    machine_health,
    operators,
    organizations,
    passthrough,
    products,
    work_plans,
)

MODULES = (
    organizations,
    fields,
    field_operations,
    guidance_lines,
    products,
    operators,
    work_plans,
    equipment,
    machine_health,
    passthrough,
    files,
)


def register_all(mcp: MCPServer) -> None:
    """Register every tool module's tools on the server."""
    for module in MODULES:
        module.register(mcp)
