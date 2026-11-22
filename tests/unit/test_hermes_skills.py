"""The Hermes knowledge skills shipped in hermes/skills: structure, real tool names, no recipes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from darkroom_ai.tools.registry import TOOLS

SKILLS = Path(__file__).parents[2] / "hermes" / "skills"
SKILL_DIRS = sorted(p for p in SKILLS.iterdir() if p.is_dir())
EXPECTED = {
    "lr-batch-operations",
    "lr-catalog-organisation",
    "lr-catalog-partnering",
    "lr-genre-knowledge",
    "lr-lens-corrections",
    "lr-masking",
    "lr-noise-reduction",
    "lr-presets",
    "lr-safe-operations",
    "lr-sharpening-clarity",
    "lr-skin-tone",
    "lr-tone-curve",
    "lr-upgrade",
    "lr-user-guidance",
}
SLIDER_WORDS = (
    "exposure|contrast|highlights|shadows|whites|blacks|temperature|temp|tint|clarity|"
    "vibrance|saturation|dehaze|texture|sharpening|feather|amount"
)
# "highlights -20", "exposure +0.3", "contrast: +10" and similar: a slider value is a recipe.
RECIPE = re.compile(rf"\b(?:{SLIDER_WORDS})\b[^.\n]{{0,12}}[+\-\u2212]\d", re.IGNORECASE)


def _split(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    head, _, body = text[4:].partition("\n---\n")
    return head, body


def test_all_expected_skills_exist() -> None:
    assert {p.name for p in SKILL_DIRS} == EXPECTED


@pytest.mark.parametrize("folder", SKILL_DIRS, ids=lambda p: p.name)
def test_frontmatter_is_complete_and_matches_the_folder(folder: Path) -> None:
    head, body = _split(folder / "SKILL.md")
    assert re.search(rf"^name: {re.escape(folder.name)}$", head, re.M)
    for key in ("description:", "version:", "license:", "platforms:", "metadata:"):
        assert key in head, f"{folder.name} lacks {key}"
    assert len(body) < 5000, "keep skills short: they cost context every time they load"


@pytest.mark.parametrize("folder", SKILL_DIRS, ids=lambda p: p.name)
def test_listed_tools_exist(folder: Path) -> None:
    head, _ = _split(folder / "SKILL.md")
    found = re.search(r"uses_tools: \[(.*?)\]", head)
    assert found, f"{folder.name} must list the tools it mentions"
    listed = {name.strip() for name in found.group(1).split(",") if name.strip()}
    known = {tool.name for tool in TOOLS}
    assert listed <= known, f"unknown tools: {sorted(listed - known)}"


@pytest.mark.parametrize("folder", SKILL_DIRS, ids=lambda p: p.name)
def test_every_backticked_tool_is_listed(folder: Path) -> None:
    head, body = _split(folder / "SKILL.md")
    found = re.search(r"uses_tools: \[(.*?)\]", head)
    assert found is not None
    listed = {name.strip() for name in found.group(1).split(",")}
    known = {tool.name for tool in TOOLS}
    mentioned = {t for t in re.findall(r"`([a-z_]+)`", body) if t in known}
    assert mentioned <= listed, f"mentioned but not listed: {sorted(mentioned - listed)}"


@pytest.mark.parametrize("folder", SKILL_DIRS, ids=lambda p: p.name)
def test_no_slider_values_or_recipes(folder: Path) -> None:
    _, body = _split(folder / "SKILL.md")
    assert RECIPE.search(body) is None, RECIPE.search(body)


def test_developer_skill_keeps_its_boundaries() -> None:
    text = (SKILLS.parent / "dev-skills" / "darkroom-dev-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "name: darkroom-dev-review" in text
    for rule in ("Do not commit, push", "Do not edit files under", "TEST catalog only"):
        assert rule in text
