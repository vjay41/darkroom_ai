"""Playbook-versus-none comparison: the same requests on the same photos in both modes.

Part "compare": each request/photo combination runs as playbook, none, playbook, none on the
photo's existing darkroom_ai virtual copy. Every run is undone and compared exactly.
Part "blind": for some combinations, one run of each mode is LEFT IN PLACE on two new virtual
copies named "... A" and "... B", assigned at random. The assignment goes only to a log file;
nothing printed here tells the modes apart.
Part "undo-blind": undo the blind copies through their saved sessions and compare exactly.

The batch stops at the first problem (restore not exact, master changed, any error from
Lightroom or the model, a slow text-only model call, or the run / copy limits).

Usage:  poetry run python scripts/compare_modes.py compare|blind|undo-blind
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.agent.loop import Agent
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.llm.client import LocalLlmClient
from darkroom_ai.net.loopback import LocalHttp
from darkroom_ai.tools.develop_keys import lighting_limits, white_balance_limits
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.lightroom_tools import pending_edits
from darkroom_ai.tools.registry import ToolRegistry

ROOT = Path(r"D:\darkroom_test")
TOOLS = ("adjust_lighting", "adjust_white_balance", "adjust_hsl", "crop")
EDIT_TOOLS = frozenset(TOOLS)
LIGHTING = {
    "Exposure2012",
    "Contrast2012",
    "Highlights2012",
    "Shadows2012",
    "Whites2012",
    "Blacks2012",
}
WHITE_BALANCE = {"Temperature", "Tint", "IncrementalTemperature", "IncrementalTint"}
BOOKKEEPING = {"WhiteBalance"}  # written by the tool together with a JPG white-balance value
COMBOS: tuple[dict[str, Any], ...] = (
    {"id": 1, "request": "Fix the lighting", "photo": ROOT / "TEST_2467-47.jpg", "named": LIGHTING},
    {
        "id": 2,
        "request": "Make it much brighter",
        "photo": ROOT / "TEST_2457-37.jpg",
        "named": LIGHTING,
    },
    {
        "id": 3,
        "request": "Make the white balance slightly warmer",
        "photo": ROOT / "raw" / "RAW_DSC00481.ARW",
        "named": WHITE_BALANCE,
    },
    {
        "id": 4,
        "request": "Reduce the saturation of orange a little",
        "photo": ROOT / "TEST_2462-42.jpg",
        "named": {"SaturationAdjustmentOrange"},
    },
)
BLIND_COMBOS = (1, 2, 4)
MAX_RUNS = 24
MAX_NEW_COPIES = 8
SLOW_TEXT_CALL_S = 5.0
THINKING = re.compile(r"of which thinking (\d+)")


class StopBatch(Exception):
    """A stop condition was met: the batch ends here and is reported."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Settings whose value differs, compared exactly. Keys only present now are listed too."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            out.append(f"{key}: only now")
        elif key not in after:
            out.append(f"{key}: missing now")
        elif json.dumps(before[key], sort_keys=True) != json.dumps(after[key], sort_keys=True):
            out.append(f"{key}: changed")
    return out


def tolerated(differences: list[str], now: Mapping[str, Any]) -> list[str]:
    """Drop the one difference Lightroom makes by itself: an added EMPTY FilterList."""
    return [
        d for d in differences if not (d == "FilterList: only now" and now.get("FilterList") == {})
    ]


def text_call_seconds(settings: Settings) -> float:
    """One text-only tool call to the model (only if it is reported as loaded)."""
    profile = settings.llm.active
    probe = LocalLlmClient(profile)
    try:
        probe.ensure_loaded()
    finally:
        probe.close()
    http = LocalHttp(profile.base_url, timeout_s=60)
    payload = {
        "model": profile.model,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "temperature": 0,
        "stream": False,
        "max_tokens": 50,
        "reasoning_effort": "none",
    }
    started = time.monotonic()
    try:
        status, _ = http.request_json("POST", "chat/completions", payload)
    finally:
        http.close()
    seconds = time.monotonic() - started
    if status != 200:
        raise StopBatch(f"the text-only model call returned HTTP {status}")
    return seconds


