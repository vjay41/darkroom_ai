"""MCP server over stdio.

No socket is opened: the MCP client (Hermes) starts this process and talks to it over stdin/stdout.
A request that needs the person's go-ahead is put to the client as an MCP elicitation. If the
client cannot ask, the person declines, or nobody answers, the request comes back as a clear error
(CONFIRMATION_REQUIRED) and nothing is changed.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import sys
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
from darkroom_ai.runtime import (
    CodeGuard,
    InstanceRecord,
    StaleWatchdog,
    WindowsInstanceControl,
    register,
    stop_stale,
    unregister,
)
from darkroom_ai.tools.factory import build_tool_context
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
    "client cannot ask), so nothing was changed. Do NOT retry in a loop. Tell the person what was "
    "refused; only their own answer to the approval prompt counts, not a reply in the chat."
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
    registry: ToolRegistry,
    confirmer: ElicitationConfirmer | None = None,
    watchdog: StaleWatchdog | None = None,
) -> Server[Any]:
    async def on_list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tools(registry))

    async def on_call_tool(
        context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        token = confirmer.bind(context.session, context.request_id) if confirmer else None
        if watchdog is not None:
            watchdog.call_started()
        try:
            # Tools block on Lightroom/Photoshop, so run them off the event loop.
            return await anyio.to_thread.run_sync(call_tool, registry, params.name, arguments)
        finally:
            if watchdog is not None:
                watchdog.call_finished()
            if confirmer is not None and token is not None:
                confirmer.unbind(token)

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


WATCHDOG_POLL_S = 5.0


async def _watch_for_stale(watchdog: StaleWatchdog, guard: CodeGuard) -> None:
    """End this process once it is stale and idle long enough; the client then starts a new one."""
    while True:
        await anyio.sleep(WATCHDOG_POLL_S)
        if watchdog.should_exit():
            print(
                f"darkroom_ai: connector pid {guard.pid} runs older code than the files on disk "
                "and is idle: exiting so a fresh one can start",
                file=sys.stderr,
                flush=True,
            )
            atexit._run_exitfuncs()  # unregister this connector before leaving
            os._exit(0)


async def _serve(
    registry: ToolRegistry,
    confirmer: ElicitationConfirmer | None = None,
    watchdog: StaleWatchdog | None = None,
    guard: CodeGuard | None = None,
) -> None:
    server = build_server(registry, confirmer, watchdog)
    async with stdio_server() as (read_stream, write_stream), anyio.create_task_group() as group:
        if watchdog is not None and guard is not None:
            group.start_soon(_watch_for_stale, watchdog, guard)
        await server.run(read_stream, write_stream, server.create_initialization_options())
        group.cancel_scope.cancel()


def run(settings: Settings) -> None:
    """Serve the tools over stdio until the client disconnects."""
    confirmer = ElicitationConfirmer()
    guard = CodeGuard(Path(__file__).resolve().parent)
    _claim_instance(settings, guard)
    context = build_tool_context(settings, confirmer=confirmer)
    context.connector = guard
    print(
        f"darkroom_ai: connector pid {guard.pid}, code {guard.version}, started {guard.started_at}",
        file=sys.stderr,
        flush=True,
    )
    runtime = settings.runtime
    watchdog = StaleWatchdog(
        guard, grace_s=runtime.stale_exit_after_s, idle_s=runtime.stale_exit_idle_s
    )
    anyio.run(_serve, ToolRegistry(context), confirmer, watchdog, guard)


def _claim_instance(settings: Settings, guard: CodeGuard) -> None:
    """Stop connectors that run different (older) code, then register this one."""
    directory = settings.paths.logs_dir.resolve() / "connectors"
    control = WindowsInstanceControl()
    for old in stop_stale(directory, control, own_version=guard.version, own_pid=guard.pid):
        print(
            f"darkroom_ai: stopped an older connector (pid {old.pid}, code {old.version}, "
            f"started {old.started})",
            file=sys.stderr,
            flush=True,
        )
    created = control.create_time(os.getpid())
    if created is not None:
        record = InstanceRecord(guard.pid, created, guard.started_at, guard.version)
        atexit.register(unregister, register(directory, record))
