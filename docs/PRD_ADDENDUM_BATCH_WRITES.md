# PRD addendum: batch writes

Status: **approved 2026-11-22 and built** (decision 16 in `DESIGN.md`). The owner approved the recommended answers to the
open questions in section 6: one go-ahead per chunk (25 library, 10 develop), stop at the first failure, copies allowed and
listed in the plan, library batches confirm above 5 photos and develop batches always, budgets of 2 and 10 minutes, no
culling of masters.
Date: 2026-11-22. Depends on: plug-in 0.11.0 (library state, tone curve, list filters), tools in `TOOLS.md`.

## 1. Problem
Today every write is one photo per call. `analyze_batch` (read-only) and the `lr-batch-operations` skill let Hermes
triage many photos, but rolling out an approved edit means one model turn and one tool call per photo: slow, and the
model's attention is spent on bookkeeping. Rating, flagging and keywording a whole set (for example "flag the
sharp ones in this folder") has the same cost. The ask: let the agent apply an approved change to many photos with
one human confirmation, without weakening any safety rule.

## 2. Success criteria
1. One confirmation (showing count, the exact change and the target list) covers a whole chunk; no confirmation, no write.
2. Zero writes to masters and zero changes outside the test or approved catalog, in every code path (tested).
3. Per-photo outcome is reported (`ok`, `skipped`, `failed` with a reason); one photo failing never hides the others.
4. The run stops at the first stop condition and says why (see 5); there is no retry loop.
5. Every item is journaled under one `batch_id`; one `undo_batch` call reverts the batch exactly (read-back verified).
6. A 25-photo library-state batch finishes in under 60 s on the test catalog; develop batches scale with the
   existing 2-3 s per photo, with no regression to single-photo tools.
7. `make quality` stays green; the offline test still passes; no new dependencies.

## 3. Scope
In:
- `batch_set_library_state`: rating, pick/reject flag, add/remove keywords for a list of photo ids (up to 25 per call).
- `batch_set_develop_parameters`: the same guarded values per photo (each photo may carry its own values), applied to
  each photo's working copy, with the existing clamps.
- `undo_batch`: reverts every item of a batch using the existing per-item undo records.
- A dry-run mode that returns the plan (what would be created or changed per photo) without writing.

Out:
- Writes to masters, culling masters, deleting, moving or exporting files.
- Batch masks, presets, lens or curve changes (revisit after this ships and is measured).
- Identical-values-for-all-photos as the default: the skill keeps saying values come from each photo's own numbers.
- Any scripted or automatic editing logic; the model chooses the values, code only applies and guards them.

## 4. Constraints
- All hard rules in `CLAUDE.md` apply unchanged: loopback only, TEST catalog during development, virtual copies only,
  guardrail clamps per photo, timeouts on every Adobe call, plain errors with a next step.
- The confirmation goes through MCP elicitation to the person; decline, cancel, timeout or no client means refusal
  (`CONFIRMATION_REQUIRED`), as for import and the people mask. The agent cannot approve its own batch.
- Batches reuse the existing single-photo handlers so guardrails and read-back are not duplicated.
- Hermes caches its tool list; adding tools needs a Hermes restart (already documented).

## 5. Plan
1. Design note in `DESIGN.md` (decision 16): batch envelope, `batch_id`, stop conditions, undo semantics.
2. `BatchPlan` model: list of per-photo operations, validated up front (ids exist, none is a master unless a working
   copy exists or the operation creates one, values inside guardrails). Invalid plan means nothing runs.
3. Dry-run first: the tool returns the plan; the real run requires the confirmation text to quote count and change.
4. Executor: sequential, per-photo try/except, stop at the first failure by default (`continue_on_error` off), at most
   25 photos per call, a per-batch wall-clock budget. Each item journaled with `batch_id`.
5. `undo_batch(batch_id)`: reverse order, each item verified by read-back, reports anything it could not revert.
6. Tests with the fake plug-in (including mid-batch failure, decline, a master in the list, a stale connector), then a
   live run on the test catalog (library batch of 10, develop batch of 5), checked against `logs/journal.jsonl`.
7. Skill update to `lr-batch-operations` (plan, confirm, chunk, verify, offer `undo_batch`); docs regenerated.

## 6. Open questions (need your answer)
1. Confirmation granularity: one confirmation per chunk (recommended, up to 25 photos) or per whole run?
2. Stop policy: stop at first failure (recommended) or continue and report at the end?
3. Chunk cap: is 25 right, or smaller for develop batches because each creates a virtual copy?
4. Virtual copy growth: a batch over masters creates one copy each and clutters the catalog. Allow batches only on
   existing working copies, or allow creation with a clear count in the confirmation?
5. Should rating/flag/keyword batches need confirmation at all below a threshold, since they are undoable? Current
   decision: confirm only for bulk. What is "bulk" for you: more than 5, 10, 25?
6. Do you want culling (rating or rejecting masters) at all? That needs a separate decision because it touches the
   originals' metadata through Lightroom's XMP writing.
7. Time budget per batch before the run is stopped and reported.

## 7. Risks
- A wrong idea repeated across a chunk: mitigated by pilot-first in the skill, per-photo values, and stopping when
  more than one photo gets worse (skill rule; not enforceable in code without making editing decisions).
- The model summarising a batch inaccurately: the tool result and journal are the source of truth, not its prose.
- Lightroom stays busy for the whole run (one client at a time): the budget in 5.4 bounds this.
