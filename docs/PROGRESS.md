# Status, open threads and next steps

Living status. Keep it short and current: update it in every change that moves the project. The full chronological
build log (every spike, failure and live check up to 2026-11-22) is kept privately by the owner; the facts that matter
are summarised here and in `DESIGN.md`.

Last updated: 2026-11-22. Branch: `dev` (same as `master` at v0.3.0). Version `0.3.0`; plug-in 0.15.0. Verified on Windows 11, Python 3.13, Lightroom Classic
15.2.1, Photoshop 2026 (27.8), Hermes with a local model via LM Studio.

## Where it stands
All v1 build phases are done except the owner-only release steps. `make quality` is green on Windows and Linux
(about 430 tests, 8 skipped).

| Phase | Result |
|---|---|
| P0 Audit of par4987/lightroom-mcp | Done; selected SDK-only logic ported with attribution, all UI-automation code rejected |
| P1 Strip to MCP, P1b JPG white-balance fix | Done and verified live |
| P1c Compatibility layer | Done: capability probing, `compatibility.toml`, `selftest`, protocol handshake, `UPGRADE.md` |
| P2 Read tools | Done: metadata, paged catalog, develop state, generic parameter tools |
| P3 Corrections | Done: lens profile and chromatic aberration as parameters (RAW verified for Sony ARW and DNG) |
| P4 Masks | Done: gradient, radial, luminance, AI (subject/sky/background/objects/landscape), people; read-back after every write |
| P5 Presets | Done: tiered rule, exact undo |
| P6 Reliability | Done on the test catalog: 20-cycle soak, 0 failures; root cause of mask delays found (collapsed stacks) |
| P7 Knowledge layer | Done: eight skills installed and loaded; supervised edit-loop check passed |
| Batch step 1 | Done: read-only `analyze_batch` (verified live) and the `lr-batch-operations` skill |
| P8 Deploy kit | Done: `darkroom deploy`, `AGENTS.md`, `NOTICE`, install docs, this documentation set |

## Verified (evidence)
- People mask end to end, including the "wrong photo showing" guard.
- 20-cycle soak on the test catalog: 180 operations, 0 failures, slowest 2.7 s.
- Hermes with the skills on the test catalog: on a photo with 16.8% highlight clipping it stated its intent, made one
  small pass, measured clipping at 2.3%, kept the change, and reported what it left alone and how to undo.
- `analyze_batch`: four photos in one call, unknown id returned as a per-photo error without failing the call.
- Handshake and capability probe live: protocol 1 matches, no missing plug-in commands.
- `darkroom deploy --dry-run` on the real profile: nothing to write.

- Supervised portrait run (2026-11-22, TEST catalog, Hermes + Qwen3.8 27B): one portrait JPG, working copy only, 2 of 3
  passes used. Subject mask created and read back with the expected exposure value; lens profile toggle applied and held.
  Journal shows no errors. **Not verified:** which lens profile actually resolved (name reads null), per-face skin
  measurement (no face model by design; the agent sampled a region instead) and a visual check of the mask shape. The
  `inspect_masks` "Tools: {}" oddity was a defect of ours (output cut off three levels deep) and is fixed in plug-in 0.11.1. The +0.25 EV
  lift raised highlight clipping from 0.32% to 1.12%; the agent reported it and left it.

## Known limits (stated in the README too)
DNG was verified only on a file Lightroom converted from an ARW (not on camera or phone DNGs); colour and depth range masks unavailable through the SDK; face
detection is left to Lightroom's own people/subject masks (no model by design); one Lightroom client at a time; mask tools take over Develop briefly; a virtual
copy in a collapsed stack cannot be shown; Hermes needs a restart to see new tools; Adobe versions frozen until a
deliberate, drilled update.

