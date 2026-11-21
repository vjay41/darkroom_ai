"""MCP server over real stdio with a SIMULATED Lightroom (started by the MCP client test).

Usage:  python tests/mcp_sim_server.py WORK_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio

from darkroom_ai import mcp_server
from darkroom_ai.adapters.lightroom.simulated import SimulatedLightroom
from darkroom_ai.config import parse_settings
from darkroom_ai.safety.journal import MemoryJournal
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import ToolRegistry


def main() -> None:
    folder = Path(sys.argv[1])
    settings = parse_settings(
        {
            "llm": {
                "active_profile": "m",
                "profiles": {"m": {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model"}},
            },
            "backgrounds": {"dir": str(folder / "backgrounds")},
        }
    )
    context = ToolContext(
        settings=settings, lightroom=SimulatedLightroom(folder), journal=MemoryJournal()
    )
    anyio.run(mcp_server._serve, ToolRegistry(context))


if __name__ == "__main__":
    main()
