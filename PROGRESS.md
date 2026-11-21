# darkroom_ai — progress log

Running log, updated at the end of every phase. PRD.md section 12 is edited only when one of its open questions is answered.
Plan of record: phases 0-8 with a GATE after each (stop and wait for an explicit "proceed").

## CURRENT STATE — 2026-11-21 07:45: hand-off spike PASSED end to end; waiting for the user

Do not push `dev`. Before touching Lightroom or Photoshop for a new step, show the plan and wait for the user's go.

### Phase status
| Phase | Status |
|---|---|
| 0 Scaffold | Done |
| 1 lightroom-cli review | Done (not adopted; own plug-in) |
| 2 Lightroom spike | Done; snapshot apply does not work, undo = settings journal |
| 3 Photoshop spike | Done (raw pywin32, child-process deadline, auto-start) |
| 4 Imaging toolkit | Hand-off spike DONE (Lightroom 16-bit TIFF -> Photoshop mask -> PSD + JPG). The imaging toolkit (analysis, ONNX mask model on GPU 1) has not been started. |
| 5-8 | Not started |

### Catalog facts (corrects earlier counts in this file)
- The user imported the test copies by hand on 2026-11-21. They are in the catalog as `D:\darkroom_test\TEST_2457-37.jpg` ...
  `TEST_2467-47.jpg` (same SHA-256 as the `DSC_...` copies, which are in the catalog too).
- The catalog had 79 photos at that point (user's count; the catalog database read-only shows 78 files + 1 virtual copy).
  After the hand-off run it has 80 (one more virtual copy). Earlier counts in this file (62, 67, 68, 72) were snapshots of
  earlier states and are stale.
- By folder: 67 files in `D:\Photos\Gallery\101D7500_Processed` (off limits), 10 in `D:\darkroom_test`, and 1 stray file in
  `D:\darkroom_test\lr_sdk\API Reference` (an SDK picture that came in with the import; not used).

### Hand-off spike result (2026-11-21 07:43, on a virtual copy of `TEST_2457-37.jpg`)
- Selftest: plug-in build 0.2.0; `list_commands` shows `export_tiff` among the write commands.
- Lightroom: virtual copy `darkroom handoff 20261121-074305`; `export_tiff` took 0.7 s; TIFF header 16/16/16 bits per sample,
  5568 x 3712, embedded profile "sRGB IEC61966-2.1" (sRGB was requested), 124.0 MB (uncompressed), written to
  `outputs\handoff`. So Lightroom does write a 16-bit TIFF from a JPG source.
- Guards verified LIVE in Lightroom: export from the master refused (`NOT_A_VIRTUAL_COPY`); a second export with the same name
  refused (`FILE_EXISTS`); a develop edit on the master refused (`NOT_A_VIRTUAL_COPY`); earlier, a write on a gallery photo
  refused (`PHOTO_OUTSIDE_ROOT`).
- Develop edit on the virtual copy: `Exposure2012` 0 -> 0.3 -> 0 (reverted from the recorded value); the master stayed at 0.
- Photoshop 27.8.0: opened a COPY of the TIFF (16 bit), layer mask from the PNG, fill layer below, new PSD + flattened JPG,
  closed without saving. Numeric checks passed (centre unchanged 85.8/130.1/123.2; corner 200/30/31 vs fill 200/30/30).
- Original `TEST_2457-37.jpg`: same size, modified time and SHA-256 (`F8BFDC77...46F8`) before the run, after the export and
  after the edit + revert; no `.xmp` sidecar.

### Wrong-catalog refusal test — DONE live (2026-11-21 08:00), PASSED
- `bridge.json` backed up outside the repo, pointed at `D:\darkroom_test\DOES_NOT_EXIST\Dummy.lrcat`, restored in a `finally`
  block (byte-identical afterwards, backup deleted).
- With the dummy path: `ping` reported the dummy as configured and writes NOT allowed; reads still worked; a no-op write
  (`Exposure2012` set to its current value on the `TEST_2457-37.jpg` virtual copy) was refused with `LR_WRONG_CATALOG`; the
  setting was unchanged. After the restore: writes allowed again and the same no-op write succeeded.
- The plug-in picks up `bridge.json` when the next client connects (no reload needed).
- Client bug found by the first attempt of this test and fixed: a second client object created right after another one closed
  (same process) connected at once, got one socket of the plug-in's old session and one of the new (the two ports come back
  about 0.5 s apart), and a fixed 1 s retry never recovered. Now the last-disconnect time is shared process-wide, slow connects
  are abandoned, retries back off (1.0 / 1.5 / 2.2 / 3.0 x), and connecting has its own deadline (`lightroom.connect_timeout_s`,
  20 s) separate from the command timeout. Verified against Lightroom: back-to-back clients 6/6.

