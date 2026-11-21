"""Command line entry point (`darkroom`).

darkroom doctor                 check the local setup (config, Lightroom, Photoshop, model, GPUs)
darkroom models test [...]      test model profiles (vision, tool calling, prompt injection)
darkroom chat "..." [...]       let the local model edit one photo; asks before anything gated
darkroom mcp                    serve the tools over MCP (stdio)
darkroom revert [--photo PATH]  put a photo's copy back to its original values; asks first
darkroom clean-outputs          remove intermediate files (never final results); asks first
darkroom lightroom setup|ping   Lightroom plug-in setup and check
"""

from __future__ import annotations

import argparse
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
from darkroom_ai.agent.loop import PLAYBOOKS, Agent
from darkroom_ai.cleanup import apply_cleanup, plan_cleanup
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.gpu import conflict_warnings, placement, read_gpus
from darkroom_ai.llm import selftest
from darkroom_ai.llm.client import LocalLlmClient
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry
from darkroom_ai.tools.revert import apply_revert, plan_revert

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_ERROR = 2
CHAT_TOOLS = ("adjust_lighting", "adjust_white_balance", "adjust_hsl", "crop", "undo")


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


# -- doctor ---------------------------------------------------------------------------------------


def _doctor(settings: Settings) -> int:
    problems = 0
    profile = settings.llm.active
    _line("ok", f"config: profile '{settings.llm.active_profile}' at {profile.base_url}")

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

    llm = LocalLlmClient(profile)
    try:
        models = llm.models()
        state = llm.model_state() if profile.model in models else "missing"
        if state == "loaded":
            _line("ok", "model server answers; model of the active profile is loaded")
        elif state == "unknown":
            _line(
                "warn",
                "model server answers and lists the model, but does not report whether it is "
                "loaded",
            )
        elif state != "missing":
            problems += 1
            _line(
                "FAIL",
                f"model of the active profile is listed but NOT loaded (state: {state}). Load "
                "it in the local model server; darkroom_ai does not load models by itself.",
            )
        else:
            problems += 1
            _line("FAIL", "model server answers, but the active profile's model is not listed")
    except DarkroomError as exc:
        problems += 1
        _line("FAIL", f"model server: [{exc.code.value}] {exc.message}\n       {exc.next_step}")
    finally:
        llm.close()

    report = read_gpus()
    for gpu in report.gpus:
        _line("ok", f"GPU {gpu.index} {gpu.name}: {gpu.used_mb} of {gpu.total_mb} MB used")
    if not report.error:
        _line("ok", placement(report))
    for warning in conflict_warnings(report, min_free_mb=settings.gpu.min_free_mb):
        _line("warn", warning)

    print("\nAll checks passed." if not problems else f"\n{problems} problem(s) found.")
    return EXIT_OK if not problems else EXIT_PROBLEM


# -- models test ----------------------------------------------------------------------------------


def _models_test(settings: Settings, names: list[str], skip_vision: bool, as_json: bool) -> int:
    wanted = names or [settings.llm.active_profile]
    failed = 0
    for name in wanted:
        if name not in settings.llm.profiles:
            print(f"There is no profile named '{name}'.", file=sys.stderr)
            return EXIT_ERROR
        profile = settings.llm.profiles[name]
        llm = LocalLlmClient(profile, require_loaded=settings.llm.require_loaded)
        print(f"== profile '{name}': {profile.model} (tool mode {profile.tool_mode}) ==")
        try:
            vision = None
            if profile.vision and not skip_vision:
                vision = selftest.vision_test(llm, profile)
                state = "PASS" if vision.passed else "FAIL"
                print(
                    f"  vision [{state}]: {vision.correct}/{vision.total} correct, "
                    f"{vision.seconds:.1f} s, sent {vision.image} (synthetic picture)"
                )
                failed += 0 if vision.passed else 1
            # With thinking switched off for tool turns, compare against the model's default.
            thinking_off = profile.reasoning_effort_tools is not None
            baseline = None
            if thinking_off:
                default_profile = profile.model_copy(
                    update={"reasoning_effort_tools": None, "reasoning_effort_planning": None}
                )
                baseline = selftest.tool_calling_test(llm, default_profile, settings)
            scenarios = selftest.tool_calling_test(
                llm, profile, settings, planning=not thinking_off
            )
            for item in scenarios:
                state = "PASS" if item.passed else "FAIL"
                print(
                    f"  tools  [{state}]: {item.name}: {item.valid_calls}/{item.calls} valid "
                    f"calls, {item.turns} turns, {item.seconds:.1f} s, exposure "
                    f"{item.final_exposure:+g} ({item.stopped_because}) {item.note}"
                )
                failed += 0 if item.passed else 1
            if baseline is not None:
                rate_on = selftest.valid_call_rate(baseline)
                rate_off = selftest.valid_call_rate(scenarios)
                state = "FAIL" if rate_off < rate_on else "PASS"
                print(
                    f"  think  [{state}]: valid-call rate {rate_on:.0%} with the model's default "
                    f"thinking ({sum(s.passed for s in baseline)}/{len(baseline)} scenarios, "
                    f"{sum(s.seconds for s in baseline):.1f} s) vs {rate_off:.0%} with "
                    f"reasoning_effort={profile.reasoning_effort_tools!r} on every turn "
                    f"({sum(s.passed for s in scenarios)}/{len(scenarios)} scenarios, "
                    f"{sum(s.seconds for s in scenarios):.1f} s); the lines above are the latter"
                )
                failed += 1 if rate_off < rate_on else 0
            injections = selftest.injection_test(llm, profile, settings, planning=not thinking_off)
            for hit in injections:
                state = "PASS" if hit.guardrails_held else "FAIL"
                followed = "model FOLLOWED it" if hit.followed else "model ignored it"
                print(
                    f"  inject [{state}]: text in {hit.where}: {followed}; guardrails "
                    f"{'held' if hit.guardrails_held else 'DID NOT HOLD'}; exposure "
                    f"{hit.final_exposure:+g}; {'; '.join(hit.attempts) or 'no attempts'}"
                )
                failed += 0 if hit.guardrails_held else 1
            if as_json:
                print(selftest.summary_json(name, profile, vision, scenarios, injections))
        except DarkroomError as exc:
            failed += 1
            print(f"  [{exc.code.value}] {exc.message}\n  Next step: {exc.next_step}")
        finally:
            llm.close()
    return EXIT_OK if not failed else EXIT_PROBLEM


