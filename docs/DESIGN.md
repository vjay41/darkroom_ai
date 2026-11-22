# Design: principles, safety model and decisions

`ARCHITECTURE.md` says how it is built; this says why, and what must stay true. The approved product requirements
are in `../PRD.md`; this file is the working summary a contributor needs.

## The central principle: no scripted editing
Photos differ, so nothing in this project may follow a fixed editing pattern, pipeline or formula.
- **Tools are granular raw material.** Each does one thing the agent asks for: set a parameter, create a mask, apply
  a preset, export a preview, read metadata. No tool decides what an edit should be, chains edits, or has a "fix it"
  or auto-correct mode.
- **The agent is the brain.** It looks at the photo (`get_preview`), reads facts (metadata, measurements, current
  settings), decides what is wrong, calls tools, looks at the result, and iterates.
- **Skills carry knowledge, not recipes.** Hermes skills (`hermes/skills/`) hold professional knowledge: how to
  diagnose, trade-offs, failure modes, how to judge a result. They contain no slider values, no step sequences and no
  per-genre presets of numbers. A test enforces "no slider values" (see `HERMES_SKILLS.md`).
- **Code holds only facts and safety:** measurements (with an honest "not measurable" rather than a guess), safety
  limits (bounds, not decisions), undo, journal, read-back after write, capability probing. Analysis tools return
  numbers, never verdicts such as "this photo needs X". Self-tests check that tools work, never that an edit is good.

If a change would make a tool decide, or add numbers an agent should follow, it belongs in the agent's skills or
not at all.

## Safety model
| Rule | How it is enforced |
|---|---|
| Offline, loopback only | Only `net/loopback.py` may import socket/HTTP; a test fails on any non-local connection or network import; configured addresses are validated as loopback (`NON_LOCAL_ENDPOINT`). |
| Originals are never overwritten | Edits go to a **virtual copy** (`working_copy`); one copy per master is reused. Photoshop output is a new PSD beside the original. `import_folder` adds to the catalog without moving or copying files. |
| Writes only on an allowlisted catalog | The plug-in compares the open catalog with `catalog_path` in `bridge.json`; mismatch is `LR_WRONG_CATALOG`. The shipped default allowlists only the test catalog; the production catalog is the person's own decision. |
| Edits are bounded | Guardrails clamp every change per pass and in total against the photo's original value; the result reports requested versus applied; exceeding a total needs a confirmation. Max 3 refinement passes by default. |
| Everything is undoable and recorded | One append-only journal line per call (also failures) with what is needed to undo. `undo` reaches this session's edits; the human-only `darkroom revert` reaches earlier ones. |
| The agent cannot approve its own actions | Gated actions (people mask, import, going past a limit, the write self-test, bulk) go through MCP elicitation to the person in Hermes. Decline, cancel, timeout, or no client means refusal (`CONFIRMATION_REQUIRED`). |
| No silent success | Writes are verified by read-back; masks by reading the stored table; results flag differences from what was applied. |
| Old code cannot do harm | A connector whose source changed on disk is *stale*: writes are refused (`CONNECTOR_STALE`) until it restarts. |
| Every Adobe call has a timeout | And fails with a stable code and a plain next step; no licence logic anywhere. |
| No secrets or media in git | `.gitignore` plus a test over `git ls-files`. |

### Default guardrail limits (`darkroom.toml` `[guardrails]`)
| Setting | Default | Meaning |
|---|---|---|
| `max_passes` | 3 | Refinement passes |
| `exposure_ev_per_pass` / `exposure_ev_total` | 1.5 / 3.0 EV | One call / against the original (beyond needs confirmation) |
| `tone_per_pass` | 50 | Contrast, highlights, shadows, whites, blacks (scale -100..100) |
| `wb_relative_per_pass` / `_total` | 30 / 60 | JPG/TIFF relative white balance (-100..100) |
| `wb_kelvin_per_pass` / `_total` | 1000 / 2000 | RAW Kelvin (RAW is unverified) |
| `wb_tint_raw_per_pass` / `_total` | 30 / 40 | RAW tint |
| `hsl_per_pass` | 30 | HSL sliders (-100..100) |
| `curve_per_pass` | 32 | Tone curve: most one pass may move a point from the current curve (0..255 scale) |
| `curve_total` | 64 | Tone curve: most a point may be from a straight line (no curve) |
| `min_crop_fraction` | 0.25 | A crop must keep at least this share of the frame |
| `batch_library_max` / `batch_develop_max` | 25 / 10 | Photos per batch call (library fields / develop edits) |
| `batch_library_confirm_above` | 5 | A library batch of more photos than this needs the person's go-ahead; a develop batch always does |
| `batch_library_budget_s` / `batch_develop_budget_s` | 120 / 600 | A batch is stopped and reported after this many seconds |
These are *bounds*, not recommendations. Generic parameters in `set_develop_parameters` are limited to Lightroom's own
slider ranges (`tools/develop_parameters.py`, data only).

