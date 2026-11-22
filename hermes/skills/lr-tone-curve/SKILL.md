---
name: lr-tone-curve
description: "Knowledge for working with the tone curve in Lightroom Classic through darkroom_ai: what the combined and colour curves do, how to read and change one safely, and how to judge the result. Process knowledge only; it never says what a curve should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, tone-curve, knowledge]
    related_skills: [lr-safe-operations, lr-user-guidance, lr-genre-knowledge]
    uses_tools: [get_tone_curve, adjust_tone_curve, get_preview, analyze_photo, undo]
---

# Tone curve: knowledge, not a recipe

## What a curve is
A curve maps each input brightness (x, 0 black to 255 white) to an output brightness (y). A straight line from
(0,0) to (255,255) changes nothing. Points above the line brighten that tonal range, points below darken it.
A steeper stretch of curve adds contrast in that range; a flatter stretch removes it.

## Combined curve and colour curves
- The **combined (rgb) curve** shapes overall tone. It is the right place for a contrast shape the basic
  sliders do not give: for example separating the shadows from the highlights, or lifting the black point.
- The **red, green and blue curves** shift colour within a tonal range (a cast only in the shadows, or only in
  the highlights). They are subtle and easy to overdo; a small move already shows. Read the colour numbers
  from `analyze_photo` before and after instead of trusting the eye on a small preview.

## How to work with it
1. Read the current curve with `get_tone_curve` first. A photo can already carry a curve (from a preset or
   an earlier edit); change that curve, do not replace it blindly.
2. Send the whole curve to `adjust_tone_curve`: points from x=0 to x=255, x increasing, at most sixteen.
   Few points make smooth shapes; many points make bumpy ones.
3. Move points a little. The tool limits each pass and the total distance from a straight line, and never lets
   the curve fold back on itself. If the result says it limited something, the idea was bigger than the
   guardrails: do not retry the same thing, show the result and ask.
4. Look at `get_preview` and the measured before/after numbers. A curve that clips highlights or crushes
   shadows shows up as the clipped shares growing; take it back with `undo` if the picture got worse.
5. Prefer the basic sliders for broad tone and the curve for shape. Do not use both to do the same job.

## What to tell the person
Say which curve (combined or a colour) was changed, in plain words what it does to the picture, and that
`undo` takes it back. Skin tones are sensitive to colour curves: after one, look at faces again.
