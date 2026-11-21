"""Confirmations through MCP elicitation, and pictures returned as MCP image content."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[2]
MASTER = "master-1"
GATED: dict[str, Any] = {"photo_id": MASTER, "exposure": 0.3, "allow_beyond_total": True}


def _run(tmp_path: Path, answer: str | None) -> dict[str, Any]:
    """Call a gated edit and get_preview; `answer` is the elicitation action (None: unsupported)."""
    server = StdioServerParameters(
        command=sys.executable, args=[str(REPO / "tests" / "mcp_sim_server.py"), str(tmp_path)]
    )
    asked: list[str] = []

    async def elicit(_ctx: Any, params: Any) -> types.ElicitResult:
        asked.append(params.message)
        content = {"proceed": True} if answer == "accept" else None
        return types.ElicitResult(action=answer or "cancel", content=content)

    async def session_run() -> dict[str, Any]:
        callback = elicit if answer is not None else None
        async with (
            stdio_client(server) as (read, write),
            ClientSession(read, write, elicitation_callback=callback) as session,
        ):
            await session.initialize()
            gated = await session.call_tool("adjust_lighting", GATED)
            preview = await session.call_tool("get_preview", {"photo_id": MASTER})
            return {"gated": gated, "preview": preview, "asked": asked}

    async def bounded() -> dict[str, Any]:
        with anyio.fail_after(60):
            return await session_run()

    return anyio.run(bounded)


def test_accepted_confirmation_lets_the_edit_through(tmp_path: Path) -> None:
    got = _run(tmp_path, "accept")
    assert len(got["asked"]) == 1 and "go-ahead" in got["asked"][0]
    assert got["gated"].is_error is not True


def test_declined_or_cancelled_confirmation_changes_nothing(tmp_path: Path) -> None:
    for answer in ("decline", "cancel"):
        got = _run(tmp_path, answer)
        assert len(got["asked"]) == 1
        assert got["gated"].is_error is True
        assert got["gated"].content[0].text.startswith("[CONFIRMATION_REQUIRED]")


def test_client_that_cannot_ask_is_refused(tmp_path: Path) -> None:
    got = _run(tmp_path, None)
    assert got["gated"].is_error is True
    assert got["gated"].content[0].text.startswith("[CONFIRMATION_REQUIRED]")


def test_get_preview_returns_the_picture(tmp_path: Path) -> None:
    got = _run(tmp_path, "accept")
    preview = got["preview"]
    assert preview.is_error is not True
    images = [block for block in preview.content if block.type == "image"]
    assert len(images) == 1 and images[0].mime_type == "image/png" and images[0].data
