---
name: lr-edit-workflow
description: "The order of work for a professional-grade edit in Lightroom Classic through darkroom_ai: intent, technical base, colour, tone, local work, detail, consistency across a set, final check. Process only; it never says what a value should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, workflow, professional, knowledge]
    related_skills: [lr-genre-knowledge, lr-style-looks, lr-skin-tone, lr-noise-reduction, lr-sharpening-clarity, lr-batch-operations, lr-safe-operations, lr-user-guidance]
    uses_tools: [auto_correct, plan_crop, get_preview, analyze_photo, analyze_batch, measure_region, crop, get_photo_metadata, adjust_white_balance, adjust_lighting, adjust_hsl, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, apply_preset, undo]
---

# A professional edit, in order

Professionals work from the big, global, technical decisions down to the small, local, taste decisions. Each stage
fixes what the next stage depends on. Skipping ahead means redoing work.

## 0. Intent
Before touching anything: what is this photo for (print, album, web, social, client gallery), who is in it, what
is the moment, and what look does the person want? If the look is unknown, offer two or three directions
(see lr-style-looks) and ask. `get_photo_metadata` tells you the camera, ISO and file type, which set the limits.

## 1. Read the photo
`get_preview` for the picture, `analyze_photo` for tones, clipping, colour cast and noise numbers. Name the
problems in words (flat, dark face, blue cast, busy background) before choosing tools. File type matters: a RAW
file has headroom, a JPEG does not.

## 2. Technical base
- Framing first: straighten and crop because every later judgement depends on the frame (measure the tilt
  and plan the crop: see lr-straighten-crop; `crop` applies it).
- A quick per-photo baseline: Lightroom's own Auto (`auto_correct`, see lr-auto-correct) gives each photo
  values of its own; judge it, do not trust it.
- Lens corrections (see lr-lens-corrections), then white balance (`adjust_white_balance`) judged on something
  neutral or on skin, then overall exposure (`adjust_lighting`).

## 3. Tone
Shape contrast with the basic tone controls first, then a curve (`adjust_tone_curve`, see lr-tone-curve) for a
finer shape. Protect highlights that carry detail (sky, white dress, silk) and the black point; re-measure
clipping after each pass.

## 4. Colour
Global colour (`adjust_hsl`, colour grading through `set_develop_parameters`) after tone, because tone changes
move colour. Check skin with `measure_region` (lr-skin-tone). Fix casts before building a look.

## 5. Local work
Only what the global edit cannot do: a subject or background, a face, a sky, a shadow area (`create_mask`,
`set_mask_adjustments`, see lr-masking). Keep each local move small and re-measure.

## 6. Detail
Noise reduction before sharpening (lr-noise-reduction, lr-sharpening-clarity). Sharpen the subject, not the noise.

## 7. Consistency across a set
For several photos from one scene, edit one pilot, check it, then roll it out in chunks (lr-batch-operations),
re-measuring each photo: light changes between frames. Use `analyze_batch` to find the outliers.

## 8. Final check
Look at the photo again at a larger preview size and compare against the original: clipping, halos, skin,
colour of known things (a white shirt, a green leaf), edges of masks. Say what you changed, what you left alone
and what you could not judge at preview size. Offer `undo` for any step.

## Stop conditions
A good edit is often small. Stop when the intent is met. If a step makes things worse twice, `undo`, and ask.
Never edit a master directly; the tools use virtual copies.
