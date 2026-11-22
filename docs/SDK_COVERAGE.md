# Lightroom Classic SDK coverage

What the Lightroom Classic SDK (reference in `lr_sdk/API Reference`) offers, and which darkroom_ai tool exposes
it to the Hermes agent. Reviewed 2026-11-22 against SDK 15.x. "Unverified" means the code exists and is unit
tested against the simulated Lightroom but has not run in a real Lightroom yet.

Rule: darkroom_ai only provides granular tools. Judgement (what to remove, which preset, how far to go) lives in
the Hermes skills.

## Develop (LrDevelopController, LrPhoto develop settings)

| SDK capability | Tool | State |
|---|---|---|
| Sliders by `applyDevelopSettings` (tone, WB, HSL, detail, effects, calibration, curves) | `adjust_lighting`, `adjust_white_balance`, `adjust_hsl`, `adjust_tone_curve`, `set_develop_parameters`, `get_develop_parameters`, `list_develop_parameters` | verified |
| Crop and angle | `crop`, `plan_crop` | **verified live**: CropAngle = measured tilt (measure_tilt, clockwise positive) levels the picture (4.16 deg -> 0.0); fractions correct |
| **Auto Tone** (`setAutoTone`) and **Auto White Balance** (`setAutoWhiteBalance`) | `auto_correct` (one photo at a time, never copied) | **verified live 2026-11-22** (values differ per photo; undo_batch restores) |
| Develop presets (read, apply) | `list_presets`, `get_preset`, `apply_preset`, `try_presets` | verified except `try_presets` |
| Snapshots (create, list, apply) | `create_snapshot`, `list_snapshots`, `restore_snapshot` | **verified live** (restore puts the value back) |
| Reset (`resetAllDevelopAdjustments`, `resetMasking`, `resetSpotRemoval`, `resetCrop`, `resetTransforms`, `resetRedeye`) | `reset_develop` (snapshot first, go-ahead needed) | **verified live** (scope all; other scopes share the code) |
| Masks: AI selections (subject, sky, background, people, objects, landscape), range, gradient, radial; edit values; invert; delete | `create_mask`, `list_masks`, `inspect_masks`, `set_mask_adjustments`, `invert_mask`, `delete_mask` | verified |
| Distracting people detect and remove (`detectDistractingPeople`, `applyRemovalOnDetectedDistractingPeople`) | `remove_distracting_people` (snapshot first, go-ahead needed) | **blocked on this PC**: Lightroom opens the Remove panel and detects, then shows an Adobe error dialog ("can't complete your request"), most likely a model or service this offline PC cannot reach. The tool now selects the Remove tool itself and reports ADOBE_FEATURE_UNAVAILABLE with the next step. Use `remove_objects` instead |
| Spot / red-eye state (`RetouchAreas`, `RedEyeInfo`) | `get_retouch_state` | fixed after a live failure (empty list was sent as an object); recheck |
| AI panel getters (Enhance, Reflection Removal, Remove panel) | `get_ai_panel_state` (raw answers, to learn what this version offers) | **verified live**: Enhance panel answers; the Reflection Removal getter returns nothing on a JPG |
| AI Enhance (`toggleEnhance`: denoise, raw details, super resolution) | `enhance` (virtual copy, snapshot first, go-ahead needed) | **verified live**: Denoise on an ARW 33 s; Super Resolution on a JPG 14 s; Denoise is not offered for JPG; Raw Details cannot run alone (Lightroom applies it with the other two) |
| Reflection Removal (`toggleReflectionRemoval`) | `reflection_removal` (same safeguards) | **verified live**: preview quality took 15 s, history shows Reflection Removal On |
| Rotate left/right | `rotate_photo` | **verified live** (orientation AB <-> BC) |
| Background replacement, object removal, red-eye as new files | `replace_background`, `remove_objects`, `fix_red_eye` (Photoshop / OpenCV) | **verified live**: remove_objects (Photoshop Content-Aware PSD and OpenCV inpaint JPG); fix_red_eye runs (no red eyes in the test frame) |

