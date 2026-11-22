# Testing and verification

Three layers, from cheap to expensive. Only the first runs in CI-style checks; the other two touch the real apps and
only ever the TEST catalog.

## 1. Unit tests (`make quality`)
`make quality` = ruff lint + ruff format check + mypy (strict) + pytest. It must pass at the end of every change.
Current baseline: about 430 tests, 8 skipped (the Lua syntax checks need `DARKROOM_LUAC`, see below).

- **Everything is injected against Protocols** (`interfaces.py`), so tests use fakes:
  - `adapters/lightroom/simulated.py` — an in-memory Lightroom (photos, copies, settings, masks, selection).
  - `tests/fake_lightroom.py` — a fake plug-in server speaking the real socket protocol (tests the client).
  - `tests/fakes.py`, `tests/mcp_sim_server.py` — a simulated MCP server and other helpers.
  - `tests/unit/test_tools.py` builds a `Rig` (registry + simulated Lightroom + journal) and calls tools by name.
- **The offline guard is always on** (`tests/conftest.py`): any attempt to reach a non-loopback address raises before a
  packet leaves the machine. `test_offline_guard.py` and `test_loopback.py` cover the rule and the one module allowed
  to touch sockets. `make offline-check` runs the guard tests alone.
- **Generated docs cannot go stale**: `tests/unit/test_release_docs.py` re-renders `TOOLS.md` and `ERRORS.md` from the
  code and fails if they differ (fix: `poetry run python scripts/gen_docs.py`). It also checks that document links
  resolve and that no photo, catalog, model, log or local config is tracked.
- **Skills are validated**: `tests/unit/test_hermes_skills.py` (frontmatter, real tool names, no slider values).
- **Compatibility data** is checked against the plug-in: `test_compat.py` compares `REQUIRED_PLUGIN_COMMANDS` with
  `Commands.lua`.
- Lua syntax: set `DARKROOM_LUAC` to the Lightroom SDK's `luac.exe` to enable 8 extra tests.
- Integration markers (`lightroom`, `photoshop`, `gpu`, `llm`, `integration`) exist for tests that need the real thing;
  they skip unless the matching `DARKROOM_IT_*` variable is set.

### Running it
```
make install             # first time; then: poetry run pip check
make quality             # the gate
poetry run pytest tests/unit/test_tools.py -k analyze_batch   # one area
```
Python is 3.13 in the in-project `.venv` (3.15 is blocked until onnxruntime and scikit-image ship wheels). Never create
a second venv and never install Poetry into `.venv`. In a Linux sandbox (where the Windows-only parts are unavailable)
`uv venv --python 3.13` plus the pinned dependencies runs the same suite; one Windows-only test
(`test_export_tiff_only_for_virtual_copies_and_never_overwrites`) is then deselected.

## 2. Live checks on the test catalog (needs Lightroom)
| Command | What it proves | Notes |
|---|---|---|
| `darkroom doctor` | Config, Lightroom reachable, Photoshop, GPU memory | Read-only |
| `darkroom selftest` | Each tool group still works on the installed Adobe versions; Develop key map has not drifted | Read-only. `--write` also makes one virtual copy, changes exposure by a tiny step, reads back and restores; asks first |
| `darkroom soak --cycles N --yes` | Reliability: per cycle list, preview, edit + read-back, undo, gradient mask create + delete | Test catalog only; writes only on one working copy; saves `outputs/soak-<date>.json`; flags missing mask ids and cleans up |
| `scripts/mcp_live.py --photo PATH` | A real MCP client over real stdio: list tools, one read, one gated call refused | Changes nothing |
| `scripts/tools_live.py --photo PATH` | Each tool once on a virtual copy, then everything undone and the original verified byte-identical | Only photos under `lightroom.test_photos_dir` |
| `scripts/offline_proof.py` | Records every address the process touches; pass = loopback only | Child processes are not seen; use `scripts/firewall_rules.ps1` output for those |

The plug-in serves one client at a time. Stop Hermes' connector first: `darkroom connectors --stop-all`.
Hermes starts a fresh one on its next call.

Last recorded soak (2026-11-21, test catalog): 20 cycles, 180 operations, 0 failures, slowest operation 2.7 s.

## 3. Agent-level checks (needs Hermes)
Unit tests and `selftest` check that *tools work*, never that *an edit is good*. The agent-level check is a
supervised run through Hermes on a test photo with a real fault: the agent should state its intent, make one small
pass, measure and compare, and undo if the result is not better. Recorded result (2026-11-21): on a photo with 16.8%
highlight clipping the agent made one change and cut clipping to 2.3%, and left everything else alone. To repeat:
ask it for a numeric ranking of the test catalog (`analyze_batch`), choose the worst JPG, and ask for one small
supervised pass.

## What is *not* tested yet
RAW/DNG develop writes and lens corrections on RAW; colour/depth range masks (unavailable); face detection (no model
shipped); the Lua plug-in under `luac` in CI; the production catalog. These are listed in `PROGRESS.md`.
