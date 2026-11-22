---
name: lr-travel-blog
description: "Professional editing for travel blogs and web storytelling in Lightroom Classic through darkroom_ai: a consistent look across places and light, street, architecture, food, landscapes and people, harsh midday sun, screen-first colour, picking and sequencing a story, preparing for web. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, travel, blog, web, knowledge]
    related_skills: [lr-edit-workflow, lr-landscape, lr-style-looks, lr-genre-knowledge, lr-batch-operations, lr-catalog-organisation, lr-presets, lr-preset-advisor, lr-black-and-white]
    uses_tools: [get_preview, analyze_photo, analyze_batch, measure_region, crop, adjust_white_balance, adjust_lighting, adjust_hsl, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, batch_set_develop_parameters, batch_set_library_state, create_collection, add_to_collection, undo]
---

# Travel blog: one voice across many places

A travel post is read on screens, quickly, in a sequence. The photos must look like one author's work although the
light, country and camera settings change from frame to frame, and each photo has to sell the place.

## Plan the story before editing
- Cull hard: a post needs the establishing view, a few details, people or life, food, and a closing image. Rate and
  flag (`batch_set_library_state`); gather the chosen set in a collection (`create_collection`, `add_to_collection`).
- Group by place and light (morning, midday, golden hour, night) with `analyze_batch`; one base edit per group.
- Decide the look first (lr-style-looks): a calm natural look travels best; a strong look ages quickly.

## Colour for screens
- Most readers see sRGB phones and laptops: avoid colours that only look good on a wide-gamut screen; keep saturated
  reds and cyan from clipping channels (`analyze_photo`).
- Colour is the destination: keep local colour true (spice markets, tiles, saris, sea). Do not push every photo to
  the same orange and teal.
- Keep skin natural for people photos (lr-skin-tone); keep food appetising: warm, clean, never grey or green.

## Harsh midday and difficult light
- Strong sun gives blown skies and black shadows: recover the highlights, lift shadows modestly, add a graduated
  mask on the sky (`create_mask`), and accept contrast as part of the place.
- Overcast gives flat light: add shape with a curve (lr-tone-curve), not heavy clarity.
- Golden hour and blue hour: keep their warmth or coolness; do not neutralise them.
- Night streets: keep lights bright, reduce noise in shadows, avoid green and magenta casts from mixed lamps.

## Subjects
- **Architecture and street:** straighten verticals and horizons with `crop`; keep lines honest. Remove nothing
  that changes the place.
- **Landscapes:** see lr-landscape; protect sky and distance.
- **Food and markets:** accurate colour, clean highlights, kept texture.
- **People and portraits:** natural skin first; respect that candid street portraits should not look retouched.

## Consistency
Apply the base edit to a group with `batch_set_develop_parameters`, then adjust each photo for its light. Compare
photos side by side at the size they will be shown. Sequence the story so brightness and colour change gradually.

## For the web
Crop to the layout (wide hero, square or vertical for social) with `crop` and keep key subjects away from edges.
Sharpen modestly and check at small sizes; compressed JPEGs show banding in skies and noise in shadows, so lower
noise first. Export for web is done by the person in Lightroom unless an export tool is available.

## Limits
Removing tourists or objects, stitching panoramas and sky replacement are not available here. Do not fake the
weather or the place; say what the edit changed.
