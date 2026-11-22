---
name: lr-sync-look
description: "Carry a chosen look from one finished photo to others on purpose (colour grading, detail, effects, and only when justified tone or white balance) with copy_develop_settings, versus giving each photo its own values with auto_correct. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, sync, look, consistency, batch, knowledge]
    related_skills: [lr-auto-correct, lr-style-looks, lr-batch-operations, lr-wedding-western, lr-wedding-indian, lr-travel-blog, lr-event-photography]
    uses_tools: [copy_develop_settings, get_develop_parameters, get_preview, analyze_batch, measure_region, auto_correct, undo_batch]
---

# Syncing a look: the same on purpose

`copy_develop_settings` gives every target the SAME values from one source photo. Use it when sameness is the
goal, not when each photo needs its own treatment (use lr-auto-correct for that).

## Sameness that works
- **Colour grading, HSL, calibration, effects (grain, vignette), detail:** these define a look and mostly
  transfer well between photos taken in the same place and light.
- **Presence (texture, clarity, dehaze, vibrance, saturation):** transfers with care; busy scenes and portraits
  react differently.

## Sameness that fails
- **Exposure and tone** depend on each frame's light. Copy them only between near-identical frames (a burst, a
  tripod series) and check each one.
- **White balance** is right for one light only. Copy it only within a single lighting situation, never across
  indoor and outdoor, and never from RAW to JPEG without checking.
- Crop, geometry and local masks are never copied.

## Process
1. Finish and approve the source photo with the person first.
2. Choose groups on purpose and say which: usually grading, detail and effects, not tone.
3. Pick targets from the same scene or light (`analyze_batch` shows frames that are much darker, warmer or
   noisier than the rest: leave those out or treat them on their own).
4. Show the plan and get the go-ahead. Targets run in small groups, each on its own working copy.
5. Look at the first and last target at a larger size; measure faces (`measure_region`) if skin matters.
6. Then fix each frame's own light with `auto_correct` or the adjust tools; sync the look first, correct the
   light second.
7. `undo_batch` takes the whole sync back.

## Say clearly
Which settings were copied, from which photo, to how many, and which were left out on purpose.
