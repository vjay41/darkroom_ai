---
name: lr-preset-advisor
description: "How to discover Lightroom presets, learn what each really does, remember it in local notes, and recommend the right preset for a photo or a whole set, trying candidates and agreeing the choice with the user before applying. Process knowledge only; the tools give facts, this skill gives the judgement."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, presets, recommendation, knowledge]
    related_skills: [lr-presets, lr-style-looks, lr-edit-workflow, lr-batch-operations, lr-user-guidance, lr-safe-operations, lr-genre-knowledge]
    uses_tools: [list_presets, get_preset, try_presets, get_preset_notes, save_preset_note, apply_preset, get_preview, analyze_photo, analyze_batch, measure_region, batch_set_develop_parameters, undo, undo_batch]
---

# Presets: learn them once, recommend them well

The tools only report facts: what presets exist (`list_presets`), what one contains (`get_preset`), what it does to a
photo (`try_presets`), and your own notes (`get_preset_notes`, `save_preset_note`). The judgement is yours.

## 1. Discover
`list_presets` shows names and folders; use `contains` to narrow. Folders and names usually say the maker, the
subject or the style (wedding, portrait, film, black and white, travel, food). `get_preset_notes` shows which presets
you already know and lists the ones you have not noted yet. Do not apply anything yet.

## 2. Learn a preset
- Read it with `get_preset` (optionally with a photo id): which panels it touches (tone, colour, curves, grain,
  vignette, masks, black and white, calibration) and whether it changes framing. A preset that only touches colour
  grading is a mood; one that moves exposure and tone is a correction. Many masks mean a heavy, photo-specific look.
- Measure it with `try_presets` on a representative photo: the numbers show what it does to contrast spread,
  shadows and highlights, colour cast, chroma and noise, and the previews show how it looks. Nothing stays applied.
- Write what you learned with `save_preset_note`: character, what it suits, where it fails, strength, and the
  measured change. Keep notes short and honest ("tested on one indoor ceremony photo").

## 3. Recommend
Start from the photo and the intent, not the list:
1. State what the photo needs: `analyze_photo` (exposure, clipping, cast, noise), the genre (see the genre and
   event skills), the person's taste and the destination (album, web, print).
2. Pick two to four candidates from names, folders and notes; read `get_preset` for any you do not know.
3. Compare them with `try_presets` on the actual photo; look at each preview next to the original. Check skin with
   `measure_region`, clipped highlights, noise and any colour the photo depends on.
4. Rank them in words: why this one fits, what it costs (for example skin pushed orange, highlights clipped), and
   what you would tune after applying.
5. Offer your recommendation with the trade-offs and one alternative.

## 4. Agree before applying
Present the choice and ask when: the preset changes the mood noticeably, several candidates are close, the person
has not stated a taste, or you will roll it out to many photos. Applying a single preset to a working copy can be
undone, so it is fine to show it as a preview when asked. For a set, apply to a pilot photo, let the person judge,
then roll out in chunks with `batch_set_develop_parameters` and keep `undo_batch` available.

## 5. After applying
`apply_preset` lists every setting changed: read it. Compare with the original, tune with the normal tools, and tell
the person what you changed on top. If it is worse, `undo`. Update the note with what you learned.

## Honest limits
Names and previews on one photo do not prove a preset suits another. Presets made for other cameras shift
white balance and exposure; always measure. Presets may include masks specific to another photo. You cannot create
or edit presets here.
