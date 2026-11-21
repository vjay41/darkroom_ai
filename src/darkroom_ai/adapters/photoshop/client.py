"""Photoshop client: safe, deadline-bound operations on COPIES inside the work folder.

Rules enforced here:
- Photoshop is attached to; it is launched only when photoshop.auto_start is on.
- Only files inside the work folder are opened; `prepare_copy` puts a copy of a source file there.
  The source file is never opened or written.
- Output is always a NEW file; nothing is overwritten.
- Every call has a deadline (see worker.py).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ValidationError

from darkroom_ai.adapters.photoshop import read_jsx
from darkroom_ai.adapters.photoshop.models import (
    ActionManagerInfo,
    DocumentInfo,
    LayerInfo,
    PhotoshopInfo,
)
from darkroom_ai.adapters.photoshop.windows import StartWatchdog, WindowInfo, photoshop_windows
from darkroom_ai.adapters.photoshop.worker import ChildProcessRunner, OperationRunner
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode

# Versions this adapter has been run against (major.minor).
TESTED_VERSIONS = ("27.8",)


def unique_path(path: Path) -> Path:
    """Return `path`, or `name-2.ext`, `name-3.ext`, ... if it already exists."""
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


class PhotoshopClient:
    def __init__(
        self,
        *,
        work_dir: Path,
        timeout_s: float = 30.0,
        start_timeout_s: float = 120.0,
        ready_grace_s: float = 30.0,
        select_subject_timeout_s: float = 180.0,
        list_windows: Callable[[], list[WindowInfo]] = photoshop_windows,
        runner: OperationRunner,
        tested_versions: tuple[str, ...] = TESTED_VERSIONS,
    ) -> None:
        self._work_dir = work_dir
        self._timeout_s = timeout_s
        self._start_timeout_s = start_timeout_s
        self._ready_grace_s = ready_grace_s
        self._select_subject_timeout_s = select_subject_timeout_s
        self._list_windows = list_windows
        self._run = runner
        self._tested_versions = tested_versions

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            work_dir=settings.paths.outputs_dir.resolve() / "photoshop",
            timeout_s=settings.photoshop.timeout_s,
            start_timeout_s=settings.photoshop.start_timeout_s,
            ready_grace_s=settings.photoshop.ready_grace_s,
            select_subject_timeout_s=settings.photoshop.select_subject_timeout_s,
            runner=ChildProcessRunner(
                settings.photoshop.prog_id, auto_start=settings.photoshop.auto_start
            ),
        )

    # -- helpers ------------------------------------------------------------------------------

    def _call(
        self, operation: str, /, *, timeout_s: float | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        deadline = self._timeout_s if timeout_s is None else timeout_s
        return self._run(operation, kwargs, timeout_s=deadline)

    def _inside_work_dir(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._work_dir.resolve()):
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                f"Photoshop only works on copies inside {self._work_dir}; refused: {path}",
                next_step="Make a working copy with prepare_copy() and use that path.",
                details={"path": str(path), "work_dir": str(self._work_dir)},
            )
        return resolved

    @staticmethod
    def _typed[T: BaseModel](model: type[T], what: str, data: Any) -> T:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise DarkroomError(
                ErrorCode.PS_COMMAND_FAILED,
                f"Photoshop returned an unexpected result for '{what}'.",
                details={"problems": [e["msg"] for e in exc.errors()]},
            ) from exc

    def _script_json(self, jsx_name: str, path: Path) -> Any:
        text = self.run_script(read_jsx(jsx_name), document=path)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise DarkroomError(
                ErrorCode.PS_COMMAND_FAILED,
                f"The Photoshop script {jsx_name} did not return valid JSON.",
            ) from exc

    # -- operations ---------------------------------------------------------------------------

    def connect(self) -> PhotoshopInfo:
        """First contact. Uses the long start deadline because Photoshop may have to launch.

        If Photoshop is on screen but does not answer for `ready_grace_s` seconds (a start,
        sign-in or other blocking screen), this fails early with a clear error instead of
        waiting for the whole start deadline.
        """
        watchdog = StartWatchdog(self._ready_grace_s, list_windows=self._list_windows)
        data = self._run("info", {}, timeout_s=self._start_timeout_s, watchdog=watchdog)
        return self._typed(PhotoshopInfo, "info", data)

    def info(self) -> PhotoshopInfo:
        return self._typed(PhotoshopInfo, "info", self._call("info"))

    def check_version(self) -> PhotoshopInfo:
        """Return app info, or raise ADOBE_VERSION_UNSUPPORTED for an untested version."""
        info = self.info()
        major_minor = ".".join(info.version.split(".")[:2])
        if major_minor not in self._tested_versions:
            tested = ", ".join(self._tested_versions)
            raise DarkroomError(
                ErrorCode.ADOBE_VERSION_UNSUPPORTED,
                f"Photoshop {info.version} detected; tested with: {tested}.",
                details={"detected": info.version, "tested": list(self._tested_versions)},
            )
        return info

    def prepare_copy(self, source: Path) -> Path:
        """Copy `source` into the work folder (new name if needed) and return the copy's path."""
        if not source.is_file():
            raise DarkroomError(ErrorCode.PS_COMMAND_FAILED, f"Source file not found: {source}")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(self._work_dir / f"{source.stem}_copy{source.suffix}")
        shutil.copy2(source, target)
        return target

    def open_document(self, path: Path) -> DocumentInfo:
        resolved = self._inside_work_dir(path)
        self._call("open_document", path=str(resolved))
        return self.document_info(resolved)

    def run_script(
        self,
        code: str,
        *,
        document: Path | None = None,
        args: Sequence[str] | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """Run ExtendScript and return its result as text. `args` become the script's arguments."""
        kwargs: dict[str, Any] = {"code": code}
        if args:
            kwargs["args"] = [str(value) for value in args]
        if document is not None:
            kwargs["path"] = str(self._inside_work_dir(document))
        return str(self._call("run_script", timeout_s=timeout_s, **kwargs).get("result", ""))

    def document_info(self, path: Path) -> DocumentInfo:
        return self._typed(
            DocumentInfo, "document_info", self._script_json("document_info.jsx", path)
        )

    def action_manager_info(self, path: Path) -> ActionManagerInfo:
        return self._typed(
            ActionManagerInfo,
            "action_manager_info",
            self._script_json("am_document_info.jsx", path),
        )

    def add_layer(self, path: Path, name: str) -> int:
        """Add an empty layer; returns the new number of layers."""
        resolved = self._inside_work_dir(path)
        return int(self._call("add_layer", path=str(resolved), name=name)["layers"])

    def save_psd(self, path: Path, out_path: Path | None = None) -> Path:
        """Save a layered PSD next to the working copy. Never overwrites: a new name is chosen."""
        resolved = self._inside_work_dir(path)
        wanted = out_path or resolved.with_name(f"{resolved.stem}_darkroom.psd")
        target = unique_path(self._inside_work_dir(wanted))
        self._call("save_psd", path=str(resolved), out_path=str(target))
        return target

    def close_document(self, path: Path) -> None:
        """Close the document without saving it."""
        self._call("close_document", path=str(self._inside_work_dir(path)))

    def apply_mask_from_png(self, path: Path, mask_png: Path, layer_name: str = "subject") -> None:
        """Make the bottom layer a normal layer and mask it with a same-size grayscale PNG.

        White keeps the picture, black hides it. The clipboard is not used.
        """
        mask = self._inside_work_dir(mask_png)
        self.run_script(read_jsx("apply_mask.jsx"), document=path, args=[str(mask), layer_name])

    def add_fill_layer_below(
        self, path: Path, rgb: tuple[int, int, int], name: str = "background"
    ) -> None:
        """Add a new bottom layer filled with one colour (the new background)."""
        if not all(0 <= value <= 255 for value in rgb):
            raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, f"Colour values must be 0-255: {rgb}")
        self.run_script(
            read_jsx("add_fill_layer_below.jsx"),
            document=path,
            args=[name, str(rgb[0]), str(rgb[1]), str(rgb[2])],
        )

    def add_image_layer_below(self, path: Path, image: Path, name: str = "background") -> None:
        """Add a picture (a working copy inside the work folder) as the new bottom layer.

        The picture is scaled to cover the document and centre-cropped.
        """
        source = self._inside_work_dir(image)
        self.run_script(
            read_jsx("add_image_layer_below.jsx"), document=path, args=[str(source), name]
        )

    def layers(self, path: Path) -> tuple[LayerInfo, ...]:
        """Layers from top to bottom, with whether each has a layer mask."""
        data = self._script_json("layers_info.jsx", path)
        if not isinstance(data, list):
            raise DarkroomError(ErrorCode.PS_COMMAND_FAILED, "Unexpected layer list.")
        return tuple(self._typed(LayerInfo, "layers", item) for item in data)

    def save_jpg(self, path: Path, out_path: Path | None = None, quality: int = 10) -> Path:
        """Save a flattened JPEG next to the working copy. Never overwrites (new name chosen)."""
        if not 0 <= quality <= 12:
            raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "JPEG quality must be 0-12.")
        resolved = self._inside_work_dir(path)
        wanted = out_path or resolved.with_name(f"{resolved.stem}_darkroom.jpg")
        target = unique_path(self._inside_work_dir(wanted))
        self._call("save_jpg", path=str(resolved), out_path=str(target), quality=quality)
        return target

    def select_subject_mode(self) -> str:
        """Photoshop's Select Subject mode, e.g. "imageProcessingModeDevice" (read-only)."""
        return self.run_script(read_jsx("select_subject_mode.jsx")).strip()

    def select_subject_mask(self, path: Path, out_png: Path) -> str:
        """Run Select Subject on the open working copy and write the selection as a mask PNG.

        The script refuses to run unless the processing mode is Device. The document is changed
        and must be closed without saving. Returns the mode that was in force.
        """
        target = self._inside_work_dir(out_png)
        if target.exists():
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED, f"Refusing to overwrite an existing file: {target}"
            )
        try:
            return self.run_script(
                read_jsx("select_subject_mask.jsx"),
                document=path,
                args=[str(target)],
                timeout_s=self._select_subject_timeout_s,
            ).strip()
        except DarkroomError as exc:
            if "SELECT_SUBJECT_NOT_DEVICE" in exc.message:
                raise DarkroomError(
                    ErrorCode.GUARDRAIL_REJECTED,
                    "Photoshop's Select Subject mode is not Device. Refused: no uploads allowed.",
                    next_step="Set Preferences > Image Processing > 'Select Subject and Remove "
                    "Background' to Device in Photoshop, then retry.",
                    details=exc.details,
                ) from exc
            if "SELECT_SUBJECT_EMPTY" in exc.message:
                raise DarkroomError(
                    ErrorCode.MASK_FAILED,
                    "Photoshop's Select Subject found no subject in this picture.",
                    details=exc.details,
                ) from exc
            raise
