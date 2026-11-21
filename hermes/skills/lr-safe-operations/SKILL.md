---
name: lr-safe-operations
description: "Safety knowledge for using the darkroom_ai Lightroom tools: what is protected, what needs a go-ahead, how to undo, how to recognise a stale connector or wrong photo, and when to stop and ask."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, safety, knowledge]
    related_skills: [lr-masking, lr-presets, lr-user-guidance, lr-upgrade]
    uses_tools: [get_capabilities, get_app_status, get_selected_photos, find_photo, undo, run_selftest, list_masks, get_develop_parameters]
---

# Safe operation of the darkroom_ai tools

## What the tools protect, and what you still own
- Edits go to a **virtual copy**, never the original file. The tools enforce this; you still must use the
  id of the photo you mean. Get it from `get_selected_photos` or `find_photo`, never by guessing.
- Edits have limits per call and per photo. A request over the limit is clamped or needs a go-ahead. Say
  when something was clamped; do not hide it.
- Writes are only allowed on the configured catalog (the test catalog until Vijay says otherwise). A
  "wrong catalog" error means stop, not work around.
- Every write is journaled and can be taken back with `undo` (this session's edits). Old sessions cannot be
  undone with it.

## Go-aheads belong to the person
- Some actions ask Vijay for a go-ahead (people masks, imports, going past an edit limit, the write
  self-test). Treat a decline or a timeout as an answer, not an error to route around.
- Never approve your own request, never look for another way to do a declined action, and never claim
  something was approved when it was not.

## Checks before and after a write
- Before: is the right photo showing, and does `get_capabilities` say the feature is verified? Tell Vijay
  about anything not verified before you use it.
- After: read back the change (`get_develop_parameters`, `list_masks`) and look at the preview. If the
  result is merely different or worse, `undo` and do less.

## Recognising common problems
- **Connector older than the code on disk:** results may come from old logic. Say so and ask for a connector
  restart; do not keep editing through it.
- **Plug-in serves one client at a time:** a second client (a CLI, another session) cannot connect while
  Hermes' connector holds it. Do not try to work around it from a shell.
- **Lightroom not reachable:** the bridge may be stopped even if Lightroom is open. Report the error code
  and the one step that unblocks it.
- **Busy or wrong-photo errors on mask tools:** the working copy may not be showing (see `lr-masking`).
  Do not retry in a loop.

## Stop and ask when
- A tool result contradicts what Lightroom shows and you cannot say why.
- An edit would touch more than one photo, or anything that cannot be undone.
- You are about to use the production catalog, or any catalog you were not told is allowed.
- Something happened that you did not expect. Report it plainly, including the exact error text.
