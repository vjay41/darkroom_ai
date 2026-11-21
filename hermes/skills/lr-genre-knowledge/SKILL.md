---
name: lr-genre-knowledge
description: "Knowledge about what matters and what usually goes wrong in different kinds of photos (portraits, ceremonies and events, landscapes, low light, food and product). Principles only, no values or recipes."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, genres, knowledge]
    related_skills: [lr-user-guidance, lr-masking]
    uses_tools: [get_preview, analyze_photo, get_photo_metadata]
---

# Genre knowledge: what matters, not what to type

This is background for deciding what the photo needs. It deliberately contains no slider values and no
fixed order of steps: photos in the same genre still differ. Look first, state the intent, then act.
`analyze_photo` gives measurements as evidence; a measurement is never a verdict.

## People and portraits
- Viewers read faces first. Skin that looks believable matters more than technical perfection elsewhere.
- Typical traps: skin turning orange or grey after a colour or contrast change, plastic skin from heavy
  smoothing or clarity, eyes and teeth overdone, a bright background pulling the eye from the face.
- Check skin tones in more than one person when there are several; lighting is rarely identical for all.

## Ceremonies, family and event photos
- The light is often the story (candles, lamps, warm interiors, festival colour). Neutralising it can erase
  what the photo is about. Correct a cast only when something known to be neutral, or skin, looks wrong.
- Mixed light and many faces mean one global setting cannot be right for everyone; local adjustments can help
  but can also look artificial. Prefer restraint.
- Busy scenes: protect the moment and the people before tidying the background.
- Saturated clothing and decor can clip colour channels long before the histogram looks clipped.

## Landscapes and outdoor scenes
- Large bright skies against darker ground are the usual tension; the fix is often local and gentle.
- Over-processing shows as halos along horizons, crunchy texture, unnatural skies and oversaturated greens.
- Haze and distance are part of depth; removing all of it can flatten the scene.

## Low light and night
- Noise and lost shadow detail are the limits, not just exposure. Lifting shadows lifts noise too.
- Light sources (lamps, signs) are meant to be bright; judge clipping on the subject, not on the lights.
- Colour from mixed artificial light can be part of the look. Decide intent before correcting.

## Food and product
- The viewer judges appetite or material quality: believable colour, clean highlights, no muddy shadows.
- Colour accuracy matters more here; a wrong tint on the product is noticed quickly.
- Reflections and specular highlights are information about the material. Do not flatten them away.

## Across all of them
- A good photo often needs very little. "Do nothing" and "do less" are valid outcomes.
- File type changes the headroom: a JPEG has far less room than a RAW file before artefacts show.
- Your own preview is a small image. Say what you cannot judge at that size.
