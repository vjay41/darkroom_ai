---
name: lr-masking
description: "Knowledge for local adjustments in Lightroom Classic through darkroom_ai: when a mask beats a global edit, how each mask kind behaves, how to judge a mask, and the known failure modes."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, masks, knowledge]
    related_skills: [lr-safe-operations, lr-user-guidance]
    uses_tools: [create_mask, list_masks, inspect_masks, set_mask_adjustments, invert_mask, delete_mask, get_preview, get_selected_photos, undo, get_capabilities]
---

# Lightroom masking: knowledge, not a recipe

This skill says how masks behave and how to judge them. It never says what an edit should be:
look at the photo, decide the intent, then pick the smallest tool that does the job.

## When a mask is the right tool
- Use a mask when the problem is local: one region is too dark, too bright, too warm or too busy while
  the rest is right. A global slider would damage the parts that were fine.
- If the whole frame has the problem, a mask is the wrong tool. Masks add places for an edit to show.
- A mask that is needed to hide an edit's side effects (for example to hold back a halo) is a sign the
  edit is too strong. Try less first.

## The kinds and what they are good at
- **AI selections (subject, sky, background, objects, landscape):** fast, good on clear edges, can miss
  fine detail such as hair, veils, jewellery and glass. Treat the result as a draft and check the edge.
- **People:** Lightroom will not make it without a person being chosen in its own panel. The tool
  asks Vijay for a go-ahead and then waits for him to pick a person and create the mask. This is
  interactive and time-boxed; do not loop retries. `get_capabilities` says whether it is verified.
- **Gradients and radial gradients:** geometric, predictable and soft. Good for sky, vignettes, light
  falloff. The feather matters as much as the position: a hard edge reads as a filter.
- **Luminance range:** selects by brightness, so it follows tones, not objects. Good for lifting
  shadows or taming highlights without drawing a shape; it can pick up unrelated areas of the same tone.
- **Colour and depth range:** not available through this tool. Say so and offer another kind.

## Judging a mask
- Look at the mask's effect at a size where edges are visible, not only the thumbnail-sized preview.
- Edges: halos (a bright or dark rim) and cut-out looks mean the edit is too strong or the selection
  too tight. Overlapping masks add up where they overlap.
- Subject masks that leak into the background, or background masks that eat into hair, show up as
  colour or brightness steps. If you cannot see the step, do not claim there is none: say what you checked.
- Local edits are easy to over-do because the rest of the frame stays put. Keep the change smaller than
  feels necessary and compare with the before.

## Failure modes seen on this setup
- **The photo must be showing in Develop.** Mask tools take over Lightroom's Develop view for a few seconds;
  do not click in Lightroom meanwhile. If it was in use you get a busy error; wait, do not hammer.
- **Collapsed stacks hide the working copy.** Lightroom stacks a virtual copy with its original, and a
  copy inside a collapsed stack cannot be shown. Symptoms: a busy error, or a mask that takes about a
  minute and comes back without an id. Vijay fixes it in the Library module (Photo > Stacking > Expand
  All Stacks, with a folder or All Photographs as the source). Do not retry in a loop.
- **Check the id you act on.** Use the id of the photo Lightroom is actually showing (`get_selected_photos`).
  A mask made on the wrong photo, possibly an original, is a real risk; say so and stop.
- **Stored scales differ from slider scales.** `list_masks` explains the scale of the numbers it returns;
  read that note before comparing values.
- **After any mask write, read it back** (`list_masks` or `inspect_masks`) and look at the preview.
  If it is worse or merely different, use `undo` or `delete_mask` and try less.
