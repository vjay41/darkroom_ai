---
name: lr-noise-reduction
description: "How to judge and reduce noise in Lightroom Classic through darkroom_ai: measure first, treat luminance and colour noise separately, protect detail, use local noise reduction for shadows, and check the result with numbers and the picture. Process knowledge only; it never says what a value should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, noise, detail, knowledge]
    related_skills: [lr-sharpening-clarity, lr-masking, lr-safe-operations, lr-user-guidance, lr-batch-operations]
    uses_tools: [analyze_photo, analyze_batch, get_photo_metadata, get_preview, list_develop_parameters, get_develop_parameters, set_develop_parameters, batch_set_develop_parameters, create_mask, set_mask_adjustments, undo]
---

# Noise reduction: measure, treat gently, check

Noise reduction trades noise for detail. The goal is the least treatment that makes the noise stop being the
first thing a viewer sees, not a noise-free picture.

## Measure before touching
- `analyze_photo` returns `detail`: `noise_luma` (brightness noise) and `noise_chroma` (colour speckle), in 8-bit levels
  measured in the flattest areas of the preview, plus `edge_strength` and `flat_fraction`. `get_photo_metadata` gives
  ISO and camera. Numbers are measured at preview size, so they understate noise at full size: use them to compare
  before and after and across photos of one set, never as a verdict.
- `noise_luma` of None means no flat area was found (busy or very dark picture): rely on the picture and say so.
- Look at the preview too: noise hides in shadows, skies and skin. Say where you see it.

## Two kinds, two treatments
- **Colour noise** (coloured speckle, blotches in shadows) is cheap to remove and costs little detail. Treat it first.
- **Luminance noise** (grain) costs detail when removed. Raise it slowly; stop when the grain stops distracting.
- Raw files respond better than JPG: a JPG already had noise reduction in the camera, so push less.
- `list_develop_parameters` shows the detail controls and their ranges; `get_develop_parameters` reads current values.
  Change one family at a time with `set_develop_parameters` (each call has a per-pass step; take small passes).

## Local noise reduction
Noise is usually worst in the shadows and in smooth areas. A mask (`create_mask` luminance or subject, then
`set_mask_adjustments` with the local noise value) treats only that area, so faces, eyes and textured subjects keep detail.
Mask edits render a few seconds late: wait about 5 seconds before measuring again, or the numbers still show the old state.
Prefer a local treatment when only part of the picture is noisy.

## Order and interactions
- Reduce noise before sharpening; sharpening makes noise louder. After noise reduction, re-check the edge numbers.
- Lifting shadows or raising exposure raises noise: re-measure after tone edits, not only before.
- Over-treatment looks like wax: skin loses pores, fine textures turn smooth. If `edge_strength` drops a lot while noise
  barely changes, you are erasing detail, not noise: back off.

## Check after each pass
1. `analyze_photo` again: did the noise numbers go down, and by how much? Did `edge_strength` stay close?
2. `get_preview` (a larger `long_edge` shows more): look at a shadow area, a smooth area and an edge.
3. Report both numbers and what you saw. If worse or unclear, `undo` and say why.

## Many photos
Use `analyze_batch` to triage by `noise_luma` and ISO, treat a pilot photo per group, then roll out in chunks
(see lr-batch-operations). Photos of one ISO can still differ; choose values per photo.

## Honest limits
- No AI denoise is available here, only the classic controls.
- The numbers do not see texture loss in faces or fabric: always look.
- The user's taste decides how much grain stays; ask when it is a style choice.
