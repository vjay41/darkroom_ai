"""End-to-end agent run on a virtual copy of a test photo, then undo and verify.

The local model sees the measurements and a downscaled preview (loopback only). Prints tool
calls, numbers, paths and timings; the model's free-text summary goes to a file under logs/ and
only its length is printed. No image is displayed.

Run and undo at once:
    poetry run python scripts/agent_run.py --photo PATH
Run, then stop so the result can be looked at (state is saved under logs/):
    poetry run python scripts/agent_run.py --photo PATH --pause [--request "..."] [--tools a,b]
Later, undo that run's edits and compare every setting with the recorded start state:
    poetry run python scripts/agent_run.py --undo-state logs/agent_run_state.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.agent.loop import PLAYBOOKS, Agent
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.llm.client import LocalLlmClient
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.lightroom_tools import pending_edits
from darkroom_ai.tools.registry import ToolRegistry

REQUEST = "Balance the lighting and correct the white balance if the measurements call for it."
TOOLS = "adjust_lighting,adjust_white_balance"
STATE_NAME = "agent_run_state.json"
failures: list[str] = []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def differences(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    changed = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if isinstance(a, int | float) and isinstance(b, int | float):
            same = abs(a - b) <= 1e-6
        else:
            same = a == b
        if not same:
            changed.append(f"{key}: {a!r} -> {b!r}")
    return changed


def numbers(analysis: Mapping[str, Any]) -> str:
    tones = {k: v for k, v in analysis["tones"].items() if k != "histogram"}
    return json.dumps(
        {"tones": tones, "clipping": analysis["clipping"], "color_cast": analysis["color_cast"]}
    )


class RecordingRegistry(ToolRegistry):
    """Prints every tool call with its numeric result as it happens."""

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = super().call(name, arguments)
        except DarkroomError as exc:
            print(f"    TOOL {name} {json.dumps(dict(arguments))} -> REFUSED [{exc.code.value}]")
            print(f"         {exc.message}")
            raise
        if name == "analyze_photo":
            print(f"    MEASURED: {numbers(result['analysis'])}")
        elif name != "get_photo_info":
            print(f"    TOOL {name} requested {json.dumps(dict(arguments))}")
            print(f"         took {time.monotonic() - started:.1f} s")
            print(f"         result {json.dumps(result, default=str)}")
        return result


def undo_and_check(context: ToolContext, state: Mapping[str, Any]) -> None:
    """Take back this session's edits and compare everything with the recorded start state."""
    lightroom, journal = context.lightroom, context.journal
    copy_id, photo_id = state["copy_id"], state["photo_id"]
    photo_path = Path(state["photo_path"])
    print("== UNDO AND CHECKS ==")
    mine = pending_edits(context, copy_id, session=journal.session)
    others = [e for e in pending_edits(context, copy_id, session=None) if e not in mine]
    print(f"  pending from this run's session: {len(mine)}; from other sessions: {len(others)}")
    try:
        undone = ToolRegistry(context).call("undo", {"photo_id": photo_id, "scope": "all"})
        print(f"  undo all: {undone['undone_steps']} step(s) taken back")
        check("undo took back exactly this run's edits", undone["undone_steps"] == len(mine))
    except DarkroomError as exc:
        print(f"UNDO ERROR [{exc.code.value}] {exc.message}", file=sys.stderr)
        failures.append("undo")
    start, master_start = state["start"], state["master_start"]
    left = differences(start, dict(lightroom.get_develop_settings(copy_id)))
    check(f"all {len(start)} settings of the copy equal the recorded start state", not left)
    if left:
        print("   differences:", left)
    master_left = differences(master_start, dict(lightroom.get_develop_settings(photo_id)))
    check("master's settings never changed", not master_left)
    check("original file SHA-256 unchanged", sha256(photo_path) == state["sha256"])
    check(
        "nothing from this session left pending",
        not pending_edits(context, copy_id, session=journal.session),
    )


