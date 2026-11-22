---
name: lr-wedding-indian
description: "Professional editing of Indian and South Asian weddings and ceremonies in Lightroom Classic through darkroom_ai: saturated silk and gold, mixed temple and hall light, rituals and their colours, skin tone across many complexions, whites that clip, consistency across hundreds of frames. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, wedding, indian, ceremony, knowledge]
    related_skills: [lr-edit-workflow, lr-skin-tone, lr-style-looks, lr-batch-operations, lr-masking, lr-noise-reduction, lr-presets, lr-preset-advisor, lr-event-photography]
    uses_tools: [get_preview, analyze_photo, analyze_batch, measure_region, adjust_white_balance, adjust_lighting, adjust_hsl, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, batch_set_develop_parameters, batch_set_library_state, undo_batch, undo]
---

# Indian weddings: colour is the story, accuracy is the craft

These weddings are rich in saturated colour, gold, silk, flowers and ritual. The edit must keep that richness
believable: a red kept red, gold kept gold, skin kept natural. Over-correcting towards neutral erases the occasion;
over-saturating makes silk and skin look cheap.

## Know the day (the light and colours change by event)
- **Haldi:** yellow everywhere, high-key daylight, faces cast yellow by reflection. Keep the yellow, protect skin.
- **Mehendi:** greens, oranges, floral decor, often shade or tent light; hands and patterns are the detail.
- **Sangeet and reception:** coloured stage and LED light, dancing, flash; do not neutralise stage colour, fix faces.
- **Baraat and night events:** flash against dark, lamps, fireworks; control flash highlights and keep the glow.
- **Ceremony (mandap, temple, fire rituals):** warm tungsten or fire light, smoke haze, white and cream garments,
  gold jewellery; the mood is warm and should stay warm.
Ask which rituals and what family or cultural style apply (regional silk, temple versus hall) when it matters.

## Colour discipline
- Red silk, marigold orange and magenta clip a single channel long before the histogram shows it: read
  clipping per channel with `analyze_photo` and look for flat, textureless red or orange (lost zari and weave).
- Reds drift to orange or pink under HSL moves; make small `adjust_hsl` moves and compare silk, garlands and skin
  together. Marigold and skin share a hue: a global orange move that fixes skin also dulls the garlands, so use a
  local mask on the face (`create_mask`, `set_mask_adjustments`) when they must differ.
- Gold jewellery wants warmth and specular highlights; do not flatten it with lifted whites.

## Skin across complexions
Families contain many skin tones in one frame under one light. Measure faces with `measure_region` (lr-skin-tone),
keep each person's tone natural and similar in hue, and do not brighten dark skin to match fair skin or the reverse.
Mixed warm and flash light often leaves one cool face next to a warm one; treat the key people locally.

## Whites and bright garments
White dhoti, shirt, veil or sari borders clip first. Protect their texture: lower the highlights, check clipped
share, and keep a slightly off-white rather than a grey. Exposure for the face, then recover the whites.

## Low light and noise
Evening halls are ISO heavy: reduce luminance noise in shadows and backgrounds, keep eyes and jewellery detail
crisp (lr-noise-reduction, lr-sharpening-clarity). Smoke and fire haze can be mood; do not dehaze it away.

## Consistency and delivery
Edit one pilot per ritual or light group, then roll out in chunks with `batch_set_develop_parameters` and re-check
faces and silk on the first and last frame (lr-batch-operations). Keep one look across the album: warm and rich,
or clean and natural, chosen with the couple. Candid emotion beats technical perfection: keep the moment.

## Limits
Removing people or objects, healing and jewellery retouching are not available. Do not change a person's
appearance beyond colour and tone. Say what you could not judge at preview size.
