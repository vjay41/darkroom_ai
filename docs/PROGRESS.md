# Status, open threads and next steps

Living status. Keep it short and current: update it in every change that moves the project. The full chronological
build log (every spike, failure and live check up to 2026-11-22) is kept privately by the owner; the facts that matter
are summarised here and in `DESIGN.md`.

Last updated: 2026-11-22. Branch: `dev` (ahead of the v0.1.0 release). Version `0.1.0` plus unreleased library, tone-curve and filter tools; plug-in 0.12.0. Verified on Windows 11, Python 3.13, Lightroom Classic
15.2.1, Photoshop 2026 (27.8), Hermes with a local model via LM Studio.

## Where it stands
All v1 build phases are done except the owner-only release steps. `make quality` is green on Windows and Linux
(about 430 tests, 8 skipped).

| Phase | Result |
|---|---|
| P0 Audit of par4987/lightroom-mcp | Done; selected SDK-only logic ported with attribution, all UI-automation code rejected |
| P1 Strip to MCP, P1b JPG white-balance fix | Done and verified live |
| P1c Compatibility layer | Done: capability probing, `compatibility.toml`, `selftest`, protocol handshake, `UPGRADE.md` |
| P2 Read tools | Done: metadata, paged catalog, develop state, generic parameter tools |
| P3 Corrections | Done: lens profile and chromatic aberration as parameters (RAW verified for Sony ARW, DNG not) |
| P4 Masks | Done: gradient, radial, luminance, AI (subject/sky/background/objects/landscape), people; read-back after every write |
| P5 Presets | Done: tiered rule, exact undo |
| P6 Reliability | Done on the test catalog: 20-cycle soak, 0 failures; root cause of mask delays found (collapsed stacks) |
| P7 Knowledge layer | Done: eight skills installed and loaded; supervised edit-loop check passed |
| Batch step 1 | Done: read-only `analyze_batch` (verified live) and the `lr-batch-operations` skill |
| P8 Deploy kit | Done: `darkroom deploy`, `AGENTS.md`, `NOTICE`, install docs, this documentation set |

## Verified (evidence)
- People mask end to end, including the "wrong photo showing" guard.
- 20-cycle soak on the test catalog: 180 operations, 0 failures, slowest 2.7 s.
- Hermes with the skills on the test catalog: on a photo with 16.8% highlight clipping it stated its intent, made one
  small pass, measured clipping at 2.3%, kept the change, and reported what it left alone and how to undo.
- `analyze_batch`: four photos in one call, unknown id returned as a per-photo error without failing the call.
- Handshake and capability probe live: protocol 1 matches, no missing plug-in commands.
- `darkroom deploy --dry-run` on the real profile: nothing to write.

- Supervised portrait run (2026-11-22, TEST catalog, Hermes + Qwen3.8 27B): one portrait JPG, working copy only, 2 of 3
  passes used. Subject mask created and read back with the expected exposure value; lens profile toggle applied and held.
  Journal shows no errors. **Not verified:** which lens profile actually resolved (name reads null), per-face skin
  measurement (no face model by design; the agent sampled a region instead) and a visual check of the mask shape. The
  `inspect_masks` "Tools: {}" oddity was a defect of ours (output cut off three levels deep) and is fixed in plug-in 0.11.1. The +0.25 EV
  lift raised highlight clipping from 0.32% to 1.12%; the agent reported it and left it.

## Known limits (stated in the README too)
DNG is untested and a RAW lens profile name was never reported (switches work, resolution unconfirmed); colour and depth range masks unavailable through the SDK; face
detection is left to Lightroom's own people/subject masks (no model by design); one Lightroom client at a time; mask tools take over Develop briefly; a virtual
copy in a collapsed stack cannot be shown; Hermes needs a restart to see new tools; Adobe versions frozen until a
deliberate, drilled update.

## Release status
Released as **v0.1.0** on 2026-11-22 (early release: JPG verified, RAW untested at release time, test-catalog writes only; ARW verified afterwards on 2026-11-22).

- Repository is public: https://github.com/vjay41/darkroom_ai (MIT, `LICENSE`).
- `dev` (default branch), `master` and tag `v0.1.0` all pointed at `3e236d9` when released; `master` was
  fast-forwarded from `dev` on the owner's explicit instruction. Never force-push or rewrite history.
- The production catalog stays off the allowlist until the owner decides; do one supervised run on a real photo first.
- Git history and `PRD.md` contain the owner's name and local paths (no secrets, photos or catalogs).

## Next steps (post-v1, in rough priority)
1. **Batch writes**: DONE (owner approved the addendum 2026-11-22). `batch_set_library_state`, `batch_set_develop_parameters`,
   `undo_batch`; verified live on three test photos (JPG + 2 ARW) and driven by Hermes itself (2026-11-22: the model chose the
   values, dry run, approval prompt, 2 of 2 applied, `undo_batch` read-back verified). Stopping at a mid-batch failure and
   at the time budget are verified live too (`scripts/batch_failure_live.py`; the failure and the clock are staged by the script,
   everything else is real Lightroom; 3 photos, JPG + 2 ARW, undo read-back verified). See `PRD_ADDENDUM_BATCH_WRITES.md` and `DESIGN.md` decision 16.
