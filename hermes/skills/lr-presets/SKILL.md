---
name: lr-presets
description: "Knowledge for using Lightroom presets through darkroom_ai: what a preset can silently change, how to read the before/after list, and when to refuse or undo."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, presets, knowledge]
    related_skills: [lr-masking, lr-safe-operations, lr-user-guidance]
    uses_tools: [list_presets, apply_preset, list_masks, get_preview, undo, get_develop_parameters]
---

# Presets: knowledge, not a recipe

## What a preset really is
A preset is a bundle of settings made for someone else's photo. It is a starting point to judge, not an
answer. It can move many controls at once, so the visible look is the sum of changes you did not choose.

## What a preset can change that people forget
- **Masks:** some presets add, replace or remove local adjustments. A single preset can create many masks.
  Read the mask list before and after (`list_masks`) whenever mask state matters.
- **Colour and tone together:** a look preset usually moves tone, colour and grain at once. Judge the whole,
  and check skin and the brightest and darkest detail specifically.
- **Frame and processing:** crop, rotation and process version are not "looks". The tool takes a preset back
  automatically when it changes the crop or process version, or moves exposure far from the original.

## How to use one responsibly
1. Choose the preset for a stated reason tied to the intent, not because its name sounds right. `list_presets`
   shows what exists; apply by exact name.
2. The result lists every setting changed with before and after. Read that list, not just the picture.
3. Look at `get_preview` and compare with the before. Presets that look good at thumbnail size can break skin
   tones or clip highlights.
4. If the preset is merely different or worse, `undo` takes the whole preset back as one step.
5. A preset applied then tuned lightly is usually better than a preset applied at full effect. Tune with the
   normal tools and say what you changed on top.

## When to say no
- The preset was made for another camera or lighting and moves white balance a lot on this scene.
- The result removes the mood the photo is about.
- The person asked for a result a preset cannot be trusted to give. Offer the granular tools instead.
