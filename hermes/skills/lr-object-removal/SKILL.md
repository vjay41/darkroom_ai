---
name: lr-object-removal
description: "Remove objects, people, dust, spots, blemishes or red-eye with darkroom_ai: find and suggest candidates sensibly, get the person's agreement, remove into new files, and check the result. Process knowledge only; the tools fill the regions they are given."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, retouch, remove, spot, red-eye, knowledge]
    related_skills: [lr-edit-workflow, lr-skin-tone, lr-travel-blog, lr-safe-operations, lr-user-guidance, lr-wedding-western, lr-wedding-indian]
    uses_tools: [get_preview, view_region, find_candidates, measure_region, remove_objects, fix_red_eye, remove_distracting_people, get_retouch_state, analyze_photo]
---

# Removing things: suggest, agree, remove, check

Removal changes what a photo shows, so the person decides. Results are always NEW files; the original and the
Lightroom edit stay as they are.

## When the person names the object
Find it in the picture (`get_preview`, `view_region`) and describe it back in one line: what and where. Draw a
region around the whole object, loosely: include its shadow and any reflection or cast light, since a removed
object with its shadow left behind looks worse than the object. Then remove.

## When you should suggest
Scan with `find_candidates` (it only measures what stands out: size, colour difference, edges, whether it touches
the border), then LOOK at each with `view_region` and decide what it is. Typical suggestions:
- Things that distract at the edges or behind the head: poles, signs, bins, cables, stray people, bright litter.
- Sensor dust and spots (the same small dark blob on a sky or wall, in many photos from one lens or day).
- Small blemishes on a subject that is not a close portrait of a person who did not ask for retouching.
- Red-eye from flash: red glow in pupils.
Do not suggest removing: the subject, family or guests, things that give the place its meaning (signs, landmarks,
cultural items, jewellery), or anything you are not sure about. The candidate list can include the subject.
Offer at most a few, ranked by how much they distract, each with where it is and what you would do. Let the person
choose. Never remove a person without being asked.

## Choosing the method
`remove_objects` with `content_aware` uses Photoshop: right for larger or textured things (a pole against trees,
a bin on pavement). `inpaint` works without Photoshop and suits small spots on smooth areas (dust on sky), with a
small area limit. `auto` picks Photoshop when it is available. A large region on a busy background may still
look smeared: check, and say so.

## Check every result
The result gives a before and after sheet and a JPG. View them. Look for repeated texture, smeared edges, a
visible patch, a missing shadow, lines that no longer join. If it is not clean, redo with a better region or the
other method; if it cannot be made clean, say so rather than hand it over.

## Red-eye
Put one tight box around each eye (pupil plus a little white) and call `fix_red_eye`. The result lists the share
fixed per eye: zero means no red was found there, so the box is off or the eye is not red.

## Lightroom's own people removal
Do not use `remove_distracting_people`: it is deprecated and unavailable here (Lightroom's people removal needs an Adobe
sign-in). For people or objects in the background use `remove_objects` (Photoshop route above), which makes new files
and a before and after sheet.

## Honesty
Say what was removed and what was not. Removing people, text or objects from journalistic or evidence photos
misleads: ask first about purpose when it could matter. The files are not in Lightroom: tell the person where
they are, and that later Lightroom edits will not appear in them.
