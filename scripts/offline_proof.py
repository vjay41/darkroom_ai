"""Offline proof: record every address this process connects to, resolves or binds.

A Python audit hook sees each socket connect / sendto / bind / name lookup made by ANY code in
this process (ours and third-party). The script then exercises the program and prints every
address seen. Pass = nothing but loopback.

Limits: child processes (the Photoshop worker, an MCP server child) are not seen by the hook;
for those, use the firewall block printed by scripts/firewall_rules.ps1 and run this again.

Usage:  poetry run python scripts/offline_proof.py [--with-adobe --photo PATH]
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SEEN: Counter[tuple[str, str]] = Counter()
EVENTS = {
    "socket.connect": 1,
    "socket.sendto": 1,
    "socket.bind": 1,
    "socket.getaddrinfo": 0,
    "socket.gethostbyname": 0,
    "socket.gethostbyaddr": 0,
}


def _hook(event: str, args: tuple[Any, ...]) -> None:
    index = EVENTS.get(event)
    if index is None:
        return
    target = args[index] if len(args) > index else None
    host = target[0] if isinstance(target, tuple) and target else target
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    SEEN[(event, str(host))] += 1


sys.addaudithook(_hook)

# Imported only after the hook is in place, so import-time network use would be seen too.
from darkroom_ai.config import load_config  # noqa: E402
from darkroom_ai.net.loopback import is_loopback_host  # noqa: E402

THIRD_PARTY = ("opentelemetry", "uvicorn", "starlette", "truststore", "mcp", "httpx2", "cv2")


def step(name: str) -> None:
    print(f"-- {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--with-adobe", action="store_true", help="also contact Lightroom")
    parser.add_argument("--photo", type=Path, default=None, help="test photo for the read tools")
    args = parser.parse_args()
    settings = load_config(None)

    step("import third-party packages: " + ", ".join(THIRD_PARTY))
    for name in THIRD_PARTY:
        importlib.import_module(name)
    import mcp.server.lowlevel
    import mcp.server.stdio  # noqa: F401

    from darkroom_ai import cli, mcp_server

    step("MCP server object with simulated tools (in this process, no client)")
    import tempfile

    from darkroom_ai.adapters.lightroom.simulated import SimulatedLightroom
    from darkroom_ai.safety.journal import MemoryJournal
    from darkroom_ai.tools.context import ToolContext
    from darkroom_ai.tools.registry import ToolRegistry

    with tempfile.TemporaryDirectory(prefix="darkroom_proof_") as folder:
        registry = ToolRegistry(
            ToolContext(
                settings=settings,
                lightroom=SimulatedLightroom(Path(folder)),
                journal=MemoryJournal(),
            )
        )
        mcp_server.build_server(registry)
        print("   tools:", len(mcp_server.list_tools(registry)))
        call = mcp_server.call_tool(registry, "get_photo_info", {"photo_id": "master-1"})
        print("   get_photo_info error:", call.is_error)

    if args.with_adobe:
        step("darkroom doctor (Lightroom ping, Photoshop window check, nvidia-smi)")
        cli.main(["doctor"])
        if args.photo is not None:
            step("Lightroom read tools on the test photo (get_photo_info, analyze_photo)")
            from darkroom_ai.tools.factory import build_tool_context

            context = build_tool_context(settings)
            photo = context.lightroom.find_photo(str(args.photo))
            if photo is not None:
                tools = ToolRegistry(context)
                tools.call("get_photo_info", {"photo_id": photo.id})
                tools.call("analyze_photo", {"photo_id": photo.id})
                print("   done")
    else:
        print("-- SKIPPED: Lightroom / doctor (run with --with-adobe)")

    print("\n== ADDRESSES SEEN BY THE AUDIT HOOK ==")
    bad = []
    for (event, host), count in sorted(SEEN.items()):
        local = is_loopback_host(host)
        print(f"  {event:22} {host:28} x{count:<4} {'loopback' if local else 'NOT LOOPBACK'}")
        if not local:
            bad.append((event, host))
    if not SEEN:
        print("  (none)")
    print("\nRESULT:", "PASS, only loopback addresses" if not bad else f"FAIL: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
