# PRD addendum: catalog management by Hermes (draft, needs the owner's sign-off)

Status: APPROVED by the owner on 2026-11-22 (decisions in section 6). Build has not started.

## 1. Problem
The owner wants to hand Hermes a folder of photos and have Hermes do the cataloguing: see which catalogs exist, work in the
right one, bring the photos in, organise them, and then edit them. If some photos are already in the catalog, Hermes must
notice and carry on from what is there. Today Hermes can only edit photos that are already in the one configured catalog, and
it cannot import, organise masters, or even see which catalogs exist.

## 2. Success criteria
1. "Here is a folder": Hermes lists what is new, what is already in the open catalog and what is missing on disk, before anything is imported.
2. After a go-ahead, the new photos are in the open catalog (by reference: files are never moved, copied or changed).
3. Hermes organises them with the owner (collections, collection sets, keywords) following a structure the owner approved, and records what it did.
4. Photos that were already catalogued are found, measured and edited through the existing tools (analyze_batch, batch writes, undo).
5. Hermes lists the catalogs on this PC and says which one is open and whether writes are allowed there. It never writes to a catalog that is not on the owner's allowlist.
6. All of this is verified live on a throw-away catalog before any real catalog is allowlisted.

## 3. Scope
In (tools, all local and offline):
- `list_catalogs`: scan owner-named folders for `.lrcat` files (name, size, modified, allowlisted yes/no, open now yes/no). Reads file names only, never the contents of another catalog.
- `get_catalog_info`: the open catalog, its allowlist state, photo, collection and keyword counts, folders.
- `scan_folder`: image files in a folder (JPG, TIFF, PSD, RAW, DNG) compared with the open catalog by path: new, already imported, unreadable.
- `import_photos` (dry run first; a cap per call; go-ahead above a small number): `catalog:addPhoto` by path, journaled with the paths added.
- `create_collection_set`, `create_collection`, `add_to_collection`, `create_keyword`: organising the catalog without touching pixels.
- Skills: `lr-catalog-organisation` (inventory, duplicate check, plan with the owner, import, organise, hand over to editing) and `lr-catalog-partnering` (what to ask the owner, naming, notes kept per catalog). Updates to `lr-safe-operations` and `lr-user-guidance`.
Out: creating or switching catalogs from inside the SDK (impossible, see 4), deleting or moving photos, removing photos from a catalog, editing a catalog file directly, any network access.

## 4. Constraints and facts found (Lightroom SDK 15)
- The SDK has no call to create, open or switch a catalog, and none to remove a photo from one. So: Hermes cannot create or switch catalogs by itself, and an import cannot be taken back by our tools (the owner removes photos in Lightroom).
- `addPhoto`, `findPhotoByPath`, `getFolders`, `createCollection(Set)`, `createSmartCollection` and `createKeyword` exist.
- Hard rule to change: today ratings, flags and keywords are written only to virtual copies, never to masters. Collections and import do not change a photo's edits; ratings and keywords on masters are the open question below.
- The allowlist today is one catalog path in `darkroom.local.toml`, checked by the plug-in against `bridge.json`.
- Offline: file scanning and everything else stays on 127.0.0.1 and local disks.

## 5. Plan
1. Allowlist becomes a list (`lightroom.allowed_catalogs`), still git-ignored, still checked by the plug-in. Only the owner can add to it: a human CLI command, or an approval prompt in Hermes that the owner clicks. Hermes can never edit the list on its own.
2. Plug-in 0.13.0: read commands (catalog info, folder paths, collections) and the write commands above, with the same guards (timeouts, journal, dry run).
3. Python tools, fakes and unit tests first; then the two skills; then live runs on a NEW empty catalog (import the owner's folder, organise, edit, undo what can be undone).
4. Catalog creation and switching: v1 is a guided step (Hermes tells the owner exactly which Lightroom menu to use and checks the result with `get_catalog_info`). Optional v2: a copied empty template catalog opened by launching Lightroom with its path, plus a plug-in that starts its bridge by itself at launch; both are unverified.
5. Gate (`make quality`) green at each step, docs and compatibility table updated, one commit per step on `dev`.

## 6. Decisions (owner, 2026-11-22)
1. Masters: Hermes MAY write collections, keywords, ratings and flags on master photos (not only virtual copies). Bulk writes still need a plan and a go-ahead, are journaled with before-values, and are undoable through `undo`. This relaxes the earlier "virtual copies only for metadata" rule; pixels and files are never touched.
2. Imports: dry run first, at most 50 photos per call, go-ahead above 10, and a saved list of what was added (SDK cannot remove photos; the owner removes them in Lightroom if needed).
3. Catalog create and switch: guided steps only in v1 (Hermes names the exact Lightroom menu, then verifies with `get_catalog_info`). The experimental launch-with-path route is not built.
4. Allowlist: a list in config. A catalog is added by a human CLI command OR by an approval prompt Hermes raises that shows the full path with Yes/No. Hermes can never edit the list itself. The production catalog stays off the list until the owner adds it this way.
5. Folders scanned by `list_catalogs` (not answered by the owner; my default, change on request): the folders listed in `catalog_search_dirs` in `darkroom.local.toml` (git-ignored), default the drive root's first level plus the folders of allowlisted catalogs. Only `.lrcat` names, sizes and dates are read.

## 7. Risks
- A wrong allowlist entry gives write access to a real catalog: mitigated by owner-only changes and showing the full path.
- Large imports are slow and not undoable: caps, dry run and a manual-removal list.
- Duplicate photos (the same file in two folders, as in the `D:\Dhiva_23072023` copy) are imported twice: `scan_folder` reports same-name and same-size photos so Hermes can ask first.
- Lightroom is single-user and modal: an import started while a dialog is open can stall; every call has a timeout.