## Release status
Released as **v0.1.0** on 2026-11-22 (early release: JPG verified, RAW untested at release time, test-catalog writes only).
**v0.3.0** (2026-11-22) adds: SDK coverage (catalog search and smart collections, smart previews, metadata presets, AI Enhance Denoise and Super Resolution, Reflection Removal, reset scopes, rotate, auto-adjust). Verified live on the rehearsal catalog and in a Hermes end-to-end run. **Blocked by the SDK:** `apply_metadata_preset` (checked live with a preset that Lightroom's own menu showed: the SDK listed none and applying by id failed; `set_photo_metadata` covers the use case) and `remove_distracting_people` (blocked: Lightroom shows an Adobe error while offline; the tool returns `ADOBE_FEATURE_UNAVAILABLE`). Raw Details cannot run alone (Lightroom applies it with Denoise or Super Resolution), so it is not offered.

**v0.2.0** (2026-11-22) adds: catalog management (allowlist, collections, keywords, imports), noise and sharpness measurement with two skills, `measure_region` with the skin-tone skill, S-curve and RAW verification, and fast catalog-wide reads (plug-in 0.13.1). All verified live on the rehearsal catalog only.

**v0.1.1** (2026-11-22) adds: library tools, tone-curve tools, batch writes with `undo_batch`, list filters (rating, flag, keyword, file format, ISO, capture date), RAW (ARW and DNG) verified, `inspect_masks` fix, and a connector that ends itself when stale and idle. All verified live on the test catalog only.

- Repository is public: https://github.com/vjay41/darkroom_ai (MIT, `LICENSE`).
- `dev` (default branch), `master` and tag `v0.1.0` all pointed at `3e236d9` when released; `master` was
  fast-forwarded from `dev` on the owner's explicit instruction. Never force-push or rewrite history.
- The production catalog stays off the allowlist until the owner decides; do one supervised run on a real photo first.
- Git history and `PRD.md` contain the owner's name and local paths (no secrets, photos or catalogs).

## Next steps (post-v1, in rough priority)
1. **Batch writes**: DONE (owner approved the addendum 2026-11-22). `batch_set_library_state`, `batch_set_develop_parameters`,
   `undo_batch`; verified live on three test photos (JPG + 2 ARW) and driven by Hermes itself (2026-11-22: the model chose the
   values, dry run, approval prompt, 2 of 2 applied, `undo_batch` read-back verified). Stopping at a mid-batch failure and
   at the time budget are verified live too (`scripts/batch_failure_live.py`; the failure and the clock are staged by the script,
   everything else is real Lightroom; 3 photos, JPG + 2 ARW, undo read-back verified). See `PRD_ADDENDUM_BATCH_WRITES.md` and `DESIGN.md` decision 16.
2. **Collapsed-stack detection**: DONE and verified live (plug-in 0.9.4, 2026-11-22). `get_photo`/`get_photo_info` report
   `in_stack`, `stack_collapsed` and a `stack_note` when Lightroom does not say; `create_mask` for kinds that need Develop
   refuses at once (`LR_BUSY_USER`, Expand All Stacks) instead of waiting a minute. Live: expanded stack -> collapsed=false;
   collapsed -> true and the mask call was refused in under 3 s with nothing created. The SDK's metadata calls yield, so
   they must run inside `LrTasks.pcall` (plain `pcall` fails). `darkroom doctor` also warns when the selected photo is in a collapsed stack.
3. **List filters**: rating, flag, keyword, file format, ISO and capture date are DONE and verified live (plug-in 0.12.0; 34 checks on 143 photos, JPG + RAW). There is deliberately no
   "edited" filter (Lightroom flags every virtual copy as edited).
4. **RAW**: Sony ARW verified 2026-11-22 (`scripts/raw_live.py`, 2 files, all checks). DNG verified the same day on `DSC00482.dng`, which Lightroom made from the ARW (the ARW file stays on disk; the test catalog now points at the DNG for that photo, so scripts that named `DSC00482.ARW` need the `.dng` path). The lens profile resolves by name on ARW and DNG (`Adobe (Sony FE 24-70mm F2.8 GM II)`).
6. **Lua syntax checks**: DONE. The repo has no CI service; the 8 tests run whenever `DARKROOM_LUAC` is set or `luac` is on PATH. Run on 2026-11-22 with the SDK's `luac.exe` (Lua 5.1): 25 passed, none skipped, plug-in 0.12.0.
7. Optional: `analyze_photo` region parameter; colour/depth range mask format capture if Adobe exposes it.

## Production catalog: supervised rehearsal
**Done 2026-11-22 on a separate rehearsal catalog, not on the owner's real catalog.** The owner pointed at the photo folder `D:\Dhiva_23072023` (240 photos: 236 JPG, 4 ARW; about 120 filenames also exist in the test catalog) instead of a catalog, so a NEW catalog `D:\darkroom_rehearsal\darkroom_rehearsal.lrcat` was made in Lightroom and the folder was imported into it by reference (Add; nothing copied or moved). `darkroom.local.toml` (git-ignored) now points at that catalog and folder; the previous values are saved in `outputs/darkroom.local.toml.test-catalog.bak`. Results: `scripts/rehearsal_live.py` passed on all 240 photos (every photo measured, filters match the metadata, 10 develop + 10 library edits verified and undone, all 240 files and the masters unchanged), and a Hermes-driven supervised run on one photo with 21% clipped highlights (Highlights -30 on a working copy, clipping 21.3% to 0.04%, undone and read back, no journal errors). The owner's real catalog (`Lightroom Catalog.lrcat`, seen in Lightroom's recent list) has NOT been touched and is still off the allowlist.
The original plan, if the real catalog is ever wanted:
1. Lightroom closed. Copy the real `.lrcat` (and nothing else) to a new folder, for example `D:\darkroom_rehearsal\rehearsal.lrcat`. The real catalog is only read by the copy command.
2. In the git-ignored `darkroom.local.toml`, point `lightroom.test_catalog` at the COPY and `lightroom.test_photos_dir` at the folder of the ONE photo to be used. Write nothing to `darkroom.toml`.
3. Open the copy in Lightroom, then Start darkroom_ai Bridge; `darkroom doctor` must say writes are allowed for exactly that catalog.
4. Supervised run with Hermes on that one photo: virtual copy only, one pass, the person looks at the result; then `undo_library_change` or the tool's undo.
5. Judge it: did the edit land on the copy, did the master stay unchanged, did the journal and undo match? Virtual copies stay (deleting is never done by the tool); delete them in the copy.
6. Only if the owner then says so: repeat the same config change for the real catalog, after a Lightroom catalog backup, and keep `test_photos_dir` limited to the folders the owner names.
Rollback at every step: restore `darkroom.local.toml` to the test catalog path and restart the bridge.

## Open threads and housekeeping
- `dev` is pushed. `master` was fast-forwarded to `dev` and tagged `v0.1.1` on the owner's explicit instruction (2026-11-22).
- Library tools use guardrail defaults of at most 20 keywords per call; tone curve moves at most 32 per pass and 64 in total (`darkroom.toml`). Treat them as starting values.
- Spike keywords `darkroom-ai-library-spike` and `darkroom-ai-filter-spike`, and extra virtual copies, remain in the TEST catalog; harmless.
- Hermes profile `darkroom` now has `lr-tone-curve`, the updated `lr-batch-operations` and the developer skill `darkroom-dev-review` (under software-development, read-and-propose only). A backup of the replaced skill is in the profile's `backups/`.
- Unverified: Windows `poetry install` itself, DNG files from other sources, the production catalog, and edit quality across photo types.
- **Stale tools in Hermes (found 2026-11-22).** Two separate causes, both seen live: (1) Hermes keeps the connector process running
  across its own restart, so it kept running pre-batch code; (2) Hermes caches the connector's tool list
  (`cache/mcp_schema_cache.json` in the profile) and kept offering 32 of 40 tools after the connector was current. Fixes: a
  stale idle connector now ends itself after 120 s (`runtime.stale_exit_after_s`, 0 = off) and Hermes starts a fresh one on its
  next call (verified live); after adding tools, toggle `darkroom_ai` off and on in Hermes' Capabilities, which refreshes the
  cache (verified live).
- A stray Photoshop instance may be open from an earlier test; it is not needed for v1.
- Two empty people masks may remain on one test-catalog working copy; harmless.
- The test catalog has working copies from supervised runs (one carries Highlights -10); keep or undo as you like.
- A few skills in an owner's Hermes profile outside this repo may still carry number-style guidance; they were given a
  "reference only, not a recipe" note.

## Decisions
Approved decisions and the design principles are in `DESIGN.md` (decision log) and `../PRD.md`. Record new decisions
there and add a dated line here.

## Change log (newest first, one line each)
- 2026-11-22: Lua syntax tests run with the SDK's `luac.exe`: all pass (plug-in 0.12.0); the test also finds `luac` on PATH.
- 2026-11-22: rehearsal on a new catalog of `D:\Dhiva_23072023` (240 photos): scripted run and a Hermes-driven supervised run passed (`scripts/rehearsal_live.py`).
- 2026-11-22: DNG develop writes verified live and the RAW lens profile confirmed to resolve (ARW and DNG).
- 2026-11-22: mid-batch failure and time budget verified live (`scripts/batch_failure_live.py`).
- 2026-11-22: connector ends itself when stale and idle (`runtime.stale_exit_*`); Hermes-driven batch run verified live (undo read-back verified).
- 2026-11-22: list filters for file format, ISO and capture date (plug-in 0.12.0), verified live.
- 2026-11-22: batch writes built and verified live (`scripts/batch_live.py`); `lr-batch-operations` 1.1.0 installed in the darkroom profile. Hermes needs a restart to see the three new tools.
- 2026-11-22: plug-in 0.11.1: `inspect_masks` no longer cuts off a mask's Tools list (depth 3 to 6); verified live.
- 2026-11-22: RAW (Sony ARW) develop writes, Kelvin white balance, lens toggles, library fields and undo verified live; `scripts/raw_live.py`. An aborted first run left one edited virtual copy in the test catalog.
- 2026-11-22: list filters (rating, flag, keyword) verified live on plug-in 0.11.0; `edited` filter dropped; batch-writes PRD addendum drafted.
- 2026-11-22: tone-curve tools (`get_tone_curve`, `adjust_tone_curve`) and `lr-tone-curve` skill; verified live.
- 2026-11-22: library tools (rating, pick/reject, keywords, undo) on virtual copies only; verified live (plug-in 0.10.0).
- 2026-11-22: collapsed-stack detection verified live (plug-in 0.9.4); mask tools refuse early.
- 2026-11-22: documentation set written; deploy kit, `NOTICE`, install guide; release-hygiene pass (skills no longer name the user).
- 2026-11-22: `analyze_batch` verified live; `lr-batch-operations` skill.
- 2026-11-21: P7 skills installed; edit-loop check passed; P6 soak passed (20 cycles, 0 failures); people mask verified;
  displayed-photo guard; P1c handshake and probe; soak runner; `--stop-all`.

## Catalog management (built 2026-11-22)
PRD: docs/PRD_ADDENDUM_CATALOG_MANAGEMENT.md (owner decisions in section 6). Built and verified live on the rehearsal catalog
(`scripts/catalog_live.py`, all checks pass; Hermes also drove it). Plug-in 0.13.0 (needs a full Lightroom restart to load).
- Allowlist: `bridge.json` holds `allowed_catalogs` and `photos_roots`; only the owner changes them: `darkroom lightroom allow --catalog PATH
  --photos-folder DIR`, or Hermes' `request_catalog_access` / `request_folder_access` (Yes/No prompt with the full path). The plug-in re-reads the
  lists on every request, so no restart is needed. `darkroom lightroom setup` keeps the owner's approvals.
- Tools: list_catalogs, get_catalog_info, request_catalog_access, request_folder_access, create_collection_set, create_collection, add_to_collection,
  remove_from_collection, get_collection_photos, create_keyword, undo_collection_change. import_folder capped at 50 per call (go-ahead above 10,
  added files kept in the journal). scan_folder flags same-name files already in the catalog.
- Rating, flag and keywords may now be written to masters (owner decision); the photo whose id is passed is the one that changes.
- Skills: lr-catalog-organisation, lr-catalog-partnering (installed in the Hermes profile, plus a note in `darkroom-ai-lightroom`).
- Bug found by the live run and fixed: create_keyword for "A/B" failed because a keyword made in the same write step cannot be a parent; each level is now its own step.
- Limits: Lightroom's SDK cannot create, open or switch catalogs or remove photos; Hermes guides the owner (File > Open Catalog / New Catalog, then Start Bridge).
- Still open: the owner's real catalog is NOT on the allowlist (the owner adds it with the command or the prompt); `list_catalogs` default search is the
  drive of the open catalog, the folders of allowed catalogs and Pictures (override with `catalog_search_dirs`).
- Leftovers in the rehearsal catalog: 8 imported scratch photos (from `D:\darkroom_rehearsal\import_test*`) and the keyword `DarkroomLiveTest/Live`.

## Noise reduction and sharpening (built 2026-11-22)
- Measurement: `analyze_photo` / `analyze_batch` now return `noise_luma`, `noise_chroma` (estimated on the flattest pixels) and `edge_strength`.
- Skills: lr-noise-reduction, lr-sharpening-clarity (installed in the Hermes profile; restart the connector to pick up the new numbers).
- Live pilot `scripts/detail_live.py` passes (see compatibility.toml). Lesson: mask edits render in Lightroom about 3 s late; the first run
  looked like "local noise reduction does nothing" because it measured too early. The skill now says to wait about 5 s.

## Skin tone, S-curve, RAW and large catalogs (built and verified 2026-11-22)
- New read tool `measure_region` (up to 6 boxes per call): CIELAB lightness, a*, b*, hue angle, chroma, median RGB, clipping and the spread
  between boxes, from a 1024 px render. No face model is needed: the agent looks at the preview and places the boxes (face detection stays
  optional with a user-provided YuNet file). New skill `lr-skin-tone` (installed in the Hermes profile).
- Live pilots (`scripts/portrait_live.py`, `scripts/detail_live.py --raw`) pass on a JPG and an ARW; Hermes drove a skin-tone job and a RAW
  noise + S-curve job through the connector, measured before and after, and undid everything (see compatibility.toml for the numbers).
- Defect found by the live runs and fixed: `list_photos` walked every photo with several calls each, so a 248-photo catalog took 7 s and timed
  out. Plug-in 0.13.1 uses one batch metadata call (0.1 s); the client gives catalog-wide reads 60 s. Needs a full Lightroom restart.
- Test-script lessons: RAW `Temperature` and `Tint` read as unset on an unedited master (compare only keys the master reports); the noise estimate
  stops falling near 0.3, so a further local drop cannot be measured there (the script says SKIP and checks the stored mask value instead).
- Hermes patched `lr-skin-tone` in its profile on its own (prefer a mask when other things share the skin's hue); the useful sentence was
  taken back into the repository skill.
- Still open: people masks for skin tone need the person chosen in Lightroom's panel; a catalog of tens of thousands of photos is untested;
  the owner's real catalog is not on the allowlist (by design).
- `master` was fast-forwarded to `dev` and tagged `v0.2.0` on the owner's explicit instruction (2026-11-22).

## 2026-11-22 - professional skills, preset tools, tilt measurement (offline, unverified live)
- Added tools: `get_preset`, `try_presets`, `get_preset_notes`, `save_preset_note` (local notes in `notes/`, git-ignored), `measure_tilt` (line-angle numbers, positive = clockwise).
- Added skills: lr-edit-workflow, lr-landscape, lr-event-photography, lr-wedding-indian, lr-wedding-western, lr-parties-celebrations, lr-travel-blog, lr-style-looks, lr-black-and-white, lr-preset-advisor. Skills stay directional (no slider-number recipes: repo rule).
- Architecture rule confirmed: darkroom_ai = granular tools only; judgement lives in Hermes skills.
- Unverified live (computer use stopped by the user): `try_presets` against real Lightroom, `measure_tilt` on real photos.
- Still to build: crop with aspect ratio/rotation (`plan_crop`), zone map for shadows, region view, object finder, object removal (Photoshop), spot/red-eye, export for delivery.

## 2026-11-22 - remaining granular tools (offline, unit-tested, NOT verified live)
- Tools: `plan_crop` (largest crop for an aspect ratio inside a rotated frame), `view_region`, `zone_map` (grid + dark regions with edge hardness), `find_candidates` (what stands out; measurements only), `remove_objects` (Photoshop Content-Aware Fill on a new layer, or OpenCV inpaint for small spots; new TIFF/JPG/PSD only), `fix_red_eye` (numeric, new files), `export_for_delivery` (JPEG via the plug-in export into `exports/`, never overwrites). `export_preview` now passes `quality` to the plug-in (the Lua side already supported it).
- Skills: lr-straighten-crop, lr-object-removal, lr-flash-shadows, lr-delivery-export.
- Decisions: Lightroom-native spot healing (`RetouchAreas`) and Upright are NOT used: their develop-setting formats are unprobed, so spot removal goes through Photoshop/inpaint instead. Auto tone/WB left to `analyze_photo` + `adjust_*` plus skills.
- Live checks needed (desktop): CropAngle sign and crop-fraction semantics with a rotated crop (`plan_crop` -> `crop` -> `measure_tilt`); `fill_content_aware.jsx` in Photoshop 27.8 (stringIDToTypeID contentAware); `export_preview` at 4096 px with quality; `try_presets`; `measure_tilt` on real photos; `zone_map`/`find_candidates` thresholds on real photos.

## 2026-11-22 - Lightroom-native automation and SDK coverage (offline, unit-tested, NOT verified live)
- `auto_correct`: Lightroom's own Auto Tone / Auto White Balance (`setAutoTone`, `setAutoWhiteBalance`) run on each photo separately (plug-in 0.14.0 `auto_adjust`), with per-pass guardrails, read-back, before/after measurements, journaling (`undo`, `undo_batch`) and a warning if all photos ended identical.
- New tools: `list_snapshots`, `create_snapshot`, `restore_snapshot`, `reset_develop`, `remove_distracting_people`, `get_retouch_state`, `get_ai_panel_state`, `rotate_photo`, `set_photo_metadata`, `list_keywords`, `copy_develop_settings`. New skills: lr-auto-correct, lr-save-points, lr-sync-look (plus updated edit-workflow, straighten-crop, object-removal, catalog-organisation).
- `docs/SDK_COVERAGE.md` maps the whole SDK to tools and lists what is deliberately not exposed and why.
- Needs a full Lightroom restart (plug-in 0.14.0). Live checks: Auto on JPG and RAW (does Auto change values per photo, how long it takes to settle), reset scopes, people removal, snapshot restore, metadata/colour label, rotate, `get_ai_panel_state` answers.

## 2026-11-22 - SDK items completed (offline, unit-tested, NOT verified live)
- Plug-in 0.15.0 (needs a full Lightroom restart). New tools: `find_photos` (incl. colour label), `create_smart_collection`, `build_smart_previews`, `list_catalog_presets`, `apply_metadata_preset`, `enhance` (Denoise / Raw Details / Super Resolution), `reflection_removal`. Searches use a whitelist of criteria and operations. `remove_distracting_people` now counts spots with `countAllSpots`.
- Owner decision: Hermes gets the full toolset; a backup of the setup is kept by the owner. Confirmations on file-changing tools stay (repo rule).
- Not exposed, with reasons in SDK_COVERAGE.md: Point Color swatch editing, per-spot edits, clipping overlay, Lens Blur bokeh, eye correction.
- New skill lr-ai-enhance; lr-catalog-organisation extended.
- Live checks needed: `enhance` and `reflection_removal` (does the callback fire, how long, snapshot restore afterwards), `create_smart_collection` criteria names (`labelColor`, `captureTime`, `keywords` operations), `find_photos`, `build_smart_previews`, `apply_metadata_preset`.
- Mistake caught and fixed during this step: a new module briefly overwrote existing `library_tools.py`; restored from git, new code lives in `catalog_search_tools.py`.

## 2026-11-22 - live functional test of plug-in 0.15.0 (rehearsal catalog, virtual copies only)
Run with `scripts/new_tools_live.py` (results in `outputs/new_tools_live.json`) and one Hermes-driven session.
- Passed live: connect, `get_ai_panel_state`, snapshot create/list/restore, `auto_correct` (per photo, undone with `undo_batch`), `reset_develop`, `set_photo_metadata`, `rotate_photo`, `find_photos`, `list_keywords`, `list_catalog_presets`, `create_smart_collection` (and its undo), `build_smart_previews`, `copy_develop_settings`, `measure_tilt`/`plan_crop`/`crop`, `zone_map`, `find_candidates`, `try_presets`, `export_for_delivery`, `reflection_removal` (15 s), `enhance` denoise on an ARW (33 s), `remove_objects` (Photoshop Content-Aware + inpaint), `fix_red_eye`, stdio MCP (`scripts/mcp_live.py`).
- Defects found by the live run and fixed: the plug-in is loaded from the repo folder, so it needs Plug-in Manager > Reload Plug-in plus Start Bridge after a code change (a stale 0.13.1 had been loaded); `find_photos` used a wrong value type for colour labels and hung Lightroom (now validated, uses the SDK criteria names, batch reads); `rotate_photo` read orientation from formatted metadata (now develop settings); `get_retouch_state` sent `{}` for an empty list; `fill_content_aware.jsx` failed on layer visibility (now loads the mask as a channel); `undo_collection_change` did not know smart collections; `remove_distracting_people` did not open the Remove tool first.
- Crop sign verified: the crop angle equal to measure_tilt's reading levels the picture (4.16 deg -> 0.0).
- Blocked on this PC: `remove_distracting_people`. Lightroom opens the Remove panel, then shows an Adobe error dialog; this probably needs a model or service the offline PC cannot reach. A modal dialog also stalls the bridge until it is dismissed, so the AI tools now answer ADOBE_FEATURE_UNAVAILABLE with the next step. No network change was made. Use `remove_objects`.
- Not verified: `apply_metadata_preset` (no metadata presets in the catalog), the smart-preview file contents, Super Resolution and Raw Details runs, other `reset_develop` scopes.
- Cleanup: the run left virtual copies in the rehearsal catalog (named "live..." and "darkroom_ai ...") and new files in `outputs/`; a stray smart collection was removed. Masters and original files were not changed.
- Hermes-driven end-to-end run (new session, Qwen3.8 27B, connector toggled to load plug-in 0.15.0 tools): `find_photos` (0 photos rated 3+, correct), `auto_correct` on DSC_2412-2 (per-slider before/after table), `undo_batch` with settings verified back to baseline. Hermes mis-copied one photo id once; the tool refused it and Hermes recovered. After a Hermes chat has used the connector, a separate script could not reach the bridge until the bridge was stopped and started again (one client at a time): run live scripts before or between Hermes sessions, or restart the bridge.
- Final state: the test copies were reset to unedited (`--only retouch_state,cleanup`); `get_retouch_state` rechecked after its fix. `dev` pushed to GitHub (24d6fa9 plus this note). `master` and tag v0.2.0 unchanged.
