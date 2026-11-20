"""Phase 3 Photoshop spike. Works ONLY on a copy of a test photo inside outputs/photoshop.

Steps: connect (launching Photoshop if needed), open a copy, run ExtendScript, one Action Manager
call, add a layer, save a NEW layered PSD, close without saving. Then the error paths.

Usage:  poetry run python scripts/photoshop_spike.py --photo PATH [--step STEP]
Steps:  not-running | edit | timeout | dialog | compare   (default: edit)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections.abc import Callable
from pathlib import Path

from darkroom_ai.adapters.photoshop.client import PhotoshopClient
from darkroom_ai.adapters.photoshop.worker import ChildProcessRunner
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError, ErrorCode

LAYER_NAME = "darkroom_ai test"
failures: list[str] = []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def timed[T](label: str, action: Callable[[], T]) -> T:
    started = time.monotonic()
    try:
        return action()
    finally:
        print(f"  ({label}: {time.monotonic() - started:.1f} s)")


def require_test_photo(settings: Settings, photo: Path) -> None:
    root = settings.lightroom.test_photos_dir
    if root is None or not photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {photo} is not under the test-photo folder ({root}).")


def step_not_running(settings: Settings) -> None:
    """With auto-start OFF and Photoshop closed, the call must fail fast with PS_NOT_REACHABLE."""
    client = PhotoshopClient(
        work_dir=settings.paths.outputs_dir.resolve() / "photoshop",
        timeout_s=20,
        runner=ChildProcessRunner(settings.photoshop.prog_id, auto_start=False),
    )
    try:
        info = client.info()
        print(f"  Photoshop IS running ({info.version}); close it to test this path.")
        check("not running -> PS_NOT_REACHABLE", False)
    except DarkroomError as exc:
        print(f"  [{exc.code.value}] {exc.message}\n  Next step: {exc.next_step}")
        check(
            "not running -> PS_NOT_REACHABLE",
            exc.code is ErrorCode.PS_NOT_REACHABLE and exc.details.get("reason") == "not_running",
        )


def step_edit(settings: Settings, photo: Path) -> None:
    client = PhotoshopClient.from_settings(settings)
    source_hash = sha256(photo)

    info = timed("connect, may launch Photoshop", client.connect)
    print(f"  app: {info.name} {info.version}, open documents: {info.documents}")
    check("version is a tested one", client.check_version().version == info.version)
    documents_before = info.documents

    copy = client.prepare_copy(photo)
    copy_hash = sha256(copy)
    print(f"  working copy: {copy}")
    check("copy is byte-identical to the test photo", copy_hash == source_hash)

    document = timed("open", lambda: client.open_document(copy))
    print(f"  document (ExtendScript): {document}")
    check("ExtendScript reports the copy", document.name == copy.name and document.layers == 1)

    am = client.action_manager_info(copy)
    print(f"  document (Action Manager): {am}")
    check("Action Manager call returns the same document", am.title == copy.name)

    layers = client.add_layer(copy, LAYER_NAME)
    check("layer added (2 layers)", layers == 2 and client.document_info(copy).layers == 2)

    psd = timed("save PSD", lambda: client.save_psd(copy))
    print(f"  new PSD: {psd} ({psd.stat().st_size} bytes)")
    check("PSD exists beside the copy", psd.is_file() and psd.parent == copy.parent)
    check("PSD starts with the Photoshop signature", psd.read_bytes()[:4] == b"8BPS")
    second = client.save_psd(copy)
    check("a second save gets a new name (no overwrite)", second != psd and second.is_file())

    client.close_document(copy)
    check("document closed, others untouched", client.info().documents == documents_before)
    check("working copy unchanged on disk", sha256(copy) == copy_hash)
    check("test photo unchanged on disk", sha256(photo) == source_hash)


def step_timeout(settings: Settings) -> None:
    """A script that waits 20 s with a 3 s deadline must give PS_NOT_REACHABLE in about 3 s."""
    client = PhotoshopClient.from_settings(settings)
    client.connect()
    started = time.monotonic()
    try:
        client.run_script("$.sleep(20000); 'done'", timeout_s=3)
        check("timeout -> PS_NOT_REACHABLE", False)
    except DarkroomError as exc:
        elapsed = time.monotonic() - started
        print(f"  [{exc.code.value}] {exc.message}  (after {elapsed:.1f} s)")
        check(
            "timeout -> PS_NOT_REACHABLE within 6 s",
            exc.code is ErrorCode.PS_NOT_REACHABLE and elapsed < 6,
        )
    print("  waiting 20 s for Photoshop to finish the sleeping script ...")
    time.sleep(20)
    check("Photoshop answers again afterwards", client.info().version != "")


def step_dialog(settings: Settings) -> None:
    """Run while a modal dialog is open in Photoshop: expect an error, not a hang."""
    client = PhotoshopClient.from_settings(settings)
    started = time.monotonic()
    try:
        info = client.info()
        print(f"  call succeeded ({info.version}) - was a dialog really open?")
        check("modal dialog -> error instead of a hang", False)
    except DarkroomError as exc:
        elapsed = time.monotonic() - started
        print(f"  [{exc.code.value}] {exc.message}  (after {elapsed:.1f} s)")
        print(f"  details: {exc.details}")
        check(
            "modal dialog -> PS_NOT_REACHABLE within the deadline",
            exc.code is ErrorCode.PS_NOT_REACHABLE and elapsed < settings.photoshop.timeout_s + 5,
        )


def step_compare(settings: Settings) -> None:
    """photoshop-python-api (comtypes) vs raw pywin32: attach and read the version with each."""
    import win32com.client

    started = time.monotonic()
    app = win32com.client.Dispatch(settings.photoshop.prog_id)
    print(f"  pywin32: version {app.Version}, {time.monotonic() - started:.2f} s")

    started = time.monotonic()
    try:
        import photoshop.api as ps

        api_app = ps.Application()
        elapsed = time.monotonic() - started
        print(f"  photoshop-python-api: version {api_app.version}, {elapsed:.2f} s")
        check("photoshop-python-api attaches to this Photoshop", str(api_app.version) != "")
    except Exception as exc:
        print(f"  photoshop-python-api failed: {type(exc).__name__}: {exc}")
        check("photoshop-python-api attaches to this Photoshop", False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, default=None, help="test photo (copied, not opened)")
    parser.add_argument(
        "--step",
        default="edit",
        choices=["not-running", "edit", "timeout", "dialog", "compare"],
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    settings = load_config(args.config)
    print(f"step: {args.step}")
    try:
        if args.step == "not-running":
            step_not_running(settings)
        elif args.step == "edit":
            if args.photo is None:
                raise SystemExit("--photo is required for the edit step")
            require_test_photo(settings, args.photo)
            step_edit(settings, args.photo)
        elif args.step == "timeout":
            step_timeout(settings)
        elif args.step == "dialog":
            step_dialog(settings)
        else:
            step_compare(settings)
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        print(f"details: {exc.details}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
