"""First end-to-end agent run: lighting + white balance on a virtual copy, then undo and verify.

The local model sees the measurements and a downscaled preview (loopback only). Prints tool
calls, numbers and timings; the model's free-text summary goes to a file under logs/ and only its
length is printed. Afterwards everything this run changed is undone and every develop setting is
compared with the state recorded before the run.

Usage:  poetry run python scripts/agent_run.py --photo PATH [--playbook portrait]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from darkroom_ai.agent.loop import PLAYBOOKS, Agent
from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.llm.client import LocalLlmClient
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

REQUEST = "Balance the lighting and correct the white balance if the measurements call for it."
TOOLS = ("adjust_lighting", "adjust_white_balance")
failures: list[str] = []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def pending_undo(entries: Sequence[JournalEntry], copy_id: str) -> list[JournalEntry]:
    """Journal entries for this copy that an undo would still take back (same rule as the tool)."""
    already = {
        str(entry_id)
        for entry in entries
        if entry.tool == "undo" and entry.status == "ok" and entry.applied
        for entry_id in entry.applied.get("undone", [])
    }
    return [
        entry
        for entry in entries
        if entry.undoable
        and entry.undo is not None
        and entry.undo.get("photo_id") == copy_id
        and entry.undo.get("settings")
        and entry.id not in already
    ]


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
        try:
            result = super().call(name, arguments)
        except DarkroomError as exc:
            print(f"    TOOL {name} {json.dumps(dict(arguments))} -> REFUSED [{exc.code.value}]")
            raise
        if name == "analyze_photo":
            print(f"    MEASURED: {numbers(result['analysis'])}")
        elif name in TOOLS:
            print(f"    TOOL {name} requested {json.dumps(dict(arguments))}")
            print(f"         result {json.dumps(result, default=str)}")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--playbook", choices=PLAYBOOKS, default="portrait")
    args = parser.parse_args()
    settings = load_config(None)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    profile = settings.llm.active
    context = build_tool_context(settings)  # default confirmer: refuses anything gated
    lightroom, journal = context.lightroom, context.journal
    session = journal.session  # type: ignore[attr-defined]
    registry = RecordingRegistry(context)

    photo = lightroom.find_photo(str(args.photo))
    if photo is None:
        raise SystemExit(f"{args.photo} is not in the open catalog.")
    hash_before = sha256(args.photo)
    info = ToolRegistry(context).call("get_photo_info", {"photo_id": photo.id})
    copy_id = info["working_copy_id"]
    if not copy_id:
        raise SystemExit("No existing virtual copy for this photo; stopping (plan assumed one).")
    left_over = pending_undo(journal.entries(), copy_id)
    if left_over:
        raise SystemExit(
            f"{len(left_over)} earlier edit(s) on this copy are not undone; 'undo all' would "
            "reach beyond this run. Stopping."
        )
    start = dict(lightroom.get_develop_settings(copy_id))
    master_start = dict(lightroom.get_develop_settings(photo.id))
    print(f"session {session[:8]} | profile model {profile.model} | playbook {args.playbook}")
    print(
        f"start state recorded: {len(start)} settings of the copy, {len(master_start)} of the "
        "master; no earlier edits pending undo"
    )
    print(f"request: {REQUEST}")

    llm = LocalLlmClient(profile)
    report = None
    started = time.monotonic()
    try:
        agent = Agent(
            llm,
            registry,
            profile,
            max_passes=settings.guardrails.max_passes,
            allowed_tools=TOOLS,
            playbook=args.playbook,
            on_event=lambda text: print(f"  {text}"),
        )
        report = agent.run(REQUEST, photo.id)
    except DarkroomError as exc:
        print(f"RUN ERROR [{exc.code.value}] {exc.message}\nNext step: {exc.next_step}")
        failures.append("agent run")
    finally:
        llm.close()
    print(f"run time: {time.monotonic() - started:.1f} s")

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
        log_dir = settings.paths.logs_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        summary_file = log_dir / f"agent_run_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        summary_file.write_text(report.final_message, encoding="utf-8")
        print(f"model summary: {len(report.final_message)} characters, saved to {summary_file}")
        after_run = dict(lightroom.get_develop_settings(copy_id))
        print("settings changed by the run:", differences(start, after_run) or "none")

    print("== UNDO AND CHECKS ==")
    mine = [e for e in pending_undo(journal.entries(), copy_id)]
    check(
        "every edit pending undo belongs to this run's session",
        all(entry.session == session for entry in mine),
    )
    try:
        undone = ToolRegistry(context).call("undo", {"photo_id": photo.id, "scope": "all"})
        print(
            f"  undo all: {undone['undone_steps']} step(s) taken back "
            f"({len(mine)} were pending from this run)"
        )
        check("undo took back exactly this run's edits", undone["undone_steps"] == len(mine))
    except DarkroomError as exc:
        print(f"UNDO ERROR [{exc.code.value}] {exc.message}", file=sys.stderr)
        failures.append("undo")
    left = differences(start, dict(lightroom.get_develop_settings(copy_id)))
    check(f"all {len(start)} settings of the copy equal the recorded start state", not left)
    if left:
        print("   differences:", left)
    master_left = differences(master_start, dict(lightroom.get_develop_settings(photo.id)))
    check("master's settings never changed", not master_left)
    check("original file SHA-256 unchanged", sha256(args.photo) == hash_before)
    check("nothing left pending undo", not pending_undo(journal.entries(), copy_id))
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
