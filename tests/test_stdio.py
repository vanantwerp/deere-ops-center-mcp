"""End-to-end check over the real stdio transport.

Launches the installed console script as a child process, exactly as a desktop host would,
and confirms the handshake completes and the tools are listed. No Deere calls are made.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

EXPECTED_TOOLS = {
    "jd_list_organizations",
    "jd_list_fields",
    "jd_get_field",
    "jd_list_field_operations",
    "jd_get_field_operation_measurements",
    "jd_list_guidance_lines",
    "jd_get_guidance_line",
    "jd_list_products",
    "jd_list_operators",
    "jd_list_work_plans",
    "jd_get_work_plan",
    "jd_list_equipment",
    "jd_get_equipment",
    "jd_get_machine_alerts",
    "jd_get_machine_engine_hours",
    "jd_get_machine_hours_of_operation",
    "jd_get_machine_location_history",
    "jd_get_machine_device_state_reports",
    "jd_get",
    "jd_download_operation_shapefile",
    "jd_save_file",
    "jd_export_field_operations",
}
WRITE_TOOLS = {"jd_download_operation_shapefile", "jd_save_file", "jd_export_field_operations"}


def _console_script() -> str | None:
    """Find the deere-mcp script next to the running interpreter, or on PATH."""
    candidate = Path(sys.executable).with_name("deere-mcp")
    if os.name == "nt":
        candidate = candidate.with_suffix(".exe")
    if candidate.exists():
        return str(candidate)
    return shutil.which("deere-mcp")


@pytest.mark.anyio
async def test_stdio_handshake_lists_all_tools(tmp_path: Path) -> None:
    script = _console_script()
    if script is None:
        pytest.skip("deere-mcp console script is not installed in this environment")

    # Keep the child away from the real per-user config folder and give it dummy credentials.
    env = {
        "DEERE_CLIENT_ID": "test-id",
        "DEERE_CLIENT_SECRET": "test-secret",
        "DEERE_OUTPUT_DIR": str(tmp_path / "out"),
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        "APPDATA": str(tmp_path / "AppData" / "Roaming"),
        "USERPROFILE": str(tmp_path),
        "PYTHONUTF8": "1",
    }
    server = StdioServerParameters(command=script, args=[], env=env)

    async with Client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert EXPECTED_TOOLS <= names, f"missing tools: {sorted(EXPECTED_TOOLS - names)}"
    for tool in result.tools:
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        expected = tool.name not in WRITE_TOOLS
        assert tool.annotations.read_only_hint is expected, f"{tool.name}: wrong read_only_hint"
