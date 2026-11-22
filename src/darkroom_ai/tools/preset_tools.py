"""Presets: try several on a photo and measure each, and keep local notes on what each is good for.

`try_presets` applies presets one at a time to the working copy, measures every result on a real
render, and puts the photo back exactly as it was. `get_preset_notes` / `save_preset_note` keep the
agent's own knowledge of the presets (what each does, when it fits, when to avoid it) in a small
JSON file under `paths.notes_dir`, so the knowledge builds up across sessions.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import ImageAnalysis, analyze
from darkroom_ai.imaging.io import load_image
from darkroom_ai.imaging.region import read_region
from darkroom_ai.tools.context import (
    Outcome,
    ToolContext,
    ToolResult,
    resolve_copy,
    working_copy,
)
from darkroom_ai.tools.develop_parameters import DEVELOP_PARAMETERS
from darkroom_ai.tools.lightroom_tools import ApplyPresetInput, apply_preset

TRIAL_LONG_EDGE = 768
MAX_TRIALS = 4
NOTES_FILE = "preset_notes.json"
MAX_NOTES = 500
UNNOTED_SAMPLE = 30

NUMBERS = (
    "lightness",
    "chroma",
    "mean_luma",
    "p05",
    "p95",
    "tone_spread",
    "highlights_clipped_pct",
    "shadows_clipped_pct",
    "blue_yellow",
    "green_magenta",
    "noise_luma",
)

SPECIAL_GROUPS = {
    "MaskGroupBasedCorrections": "local adjustments (masks)",
    "ToneCurvePV2012": "tone curve",
    "ToneCurvePV2012Red": "tone curve",
    "ToneCurvePV2012Green": "tone curve",
    "ToneCurvePV2012Blue": "tone curve",
}


def _group(key: str) -> str:
    if key in SPECIAL_GROUPS:
        return SPECIAL_GROUPS[key]
    entry = DEVELOP_PARAMETERS.get(key)
    return entry[0] if entry else "other"


def _numbers(analysis: ImageAnalysis, pixels: Any) -> dict[str, float | None]:
    frame = read_region(pixels, "frame", [0.0, 0.0, 1.0, 1.0])
    cast = analysis.color_cast
    return {
        "lightness": frame.lightness,
        "chroma": frame.chroma,
        "mean_luma": analysis.tones.mean,
        "p05": analysis.tones.p05,
        "p95": analysis.tones.p95,
        "tone_spread": round(analysis.tones.p95 - analysis.tones.p05, 4),
        "highlights_clipped_pct": analysis.clipping.highlights_pct,
        "shadows_clipped_pct": analysis.clipping.shadows_pct,
        "blue_yellow": cast.blue_yellow if cast.measurable else None,
        "green_magenta": cast.green_magenta if cast.measurable else None,
        "noise_luma": analysis.detail.noise_luma,
    }


def _render_numbers(ctx: ToolContext, photo_id: str, label: str) -> tuple[dict[str, Any], str]:
    preview = ctx.lightroom.export_preview(
        photo_id, f"{label}-{uuid.uuid4().hex[:10]}", TRIAL_LONG_EDGE
    )
    pixels = load_image(preview.path)
    return _numbers(analyze(pixels), pixels), str(preview.path)


# -- get_preset -----------------------------------------------------------------------------------

FRAMING_KEYS = frozenset(
    {"CropLeft", "CropTop", "CropRight", "CropBottom", "CropAngle", "HasCrop", "ProcessVersion"}
)
MAX_SHOWN_PER_GROUP = 40


class GetPresetInput(BaseModel):
    """Read what one develop preset contains WITHOUT applying it: every setting it would write,
    grouped by panel (tone, colour, detail, effects, curves, masks...), with values for plain
    settings and a short description for big ones (curves, masks). Give photo_id to also see, for
    each setting, what the photo has now. Use it to learn what a preset really does before
    choosing it.
    Changes nothing."""

    model_config = ConfigDict(extra="forbid")

    preset_name: str = Field(min_length=1, description="Exact name from list_presets.")
    folder: str | None = Field(default=None, description="Folder, if the name is in several.")
    photo_id: str | None = Field(
        default=None, description="Optional: compare with this photo's current values."
    )


class PresetSettingLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    setting: str
    preset_value: Any
    photo_value: Any = None


class GetPresetResult(ToolResult):
    name: str
    folder: str
    total_settings: int
    groups: dict[str, tuple[PresetSettingLine, ...]]
    touches_framing: tuple[str, ...]
    masks: tuple[str, ...]
    truncated: bool
    note: str = (
        "These are the settings the preset would write (Lightroom's own keys). A plain number is "
        "the new value; compare with photo_value when given. apply_preset also refuses presets "
        "that change framing and takes back ones that go far beyond the limits."
    )


def _describe(value: Any) -> Any:
    if isinstance(value, dict):
        return f"<table with {len(value)} entries>"
    if isinstance(value, list | tuple):
        return f"<list with {len(value)} entries>"
    return value


def _mask_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                label = item.get("CorrectionName") or item.get("What") or "unnamed mask"
                names.append(str(label))
    return names


def _find_preset(ctx: ToolContext, name: str, folder: str | None) -> Any:
    wanted = name.strip().lower()
    presets = [p for p in ctx.lightroom.list_presets() if p.name.strip().lower() == wanted]
    if folder is not None:
        presets = [p for p in presets if p.folder.strip().lower() == folder.strip().lower()]
    if not presets:
        similar = sorted(p.name for p in ctx.lightroom.list_presets() if wanted in p.name.lower())
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"There is no develop preset named '{name}'.",
            next_step="Use the exact name from list_presets."
            + (f" Similar names: {', '.join(similar[:8])}." if similar else ""),
        )
    if len(presets) > 1:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Several presets are named '{name}' (folders: "
            f"{', '.join(sorted(p.folder for p in presets))}).",
            next_step="Give the folder as well.",
        )
    return presets[0]


def get_preset(ctx: ToolContext, args: GetPresetInput) -> Outcome:
    preset = _find_preset(ctx, args.preset_name, args.folder)
    settings = ctx.lightroom.get_preset_settings(preset.uuid)
    current: dict[str, Any] = {}
    if args.photo_id is not None:
        photo = resolve_copy(ctx, args.photo_id)
        current = ctx.lightroom.get_develop_settings(photo.id, sorted(settings))
    groups: dict[str, list[PresetSettingLine]] = {}
    for key in sorted(settings):
        groups.setdefault(_group(key), []).append(
            PresetSettingLine(
                setting=key,
                preset_value=_describe(settings[key]),
                photo_value=_describe(current.get(key)) if args.photo_id is not None else None,
            )
        )
    truncated = any(len(lines) > MAX_SHOWN_PER_GROUP for lines in groups.values())
    result = GetPresetResult(
        name=preset.name,
        folder=preset.folder,
        total_settings=len(settings),
        groups={g: tuple(lines[:MAX_SHOWN_PER_GROUP]) for g, lines in sorted(groups.items())},
        touches_framing=tuple(sorted(FRAMING_KEYS & set(settings))),
        masks=tuple(_mask_names(settings.get("MaskGroupBasedCorrections"))),
        truncated=truncated,
    )
    return Outcome(result, applied={"preset": preset.name, "settings": len(settings)})


# -- try_presets ----------------------------------------------------------------------------------


class TryPresetsInput(BaseModel):
    """Try up to 4 develop presets on the photo's working copy, one at a time: each is applied,
    measured on a real render (tones, clipping, colour, noise, chroma) and a preview of it is
    saved for you to look at, then the photo is put back exactly as it was. Use it to compare
    candidates before recommending one. Nothing stays changed; apply the chosen preset with
    apply_preset after the person agrees. A preset that changes framing or goes beyond the limits is
    reported as refused."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")
    preset_names: list[str] = Field(
        min_length=1,
        max_length=MAX_TRIALS,
        description="Exact preset names from list_presets (a name in several folders is refused).",
    )


class PresetTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    preset: str
    status: Literal["measured", "refused", "no_change"]
    changed_count: int = 0
    changed_groups: dict[str, int] = Field(default_factory=dict)
    numbers: dict[str, Any] = Field(default_factory=dict)
    change_from_original: dict[str, float] = Field(default_factory=dict)
    preview_path: str | None = None
    note: str = ""


class TryPresetsResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    original_numbers: dict[str, Any]
    original_preview_path: str
    trials: tuple[PresetTrial, ...]
    restored: bool
    note: str = (
        "Numbers are from real renders; 'change_from_original' is trial minus original. Look at "
        "each preview_path next to original_preview_path (skin, highlights, key colours) "
        "before recommending. Nothing was left applied."
    )


def _delta(trial: dict[str, Any], base: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in NUMBERS:
        a, b = trial.get(key), base.get(key)
        if isinstance(a, int | float) and isinstance(b, int | float):
            out[key] = round(float(a) - float(b), 4)
    return out


def try_presets(ctx: ToolContext, args: TryPresetsInput) -> Outcome:
    if len({n.strip().lower() for n in args.preset_names}) != len(args.preset_names):
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "The same preset is listed twice.",
            next_step="List each preset once.",
        )
    copy, created = working_copy(ctx, args.photo_id)
    keys: set[str] = set()
    original, original_preview = _render_numbers(ctx, copy.id, "preset-original")
    trials: list[PresetTrial] = []
    restored_ok = True
    for name in args.preset_names:
        try:
            outcome = apply_preset(ctx, ApplyPresetInput(photo_id=copy.id, preset_name=name))
        except DarkroomError as exc:
            if exc.code is not ErrorCode.GUARDRAIL_REJECTED:
                raise
            trials.append(PresetTrial(preset=name, status="refused", note=exc.message))
            continue
        result = outcome.result.model_dump()
        restore = dict((outcome.undo or {}).get("settings") or {})
        before_values = {k: v for k, v in restore.items()}
        keys.update(restore)
        try:
            if result["changed_count"] == 0:
                trials.append(
                    PresetTrial(preset=name, status="no_change", note=str(result.get("note", "")))
                )
                continue
            numbers, preview = _render_numbers(ctx, copy.id, "preset-trial")
            groups: dict[str, int] = {}
            for key in result["changed"]:
                groups[_group(key)] = groups.get(_group(key), 0) + 1
            trials.append(
                PresetTrial(
                    preset=name,
                    status="measured",
                    changed_count=int(result["changed_count"]),
                    changed_groups=dict(sorted(groups.items())),
                    numbers=numbers,
                    change_from_original=_delta(numbers, original),
                    preview_path=preview,
                )
            )
        finally:
            if restore:
                ctx.lightroom.apply_develop_settings(
                    copy.id, restore, "darkroom_ai: preset trial taken back"
                )
                now = ctx.lightroom.get_develop_settings(copy.id, sorted(restore))
                if any(
                    now.get(k) != v for k, v in before_values.items() if not isinstance(v, list)
                ):
                    restored_ok = False
    if not restored_ok:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "A preset trial could not be fully taken back; the working copy may still carry some "
            "of its settings.",
            next_step="Check the working copy's Develop panels; use undo or Lightroom's history.",
            details={"photo_id": copy.id},
        )
    result_model = TryPresetsResult(
        photo_id=copy.id,
        working_copy_created=created,
        original_numbers=original,
        original_preview_path=original_preview,
        trials=tuple(trials),
        restored=True,
    )
    return Outcome(
        result_model,
        applied={
            "photo_id": copy.id,
            "tried": [t.preset for t in trials],
            "measured": [t.preset for t in trials if t.status == "measured"],
        },
    )


