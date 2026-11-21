"""Friendly slider names -> Lightroom develop settings, with their limits.

Verified on Lightroom Classic 15.2.1 (process version 15.4): the Basic-panel sliders are the
`...2012` settings; the legacy `Exposure` key is stored but does not change the picture.
JPG/TIFF white balance uses the relative `IncrementalTemperature` / `IncrementalTint` sliders
(-100..100). RAW white balance uses `Temperature` (Kelvin) and `Tint`; the RAW path is UNTESTED.
"""

from __future__ import annotations

from typing import Literal

from darkroom_ai.config import GuardrailSettings
from darkroom_ai.safety.guardrails import Limit

# Process versions whose Basic panel uses the ...2012 settings (PV 2012 = 6.7 and later).
MIN_PROCESS_VERSION = 6.7
TESTED_PROCESS_VERSIONS = ("15.4",)
RAW_FORMATS = frozenset({"RAW", "DNG"})

HslColor = Literal["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
HSL_COLORS: tuple[str, ...] = (
    "red",
    "orange",
    "yellow",
    "green",
    "aqua",
    "blue",
    "purple",
    "magenta",
)
HSL_PREFIX = {
    "hue": "HueAdjustment",
    "saturation": "SaturationAdjustment",
    "luminance": "LuminanceAdjustment",
}
CROP_KEYS = ("CropLeft", "CropTop", "CropRight", "CropBottom", "CropAngle")


def lighting_limits(guardrails: GuardrailSettings) -> dict[str, Limit]:
    tone = guardrails.tone_per_pass
    return {
        "exposure": Limit(
            "exposure",
            "Exposure2012",
            -5.0,
            5.0,
            guardrails.exposure_ev_per_pass,
            guardrails.exposure_ev_total,
        ),
        "contrast": Limit("contrast", "Contrast2012", -100.0, 100.0, tone),
        "highlights": Limit("highlights", "Highlights2012", -100.0, 100.0, tone),
        "shadows": Limit("shadows", "Shadows2012", -100.0, 100.0, tone),
        "whites": Limit("whites", "Whites2012", -100.0, 100.0, tone),
        "blacks": Limit("blacks", "Blacks2012", -100.0, 100.0, tone),
    }


def white_balance_limits(guardrails: GuardrailSettings, file_format: str) -> dict[str, Limit]:
    if file_format.upper() in RAW_FORMATS:
        return {
            "temperature": Limit(
                "temperature",
                "Temperature",
                2000.0,
                50000.0,
                guardrails.wb_kelvin_per_pass,
                needs_current=True,
            ),
            "tint": Limit(
                "tint", "Tint", -150.0, 150.0, guardrails.wb_tint_raw_per_pass, needs_current=True
            ),
        }
    relative = guardrails.wb_relative_per_pass
    return {
        "temperature": Limit("temperature", "IncrementalTemperature", -100.0, 100.0, relative),
        "tint": Limit("tint", "IncrementalTint", -100.0, 100.0, relative),
    }


def hsl_limit(guardrails: GuardrailSettings, channel: str, color: str) -> Limit:
    key = f"{HSL_PREFIX[channel]}{color.capitalize()}"
    return Limit(f"{channel}_{color}", key, -100.0, 100.0, guardrails.hsl_per_pass)


def capped_keys(guardrails: GuardrailSettings) -> dict[str, Limit]:
    """Settings that have a total limit against the original (checked after applying a preset)."""
    return {
        limit.key: limit
        for limit in lighting_limits(guardrails).values()
        if limit.total is not None
    }
