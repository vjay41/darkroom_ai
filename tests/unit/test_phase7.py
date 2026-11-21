"""Phase 7: clean-outputs, a real MCP stdio client, the firewall script, result notes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from darkroom_ai import cli
from darkroom_ai.cleanup import apply_cleanup, plan_cleanup
from darkroom_ai.config import Settings, parse_settings
from darkroom_ai.tools.background import OUTPUT_NOTE, ReplaceBackgroundResult

REPO = Path(__file__).resolve().parents[2]
MASTER = "master-1"


def settings_in(folder: Path) -> Settings:
    return parse_settings(
        {
            "llm": {
                "active_profile": "m",
                "profiles": {"m": {"base_url": "http://127.0.0.1:1234/v1", "model": "x"}},
            },
            "paths": {
                "outputs_dir": str(folder / "outputs"),
                "previews_dir": str(folder / "previews"),
                "logs_dir": str(folder / "logs"),
            },
        }
    )


def make_outputs(folder: Path) -> dict[str, Path]:
    names = {
        "preview": "previews/analyze-1.jpg",
        "tiff": "outputs/handoff/background-1.tif",
        "mask": "outputs/masks/TEST_subject.png",
        "copy": "outputs/photoshop/background-1_copy.tif",
        "copy_mask": "outputs/photoshop/background-1_copy_mask.png",
        "reference": "outputs/photoshop/handoff-1_copy_reference.jpg",
        "psd": "outputs/photoshop/background-1_copy_darkroom.psd",
        "jpg": "outputs/photoshop/background-1_copy_darkroom.jpg",
        "psd2": "outputs/photoshop/DSC_1_copy_darkroom-2.psd",
        "unknown": "outputs/photoshop/notes.txt",
        "journal": "logs/journal.jsonl",
    }
    paths = {}
    for key, name in names.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        paths[key] = path
    return paths


def test_cleanup_plan_keeps_finals_and_unknown_files(tmp_path: Path) -> None:
    paths = make_outputs(tmp_path)
    plan = plan_cleanup(settings_in(tmp_path))
    assert {p.name for p in plan.finals} == {
        "background-1_copy_darkroom.psd",
        "background-1_copy_darkroom.jpg",
        "DSC_1_copy_darkroom-2.psd",
    }
    assert [p.name for p in plan.unknown] == ["notes.txt"]
    assert {p.name for p in plan.files} == {
        "analyze-1.jpg",
        "background-1.tif",
        "TEST_subject.png",
        "background-1_copy.tif",
        "background-1_copy_mask.png",
        "handoff-1_copy_reference.jpg",
    }
    assert all(path.exists() for path in paths.values())  # planning removes nothing

    assert apply_cleanup(plan) == 6
    for key in ("psd", "jpg", "psd2", "unknown", "journal"):
        assert paths[key].exists()
    for key in ("preview", "tiff", "mask", "copy", "copy_mask", "reference"):
        assert not paths[key].exists()
    assert plan_cleanup(settings_in(tmp_path)).files == []


def test_clean_outputs_command_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_outputs(tmp_path)
    settings = settings_in(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli._clean_outputs(settings) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "6 file(s)" in out and "3 final result(s) are kept" in out
    assert "Nothing was removed." in out
    assert paths["tiff"].exists()

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert cli._clean_outputs(settings) == cli.EXIT_OK
    assert "Removed 6 file(s)." in capsys.readouterr().out
    assert not paths["tiff"].exists() and paths["psd"].exists()
    assert cli._clean_outputs(settings) == cli.EXIT_OK
    assert "Nothing to remove." in capsys.readouterr().out


def test_replace_background_result_says_what_the_output_contains() -> None:
    assert "current edits and crop" in OUTPUT_NOTE
    assert ReplaceBackgroundResult.model_fields["note"].default == OUTPUT_NOTE


def test_firewall_script_only_prints() -> None:
    text = (REPO / "scripts" / "firewall_rules.ps1").read_text(encoding="utf-8")
    code = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
    # Firewall cmdlets appear only inside strings that are written to the screen.
    for line in code:
        if "NetFirewallRule" in line:
            assert line.startswith("Write-Output"), line
    assert not any(line.startswith(("Invoke-Expression", "iex ", "netsh")) for line in code)


def test_mcp_over_real_stdio(tmp_path: Path) -> None:
    """A real MCP client talks to the server process over stdin/stdout (simulated Lightroom)."""
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO / "tests" / "mcp_sim_server.py"), str(tmp_path)],
    )

    async def session_run() -> dict[str, Any]:
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            started = await session.initialize()
            tools = await session.list_tools()
            info = await session.call_tool("get_photo_info", {"photo_id": MASTER})
            edit = await session.call_tool("adjust_lighting", {"photo_id": MASTER, "exposure": 0.3})
            gated = await session.call_tool(
                "adjust_lighting",
                {"photo_id": MASTER, "exposure": 0.3, "allow_beyond_total": True},
            )
            unknown = await session.call_tool("revert", {"photo_id": MASTER})
            return {
                "server": started.server_info.name,
                "tools": [tool.name for tool in tools.tools],
                "info": info,
                "edit": edit,
                "gated": gated,
                "unknown": unknown,
            }

    async def bounded() -> dict[str, Any]:
        with anyio.fail_after(50):
            return await session_run()

    got = anyio.run(bounded)
    assert got["server"] == "darkroom_ai"
    assert "adjust_lighting" in got["tools"] and "undo" in got["tools"]
    assert "revert" not in got["tools"] and "run_photoshop_script" not in got["tools"]
    assert got["info"].is_error is not True
    assert got["edit"].is_error is not True
    assert got["edit"].structured_content["clamped"] is False
    assert got["gated"].is_error is True
    assert got["gated"].content[0].text.startswith("[CONFIRMATION_REQUIRED]")
    assert got["unknown"].is_error is True