def per_call_limits(settings: Settings) -> dict[str, float]:
    guard = settings.guardrails
    limits = {limit.key: limit.per_pass for limit in lighting_limits(guard).values()}
    for file_format in ("JPG", "RAW"):
        limits.update(
            {m.key: m.per_pass for m in white_balance_limits(guard, file_format).values()}
        )
    return limits


class Recorder(ToolRegistry):
    """Keeps every tool call and its result, silently."""

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self.calls: list[dict[str, Any]] = []

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            result = super().call(name, arguments)
        except DarkroomError as exc:
            self.calls.append({"name": name, "args": dict(arguments), "error": exc.code.value})
            raise
        self.calls.append({"name": name, "args": dict(arguments), "result": result})
        return result


def run_once(
    settings: Settings, combo: Mapping[str, Any], mode: str, copy_id: str | None, *, undo: bool
) -> dict[str, Any]:
    """One agent run. With `undo`, take the edits back and compare exactly; else leave them."""
    seconds = text_call_seconds(settings)
    if seconds > SLOW_TEXT_CALL_S:
        raise StopBatch(
            f"a text-only model call took {seconds:.1f} s (over {SLOW_TEXT_CALL_S:g} s)"
        )
    context = build_tool_context(settings)  # a fresh journal session for this run
    try:
        return _run(settings, context, combo, mode, copy_id, undo=undo)
    finally:
        disconnect(context)


def disconnect(context: Any) -> None:
    """Close the Lightroom connection of a finished run (the plug-in serves one client)."""
    close = getattr(context.lightroom, "close", None)
    if callable(close):
        close()