## Key behaviours worth knowing before you change anything
- **Presets are tiered.** Look settings may be applied (with a summary of every setting changed); a preset that moves
  exposure far from the original or changes the crop or process version is taken back automatically.
- **JPG white balance** uses Lightroom's relative `IncrementalTemperature`/`IncrementalTint` sliders; the Kelvin path
  is RAW-only and unverified. Basic-panel sliders are the `...2012` keys; the legacy `Exposure` key is stored but does
  not change the picture.
- **Measurements are real renders**, not predictions: before and after an edit the tool exports a small preview and
  measures it. Clipping shares depend on preview size; compare like with like (`analyze_batch` notes this).
- **Interactive steps are explicit.** People masks need the person to pick someone in Lightroom; the tool asks, then
  waits up to 60 s, then verifies. Takeover of the Develop view waits for a quiet Lightroom.

## Decision log (approved by the owner, 2026-11-21)
1. Hybrid build: keep our own plug-in and tool layer; port selected pure-SDK logic from par4987/lightroom-mcp (MIT,
   attributed in `NOTICE`) after an audit; reject all PowerShell, keystroke and screen-capture code.
2. Photoshop is kept in code, off by default and outside v1 (only the `replace_background` hand-off is exposed).
3. The old chat/agent/LLM code is parked in `legacy/` (tag `pre-mcp-pivot`); nothing deleted.
4. Writes only on an explicit catalog allowlist; the test catalog is allowlisted; production only after the soak
   (done) **and** the owner's say-so.
5. The JPG white-balance loss was fixed right after P1, before masks and presets.
6. Hermes config wiring is added with a backup and a shown diff (now automated by `darkroom deploy`).
7. Tiered preset rule (see above); exact undo.
8. Deploy kit: `AGENTS.md`, skills in `hermes/skills/`, `darkroom deploy` that plans first, asks before every write,
   refuses unattended, backs up and diffs the Hermes config.
9. Repo private until the deploy kit passed; `dev` is the working branch; `master` is never committed to by tools.
10. MIT licence with a `NOTICE` crediting Marcin Skalski / par4987.
11. Adobe versions are frozen (Lightroom Classic 15.2.1, Photoshop 27.8) until after the soak; then one deliberate
    update with `darkroom selftest` as the drill. Version mismatches warn; only a failed capability probe blocks.
12. Autonomy: do everything that can be done without asking; ask only for irreversible actions, the production
    catalog, or credentials that are missing.
13. No scripted editing anywhere (this document's first section).
14. Faces and people are Lightroom's job (2026-11-22): the agent uses Lightroom's own people, subject and
    background masks and does not keep its own face data. No face model is shipped or required; the optional
    YuNet measurement path stays off by default. `FACE_MODEL_MISSING` is informational, not a defect.

15. Library fields (rating, pick/reject flag, keywords) are catalog data, written only to virtual copies (2026-11-22).
    A master is refused by the plug-in itself, so an original file is never touched through Lightroom's automatic XMP writing.
    Every change is read back and journaled with what is needed to take it back (`undo_library_change`). Culling masters
    would need an explicit decision and a bulk-confirmation design first.
16. Batch writes (2026-11-22, approved by the owner): `batch_set_library_state`, `batch_set_develop_parameters` and
    `undo_batch`. A batch is planned and validated first (one invalid item means nothing runs, and `dry_run` shows the
    plan), then run item by item through the same guarded handlers, so every clamp, the virtual-copy rule and the read-back
    still apply. One go-ahead per chunk, asked by the tool itself: always for develop batches, above
    `batch_library_confirm_above` photos for library batches. Chunks are 25 (library) and 10 (develop) photos; the run stops
    at the first failure or when the time budget is used up and reports done, failed and not run. Every step is journaled
    under one `batch_id`; `undo_batch` restores and reads back the whole batch (this session only). A batch may create
    working copies (the go-ahead says how many) but never deletes them. Culling masters stays out of scope.
## Non-goals (v1)
A second LLM or an agent loop inside this project; generative edits; deleting or moving files; a GUI; AI Denoise or
any UI automation (keystrokes, screen capture, PowerShell); cloud Lightroom; scripted or automatic editing logic.

## Trade-offs, honestly
- **SDK-only is slower and narrower than UI automation** (no colour/depth range masks, interactive people masks,
  one client at a time) but it does not break when a menu moves, and it keeps the safety story simple.
- **Verification by real renders costs time** but is the only way to catch a dropped edit.
- **Hermes caches its tool list at start**, so tool changes need an app restart; we accept that over a custom
  reload protocol.