2. **Collapsed-stack detection**: DONE and verified live (plug-in 0.9.4, 2026-11-22). `get_photo`/`get_photo_info` report
   `in_stack`, `stack_collapsed` and a `stack_note` when Lightroom does not say; `create_mask` for kinds that need Develop
   refuses at once (`LR_BUSY_USER`, Expand All Stacks) instead of waiting a minute. Live: expanded stack -> collapsed=false;
   collapsed -> true and the mask call was refused in under 3 s with nothing created. The SDK's metadata calls yield, so
   they must run inside `LrTasks.pcall` (plain `pcall` fails). `darkroom doctor` also warns when the selected photo is in a collapsed stack.
3. **List filters**: rating, flag, keyword, file format, ISO and capture date are DONE and verified live (plug-in 0.12.0; 34 checks on 143 photos, JPG + RAW). There is deliberately no
   "edited" filter (Lightroom flags every virtual copy as edited).
4. **RAW**: Sony ARW verified 2026-11-22 (`scripts/raw_live.py`, 2 files, all checks). Still open: DNG, and whether a lens profile really resolves for RAW.
6. **Lua checks** in CI: provide `luac` so the 8 skipped tests run.
7. Optional: `analyze_photo` region parameter; colour/depth range mask format capture if Adobe exposes it.

## Open threads and housekeeping
- `dev` is pushed (the owner approved pushing dev); `master` and the v0.1.0 tag are untouched.
- Library tools use guardrail defaults of at most 20 keywords per call; tone curve moves at most 32 per pass and 64 in total (`darkroom.toml`). Treat them as starting values.
- Spike keywords `darkroom-ai-library-spike` and `darkroom-ai-filter-spike`, and extra virtual copies, remain in the TEST catalog; harmless.
- Hermes profile `darkroom` now has `lr-tone-curve`, the updated `lr-batch-operations` and the developer skill `darkroom-dev-review` (under software-development, read-and-propose only). A backup of the replaced skill is in the profile's `backups/`.
- Unverified: Windows `poetry install` itself, DNG writes, the production catalog, and edit quality across photo types.
- **Stale tools in Hermes (found 2026-11-22).** Two separate causes, both seen live: (1) Hermes keeps the connector process running
  across its own restart, so it kept running pre-batch code; (2) Hermes caches the connector's tool list
  (`cache/mcp_schema_cache.json` in the profile) and kept offering 32 of 40 tools after the connector was current. Fixes: a
  stale idle connector now ends itself after 120 s (`runtime.stale_exit_after_s`, 0 = off) and Hermes starts a fresh one on its
  next call (verified live); after adding tools, toggle `darkroom_ai` off and on in Hermes' Capabilities, which refreshes the
  cache (verified live).
- A stray Photoshop instance may be open from an earlier test; it is not needed for v1.
- Two empty people masks may remain on one test-catalog working copy; harmless.
- The test catalog has working copies from supervised runs (one carries Highlights -10); keep or undo as you like.
- A few skills in an owner's Hermes profile outside this repo may still carry number-style guidance; they were given a
  "reference only, not a recipe" note.

## Decisions
Approved decisions and the design principles are in `DESIGN.md` (decision log) and `../PRD.md`. Record new decisions
there and add a dated line here.

## Change log (newest first, one line each)
- 2026-11-22: mid-batch failure and time budget verified live (`scripts/batch_failure_live.py`).
- 2026-11-22: connector ends itself when stale and idle (`runtime.stale_exit_*`); Hermes-driven batch run verified live (undo read-back verified).
- 2026-11-22: list filters for file format, ISO and capture date (plug-in 0.12.0), verified live.
- 2026-11-22: batch writes built and verified live (`scripts/batch_live.py`); `lr-batch-operations` 1.1.0 installed in the darkroom profile. Hermes needs a restart to see the three new tools.
- 2026-11-22: plug-in 0.11.1: `inspect_masks` no longer cuts off a mask's Tools list (depth 3 to 6); verified live.
- 2026-11-22: RAW (Sony ARW) develop writes, Kelvin white balance, lens toggles, library fields and undo verified live; `scripts/raw_live.py`. An aborted first run left one edited virtual copy in the test catalog.
- 2026-11-22: list filters (rating, flag, keyword) verified live on plug-in 0.11.0; `edited` filter dropped; batch-writes PRD addendum drafted.
- 2026-11-22: tone-curve tools (`get_tone_curve`, `adjust_tone_curve`) and `lr-tone-curve` skill; verified live.
- 2026-11-22: library tools (rating, pick/reject, keywords, undo) on virtual copies only; verified live (plug-in 0.10.0).
- 2026-11-22: collapsed-stack detection verified live (plug-in 0.9.4); mask tools refuse early.
- 2026-11-22: documentation set written; deploy kit, `NOTICE`, install guide; release-hygiene pass (skills no longer name the user).
- 2026-11-22: `analyze_batch` verified live; `lr-batch-operations` skill.
- 2026-11-21: P7 skills installed; edit-loop check passed; P6 soak passed (20 cycles, 0 failures); people mask verified;
  displayed-photo guard; P1c handshake and probe; soak runner; `--stop-all`.
