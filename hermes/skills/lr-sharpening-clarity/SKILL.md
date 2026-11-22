---
name: lr-sharpening-clarity
description: "How to judge and improve detail in Lightroom Classic through darkroom_ai: sharpening, texture, clarity and dehaze, global versus local, protecting skin and avoiding halos, and checking with edge numbers and the picture. Process knowledge only; it never says what a value should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, sharpening, clarity, detail, knowledge]
    related_skills: [lr-noise-reduction, lr-masking, lr-tone-curve, lr-genre-knowledge, lr-safe-operations, lr-user-guidance]
    uses_tools: [analyze_photo, get_preview, list_develop_parameters, get_develop_parameters, set_develop_parameters, create_mask, set_mask_adjustments, undo]
---

# Sharpening, texture, clarity: add detail without damage

Detail controls make edges and surfaces more visible. They also amplify noise, halos and skin flaws. Decide what the
photo needs, then use the smallest control that does it.

## What each control is for
- **Sharpening** (amount, radius, detail, edge masking) restores crispness lost to the lens, the sensor filter or
  resizing. It works on fine edges. Edge masking keeps sharpening off flat areas, which protects skin and sky.
- **Texture** boosts or softens fine surface detail (bark, fabric, skin pores) without touching larger tone shapes.
  Negative texture softens skin more gently than blurring.
- **Clarity** adds local contrast in the midtones over a wider area: punchy, gritty, can create halos. Negative
  clarity gives a soft glow.
- **Dehaze** removes or adds atmospheric veil; it changes contrast and colour strongly. Use it small, check clipping.
- `list_develop_parameters` lists these controls and their ranges; local versions exist in masks (`set_mask_adjustments`).

## Decide before you edit
- Is the photo actually soft, or is it motion or focus blur? Sharpening cannot fix blur; say so honestly.
- Where should the eye go? Sharpen and add clarity there (a mask on the subject), and leave out-of-focus areas, sky
  and skin alone.
- Portraits: faces want less clarity and often a little softening; eyes, lashes and hair can take more detail.
  Landscapes and architecture tolerate more. Check lr-genre-knowledge.

## Measure and look
- `analyze_photo` gives `edge_strength` (how strong the strongest edges are) and `noise_luma`. After a pass both are
  measured again in the edit's before/after report: edge strength up and noise only slightly up is good; noise up
  as much as edges means you sharpened the noise, so reduce noise first (lr-noise-reduction) or raise edge masking.
- Numbers are measured at preview size and cannot see halos. Look with `get_preview` (larger `long_edge`) at
  high-contrast edges, skin and skies for bright or dark outlines, crunchy grain and plastic skin.

## Order
1. Fix exposure, tone and colour. 2. Noise reduction. 3. Sharpening and texture. 4. Local clarity or dehaze.
Clarity and dehaze shift tones: re-check clipping with `analyze_photo` after them.

## Keep it safe
- Take small passes with `set_develop_parameters`; each call is limited per pass. Re-measure and look every pass.
- Halos, crunchy skin or colour shifts mean back off: `undo`, then try a smaller change or a mask.
- Save strong effects for masks: a global clarity push rarely suits the whole frame.

## Honest limits
- Preview-scale numbers understate real sharpness; the final judgement is the picture at the size it will be used.
- JPG files already carry camera sharpening; push less than on raw files.
- Taste varies: say what you changed and offer to soften it.
