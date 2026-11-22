---
name: lr-landscape
description: "Professional landscape editing in Lightroom Classic through darkroom_ai: skies, dynamic range, depth, light direction, colour of greens and water, avoiding halos and over-processing, golden hour and haze, panoramas and horizon. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, landscape, nature, knowledge]
    related_skills: [lr-edit-workflow, lr-masking, lr-tone-curve, lr-style-looks, lr-noise-reduction, lr-sharpening-clarity, lr-lens-corrections]
    uses_tools: [get_preview, analyze_photo, measure_region, crop, adjust_white_balance, adjust_lighting, adjust_hsl, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, undo]
---

# Landscape: honest light, believable colour

A landscape works when the light feels like a real moment. The edit's job is to recover what the eye saw (the
camera sees less range) and guide the eye, not to invent a scene.

## Read the scene
- Where is the light coming from and how strong? Golden hour, overcast, midday and blue hour each want a different
  treatment: warm glow kept, flat light given gentle contrast, harsh light tamed, cool dusk left cool.
- Is the sky the subject or the background? Is there a clear foreground, midground and background (depth)?
- `analyze_photo`: clipping in the sky or the shadows tells you how much recovery is left. A RAW file recovers far
  more than a JPEG.

## Frame
Level the horizon (`crop` with a small angle, estimated by eye: water must look level). Crop to strengthen the
composition and remove distractions at the edges (bright spots pull the eye out of the frame).

## Dynamic range
- Recover bright sky detail and open dark foreground with the global tone controls first, then local graduated or
  radial masks (`create_mask` gradient or radialGradient) for the sky and the ground separately.
- Halos along the horizon or mountain edge mean a local move is too strong or too hard-edged: soften the mask,
  reduce the move, or `undo`.
- Keep the sky lighter than the ground in most scenes; a dark sky over a bright ground looks wrong.

## Colour
- White balance sets the mood: slightly warm for sunrise and sunset, neutral for overcast. Check snow, cloud
  and rock for casts.
- Greens go yellow or neon easily: adjust the green and yellow channels in `adjust_hsl` in small steps and look
  at foliage in shade and in sun. Blues in sky and water: guard against cyan banding and a purple fringe.
- Saturation pulled on the whole picture over-cooks the sky and the foliage; prefer targeted colour moves.

## Depth and atmosphere
Haze gives depth: a small amount of dehaze can help a washed distance, too much flattens colour and darkens
the sky unnaturally. Contrast and clarity belong mostly in the midground; leave soft distance soft.

## Detail and noise
Long exposures and skies hide noise differently: reduce noise in the sky first, then sharpen the textured
foreground (rock, grass, bark) with edge masking so the sky stays smooth (lr-noise-reduction,
lr-sharpening-clarity).

## Water, snow, light sources
Water: keep reflections and a little highlight detail; moving water can stay soft. Snow: keep texture, watch the
blue cast. Sun, moon, lamps: allowed to be bright; judge clipping on the scene.

## Limits
Panorama stitching, focus stacking, object removal and sky replacement are not available here. Say so when the
photo needs them. Preview size hides halos: check masks' edges at a larger preview.