### Open threads
- Rules in force: edits only on virtual copies of photos under `D:\darkroom_test`; gallery photos are off limits; Photoshop
  works only on copies inside `outputs\photoshop`; no add-photos command (user decision).
- Not tested: which TIFF compression key spelling Lightroom honours (the file is uncompressed, as intended, but both
  spellings were sent); AdobeRGB / ProPhotoRGB export; RAW files; selection restore when nothing was selected; a real
  Photoshop start/sign-in screen (unit tests only).
- Left behind: test catalog has virtual copies `darkroom handoff 20261121-074305` (of `TEST_2457-37.jpg`, settings back to
  original) and `darkroom 20261120-233520` (of a gallery photo, from the Phase 2 spike) with snapshot `darkroom-before`.
  `outputs\` holds the spike files (git-ignored). Lightroom and Photoshop were left running.

### Next step
Phase 4 proper: imaging toolkit (preview analysis: histogram, clipping, colour cast, face/skin-tone level) and the local mask
model via ONNX on GPU 1. Needs from the user: the path of the mask ONNX model file (nothing is downloaded). Start with a short
plan and wait for the go.

## Phase 0 — Scaffold (2026-11-20) — DONE, waiting at GATE

### Done
- Branch `dev` created from `master`; Poetry 2.2.1 project (`pyproject.toml`, PEP 621, src layout, `requires-python >=3.13,<3.15`),
  using the existing in-project `.venv` (Python 3.13.14); `poetry.lock` committed.
- `poetry check`, `poetry lock`, `poetry install --with dev`, `poetry run pip check` all clean (76 packages installed; ml extra NOT installed yet).
- Makefile (single-command recipes) + `scripts/make_help.py`, `scripts/clean.py`. Ran: help, quality, doctor, offline-check, cov, build, clean-all.
- `.gitignore` extended (build output, logs, outputs, previews, models, `*.onnx`, `*.gguf`, `test_catalog/`, `*.lrcat*`, `*.lrdata/`, `darkroom.local.toml`).
- Code: `errors.py` (PRD s8 codes + 6 internal codes), `net/loopback.py` (only module allowed to open connections),
  `config.py` (TOML loader, rejects non-local `base_url` and Lightroom host), `safety/journal.py` (append-only JSONL),
  `interfaces.py` (`ActionJournal` Protocol), `cli.py` (`--version`, config-only `doctor`), example `darkroom.toml`.
- Tests: 121 passing, 99% coverage. Offline guard blocks non-loopback `connect`, `connect_ex`, UDP `sendto`, name lookups and
  asyncio (IOCP) connections; AST scan forbids network/cloud/telemetry imports outside `net/loopback.py`; scan forbids model names
  and `0.0.0.0` in `src/`.

### Decisions
- HTTP client for the LLM: httpx2 behind `net/loopback.py` (PRD s10 to be reworded to "no non-loopback traffic").
- `mcp` stays in core, stdio transport only. scikit-image is not a direct dependency.
- Packaging per the user's amendment: Poetry, groups main / win32-marked (pywin32, comtypes, photoshop-python-api) / extra `ml` / dev.
- Dependency injection: collaborators are passed through constructors against Protocols in `interfaces.py`; fakes live in `tests/fakes.py`.
  Protocols for Lightroom, Photoshop, imaging and the LLM are added in the phase that first verifies their real behavior.
- Direct dependencies are pinned exactly in `pyproject.toml`; `poetry.lock` pins the rest with hashes.
- Git: commit only to `dev`, never push without the user's say-so, check staged files before every commit.

### Follow-up after the gate (2026-11-20)
- CLAUDE.md and PRD.md updated as approved (Poetry, Makefile, Python `>=3.13,<3.15`, PRD s10 wording, scikit-image, git rules).
- `.gitattributes` (`* text=auto`) added; `pytest-timeout` 2.4.0 added to dev (60 s per test).
- `rembg` removed from the `ml` extra (user decision): masks use onnxruntime directly on user-provided ONNX files; no automatic
  model downloads. To be confirmed again in the Phase 4 plan. `requests` remains in the lock only through pip-audit (dev).

### Open threads
- `mcp` pulls `opentelemetry-api` (interface only); Phase 7 must prove it and uvicorn open no connection.
- Phase 6 httpx2 client factory: `trust_env=False`, no proxy mounts, tested with `HTTP_PROXY` set (user requirement).
- `vision` / `tool_mode` values in `darkroom.toml` are unverified starting values (Phase 6).
- Unverified: Python 3.14 (only 3.13 was run); the ml extra install and CUDA 13 loading (Phase 4); everything involving Adobe apps or a model.

## Phase 1 — lightroom-cli source review (2026-11-20) — DONE, waiting at GATE

Read-only review; nothing from it was imported, run, built or installed. Files live in a temp folder outside the project.

### What was reviewed
- PyPI `lightroom-cli` 1.2.2: sdist sha256 `fbcd1aee…9a2b`, wheel sha256 `417d6ef0…d1b5` (both match PyPI's digests).
- Git tag `v1.2.2` = commit `a4fc4f65a14ef3fc8e7b495b11a795c94020b19e` (2026-04-06).
- sdist vs tag: identical content for every shared file (differences were line endings from my checkout only). The tag additionally has
  CI workflows, docs, a Claude plugin/skill folder, version scripts and e2e/integration tests. Wheel files are identical to the sdist.

### Findings
- **Lua plugin network**: only `LrSocket.bind` with `address = "localhost"`, two fixed ports (54322 send, 54323 receive). No `LrHttp`,
  no process execution (`LrTasks.execute`, `LrShell`, `os.execute`), no `loadstring`/`dofile`. Whether "localhost" really binds
  loopback-only is unverified until Phase 2.
- **Lua plugin files**: writes/deletes one port file `lightroom_ports.txt` in the temp folder; logs through `LrLogger` at debug level,
  including every command's parameters (file paths, metadata) in Lightroom's log folder.
- **No authentication**: any local process can connect and call ~150 commands, including `catalog.removeFromCatalog`, with no check of
  which catalog is open (no command returns the catalog path).
- **Python side**: `asyncio.open_connection` to host `localhost` (host is a parameter, so not forced local); no HTTP client, no
  subprocess, no telemetry. `lr plugin install` deletes and replaces the plugin folder under `%APPDATA%\Adobe\Lightroom\Modules`;
  `lr mcp install` edits Claude Desktop's config file.
- **Dependencies**: click, rich, pydantic, platformdirs, fastmcp (a large MCP/web stack we do not otherwise need). Top-level
  package names are generic (`cli`, `mcp_server`).
- **Fit for our needs** (from reading; unverified in Lightroom):
  - Develop edits go through `LrDevelopController` (acts on the active photo in the Develop module and changes the selection);
    `getSettings` refuses photos that are not RAW/DNG or a virtual copy, which conflicts with our JPG default.
  - No snapshot apply/revert or snapshot listing; `createVirtualCopy` calls `photo:createVirtualCopy()`, which I could not confirm in the SDK.
  - Previews come from `requestJpegThumbnail` as base64 over the socket (not an export to file; may lag behind edits).
  - Hand-written JSON: the encoder does not escape backslashes or control characters (Windows paths would produce invalid JSON);
    the decoder does not unescape strings and does not handle nested arrays.
- **Windows**: its changelog says Windows support is "not yet tested on real hardware". Plugin `Info.lua` still reports 1.2.1.
- **License**: MIT (LICENSE file, copyright "motokiendo"; PyPI author "M.Endo").

### Recommendation
Write our own small plugin (ping, catalog info, selected photo, virtual copy, snapshot create/apply, set/get Develop settings, export
preview to file), reusing lightroom-cli's two-socket `LrSocket` pattern as reference with MIT attribution. Do not install lightroom-cli.

### Decision (user, 2026-11-20)
Approved: write our OWN Lightroom plugin; lightroom-cli is not installed. PRD 5.1 and the matching open question in PRD 12 updated.

### Open threads for Phase 2
- Confirm with `Get-NetTCPConnection` that `LrSocket.bind` listens on loopback only.
- Add what lightroom-cli lacks: catalog-path check before any write, a shared token per request, proper JSON escaping.
- Find out which Develop operations work without the Develop module (`photo:applyDevelopSettings` vs `LrDevelopController`).

### Next step
Phase 2 (below).

## Phase 2 — Lightroom spike (2026-11-20) — DONE (one item failed: snapshot apply), waiting at GATE

### Done (no Lightroom interaction)
- SDK reference: user-provided Lightroom Classic 15.2 SDK at `D:\darkroom_test\lr_sdk` (API Reference HTML + SDK Guide PDF). Read for
  every call used. Confirmed there: snapshots exist (`createDevelopSnapshot`, `getDevelopSnapshots`, `applyDevelopSnapshot`);
  `LrSocket.bind` listens on localhost only (no address parameter); `catalog:createVirtualCopies` works on the selection only;
  `catalog:findPhotoByUuid` is the documented lookup (`getPhotoByLocalId` is not documented).
- Plug-in `src/darkroom_ai/adapters/lightroom/plugin/darkroom.lrplugin` (Info, Init, Stop, Shutdown, Bridge, Commands, Json):
  per-request token, write refusal unless the open catalog equals `catalog_path` in `bridge.json`, photos addressed by UUID,
  LrPhoto settings calls, previews exported to files in the configured preview folder only, one request at a time, no
  parameters or token in the log. All files pass the SDK's `luac -p` syntax check (Lua 5.1.5).
- Python: `LightroomClient` (deadline on every call -> `LR_NOT_REACHABLE`), `bridge_file.py`, result models, `LightroomAdapter`
  Protocol, `darkroom lightroom setup|ping`, error codes `LR_WRONG_CATALOG` and `LR_COMMAND_FAILED`.
- Tests: fake plug-in server speaking the same protocol; 160 tests pass; `make quality` clean.
- `scripts/lightroom_spike.py`: read-only by default, edit steps only with `--write`.

### Decisions
- Photo ID = the photo's UUID (documented lookup). The catalog's numeric local ID is reported but not used for addressing.
- Virtual copy: the plug-in saves the selection, selects only the target photo, verifies that, creates the copy, restores the
  selection; if it cannot select exactly the target it refuses and creates nothing.
- `bridge.json` lives in the plug-in folder (git-ignored) and is re-read whenever a client connects.
- Preview export counts as a write (refused on the wrong catalog) and never overwrites: Lightroom renames on collision.

### Open threads
- RESOLVED (user, 2026-11-20): the TEST catalog is
  `D:\darkroom_test\photos\D7500UMNSG_NSGTH(En)07.pdf\D7500UMNSG_NSGTH(En)07.pdf.lrcat` (the earlier `catalog\DarkroomTest.lrcat`
  path does not exist). The five JPGs in `D:\darkroom_test` are copies but are NOT imported into the catalog (checked by file
  path on 2026-11-20). `darkroom.local.toml` and `bridge.json` are written (both git-ignored).
- Read-only spike run in Lightroom 15.2.1 (2026-11-20 23:0x) — PASSED:
  - Loopback only: `Get-NetTCPConnection` shows both ports on `127.0.0.1`, owned by `Lightroom` (no other address).
  - `ping` (catalog path = configured path, writes allowed), JSON round trip inside Lightroom (backslashes, quotes, unicode,
    nested arrays), wrong token rejected (`UNAUTHORIZED`), selection read by UUID, Develop settings read on a JPG.
  - `probe`: every planned LrPhoto/LrCatalog method exists (snapshots create/list/apply/delete, `applyDevelopSettings`,
    `createVirtualCopies`, `findPhotoByUuid`; the undocumented `getPhotoByLocalId` exists too but is not used).
- Three bugs found and fixed while getting there:
  1. Lightroom's Lua raises an error on reading an undefined global -> the plug-in never started. Globals are read under `pcall`.
  2. "Reload Plug-in": the old instance's shutdown stopped the new run loop. Run loops are now generation-based.
  3. Reconnecting within ~0.3 s of a disconnect hits sockets the plug-in is still closing (measured: 1 s is reliable).
     The client now waits 1 s after a disconnect and proves the link with a `ping` before sending a real command.
- Two more plug-in bugs found and fixed (verified in Lightroom at 23:22):
  4. "Reload Plug-in" creates a NEW global environment and leaves the old run loop alive (SDK Guide p.32 confirms the new
     environment). Run loops now stop when a newer instance id appears in the shared plug-in preferences (`LrPrefs`).
  5. `LrForceInitPlugin` only runs the init script at launch if the plug-in contributes a menu item (SDK Guide p.23). Added
     File > Plug-in Extras > Start/Stop. The plug-in now starts by itself when Lightroom starts.
- Safety additions: `lightroom.test_photos_dir` (the spike refuses edit steps for photos outside it) and a `find_photo` read
  command (look-up by file path, so the selection is not needed).
- WAITING (edit steps NOT run): the five test copies in `D:\darkroom_test` are NOT in the catalog (`find_photo` returns nothing
  for all five; a known catalog photo is found, so the look-up works). The user has to import them with "Add". The photo that
  was selected earlier, `D:\Photos\Gallery\101D7500_Processed\DSC_3533_final.jpg`, is not a test copy and will not be edited.
- Plug-in log location on this machine: `%LOCALAPPDATA%\Adobe\Lightroom\Logs\LrClassicLogs\darkroom_ai.log`.
- `test_lua_syntax` runs only when `DARKROOM_LUAC` points at the SDK's `luac.exe`.

### Spike result (run in Lightroom 15.2.1, 2026-11-20 23:35-23:40) — waiting at GATE
The user explicitly approved using `D:\Photos\Gallery\101D7500_Processed\DSC_3533_final.jpg` (the five test copies were never
imported). All writes went to a virtual copy of it.

Verified (with evidence from the run):
- Virtual copy created by photo ID; the plug-in had to switch the grid to "All Photographs" because the photo was not in the
  current view (a photo that is not shown cannot be selected), then switched back.
- Snapshot `darkroom-before` created (`createDevelopSnapshot` in a write gate).
- Develop settings applied to a JPG virtual copy with `photo:applyDevelopSettings` from the Library module (no Develop module
  needed): `Exposure2012` 0 -> 0.30, read back; the original stayed at 0.
- Preview export to a file via `LrExportSession` + `renditions()` + `waitForRender()`: JPEG, long edge <= 1024 px, in `previews/`.
- The edit is visible: mean brightness 97.75 -> 108.34; after undo exactly 97.75.
- Undo by re-applying the journaled "before" settings works.
- The original file is byte-identical after all runs (same size, mtime and SHA-256; no `.xmp` sidecar).
- Loopback only: both ports on `127.0.0.1`, owned by Lightroom; no other local address.
- Wrong token rejected; unknown photo ID refused; wrong-catalog refusal is covered by unit tests against the fake only
  (NOT exercised in Lightroom - it needs bridge.json pointed at a dummy path).

Failed / not working:
- `photo:applyDevelopSnapshot` had no effect on the virtual copy in all four variants (local or global snapshot id, inside or
  outside a write gate; no error raised). Undo therefore uses the before/after settings journal; the snapshot is kept as a
  marker the user can click in Lightroom. Untested: applying a snapshot to a master photo.

Findings that change later phases:
- The effective Basic-panel keys are the `...2012` ones (`Exposure2012`, `Contrast2012`, `Highlights2012`, `Shadows2012`,
  `Whites2012`, `Blacks2012`, `Clarity2012`); legacy `Exposure` is stored but inert. Process version here is 15.4. Phase 5 needs
  a name map and must check `ProcessVersion`.
- Snapshots are shared between a master and its virtual copies (the snapshot made on the copy also lists on the original).
- Reconnect needs ~1 s after a disconnect; the plug-in serves one client at a time.
- Plug-in start-up needs a menu item for `LrForceInitPlugin`; "Reload Plug-in" creates a new environment (see bugs 1-5 above).

Unverified:
- Selection restore after a virtual copy when nothing was selected before (one run ended with the first grid photo selected;
  a longer wait was added but not re-tested).
- RAW files; photos hidden by a Library filter; more than one client; long exports; behavior with a dialog open in Lightroom.
- Whether "Automatically write changes into XMP" is off in this catalog (the file hash did not change in this run).

Left in the test catalog: one virtual copy `darkroom 20261120-233520` (settings back to Exposure 0), one snapshot
`darkroom-before`, history steps on the copy. Ten preview JPEGs in `previews/` (git-ignored). Nothing was deleted.

### Decisions after the gate (user, 2026-11-20)
- Undo = settings journal; the snapshot stays a marker; no more time on snapshot apply.
- PRD 5.1 updated with the spike findings (virtual copies only, `...2012` keys / process version 15.4, ~1 s reconnect delay,
  shared snapshots).
- Edits go to files under `test_photos_dir` only. The one-off approval for `DSC_3533_final.jpg` is REVOKED; the spike's
  override flag was removed, and the plug-in itself now enforces the folder (`photos_root` in `bridge.json`) and refuses
  develop edits on photos that are not virtual copies.

### Open threads carried forward
- BEFORE PHASE 5: run the wrong-catalog refusal live in Lightroom (dummy path in `bridge.json`, then restore it).
- The five test copies in `D:\darkroom_test` must be imported into the test catalog ("Add") before any further Lightroom edit.
- The plug-in changes above need "Reload Plug-in" once (not yet reloaded or re-tested in Lightroom).

### Next step
Phase 3 (below).

## Phase 3 — Photoshop spike (2026-11-20/21) — DONE, waiting at GATE

### Done
- Adapter `src/darkroom_ai/adapters/photoshop`: COM via raw pywin32 in a child process per operation with a hard deadline
  (missed deadline -> child killed -> `PS_NOT_REACHABLE`; Photoshop is never killed), `photoshop.auto_start` (user decision: ON,
  `start_timeout_s = 120`), version check (`ADOBE_VERSION_UNSUPPORTED`, tested: 27.8), ExtendScript + Action Manager scripts in
  `jsx/`, `PhotoshopAdapter` Protocol, new code `PS_COMMAND_FAILED`.
- Safety in code: only files inside `outputs/photoshop` are opened (`prepare_copy` puts a copy there); output is always a new
  file (`name-2`, `name-3`, ... on collision); documents are closed without saving; Photoshop's own dialogs are suppressed
  during our calls and the user's setting restored.
- Spike on Photoshop 27.8.0 with a copy of `D:\darkroom_test\DSC_2457-37.jpg` (all checks passed):
  - Not running + auto-start off -> `PS_NOT_REACHABLE` ("Photoshop is not running").
  - Auto-start: COM launched Photoshop and answered in 6.1 s.
  - Opened the copy; ExtendScript returned name/size/bits/mode/layers (5568 x 3712, 8 bit, RGB, 1 layer).
  - Action Manager `executeActionGet` returned the same document (title, id, `hasBackgroundLayer`; `numberOfLayers` is 0 for a
    background-only document).
  - Added a layer (2 layers), saved a NEW layered PSD beside the copy (starts with `8BPS`); a second save got a new name.
  - Closed without saving; working copy and test photo are byte-identical (SHA-256) afterwards.
  - Timeout: a 20 s script with a 3 s deadline -> `PS_NOT_REACHABLE` after 3.0 s; Photoshop answered again afterwards.
- Library comparison: both attach to 27.8 (pywin32 0.01 s, photoshop-python-api 0.36 s). Recommendation: raw pywin32
  (already required by `mcp`, no extra packages, we need our own child-process deadline anyway); photoshop-python-api adds
  comtypes and a pinned `wheel`, and lists support only up to 2025.

### Open threads
- Modal-dialog test DONE: with a Photoshop dialog open, the call failed after 0.2 s with `PS_NOT_REACHABLE`
  ("busy ... likely a dialog is open", COM result 0x8001010A). No hang.
- DONE (user decision 2026-11-21): raw pywin32 accepted; `photoshop-python-api` and `comtypes` removed, re-locked
  (`wheel` went with them), `pip check` clean. PRD 5.2 and CLAUDE.md updated.
- Auto-start is configurable in `darkroom.toml` (`photoshop.auto_start`, `start_timeout_s`, `ready_grace_s`).
- Start / sign-in screen: `connect()` now watches Photoshop's top-level windows (plain Win32, no COM). If Photoshop is on
  screen but has not answered for `ready_grace_s` (30 s), it raises `PS_NOT_REACHABLE` naming the open windows instead of
  waiting 120 s. Verified: window listing works (saw the main window and an open 'Preferences' dialog); an already-running
  Photoshop with a dialog open answers "busy" in 0.2 s. NOT verified: a real sign-in screen (cannot be triggered here), so
  the 30 s path is covered by unit tests only.
- Unverified: 16-bit TIFF hand-off, masks/background layers, behavior on Photoshop's start/sign-in screen, UXP/batchPlay route.

## Phase 4 (start) — hand-off spike (2026-11-21) — DONE, full chain passed

### Done (no app contact)
- Lightroom plug-in command `export_tiff`: full-size 16-bit TIFF of a VIRTUAL COPY. It is a write command, so it goes through
  the same gate as all writes (token, open catalog == configured catalog, photo under `photos_root`). It writes only into
  `handoff_dir` from `bridge.json` (= `outputs\handoff`; no path is accepted from the request), refuses an existing file name
  (`FILE_EXISTS`) and additionally sets Lightroom's collision handling to "rename".
- Photoshop adapter: `apply_mask_from_png` (background -> layer, layer mask from a same-size grayscale PNG via Action Manager,
  no clipboard), `add_fill_layer_below`, `layers` (with `hasUserMask`), `save_jpg` (flattened copy, never overwrites).
- `scripts/handoff_spike.py`: Lightroom virtual copy -> TIFF -> Photoshop copy -> mask -> PSD + JPG, with numeric checks
  (TIFF header bit depth, layer/mask report, centre unchanged vs. a reference JPG, corner == fill colour, file hashes).
- 187 tests pass with the Lua syntax check; `make quality` clean. `bridge.json` rewritten with `handoff_dir` and `photos_root`.

### Run on 2026-11-21 00:12 (Photoshop half only)
- Plug-in reloaded; `ping` now reports `photos_root = D:\darkroom_test`.
- Folder rule verified LIVE in Lightroom: a preview export for a gallery photo was refused with `PHOTO_OUTSIDE_ROOT`
  (`GUARDRAIL_REJECTED`) and no file was written.
- Photoshop half PASSED on Photoshop 27.8.0 (auto-started), using a 16-bit TIFF converted locally from
  `D:\darkroom_test\DSC_2457-37.jpg` with OpenCV as a stand-in for the Lightroom export:
  - TIFF header 16/16/16 bit; Photoshop opened a COPY of it and reports 16 bit, 5568 x 3712.
  - Layer mask from the PNG (Action Manager, no clipboard); layers top to bottom: `subject` (mask) / `background`.
  - New layered PSD and flattened JPG saved beside the copy (JPG save from a 16-bit document works); closed without saving.
  - Numeric: centre region identical before/after the mask (85.9/130.1/123.4); corner = fill colour (200/30/31 vs 200/30/30).
  - TIFF and test photo byte-identical afterwards.

### Run on 2026-11-21 07:43 (full chain, Lightroom half included) — PASSED
- At 00:05-00:20 the test copies were not in the catalog (it then held 67 files, all gallery photos). The user imported them
  by hand later (as `TEST_*.jpg` plus the `DSC_*.jpg` copies); the catalog then had 79 photos. Counts earlier in this file
  were taken before that import and are stale.
- Results are in "CURRENT STATE" at the top of this file: 16-bit TIFF from a virtual copy in 0.7 s, sRGB profile embedded,
  refusals for a master and for an existing name verified live, Photoshop chain passed, original byte-identical.

### Open threads
- Still due before Phase 5: live wrong-catalog refusal test.
