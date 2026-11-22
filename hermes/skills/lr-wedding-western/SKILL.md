---
name: lr-wedding-western
description: "Professional editing of Western-style weddings in Lightroom Classic through darkroom_ai: white dress and dark suit in one frame, church and outdoor light, golden hour portraits, reception flash, first dance, details, light-and-airy versus timeless looks, consistency across a gallery. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, wedding, western, knowledge]
    related_skills: [lr-edit-workflow, lr-skin-tone, lr-style-looks, lr-batch-operations, lr-masking, lr-noise-reduction, lr-presets, lr-preset-advisor, lr-landscape]
    uses_tools: [get_preview, analyze_photo, analyze_batch, measure_region, adjust_white_balance, adjust_lighting, adjust_hsl, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, batch_set_develop_parameters, undo_batch, undo]
---

# Western weddings: a white dress, a dark suit, one honest skin tone

The signature difficulty is range: a white dress with visible lace and a black suit with visible fabric in the same
frame, with believable skin. The signature risk is a style (airy, moody, film) applied so hard it damages that.

## Know the parts of the day
- **Getting ready:** window light, mixed room lamps, details (dress, rings, shoes, flowers). Keep it soft and clean.
- **Ceremony:** church or hall light can be dim and tinted, outdoor ceremonies have harsh or dappled light. The
  dress is the highlight reference, the suit the shadow reference.
- **Couple portraits and golden hour:** the best light of the day; keep its warmth, protect the sky and skin.
- **Reception:** flash, dim venue, string lights, first dance under coloured light, speeches, cake, dancing.

## The dress and the suit
- White dress: keep lace, folds and beading visible. Read clipping with `analyze_photo`; recover highlights first,
  then restore contrast through a curve so the dress does not go grey. Pure white without any texture is lost detail.
- Black suit: open the shadows slightly so buttons, lapels and fabric read, without turning black into milky grey.
- When one global setting cannot serve both, use a local mask on the dress or the suit (lr-masking).

## Skin and colour
Skin should stay natural across bride, groom and guests: measure faces (`measure_region`, lr-skin-tone) and check
that a style does not push skin orange or green. White balance on the dress or a neutral reference; accept warm
light at golden hour. Flowers and decor are allowed their colour: check reds and greens in `adjust_hsl` moves.

## Looks
Light and airy lifts shadows and softens contrast and can wash skin and lose the dress: check highlights. A timeless
or film look keeps true colour with gentle contrast: safest for an album. Dark and moody suits dramatic venues but
not a bright garden. Offer two directions (lr-style-looks) and let the couple choose; keep the whole gallery in one
look.

## Reception and low light
Direct flash gives hard shadows and shiny skin; bounce gives softer light. Control specular shine on foreheads with a
small local highlight move. Keep coloured dance-floor light as atmosphere, correct faces. Reduce noise in dark
backgrounds while keeping eyes sharp (lr-noise-reduction).

## Consistency
One pilot per location and light, then chunked roll-out (`batch_set_develop_parameters`), re-check the dress and
faces in the first and last frame. Light changes through the day: relative moves, not copies. `undo_batch` takes a
chunk back.

## Limits
Removing guests, objects or blemishes and reshaping are not available. Do not alter a person's appearance beyond
tone and colour. Say what was not done.
