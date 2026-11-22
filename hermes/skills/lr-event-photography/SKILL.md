---
name: lr-event-photography
description: "Professional editing of events in Lightroom Classic through darkroom_ai: conferences, concerts, festivals, corporate and community events, stage and mixed light, many faces, fast turnaround, consistency across hundreds of frames, delivery priorities. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, events, batch, knowledge]
    related_skills: [lr-edit-workflow, lr-batch-operations, lr-skin-tone, lr-noise-reduction, lr-masking, lr-catalog-organisation, lr-style-looks]
    uses_tools: [get_preview, analyze_photo, analyze_batch, measure_region, adjust_white_balance, adjust_lighting, adjust_tone_curve, create_mask, set_mask_adjustments, set_develop_parameters, batch_set_develop_parameters, batch_set_library_state, undo_batch, undo]
---

# Events: speed, consistency, people first

An event gallery is judged by whether people look good, the moments are kept, and the set looks like one
story. Hundreds of frames mean the method matters more than perfecting any single one.

## Triage first
- Rate and flag before editing (`batch_set_library_state`): keep the best frame of each moment, the key people
  and moments (speeches, awards, performances, reactions), reject blinks, blur and duplicates. Editing rejects is
  wasted time.
- `analyze_batch` ranks frames by exposure, clipping, cast and noise without loading pictures; group frames that
  share light (one stage, one room, one time block) so each group gets one base edit.

## Mixed and stage light
- Stage and LED light is coloured on purpose: do not neutralise it. Fix the skin and the speaker, not the lights.
- Venues mix daylight windows, tungsten and LED. Choose the light that falls on faces as the reference and let the
  rest drift. Check faces with `measure_region`.
- Flash plus ambient gives a cool or neutral subject on a warm background: usually fine, keep it consistent.
- Bright screens and projectors clip first; protect the speaker's face, not the screen.

## Faces and skin
Different people in one frame get different light; avoid one global warm or cool move that helps one and hurts
another. Use a local mask only for a key person (lr-skin-tone). Keep skin natural across the group.

## Noise and sharpness
Event rooms are dark and ISO is high: reduce luminance noise in the shadows and background, keep faces and eyes
crisp, and avoid plastic skin (lr-noise-reduction, lr-sharpening-clarity). Motion blur on a performer is part of
the moment; do not sharpen it into artefacts.

## Consistency across the set
Edit one pilot per light group, check it at a larger preview, then roll out in chunks with
`batch_set_develop_parameters`, re-measuring and fixing outliers by hand. Exposure and white balance shift
between frames; apply relative moves, not absolute copies, and look at the first and last frame of each chunk.
`undo_batch` takes a chunk back.

## Delivery priorities
1. People and key moments clean and consistent. 2. Wide shots that show the venue and crowd. 3. Details
(signs, decor, food). Do not over-style a corporate event: a clean, neutral, slightly crisp look is safest.

## Limits
Removing distracting objects and retouching individuals are outside these tools unless the person agrees to
another route. Say what was not done.
