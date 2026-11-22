"""Configuration: darkroom.toml, optionally overridden by darkroom.local.toml next to it.

Model names live only in the TOML file, never in code. Every address is validated as loopback.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.net.loopback import validate_local_host, validate_local_url

CONFIG_ENV_VAR = "DARKROOM_CONFIG"
DEFAULT_CONFIG_NAME = "darkroom.toml"
LOCAL_OVERRIDE_NAME = "darkroom.local.toml"

ToolMode = Literal["native", "json_schema", "prompt"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelProfile(_Section):
    """One OpenAI-compatible model on this computer."""

    base_url: str
    model: str = Field(min_length=1)
    vision: bool = False
    tool_mode: ToolMode = "prompt"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_image_px: int = Field(default=1024, ge=64, le=4096)
    max_images: int = Field(default=2, ge=0, le=8)
    timeout_s: float = Field(default=120.0, gt=0.0, le=3600.0)
    # How much the model 'thinks' before answering. None = not sent (the model's default).
    # tools: turns that only follow up on a tool result; planning: turns that look at new
    # measurements (the first turn and the turn after each pass).
    reasoning_effort_tools: ReasoningEffort | None = None
    reasoning_effort_planning: ReasoningEffort | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url_is_local(cls, value: str) -> str:
        return validate_local_url(value, what="base_url")


class LlmSettings(_Section):
    active_profile: str
    # True: send a request only when the server reports the model as already loaded, so a
    # request never makes the server load (or swap) a model on demand.
    require_loaded: bool = True
    profiles: dict[str, ModelProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _active_profile_exists(self) -> Self:
        if self.active_profile not in self.profiles:
            known = ", ".join(sorted(self.profiles))
            raise ValueError(
                f"active_profile {self.active_profile!r} is not defined (have: {known})"
            )
        return self

    @property
    def active(self) -> ModelProfile:
        return self.profiles[self.active_profile]


class LightroomSettings(_Section):
    host: str = "127.0.0.1"
    # The plug-in listens on these two localhost ports (requests in, responses out).
    request_port: int = Field(default=58763, ge=1024, le=65535)
    response_port: int = Field(default=58764, ge=1024, le=65535)
    timeout_s: float = Field(default=10.0, gt=0.0, le=600.0)
    export_timeout_s: float = Field(default=120.0, gt=0.0, le=1800.0)
    # Longest wait for the plug-in to accept a connection (it needs about 1 s after a disconnect).
    connect_timeout_s: float = Field(default=20.0, gt=0.0, le=300.0)
    # The plug-in refuses every write unless Lightroom has exactly this catalog open.
    test_catalog: Path | None = None
    # Development edits are only made to photos whose files are under this folder (test copies).
    test_photos_dir: Path | None = None

    @field_validator("host")
    @classmethod
    def _host_is_local(cls, value: str) -> str:
        return validate_local_host(value, what="lightroom.host")


class PhotoshopSettings(_Section):
    prog_id: str = "Photoshop.Application"
    timeout_s: float = Field(default=30.0, gt=0.0, le=600.0)
    # Launch Photoshop through COM when it is not running (it can take a while to start).
    auto_start: bool = True
    start_timeout_s: float = Field(default=120.0, gt=0.0, le=900.0)
    # If Photoshop is on screen but does not answer for this long (start, sign-in or another
    # blocking screen), connecting fails early with a clear error.
    ready_grace_s: float = Field(default=30.0, gt=0.0, le=600.0)
    # Select Subject on the device can take a while on a large image.
    select_subject_timeout_s: float = Field(default=180.0, gt=0.0, le=1800.0)


class AppsSettings(_Section):
    """Where the Adobe programs are installed (used only by ensure_apps_running to start them)."""

    lightroom_exe: str = r"C:\Program Files\Adobe\Adobe Lightroom Classic\Lightroom.exe"
    photoshop_exe: str = r"C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
    lightroom_process: str = "Lightroom.exe"
    photoshop_process: str = "Photoshop.exe"
    # Longest wait for an app to start and answer.
    start_wait_s: float = Field(default=180.0, gt=0.0, le=900.0)


class GuardrailSettings(_Section):
    """Limits for edits. Per-pass limits bound one tool call; totals bound the change against
    the photo's original values. Values beyond a limit are clamped and reported."""

    max_passes: int = Field(default=3, ge=1, le=10)
    # Exposure, in EV.
    exposure_ev_per_pass: float = Field(default=1.5, gt=0.0, le=5.0)
    exposure_ev_total: float = Field(default=3.0, gt=0.0, le=5.0)
    # Contrast, highlights, shadows, whites, blacks (Lightroom scale -100..100).
    tone_per_pass: float = Field(default=50.0, gt=0.0, le=100.0)
    # White balance. JPG/TIFF use Lightroom's relative -100..100 sliders; RAW uses Kelvin.
    # Each has a per-pass limit and a total against the photo's original value; going beyond
    # the total needs an explicit confirmation (same pattern as exposure).
    wb_relative_per_pass: float = Field(default=30.0, gt=0.0, le=100.0)
    wb_relative_total: float = Field(default=60.0, gt=0.0, le=200.0)
    wb_kelvin_per_pass: float = Field(default=1000.0, gt=0.0, le=10000.0)
    wb_kelvin_total: float = Field(default=2000.0, gt=0.0, le=48000.0)
    wb_tint_raw_per_pass: float = Field(default=30.0, gt=0.0, le=150.0)
    wb_tint_raw_total: float = Field(default=40.0, gt=0.0, le=300.0)
    # HSL sliders (Lightroom scale -100..100).
    hsl_per_pass: float = Field(default=30.0, gt=0.0, le=100.0)
    # Tone curve points, on the curve's 0..255 scale: the most one pass may move a point from the
    # current curve, and the most a point may be from a straight line (no curve) in total.
    curve_per_pass: float = Field(default=32.0, gt=0.0, le=255.0)
    curve_total: float = Field(default=64.0, gt=0.0, le=255.0)
    # A crop must keep at least this share of the frame.
    min_crop_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    # Batch writes: photos per call, when a library batch needs the person's go-ahead, and how long
    # a batch may run before it is stopped and reported. A develop batch always needs the go-ahead.
    batch_library_max: int = Field(default=25, ge=1, le=25)
    batch_develop_max: int = Field(default=10, ge=1, le=25)
    batch_library_confirm_above: int = Field(default=5, ge=0, le=25)
    batch_library_budget_s: float = Field(default=120.0, gt=0.0, le=3600.0)
    batch_develop_budget_s: float = Field(default=600.0, gt=0.0, le=7200.0)