# -- local preset notes ---------------------------------------------------------------------------


class PresetNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    folder: str = ""
    character: list[str] = Field(default_factory=list, description="Short tags: warm, matte, ...")
    best_for: list[str] = Field(default_factory=list, description="Subjects, light, genres.")
    avoid_for: list[str] = Field(default_factory=list, description="Where it goes wrong.")
    strength: Literal["subtle", "moderate", "strong", "unknown"] = "unknown"
    comment: str = Field(default="", max_length=600)
    evidence: dict[str, float] = Field(
        default_factory=dict, description="Measured change from try_presets, if any."
    )
    updated: str = ""


def _notes_path(ctx: ToolContext) -> Path:
    return ctx.settings.paths.notes_dir.resolve() / NOTES_FILE


def _load_notes(ctx: ToolContext) -> dict[str, PresetNote]:
    path = _notes_path(ctx)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {key: PresetNote.model_validate(value) for key, value in raw.items()}
    except (ValueError, OSError) as exc:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"The preset notes file could not be read ({exc}). Nothing was changed.",
            next_step=f"Fix or move {path}; it is plain JSON.",
        ) from exc


def _save_notes(ctx: ToolContext, notes: dict[str, PresetNote]) -> Path:
    path = _notes_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    payload = {key: note.model_dump() for key, note in sorted(notes.items())}
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
    return path


def _key(name: str, folder: str) -> str:
    return f"{folder.strip().lower()}/{name.strip().lower()}"


class GetPresetNotesInput(BaseModel):
    """Read the agent's own notes on presets (character, what each is best for, where to avoid it)
    and see which of Lightroom's presets have no note yet. Changes nothing in Lightroom."""

    model_config = ConfigDict(extra="forbid")

    contains: str | None = Field(
        default=None, description="Only notes whose name, folder, tags or comment contain this."
    )
    limit: int = Field(default=50, ge=1, le=200)


