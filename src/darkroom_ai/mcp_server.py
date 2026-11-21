"""MCP server over stdio.

No socket is opened: the MCP client (Hermes) starts this process and talks to it over stdin/stdout.
A request that needs the person's go-ahead is put to the client as an MCP elicitation. If the
client cannot ask, the person declines, or nobody answers, the request comes back as a clear error
(CONFIRMATION_REQUIRED) and nothing is changed.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from darkroom_ai import __version__
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.mcp_confirm import ElicitationConfirmer
from darkroom_ai.tools.factory import build_tool_registry
from darkroom_ai.tools.registry import ToolRegistry

SERVER_NAME = "darkroom_ai"
INSTRUCTIONS = (
    "Granular tools for Lightroom Classic (and Photoshop) on this computer. They are raw material: "
    "you decide what an edit should be. Look at the photo with get_preview, read facts and "
    "measurements, make one change at a time, then look again. Edits go to virtual copies, are "
    "limited to configured bounds and can be undone. Going beyond a bound asks the person for a "
    "go-ahead; if they decline, nothing changes."
)
NO_CONFIRMATION_HERE = (
    "The request needed the person's go-ahead and did not get it (declined, no answer, or this "
    "client cannot ask), so nothing was changed. Ask the person in the conversation, then retry."
)
MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _image_block(path: str) -> types.ImageContent | None:
    file = Path(path)
    mime = MIME_TYPES.get(file.suffix.lower())
    if mime is None or not file.is_file():
        return None
    return types.ImageContent(
        type="image", data=base64.b64encode(file.read_bytes()).decode("ascii"), mime_type=mime
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
    content: list[types.TextContent | types.ImageContent] = [
        types.TextContent(type="text", text=text)
    ]
    image_path = result.get("image_path")
    if isinstance(image_path, str):
        image = _image_block(image_path)
        if image is not None:
            content.append(image)
    return types.CallToolResult(content=content, structured_content=result)


def build_server(
    registry: ToolRegistry, confirmer: ElicitationConfirmer | None = None
) -> Server[Any]:
    async def on_list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tools(registry))

    async def on_call_tool(
        context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        token = confirmer.bind(context.session, context.request_id) if confirmer else None
        try:
            # Tools block on Lightroom/Photoshop, so run them off the event loop.
            return await anyio.to_thread.run_sync(call_tool, registry, params.name, arguments)
        finally:
            if confirmer is not None and token is not None:
                confirmer.unbind(token)

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _serve(registry: ToolRegistry, confirmer: ElicitationConfirmer | None = None) -> None:
    server = build_server(registry, confirmer)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run(settings: Settings) -> None:
    """Serve the tools over stdio until the client disconnects."""
    confirmer = ElicitationConfirmer()
    anyio.run(_serve, build_tool_registry(settings, confirmer=confirmer), confirmer)
