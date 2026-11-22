# Architecture

How the pieces fit, what runs where, and what happens on one tool call. Read this first; `DESIGN.md` explains
*why*, `TOOLS.md` lists *what* is exposed, `LIGHTROOM_PLUGIN.md` goes deep on the Lightroom side.

## The one-paragraph version
An AI agent (Hermes, with one local vision model) decides what to do to a photo. It calls granular tools exposed
by `darkroom mcp`, a small Python MCP server that talks over stdio. The server validates each call, applies safety
limits, and sends the work over two localhost sockets to a Lua plug-in running inside Lightroom Classic, which does
the actual catalog reads and edits with Adobe's SDK. Everything is offline and loopback-only. The server has no
model and no agent loop of its own: the agent is the brain, this project is the hands.

## Components
```
 You  <-->  Hermes (desktop app, profile with skills + one local vision model, e.g. via LM Studio)
               |  MCP over stdio (Hermes starts and owns the process)
               v
        darkroom mcp  (Python, this repo; one process = one "connector")
          mcp_server.py -> ToolRegistry (tools/registry.py) -> tool handlers (tools/*.py)
               |  guardrails, journal, confirmations, compatibility data, measurements
               |  adapters (interfaces.py Protocols): LightroomAdapter, PhotoshopAdapter, masks, faces
               v
        LightroomClient (adapters/lightroom/client.py)
               |  newline-delimited JSON over TWO localhost sockets (127.0.0.1), token on every request
               v
        darkroom.lrplugin  (Lua, inside Lightroom Classic)   -- Bridge.lua + Commands.lua
               |  Adobe Lightroom SDK (LrApplication, LrDevelopController, photo:applyDevelopSettings ...)
               v
        Lightroom Classic catalog (the allowlisted TEST catalog until you say otherwise)

 Optional, outside v1: Photoshop 2026 driven over COM (adapters/photoshop/) for the replace_background hand-off.
```
Three separate programs are involved at runtime: Hermes, Lightroom, and the connector. darkroom_ai itself never
opens a non-loopback connection; `net/loopback.py` is the only module allowed to import socket or HTTP libraries and
a test enforces that.

## Repository layout
| Path | What lives there |
|---|---|
| `src/darkroom_ai/mcp_server.py` | The MCP server (stdio). Turns registry tools into MCP tools; `get_preview` returns the picture as image content; asks the person for confirmations through MCP elicitation (`mcp_confirm.py`). |
| `src/darkroom_ai/tools/` | The tools. `registry.py` is the single list; one module per area: `lightroom_tools.py` (info, measure, lighting, WB, HSL, crop, presets, undo), `masks.py`, `develop_parameters.py`, `catalog_tools.py`, `app_status.py`, `capabilities.py`, `background.py`. `context.py` holds `ToolContext` and the shared edit rules; `takeover.py` waits for a quiet Lightroom before taking over Develop. |
| `src/darkroom_ai/interfaces.py` | The Protocols everything is injected against (`LightroomAdapter`, `PhotoshopAdapter`, mask provider, face detector, confirmer, journal). |
| `src/darkroom_ai/adapters/lightroom/` | `client.py` (the socket client), `bridge_file.py` (`bridge.json`), `models.py` (typed results), `simulated.py` (an in-memory Lightroom for tests), and `plugin/darkroom.lrplugin/` (the Lua plug-in). |
| `src/darkroom_ai/adapters/photoshop/` | COM client and scripts for the optional Photoshop hand-off. |
| `src/darkroom_ai/safety/` | `guardrails.py` (clamps), `journal.py` (append-only action log), `confirm.py` (the confirmer contract). |
| `src/darkroom_ai/imaging/` | Pure measurement code (`analysis.py`: tones, clipping, colour cast), mask providers, face detection (needs a user-supplied ONNX file). |
| `src/darkroom_ai/compat.py`, `compatibility.toml` | What has been verified on which Adobe versions; the handshake rules. |
| `src/darkroom_ai/runtime.py` | Stale-connector detection (hashes the source), instance registry, stopping old connectors. |
| `src/darkroom_ai/selftest.py`, `soak.py` | `darkroom selftest` and `darkroom soak`. |
| `src/darkroom_ai/deploy.py` | `darkroom deploy` (connect a Hermes profile; plans first, asks per write). |
| `src/darkroom_ai/config.py`, `darkroom.toml` | Typed config; `darkroom.local.toml` (git-ignored) overrides it. |
| `src/darkroom_ai/cli.py` | The `darkroom` command. |
| `hermes/skills/` | Knowledge skills for the agent (see `HERMES_SKILLS.md`). |
| `tests/` | Unit tests with fakes (see `TESTING.md`). |
| `scripts/` | Dev helpers: doc generation, live spikes, offline proof, firewall rule printer. |
| `legacy/` | The pre-MCP chat/agent code, parked and not imported. No deletion. |