## Library and catalog (LrCatalog, LrPhoto, LrCollection, LrKeyword, LrFolder)

| SDK capability | Tool | State |
|---|---|---|
| Catalog and folder/collection trees | `get_catalog_info`, `catalog_summary`, `list_catalogs` | verified |
| Find photos (filters: path, rating, flag, keyword, format, ISO, date) | `list_catalog_photos`, `find_photo`, `get_selected_photos` | verified |
| Metadata (read) | `get_photo_info`, `get_photo_metadata` | verified |
| Rating, flag, keywords | `get_library_state`, `set_library_state`, `batch_set_library_state`, `undo_library_change` | verified |
| Title, caption, headline, copyright, creator, colour label | `set_photo_metadata` | **verified live** |
| Keywords list | `list_keywords` | **verified live** |
| Search the catalog incl. colour label (`findPhotos`) | `find_photos` | **verified live** after a fix: labelColor takes a number 1-5 and an invalid value stalls Lightroom, so values are now checked against the SDK's types |
| Smart collections (`createSmartCollection`) | `create_smart_collection` | **verified live** (created and removed through undo_collection_change) |
| Smart previews (`buildSmartPreviews`) | `build_smart_previews` | runs without error; the preview file itself was not inspected |
| Metadata and file-naming presets | `list_catalog_presets`, `apply_metadata_preset` | file-naming list verified live. **Metadata presets: SDK limit** - on Lightroom 15.2.1 `LrApplication.metadataPresets()` returns an empty table even when a preset shows in Lightroom's own menu, and applying by id fails, so `apply_metadata_preset` returns `PRESET_NOT_FOUND`. Use `set_photo_metadata` for copyright and similar fields |
| Collections and sets, add/remove photos | `create_collection_set`, `create_collection`, `add_to_collection`, `remove_from_collection`, `get_collection_photos`, `undo_collection_change` | verified |
| Import from folder (no move/copy) | `scan_folder`, `import_folder` | verified |
| Virtual copies (working copies) | automatic in every edit tool | verified |
| Copy settings to other photos | `copy_develop_settings` (same values, chosen groups) | **verified live** |
| Preview and TIFF export | `get_preview`, `export_for_delivery` | **verified live** (2048 px JPEG) |

## Deliberately not exposed

| SDK capability | Why |
|---|---|
| `removeFromCatalog`, deleting photos or files | Destructive and not recoverable here. |
| `LrHttp`, `LrSocket` (outside the loopback bridge), `LrFtp`, publish services, tethering | The runtime is offline: no network calls other than loopback. |
| Export dialogs (`openExportDialog`), `LrExportSession` with arbitrary destinations | UI-driven and could write anywhere; `export_for_delivery` writes only to the exports folder. |
| Lens Blur (`setLensBlurBokeh`), eye correction | Unprobed; `get_ai_panel_state` reports what this version supports. Lens Blur sliders are plain develop parameters (`LensBlur*`). |
| Point Color swatch editing (`addPointColorSwatch`, `updateSelectedPointColorSwatch`) | Needs the Point Color tool open in the UI and an unprobed swatch table; the `PointColors` develop setting is readable with `get_develop_parameters`. |
| Per-spot read/write, Show Clipping overlay | Spot format unprobed (`get_retouch_state` counts spots); the overlay is UI only, clipping is measured by `analyze_photo`. |
| Quick Develop (`quickDevelop*`) | Duplicates the slider tools with weaker limits. |
| `LrUndo` (Lightroom's own history) | darkroom_ai keeps its own journal (`undo`, `undo_batch`); mixing both would confuse the history. |
| View and screen controls (`LrApplicationView` zoom, loupe, secondary display), view filters, active sources | UI state the person is using; only the Develop switch needed for masks, Auto and resets is made, and only while those run. |
| `LrApplication.shutdown`, backups, plug-in prefs, passwords, system info | Not photo editing. |
