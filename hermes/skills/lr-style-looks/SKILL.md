---
name: lr-style-looks
description: "A professional catalogue of editing styles for Lightroom Classic through darkroom_ai: clean natural, light and airy, dark and moody, warm golden, film and matte, cinematic split tones, vibrant punchy, vintage, desaturated documentary: what defines each, how it is built with the real tools, what suits which subject, what breaks. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, style, look, colour-grading, knowledge]
    related_skills: [lr-edit-workflow, lr-tone-curve, lr-skin-tone, lr-black-and-white, lr-presets, lr-preset-advisor, lr-genre-knowledge, lr-user-guidance]
    uses_tools: [get_preview, analyze_photo, measure_region, adjust_lighting, adjust_white_balance, adjust_hsl, adjust_tone_curve, list_develop_parameters, get_develop_parameters, set_develop_parameters, batch_set_develop_parameters, list_presets, get_preset, undo]
---

# Looks: a look is a set of decisions, not a filter

A look combines contrast shape, black and white points, colour balance, saturation behaviour and grain. Choose it
for the subject and the client, build it from small moves, and verify that skin, whites and key colours survive.
Look first at what the photo already is. When the look is unclear, offer two or three directions and ask.

## The levers
- **Tone curve** (`adjust_tone_curve`): the shape of contrast; lifting the black end gives matte, a steep middle gives
  punch, a flat curve gives softness.
- **Basic tone and white balance** (`adjust_lighting`, `adjust_white_balance`): overall brightness and warmth.
- **HSL** (`adjust_hsl`): per colour hue, saturation and brightness; shape the palette (greens, blues, oranges).
- **Colour grading** (find the keys with `list_develop_parameters`, panel colour grading; set with
  `set_develop_parameters`): tint shadows, midtones and highlights separately; the heart of cinematic and film looks.
- **Grain, vignette, texture and clarity:** finish; strong values age fast. Calibration shifts primaries, a strong
  tool; use sparingly.

## The common looks
- **Clean and natural:** accurate colour, gentle contrast, true skin. Safest for weddings, corporate, family. Breaks
  by being dull: add shape with a curve.
- **Light and airy:** bright, lifted shadows, soft contrast, pastel colour, warm skin. Suits bright rooms, gardens,
  weddings by day. Breaks: washed skin, lost dress or sky detail, muddy cool casts. Watch highlights and skin.
- **Dark and moody:** deeper midtones, controlled highlights, muted or selective colour, often cool or teal shadows.
  Suits dim venues, forests, evening, portraits. Breaks: crushed shadow detail, noisy blacks, grey faces.
- **Warm golden:** warm balance, glowing highlights, rich oranges. Suits sunrise, sunset, weddings, travel. Breaks:
  orange skin and flat warm sameness.
- **Film and matte:** lifted blacks, soft highlights, slightly shifted colour, light grain. Suits lifestyle, travel,
  weddings. Breaks: milky blacks on bright scenes, heavy grain on noisy files.
- **Cinematic split tone:** cool shadows against warm highlights, controlled saturation. Suits street, travel, night
  and drama. Breaks: teal skin; protect faces with a local correction (lr-skin-tone).
- **Vibrant and punchy:** strong contrast and saturation. Suits food, festivals, bright travel, products. Breaks:
  clipped colour channels, neon greens, plastic skin; prefer vibrance and targeted colour.
- **Vintage and faded:** low contrast, warm or green cast, strong grain, soft vignette. Use deliberately.
- **Desaturated documentary:** muted colour, honest tone, texture. Suits reportage; see lr-black-and-white for mono.
- **Black and white:** its own craft, see lr-black-and-white.

## Choosing and checking
Match the look to the occasion and the person's taste, not to fashion. Apply the look with small passes, measure
skin and the whites after each pass, and look at a larger preview. Take a look through a whole set with one pilot
and chunked roll-out. A preset can supply a look: see lr-preset-advisor. Always keep an exit: `undo`.

## Limits
Previews on screens differ; a look that suits a phone can look heavy in print. Avoid trends the person did not ask
for. Say how far the edit moved the photo from the original.
