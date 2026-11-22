from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from darkroom_ai.adapters.photoshop import jsx_dir
from darkroom_ai.adapters.photoshop.client import PhotoshopClient, unique_path
from darkroom_ai.adapters.photoshop.masks import PhotoshopSelectSubjectProvider
from darkroom_ai.adapters.photoshop.windows import StartWatchdog, WindowInfo
from darkroom_ai.adapters.photoshop.worker import ChildProcessRunner
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.interfaces import MaskProvider, PhotoshopAdapter


class FakeRunner:
    """OperationRunner that records calls and behaves like a tiny Photoshop."""

    def __init__(self, version: str = "27.8.0") -> None:
        self.version = version
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.watchdogs: list[Any] = []
        self.open: dict[str, int] = {}
        self.layer_lists: dict[str, list[dict[str, Any]]] = {}
        self.masks: list[str] = []
        self.select_subject_mode = "imageProcessingModeDevice"
        self.select_subject_runs = 0
        self.fills: list[tuple[int, ...]] = []

    def __call__(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        timeout_s: float,
        watchdog: Any = None,
    ) -> dict[str, Any]:
        self.calls.append((name, kwargs, timeout_s))
        self.watchdogs.append(watchdog)
        if name == "info":
            return {"name": "Adobe Photoshop", "version": self.version, "documents": len(self.open)}
        if name == "open_document":
            self.open[kwargs["path"]] = 1
            self.layer_lists[kwargs["path"]] = [
                {"name": "Background", "has_mask": False, "is_background": True}
            ]
            return {"name": Path(kwargs["path"]).name}
        if name == "run_script":
            path = kwargs.get("path", "")
            if "autoCutout" in kwargs["code"]:
                if self.select_subject_mode != "imageProcessingModeDevice":
                    raise DarkroomError(
                        ErrorCode.PS_COMMAND_FAILED,
                        "Photoshop could not run 'run_script': SELECT_SUBJECT_NOT_DEVICE: mode is "
                        + self.select_subject_mode,
                    )
                self.select_subject_runs += 1
                mask = np.zeros((40, 60), dtype=np.uint8)
                mask[10:30, 20:40] = 255
                assert cv2.imwrite(kwargs["args"][0], mask)
                return {"result": self.select_subject_mode}
            if "imageProcessingSelectSubjectPrefs" in kwargs["code"]:
                return {"result": self.select_subject_mode}
            if "hasUserMask" in kwargs["code"]:
                return {"result": json.dumps(self.layer_lists[path])}
            if "RvlS" in kwargs["code"]:
                self.layer_lists[path][-1] = {
                    "name": kwargs["args"][1],
                    "has_mask": True,
                    "is_background": False,
                }
                self.masks.append(kwargs["args"][0])
                return {"result": "ok"}
            if "SolidColor" in kwargs["code"]:
                self.layer_lists[path].append(
                    {"name": kwargs["args"][0], "has_mask": False, "is_background": False}
                )
                self.fills.append(tuple(int(v) for v in kwargs["args"][1:]))
                return {"result": "ok"}
            if "executeActionGet" in kwargs["code"]:
                data: dict[str, Any] = {
                    "title": Path(path).name,
                    "document_id": 7,
                    "number_of_layers": self.open[path] - 1,
                    "has_background_layer": True,
                }
            elif "activeDocument" in kwargs["code"]:
                data = {
                    "name": Path(path).name,
                    "width": 6000,
                    "height": 4000,
                    "bits": 8,
                    "mode": "DocumentMode.RGB",
                    "layers": self.open[path],
                }
            else:
                return {"result": "42"}
            return {"result": json.dumps(data)}
        if name == "add_layer":
            self.open[kwargs["path"]] += 1
            return {"layers": self.open[kwargs["path"]], "layer_name": kwargs["name"]}
        if name == "save_psd":
            Path(kwargs["out_path"]).write_bytes(b"8BPS")
            return {"path": kwargs["out_path"], "size": 4}
        if name == "save_jpg":
            Path(kwargs["out_path"]).write_bytes(b"\xff\xd8jpg")
            return {"path": kwargs["out_path"], "size": 5}
        if name == "close_document":
            del self.open[kwargs["path"]]
            return {"closed": True}
        raise AssertionError(f"unexpected operation {name}")


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def client(tmp_path: Path, runner: FakeRunner) -> PhotoshopClient:
    return PhotoshopClient(
        work_dir=tmp_path / "work",
        timeout_s=5,
        start_timeout_s=60,
        list_windows=list,
        runner=runner,
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "originals" / "DSC_0001.jpg"
    path.parent.mkdir()
    path.write_bytes(b"original-bytes")
    return path


def test_client_satisfies_the_adapter_protocol(client: PhotoshopClient) -> None:
    adapter: PhotoshopAdapter = client
    assert adapter.info().version == "27.8.0"


def test_connect_uses_the_long_start_deadline(client: PhotoshopClient, runner: FakeRunner) -> None:
    client.connect()
    client.info()
    assert [timeout for _, _, timeout in runner.calls] == [60, 5]
    assert isinstance(runner.watchdogs[0], StartWatchdog)
    assert runner.watchdogs[1] is None


def test_watchdog_reports_a_silent_photoshop_after_the_grace_period() -> None:
    now = [100.0]
    windows: list[WindowInfo] = []
    watchdog = StartWatchdog(30, list_windows=lambda: windows, clock=lambda: now[0])

    assert watchdog() is None  # Photoshop not on screen yet: keep waiting
    now[0] += 50
    assert watchdog() is None
    windows.append(WindowInfo("Sign in - Adobe", "SomeDialog"))
    assert watchdog() is None  # just appeared
    now[0] += 29
    assert watchdog() is None
    now[0] += 2
    problem = watchdog()
    assert problem is not None
    assert problem.code is ErrorCode.PS_NOT_REACHABLE
    assert problem.details == {"reason": "not_answering", "windows": ["Sign in - Adobe"]}
    assert "Sign in - Adobe" in problem.message
    assert "likely" in problem.message.lower()


def test_worker_stops_early_when_the_watchdog_reports_a_problem() -> None:
    run = ChildProcessRunner("Photoshop.Application")
    calls = [0]

    def watchdog() -> DarkroomError | None:
        calls[0] += 1
        if calls[0] < 2:
            return None
        return DarkroomError(ErrorCode.PS_NOT_REACHABLE, "silent", details={"reason": "x"})

    started = time.monotonic()
    with pytest.raises(DarkroomError) as caught:
        run("diag_sleep", {"seconds": 30}, timeout_s=30, watchdog=watchdog)
    assert caught.value.details == {"reason": "x"}
    assert time.monotonic() - started < 10


def test_layered_edit_on_a_copy(client: PhotoshopClient, source: Path, tmp_path: Path) -> None:
    copy = client.prepare_copy(source)
    assert copy.parent == tmp_path / "work"
    assert copy.name == "DSC_0001_copy.jpg"
    assert copy.read_bytes() == b"original-bytes"

    document = client.open_document(copy)
    assert (document.name, document.layers, document.bits) == ("DSC_0001_copy.jpg", 1, 8)
    assert client.action_manager_info(copy).has_background_layer is True
    assert client.add_layer(copy, "darkroom_ai test") == 2
    assert client.run_script("6 * 7") == "42"

    psd = client.save_psd(copy)
    assert psd == tmp_path / "work" / "DSC_0001_copy_darkroom.psd"
    assert psd.is_file()
    client.close_document(copy)

    # Originals are never touched.
    assert source.read_bytes() == b"original-bytes"
    assert sorted(p.name for p in source.parent.iterdir()) == ["DSC_0001.jpg"]


def test_mask_fill_layer_and_flattened_jpg(
    client: PhotoshopClient, runner: FakeRunner, source: Path, tmp_path: Path
) -> None:
    copy = client.prepare_copy(source)
    client.open_document(copy)
    mask = tmp_path / "work" / "mask.png"
    mask.write_bytes(b"png")

    client.apply_mask_from_png(copy, mask, "subject")
    client.add_fill_layer_below(copy, (200, 30, 30), "background")
    layers = client.layers(copy)
    assert [(layer.name, layer.has_mask) for layer in layers] == [
        ("subject", True),
        ("background", False),
    ]
    assert runner.masks == [str(mask.resolve())]
    assert runner.fills == [(200, 30, 30)]

    jpg = client.save_jpg(copy)
    again = client.save_jpg(copy)
    assert jpg.name == "DSC_0001_copy_darkroom.jpg"
    assert again.name == "DSC_0001_copy_darkroom-2.jpg"


def test_mask_outside_the_work_folder_and_bad_values_are_refused(
    client: PhotoshopClient, runner: FakeRunner, source: Path
) -> None:
    copy = client.prepare_copy(source)
    for action in (
        lambda: client.apply_mask_from_png(copy, source),
        lambda: client.add_fill_layer_below(copy, (300, 0, 0)),
        lambda: client.fill_selection_from_mask(copy, source),
        lambda: client.fill_selection_from_mask(copy, copy, grow_px=99),
        lambda: client.save_jpg(copy, quality=99),
        lambda: client.save_jpg(copy, source.with_suffix(".jpg")),
    ):
        with pytest.raises(DarkroomError) as caught:
            action()
        assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert runner.calls == []


def test_select_subject_provider_writes_a_mask_and_closes_without_saving(
    client: PhotoshopClient, runner: FakeRunner, source: Path, tmp_path: Path
) -> None:
    provider: MaskProvider = PhotoshopSelectSubjectProvider(client)
    out = tmp_path / "masks" / "subject.png"
    result = provider.subject_mask(source, out)

    assert result.provider == "photoshop-select-subject"
    assert result.path == out and out.is_file()
    assert (result.metrics.width, result.metrics.height) == (60, 40)
    assert result.metrics.region_count == 1
    assert runner.select_subject_runs == 1
    assert runner.open == {}  # the working copy was closed
    names = [name for name, _, _ in runner.calls]
    assert names.index("open_document") < names.index("close_document")
    assert "save_psd" not in names and "save_jpg" not in names
    assert source.read_bytes() == b"original-bytes"

    with pytest.raises(DarkroomError) as caught:
        provider.subject_mask(source, out)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED


@pytest.mark.parametrize("mode", ["imageProcessingModeCloud", "somethingNew", ""])
def test_select_subject_is_refused_unless_the_mode_is_device(
    client: PhotoshopClient, runner: FakeRunner, source: Path, tmp_path: Path, mode: str
) -> None:
    runner.select_subject_mode = mode
    with pytest.raises(DarkroomError) as caught:
        PhotoshopSelectSubjectProvider(client).subject_mask(source, tmp_path / "m.png")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "Device" in caught.value.next_step
    assert runner.select_subject_runs == 0
    assert "open_document" not in [name for name, _, _ in runner.calls]  # nothing was opened


def test_select_subject_script_checks_the_mode_itself(
    client: PhotoshopClient, runner: FakeRunner, source: Path
) -> None:
    copy = client.prepare_copy(source)
    client.open_document(copy)
    runner.select_subject_mode = "imageProcessingModeCloud"  # changed after the first check
    with pytest.raises(DarkroomError) as caught:
        client.select_subject_mask(copy, copy.with_name("m.png"))
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert runner.select_subject_runs == 0


def test_select_subject_script_guards_come_before_the_call() -> None:
    code = (jsx_dir() / "select_subject_mask.jsx").read_text(encoding="utf-8")
    assert code.index("SELECT_SUBJECT_NOT_DEVICE") < code.index('stringIDToTypeID("autoCutout")')
    assert code.index("MASK_FILE_EXISTS") < code.index('stringIDToTypeID("autoCutout")')
    assert '"imageProcessingModeDevice"' in code
    assert "DialogModes.NO" in code


def test_second_copy_and_second_psd_get_new_names(client: PhotoshopClient, source: Path) -> None:
    first = client.prepare_copy(source)
    second = client.prepare_copy(source)
    assert second.name == "DSC_0001_copy-2.jpg"
    client.open_document(first)
    one = client.save_psd(first)
    two = client.save_psd(first)
    assert one != two
    assert two.name == "DSC_0001_copy_darkroom-2.psd"


def test_files_outside_the_work_folder_are_refused(
    client: PhotoshopClient, source: Path, runner: FakeRunner
) -> None:
    copy = client.prepare_copy(source)
    for action in (
        lambda: client.open_document(source),
        lambda: client.add_layer(source, "x"),
        lambda: client.close_document(source),
        lambda: client.save_psd(copy, source.with_suffix(".psd")),
        lambda: client.run_script("1", document=source),
    ):
        with pytest.raises(DarkroomError) as caught:
            action()
        assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert runner.calls == []


def test_missing_source_is_reported(client: PhotoshopClient, tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        client.prepare_copy(tmp_path / "nope.jpg")
    assert caught.value.code is ErrorCode.PS_COMMAND_FAILED


def test_untested_version_is_not_refused(tmp_path: Path) -> None:
    client = PhotoshopClient(
        work_dir=tmp_path, list_windows=list, runner=FakeRunner(version="99.1.0")
    )
    assert client.check_version().version == "99.1.0"  # compat.assess_version warns instead


def test_tested_version_passes(client: PhotoshopClient) -> None:
    assert client.check_version().version == "27.8.0"


def test_unique_path(tmp_path: Path) -> None:
    path = tmp_path / "a.psd"
    assert unique_path(path) == path
    path.write_bytes(b"x")
    (tmp_path / "a-2.psd").write_bytes(b"x")
    assert unique_path(path) == tmp_path / "a-3.psd"


def test_jsx_files_exist() -> None:
    names = {p.name for p in jsx_dir().glob("*.jsx")}
    assert {
        "document_info.jsx",
        "am_document_info.jsx",
        "apply_mask.jsx",
        "add_fill_layer_below.jsx",
        "fill_content_aware.jsx",
        "layers_info.jsx",
    } <= names
    assert "contentAware" in (jsx_dir() / "fill_content_aware.jsx").read_text()


def test_worker_deadline_kills_a_stuck_operation() -> None:
    run = ChildProcessRunner("Photoshop.Application")
    started = time.monotonic()
    with pytest.raises(DarkroomError) as caught:
        run("diag_sleep", {"seconds": 30}, timeout_s=1.5)
    elapsed = time.monotonic() - started
    assert caught.value.code is ErrorCode.PS_NOT_REACHABLE
    assert caught.value.details["reason"] == "timeout"
    assert "dialog" in caught.value.message
    assert elapsed < 10


def test_worker_returns_a_result_within_the_deadline() -> None:
    run = ChildProcessRunner("Photoshop.Application")
    assert run("diag_sleep", {"seconds": 0}, timeout_s=30) == {"slept": 0}
