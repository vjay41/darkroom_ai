---
name: lr-straighten-crop
description: "Level horizons and verticals, rotate, and crop to a chosen aspect ratio or composition in Lightroom Classic through darkroom_ai: measure the tilt, plan the largest crop that fits, apply it, then measure again. Process knowledge only; the tools give numbers."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, crop, straighten, composition, knowledge]
    related_skills: [lr-edit-workflow, lr-landscape, lr-travel-blog, lr-delivery-export, lr-safe-operations]
    uses_tools: [get_preview, view_region, measure_tilt, plan_crop, crop, find_candidates, undo]
---

# Straighten and crop: measure, plan, apply, measure again

The tools measure and do arithmetic; you decide what the picture should look like.

## 1. Decide whether to straighten
Look at the picture (`get_preview`). Run `measure_tilt`: it gives the tilt of the near-horizontal lines and of the
near-vertical lines, each with a line count and a confidence, and whether the two agree.
- A horizon or a water line is the best reference. Walls, door frames and shelves are good too.
- Distrust the number when the scene has sloping streets, hills, stairs, a tilted camera on purpose, leaning
  buildings or few lines (low confidence, one family only). Check the lines in the picture with `view_region`.
- When horizontal and vertical agree with high confidence, correct it. When they disagree, the cause is
  perspective, not rotation: crop cannot fix that; say so.
- A tilt that is tiny is below what people see; leave it unless the person wants it exact.
- Deliberate tilt (dutch angle, dynamic action) stays: ask when unsure.

## 2. Plan the crop
Rotating leaves empty corners, so a straightened picture must be cropped. `plan_crop` gives the largest crop of a
given shape (original, or 3:2, 4:5, 1:1, 16:9) that stays inside the rotated picture, centred or moved towards an
anchor point (a face or the main subject) as far as the picture allows. It returns `crop_arguments` for `crop`.
Choose the shape for the destination: social feed, print size, album page, web hero. Say how much of the frame is kept.

## 3. Compose
Before applying, think about where the subject sits: keep faces and key objects clear of the edges, leave room
where the subject looks or moves, and do not cut limbs at joints. `find_candidates` lists what stands out in the
frame and where; use it to see distractions near the edges that a crop could drop. Crop for a reason: a tighter
crop also loses resolution and noise grows when it is enlarged.

## 4. Apply and verify
Apply the planned values with `crop`. Then measure again with `measure_tilt` on the same photo: the tilt should be
near level. If it got worse, the angle had the wrong sign: apply the same crop with the opposite sign of angle and
measure once more. Look at the result with `get_preview`, check that no empty corner shows and that edges are
clean. `undo` takes the crop back.

## Limits
Keystone and perspective correction, lens distortion and stitching are not available here. A crop is always
non-destructive: the original frame is kept in Lightroom. Say what the final crop kept and why.
