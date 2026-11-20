"""Phase 2 Lightroom spike. Runs against the REAL Lightroom plug-in on the configured catalog.

Read-only by default (ping, self-test, probe, selection, settings). With --write it also creates
ONE virtual copy of the selected photo, a snapshot on that copy, changes Exposure on the copy,
exports previews into the preview folder, and reverts. The original photo is never edited.

Usage:  poetry run python scripts/lightroom_spike.py [--write] [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageStat

from darkroom_ai.adapters.lightroom.bridge_file import load_bridge_file
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError, ErrorCode

EXPOSURE_STEP = 0.30
# Observed on Lightroom 15.2.1 (process version 15.4): the Basic-panel Exposure slider is the
# develop setting "Exposure2012". The legacy key "Exposure" is stored but does not change the image.
EXPOSURE_KEY = "Exposure2012"
SNAPSHOT_NAME = "darkroom-before"
SELFTEST_VALUE = {
    "path": "D:\\photos\\new\\tab\there.jpg",
    "quote": 'say "hi"',
    "unicode": "caf\u00e9 \u65e5\u672c",
    "numbers": [0, -1, 1.5, 0.001, 123456789],
    "nested": {"list": [[1, 2], {"a": True}], "flag": False},
}


def mean_brightness(path: Path) -> float:
    with Image.open(path) as image:
        return float(ImageStat.Stat(image.convert("L")).mean[0])


def report(label: str, value: object) -> None:
    print(f"{label}: {value}")


def run(
    client: LightroomClient,
    *,
    write: bool,
    photo_path: Path | None,
    allowed_dir: Path | None,
    reuse_copy_id: str | None = None,
) -> int:
    failures: list[str] = []

    def check(name: str, passed: bool) -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if not passed:
            failures.append(name)

    info = client.ping()
    report("lightroom", info.lightroom_version)
    report("plug-in", info.plugin_version)
    report("open catalog", info.catalog_path)
    report("configured catalog", info.configured_catalog)
    report("writes allowed", info.writes_allowed)

    check("JSON round trip inside Lightroom", client.selftest(SELFTEST_VALUE) == SELFTEST_VALUE)
    report("probe", json.dumps(client.probe(), sort_keys=True))

    # The plug-in serves one client at a time: disconnect, try a wrong token, reconnect.
    client.close()
    time.sleep(1.5)
    intruder = LightroomClient(
        host="127.0.0.1",
        request_port=client._request_port,
        response_port=client._response_port,
        token="0" * 64,
        timeout_s=15,
    )
    try:
        intruder.get_selection()
        check("wrong token is rejected", False)
    except DarkroomError as exc:
        check("wrong token is rejected", exc.details.get("plugin_code") == "UNAUTHORIZED")
    finally:
        intruder.close()
        time.sleep(1.5)

    selection = client.get_selection()
    selected_before = (
        selection.active.id if selection.active else None,
        sorted(p.id for p in selection.photos),
    )
    if photo_path is not None:
        # Address the photo by file path -> catalog ID; the selection is not used.
        found = client.find_photo(str(photo_path))
        if found is None:
            print(f"{photo_path} is not in the open catalog; import it with 'Add' and run again.")
            return 1
        original = found
    elif selection.active is not None:
        original = selection.active
    else:
        print("No photo is selected in Lightroom; select one JPG or pass --photo PATH.")
        return 1
    report("target photo", f"{original.file_name} ({original.file_format}) id={original.id}")
    report("target path", original.path)
    check("target photo is a JPG", original.file_format == "JPG")
    in_test_folder = (
        allowed_dir is not None
        and original.path is not None
        and Path(original.path).resolve().is_relative_to(allowed_dir.resolve())
    )
    check(f"target photo is a test copy under {allowed_dir}", in_test_folder)
    if write and not in_test_folder:
        print("\nRefusing the edit steps: the target photo is not in the test-photo folder.")
        return 1
    original_exposure = client.get_develop_settings(original.id, [EXPOSURE_KEY])
    report("original Exposure", original_exposure)
    report("existing snapshots", [s.name for s in client.list_snapshots(original.id)])

    if not write:
        print("\nRead-only run finished (use --write for the edit steps).")
        return 1 if failures else 0
    if not info.writes_allowed:
        print("\nWrites are not allowed: Lightroom has a different catalog open. Stopping.")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if reuse_copy_id is not None:
        copy = client.get_photo(reuse_copy_id)
        report("reusing virtual copy", f"id={copy.id} copy_name={copy.copy_name}")
    else:
        copy = client.create_virtual_copy(original.id, f"darkroom {stamp}")
        report("virtual copy", f"id={copy.id} copy_name={copy.copy_name}")
        after_copy = client.get_selection()
        check(
            "selection restored after creating the copy",
            (
                after_copy.active.id if after_copy.active else None,
                sorted(p.id for p in after_copy.photos),
            )
            == selected_before,
        )
    check(
        "copy is a virtual copy of the target photo",
        copy.is_virtual_copy and copy.master_id == original.id,
    )

    snapshot = client.create_snapshot(copy.id, SNAPSHOT_NAME)
    report("snapshot", snapshot)
    check("snapshot created", snapshot.snapshot is not None)

    before = client.get_develop_settings(copy.id, [EXPOSURE_KEY])
    preview_before = client.export_preview(copy.id, f"spike-{stamp}-before")
    report("preview before", preview_before.path)

    start_exposure = float(before.get(EXPOSURE_KEY) or 0.0)
    change = client.apply_develop_settings(
        copy.id, {EXPOSURE_KEY: start_exposure + EXPOSURE_STEP}, "darkroom_ai spike"
    )
    report("settings change", change)
    check(
        "Exposure changed on the JPG virtual copy",
        abs(float(change.after.get(EXPOSURE_KEY, 0.0)) - (start_exposure + EXPOSURE_STEP)) < 0.01,
    )
    check(
        "original photo's Exposure is unchanged",
        client.get_develop_settings(original.id, [EXPOSURE_KEY]) == original_exposure,
    )
    preview_edited = client.export_preview(copy.id, f"spike-{stamp}-edited")

    def exposure_is_back() -> bool:
        # Give Lightroom up to 2 s to show the reverted value.
        for _ in range(8):
            current = client.get_develop_settings(copy.id, [EXPOSURE_KEY])
            if abs(float(current.get(EXPOSURE_KEY) or 0.0) - start_exposure) < 0.01:
                return True
            time.sleep(0.25)
        return False

    # The SDK reference does not say which snapshot id applyDevelopSnapshot expects or whether it
    # needs a write gate; try the variants and report the one that actually reverts.
    revert_method = None
    if snapshot.snapshot is not None:
        variants = (("local", True), ("local", False), ("global", True), ("global", False))
        for id_kind, gate in variants:
            label = f"snapshot ({id_kind} id, gate={gate})"
            try:
                client.apply_snapshot(copy.id, snapshot.snapshot.id, id_kind=id_kind, gate=gate)
            except DarkroomError as exc:
                report(label, f"error: {exc.message}")
                continue
            worked = exposure_is_back()
            report(label, "reverted" if worked else "no effect")
            if worked:
                revert_method = label
                break
    check("revert via snapshot restores Exposure", revert_method is not None)
    if revert_method is None:
        client.apply_develop_settings(
            copy.id, {EXPOSURE_KEY: start_exposure}, "darkroom_ai spike revert (journal)"
        )
        revert_method = "journaled settings"
    report("revert method used", revert_method)
    check(f"Exposure is back after revert via {revert_method}", exposure_is_back())
    preview_reverted = client.export_preview(copy.id, f"spike-{stamp}-reverted")

    levels = [mean_brightness(p.path) for p in (preview_before, preview_edited, preview_reverted)]
    report("mean brightness before / edited / reverted", [round(v, 2) for v in levels])
    check("edited preview is brighter", levels[1] > levels[0] + 0.5)
    check("reverted preview matches the first one", abs(levels[2] - levels[0]) < 0.5)
    with Image.open(preview_before.path) as image:
        check("preview long edge is 1024 px or less", max(image.size) <= 1024)

    try:
        client.apply_develop_settings("00000000-0000-0000-0000-000000000000", {EXPOSURE_KEY: 1})
        check("unknown photo id is refused", False)
    except DarkroomError as exc:
        check("unknown photo id is refused", exc.code is ErrorCode.LR_COMMAND_FAILED)

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="also run the edit steps")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--photo", type=Path, default=None, help="file path of the photo to use")
    parser.add_argument(
        "--reuse-copy",
        default=None,
        metavar="ID",
        help="use this existing virtual copy instead of creating another one",
    )
    args = parser.parse_args()
    try:
        settings = load_config(args.config)
        with LightroomClient.from_settings(settings, load_bridge_file()) as client:
            return run(
                client,
                write=args.write,
                photo_path=args.photo,
                allowed_dir=settings.lightroom.test_photos_dir,
                reuse_copy_id=args.reuse_copy,
            )
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