class GetPresetNotesResult(ToolResult):
    notes: tuple[PresetNote, ...]
    matching: int
    presets_in_lightroom: int
    without_notes: int
    without_notes_sample: tuple[str, ...]
    notes_file: str
    note: str = ""


def get_preset_notes(ctx: ToolContext, args: GetPresetNotesInput) -> Outcome:
    notes = _load_notes(ctx)
    wanted = (args.contains or "").strip().lower()

    def hit(note: PresetNote) -> bool:
        text = " ".join(
            [note.name, note.folder, note.comment, *note.character, *note.best_for, *note.avoid_for]
        ).lower()
        return not wanted or wanted in text

    matching = sorted((n for n in notes.values() if hit(n)), key=lambda n: (n.folder, n.name))
    presets = ctx.lightroom.list_presets()
    missing = sorted(
        f"{p.folder}/{p.name}" if p.folder else p.name
        for p in presets
        if _key(p.name, p.folder) not in notes
    )
    result = GetPresetNotesResult(
        notes=tuple(matching[: args.limit]),
        matching=len(matching),
        presets_in_lightroom=len(presets),
        without_notes=len(missing),
        without_notes_sample=tuple(missing[:UNNOTED_SAMPLE]),
        notes_file=str(_notes_path(ctx)),
        note="Notes are the agent's own, written from names and measured trials; treat them as "
        "experience to check against the photo, not as facts.",
    )
    return Outcome(result, applied={"notes": len(matching), "without_notes": len(missing)})


class SavePresetNoteInput(BaseModel):
    """Save or update the agent's note about one Lightroom preset (its character, what it suits,
    where it goes wrong). Keep it short and base it on the preset's name and folder or on a
    try_presets measurement. Writes only a small local notes file, never Lightroom."""

    model_config = ConfigDict(extra="forbid")

    preset_name: str = Field(min_length=1, description="Exact name from list_presets.")
    folder: str | None = Field(
        default=None, description="Folder, needed when the name exists in several folders."
    )
    character: list[str] = Field(default_factory=list, max_length=8)
    best_for: list[str] = Field(default_factory=list, max_length=8)
    avoid_for: list[str] = Field(default_factory=list, max_length=8)
    strength: Literal["subtle", "moderate", "strong", "unknown"] = "unknown"
    comment: str = Field(default="", max_length=600)
    evidence: dict[str, float] = Field(default_factory=dict, max_length=12)


class SavePresetNoteResult(ToolResult):
    saved: PresetNote
    notes_total: int
    notes_file: str


def save_preset_note(ctx: ToolContext, args: SavePresetNoteInput) -> Outcome:
    presets = ctx.lightroom.list_presets()
    wanted = args.preset_name.strip().lower()
    matches = [p for p in presets if p.name.strip().lower() == wanted]
    if args.folder is not None:
        matches = [p for p in matches if p.folder.strip().lower() == args.folder.strip().lower()]
    if not matches:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"There is no preset named '{args.preset_name}' in Lightroom. No note was saved.",
            next_step="Use the exact name (and folder) from list_presets.",
        )
    if len(matches) > 1:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"'{args.preset_name}' exists in several folders: "
            f"{', '.join(sorted(p.folder for p in matches))}. No note was saved.",
            next_step="Give the folder as well.",
        )
    preset = matches[0]
    notes = _load_notes(ctx)
    key = _key(preset.name, preset.folder)
    if key not in notes and len(notes) >= MAX_NOTES:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"The notes file already holds {MAX_NOTES} presets.",
            next_step="Ask the person which notes to drop; edit the JSON file by hand.",
        )
    note = PresetNote(
        name=preset.name,
        folder=preset.folder,
        character=[c.strip() for c in args.character if c.strip()],
        best_for=[c.strip() for c in args.best_for if c.strip()],
        avoid_for=[c.strip() for c in args.avoid_for if c.strip()],
        strength=args.strength,
        comment=args.comment.strip(),
        evidence=dict(args.evidence),
        updated=time.strftime("%Y-%m-%d"),
    )
    notes[key] = note
    path = _save_notes(ctx, notes)
    result = SavePresetNoteResult(saved=note, notes_total=len(notes), notes_file=str(path))
    return Outcome(result, applied={"preset": preset.name, "folder": preset.folder})
