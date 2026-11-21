# AGENTS.md

Instructions for an AI agent that works IN this repository or sets it up for a person.
(Instructions for an agent that USES the tools are in the Hermes skills under `hermes/skills/`.)

## What this is
A local, offline MCP server (`darkroom mcp`) plus a Lightroom Classic plug-in (`src/darkroom_ai/adapters/lightroom/plugin/darkroom.lrplugin`).
An agent calls granular tools to read a catalog and edit photos on virtual copies. The agent is the brain;
this project is the hands. No tool decides what an edit should be. Scope and decisions: `PRD.md`; current
state and open points: `docs/PROGRESS.md`.

## Hard rules
- Runtime is offline: bind only to 127.0.0.1; no non-loopback network calls. An automated test enforces this.
- Never overwrite originals. Edits go to virtual copies and are journaled; every write has a tested undo.
- Develop and test against a dedicated TEST catalog only. Never the person's main catalog.
- Ask before anything destructive, bulk, or outside the repository; show the plan first and wait for a clear yes.
- No licence logic. On Adobe problems raise the stable errors (`LR_NOT_REACHABLE`, `PS_NOT_REACHABLE`,
  `ADOBE_FEATURE_UNAVAILABLE`, `ADOBE_VERSION_UNSUPPORTED`) with a plain next step. Every Adobe call has a timeout.
- Dependencies are pinned exactly; `poetry.lock` is committed. Do not add cloud SDKs or telemetry.
- Never commit photos, catalogs, previews, model weights, logs, `.env` files, `darkroom.local.toml` or credentials.
  Check `git status` and `git diff --stat` before every commit.
- Commit to `dev` only. Never to `master`, never force-push, never rewrite history; push only when told.
- Never approve a confirmation on the person's behalf, and never work around a declined one.

## Commands
- `make install` then `poetry run pip check` (first step on a new machine).
- `make quality` = lint + format-check + typecheck + test. It must pass at the end of every change.
- `poetry run darkroom doctor | selftest | soak | connectors | deploy --hermes-profile DIR [--dry-run]`.

## Setting it up for a person (`darkroom deploy`)
1. Do not run `darkroom deploy` yourself: it needs the person at the keyboard and refuses otherwise.
   You may run it with `--dry-run` to show them the plan.
2. It plans first, shows a diff of their Hermes `config.yaml`, backs up every file it would replace and asks before
   each write. Then it prints the manual steps (adding the plug-in in Lightroom, choosing the test catalog,
   restarting Hermes). Those steps are the person's.

## Working style
- Show a plan before building each piece; report what worked, what failed and what is unverified.
- Flag contradictions and risks instead of silently choosing. Keep decisions and open threads in `docs/PROGRESS.md`.
- Prefer numeric checks over viewing real photos; images you view are sent to the model provider you use.
