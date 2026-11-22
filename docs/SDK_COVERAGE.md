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
| Crop and angle | `crop`, `plan_crop` | crop verified; angle sign and fraction convention unverified |
| **Auto Tone** (`setAutoTone`) and **Auto White Balance** (`setAutoWhiteBalance`) | `auto_correct` (one photo at a time, never copied) | unverified |
| Develop presets (read, apply) | `list_presets`, `get_preset`, `apply_preset`, `try_presets` | verified except `try_presets` |
| Snapshots (create, list, apply) | `create_snapshot`, `list_snapshots`, `restore_snapshot` | create/list verified earlier; restore unverified |
| Reset (`resetAllDevelopAdjustments`, `resetMasking`, `resetSpotRemoval`, `resetCrop`, `resetTransforms`, `resetRedeye`) | `reset_develop` (snapshot first, go-ahead needed) | unverified |
| Masks: AI selections (subject, sky, background, people, objects, landscape), range, gradient, radial; edit values; invert; delete | `create_mask`, `list_masks`, `inspect_masks`, `set_mask_adjustments`, `invert_mask`, `delete_mask` | verified |
| Distracting people detect and remove (`detectDistractingPeople`, `applyRemovalOnDetectedDistractingPeople`) | `remove_distracting_people` (snapshot first, go-ahead needed) | unverified |
| Spot / red-eye state (`RetouchAreas`, `RedEyeInfo`) | `get_retouch_state` | unverified |
| AI panel getters (Enhance, Reflection Removal, Remove panel) | `get_ai_panel_state` (raw answers, to learn what this version offers) | unverified |
| Rotate left/right | `rotate_photo` | unverified |
| Background replacement, object removal, red-eye as new files | `replace_background`, `remove_objects`, `fix_red_eye` (Photoshop / OpenCV) | background verified; others unverified |

## Library and catalog (LrCatalog, LrPhoto, LrCollection, LrKeyword, LrFolder)

| SDK capability | Tool | State |
|---|---|---|
| Catalog and folder/collection trees | `get_catalog_info`, `catalog_summary`, `list_catalogs` | verified |
| Find photos (filters: path, rating, flag, keyword, format, ISO, date) | `list_catalog_photos`, `find_photo`, `get_selected_photos` | verified |
| Metadata (read) | `get_photo_info`, `get_photo_metadata` | verified |
| Rating, flag, keywords | `get_library_state`, `set_library_state`, `batch_set_library_state`, `undo_library_change` | verified |
| Title, caption, headline, copyright, creator, colour label | `set_photo_metadata` | unverified |
| Keywords list | `list_keywords` | unverified |
| Collections and sets, add/remove photos | `create_collection_set`, `create_collection`, `add_to_collection`, `remove_from_collection`, `get_collection_photos`, `undo_collection_change` | verified |
| Import from folder (no move/copy) | `scan_folder`, `import_folder` | verified |
| Virtual copies (working copies) | automatic in every edit tool | verified |
| Copy settings to other photos | `copy_develop_settings` (same values, chosen groups) | unverified |
| Preview and TIFF export | `get_preview`, `export_for_delivery` | preview verified; delivery export unverified |

## Deliberately not exposed

| SDK capability | Why |
|---|---|
| `removeFromCatalog`, deleting photos or files | Destructive and not recoverable here. |
| `LrHttp`, `LrSocket` (outside the loopback bridge), `LrFtp`, publish services, tethering | The runtime is offline: no network calls other than loopback. |
| Export dialogs (`openExportDialog`), `LrExportSession` with arbitrary destinations | UI-driven and could write anywhere; `export_for_delivery` writes only to the exports folder. |
| Enhance / Denoise (`toggleEnhance`, `changeDenoiseAmount`), Super Resolution | Creates new DNG files and is GPU-heavy; read the state with `get_ai_panel_state` first, then decide. |
| Reflection Removal, Lens Blur, eye correction | Unprobed; `get_ai_panel_state` reports what this version supports. |
| Quick Develop (`quickDevelop*`) | Duplicates the slider tools with weaker limits. |
| `LrUndo` (Lightroom's own history) | darkroom_ai keeps its own journal (`undo`, `undo_batch`); mixing both would confuse the history. |
| View and screen controls (`LrApplicationView` zoom, loupe, secondary display), view filters, active sources | UI state the person is using; only the Develop switch needed for masks, Auto and resets is made, and only while those run. |
| `LrApplication.shutdown`, backups, plug-in prefs, passwords, system info | Not photo editing. |
| Smart collections, smart previews | Candidates for later: catalog-only and low risk, but not needed for editing. |
