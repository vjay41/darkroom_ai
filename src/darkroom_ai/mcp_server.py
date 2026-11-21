"""MCP server over stdio, on the same tool registry as the agent.

No socket is opened: the MCP client starts this process and talks to it over stdin/stdout.
There is nobody to ask for a confirmation here, so the registry is built with the default
confirmer that refuses: a request that needs the user's go-ahead comes back as a clear error
(CONFIRMATION_REQUIRED) and nothing is changed.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from darkroom_ai import __version__
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.factory import build_tool_registry
from darkroom_ai.tools.registry import ToolRegistry

SERVER_NAME = "darkroom_ai"
INSTRUCTIONS = (
    "Local photo-editing tools for Lightroom Classic and Photoshop on this computer. Edits go to "
    "virtual copies and are clamped to configured limits. Requests that need the user's "
    "confirmation are refused through this interface; use `darkroom chat` for those."
)
NO_CONFIRMATION_HERE = (
    "This interface cannot ask you for a confirmation, so the request was refused and nothing "
    "was changed. Use `darkroom chat` on this computer for requests that need your go-ahead."
)


def list_tools(registry: ToolRegistry) -> list[types.Tool]:
    return [
        types.Tool(
            name=schema["name"],
            description=schema["description"],
            input_schema=schema["parameters"],
        )
        for schema in registry.schemas()
    ]


def call_tool(registry: ToolRegistry, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """Run one tool; failures become an error result with the code, message and next step."""
    try:
        result = registry.call(name, arguments)
    except DarkroomError as exc:
        next_step = exc.next_step
        if exc.code is ErrorCode.CONFIRMATION_REQUIRED:
            next_step = NO_CONFIRMATION_HERE
        text = f"[{exc.code.value}] {exc.message}\nNext step: {next_step}"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=True
        )
    text = json.dumps(result, indent=2, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], structured_content=result
    )


def build_server(registry: ToolRegistry) -> Server[Any]:
    async def on_list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tools(registry))

    async def on_call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        # Tools block on Lightroom/Photoshop, so run them off the event loop.
        return await anyio.to_thread.run_sync(call_tool, registry, params.name, arguments)

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _serve(registry: ToolRegistry) -> None:
    server = build_server(registry)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run(settings: Settings) -> None:
    """Serve the tools over stdio until the client disconnects."""
    anyio.run(_serve, build_tool_registry(settings))