def run(settings: Settings, args: argparse.Namespace) -> int:
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    tools = tuple(name.strip() for name in args.tools.split(",") if name.strip())
    profile = settings.llm.active
    if settings.llm.require_loaded:  # before anything touches Lightroom
        probe = LocalLlmClient(profile)
        try:
            probe.ensure_loaded()
        except DarkroomError as exc:
            raise SystemExit(
                f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}"
            ) from exc
        finally:
            probe.close()
    context = build_tool_context(settings)  # default confirmer: refuses anything gated
    lightroom, journal = context.lightroom, context.journal
    registry = RecordingRegistry(context)

    photo = lightroom.find_photo(str(args.photo))
    if photo is None:
        raise SystemExit(f"{args.photo} is not in the open catalog.")
    info = ToolRegistry(context).call("get_photo_info", {"photo_id": photo.id})
    copy_id = info["working_copy_id"]
    if not copy_id:
        raise SystemExit("No existing virtual copy for this photo; stopping (plan assumed one).")
    left_over = pending_edits(context, copy_id, session=None)
    if left_over:
        raise SystemExit(
            f"{len(left_over)} earlier edit(s) on this copy are not undone, so the start state "
            "would not be the original. Stopping."
        )
    state: dict[str, Any] = {
        "session": journal.session,
        "photo_path": str(args.photo),
        "photo_id": photo.id,
        "copy_id": copy_id,
        "sha256": sha256(args.photo),
        "start": dict(lightroom.get_develop_settings(copy_id)),
        "master_start": dict(lightroom.get_develop_settings(photo.id)),
    }
    print(f"session {journal.session[:8]} | model {profile.model} | playbook {args.playbook}")
    print(
        f"start state recorded: {len(state['start'])} settings of the copy, "
        f"{len(state['master_start'])} of the master; no earlier edits pending undo"
    )
    print(f"request: {args.request}")
    print(f"tools offered: {', '.join(tools)}")

    llm = LocalLlmClient(profile, require_loaded=settings.llm.require_loaded)
    report = None
    started = time.monotonic()
    try:
        agent = Agent(
            llm,
            registry,
            profile,
            max_passes=settings.guardrails.max_passes,
            allowed_tools=tools,
            playbook=args.playbook,
            on_event=lambda text: print(f"  {text}"),
        )
        report = agent.run(args.request, photo.id)
    except DarkroomError as exc:
        print(f"RUN ERROR [{exc.code.value}] {exc.message}\nNext step: {exc.next_step}")
        failures.append("agent run")
    finally:
        llm.close()
    print(f"run time: {time.monotonic() - started:.1f} s")

    log_dir = settings.paths.logs_dir.resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    if report is not None:
        print("== REPORT ==")
        print(
            f"stopped because: {report.stopped_because} | passes {report.passes} | turns "
            f"{report.turns} | previews sent {report.images_sent} | {report.seconds} s"
        )
        for step in report.steps:
            print(
                f"  turn {step.turn}: {step.tool} {json.dumps(step.arguments)} -> "
                f"{'ok' if step.ok else 'refused'}"
            )
        summary_file = log_dir / f"agent_run_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        summary_file.write_text(report.final_message, encoding="utf-8")
        print(f"model summary: {len(report.final_message)} characters, saved to {summary_file}")
    after_run = dict(lightroom.get_develop_settings(copy_id))
    print("settings changed by the run:", differences(state["start"], after_run) or "none")
    master_now = dict(lightroom.get_develop_settings(photo.id))
    check("master's settings unchanged", not differences(state["master_start"], master_now))
    check("original file SHA-256 unchanged", sha256(args.photo) == state["sha256"])

    if args.pause:
        state_file = log_dir / STATE_NAME
        state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"PAUSED: nothing was undone. State saved to {state_file}")
        print(f"virtual copy to look at in Lightroom: id {copy_id} (copy of {args.photo.name})")
    else:
        undo_and_check(context, state)
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


def undo_later(settings: Settings, state_file: Path) -> int:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    context = build_tool_context(settings, session=state["session"])  # resume that run's session
    print(f"resuming session {state['session'][:8]} for {state['photo_path']}")
    undo_and_check(context, state)
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--photo", type=Path, default=None)
    parser.add_argument("--playbook", choices=PLAYBOOKS, default="portrait")
    parser.add_argument("--request", default=REQUEST)
    parser.add_argument("--tools", default=TOOLS, help="comma-separated tool names to offer")
    parser.add_argument("--pause", action="store_true", help="do not undo; save the state")
    parser.add_argument("--undo-state", type=Path, default=None, help="state file of a paused run")
    args = parser.parse_args()
    settings = load_config(None)
    if args.undo_state is not None:
        return undo_later(settings, args.undo_state)
    if args.photo is None:
        parser.error("--photo is required")
    return run(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
