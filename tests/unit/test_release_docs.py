"""Release hygiene: needed files exist, credit the upstream, and their links are real."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_notice_credits_the_upstream_author_and_keeps_its_licence() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    for needle in ("par4987", "Marcin Skalski", "MIT License", "Permission is hereby granted"):
        assert needle in notice


@pytest.mark.parametrize("name", ["README.md", "AGENTS.md", "docs/INSTALL.md", "docs/UPGRADE.md"])
def test_documents_exist_and_their_file_links_resolve(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert text.strip()
    for target in re.findall(r"`((?:docs/|hermes/|src/)[\w./-]+)`", text):
        assert (ROOT / target.rstrip("/")).exists(), f"{name} points at missing {target}"


def test_claude_md_points_at_the_moved_progress_file() -> None:
    assert "docs/PROGRESS.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert not (ROOT / "PROGRESS.md").exists()


def test_no_photos_catalogs_models_or_local_config_are_tracked() -> None:
    try:
        listed = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    forbidden = re.compile(
        r"\.(jpe?g|tiff?|nef|arw|dng|lrcat\w*|lrdata|onnx|gguf|log)$|(^|/)\.env|darkroom\.local\.toml",
        re.IGNORECASE,
    )
    assert [f for f in listed if forbidden.search(f)] == []
