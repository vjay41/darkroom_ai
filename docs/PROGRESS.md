# Status, open threads and next steps

Living status. Keep it short and current: update it in every change that moves the project. The full chronological
build log (every spike, failure and live check up to 2026-11-22) is kept privately by the owner; the facts that matter
are summarised here and in `DESIGN.md`.

Last updated: 2026-11-22. Branch: `dev`. Version `0.1.0`. Verified on Windows 11, Python 3.13, Lightroom Classic
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
| P3 Corrections | Done: lens profile and chromatic aberration as parameters (RAW unverified) |
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

## Known limits (stated in the README too)
RAW/DNG develop writes and RAW lens work unverified; colour and depth range masks unavailable through the SDK; no face
model shipped (`FACE_MODEL_MISSING`); one Lightroom client at a time; mask tools take over Develop briefly; a virtual
copy in a collapsed stack cannot be shown; Hermes needs a restart to see new tools; Adobe versions frozen until a
deliberate, drilled update.

## What blocks a public release (owner)
1. Add `LICENSE` (MIT): needs the copyright holder name.
2. Decide what ships publicly: this repo's history and `PRD.md` contain the owner's name and local paths (no secrets,
   photos or catalogs were ever committed).
3. Push `dev`, set it as the GitHub default branch, make the repository public, merge `dev` into `master`, tag the release.
   Tools and agents never commit to `master`.
4. Allowlisting the production catalog stays a local decision of the owner; do one supervised run on a real photo first.

## Next steps (post-v1, in rough priority)
1. **Batch writes**: a batch id in the journal, one undo for a whole batch, chunk and error-budget limits, one
   confirmation per chunk plan. Needs a short PRD addendum and sign-off first. Design is in `lr-batch-operations`.
2. **Collapsed-stack detection**: DONE and verified live (plug-in 0.9.4, 2026-11-22). `get_photo`/`get_photo_info` report
   `in_stack`, `stack_collapsed` and a `stack_note` when Lightroom does not say; `create_mask` for kinds that need Develop
   refuses at once (`LR_BUSY_USER`, Expand All Stacks) instead of waiting a minute. Live: expanded stack -> collapsed=false;
   collapsed -> true and the mask call was refused in under 3 s with nothing created. The SDK's metadata calls yield, so
   they must run inside `LrTasks.pcall` (plain `pcall` fails). Not done: a `darkroom doctor` warning.
3. **List filters** (file format, capture date, ISO) on `list_catalog_photos`: needs a plug-in change.
4. **RAW/DNG**: verify writes and lens corrections on a RAW test catalog; then update `compatibility.toml`.
5. **Faces**: document how to supply a YuNet ONNX file; verify the face tools.
6. **Lua checks** in CI: provide `luac` so the 8 skipped tests run.
7. Optional: `analyze_photo` region parameter; colour/depth range mask format capture if Adobe exposes it.

## Open threads and housekeeping
- Several local commits on `dev` are not pushed; the owner pushes.
- A stray Photoshop instance may be open from an earlier test; it is not needed for v1.
- Two empty people masks may remain on one test-catalog working copy; harmless.
- The test catalog has working copies from supervised runs (one carries Highlights -10); keep or undo as you like.
- A few skills in an owner's Hermes profile outside this repo may still carry number-style guidance; they were given a
  "reference only, not a recipe" note.

## Decisions
Approved decisions and the design principles are in `DESIGN.md` (decision log) and `../PRD.md`. Record new decisions
there and add a dated line here.

## Change log (newest first, one line each)
- 2026-11-22: collapsed-stack detection verified live (plug-in 0.9.4); mask tools refuse early.
- 2026-11-22: documentation set written; deploy kit, `NOTICE`, install guide; release-hygiene pass (skills no longer name the user).
- 2026-11-22: `analyze_batch` verified live; `lr-batch-operations` skill.
- 2026-11-21: P7 skills installed; edit-loop check passed; P6 soak passed (20 cycles, 0 failures); people mask verified;
  displayed-photo guard; P1c handshake and probe; soak runner; `--stop-all`.
