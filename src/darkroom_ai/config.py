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

    @field_validator("base_url")
    @classmethod
    def _base_url_is_local(cls, value: str) -> str:
        return validate_local_url(value, what="base_url")


class LlmSettings(_Section):
    active_profile: str
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
    timeout_s: float = Field(default=10.0, gt=0.0, le=600.0)
    # Development and tests refuse to write to any catalog other than this one.
    test_catalog: Path | None = None

    @field_validator("host")
    @classmethod
    def _host_is_local(cls, value: str) -> str:
        return validate_local_host(value, what="lightroom.host")


class PhotoshopSettings(_Section):
    prog_id: str = "Photoshop.Application"
    timeout_s: float = Field(default=30.0, gt=0.0, le=600.0)


class GuardrailSettings(_Section):
    max_passes: int = Field(default=3, ge=1, le=10)
    exposure_ev_per_pass: float = Field(default=1.5, gt=0.0, le=5.0)


class PathSettings(_Section):
    logs_dir: Path = Path("logs")
    outputs_dir: Path = Path("outputs")
    previews_dir: Path = Path("previews")
    models_dir: Path = Path("models")


class Settings(_Section):
    llm: LlmSettings
    lightroom: LightroomSettings = LightroomSettings()
    photoshop: PhotoshopSettings = PhotoshopSettings()
    guardrails: GuardrailSettings = GuardrailSettings()
    paths: PathSettings = PathSettings()


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
