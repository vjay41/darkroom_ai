"""MCP against the real Lightroom over real stdio: list tools, one read, one gated call.

A real MCP client starts `darkroom mcp` as a child process. Nothing is edited: the read tool
changes nothing and the gated call must be refused. Settings are compared before and after.

Usage:  poetry run python scripts/mcp_live.py --photo PATH
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from darkroom_ai.adapters.lightroom.bridge_file import load_bridge_file
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.config import load_config


def snapshot(settings: Any, photo_path: Path) -> tuple[str, dict[str, Any]]:
    with LightroomClient.from_settings(settings, load_bridge_file()) as lightroom:
        photo = lightroom.find_photo(str(photo_path))
        if photo is None:
            raise SystemExit(f"{photo_path} is not in the open catalog.")
        return photo.id, dict(lightroom.get_develop_settings(photo.id))


async def session_run(photo_id: str) -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", "darkroom_ai.cli", "mcp"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        with anyio.fail_after(120):
            started = await session.initialize()
            print("server:", started.server_info.name, started.server_info.version)
            tools = await session.list_tools()
            print("tools:", [tool.name for tool in tools.tools])
            info: Any = await session.call_tool("get_photo_info", {"photo_id": photo_id})
            data = info.structured_content or {}
            print(
                "get_photo_info error:",
                info.is_error,
                "| working copy:",
                data.get("working_copy_id"),
                "| settings:",
                json.dumps(data.get("settings")),
            )
            gated: Any = await session.call_tool(
                "adjust_lighting",
                {"photo_id": photo_id, "exposure": 0.2, "allow_beyond_total": True},
            )
            print("gated call error:", gated.is_error)
            print("gated call text:", gated.content[0].text)
            unknown: Any = await session.call_tool("revert", {"photo_id": photo_id})
            print(
                "revert (not a tool) error:",
                unknown.is_error,
                "|",
                unknown.content[0].text.splitlines()[0],
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    args = parser.parse_args()
    settings = load_config(None)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    photo_id, before = snapshot(settings, args.photo)
    anyio.run(session_run, photo_id)
    _, after = snapshot(settings, args.photo)
    print("master settings unchanged:", before == after)
    return 0 if before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
