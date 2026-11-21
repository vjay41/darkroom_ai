---
name: lr-lens-corrections
description: "Knowledge for lens profile, chromatic aberration, defringe and vignette work in Lightroom Classic through darkroom_ai: what each correction fixes, how JPEG and RAW differ, and how to check an effect honestly."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, lens, knowledge]
    related_skills: [lr-safe-operations, lr-user-guidance]
    uses_tools: [get_photo_metadata, list_develop_parameters, get_develop_parameters, set_develop_parameters, get_preview, undo, get_capabilities]
---

# Lens corrections: knowledge, not a recipe

## What each correction is for
- **Lens profile:** removes the lens's known geometric distortion and darkened corners using a stored
  model of that lens. It only helps if Lightroom has a profile for the lens; the profile name setting
  shows whether it found one (read it with `get_develop_parameters`).
- **Chromatic aberration:** colour fringes (often purple or green) along high-contrast edges. Removal is
  usually safe and subtle; it matters most at the frame edges with fast lenses or bright backgrounds.
- **Defringe:** a stronger tool for fringes that remain. It desaturates a colour range near edges, so it
  can also dull real coloured edges. Use only when a fringe is visible, and keep it small.
- **Vignette (lens vs creative):** a lens profile removes the lens's own darkening. A post-crop vignette
  is an artistic choice. Do not confuse them: removing lens vignetting is a correction, adding one is a look.
- **Geometry (Upright, perspective, crop):** changes the frame. It is not a quiet correction; treat it as
  an edit the person should see.

## Facts to read before deciding (they are facts, not verdicts)
- `get_photo_metadata`: camera, lens, focal length, aperture, file type.
- JPEG vs RAW: many cameras already apply some lens corrections inside a JPEG. Applying the same correction
  again can over-correct. Whether a camera applied them is a fact about that file, not something to assume.
- RAW/DNG develop writes are not yet verified on this setup (`get_capabilities` lists it). Say so before
  relying on them.

## Judging the effect honestly
- The effect of a lens correction on a clean modern lens is often below what a preview shows. A value
  changing is not a visible change.
- Compare at 100% on the highest-contrast edges and the corners, before and after. If you cannot see a
  difference, say you cannot see one and let the person's eyes decide. Never report it as "visibly fixed"
  from a value change alone.
- Check the other direction too: a correction that is too strong shows as stretched corners, a bright
  rim, or desaturated edges.

## Working practice
- Read the current state first (`get_develop_parameters`), change one thing, look, then continue. The
  switch-style settings take small relative steps; read back to confirm the setting actually moved.
- Everything lands on a virtual copy and `undo` takes it back. If the result is merely different, undo.