def _run(
    settings: Settings,
    context: Any,
    combo: Mapping[str, Any],
    mode: str,
    copy_id: str | None,
    *,
    undo: bool,
) -> dict[str, Any]:
    profile = settings.llm.active
    lightroom = context.lightroom
    path: Path = combo["photo"]
    photo = lightroom.find_photo(str(path))
    if photo is None:
        raise StopBatch(f"{path.name} is not in the open catalog")
    registry = Recorder(context)
    target = copy_id or registry.call("get_photo_info", {"photo_id": photo.id})["working_copy_id"]
    if not target:
        raise StopBatch(f"{path.name} has no darkroom_ai virtual copy")
    registry.calls.clear()
    if pending_edits(context, target, session=None):
        raise StopBatch(f"edits are pending on the copy of {path.name}")
    file_hash = sha256(path)
    start = dict(lightroom.get_develop_settings(target))
    master_start = dict(lightroom.get_develop_settings(photo.id))
    events: list[str] = []
    llm = LocalLlmClient(profile, require_loaded=settings.llm.require_loaded)
    try:
        agent = Agent(
            llm,
            registry,
            profile,
            max_passes=settings.guardrails.max_passes,
            allowed_tools=TOOLS,
            playbook="none" if mode == "none" else "portrait",
            guardrails=settings.guardrails,
            on_event=events.append,
        )
        report = agent.run(combo["request"], target)
    except DarkroomError as exc:
        raise StopBatch(f"run error [{exc.code.value}] {exc.message}") from exc
    finally:
        llm.close()

    limits = per_call_limits(settings)
    edit_calls = [c for c in registry.calls if c["name"] in EDIT_TOOLS]
    changes = [change for c in edit_calls for change in (c.get("result") or {}).get("changes", [])]
    analyses = [
        c["result"] for c in registry.calls if c["name"] == "analyze_photo" and "result" in c
    ]
    written: set[str] = set()
    for entry in context.journal.entries():
        applied = entry.applied or {}
        if entry.session == context.journal.session and entry.status == "ok" and entry.undo:
            written.update((applied.get("settings") or {}).keys())
    keys = [json.dumps([c["name"], c["args"]], sort_keys=True) for c in edit_calls]

    def cast(analysis: Mapping[str, Any]) -> Any:
        return analysis["analysis"]["color_cast"]["blue_yellow"]

    first, last = analyses[0]["analysis"], analyses[-1]["analysis"]
    metrics: dict[str, Any] = {
        "combo": combo["id"],
        "request": combo["request"],
        "photo": path.name,
        "mode": mode,
        "passes": report.passes,
        "turns": report.turns,
        "tool_calls": len(edit_calls),
        "refused_calls": sum(1 for c in edit_calls if "error" in c),
        "clamps": sum(1 for change in changes if change["limited_by"]),
        "at_per_call_max": sum(
            1
            for change in changes
            if "per_pass" in change["limited_by"]
            or abs(abs(change["applied_change"]) - limits.get(change["key"], 30.0)) < 1e-9
        ),
        "repeated_calls": sum(1 for a, b in itertools.pairwise(keys) if a == b),
        "no_change_answers": sum(1 for c in edit_calls if (c.get("result") or {}).get("no_change")),
        "early_stop": report.stopped_because == "finished"
        and report.passes < settings.guardrails.max_passes,
        "ended_at_pass_limit": report.ended_at_pass_limit,
        "stopped_because": report.stopped_because,
        "settings_written": sorted(written - BOOKKEEPING),
        "unnamed_edits": sorted(written - set(combo["named"]) - BOOKKEEPING),
        "start": {
            "highlights_pct": first["clipping"]["highlights_pct"],
            "shadows_pct": first["clipping"]["shadows_pct"],
            "p50": first["tones"]["p50"],
            "blue_yellow": cast(analyses[0]),
        },
        "final": {
            "highlights_pct": last["clipping"]["highlights_pct"],
            "shadows_pct": last["clipping"]["shadows_pct"],
            "p50": last["tones"]["p50"],
            "blue_yellow": cast(analyses[-1]),
        },
        "readback_differences": len(analyses[-1].get("differs_from_applied") or []),
        "seconds": report.seconds,
        "thinking_tokens": sum(int(m.group(1)) for e in events for m in [THINKING.search(e)] if m),
        "applied": [
            {
                "tool": c["name"],
                "changes": [
                    [ch["key"], ch["requested_change"], ch["applied_change"], ch["limited_by"]]
                    for ch in (c.get("result") or {}).get("changes", [])
                ],
            }
            for c in edit_calls
        ],
    }

    master_now = dict(lightroom.get_develop_settings(photo.id))
    if tolerated(exact_diff(master_start, master_now), master_now):
        raise StopBatch(f"the master of {path.name} changed")
    if sha256(path) != file_hash:
        raise StopBatch(f"the file {path.name} changed")
    if not undo:
        metrics["state"] = {
            "session": context.journal.session,
            "copy_id": target,
            "photo_id": photo.id,
            "photo_path": str(path),
            "start": start,
            "master_start": master_start,
            "sha256": file_hash,
        }
        return metrics
    ToolRegistry(context).call("undo", {"photo_id": target, "scope": "all"})
    now = dict(lightroom.get_develop_settings(target))
    left = tolerated(exact_diff(start, now), now)
    if left:
        raise StopBatch(f"restore not exact on the copy of {path.name}: {left[:6]}")
    if pending_edits(context, target, session=None):
        raise StopBatch(f"edits still pending on the copy of {path.name}")
    return metrics


def line(m: Mapping[str, Any]) -> str:
    return (
        f"combo {m['combo']} {m['mode']:8} passes {m['passes']} calls {m['tool_calls']} clamps "
        f"{m['clamps']} at-max {m['at_per_call_max']} repeats {m['repeated_calls']} unnamed "
        f"{len(m['unnamed_edits'])} early-stop {m['early_stop']} | highlights "
        f"{m['start']['highlights_pct']} -> {m['final']['highlights_pct']} %, shadows "
        f"{m['start']['shadows_pct']} -> {m['final']['shadows_pct']} %, p50 {m['start']['p50']} -> "
        f"{m['final']['p50']} | {m['seconds']} s, thinking {m['thinking_tokens']}"
    )


