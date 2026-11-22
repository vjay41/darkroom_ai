---
name: lr-skin-tone
description: "How to judge and correct skin tone in Lightroom Classic through darkroom_ai: measuring skin patches as numbers, separating a colour cast from a skin-only problem, choosing white balance, HSL or a local mask, keeping faces consistent and natural. Process knowledge only; it never says what a value should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, skin-tone, portrait, colour, knowledge]
    related_skills: [lr-masking, lr-sharpening-clarity, lr-tone-curve, lr-genre-knowledge, lr-safe-operations, lr-user-guidance]
    uses_tools: [measure_region, analyze_photo, get_preview, adjust_white_balance, adjust_hsl, create_mask, set_mask_adjustments, set_develop_parameters, undo]
---

# Skin tone: measure first, change the smallest thing

Skin that looks wrong is either part of a colour cast over the whole picture, or a problem of the skin alone (a
coloured reflection, mixed light, an over-saturated preset). The fix differs, so find out which before touching anything.

## Look and measure
1. `get_preview`, then choose clean skin: a cheek or the forehead, away from hair, shadow edges, glasses, makeup and
   bright highlights. Note its box as fractions of the picture.
2. `measure_region` on that box gives lightness, a* and b*, hue angle, chroma and the median RGB. Add a box on a
   neutral thing (a white shirt, grey wall) and one on a second face. Hue angle is the direction of the colour
   (lower more red, higher more yellow); chroma is how strong it is; lightness is brightness.
3. Skin of every complexion sits in a fairly narrow band of hue; people differ mostly in lightness and chroma. A face
   far outside that band, or two faces in one picture with clearly different hue, points to a cast or mixed light.
4. `analyze_photo` shows the colour cast of the whole frame. If the neutral box is tinted too, it is a cast: fix it
   with `adjust_white_balance` first, then measure the skin again. Often that is the whole job.

## Choose the tool
- **Whole picture tinted:** white balance (temperature and tint). Small moves; skin and neutrals move together.
- **Skin too red, orange or yellow while neutrals are fine:** `adjust_hsl` on the colours skin lives in (usually the
  orange and red channels, yellow for sallow skin). Hue moves the colour, saturation its strength, luminance its
  brightness. These also move other things of the same colour (lips, wood, clothing): look for side effects.
- **One person differs from another, or only a part of the frame is off:** a local mask (`create_mask`, then
  `set_mask_adjustments` with the local temperature, tint or saturation). A people mask needs the person chosen in
  Lightroom's panel; a radial mask on a cheek works when that is not possible. Prefer a mask over `adjust_hsl`
  when the goal is one face and the frame holds other things of the same hue (clothing, garlands, wood): a global
  orange move drags them along, a mask leaves them and the neutrals untouched.
- **Skin too dark or too bright:** that is exposure, a mask, or the luminance of the orange channel, not colour.

## Check after every pass
- Measure the same boxes again. The wanted change shows in the numbers (hue, chroma or lightness moved the right way)
  and the neutral box stays neutral. Mask edits render a few seconds late: wait about 5 seconds before measuring.
- Look at the preview. Numbers cannot see blotchy or plastic skin, orange lips or a green tint in shadows.
- Do not chase an exact number. A natural result keeps skin lighter or darker, warmer or cooler as the light of the
  scene implies; the goal is consistent and believable, not identical.

## Keep it safe
- Work on the working copy; each pass is limited in size. If the result looks worse, `undo`, then try a smaller
  change or a mask.
- Tell the person what moved and offer to take it back. Taste differs between people and cultures: if unsure whether
  to warm or cool the skin, ask.

## Honest limits
- Boxes are placed by you from a preview; a box on a shadow or a reflection gives misleading numbers. Re-check the box.
- Previews are small and colour managed by Lightroom; the final judgement is the picture on a calibrated screen.
- Skin retouching beyond colour (blemishes, smoothing) is outside these tools.
