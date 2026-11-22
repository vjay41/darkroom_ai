---
name: lr-ai-enhance
description: "Use Lightroom's own AI Enhance (Denoise, Raw Details, Super Resolution) and Reflection Removal: when each helps, what it costs, how to check the result and go back. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, enhance, denoise, super-resolution, reflection, knowledge]
    related_skills: [lr-edit-workflow, lr-save-points, lr-auto-correct, lr-delivery-export]
    uses_tools: [enhance, reflection_removal, get_ai_panel_state, create_snapshot, restore_snapshot, get_preview, analyze_photo, undo]
---

# Lightroom AI Enhance and Reflection Removal

These run inside Lightroom on a working copy, need the person's go-ahead, and save a snapshot first. They can take
minutes and load the GPU, so do one photo at a time and say so before starting.

## Choose
- Noisy high-ISO photo: `enhance` with feature `denoise`. Start with a moderate amount; more removes detail.
  Compare noise before and after with `analyze_photo`.
- RAW with soft fine detail: `enhance` with `rawDetails`. Not useful on a JPG.
- Need more pixels for a big print or a hard crop: `enhance` with `superRes` (about four times the pixels). Do this
  last, after the edit is settled.
- Glass glare or window reflections: `reflection_removal`, quality `preview` first, then `standard` or `best` if
  it helps. It may not support every photo; the tool says so.

## Check
Read `get_ai_panel_state` first when unsure what this Lightroom version offers. After a run, look at `get_preview`
and the numbers; the result says whether Lightroom's callback finished. If the picture looks worse, or nothing
changed, use `restore_snapshot` with the snapshot named in the result and tell the person.

## Do not
Run these in bulk without asking, stack Denoise on top of heavy sharpening, or claim success from the tool
answer alone: look at the picture. Checked on this PC: Denoise on a RAW took about half a minute; Reflection Removal at preview quality about 15 seconds; Denoise and Raw Details are not offered for JPG files (Super Resolution is). The reflection panel getter may answer nothing, so judge it from the picture and Lightroom's history.
