"""scripts/clean.py removes caches only; never the venv, catalogs, photos or user data."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]


def _load_clean() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clean_script", REPO / "scripts" / "clean.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clean_script = _load_clean()

REMOVED_BY_CLEAN = [
    "__pycache__/a.pyc",
    "src/pkg/__pycache__/b.pyc",
    ".pytest_cache/x",
    ".mypy_cache/x",
    ".ruff_cache/x",
    "build/lib/x",
    "src/pkg.egg-info/PKG-INFO",
    ".coverage",
]
REMOVED_BY_CLEAN_ALL = ["dist/pkg.whl", "htmlcov/index.html"]
MUST_SURVIVE = [
    ".venv/Lib/__pycache__/keep.pyc",
    ".venv/pyvenv.cfg",
    ".git/config",
    "test_catalog/Test.lrcat",
    "test_catalog/__pycache__/keep.pyc",
    "Other.lrcat-data/__pycache__/keep.pyc",
    "Test Previews.lrdata/__pycache__/keep.pyc",
    "photos/__pycache__/keep.jpg",
    "photos/img.jpg",
    "models/mask.onnx",
    "logs/journal.jsonl",
    "outputs/result.psd",
    "previews/p.jpg",
    "src/pkg/module.py",
    "src/build/keep.py",
    "darkroom.local.toml",
    "poetry.lock",
]


def _make_tree(root: Path) -> None:
    for name in REMOVED_BY_CLEAN + REMOVED_BY_CLEAN_ALL + MUST_SURVIVE:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def _exists(root: Path, names: list[str]) -> list[bool]:
    return [(root / name).exists() for name in names]


def test_clean_removes_caches_only(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    clean_script.clean(tmp_path)
    assert not any(_exists(tmp_path, REMOVED_BY_CLEAN))
    assert all(_exists(tmp_path, REMOVED_BY_CLEAN_ALL))
    assert all(_exists(tmp_path, MUST_SURVIVE))


def test_clean_all_also_removes_dist_and_htmlcov(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    clean_script.clean(tmp_path, everything=True)
    assert not any(_exists(tmp_path, REMOVED_BY_CLEAN + REMOVED_BY_CLEAN_ALL))
    assert all(_exists(tmp_path, MUST_SURVIVE))


def test_dry_run_removes_nothing(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    listed = clean_script.clean(tmp_path, everything=True, dry_run=True)
    assert len(listed) >= len(REMOVED_BY_CLEAN_ALL) + 5
    assert all(_exists(tmp_path, REMOVED_BY_CLEAN + REMOVED_BY_CLEAN_ALL + MUST_SURVIVE))