## What happens on one tool call
1. Hermes sends a tool call over stdio; `mcp_server.py` hands it to `ToolRegistry.call` (`tools/registry.py`).
2. **Stale check.** If the connector's source hash differs from the files on disk and the tool writes, it is refused
   with `CONNECTOR_STALE` (reads still work). See `runtime.py`.
3. **Validation.** Arguments are validated against the tool's pydantic model (unknown arguments are rejected).
4. **Handler** runs with an injected `ToolContext`. For an edit it: resolves the photo, finds or creates the photo's
   **virtual copy** (`working_copy` in `tools/context.py`; a master photo is never edited), reads the original
   values, clamps the requested change with the guardrails, measures the photo before and after with real preview
   renders, writes through the Lightroom client, and reads the value back.
5. **Lightroom client** sends one JSON request (token included) to the plug-in; the plug-in refuses writes unless
   the open catalog is the configured one, runs the command with a time budget, and answers.
6. **Journal.** Exactly one line is appended to `logs/journal.jsonl` (also for failures): what was asked, what was
   really applied, and what is needed to undo it.
7. The result (requested versus applied, measurements, notes) goes back to the agent as JSON. If the connector is
   stale a warning is attached.

## Process model and its consequences
- **Hermes owns the connector process.** It starts `python -m darkroom_ai mcp` and may start more than one. Each
  connector registers itself (`runtime.py`); a newly started one stops registered connectors that run *different*
  code, never ones with the same code.
- **The plug-in serves one client at a time.** Hermes' connector holds it, so a CLI command that needs Lightroom
  (`selftest`, `doctor`, `soak`) can fail until the connector is stopped: `darkroom connectors --stop-all`.
  Hermes starts a new connector on its next call.
- **Hermes loads the tool list when it starts.** A new or changed tool is only visible after the Hermes desktop app
  is restarted (and the right profile is selected).
- **Mask tools take over Lightroom's Develop view** for a few seconds, because the SDK only creates masks while
  Develop shows the photo. `takeover.py` waits until the person has stopped clicking, and asks if they do not.

## Configuration, state and data locations
| What | Where |
|---|---|
| Settings | `darkroom.toml`, overridden by `darkroom.local.toml` (git-ignored; holds your test catalog path) |
| Plug-in settings and token | `bridge.json` in the plug-in folder (git-ignored); written by `darkroom lightroom setup` |
| Action journal | `logs/journal.jsonl` (append-only) |
| Previews and measurements | `previews/` |
| Intermediate files, soak reports | `outputs/` |
| Hermes wiring | the Hermes profile's `config.yaml` (`mcp_servers.darkroom_ai`) and `skills/photo/lr-*` |

All of these runtime folders are git-ignored; no photo, catalog, model, log or credential is ever committed.

## Compatibility and upgrades
`compatibility.toml` records which Lightroom and Photoshop versions and which features were verified. A mismatch
only warns. The plug-in and server check a protocol number on connect (`EXPECTED_PROTOCOL`), `get_capabilities`
probes the plug-in's command list, and `run_selftest` checks each Develop key the tools rely on. See `UPGRADE.md`.