class BackgroundSettings(_Section):
    """Where replace_background may take a picture from: this one folder, nothing else."""

    dir: Path = Path("backgrounds")
    max_file_mb: float = Field(default=50.0, gt=0.0, le=1000.0)


MaskBackend = Literal["photoshop", "onnx", "synthetic"]


class MaskSettings(_Section):
    """Subject masks. One interface, three backends (see imaging/providers.py)."""

    backend: MaskBackend = "photoshop"
    # ONNX backend: a model file the user provides. Nothing is downloaded.
    onnx_model: Path | None = None
    gpu_index: int = Field(default=1, ge=0, le=15)
    allow_cpu: bool = False
    onnx_input_width: int = Field(default=1024, ge=32, le=4096)
    onnx_input_height: int = Field(default=1024, ge=32, le=4096)
    onnx_mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    onnx_std: tuple[float, float, float] = (0.229, 0.224, 0.225)


class FaceSettings(_Section):
    """Face detection needs a user-provided YuNet ONNX file; without it face analysis is off."""

    model: Path | None = None
    score_threshold: float = Field(default=0.7, gt=0.0, lt=1.0)


class GpuSettings(_Section):
    """When low free memory on a GPU that Photoshop uses becomes a warning in `doctor`."""

    min_free_mb: int = Field(default=3000, ge=0, le=100000)


class RuntimeSettings(_Section):
    """How the MCP connector behaves when it is running older code than the files on disk."""

    # True: write tools are refused until the connector is restarted (reads keep working).
    refuse_writes_when_stale: bool = True
    # A connector that has run older code than the files on disk for this many seconds, with no tool
    # call running and none for `stale_exit_idle_s`, ends itself so the client starts a fresh one.
    # 0 turns this off (the connector then only refuses writes and says it is stale).
    stale_exit_after_s: int = Field(default=120, ge=0, le=86400)
    stale_exit_idle_s: int = Field(default=30, ge=0, le=86400)


class PathSettings(_Section):
    logs_dir: Path = Path("logs")
    outputs_dir: Path = Path("outputs")
    previews_dir: Path = Path("previews")
    models_dir: Path = Path("models")


PlaybookName = Literal["portrait", "landscape", "none"]


class AgentSettings(_Section):
    """How the agent is briefed. "none" gives the model tools, measurements and limits only."""

    default_playbook: PlaybookName = "portrait"


class Settings(_Section):
    # LEGACY (parked in legacy/llm_agent): the model profiles are only read by the old chat/agent
    # code. darkroom_ai is an MCP tool now and has no model of its own; the section is optional.
    llm: LlmSettings | None = None
    agent: AgentSettings = AgentSettings()
    lightroom: LightroomSettings = LightroomSettings()
    photoshop: PhotoshopSettings = PhotoshopSettings()
    apps: AppsSettings = AppsSettings()
    guardrails: GuardrailSettings = GuardrailSettings()
    masks: MaskSettings = MaskSettings()
    backgrounds: BackgroundSettings = BackgroundSettings()
    gpu: GpuSettings = GpuSettings()
    faces: FaceSettings = FaceSettings()
    paths: PathSettings = PathSettings()
    runtime: RuntimeSettings = RuntimeSettings()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"Config file not found: {path}",
            next_step=f"Create {DEFAULT_CONFIG_NAME} (see the example in the repository) or point "
            f"{CONFIG_ENV_VAR} at your config file.",
            details={"path": str(path)},
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"{path} is not valid TOML: {exc}",
            details={"path": str(path)},
        ) from exc


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def parse_settings(data: dict[str, Any], *, source: str = "configuration") -> Settings:
    """Validate a settings mapping. Raises CONFIG_INVALID or NON_LOCAL_ENDPOINT."""
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in err['loc']) or '(top level)'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"{source} has {len(problems)} problem(s): " + "; ".join(problems),
            details={"source": source, "problems": problems},
        ) from exc


def load_config(path: Path | None = None) -> Settings:
    """Load settings from `path`, else $DARKROOM_CONFIG, else ./darkroom.toml.

    A `darkroom.local.toml` next to the config file is merged on top (and validated the same way).
    """
    config_path = path or Path(os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_NAME)
    data = _read_toml(config_path)
    local_path = config_path.with_name(LOCAL_OVERRIDE_NAME)
    if local_path.is_file():
        data = _merge(data, _read_toml(local_path))
    return parse_settings(data, source=str(config_path))
