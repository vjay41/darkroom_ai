---
name: lr-catalog-partnering
description: "How to work with the owner on WHICH catalog to use: list catalogs, check what is allowed, ask for access, guide the owner through creating or opening a catalog, and keep notes. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, catalog, safety, knowledge]
    related_skills: [lr-catalog-organisation, lr-safe-operations, lr-user-guidance]
    uses_tools: [list_catalogs, get_catalog_info, request_catalog_access, request_folder_access, get_app_status]
---

# Choosing and maintaining catalogs with the owner

Catalogs are the owner's data. The owner decides which ones you may change; you propose, explain and ask.

## What you can and cannot do
- You CAN: list the catalogs on this PC (`list_catalogs`), describe the open one (`get_catalog_info`), ask the owner to
  allow a catalog or a photo folder (`request_catalog_access`, `request_folder_access`).
- You CANNOT create, open or switch a catalog: Lightroom does not let a plug-in do it. Say so plainly and guide the owner
  (the exact menu steps are in the `note` of `list_catalogs` and `get_catalog_info`). Do not try to drive Lightroom's menus
  or edit catalog files.
- You can never add to the allowlist yourself. The owner answers a prompt that shows the full path.

## Start of a session
1. `get_catalog_info`: what is open and is it allowed? Say it in one line ("Open: Family 2026, 5,400 photos, changes allowed").
2. If the owner talks about a different catalog or has none that fits, `list_catalogs`; show name, size, date, open or not,
   allowed or not. Ask which one they mean. Do not guess between similar names.
3. If the wanted catalog is not allowed: explain what allowing means (edits, imports, collections, keywords in that
   catalog; photo files never change), then call `request_catalog_access` with its full path. Wait for the answer. A No is final
   for this session: do not ask again unless the owner asks.
4. If it is allowed but not open: tell the owner the Lightroom steps (File > Open Catalog..., then File > Plug-in Extras >
   Start darkroom_ai Bridge) and check with `get_catalog_info` afterwards.
5. A new catalog: the owner creates it (File > New Catalog...). Then it must be allowed before you can organise it.

## Partnering habits
- Prefer the smallest access: one catalog, the folders actually needed.
- Be careful with the owner's main catalog: propose a pilot on a few photos or a copy of the catalog first, and say
  that imports cannot be undone by you.
- Keep notes in the conversation of decisions (which catalog, which structure, what was imported). On long jobs, restate
  them before big steps.
- Errors: report the exact code and the one next step: `LR_WRONG_CATALOG` or `LR_NOT_REACHABLE` mean the bridge or catalog
  needs the owner's action, not retries.
