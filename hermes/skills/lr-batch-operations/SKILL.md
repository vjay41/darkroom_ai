---
name: lr-batch-operations
description: "How to work through many photos efficiently and safely in phases: scope, numeric triage, grouping, a pilot, chunked rollout with stop conditions, and a review. Process knowledge only; it never says what an edit should be."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, batch, knowledge]
    related_skills: [lr-safe-operations, lr-user-guidance, lr-genre-knowledge]
    uses_tools: [list_catalog_photos, analyze_batch, get_photo_metadata, get_preview, analyze_photo, get_develop_parameters, set_develop_parameters, undo]
---

# Many photos: work in phases, not in one go

Looking at every photo one by one is slow and expensive: each look costs minutes of model time. Editing
everything in one go is risky: one wrong idea is repeated everywhere. Work in phases and spend
attention where it matters. This is a process, not an editing recipe: each photo still gets its own
decision.

## Phases
1. **Scope (ask once).** Count what is involved (`list_catalog_photos`, filter by folder text, page with the
   offset). Say how many photos, the phases below and a rough time. Wait for Vijay's go-ahead before any write.
2. **Triage with numbers only.** Use `analyze_batch` (up to 25 photos per call, no pictures). Keep the numbers,
   not the images. If it returns `not_processed`, call again with those ids. Facts from `get_photo_metadata`
   (camera, ISO, file type) add context. Numbers are evidence; they never decide an edit alone.
3. **Group.** Put photos with similar scene and light together (same session, similar numbers). Mark outliers
   (very dark, very bright, odd cast, a different file type) to handle on their own.
4. **Pilot.** For each group, pick one representative and do the full careful loop: look, state the intent,
   one small pass, measure, compare, undo if worse. Show Vijay the result and get his approval before spreading it.
5. **Rollout in chunks.** Apply the approved idea to a small chunk (about ten photos), not the whole set.
   Choose the values per photo from its own numbers; identical values on photos with different exposure can
   hurt them. After each chunk, re-measure with `analyze_batch` and compare with the triage numbers.
   Stop and report at the first photo that got worse or errored. Do not retry in a loop.
6. **Review.** Show a small sample of before and after, list what was changed and which photos were left
   alone or flagged, and say how to take it back (`undo` per working copy).

## Stop conditions (stop, report, ask)
- A tool error that is not a single odd photo (Lightroom not reachable, wrong catalog, wrong photo showing).
- More than one photo in a chunk got worse after the same change: the idea is wrong for this group.
- Anything outside what Vijay approved (another folder, masks, presets, RAW files not covered by the pilot).
- A time or call budget that Vijay set is used up.

## Keeping it efficient
- Smaller previews are enough for triage; look at full-size images only for the pilot and for flagged photos.
- Do not re-measure photos you have not changed. Remember which ids are done, so a resumed run skips them.
- One confirmation per chunk plan is better than one per photo; but never skip the first confirmation.
- Work on working copies only. If an original shows where a copy should be, stop and report.

## Honest limits
- Grouping by numbers can put different scenes together; check the pilot against a second photo of the group.
- A batch that "worked" on numbers can still look wrong. Offer samples and let Vijay's eyes decide.
