"""`darkroom deploy`: plan first, ask before each write, back up, refuse unattended."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from darkroom_ai.deploy import (
    SERVER_NAME,
    apply_plan,
    config_with_server,
    plan_deploy,
    run_deploy,
)
from darkroom_ai.errors import DarkroomError, ErrorCode

SKILL = "---\nname: lr-demo\n---\n# demo\n"


class Answers:
    """Confirmer that answers from a list and records every question."""

    def __init__(self, *answers: bool, default: bool = False) -> None:
        self.answers = list(answers)
        self.default = default
        self.asked: list[tuple[str, Mapping[str, Any]]] = []

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append((action, details))
        return self.answers.pop(0) if self.answers else self.default


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "hermes" / "skills" / "lr-demo").mkdir(parents=True)
    (root / "hermes" / "skills" / "lr-demo" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    python = root / ".venv" / "Scripts" / "python.exe"
    posix = root / ".venv" / "bin" / "python"
    for path in (python, posix):
        path.parent.mkdir(parents=True)
        path.write_text("")
    return root


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    folder = tmp_path / "profile"
    folder.mkdir()
    (folder / "config.yaml").write_text("model: x\nmcp_servers:\n  other:\n    command: y\n")
    return folder


def test_adds_the_server_under_an_existing_mcp_servers_section(repo: Path) -> None:
    text = "model: x\nmcp_servers:\n  other:\n    command: y\n"
    new, changed = config_with_server(text, repo)
    assert changed
    assert new.index(f"  {SERVER_NAME}:") < new.index("  other:")
    assert "sampling:\n      enabled: false" in new
    assert new.count("mcp_servers:") == 1


def test_adds_the_whole_section_when_missing_and_keeps_crlf(repo: Path) -> None:
    new, changed = config_with_server("model: x\r\n", repo)
    assert changed and "mcp_servers:\r\n" in new and "\r\n" in new
    assert "\n" not in new.replace("\r\n", "")


def test_leaves_an_existing_entry_alone(repo: Path) -> None:
    text = f"mcp_servers:\n  {SERVER_NAME}:\n    command: mine\n"
    new, changed = config_with_server(text, repo)
    assert not changed and new == text


def test_plan_changes_nothing_and_lists_config_diff_and_skill(repo: Path, profile: Path) -> None:
    before = (profile / "config.yaml").read_bytes()
    plan = plan_deploy(repo, profile)
    labels = [a.label for a in plan.actions]
    assert "Hermes config: add the darkroom_ai server" in labels and "skill lr-demo" in labels
    config = next(a for a in plan.actions if a.path.name == "config.yaml")
    assert "+  darkroom_ai:" in config.diff
    assert (profile / "config.yaml").read_bytes() == before
    assert not (profile / "skills").exists()


def test_a_folder_without_config_is_refused(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        plan_deploy(repo, tmp_path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_nothing_is_written_without_an_answer_and_declines_are_respected(
    repo: Path, profile: Path
) -> None:
    plan = plan_deploy(repo, profile)
    confirmer = Answers(default=False)
    done = apply_plan(plan, confirmer, "T")
    assert len(confirmer.asked) == len(plan.actions) == 2
    assert all(line.startswith("skipped") for line in done)
    assert not (profile / "skills").exists()
    assert SERVER_NAME not in (profile / "config.yaml").read_text()


def test_each_write_asks_and_replaced_files_are_backed_up(repo: Path, profile: Path) -> None:
    original = (profile / "config.yaml").read_bytes()
    plan = plan_deploy(repo, profile)
    apply_plan(plan, Answers(default=True), "T")
    assert (profile / "config.yaml.bak-pre-deploy-T").read_bytes() == original
    assert SERVER_NAME in (profile / "config.yaml").read_text()
    skill = profile / "skills" / "photo" / "lr-demo" / "SKILL.md"
    assert skill.read_text(encoding="utf-8") == SKILL

    skill.write_text("old", encoding="utf-8")  # a changed skill is replaced only with a backup
    apply_plan(plan_deploy(repo, profile), Answers(default=True), "T2")
    assert skill.read_text(encoding="utf-8") == SKILL
    assert (skill.parent / "SKILL.md.bak-pre-deploy-T2").read_text() == "old"


def test_a_second_run_has_nothing_to_do(repo: Path, profile: Path) -> None:
    apply_plan(plan_deploy(repo, profile), Answers(default=True), "T")
    again = plan_deploy(repo, profile)
    assert again.actions == [] and len(again.unchanged) == 2


def test_refuses_to_write_when_nobody_is_at_the_keyboard(repo: Path, profile: Path) -> None:
    lines: list[str] = []
    with pytest.raises(DarkroomError) as caught:
        run_deploy(
            repo,
            profile,
            Answers(default=True),
            dry_run=False,
            interactive=False,
            stamp="T",
            out=lines.append,
        )
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert SERVER_NAME not in (profile / "config.yaml").read_text()


def test_dry_run_works_without_a_terminal_and_writes_nothing(repo: Path, profile: Path) -> None:
    lines: list[str] = []
    code = run_deploy(
        repo,
        profile,
        Answers(default=True),
        dry_run=True,
        interactive=False,
        stamp="T",
        out=lines.append,
    )
    assert code == 0 and any("Dry run" in line for line in lines)
    assert SERVER_NAME not in (profile / "config.yaml").read_text()


def test_missing_environment_blocks_the_deploy(repo: Path, profile: Path) -> None:
    for path in (repo / ".venv").rglob("python*"):
        path.unlink()
    lines: list[str] = []
    confirmer = Answers(default=True)
    code = run_deploy(
        repo, profile, confirmer, dry_run=False, interactive=True, stamp="T", out=lines.append
    )
    assert code == 1 and confirmer.asked == []
    assert any("make install" in line for line in lines)


def test_the_bridge_file_is_written_only_after_its_own_answer(repo: Path, profile: Path) -> None:
    called: list[bool] = []

    def writer() -> list[str]:
        called.append(True)
        return ["bridge written"]

    confirmer = Answers(True, True, False)  # config, skill, bridge: declined
    run_deploy(
        repo,
        profile,
        confirmer,
        dry_run=False,
        interactive=True,
        stamp="T",
        bridge_writer=writer,
        out=lambda _: None,
    )
    assert called == [] and len(confirmer.asked) == 3
