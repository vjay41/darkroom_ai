---
name: lr-save-points
description: "Use snapshots (named save points), resets, rotation and Lightroom's own removal of distracting people safely: when to save a point, how to try something bold and come back, what a reset removes, and what to tell the person. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, snapshots, reset, safety, knowledge]
    related_skills: [lr-edit-workflow, lr-safe-operations, lr-object-removal, lr-style-looks, lr-preset-advisor, lr-straighten-crop]
    uses_tools: [remove_objects, create_snapshot, list_snapshots, restore_snapshot, reset_develop, remove_distracting_people, get_retouch_state, rotate_photo, get_preview, undo]
---

# Save points: try boldly, come back safely

Every darkroom_ai edit happens on a working copy, never the original. A snapshot adds a named save point inside
that copy so a bold idea can be tried and abandoned.

## Save before big moves
Create a snapshot (`create_snapshot`) before: trying a strong look or preset, a stack of local masks, a
reset, removing people. Name it for what it holds ("clean base", "before moody grade"). `list_snapshots` shows
the points on a photo.

## Coming back
`restore_snapshot` replaces the copy's current settings with the point. It needs the person's go-ahead because
it discards what came after. Restoring a snapshot has not been verified on this Lightroom version: look at the
picture afterwards and say if it did not change. For slider edits, `undo` is the lighter tool.

## Resets
`reset_develop` clears the copy's edits: everything, or one group (masks, spot removal, crop, transforms,
red-eye). It saves a snapshot first and needs a go-ahead. Say what will be lost before asking: a reset of
everything removes every edit on the copy; a reset of masks removes all local adjustments. `undo` brings back the
plain slider values; masks and spots return only through the snapshot.

## Distracting people
`remove_distracting_people` lets Lightroom detect and remove people in the background. It changes what the photo
shows, so: ask first, name who you expect to be removed, and never use it to remove someone the person wants.
A snapshot is saved before it runs; look at the result, and restore if it removed the wrong people or left
artefacts. `get_retouch_state` shows how many spot corrections the photo has before and after. This tool is
unverified on this Lightroom version: report what you see, not what you expect.

## Rotation
`rotate_photo` turns the copy a quarter turn left or right (the orientation, not the fine crop angle; use
lr-straighten-crop for levelling). Turn the other way to undo.

## Tell the person
Which save points exist, what a restore or reset would remove, and where a removal or reset could not be
fully undone.

## When Lightroom's people removal does not work
On the owner's offline PC Lightroom shows an Adobe error ("can't complete your request") after detecting people.
The tool then answers ADOBE_FEATURE_UNAVAILABLE. Tell the owner, ask them to dismiss the dialog in Lightroom and
press Cancel in the Remove panel, and use `remove_objects` for the specific people instead. Do not retry in a loop.
