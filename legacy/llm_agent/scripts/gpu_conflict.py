"""GPU conflict measurement: Photoshop Select Subject while the local model is loaded.

Runs Select Subject twice (cold, warm) on COPIES of a test photo, samples both GPUs' memory
during each run, and then checks that the model still answers a text-only tool call. Stops at
the first error; nothing is retried and no Photoshop setting is changed.

Usage:  poetry run python scripts/gpu_conflict.py --photo PATH
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import threading
import time
from pathlib import Path

from darkroom_ai.adapters.photoshop.client import PhotoshopClient, unique_path
from darkroom_ai.adapters.photoshop.masks import DEVICE_MODE, PhotoshopSelectSubjectProvider
from darkroom_ai.config import ModelProfile, load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.gpu import MODEL_SERVER_HINTS, PHOTOSHOP_HINTS, read_gpus
from darkroom_ai.net.loopback import LocalHttp

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "adjust_lighting",
            "description": "Change exposure (EV, relative).",
            "parameters": {
                "type": "object",
                "properties": {"photo_id": {"type": "string"}, "exposure": {"type": "number"}},
                "required": ["photo_id"],
            },
        },
    }
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def used() -> dict[int, int]:
    return {gpu.index: gpu.used_mb for gpu in read_gpus().gpus}


class Sampler:
    """Records the highest used memory per GPU while running."""

    def __init__(self) -> None:
        self.peak: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for index, value in used().items():
                self.peak[index] = max(self.peak.get(index, 0), value)
            self._stop.wait(0.5)

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=20)


def model_check(profile: ModelProfile) -> None:
    http = LocalHttp(profile.base_url, timeout_s=120)
    payload = {
        "model": profile.model,
        "messages": [
            {"role": "user", "content": "Make photo 'p1' about half a stop brighter. Use the tool."}
        ],
        "temperature": profile.temperature,
        "stream": False,
        "tools": TOOLS,
    }
    try:
        for attempt in (1, 2):
            started = time.monotonic()
            status, data = http.request_json("POST", "chat/completions", payload)
            seconds = time.monotonic() - started
            calls = []
            if status == 200 and isinstance(data, dict):
                message = data["choices"][0]["message"]
                calls = [
                    (c["function"]["name"], c["function"]["arguments"])
                    for c in message.get("tool_calls") or []
                ]
            print(f"model call {attempt}: HTTP {status}, {seconds:.2f} s, calls {calls}")
    finally:
        http.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    args = parser.parse_args()
    settings = load_config(None)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    photo_hash = sha256(args.photo)
    print("GPU used MB before Photoshop:", used())

    try:
        photoshop = PhotoshopClient.from_settings(settings)
        started = time.monotonic()
        info = photoshop.connect()
        print(
            f"photoshop {info.version} reachable after {time.monotonic() - started:.1f} s, "
            f"open documents: {info.documents}"
        )
        mode = photoshop.select_subject_mode()
        print("Select Subject mode:", mode)
        if mode != DEVICE_MODE:
            print("BLOCKED: the mode is not Device. Nothing was run.")
            return 3
        provider = PhotoshopSelectSubjectProvider(photoshop)
        for label in ("cold", "warm"):
            before = used()
            out = unique_path(
                settings.paths.outputs_dir.resolve() / "masks" / f"{args.photo.stem}_subject.png"
            )
            with Sampler() as sampler:
                started = time.monotonic()
                result = provider.subject_mask(args.photo, out)
                elapsed = time.monotonic() - started
            after = used()
            report = read_gpus()
            print(f"-- {label} run: {elapsed:.1f} s (baseline 8.1 s)")
            print("   GPU used MB before:", before, "| peak:", sampler.peak, "| after:", after)
            print(
                "   Photoshop on GPU:",
                report.gpu_indexes_for(PHOTOSHOP_HINTS),
                "| model server on GPU:",
                report.gpu_indexes_for(MODEL_SERVER_HINTS),
            )
            metrics = result.metrics
            print(
                f"   mask {result.path.name}: coverage {metrics.coverage_pct} % "
                f"(baseline 69.937 %), regions {metrics.region_count}, "
                f"specks {metrics.speck_count}, {metrics.width} x {metrics.height}"
            )
        print(
            "mode after:",
            photoshop.select_subject_mode(),
            "| open documents:",
            photoshop.info().documents,
        )
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        print(f"details: {exc.details}", file=sys.stderr)
        print("GPU used MB at the error:", used(), file=sys.stderr)
        return 2

    print("original unchanged on disk:", sha256(args.photo) == photo_hash)
    model_check(settings.llm.active)
    print("GPU used MB at the end:", used())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
