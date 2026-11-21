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
    "Measured on 2026-11-21 (one photo): with 4.3 GB free on the shared GPU and Photoshop open, "
    "Select Subject showed no slowdown (7.6-8.3 s vs 8.1 s) and the model kept answering."
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


def conflict_warnings(report: GpuReport, *, llm_gpu: int, min_free_mb: int) -> list[str]:
    """Plain warnings about the model server and Photoshop competing for one GPU."""
    warnings: list[str] = []
    if report.error:
        return [report.error]
    model_gpus = report.gpu_indexes_for(MODEL_SERVER_HINTS)
    photoshop_gpus = report.gpu_indexes_for(PHOTOSHOP_HINTS)
    if model_gpus and llm_gpu not in model_gpus:
        warnings.append(
            f"The model server is on GPU {', '.join(map(str, model_gpus))}, not on GPU {llm_gpu} "
            "as configured. Pin it to that GPU in the model server's settings."
        )
    shared = sorted(set(model_gpus) & set(photoshop_gpus))
    for index in shared:
        gpu = next(g for g in report.gpus if g.index == index)
        if gpu.free_mb < min_free_mb:
            warnings.append(
                f"The model server and Photoshop both use GPU {index} ({gpu.name}) and only "
                f"{gpu.free_mb} MB of {gpu.total_mb} MB is free. Photoshop's Select Subject may "
                "be slow or fail; unload the model or move Photoshop to the other GPU "
                "(Photoshop: Preferences > Performance). " + MEASURED
            )
        else:
            warnings.append(
                f"Note: the model server and Photoshop share GPU {index} ({gpu.name}); "
                f"{gpu.free_mb} MB is free, which is above the {min_free_mb} MB threshold. "
                + MEASURED
            )
    return warnings
