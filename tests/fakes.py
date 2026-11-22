"""Fake collaborators for tests. They satisfy the Protocols in darkroom_ai.interfaces."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from darkroom_ai.adapters.lightroom.simulated import DEFAULT_SETTINGS, SimulatedLightroom
from darkroom_ai.adapters.photoshop.models import DocumentInfo, LayerInfo, PhotoshopInfo
from darkroom_ai.safety.journal import JournalEntry, JournalStatus, new_entry


class InMemoryJournal:
    """ActionJournal that keeps entries in a list."""

    def __init__(self, session: str = "test-session") -> None:
        self.session = session
        self._entries: list[JournalEntry] = []

    def record(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        status: JournalStatus = "ok",
        applied: Mapping[str, Any] | None = None,
        undo: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JournalEntry:
        entry = new_entry(
            self.session, tool, args, status=status, applied=applied, undo=undo, error=error
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[JournalEntry]:
        return list(self._entries)


class AllowAll:
    """Confirmer that agrees to everything and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append(action)
        return True


FakeLightroom = SimulatedLightroom
__all__ = ["DEFAULT_SETTINGS", "FakeLightroom"]


class FakePhotoshop:
    """PhotoshopAdapter that copies files and records what it was asked to do."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.open: set[Path] = set()

    def _note(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def _unique(self, path: Path) -> Path:
        candidate, counter = path, 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
            counter += 1
        return candidate

    def connect(self) -> PhotoshopInfo:
        return self.info()

    def info(self) -> PhotoshopInfo:
        return PhotoshopInfo(name="Adobe Photoshop", version="27.8.0", documents=len(self.open))

    def check_version(self) -> PhotoshopInfo:
        return self.info()

    def prepare_copy(self, source: Path) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        target = self._unique(self.work_dir / f"{source.stem}_copy{source.suffix}")
        shutil.copy2(source, target)
        self._note("prepare_copy", source)
        return target

    def open_document(self, path: Path) -> DocumentInfo:
        self.open.add(path)
        self._note("open_document", path)
        return self.document_info(path)

    def run_script(
        self,
        code: str,
        *,
        document: Path | None = None,
        args: Sequence[str] | None = None,
        timeout_s: float | None = None,
    ) -> str:
        self._note("run_script", code)
        return ""

    def document_info(self, path: Path) -> DocumentInfo:
        return DocumentInfo(
            name=path.name, width=60, height=40, bits=16, mode="DocumentMode.RGB", layers=1
        )

    def add_layer(self, path: Path, name: str) -> int:
        self._note("add_layer", path, name)
        return 2

    def save_psd(self, path: Path, out_path: Path | None = None) -> Path:
        target = self._unique(out_path or path.with_name(f"{path.stem}_darkroom.psd"))
        target.write_bytes(b"8BPS")
        self._note("save_psd", target)
        return target

    def apply_mask_from_png(self, path: Path, mask_png: Path, layer_name: str = "subject") -> None:
        assert path in self.open, "the document must be open"
        assert mask_png.is_file()
        self._note("apply_mask_from_png", mask_png, layer_name)

    def fill_selection_from_mask(
        self, path: Path, mask_png: Path, grow_px: int = 4, layer_name: str = "removed"
    ) -> None:
        assert path in self.open, "the document must be open"
        assert mask_png.is_file()
        self._note("fill_selection_from_mask", mask_png, grow_px)

    def add_fill_layer_below(
        self, path: Path, rgb: tuple[int, int, int], name: str = "background"
    ) -> None:
        self._note("add_fill_layer_below", rgb)

    def add_image_layer_below(self, path: Path, image: Path, name: str = "background") -> None:
        assert image.is_file() and image.parent == self.work_dir
        self._note("add_image_layer_below", image)

    def layers(self, path: Path) -> tuple[LayerInfo, ...]:
        return ()

    def save_jpg(self, path: Path, out_path: Path | None = None, quality: int = 10) -> Path:
        target = self._unique(out_path or path.with_name(f"{path.stem}_darkroom.jpg"))
        target.write_bytes(b"\xff\xd8jpg")
        self._note("save_jpg", target)
        return target

    def close_document(self, path: Path) -> None:
        self.open.discard(path)
        self._note("close_document", path)


class FakeAccess:
    """AccessStore that keeps the allowlist in memory."""

    def __init__(self, catalogs: Sequence[str] = (), folders: Sequence[str] = ()) -> None:
        self._catalogs = list(catalogs)
        self._folders = list(folders)

    def allowed_catalogs(self) -> tuple[str, ...]:
        return tuple(self._catalogs)

    def photos_roots(self) -> tuple[str, ...]:
        return tuple(self._folders)

    def allow_catalog(self, catalog: str) -> None:
        self._catalogs.append(catalog)

    def allow_photos_folder(self, folder: str) -> None:
        self._folders.append(folder)
