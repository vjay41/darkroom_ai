# Contributing (humans and LLM agents)

Read in this order: `../README.md`, `ARCHITECTURE.md`, `DESIGN.md`, then the area you will touch. `TOOLS.md` and
`ERRORS.md` are generated reference. `PROGRESS.md` says what is done, what is open and what to do next.

## Ground rules (they are enforced by tests where possible)
1. **Offline.** Bind only to 127.0.0.1; add no network calls, cloud SDKs or telemetry. Only `net/loopback.py` may
   import socket or HTTP libraries.
2. **Never overwrite originals.** Edit virtual copies; journal every write with its undo; read back after writing.
3. **Test catalog only** for development and live tests. Never the owner's main catalog.
4. **No scripted editing.** A tool never decides what an edit should be, and skills never contain slider values.
5. **Ask before anything destructive, bulk or outside the repo**; show the plan and wait for a clear yes.
6. **No licence logic.** Adobe problems raise the stable error codes with a plain next step; every Adobe call has a timeout.
7. **Dependencies are pinned exactly** and `poetry.lock` is committed. Models are provided by the user and never downloaded.
8. **Git:** commit to `dev` only; never to `master`, never force-push or rewrite history; push only when the owner says so.
   Never commit photos, catalogs, previews, model weights, logs, `.env`, `darkroom.local.toml` or credentials. Check
   `git status` and `git diff --stat` first and stage only your own files (not IDE files).
9. **`make quality` must pass** before you finish.
10. **Be honest in docs and reports:** say what worked, what failed and what is unverified.

## Workflow
1. For anything non-trivial write a short plan first (problem, success criteria, scope, constraints, steps, open
   questions) and get sign-off. Check what already exists before building something new.
2. Build with tests next to the change. Keep tools small and typed (pydantic models with a clear docstring: the
   docstring becomes the tool description the agent reads, so write it for the agent).
3. Run `make quality`. If you changed a tool or an error code, run `poetry run python scripts/gen_docs.py` and commit the
   regenerated `docs/TOOLS.md` / `docs/ERRORS.md`.
4. Update `PROGRESS.md` (status, decisions, open threads). Edit `../PRD.md` section 12 only when one of its open
   questions is answered.
5. Commit with a message that says why; stage only your own files.

## Recipes
### Add a tool
1. In the right `tools/*.py` module add an input model (`extra="forbid"`, docstring = description for the agent) and a
   handler `(ctx, args) -> Outcome`. Return facts and requested-versus-applied; never verdicts.
2. Register it in `tools/registry.py` (`writes=True` only if it changes anything). Add its name to the expected list in
   `tests/unit/test_tools.py` and, if it takes no `photo_id`, to `NOT_ABOUT_ONE_PHOTO`.
3. Edits: use `working_copy`, clamp with the guardrails, measure before/after, write, read back, record undo.
4. If it needs a new plug-in command see `LIGHTROOM_PLUGIN.md`; add it to the simulated adapter and the fake plug-in.
5. Add it to a feature in `compatibility.toml` (status `untested` until verified live), regenerate the docs, add tests,
   mention it in a skill's `uses_tools` if a skill should use it.

### Add or change a Hermes skill
Edit `hermes/skills/<name>/SKILL.md` (see `HERMES_SKILLS.md`). Keep it short, knowledge only, no numbers to follow.
`tests/unit/test_hermes_skills.py` enforces the rules. Installing into a profile is `darkroom deploy` (asks per write).

### Support a new Lightroom or Photoshop version
Follow `UPGRADE.md`: freeze, update one app, run `darkroom selftest` (and `--write` on the test catalog), then update
`compatibility.toml` with what you verified.

### Change the config
Add the field to the pydantic section in `config.py` (typed, bounded), document it in `darkroom.toml`, and add a test.
Model names never appear in code; every address must validate as loopback.

## Pitfalls that cost real time
- **Stale connector:** editing any `.py` under `src/darkroom_ai` makes the running connector stale and write tools
  refuse. `darkroom connectors --stop-stale` (or `--stop-all`), then the agent's next call starts a fresh one.
- **Hermes only sees new tools after the desktop app restarts**, and only for the profile you selected.
- **One Lightroom client at a time:** stop the connector before CLI commands that need Lightroom.
- **Collapsed stacks hide working copies:** expand all stacks in the Library module before mask work.
- **Files created by an agent's Write tool may land in a cloud container, not on the owner's machine.** Create repo
  files on the machine itself.
- **Do not view real photos unnecessarily:** images you look at are sent to the model provider you use. Prefer numeric checks.
