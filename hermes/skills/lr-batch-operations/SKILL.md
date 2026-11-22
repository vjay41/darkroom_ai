---
name: lr-batch-operations
description: "How to work through many photos efficiently and safely in phases: scope, numeric triage, grouping, a pilot, chunked rollout with stop conditions, and a review. Process knowledge only; it never says what an edit should be."
version: 1.1.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, batch, knowledge]
    related_skills: [lr-safe-operations, lr-user-guidance, lr-genre-knowledge]
    uses_tools: [batch_set_library_state, batch_set_develop_parameters, undo_batch, get_library_state, set_library_state, undo_library_change, list_catalog_photos, analyze_batch, get_photo_metadata, get_preview, analyze_photo, get_develop_parameters, set_develop_parameters, undo]
---

# Many photos: work in phases, not in one go

Looking at every photo one by one is slow and expensive: each look costs minutes of model time. Editing
everything in one go is risky: one wrong idea is repeated everywhere. Work in phases and spend
attention where it matters. This is a process, not an editing recipe: each photo still gets its own
decision.

## Phases
1. **Scope (ask once).** Count what is involved (`list_catalog_photos`, filter by folder text, page with the
   offset). Say how many photos, the phases below and a rough time. Wait for the user's go-ahead before any write.
2. **Triage with numbers only.** Use `analyze_batch` (up to 25 photos per call, no pictures). Keep the numbers,
   not the images. If it returns `not_processed`, call again with those ids. Facts from `get_photo_metadata`
   (camera, ISO, file type) add context. Numbers are evidence; they never decide an edit alone.
3. **Group.** Put photos with similar scene and light together (same session, similar numbers). Mark outliers
   (very dark, very bright, odd cast, a different file type) to handle on their own.
4. **Pilot.** For each group, pick one representative and do the full careful loop: look, state the intent,
   one small pass, measure, compare, undo if worse. Show the user the result and get their approval before spreading it.
5. **Rollout in chunks.** Apply the approved idea to a small chunk (about ten photos), not the whole set.
   Choose the values per photo from its own numbers; identical values on photos with different exposure can
   hurt them. After each chunk, re-measure with `analyze_batch` and compare with the triage numbers.
   Stop and report at the first photo that got worse or errored. Do not retry in a loop.
6. **Review.** Show a small sample of before and after, list what was changed and which photos were left
   alone or flagged, and say how to take it back (`undo` per working copy).

## Stop conditions (stop, report, ask)
- A tool error that is not a single odd photo (Lightroom not reachable, wrong catalog, wrong photo showing).
- More than one photo in a chunk got worse after the same change: the idea is wrong for this group.
- Anything outside what the user approved (another folder, masks, presets, RAW files not covered by the pilot).
- A time or call budget that the user set is used up.

## Keeping it efficient
- Small previews are enough for triage; look closely at the pilot and flagged photos only.
- Remember which ids are done, so a resumed run skips them.
- One confirmation per chunk plan, not per photo; never skip the first.
- Develop edits go to working copies only; if an original shows where a copy should be, stop.

## Limits
- Numbers can mix scenes; check the pilot on a second photo.
- A batch that worked on numbers can still look wrong: offer samples; the user's eyes decide.

## Rating, flagging and keywords
`get_library_state` reads a photo's stars, pick/reject flag and keywords. `set_library_state` changes them,
`undo_library_change` takes this session's changes back. They change exactly the photo whose id you pass, a master
or a virtual copy (catalog data only; the file is never touched), and each change is read back: look at `verified` and `problems` in the result.
- Only set what the user asked for; never rate, reject or tag as a side effect of editing.
- A rating or flag is the user's taste, not a measurement: never infer one from numbers.
- Keywords are plain names; an existing keyword of that name (any letter case) is reused. A new one stays in
  Lightroom's keyword list even after undo, so keep names few and deliberate.
- For many photos, follow the phases above: confirm the plan and the exact words first, then do a small chunk.

## Batch tools: one go-ahead for a chunk
`batch_set_library_state` (rating, flag, keywords) and `batch_set_develop_parameters` (exposure, tone, white balance,
other settings) apply values you choose, each photo with its own, in one call. Every value still goes through the
single-photo limits.
- **Plan first:** `dry_run` shows the plan: which working copies exist, how many new ones a develop batch would create,
  and any photo not allowed. One bad photo means nothing runs; fix the list and plan again.
- **One chunk, one go-ahead:** the tool asks the person itself (a develop batch always, a library batch above a few
  photos). Say the count, the exact change and the new copies first. If declined, nothing changed; do not ask again.
- **Sizes:** up to 25 photos for library fields, 10 for develop edits. Pilot with the single-photo tools first, then
  roll out in chunks and re-measure with `analyze_batch` between them.
- **It stops at the first failure or the time budget.** Read `items` (`ok`, `failed` with reason and step, `not_run`),
  report, and do not retry in a loop.
- **Taking it back:** keep the `batch_id`. `undo_batch` restores and reads back the whole batch (this session only).
  Working copies a batch created stay in the catalog; say how many.
