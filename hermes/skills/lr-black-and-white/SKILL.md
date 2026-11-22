---
name: lr-black-and-white
description: "Professional black and white conversion and editing in Lightroom Classic through darkroom_ai: when mono helps, how colour channels become tones, contrast and tone curves, skin, skies, grain, avoiding mud and clipping. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, black-and-white, monochrome, knowledge]
    related_skills: [lr-edit-workflow, lr-style-looks, lr-tone-curve, lr-skin-tone, lr-sharpening-clarity, lr-landscape, lr-noise-reduction]
    uses_tools: [get_preview, analyze_photo, measure_region, list_develop_parameters, get_develop_parameters, set_develop_parameters, adjust_lighting, adjust_tone_curve, adjust_hsl, create_mask, set_mask_adjustments, undo]
---

# Black and white: tone, shape and texture

Without colour, the picture lives on brightness contrast, shape, light direction and texture. A good colour photo is
not automatically a good mono photo.

## When mono helps
Hard or ugly colour (mixed lamps, clashing decor), strong light and shadow, texture and emotion, timeless
portraits, rescue of a poor colour cast or high ISO noise. It rarely suits photos whose point is colour (festival
silk, flowers, sunset).

## How the conversion works
Each colour becomes a grey. In Lightroom's black and white mix (find the keys with `list_develop_parameters`, set
with `set_develop_parameters`), brightening a colour channel lightens everything of that colour. So:
- A blue sky darkens by darkening the blue channel; clouds then stand out.
- Skin sits mainly in red and orange: lightening them lightens and smooths skin, darkening gives a harder look.
- Foliage sits in green and yellow; red lips and flowers in red and magenta.
Decide which tones must separate (sky from cloud, face from background) and shape the channels for that.

## Tone
- Check clipping and the histogram with `analyze_photo`: mono hides colour but not blown whites or crushed blacks.
- Build contrast with the basic tone controls and a curve (`adjust_tone_curve`, lr-tone-curve): a steady midtone
  slope, with a true black and a white that is not pure paper unless intended.
- Local dodge and burn with masks (`create_mask`, `set_mask_adjustments`) guides the eye: brighten the subject
  slightly, darken the edges and distractions, in small moves.

## Texture, grain, noise
Texture and clarity add grit; they suit weathered faces and architecture and harm smooth skin. Grain gives film
character and hides noise but looks fake if heavy. Reduce chroma noise first (colour noise becomes ugly mono noise)
and keep luminance grain only if it belongs to the look (lr-noise-reduction).

## People
Keep skin tones even; patchy skin appears as uneven grey. Check eyes keep a catch of light. A soft, high-key mono
suits children and weddings; a dark, contrasty mono suits dramatic portraits.

## Check
Compare with the colour version at a larger preview: do the same elements still separate? Say that mono is a
choice that can be undone (`undo`), and keep the colour original.

## Limits
Split toning and a sepia feel are done with colour grading keys; toned mono needs care on skin. Print and screen
rendering of deep blacks differ.
