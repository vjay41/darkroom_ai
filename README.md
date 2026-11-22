# darkroom_ai

A local, offline MCP tool that lets an AI agent (built for the Hermes agent with a local vision model)
inspect a Lightroom Classic catalog and edit photos on Windows. darkroom_ai itself makes no internet connections:
it listens on 127.0.0.1 only and talks to Lightroom through its own plug-in. The model, Lightroom and Hermes are
separate programs.

The agent is the brain; this project is the hands. It exposes about thirty granular tools (read facts, measure a
photo, set develop parameters, create and adjust masks, apply a preset, undo) and decides nothing about what an
edit should be. Professional knowledge lives in agent skills (`hermes/skills/`), not in code.

Documentation index (architecture, design, tools, testing, contributing): `docs/README.md`.
Design and decisions: `PRD.md`. State, test evidence and open points: `docs/PROGRESS.md`.
Install: `docs/INSTALL.md`. After an Adobe or darkroom_ai update: `docs/UPGRADE.md`. Working in the repo
as an agent: `AGENTS.md`.

## Safety rules the program enforces
- Loopback only (127.0.0.1). A test fails on any non-local connection or network import.
- Originals are never overwritten. Edits go to a virtual copy and are journaled with what is needed to undo them.
- Writes are refused unless the open catalog is the configured, allowlisted one. Develop and test on a dedicated
  test catalog.
- Every edit is clamped (per pass and in total) and reported as requested versus applied.
- Anything destructive, bulk or interactive (a people mask, an import, going past a limit) asks the person first.
  The agent cannot confirm for itself.
- Every Adobe call has a timeout and failures use stable error codes with a plain next step.

## What works, honestly
Tested with Windows 11, Python 3.13, **Lightroom Classic 15.2.1** and Photoshop 2026 (27.8), driven by Hermes with a
local model through LM Studio. Other Adobe versions give a warning (`get_capabilities`), not a block.

| Area | State |
|---|---|
| Read tools (catalog, metadata, previews, measurements, develop state) | Verified |
| Develop edits on JPG (lighting, white balance, HSL, crop, any listed parameter), undo | Verified |
| Masks: gradient, radial, luminance, AI subject/sky/background, people (needs the person to pick) | Verified on the test catalog |
| Presets (look settings allowed with a summary; crop and process-version changes refused) | Verified |
| Reliability: 20-cycle soak on the test catalog | 0 failures |
| Batch writes: plan, one go-ahead per chunk, stop at first failure, `undo_batch` (up to 25 photos for rating/flag/keywords, 10 for develop edits) | Verified on the test catalog (JPG and ARW) |
| RAW develop writes (Sony ARW: lighting, Kelvin white balance, lens toggles, library fields, undo) | Verified on the test catalog, including a DNG converted from an ARW |
| Colour and depth range masks | Not available through Lightroom's scripting interface |
| Face detection | Left to Lightroom: people and subject masks use Lightroom's own detection. No face model is shipped or needed |
| Import a folder into the catalog | Verified (asks first) |
| Photoshop hand-off (`replace_background`: 16-bit TIFF in, new PSD out) | Present and verified, but outside the v1 scope; other Photoshop tools are not exposed |

Known limits: the Lightroom plug-in serves one client at a time; mask tools briefly take over Lightroom's Develop
view; a virtual copy inside a collapsed stack cannot be shown (expand stacks in the Library module first); Adobe
versions are frozen until you update one at a time and run `darkroom selftest`.

## Quick start
See `docs/INSTALL.md`. In short: `make install`, create `darkroom.local.toml` with your TEST catalog, run
`poetry run darkroom deploy --hermes-profile <your Hermes profile folder>` (plans first, asks before each write),
add the plug-in in Lightroom, restart Hermes, then `poetry run darkroom doctor` and `darkroom selftest`.

## Commands
| Command | What it does |
|---|---|
| `darkroom doctor` | Checks config, Lightroom, Photoshop and GPU memory. |
| `darkroom selftest [--write]` | Checks each tool group against the installed apps (run after updates). |
| `darkroom soak --cycles N --yes` | Repeats the real tools on the test catalog and counts failures. |
| `darkroom connectors [--stop-stale\|--stop-all]` | Lists or stops running MCP connectors. |
| `darkroom deploy --hermes-profile DIR [--dry-run]` | Connects a Hermes profile; plans first, backs up, asks per write. |
| `darkroom revert [--photo PATH]` | Puts a photo's copy back to its original values. For you, not the agent. |
| `darkroom clean-outputs` | Lists and, after a yes, removes intermediate files. |
| `darkroom mcp` | Serves the tools over MCP (stdio). |
| `darkroom lightroom setup\|ping` | Writes the plug-in's settings file / checks that the plug-in answers. |

Run them as `poetry run darkroom ...`. `make help` lists the make targets.

## Development
`make quality` (lint, format check, type check, tests) must pass before every commit. Dependencies are pinned
exactly and `poetry.lock` is committed. `make audit` uses the network and is not part of `make quality`.

## Licence and credits
MIT, see `LICENSE`. Mask-handling ideas were ported with attribution from par4987/lightroom-mcp (MIT), see
`NOTICE`. Not affiliated with Adobe.
