"""Lightroom develop parameters the model may set directly, with their ranges.

This is DATA, not editing logic: it says which settings exist and what values Lightroom accepts.
Nothing here decides what an edit should be. Exposure, tone, white balance and HSL are not listed:
they have their own tools with tighter, configurable limits (adjust_lighting, adjust_white_balance,
adjust_hsl). Ranges follow Lightroom Classic's slider ranges; a value outside a range is clamped.
Each entry: key -> (panel, minimum, maximum, note).
"""

from __future__ import annotations

from darkroom_ai.config import GuardrailSettings
from darkroom_ai.safety.guardrails import Limit

# Share of a parameter's full range that one call may move it (a safety bound, not a decision).
PER_PASS_SHARE = 0.25

DEVELOP_PARAMETERS: dict[str, tuple[str, float, float, str]] = {
    # Presence
    "Texture": ("basic", -100, 100, ""),
    "Clarity2012": ("basic", -100, 100, ""),
    "Dehaze": ("basic", -100, 100, ""),
    "Vibrance": ("basic", -100, 100, ""),
    "Saturation": ("basic", -100, 100, ""),
    # Detail: sharpening and noise reduction
    "Sharpness": ("detail", 0, 150, "amount"),
    "SharpenRadius": ("detail", 0.5, 3.0, ""),
    "SharpenDetail": ("detail", 0, 100, ""),
    "SharpenEdgeMasking": ("detail", 0, 100, ""),
    "LuminanceSmoothing": ("detail", 0, 100, "luminance noise reduction"),
    "LuminanceNoiseReductionDetail": ("detail", 0, 100, ""),
    "LuminanceNoiseReductionContrast": ("detail", 0, 100, ""),
    "ColorNoiseReduction": ("detail", 0, 100, ""),
    "ColorNoiseReductionDetail": ("detail", 0, 100, ""),
    "ColorNoiseReductionSmoothness": ("detail", 0, 100, ""),
    # Lens corrections (1 = on, 0 = off for the toggles)
    "LensProfileEnable": ("lens", 0, 1, "toggle: apply the lens profile"),
    "AutoLateralCA": ("lens", 0, 1, "toggle: remove chromatic aberration"),
    "DefringePurpleAmount": ("lens", 0, 20, ""),
    "DefringeGreenAmount": ("lens", 0, 20, ""),
    "LensManualDistortionAmount": ("lens", -100, 100, ""),
    "VignetteAmount": ("lens", -100, 100, "lens vignetting correction"),
    "VignetteMidpoint": ("lens", 0, 100, ""),
    # Geometry
    "PerspectiveVertical": ("geometry", -100, 100, ""),
    "PerspectiveHorizontal": ("geometry", -100, 100, ""),
    "PerspectiveRotate": ("geometry", -10, 10, ""),
    "PerspectiveScale": ("geometry", 50, 150, ""),
    # Effects
    "PostCropVignetteAmount": ("effects", -100, 100, ""),
    "PostCropVignetteMidpoint": ("effects", 0, 100, ""),
    "PostCropVignetteFeather": ("effects", 0, 100, ""),
    "PostCropVignetteRoundness": ("effects", -100, 100, ""),
    "GrainAmount": ("effects", 0, 100, ""),
    "GrainSize": ("effects", 0, 100, ""),
    "GrainFrequency": ("effects", 0, 100, ""),
    # Colour grading (hue in degrees)
    "SplitToningShadowHue": ("color_grading", 0, 360, ""),
    "SplitToningShadowSaturation": ("color_grading", 0, 100, ""),
    "SplitToningHighlightHue": ("color_grading", 0, 360, ""),
    "SplitToningHighlightSaturation": ("color_grading", 0, 100, ""),
    "SplitToningBalance": ("color_grading", -100, 100, ""),
    "ColorGradeMidtoneHue": ("color_grading", 0, 360, ""),
    "ColorGradeMidtoneSat": ("color_grading", 0, 100, ""),
    "ColorGradeMidtoneLum": ("color_grading", -100, 100, ""),
    "ColorGradeShadowLum": ("color_grading", -100, 100, ""),
    "ColorGradeHighlightLum": ("color_grading", -100, 100, ""),
    "ColorGradeBlending": ("color_grading", 0, 100, ""),
    "ColorGradeGlobalHue": ("color_grading", 0, 360, ""),
    "ColorGradeGlobalSat": ("color_grading", 0, 100, ""),
    "ColorGradeGlobalLum": ("color_grading", -100, 100, ""),
    # Calibration
    "ShadowTint": ("calibration", -100, 100, ""),
    "RedHue": ("calibration", -100, 100, ""),
    "RedSaturation": ("calibration", -100, 100, ""),
    "GreenHue": ("calibration", -100, 100, ""),
    "GreenSaturation": ("calibration", -100, 100, ""),
    "BlueHue": ("calibration", -100, 100, ""),
    "BlueSaturation": ("calibration", -100, 100, ""),
}

# Text settings that can be READ (get_develop_parameters) but are not set through
# set_develop_parameters. They tell whether Lightroom found and used a lens profile.
READ_ONLY_TEXT: dict[str, str] = {
    "LensProfileName": "name of the lens profile Lightroom chose (null if none)",
    "LensProfileSetup": "how the profile was chosen: Default, Auto or Custom",
    "LensProfileFilename": "file name of the lens profile in use",
    "LensProfileIsEmbedded": "true when the file carries its own (embedded) lens correction",
    "ProfileName": "the colour profile selected in Lightroom (for example Adobe Color)",
}

OWN_TOOLS = (
    "Exposure2012, Contrast2012, Highlights2012, Shadows2012, Whites2012, Blacks2012: "
    "adjust_lighting; "
    "Temperature/Tint: adjust_white_balance; hue/saturation/luminance per colour: adjust_hsl; "
    "crop: crop"
)


def parameter_limit(guardrails: GuardrailSettings, key: str) -> Limit:
    """The limit for a listed parameter (`guardrails` is kept so limits can become configurable)."""
    _panel, low, high, note = DEVELOP_PARAMETERS[key]
    del guardrails
    span = float(high) - float(low)
    # A toggle (0 = off, 1 = on) has to be able to switch in one step; a quarter step would leave
    # it at 0.25, which is neither state.
    step = span if note.startswith("toggle") else round(span * PER_PASS_SHARE, 4)
    return Limit(key, key, float(low), float(high), step)