def main() -> int:
    part = sys.argv[1]
    settings = load_config(None)
    logs = settings.paths.logs_dir.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    results_file = logs / "compare_results.json"
    mapping_file = logs / "blind_pairs_mapping.json"
    counter_file = logs / "compare_counters.json"
    counters = (
        json.loads(counter_file.read_text(encoding="utf-8")) if counter_file.is_file() else {}
    )
    runs, copies = int(counters.get("runs", 0)), int(counters.get("new_copies", 0))

    def save_counters() -> None:
        counter_file.write_text(json.dumps({"runs": runs, "new_copies": copies}), encoding="utf-8")

    try:
        if part == "compare":
            results: list[dict[str, Any]] = []
            for combo in COMBOS:
                for mode in ("playbook", "none", "playbook", "none"):
                    if runs + 1 > MAX_RUNS:
                        raise StopBatch("the run limit would be exceeded")
                    metrics = run_once(settings, combo, mode, None, undo=True)
                    runs += 1
                    save_counters()
                    results.append(metrics)
                    results_file.write_text(json.dumps(results, indent=1), encoding="utf-8")
                    print(line(metrics), flush=True)
            print(f"compare part done: {len(results)} runs, all undone and compared exactly")
        elif part == "blind":
            pairs: list[dict[str, Any]] = []
            chooser = random.SystemRandom()
            for number, combo_id in enumerate(BLIND_COMBOS, start=1):
                combo = next(c for c in COMBOS if c["id"] == combo_id)
                if runs + 2 > MAX_RUNS or copies + 2 > MAX_NEW_COPIES:
                    raise StopBatch("the run or copy limit would be exceeded")
                context = build_tool_context(settings)
                photo = context.lightroom.find_photo(str(combo["photo"]))
                if photo is None:
                    raise StopBatch(f"{combo['photo'].name} is not in the open catalog")
                made = {}
                for letter in ("A", "B"):
                    copy = context.lightroom.create_virtual_copy(
                        photo.id, f"darkroom_ai pair {number} {letter}"
                    )
                    made[letter] = copy.id
                    copies += 1
                    save_counters()
                disconnect(context)
                modes = ["playbook", "none"]
                chooser.shuffle(modes)
                pair: dict[str, Any] = {"pair": number, "combo": combo_id, "runs": {}}
                for letter, mode in zip(("A", "B"), modes, strict=True):
                    metrics = run_once(settings, combo, mode, made[letter], undo=False)
                    runs += 1
                    save_counters()
                    pair["runs"][letter] = metrics
                pairs.append(pair)
                mapping_file.write_text(json.dumps(pairs, indent=1), encoding="utf-8")
                print(
                    f'blind pair {number}: "{combo["request"]}" on {combo["photo"].name} -> copies '
                    f"'darkroom_ai pair {number} A' and 'darkroom_ai pair {number} B' are ready "
                    "(edits left in place)",
                    flush=True,
                )
            print(f"blind part done; the assignment is only in {mapping_file}")
        elif part == "undo-blind":
            pairs = json.loads(mapping_file.read_text(encoding="utf-8"))
            for pair in pairs:
                for letter, metrics in pair["runs"].items():
                    state = metrics["state"]
                    context = build_tool_context(settings, session=state["session"])
                    lightroom = context.lightroom
                    ToolRegistry(context).call(
                        "undo", {"photo_id": state["copy_id"], "scope": "all"}
                    )
                    now = dict(lightroom.get_develop_settings(state["copy_id"]))
                    left = tolerated(exact_diff(state["start"], now), now)
                    master_now = dict(lightroom.get_develop_settings(state["photo_id"]))
                    master_left = tolerated(
                        exact_diff(state["master_start"], master_now), master_now
                    )
                    same_file = sha256(Path(state["photo_path"])) == state["sha256"]
                    pending = pending_edits(context, state["copy_id"], session=None)
                    ok = not left and not master_left and same_file and not pending
                    print(
                        f"pair {pair['pair']} {letter}: restore exact {not left}, master unchanged "
                        f"{not master_left}, file unchanged {same_file}, "
                        f"nothing pending {not pending}"
                    )
                    disconnect(context)
                    if not ok:
                        raise StopBatch(
                            f"pair {pair['pair']} {letter} did not restore exactly: {left[:6]}"
                        )
        else:
            raise SystemExit(f"unknown part {part}")
    except StopBatch as stop:
        save_counters()
        print(f"BATCH STOPPED: {stop}", flush=True)
        print(f"runs so far: {runs}; new copies so far: {copies}")
        return 1
    save_counters()
    print(f"runs so far: {runs} (limit {MAX_RUNS}); new copies: {copies} (limit {MAX_NEW_COPIES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
