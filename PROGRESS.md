# darkroom_ai — progress log

Running log, updated at the end of every phase. PRD.md section 12 is edited only when one of its open questions is answered.
Plan of record: phases 0-8 with a GATE after each (stop and wait for an explicit "proceed").

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
  path does not exist). The five JPGs in `D:\darkroom_test` are copies and are imported. `darkroom.local.toml` and `bridge.json`
  are written (both git-ignored).
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

### Next step
Phase 3 — Photoshop spike, after the user says "proceed". Proposed PRD 5.1 wording change (not applied): Develop edits via the
LrPhoto calls work from the Library module; undo uses the settings journal because snapshot apply had no effect.
