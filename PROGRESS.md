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

### Open threads for Phase 2
- Confirm with `Get-NetTCPConnection` that `LrSocket.bind` listens on loopback only.
- Add what lightroom-cli lacks: catalog-path check before any write, a shared token per request, proper JSON escaping.
- Find out which Develop operations work without the Develop module (`photo:applyDevelopSettings` vs `LrDevelopController`).

### Next step
Phase 2 plan (exact Lightroom actions + the manual steps for the user), after the user says "proceed". Needs: TEST catalog path and a
folder of test-photo copies.
