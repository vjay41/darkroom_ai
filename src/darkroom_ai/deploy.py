"""`darkroom deploy`: connect a Hermes profile to this checkout, one confirmed write at a time.

The command first PLANS: it reads the Hermes profile and this repository and lists what it would
change, with a diff for the Hermes config. Nothing is written until the person answers each
question. Every file it would replace is copied to a backup first. It never deletes anything,
never touches Lightroom, and refuses to run when nobody is at the keyboard.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from darkroom_ai.errors import DarkroomError, ErrorCode

SERVER_NAME = "darkroom_ai"
SKILLS_CATEGORY = "photo"
MIN_PYTHON = (3, 13)


class Confirmer(Protocol):
    def confirm(self, action: str, details: Mapping[str, Any]) -> bool: ...


@dataclass(frozen=True)
class FileAction:
    """One file the deploy would write."""

    label: str
    path: Path
    content: bytes
    existing: bytes | None
    diff: str = ""

    @property
    def kind(self) -> str:
        return "create" if self.existing is None else "replace"


@dataclass
class DeployPlan:
    actions: list[FileAction] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (status, text)

    @property
    def problems(self) -> list[str]:
        return [text for status, text in self.checks if status == "FAIL"]


# -- the Hermes config entry ----------------------------------------------------------------------


def venv_python(repo_dir: Path) -> Path:
    return repo_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def server_block(repo_dir: Path, newline: str = "\n") -> str:
    lines = [
        f"  {SERVER_NAME}:   # added by `darkroom deploy`",
        f"    command: {venv_python(repo_dir)}",
        "    args:",
        "      - -m",
        "      - darkroom_ai",
        "      - mcp",
        f"    cwd: {repo_dir}",
        "    timeout: 420            # must exceed the 300 s confirmation wait",
        "    connect_timeout: 60",
        "    sampling:",
        "      enabled: false        # the server never needs the model",
        "    tools:",
        "      resources: false",
        "      prompts: false",
    ]
    return newline.join(lines) + newline


def config_with_server(text: str, repo_dir: Path) -> tuple[str, bool]:
    """Return the config text with the darkroom_ai entry added, and whether it changed."""
    newline = "\r\n" if "\r\n" in text else "\n"
    if re.search(rf"(?m)^  {SERVER_NAME}:", text):
        return text, False
    block = server_block(repo_dir, newline)
    header = re.search(r"(?m)^mcp_servers:[ \t]*\r?\n", text)
    if header:
        return text[: header.end()] + block + text[header.end() :], True
    if text and not text.endswith("\n"):
        text += newline
    return text + newline + "mcp_servers:" + newline + block, True


# -- planning -------------------------------------------------------------------------------------


def _check_profile(profile_dir: Path) -> None:
    if not (profile_dir / "config.yaml").is_file():
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"{profile_dir} does not look like a Hermes profile folder (no config.yaml).",
            next_step="Pass --hermes-profile with the profile folder that holds config.yaml and "
            "skills/ (for example the folder of the profile you use for photo work).",
            details={"profile": str(profile_dir)},
        )


def plan_deploy(repo_dir: Path, profile_dir: Path) -> DeployPlan:
    _check_profile(profile_dir)
    plan = DeployPlan()
    version = sys.version_info[:2]
    plan.checks.append(
        (
            "ok" if version >= MIN_PYTHON else "FAIL",
            f"Python {version[0]}.{version[1]} (needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer)",
        )
    )
    interpreter = venv_python(repo_dir)
    plan.checks.append(
        (
            "ok" if interpreter.is_file() else "FAIL",
            f"project environment: {interpreter}"
            + ("" if interpreter.is_file() else " (run `make install` first)"),
        )
    )

    config = profile_dir / "config.yaml"
    old = config.read_bytes()
    new_text, changed = config_with_server(old.decode("utf-8"), repo_dir)
    if changed:
        diff = "".join(
            difflib.unified_diff(
                old.decode("utf-8").splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                "config.yaml (now)",
                "config.yaml (after deploy)",
            )
        )
        plan.actions.append(
            FileAction(
                "Hermes config: add the darkroom_ai server", config, new_text.encode(), old, diff
            )
        )
    else:
        plan.unchanged.append(f"Hermes config already has a {SERVER_NAME} entry (left as is)")

    skills_src = repo_dir / "hermes" / "skills"
    for source in sorted(skills_src.glob("*/SKILL.md")):
        name = source.parent.name
        target = profile_dir / "skills" / SKILLS_CATEGORY / name / "SKILL.md"
        content = source.read_bytes()
        existing = target.read_bytes() if target.is_file() else None
        if existing == content:
            plan.unchanged.append(f"skill {name} is up to date")
        else:
            plan.actions.append(FileAction(f"skill {name}", target, content, existing))
    return plan


def format_plan(plan: DeployPlan) -> str:
    lines = ["Checks:"]
    lines += [f"  [{status:^4}] {text}" for status, text in plan.checks]
    lines.append("")
    if not plan.actions:
        lines.append("Nothing to write.")
    else:
        lines.append(f"{len(plan.actions)} file(s) would be written (each asks first):")
        for action in plan.actions:
            lines.append(f"  - {action.kind}: {action.label}")
            lines.append(f"      {action.path}")
            if action.diff:
                lines += ["      " + line.rstrip("\r\n") for line in action.diff.splitlines()]
    if plan.unchanged:
        lines.append("")
        lines += [f"Skipped: {text}" for text in plan.unchanged]
    return "\n".join(lines)


# -- applying -------------------------------------------------------------------------------------


def backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.bak-pre-deploy-{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-pre-deploy-{stamp}-{counter}")
        counter += 1
    return candidate


def apply_plan(plan: DeployPlan, confirmer: Confirmer, stamp: str) -> list[str]:
    """Write each planned file after its own confirmation. Returns what was done, in words."""
    done: list[str] = []
    for action in plan.actions:
        details = {"file": str(action.path), "what": action.kind}
        if action.existing is not None:
            details["backup"] = str(backup_path(action.path, stamp))
        if not confirmer.confirm(f"write {action.label}", details):
            done.append(f"skipped (not confirmed): {action.label}")
            continue
        if action.existing is not None:
            backup = backup_path(action.path, stamp)
            shutil.copy2(action.path, backup)
            done.append(f"backup: {backup}")
        action.path.parent.mkdir(parents=True, exist_ok=True)
        action.path.write_bytes(action.content)
        done.append(f"{action.kind}d: {action.label}")
    return done


MANUAL_STEPS = (
    "In Lightroom: File > Plug-in Manager > Add, choose the darkroom.lrplugin folder "
    "(`darkroom lightroom setup` prints it), then Reload Plug-in.",
    "Use a dedicated test catalog and set [lightroom] test_catalog in darkroom.local.toml; "
    "writes are only allowed there until you allowlist another catalog.",
    "Restart Hermes (the desktop app) so it loads the new server and skills, and pick the "
    "profile you deployed to.",
    "Check: `darkroom doctor`, then `darkroom selftest` (read-only).",
)


def require_person(interactive: bool) -> None:
    if not interactive:
        raise DarkroomError(
            ErrorCode.CONFIRMATION_REQUIRED,
            "darkroom deploy changes files outside this repository and needs a person at the "
            "keyboard to answer each question.",
            next_step="Run it yourself in a terminal (not from a script, scheduler or agent). "
            "Use --dry-run to see the plan without a terminal.",
        )


def run_deploy(
    repo_dir: Path,
    profile_dir: Path,
    confirmer: Confirmer,
    *,
    dry_run: bool,
    interactive: bool,
    stamp: str,
    bridge_writer: Callable[[], list[str]] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Plan, show, and (unless dry_run) apply. Returns 0 when done, 1 when checks failed."""
    plan = plan_deploy(repo_dir, profile_dir)
    out(format_plan(plan))
    if plan.problems:
        out("\nFix the failed checks first; nothing was written.")
        return 1
    if dry_run:
        out("\nDry run: nothing was written.")
        return 0
    require_person(interactive)
    for line in apply_plan(plan, confirmer, stamp):
        out(f"  {line}")
    if bridge_writer is not None and confirmer.confirm(
        "write the Lightroom bridge file (bridge.json) from darkroom.toml", {}
    ):
        for line in bridge_writer():
            out(f"  {line}")
    out("\nStill manual:")
    for index, step in enumerate(MANUAL_STEPS, 1):
        out(f"  {index}. {step}")
    return 0
