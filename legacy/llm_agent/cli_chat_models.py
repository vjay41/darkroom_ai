"""PARKED (2026-11-21): the old `darkroom chat` and `darkroom models test` commands.

Not importable as part of the installed package any more. To bring the old design back, check out
the git tag `pre-mcp-pivot`. Kept here only as a reference.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping
from typing import Any

# (imports from darkroom_ai.agent / darkroom_ai.llm are valid only in the pre-mcp-pivot tag)

EXIT_OK, EXIT_PROBLEM, EXIT_ERROR = 0, 1, 2
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
    if settings.llm.require_loaded:
        # First of all, before anything touches Lightroom: is the model loaded?
        probe = LocalLlmClient(profile)
        try:
            probe.ensure_loaded()
        finally:
            probe.close()
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
    playbook = args.playbook or settings.agent.default_playbook
    print(f"photo: {photo.file_name} | model: {profile.model} | playbook: {playbook}")
    llm = LocalLlmClient(profile, require_loaded=settings.llm.require_loaded)
    try:
        agent = Agent(
            llm,
            registry,
            profile,
            max_passes=settings.guardrails.max_passes,
            allowed_tools=CHAT_TOOLS,
            playbook=playbook,
            guardrails=settings.guardrails,
            on_event=lambda text: print(f"  {text}"),
        )
        report = agent.run(args.request, photo.id)
    finally:
        llm.close()
    if getattr(args, "summary_file", False):
        log_dir = settings.paths.logs_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        target = log_dir / f"chat_summary_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        target.write_text(report.final_message, encoding="utf-8")
        print(f"\nsummary: {len(report.final_message)} characters, saved to {target}")
    else:
        print(f"\n{report.final_message}")
    print(
        f"({report.passes} pass(es), {report.turns} turns, {report.seconds:.0f} s, "
        f"{report.images_sent} preview(s) sent to the local model; `darkroom chat` with "
        f"'undo' or the undo tool takes the edits back)"
    )
    return EXIT_OK


