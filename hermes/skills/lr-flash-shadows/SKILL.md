---
name: lr-flash-shadows
description: "Reduce harsh flash artefacts in Lightroom Classic through darkroom_ai: hard dark shadows behind subjects, shiny skin, hot spots, flat flash light, red-eye; find the problem with zone and region measurements and fix it locally. Process knowledge only; no values."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, flash, shadows, portrait, knowledge]
    related_skills: [lr-edit-workflow, lr-masking, lr-skin-tone, lr-event-photography, lr-parties-celebrations, lr-object-removal]
    uses_tools: [get_preview, view_region, analyze_photo, zone_map, measure_region, create_mask, set_mask_adjustments, adjust_lighting, adjust_white_balance, fix_red_eye, undo]
---

# Flash problems: find them, then fix them locally

Direct on-camera flash gives a typical set of faults. A global slider fixes one and breaks another, so find each
fault with numbers, then correct only where it lives.

## Find them
- **Hard shadow behind the subject:** `zone_map` lists dark regions with their area, how much darker they are
  than the surroundings and how hard their edge is. A dark region with a hard edge right next to a subject, on a
  wall or backdrop, is a flash shadow. Check it with `view_region`.
- **Shiny skin and hot spots:** forehead, nose and cheeks with highlights nearly clipped: measure with
  `measure_region` boxes on the shiny and on matte skin of the same person, and `analyze_photo` for clipping.
- **Flat, harsh frontal light:** little shape in faces, bright subject on a dark background (dark falloff).
- **Colour cast:** flash is cool against warm room light: compare skin and background with `measure_region`.
- **Red-eye:** red glow in pupils.

## Fix them
- **Shadow:** a local mask over the shadow region (`create_mask`, `set_mask_adjustments`) raising its exposure and
  shadows a little, and warming it slightly if it looks cold. Lift in small steps and measure the same region
  again until it is close to its surroundings but not flat. Keep the soft transition: a patch brighter than the
  wall looks fake. If the shadow falls on the subject's own face or hair, lift less.
- **Shine:** a local mask on the shiny skin lowering highlights and whites modestly; do not remove all
  specular light, faces need some. Measure the share of clipped pixels before and after.
- **Dark falloff:** a gentle large mask on the dark background lifting exposure, or a radial mask around the
  subject; avoid raising noise (see lr-noise-reduction).
- **Cast:** adjust white balance toward the skin, not toward the wall; check with `measure_region`.
- **Red-eye:** `fix_red_eye` with a tight box per eye (makes a new file; see lr-object-removal).
- **Overall:** after the local fixes, judge the whole picture again; a global change is rarely needed.

## Verify
Measure the same boxes before and after, view the result at a larger size, and compare with the original. Wait a
few seconds after mask edits before measuring, because Lightroom shows them late. Tell the person which problems
were fixed, which were reduced and which could not be (a shadow that merges with real dark hair, a clipped
highlight with no detail).

## Limits
Relighting a whole scene and removing a hard shadow completely are not possible; the goal is believable,
softer light. Skin retouching and reshaping are out of scope.