# -- chat -----------------------------------------------------------------------------------------


def _chat(settings: Settings, args: argparse.Namespace) -> int:
    profile = settings.llm.profiles[args.profile or settings.llm.active_profile]
    context = build_tool_context(settings, confirmer=AskUser())
    registry = ToolRegistry(context)
    if args.photo:
        photo = context.lightroom.find_photo(str(args.photo))
        if photo is None:
            print(f"{args.photo} is not in the open Lightroom catalog.", file=sys.stderr)
            return EXIT_ERROR
    else:
        selection = context.lightroom.get_selection()
        if selection.active is None:
            print("Select one photo in Lightroom, or pass --photo PATH.", file=sys.stderr)
            return EXIT_ERROR
        photo = selection.active
    print(f"photo: {photo.file_name} | model: {profile.model} | playbook: {args.playbook}")
    llm = LocalLlmClient(profile, require_loaded=settings.llm.require_loaded)
    try:
        agent = Agent(
            llm,
            registry,
            profile,
            max_passes=settings.guardrails.max_passes,
            allowed_tools=CHAT_TOOLS,
            playbook=args.playbook,
            on_event=lambda text: print(f"  {text}"),
        )
        report = agent.run(args.request, photo.id)
    finally:
        llm.close()
    print(f"\n{report.final_message}")
    print(
        f"({report.passes} pass(es), {report.turns} turns, {report.seconds:.0f} s, "
        f"{report.images_sent} preview(s) sent to the local model; `darkroom chat` with "
        f"'undo' or the undo tool takes the edits back)"
    )
    return EXIT_OK


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


# -- parser ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkroom",
        description="Local, offline AI photo-editing agent for Lightroom Classic and Photoshop.",
    )
    parser.add_argument("--version", action="version", version=f"darkroom {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to darkroom.toml")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check the local setup")

    models = commands.add_parser("models", help="model profile checks")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    test = model_commands.add_parser("test", help="vision, tool-calling and injection tests")
    test.add_argument("profiles", nargs="*", help="profile names (default: the active profile)")
    test.add_argument("--skip-vision", action="store_true", help="do not send the test picture")
    test.add_argument("--json", action="store_true", help="also print the results as JSON")

    chat = commands.add_parser("chat", help="let the local model edit one photo")
    chat.add_argument("request", help='what to do, e.g. "brighten the faces a little"')
    chat.add_argument("--photo", type=Path, default=None, help="file path of the photo")
    chat.add_argument("--playbook", choices=PLAYBOOKS, default="portrait")
    chat.add_argument("--profile", default=None, help="model profile (default: the active one)")

    commands.add_parser("mcp", help="serve the tools over MCP (stdio)")

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
        if args.command == "models":
            return _models_test(settings, args.profiles, args.skip_vision, args.json)
        if args.command == "chat":
            if args.profile and args.profile not in settings.llm.profiles:
                print(f"There is no profile named '{args.profile}'.", file=sys.stderr)
                return EXIT_ERROR
            return _chat(settings, args)
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
