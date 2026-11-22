---
name: lr-auto-correct
description: "Use Lightroom's own Auto (the Basic-panel Auto button, plus Auto White Balance) on each photo separately as a fast, per-photo starting point, then judge and refine it. Never a copied setting, never a verdict. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, auto, tone, white-balance, batch, knowledge]
    related_skills: [lr-edit-workflow, lr-batch-operations, lr-skin-tone, lr-event-photography, lr-wedding-western, lr-wedding-indian, lr-sync-look, lr-preset-advisor]
    uses_tools: [auto_correct, get_preview, analyze_photo, analyze_batch, measure_region, adjust_lighting, adjust_white_balance, adjust_tone_curve, undo, undo_batch]
---

# Lightroom Auto: fast and individual

`auto_correct` presses Lightroom's own Auto for each photo you list, one photo at a time. Lightroom measures that
photo alone and chooses its own tone settings (and white balance if you ask), so every photo gets values of its
own. That is the point: a copied setting treats photos with different light as if they were the same.

## When it helps
- A first pass over a mixed set (an event, a trip) where exposure and tone differ from frame to frame.
- A quick baseline before the careful work: it fixes the obvious, you judge the rest.
- A photo you cannot read well: see what Lightroom would do, then decide whether to keep it.

## When to be careful
- Backlit, high-key, low-key and silhouette photos: Auto tends to "normalise" them and lose the mood. Judge it
  against the intent of the photo, and undo if it spoils it.
- Photos where skin, a white dress or a coloured stage light matters: measure faces afterwards (`measure_region`).
- Auto White Balance neutralises warm light; golden hour, candles and stage colour may be meant to stay. Ask for
  white balance only when the cast is a problem, not a mood.
- A whole set at once is a bulk edit: it needs the person's go-ahead, a small group at a time, and the result
  must be looked at, not trusted.

## How to use it
1. Look first (`get_preview`, `analyze_photo`) and say what the photo needs. Decide whether tone, white balance
   or both.
2. Show the plan with a dry run when several photos are involved, and get the person's agreement.
3. Run `auto_correct` with the photos in small groups. Each photo's result lists what Lightroom chose, what was
   kept after the safety limits, what it was before, and measurements before and after.
4. If a value was limited, Lightroom wanted a bigger change than one pass allows: look at the photo, and decide
   whether another pass is right.
5. If the result says every photo ended up with the same values, check previews: that is suspicious.
6. Judge each photo with your own eyes against its purpose, then refine with the normal tools. `undo` takes one
   photo back, `undo_batch` the whole group.

## What it does not do
It does not choose a look, fix local problems (a dark face in a bright room, flash shadows), remove objects or
decide the crop. It cannot know the mood the person wants: that stays your job and theirs.
