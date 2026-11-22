---
name: lr-parties-celebrations
description: "Professional editing of parties and celebrations in Lightroom Classic through darkroom_ai: birthdays, baby showers, engagements, housewarmings, graduations, religious festivals, receptions, kids and families; indoor mixed light, flash, candid moments, fast consistent galleries. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, party, celebration, family, knowledge]
    related_skills: [lr-edit-workflow, lr-event-photography, lr-skin-tone, lr-style-looks, lr-batch-operations, lr-noise-reduction, lr-presets, lr-preset-advisor]
    uses_tools: [get_preview, analyze_photo, analyze_batch, measure_region, adjust_white_balance, adjust_lighting, adjust_hsl, create_mask, set_mask_adjustments, set_develop_parameters, batch_set_develop_parameters, batch_set_library_state, undo_batch, undo]
---

# Parties and celebrations: warm, happy, true to the room

People want to see the fun, the faces and the family as they remember them. The edit should make photos feel
bright and warm without making them look processed.

## What these photos have in common
- Indoor light: mixed lamps, windows and ceiling lights; often flash. Colour casts and dark backgrounds are the norm.
- Many candid frames with blinks and motion: choose before you edit (`batch_set_library_state` to rate and flag).
- Kids, elders and mixed skin tones in one frame; bright decor and balloons that clip; cakes and candles.

## Set the intent by occasion
- **Kids' birthdays and baby showers:** bright, cheerful, soft; keep decor colours fun but skin natural.
- **Engagements and housewarmings:** warm, intimate, clean; keep room atmosphere.
- **Graduations and formal family portraits:** neutral, crisp, accurate colour, faces first.
- **Religious festivals and family functions:** respect the colours of clothing and decor; see lr-wedding-indian for
  silk, garlands and ritual light.
- **Evening parties and dancing:** keep coloured light and mood; fix faces and avoid blown flash.

## Light problems and their usual fixes
- Flash on close subjects: shiny foreheads and hard shadows behind people. Lower shine locally on skin, lift the
  shadow behind subjects with a mask rather than a global change.
- Mixed light: pick faces as the reference; accept the room being warmer. Check with `measure_region`.
- Candles, cake and fairy lights: they are meant to glow; judge clipping on faces, not on flames.
- Underexposed backgrounds are normal; do not lift everything and raise noise (lr-noise-reduction).

## Faces and groups
Group shots contain people at different distances from the flash: front faces bright, back faces dark. A gentle
local lift on the dark faces is better than a global lift. Keep children's skin soft, not plastic: no heavy clarity.

## Speed and consistency
Triage first. Edit one pilot per room or light, then roll out in chunks (`batch_set_develop_parameters`), re-check
the first and last frame. Use `analyze_batch` to find frames that are much darker, warmer or noisier than the rest.
Deliver a clean, slightly warm, consistent set; add a style only when asked (lr-style-looks).

## Limits
Removing people or objects, healing and body or face retouching are not available. Say what was left alone.
