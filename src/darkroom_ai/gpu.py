"""GPU report from `nvidia-smi` (a local program; no network), and the LM Studio / Photoshop check.

On this Windows setup nvidia-smi does not report memory per process, so a conflict is judged from
each GPU's total used memory plus which programs hold a context on which GPU.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

NVIDIA_SMI = "nvidia-smi"
TIMEOUT_S = 15
MODEL_SERVER_HINTS = ("lm studio", "lmstudio", "lms.exe", "llama")
PHOTOSHOP_HINTS = ("photoshop",)
MEASURED = (
    "Measured on 2026-11-21 (one photo, one model loaded): with 4.3 GB free on a GPU shared "
    "with the model server and Photoshop open, Select Subject showed no slowdown "
    "(7.6-8.3 s vs 8.1 s; about 3.2 GB peak on the other GPU) and the model kept answering."
)


@dataclass(frozen=True)
class GpuInfo:
    index: int
    uuid: str
    name: str
    used_mb: int
    total_mb: int

    @property
    def free_mb(self) -> int:
        return self.total_mb - self.used_mb


@dataclass(frozen=True)
class GpuProcess:
    pid: int
    name: str
    gpu_uuid: str


@dataclass(frozen=True)
class GpuReport:
    gpus: tuple[GpuInfo, ...]
    processes: tuple[GpuProcess, ...]
    error: str = ""

    def gpu_indexes_for(self, hints: tuple[str, ...]) -> tuple[int, ...]:
        """Indexes of the GPUs on which a program whose name matches `hints` holds a context."""
        by_uuid = {gpu.uuid: gpu.index for gpu in self.gpus}
        found = {
            by_uuid[process.gpu_uuid]
            for process in self.processes
            if process.gpu_uuid in by_uuid and any(hint in process.name.lower() for hint in hints)
        }
        return tuple(sorted(found))


def parse_gpus(text: str) -> tuple[GpuInfo, ...]:
    gpus = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5 or not parts[0].isdigit():
            continue
        gpus.append(GpuInfo(int(parts[0]), parts[1], parts[2], int(parts[3]), int(parts[4])))
    return tuple(gpus)


def parse_processes(text: str) -> tuple[GpuProcess, ...]:
    processes = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        processes.append(GpuProcess(int(parts[0]), parts[1], parts[2]))
    return tuple(processes)


def _run(arguments: list[str]) -> str:
    result = subprocess.run(
        [NVIDIA_SMI, *arguments], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    return result.stdout


def read_gpus() -> GpuReport:
    """Ask nvidia-smi for the GPUs and the programs using them. Never raises."""
    try:
        gpus = parse_gpus(
            _run(
                [
                    "--query-gpu=index,uuid,name,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
        )
        processes = parse_processes(
            _run(["--query-compute-apps=pid,process_name,gpu_uuid", "--format=csv,noheader"])
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GpuReport((), (), error=f"nvidia-smi could not be run: {exc}")
    if not gpus:
        return GpuReport((), (), error="nvidia-smi reported no GPUs")
    return GpuReport(gpus, processes)


def placement(report: GpuReport) -> str:
    """One line saying which GPUs the model server and Photoshop are on (information only)."""

    def where(indexes: tuple[int, ...]) -> str:
        return "GPU " + ", ".join(map(str, indexes)) if indexes else "no GPU (not running)"

    return (
        f"model server on {where(report.gpu_indexes_for(MODEL_SERVER_HINTS))}; "
        f"Photoshop on {where(report.gpu_indexes_for(PHOTOSHOP_HINTS))}"
    )


def conflict_warnings(report: GpuReport, *, min_free_mb: int) -> list[str]:
    """Warn only when a GPU that Photoshop holds a context on is short of free memory.

    Where the model server runs is the user's choice and is never a warning by itself.
    """
    if report.error:
        return [report.error]
    warnings: list[str] = []
    model_gpus = report.gpu_indexes_for(MODEL_SERVER_HINTS)
    for index in report.gpu_indexes_for(PHOTOSHOP_HINTS):
        gpu = next(g for g in report.gpus if g.index == index)
        if gpu.free_mb >= min_free_mb:
            continue
        shared = " The model server uses this GPU too." if index in model_gpus else ""
        warnings.append(
            f"Photoshop uses GPU {index} ({gpu.name}) and only {gpu.free_mb} MB of "
            f"{gpu.total_mb} MB is free (threshold {min_free_mb} MB).{shared} Photoshop's Select "
            "Subject may be slow or fail; free memory on this GPU (for example unload a model). "
            + MEASURED
        )
    return warnings
