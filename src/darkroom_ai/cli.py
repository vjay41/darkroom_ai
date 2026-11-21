"""Command line entry point (`darkroom`).

darkroom doctor                 check the local setup (config, Lightroom, Photoshop, GPUs)
darkroom selftest [--write]     check every tool group against the installed Adobe apps
darkroom soak [--cycles N] --yes  repeat the real tools on the test catalog, count failures
darkroom connectors             list running MCP connectors; --stop-stale stops ones with older code
darkroom mcp                    serve the tools over MCP (stdio)
darkroom revert [--photo PATH]  put a photo's copy back to its original values; asks first
darkroom clean-outputs          remove intermediate files (never final results); asks first
darkroom lightroom setup|ping   Lightroom plug-in setup and check
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai import __version__
from darkroom_ai.adapters.lightroom import plugin_dir
from darkroom_ai.adapters.lightroom.bridge_file import (
    bridge_path,
    load_bridge_file,
    write_bridge_file,
)
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.adapters.photoshop.windows import photoshop_windows
from darkroom_ai.cleanup import apply_cleanup, plan_cleanup
from darkroom_ai.compat import assess_version
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.gpu import conflict_warnings, placement, read_gpus
from darkroom_ai.runtime import (
    WindowsInstanceControl,
    code_hash,
    list_instances,
    stop_stale,
)
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.revert import apply_revert, plan_revert

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_ERROR = 2


class AskUser:
    """Confirmer for `darkroom chat`: asks the person at the keyboard, y or n."""

    def __init__(self, lead: str = "The assistant wants to") -> None:
        self._lead = lead

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        print(f"\n{self._lead}: {action}")
        for key, value in details.items():
            print(f"  {key}: {value}")
        try:
            answer = input("Allow this? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")


def _line(status: str, text: str) -> None:
    print(f"[{status:^4}] {text}")


# -- connectors -----------------------------------------------------------------------------------


def _source_dir() -> Path:
    return Path(__file__).resolve().parent


def _connectors(settings: Settings, stop: bool) -> int:
    directory = settings.paths.logs_dir.resolve() / "connectors"
    control = WindowsInstanceControl()
    disk = code_hash(_source_dir())
    records = list_instances(directory, control)
    if not records:
        print(
            "No registered connector is running. (A connector started before this check existed "
            "is not listed: stop it in Task Manager or restart Hermes.)"
        )
    for record in records:
        status = "current" if record.version == disk else "STALE (older code)"
        print(f"pid {record.pid}  started {record.started}  code {record.version}  {status}")
    if stop:
        for old in stop_stale(directory, control, own_version=disk, own_pid=os.getpid()):
            print(f"stopped pid {old.pid} (older code {old.version})")
    return EXIT_OK


# -- doctor ---------------------------------------------------------------------------------------


def _doctor(settings: Settings) -> int:
    problems = 0
    _line("ok", "config: loaded")
    disk = code_hash(_source_dir())
    running = list_instances(
        settings.paths.logs_dir.resolve() / "connectors", WindowsInstanceControl()
    )
    old = [r for r in running if r.version != disk]
    if old:
        pids = ", ".join(str(r.pid) for r in old)
        _line(
            "warn",
            f"{len(old)} MCP connector(s) run older code (pid {pids}): run "
            "`darkroom connectors --stop-stale`, then restart the connector in Hermes.",
        )
    elif running:
        _line("ok", f"{len(running)} MCP connector(s) running the current code")

    try:
        with LightroomClient.from_settings(settings, load_bridge_file()) as lightroom:
            info = lightroom.ping()
        version = f"Lightroom {info.lightroom_version}, plug-in {info.plugin_version}"
        if info.writes_allowed:
            _line("ok", f"{version}, writes allowed")
        else:
            problems += 1
            _line(
                "FAIL",
                f"{version}: [LR_WRONG_CATALOG] edits are refused because the open catalog is "
                f"not the configured one.\n       open:       {info.catalog_path}\n"
                f"       configured: {info.configured_catalog}\n"
                "       Open the configured catalog in Lightroom, or change lightroom.test_catalog "
                "and run `darkroom lightroom setup` again.",
            )
        verdict = assess_version("lightroom", info.lightroom_version)
        if verdict.status != "tested":
            # A warning only: an untested version is not refused.
            _line("warn", verdict.note)
    except DarkroomError as exc:
        problems += 1
        _line("FAIL", f"Lightroom: [{exc.code.value}] {exc.message}\n       {exc.next_step}")

    windows = photoshop_windows()
    if windows:
        titles = ", ".join(repr(w.title) for w in windows if w.title)
        _line("ok", f"Photoshop is running (windows: {titles})")
    else:
        note = "it is started when needed" if settings.photoshop.auto_start else "start it first"
        _line("warn", f"Photoshop is not running ({note})")

    report = read_gpus()
    for gpu in report.gpus:
        _line("ok", f"GPU {gpu.index} {gpu.name}: {gpu.used_mb} of {gpu.total_mb} MB used")
    if not report.error:
        _line("ok", placement(report))
    for warning in conflict_warnings(report, min_free_mb=settings.gpu.min_free_mb):
        _line("warn", warning)

    print("\nAll checks passed." if not problems else f"\n{problems} problem(s) found.")
    return EXIT_OK if not problems else EXIT_PROBLEM


# -- revert ---------------------------------------------------------------------------------------


def _revert(settings: Settings, photo_path: Path | None) -> int:
    """Human-only: back to the values from before any darkroom_ai edit. Never offered to a model."""
    context = build_tool_context(settings)
    if photo_path:
        photo = context.lightroom.find_photo(str(photo_path))
        if photo is None:
            print(f"{photo_path} is not in the open Lightroom catalog.", file=sys.stderr)
            return EXIT_ERROR
    else:
        selection = context.lightroom.get_selection()
        if selection.active is None:
            print("Select one photo in Lightroom, or pass --photo PATH.", file=sys.stderr)
            return EXIT_ERROR
        photo = selection.active
    plan = plan_revert(context, photo.id)
    if not plan.entries:
        print(f"{photo.file_name}: no darkroom_ai edits to take back.")
        return EXIT_OK
    print(
        f"{photo.file_name}: {len(plan.entries)} darkroom_ai edit(s) from {plan.sessions} "
        "session(s) would be taken back on its virtual copy. Settings changed by hand in "
        "Lightroom are not touched; the original file is never changed."
    )
    agreed = AskUser(lead="This will").confirm(
        "put these settings back to their values from before darkroom_ai's first edit",
        plan.settings,
    )
    if not agreed:
        print("Nothing was changed.")
        return EXIT_OK
    apply_revert(context, plan)
    print(f"Reverted {len(plan.settings)} setting(s).")
    return EXIT_OK


# -- clean-outputs --------------------------------------------------------------------------------


def _clean_outputs(settings: Settings) -> int:
    """Human-only: remove intermediate files after showing the list and asking."""
    plan = plan_cleanup(settings)
    print(
        f"{len(plan.finals)} final result(s) are kept (PSD/JPG ending in _darkroom); "
        f"{len(plan.unknown)} unrecognised file(s) are kept."
    )
    if not plan.files:
        print("Nothing to remove.")
        return EXIT_OK
    print(f"{len(plan.files)} file(s), {plan.megabytes:.0f} MB, would be removed:")
    for category, paths in plan.remove.items():
        size = sum(path.stat().st_size for path in paths) / 1_000_000
        print(f"  {category}: {len(paths)} file(s), {size:.0f} MB, in {paths[0].parent}")
        for path in paths:
            print(f"      {path.name}")
    details = {category: f"{len(paths)} file(s)" for category, paths in plan.remove.items()}
    if not AskUser(lead="This will").confirm("remove these intermediate files for good", details):
        print("Nothing was removed.")
        return EXIT_OK
    print(f"Removed {apply_cleanup(plan)} file(s).")
    return EXIT_OK


# -- lightroom ------------------------------------------------------------------------------------


def _lightroom_setup(settings: Settings, rotate_token: bool) -> int:
    bridge = write_bridge_file(settings, rotate_token=rotate_token)
    print(f"bridge file written: {bridge_path()}")
    print(f"  catalog the plug-in may write to: {bridge.catalog_path}")
    print(f"  preview folder: {bridge.preview_dir}")
    print(f"  localhost ports: {bridge.request_port} (requests), {bridge.response_port} (replies)")
    print("In Lightroom: File > Plug-in Manager > Add, and choose this folder:")
    print(f"  {plugin_dir()}")
    print("If the plug-in is already added, press 'Reload Plug-in' there.")
    return EXIT_OK


def _lightroom_ping(settings: Settings) -> int:
    with LightroomClient.from_settings(settings, load_bridge_file()) as client:
        info = client.ping()
    print(f"Lightroom {info.lightroom_version}, plug-in {info.plugin_version}")
    print(f"  open catalog: {info.catalog_path}")
    print(f"  configured catalog: {info.configured_catalog}")
    print(f"  writes allowed: {'yes' if info.writes_allowed else 'NO (different catalog open)'}")
    return EXIT_OK


def _selftest(settings: Settings, write: bool, photo_id: str | None) -> int:
    from darkroom_ai.adapters.photoshop.client import PhotoshopClient
    from darkroom_ai.selftest import format_report, run_selftest

    # Photoshop is only asked when it is already running: the selftest never starts it.
    photoshop = PhotoshopClient.from_settings(settings) if photoshop_windows() else None
    with LightroomClient.from_settings(settings, load_bridge_file()) as lightroom:
        report = run_selftest(
            lightroom,
            photoshop,
            settings.guardrails,
            write=write,
            photo_id=photo_id,
        )
    print(format_report(report))
    out_dir = settings.paths.outputs_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"selftest-{report.started[:10]}.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"Report saved: {out}")
    return EXIT_PROBLEM if report.failed else EXIT_OK


def _soak(settings: Settings, cycles: int, photo_id: str | None, yes: bool) -> int:
    from darkroom_ai.soak import format_soak, run_soak
    from darkroom_ai.tools.factory import build_tool_registry

    with LightroomClient.from_settings(settings, load_bridge_file()) as lightroom:
        info = lightroom.ping()
    print(f"Soak plan: {cycles} cycle(s) on the open catalog {info.catalog_path}")
    print("  each cycle: list, preview, exposure edit + undo, gradient mask create + delete")
    print("  writes happen on ONE working copy (a virtual copy); originals are never changed")
    if not info.writes_allowed:
        print(
            "  writes are NOT allowed for this catalog (not the configured test catalog): stopping"
        )
        return EXIT_PROBLEM
    if not yes:
        print("Run again with --yes to start.")
        return EXIT_PROBLEM
    registry = build_tool_registry(settings)
    report = run_soak(registry.call, cycles=cycles, photo_id=photo_id)
    print(format_soak(report))
    out_dir = settings.paths.outputs_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"soak-{report.started[:10]}.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"Report saved: {out}")
    return EXIT_PROBLEM if report.failed else EXIT_OK


# -- parser ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkroom",
        description="Local, offline MCP tool: lets an agent edit photos in Lightroom Classic.",
    )
    parser.add_argument("--version", action="version", version=f"darkroom {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to darkroom.toml")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check the local setup")

    commands.add_parser("mcp", help="serve the tools over MCP (stdio)")
    connectors = commands.add_parser(
        "connectors", help="list running MCP connectors and whether they run the current code"
    )
    connectors.add_argument(
        "--stop-stale", action="store_true", help="stop connectors that run older code"
    )

    selftest = commands.add_parser(
        "selftest",
        help="check each tool group against the installed Adobe apps (run after updates)",
    )
    selftest.add_argument(
        "--write", action="store_true", help="also test an edit on a virtual copy (test catalog)"
    )
    selftest.add_argument("--photo-id", default=None, help="photo to test with (default: first)")

    soak = commands.add_parser(
        "soak", help="repeat the real tools on the test catalog and count failures (P6)"
    )
    soak.add_argument("--cycles", type=int, default=10, help="how many rounds (default 10)")
    soak.add_argument("--photo-id", default=None, help="master photo to use (default: first)")
    soak.add_argument(
        "--yes", action="store_true", help="start (without it only the plan is shown)"
    )

    revert = commands.add_parser(
        "revert", help="put a photo's copy back to its original values (asks first)"
    )
    revert.add_argument("--photo", type=Path, default=None, help="file path of the photo")
    commands.add_parser(
        "clean-outputs", help="remove intermediate files, never final results (asks first)"
    )

    lightroom = commands.add_parser("lightroom", help="Lightroom plug-in setup and checks")
    lightroom_commands = lightroom.add_subparsers(dest="lightroom_command", required=True)
    setup = lightroom_commands.add_parser("setup", help="write the plug-in's bridge.json")
    setup.add_argument("--rotate-token", action="store_true", help="generate a new token")
    lightroom_commands.add_parser("ping", help="check that the plug-in answers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    try:
        settings = load_config(args.config)
        if args.command == "doctor":
            return _doctor(settings)
        if args.command == "selftest":
            return _selftest(settings, args.write, args.photo_id)
        if args.command == "soak":
            return _soak(settings, args.cycles, args.photo_id, args.yes)
        if args.command == "connectors":
            return _connectors(settings, args.stop_stale)
        if args.command == "revert":
            return _revert(settings, args.photo)
        if args.command == "clean-outputs":
            return _clean_outputs(settings)
        if args.command == "mcp":
            from darkroom_ai import mcp_server

            mcp_server.run(settings)
            return EXIT_OK
        if args.lightroom_command == "setup":
            return _lightroom_setup(settings, args.rotate_token)
        return _lightroom_ping(settings)
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}", file=sys.stderr)
        print(f"Next step: {exc.next_step}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
