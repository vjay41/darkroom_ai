# P8 plan: deploy kit and release (DRAFT for sign-off)

Goal (PRD success criterion 7): a fresh Hermes instance can clone the repo, run `darkroom deploy`, and reach a
working Lightroom connection with only the unavoidable manual steps left.

## What gets built
1. **LICENSE (MIT) and NOTICE.** NOTICE credits Marcin Skalski / par4987 (MIT) for the ported mask logic and
   reproduces their licence text. Not legal advice. Needs: the copyright holder name for LICENSE.
2. **AGENTS.md** for AI agents working in or deploying the repo: hard rules (offline, never overwrite originals,
   test catalog only until allowlisted, ask before destructive/bulk), the Makefile targets, where skills live.
3. **`darkroom deploy`** (new CLI command). Plans first, changes nothing until told:
   - checks prerequisites (Python version, Poetry, `.venv`, Lightroom Classic installed and its version);
   - writes the plug-in's `bridge.json` (existing `darkroom lightroom setup`);
   - shows a diff of the Hermes `config.yaml` `mcp_servers.darkroom_ai` entry, backs the file up, asks, then writes;
   - copies `hermes/skills/*` into the Hermes profile (backs up any file it would replace, asks per write);
   - ends with `darkroom doctor` and `darkroom selftest` (read-only by default).
   Refuses to run unattended (no terminal, or `--yes` is not accepted for config writes). `--dry-run` prints the plan only.
4. **Unavoidable manual steps** (documented, with a checklist the command prints at the end): add the plug-in in
   Lightroom's Plug-in Manager; create a dedicated test catalog; choose the allowlisted catalog; restart Hermes so it
   sees new tools; pick the Hermes profile.
5. **Docs layout:** `docs/INSTALL.md`, `docs/UPGRADE.md` (exists), `docs/PROGRESS.md` (moved from the root, with the
   path in CLAUDE.md updated via a shown diff), README rewritten for a stranger (no personal paths, honest limits).
6. **Tests:** deploy planning and writes against a fake profile directory; refusal when unattended; backups made;
   no write without an answer. `make quality` stays green.

## Release checklist (after the above)
- Pre-public scan: no photos, catalogs, models, logs, `.env`, `darkroom.local.toml` tracked (already clean at 151
  files); remove personal paths and names from README/PRD/PROGRESS; check git history for the same.
- Honest limits section: RAW writes unverified, no face model shipped, colour/depth masks unavailable, one Lightroom
  client at a time, Adobe versions tested only on Lightroom Classic 15.2.1 / Photoshop 27.8.
- The user pushes `dev`, sets `dev` as the GitHub default branch, makes the repo public, merges `dev` into `master`
  and tags the release. Claude never commits to `master`.

## Decisions needed from the user
1. Copyright holder name for LICENSE (your name or handle).
2. Ship `docs/PROGRESS.md` and `PRD.md` publicly, or keep them private/trimmed?
3. Production catalog: allowlist stays off in the shipped default (recommended); you enable it locally.

## Not in P8
Batch writes (batch id, batch undo, chunk limits), list filters needing a plug-in change, RAW write verification,
collapsed-stack detection in the plug-in. These are post-v1 unless you say otherwise.
