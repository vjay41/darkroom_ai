---
name: lr-catalog-organisation
description: "How to bring a folder of photos into a Lightroom catalog and organise it with the owner: look first, dedupe, plan, import in small steps, build collections and keywords, then hand over to editing. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, catalog, organisation, knowledge]
    related_skills: [lr-catalog-partnering, lr-batch-operations, lr-safe-operations, lr-user-guidance]
    uses_tools: [get_catalog_info, list_catalogs, request_folder_access, scan_folder, import_folder, catalog_summary, list_catalog_photos, create_collection_set, create_collection, add_to_collection, remove_from_collection, get_collection_photos, create_keyword, list_keywords, find_photos, create_smart_collection, build_smart_previews, list_catalog_presets, apply_metadata_preset, set_photo_metadata, set_library_state, batch_set_library_state, undo_collection_change, undo_library_change, undo_batch]
---

# Organising a catalog from a folder of photos

The owner usually just drops photos in a folder and says what they want. You look, propose, ask, then do it in small,
undoable steps. Nothing here changes a photo file: import adds references, everything else is catalog data.

## 1. Look before touching
- `get_catalog_info`: which catalog is open, is it allowed (`writes_allowed`), what already exists (collections, folders, counts).
  If it is not allowed, stop and follow lr-catalog-partnering. Never work around a refusal.
- `scan_folder` on the owner's folder (recursive if they have sub-folders). It tells you new files, files already in the
  catalog, unsupported files and `same_name_count`: new files whose name already exists elsewhere in the catalog.
- If the folder is outside the allowed folders you get `PHOTO_OUTSIDE_ROOT`: call `request_folder_access`; the owner
  sees the full path and answers Yes or No.

## 2. Photos that are already there
Do not import them again. Read them: `catalog_summary` (cameras, dates, ratings, folders), `list_catalog_photos` with
filters (folder text, rating, flag, keyword, file format, ISO, capture dates). Offer editing or organising from what is
found ("you already have 80 of these, 12 are unrated"). Same-name files may be the same photo in another folder: ask
the owner before importing them.

## 3. Propose a structure, then ask
Suggest one simple structure drawn from the facts (capture date, folder names, camera, rating), for example sets by
year or event and collections inside. Show it as a short list with counts. Ask what to change. Prefer collections for
groups, keywords for facts about the subject ("Kyoto", "portrait"), ratings and flags only when the owner asks.
Names: plain, short, no slashes inside a name ("/" separates levels).

## 4. Import in small steps
`import_folder` adds at most 50 photos per call; more than 10 asks the owner for a go-ahead showing the count. Say how
many calls it will take. It cannot be undone by you: the journal keeps the list of added files, and the owner can remove
them in Lightroom. If one call fails, stop and report; do not loop.

## 5. Organise
`create_collection_set`, `create_collection`, then `add_to_collection` (more than 10 photos asks for a go-ahead).
`create_keyword` makes keywords or a hierarchy; `set_library_state` / `batch_set_library_state` put them on photos.
Rating, flag and keywords may be written to masters or copies: the photo whose id you pass is the one that changes.
Check each result (`verified`, `changed_ids`, `missing_ids`). Everything except imports and created keywords is undoable:
`undo_collection_change`, `undo_library_change`, `undo_batch`.

## 6. Hand over to editing
When organised, say what now exists and offer next steps. Editing then follows lr-batch-operations and lr-user-guidance
(working copies, small passes, measurements).

## Stop and ask
- The catalog is not allowed, or the open catalog is not the one the owner named.
- Counts differ from what the scan said, or a tool reports an error you do not understand.
- Anything the owner did not approve: other folders, deleting, moving files (you cannot, and must not suggest tricks).

## Titles, captions, labels
Use `list_keywords` to reuse the catalog's own keyword names before creating new ones. `set_photo_metadata` sets
title, caption, headline, copyright, creator and the colour label (catalog data only; it returns the earlier
values). Ask before writing text in the owner's name, and keep captions factual.

## Searching, smart collections, presets
`find_photos` searches the whole catalog (rating, pick flag, colour label, keyword, file format, ISO, capture
date; combine with intersect or union). Use it to answer "where are my red-labelled photos". When the owner wants
a search that keeps itself current, `create_smart_collection` saves the same conditions as a smart collection
(it only adds a collection). `build_smart_previews` makes small editable stand-ins for up to 25 photos so work can
continue when originals are offline. `list_catalog_presets` lists metadata or file-naming presets;
`apply_metadata_preset` overwrites the fields a preset fills in, so say which fields and ask first. Only presets saved inside Lightroom are listed (`list_catalog_presets`); if the one you need is missing (`PRESET_NOT_FOUND`), ask the owner to create it in Lightroom or set the fields with `set_photo_metadata`. All of these
are unverified on a live Lightroom yet.
