# darkroom_ai — progress log

Running log, updated at the end of every phase. PRD.md section 12 is edited only when one of its open questions is answered.
Plan of record: phases 0-8 with a GATE after each (stop and wait for an explicit "proceed").

## CURRENT STATE — 2026-11-21: Phase 8 in progress: default_playbook setting built; confirmation test (real y / n in darkroom chat) plan shown, waiting for the user's go. Not pushed.

Do not push `dev`. Before touching Lightroom or Photoshop for a new step, show the plan and wait for the user's go.

### Phase status
| Phase | Status |
|---|---|
| 0 Scaffold | Done |
| 1 lightroom-cli review | Done (not adopted; own plug-in) |
| 2 Lightroom spike | Done; snapshot apply does not work, undo = settings journal |
| 3 Photoshop spike | Done (raw pywin32, child-process deadline, auto-start) |
| 4 Imaging toolkit | GATE ACCEPTED by the user on 2026-11-21, pending the user's own visual check of the Select Subject mask. ONNX mask backend and YuNet face detector DEFERRED until the user says so (unit-tested with fakes only). |
| 5 Tool layer + guardrails | GATE ACCEPTED by the user on 2026-11-21, pending the user's visual checks of the mask and the background JPG. `apply_preset` stays unexposed until tested live. |
| 6 LLM layer + agent | GATE ACCEPTED by the user on 2026-11-21 with the listed deferrals (Muse/fast and the two-model GPU measurement to Phase 8). |
| 7 Hardening | GATE ACCEPTED by the user on 2026-11-21 |
| 8 Acceptance | In progress (see "Phase 8 progress") |

### Phase 6 (built 2026-11-21)
User decisions: one model loaded at a time (JIT load / auto-unload if available, otherwise ask the user to switch); LM Studio
pinned to GPU 0; first agent run = lighting + white balance only on a virtual copy of a TEST_ JPG with undo at the end,
`replace_background` as a second run only after the first passes; prompt-injection case per profile; confirmations are
interactive y/n in `darkroom chat` and refused through MCP.

Built:
- `net/loopback.py` `LocalHttp`: httpx2 client, loopback base URL only, `trust_env=False`, no proxy, no mounts, no redirects,
  per-request loopback check. Unit test sets `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` to a non-local address and still reaches
  only the local fake server.
- `llm/`: `client.py` (OpenAI-compatible chat; `LLM_NOT_REACHABLE`, `LLM_NO_VISION`, new code `LLM_REQUEST_FAILED`),
  `tool_modes.py` (native / json_schema / prompt -> one `ToolCall` form; unusable answers = `LLM_BAD_TOOL_CALL`),
  `vision.py` (downscaled JPEG as an inline data URL), `selftest.py` (`darkroom models test`).
- `agent/loop.py` + playbooks `portrait.md`, `landscape.md`: at most `max_passes` passes, allowed-tool subset, tool results and
  metadata labelled as data, three unusable answers in a row stop the run.
- `adapters/lightroom/simulated.py`: the in-memory Lightroom (moved from tests) so model tests never touch Lightroom or photos.
- `gpu.py` + `[gpu]` config: nvidia-smi report; warns if the model server is not on the configured GPU, or if it shares a GPU
  with Photoshop below `min_free_mb`. (Per-process memory is not available on Windows; the check uses per-GPU totals plus
  which program holds a context on which GPU.)
- CLI: `darkroom doctor`, `darkroom models test [profiles] [--skip-vision] [--json]`, `darkroom chat "..." [--photo] [--playbook]
  [--profile]` (asks y/n for gated actions; chat tools = lighting, white balance, HSL, crop, undo), `darkroom mcp` (stdio).
- `mcp_server.py`: same registry; default confirmer refuses, so a gated request returns `[CONFIRMATION_REQUIRED]` + next step.
- 296 tests pass, `make quality` clean.

Run for real (2026-11-21, text only, no image sent, simulated Lightroom):
- `darkroom doctor`: Lightroom 15.2.1 / plug-in 0.3.0 writes allowed; Photoshop not running; model listed; model server on
  GPU 0 (26.7 of 32.6 GB used), GPU 1 idle; no warnings.
- `darkroom models test primary --skip-vision`: 4/4 tool scenarios passed, 6/6 calls valid (0.8-10.8 s per scenario); the
  "4 stops" request ended at +3.0 EV (total cap). Injection in a file name and in a tool result: model ignored both,
  guardrails held, exposure +0.33 as asked.
- Finding while testing: the first version of the tool-result injection never reached the model (the field was dropped when
  the measurements were summarised); fixed and covered by a test that checks the text is really in the request.

Unverified / not run: vision test (waiting for the go); profiles `glimmer` and `fast` (not loaded; whether LM Studio's JIT
load + auto-unload is on is unknown); json_schema and prompt modes against a real model; `darkroom chat` live; MCP over a
real stdio client (handlers are unit-tested); GPU conflict measurement (Select Subject while a model is loaded). The
"model followed the injection" flag is a heuristic (unknown tool, `allow_beyond_total`, exposure >= 2).

Vision test DONE (2026-11-21, `primary`, synthetic picture only): 5 of 5 fields correct (square/red, circle/blue, 47),
8.2 s, request 14 kB to 127.0.0.1:1234. GPU 0 used 26731 MB before, 26783 MB after (+52 MB); GPU 1 unchanged at 26 MB.

Thinking time (2026-11-21, `primary`, text-only tool call, two runs each): about 62 of 110 completion tokens are thinking;
0.84-1.01 s with it, 0.34-0.49 s without. `reasoning_effort: "none"` switches it off (0 reasoning tokens, same tool call);
`"low"` trims it to about 50 tokens. `enable_thinking: false`, `chat_template_kwargs` and `/no_think` had NO effect.
Proposed, not built: a per-profile `reasoning_effort` setting ("none" for tool-call turns).

Photo vision check (`scripts/vision_photo_check.py`, `D:\darkroom_test\TEST_2459-39.jpg`; the existing virtual copy, at its
start values, was analysed; no edit, no new copy): preview 1024 x 683 JPEG, 164 kB, request 219 kB to 127.0.0.1:1234, no
file name / metadata / numbers sent. 1.5 s; 133 completion tokens, 84 of them thinking.
| Field | Model | Measured | |
|---|---|---|---|
| brightness | bright | balanced (mean 136 of 255, median 145) | disagree |
| mean 0-255 | 185 | 136.3 | off by +49 |
| highlights clipped | no | yes (1.25 %, almost all in the red channel) | disagree |
| shadows clipped | no | no (0.57 %) | agree |
| colour cast | none | no noticeable cast | agree |
Conclusion (one photo only): the model's eye for absolute brightness and clipping is not reliable; the agent must lean on the
measured numbers, which it already gets in every pass. The category limits (50 / 95 / 160 / 205) are my own choice.

NOTE (added later the same day): this measurement was taken with Qwen ALONE (one model loaded, on GPU 0, GPU 1 idle).
The user now runs Qwen on GPU 0 and Muse (`glimmer`) on GPU 1; Photoshop's 3.2 GB Select Subject peak was on GPU 1, so the
result does not carry over and a re-measurement with both models loaded is planned.
GPU conflict measurement (`scripts/gpu_conflict.py`, `TEST_2457-37.jpg`, model loaded on GPU 0, Photoshop auto-started in 5.6 s,
mode Device before and after):
| | Time | GPU 0 used MB (before / peak / after) | GPU 1 used MB (before / peak / after) |
|---|---|---|---|
| cold run | 8.3 s | 27469 / 28336 / 28337 | 38 / 3268 / 147 |
| warm run | 7.6 s | 28337 / 28518 / 28411 | 147 / 3268 / 147 |
Baseline without a model in use: 8.1 s. Mask coverage 69.937 % both times (identical to the baseline), 1 region, 0 specks.
Photoshop holds a context on BOTH GPUs: about +0.9 GB stays on GPU 0 while it is open, and Select Subject's working memory
(about 3.2 GB peak) showed up on GPU 1. No slowdown, no failure. The model answered afterwards (1.06 s and 0.72 s, correct
tool call). Original SHA-256 unchanged; documents closed without saving; Photoshop left running. GPU 0 ended at 28.3 of
32.6 GB (4.3 GB free), which is BELOW the 6 GB `min_free_mb` threshold, so `doctor` will now warn while Photoshop is open
although the measurement shows no problem; the threshold may be too cautious (open decision).
Left behind: `outputs\masks\TEST_2457-37_subject-2.png`, `-3.png`, copies in `outputs\photoshop`, one preview in `previews`.

User decisions (2026-11-21) and what was built for them:
- Per-profile `reasoning_effort_tools` / `reasoning_effort_planning` (none | minimal | low | medium | high; unset = not sent).
  `primary` has tools = "none"; planning is unset, so planning turns (the first turn and the turn after each pass, i.e. when
  the model sees new measurements) keep the model's default thinking. `Agent(planning=False)` uses the tools value on every turn.
- `darkroom models test` now runs the tool scenarios twice when thinking is off for tools and fails if the valid-call rate
  drops. Run on `primary` (text only, simulated): 100 % valid calls and 4/4 scenarios both ways; 17.8 s with default thinking
  vs 4.2 s with "none" on every turn. Injection tests (thinking off on every turn): ignored, guardrails held.
- `gpu.min_free_mb` = 3000; the shared-GPU message states what was measured (4.3 GB free with Photoshop open, no slowdown,
  one photo). `doctor` with Photoshop open now prints it as a note (4457 MB free).
- Playbooks and the system rules: exposure / highlights / shadows decisions come from the measurements (p50, p05,
  highlights_pct, shadows_pct with stated ranges); the model's visual impression of brightness and clipping is declared
  unreliable; the preview is for subject, skin and colour only. The numeric ranges in the playbooks are my starting values,
  not tuned.
- 300 tests pass, `make quality` clean.

First agent run (`scripts/agent_run.py`, 2026-11-21, `primary`, portrait playbook, `TEST_2459-39.jpg`, existing virtual copy;
tools offered: `adjust_lighting`, `adjust_white_balance`; no confirmer):
- Before the run: only the latest preview is kept in the conversation (older ones are replaced by a short note; unit test).
- Turn 1 (3.4 s, 385 completion tokens, 306 thinking): `adjust_lighting` highlights -20; requested -20, applied -20, not
  clamped. Turn 2 (2.9 s, 393 tokens, 162 thinking): finished with a summary. 1 pass, 2 turns, 2 previews (164 kB, 163 kB),
  7.5 s in total.
- Measurements before -> after: highlights clipped 1.245 % -> 0.172 %; p50 0.567 -> 0.555; p95 0.910 -> 0.886; shadows
  0.566 % -> 0.570 %; cast "no noticeable cast" both times.
- Matches the prediction from the numbers: exposure untouched, highlights lowered, white balance untouched.
- Undo: `undo all` took back 1 step, which was the only pending edit and belonged to this run's session. All 171 develop
  settings of the copy equal the start state recorded before the run; the master's 170 settings never changed; original
  SHA-256 unchanged; nothing left pending.
- The model's summary (819 characters) is in `logs\agent_run_20261121-094450.txt` (git-ignored); not read by the assistant.
- Design fact: the `undo` tool's scope "all" is NOT limited to a session; it takes back every not-yet-undone darkroom_ai
  edit on the copy, across sessions. The script made it equal to "this run" by refusing to start if earlier edits were
  pending and by checking the undone entries' session. Open decision: add a scope "session" to the tool.
- One photo, one request: no evidence yet for white-balance changes, multi-pass runs, or other photos.

User decisions after the first run (2026-11-21) and what was built:
- `undo` tool: reaches only edits made in the CURRENT session ("last" is the default, "all" = everything from this session).
  My reading, flagged to the user: "last" is session-limited too, so a model can never take back an earlier session's edit.
  With nothing to undo, the result points to `darkroom revert`.
- `darkroom revert [--photo PATH]` (`tools/revert.py`): human-only, not in the tool registry and not offered over MCP. Shows
  how many edits from how many sessions would be taken back and the values to restore, asks y/N, then puts every setting
  darkroom_ai ever changed on the copy back to its value from before the first such change (from the journal). Settings
  changed by hand in Lightroom are not touched. Unit-tested; NOT yet run against Lightroom.
- Answer recorded: on turn 1 of the first run no `reasoning_effort` was sent (planning turn; `reasoning_effort_planning` is
  unset for `primary`), so the model used its default thinking: 306 of 385 tokens.
- 304 tests pass, `make quality` clean.

Second agent run, stage 1 (2026-11-21, `primary`, portrait, `TEST_2459-39.jpg`, session `097de950...`; tools offered:
`adjust_lighting`, `adjust_white_balance`, `replace_background`; request: balance the lighting if the measurements call for
it, then replace the background with solid light grey RGB 210/210/210):
- Turn 1 (5.6 s, 732 tokens, 499 thinking): `adjust_lighting` highlights -20 (applied -20, not clamped).
  Highlights clipped 1.245 % -> 0.172 %, p50 0.567 -> 0.555.
- Turn 2 (2.3 s, 304 tokens, 110 thinking): `replace_background` colour [210, 210, 210]; 13.8 s; mask by
  photoshop-select-subject, 5568 x 3712, coverage 44.715 %, 1 region, 0 specks. New files in `outputs\photoshop`:
  `background-20261121-095404-f40b4c_copy_darkroom.psd`, `..._copy_darkroom.jpg`, `..._copy_mask.png`.
- Turn 3 (3.2 s, 358 tokens, 95 thinking): finished. 2 passes, 3 turns, 3 previews (one per request), 26.7 s in total.
- As predicted: exposure and white balance untouched, highlights -20, one background call with the right colour.
- PAUSED: `Highlights2012` is -20 on the virtual copy `7F5FB162-...` (NOT undone). Master settings and original SHA-256
  unchanged. State for the undo: `logs\agent_run_state.json` (git-ignored). Model summary (866 characters) in
  `logs\agent_run_20261121-095422.txt`, not read by the assistant.
- Stage 2 (needs the user's go): `poetry run python scripts/agent_run.py --undo-state logs/agent_run_state.json`.
- `build_tool_context(session=...)` lets a script resume a journal session (for this pause); a model cannot set it.
- Noted, not fixed: after `replace_background` the loop re-measures and sends a new preview although nothing changed in
  Lightroom (one wasted preview and turn input). Not verified by numbers: the composite's background colour and the output
  files' contents (the user is looking at them).

Second agent run, stage 2 (2026-11-21, session `097de950...` resumed from `logs\agent_run_state.json`): `undo all` took
back 1 step (the only pending edit, from that session; 0 from other sessions). All 171 settings of the copy equal the
recorded start state; the master's 170 settings never changed; original SHA-256 unchanged; nothing left pending. The PSD,
JPG, mask and TIFF stay in `outputs\` (git-ignored). The user looked at the result during the pause.

Change after the run: a pass in which only `replace_background` succeeded (no Lightroom edit in that step) no longer
re-measures or sends a new preview; the model is told the last measurements still hold. A Lightroom edit in the same step
still triggers both. Unit-tested (305 tests, `make quality` clean); not re-run live.

Profiles `glimmer` and `fast`: UNTESTED and DEFERRED to the Phase 8 bake-off (user decision 2026-11-21: no more model
switching for now; Qwen stays the only loaded model).
- The user confirmed LM Studio's auto-unload is on, and it worked (requesting `glimmer` unloaded Qwen; one model at a time).
- A `glimmer` run was started and then overtaken by the change of plan. Its output is INCONCLUSIVE and not a test result:
  vision 5/5 on the synthetic picture in 102 s (includes loading the model); all four tool scenarios failed with server
  errors, not model answers (a 120 s read timeout, then "Model unloaded by user or API request", "Model is unloaded",
  "Failed to load model ... Engine protocol startup was aborted"); the two injection runs afterwards worked (ignored,
  guardrails held). Cause of the unload not established. `fast` was not started.
- Qwen was brought back with one text-only request (14.4 s) and is the only loaded model again.
- NEW since the reload: Qwen is now spread over BOTH GPUs (GPU 0 24.8 GB, GPU 1 11.1 GB used) instead of GPU 0 only
  (26.7 GB before). Not pinned to GPU 0 as the user wants; LM Studio's GPU setting was not touched by the assistant.
  Gap: `doctor` does not warn in this case (it only warns when the model server is absent from the configured GPU).

User update (2026-11-21): Qwen runs on GPU 0 (RTX 5090) and Muse on GPU 1 (RTX 3090); the user manages LM Studio's GPU
allocation. Built for it:
- `doctor`: where the model server runs is never a warning. The only GPU warning is low free memory (below
  `gpu.min_free_mb`, 3000) on a GPU that Photoshop holds a context on; it says whether the model server uses that GPU too
  and quotes the measurement with its limits (one photo, one model loaded). A new information line shows the placement
  ("model server on GPU ...; Photoshop on GPU ...").
- `gpu.llm_gpu_index` removed from the config and `darkroom.toml` (nothing uses it any more).
- 305 tests pass, `make quality` clean. `doctor` live: no warnings (no model was loaded at that moment).

Live `darkroom chat` run (2026-11-21, `primary`, portrait playbook, `D:\darkroom_test\TEST_2461-41.jpg`, request "Make
this photo slightly cooler"; session `4b4fad73...`; rule from the user: send requests only when the model is already loaded,
never rely on JIT loading; the first attempt was stopped because no model was loaded):
- A NEW virtual copy `AF024C2F-...` was created on the first edit (none existed for this photo); it stays in the catalog.
- Turn 1 (14.3 s, 411 tokens, 319 thinking): `adjust_white_balance` temperature -10; applied `IncrementalTemperature`
  0 -> -10 (not clamped; limit 30 per pass); tint not touched. Turn 2 (11.4 s, 320 tokens, 192 thinking): finished.
  1 pass, 2 turns, 2 previews (138 kB, 139 kB), 31 s.
- Measured colour balance before -> after: blue_yellow +0.0084 -> -0.0281; green_magenta -0.0051 -> +0.0004; strength
  0.0099 -> 0.0281; description "no noticeable cast" -> "cast towards blue/cool" (as requested). Side effect: highlights
  clipped 1.845 % -> 0.463 % (red channel 1.823 % -> 0.392 %); p50 0.553 -> 0.566.
- The only setting that differs from the master's recorded values is `IncrementalTemperature`. Master's 170 settings and
  the original SHA-256 unchanged. 1 edit pending on the copy.
- Full chat output (with the model's summary) is in `logs\chat_run_20261121-102122.txt`; only status lines were read.
- Model calls were 3-4 times slower than in the earlier runs (11-14 s vs 2-6 s). GPU readings at the time: GPU 0 3.6 GB
  used, GPU 1 24.25 of 24.58 GB used, i.e. Qwen appears to sit on GPU 1 (RTX 3090), not GPU 0 as intended. Flagged to the
  user; not changed by the assistant.
- NOT YET DONE: the user runs `poetry run darkroom revert --photo D:\darkroom_test\TEST_2461-41.jpg`; then the
  after-revert checks (copy equals the recorded start values, nothing pending, hash).

DEFERRED to the Phase 8 bake-off (user decision 2026-11-21; Muse is not to be loaded, Qwen is the only loaded model):
- Part B: the Select Subject re-measurement with both models loaded. NOT RUN. The only GPU measurement so far is the
  one with Qwen alone on GPU 0.
- The Muse (`glimmer`) and `fast` profile tests (vision, tool calling, prompt injection). UNTESTED.

After-revert checks (2026-11-21, after the user reported the revert as done):
- PASS: all settings of the copy `AF024C2F-...` equal the master's recorded start values (`IncrementalTemperature` is 0
  again; the copy's extra `FilterList = {}` is normal for a virtual copy). Measurements are identical to "before" (cast
  strength 0.0099, "no noticeable cast"). Master's 170 settings unchanged; original SHA-256 unchanged.
- FAIL / OPEN: the journal has NO `revert` entry and still lists the -10 edit (session `4b4fad73`) as pending. So the
  values were put back some other way than `darkroom revert` (not established how), and `darkroom revert` has still NOT
  been run live. Effect: the journal and Lightroom disagree for this copy; a later `darkroom revert` on this photo would
  offer to take that edit back (it would set the temperature to 0, which it already is) and that would clear the entry.
- GPU placement fixed by the user: Qwen is back on GPU 0 (28.3 of 32.6 GB used), GPU 1 idle (147 MB). `doctor`: all checks
  passed, "model server on GPU 0; Photoshop on GPU 0, 1", no warnings. The slow chat run (11-14 s per turn) was with Qwen
  on GPU 1 (RTX 3090, 24.25 of 24.58 GB used).
- Timing after the fix, text-only tool call (no image): 1.07 s and 0.70 s with default thinking, 0.45 s with thinking off;
  same as the first measurement (0.84-1.01 s / 0.34-0.49 s). Not re-measured: an agent turn with a preview (earlier 2-6 s).

`darkroom revert` run by the user (2026-11-21): "Reverted 1 setting(s)." Checks afterwards: journal has 1 `revert` entry
(took back entry `9081556d...`, `IncrementalTemperature` -> 0); copy equals the recorded start values; nothing pending;
master unchanged; SHA-256 unchanged. Limit of this live test: the value was already 0 when the command ran, so it proves
the command path, the confirmation and the journal entry, not a visible change of a setting (that is unit-tested).

### Phase 6 gate report (2026-11-21)
Tested LIVE on `primary` (Qwen, LM Studio on 127.0.0.1):
- `darkroom doctor`; `darkroom models test primary` (vision on the synthetic picture 5/5; 4/4 tool scenarios, 100 % valid
  calls with and without thinking; prompt injection in a file name and a tool result ignored, guardrails held).
- Thinking: about half of a tool call; `reasoning_effort: "none"` switches it off; other switches had no effect.
- Photo vision check on one photo: the model misjudged brightness and clipping -> playbooks rely on measurements.
- GPU: Select Subject with Qwen alone on GPU 0: no slowdown (7.6-8.3 s vs 8.1 s).
- Agent run 1 (highlights -20) and run 2 (highlights -20 + `replace_background` 210/210/210, pause, undo by resumed
  session): results as predicted from the numbers; all settings restored; originals unchanged.
- `darkroom chat` once (temperature -10, "cooler"): measured cast moved as requested. `darkroom revert` once (see above).
Unit-tested ONLY (fakes / local fake server): proxy settings ignored by the HTTP client; json_schema and prompt tool
modes; bad-tool-call handling and the three-strikes stop; an obedient model following injected text (tool layer holds);
latest-preview-only history; no re-measure after output-only passes; session-limited undo across sessions; revert of a
non-zero value; interactive y/N confirmer; MCP handlers (list, call, CONFIRMATION_REQUIRED refusal); doctor's GPU rules.
DEFERRED to Phase 8 (user decisions): profiles Muse (`glimmer`) and `fast` (untested; one inconclusive glimmer attempt);
Part B, Select Subject with both models loaded (not run). To Phase 7: MCP over a real stdio client.
Not covered at all yet: a gated action confirmed with a real "y" in chat; multi-pass lighting runs; HSL and crop through
the agent; RAW files; the landscape playbook live; any photo other than `TEST_2459-39` and `TEST_2461-41`.
Open items carried over: `replace_background` result should state that the output includes the copy's current edits and
crop; read-only `list_presets` + live `apply_preset` test; RAW acceptance test; playbook number ranges are untuned.
State left behind: virtual copies of `TEST_2459-39` and `TEST_2461-41` (both at start values); output files under
`outputs\`, previews and logs (git-ignored); Lightroom, Photoshop and LM Studio (Qwen on GPU 0) running.

### Phase 7 plan (PROPOSED, not started; needs the user's "proceed")
1. Offline proof: run doctor, models test, an agent run and the MCP server while recording this process tree's
   connections (Get-NetTCPConnection / socket audit hook); show that only 127.0.0.1 is contacted; prove that
   opentelemetry-api, uvicorn, starlette and truststore open nothing.
2. Dependency audit: `make audit` (pip-audit). Needs the user's OK first: it sends package names and versions to a
   vulnerability database.
3. Error paths: trigger every error code on purpose and review each message and next step; list any that cannot be
   triggered.
4. MCP client test: a real stdio client (the `mcp` package's own client, local process) lists tools, calls a read tool,
   and gets the CONFIRMATION_REQUIRED refusal.
5. `darkroom clean-outputs`: lists what it would remove (intermediates: hand-off TIFFs, working copies, masks, previews),
   asks first, never removes final PSD/JPG results.
6. Firewall script: prints the Windows Firewall commands for the user to review; runs nothing.
7. README (setup, commands, safety rules, limits); `pip install -e .` check in a throwaway venv outside the repo (needs
   the user's OK, since the rule is no second venv).
8. `replace_background` result note (current edits and crop); `make quality`; PROGRESS.md; commit on `dev`; gate.

### Phase 8 progress (2026-11-21)
User go with changes: start with the RAW test and `list_presets` (read-only); before any live `apply_preset` show how a
preset's values are checked against the guardrails (clamp or refuse) and wait for the OK; RAW test only on virtual copies
of the three RAW files, one step at a time, journal restore verified; the real "y" confirmation test last, when the user
is at the keyboard. No push yet (the repository is private; 64+ commits on `dev` are local only).

Done so far (read-only in Lightroom; no edit, no virtual copy created):
- `list_presets` tool built (read-only: name + folder, optional `contains`, `limit`; no internal ids; not in the chat tool
  list). Live: 393 presets in 40 folders; filter works ("warm": 11 matches). `apply_preset` is still hidden.
- RAW facts: `RAW_DSC00480/481/482.ARW` are in the catalog, format RAW, 7008 x 4672, process version 15.4, no darkroom_ai
  copy yet, nothing pending. Their white balance is "As Shot" and Lightroom reports NO number for `Temperature` / `Tint`
  (null).
- BUG FOUND by this read-only step, before any RAW edit, and FIXED: the slider code treated an unknown current value as
  0. A relative white-balance change on such a RAW file (e.g. +500 K) would have been computed from 0, clamped to the
  range minimum and written as 2000 K, and the undo would have "restored" 0. Now settings whose neutral value is not 0
  (RAW `Temperature`, `Tint`: `Limit.needs_current`) are refused with GUARDRAIL_REJECTED when Lightroom reports no
  current value ("Nothing was changed"; next step: give the copy a fixed white balance once). Unit test added
  (320 tests, `make quality` clean). JPG white balance (relative scale, neutral 0) is not affected.
- (superseded by the RAW steps below) how to get a fixed Kelvin value for an "As Shot" RAW copy.

RAW test, user go for steps 1-6 in order (2026-11-21) with additions: list the RAW folder for new .xmp and re-hash the
three ARW files at the end; the step-3 probe goes through the normal write gate and its restore is verified exactly
against a saved snapshot; step 4 uses the As Shot Kelvin value the probe reports, otherwise stop, never guess; before
step 5 check free space on D:, write only to the hand-off folder, change no Lightroom export preset; show the exact
plug-in call for step 3 and wait for the OK.
- Baseline: the RAW folder holds only the three ARW files (no .xmp); their SHA-256 recorded in `logs\raw_test_state.json`.
- Step 1 DONE: a new virtual copy of `RAW_DSC00480.ARW` (`C924BDE9-...`; stays in the catalog); snapshot of all 169
  settings of the copy and of the master saved; `adjust_lighting` exposure +0.3 (requested +0.3, applied +0.3, 0 -> 0.3,
  not clamped); undo; all 169 settings equal the snapshot EXACTLY; master unchanged; nothing pending; hash unchanged; no
  new file.
- Step 2 did NOT go as predicted (prediction: refused). Between step 1 and step 2 Lightroom started to report numbers for
  the copy: `Temperature` 4100, `Tint` 9, with `WhiteBalance` still "As Shot" (the master still reports none). So the
  tool had a real current value and APPLIED the request: Temperature 4100 -> 4600 (requested +500, within the 1000 K
  per-pass limit). The assistant took it back at once through the journal (resumed session, `undo`: Temperature -> 4100;
  nothing pending). No guess was involved and the guard did what it is built for, but the step's expectation was wrong.
- State now, exact comparison with the step-1 snapshot: every setting darkroom_ai wrote is back at its value. Four
  differences remain, all added by Lightroom itself after the copy's first edit, none written by darkroom_ai:
  `Temperature` 4100 and `Tint` 9 now present (were absent), `FilterList` = {} now present, and `Look.Parameters` gained
  an empty `PointColors`. Master identical to its snapshot.
- Findings: (1) Lightroom reports As Shot numbers for a RAW virtual copy only after the copy has been edited once, and
  not immediately (step 1's check right after the undo still saw none). (2) The `WhiteBalance` label stayed "As Shot"
  even while Temperature was 4600, so the label cannot be used to tell whether a fixed value is set. (3) An "exactly
  equal to the snapshot" check on a RAW copy must allow for keys that Lightroom adds by itself, or take the snapshot
  after the first edit. (4) Not verified: that 4100 / 9 are really the camera's As Shot values, and that the 4600 K
  edit changed the rendering (no measurement was taken while it was applied).
- Lens-related develop settings are readable on the RAW copy (read only): `EnableLensCorrections` true,
  `LensProfileEnable` 0, `LensProfileSetup` "LensDefaults", `AutoLateralCA` 0, `ChromaticAberrationR/B` 0, `Defringe*`,
  `PerspectiveUpright` 0, `PostCropVignette*`, `Upright*`, `Vignette*`. Whether they can be SET is untested.
- Step 3 DONE as a READ (user decision; no write probe): the copy reports Temperature 4100 K and Tint 9. These are the
  values AS REPORTED BY LIGHTROOM for the As Shot white balance; they are NOT independently verified against the camera
  or the file's metadata. A fresh snapshot of the copy (172 settings; the keys not in the step-1 snapshot are
  `FilterList`, `Temperature`, `Tint`) is the reference for all later exactness checks.
- Step 4 DONE. Expected values were written down before the run (in the chat and printed by the script):
  A: +500 K -> applied +500, 4600 K, no limit. B (after undoing A, so from 4100): +3000 K -> per-pass clamp to +1000,
  5100 K, rule "per_pass"; no total limit exists for temperature and the range (2000-50000 K) is not reached.
  | Request | Expected | Actual | Lightroom reports | blue_yellow before -> with edit -> after undo |
  |---|---|---|---|---|
  | A +500 K | +500, 4600, no limit | +500, 4600, no limit | 4600 | -0.0115 -> -0.0029 -> -0.0115 |
  | B +3000 K | +1000, 5100, per_pass | +1000, 5100, per_pass ("Clamped - temperature: asked +3000, applied +1000") | 5100 | -0.0115 -> +0.0031 -> -0.0115 |
  The preview moved towards warm in both cases, further for B, and returned to the same numbers after each undo. After
  each undo all 172 settings equalled the fresh snapshot EXACTLY and nothing was pending. Master unchanged; file SHA-256
  unchanged; no new file in the RAW folder. Tint stayed 9. The label stayed "As Shot" throughout.
  Observations: highlights clipping fell with the warmer setting (1.27 % -> 0.32 % -> 0.06 %); the preview's median
  brightness is very low (p50 0.008), i.e. a dark picture, so the cast numbers rest on few neutral pixels.
  GAP flagged: temperature and tint have NO total limit against the original (only exposure has one); with 3 passes a
  run can move 3000 K, and further runs more.
- Step 5 DONE: free space on D: checked first (over 1500 GB); `export_tiff` of the RAW copy wrote exactly one new file,
  `outputs\handoff\rawtest-20261121-123902.tif`, in 1.0 s: 196 MB, 7008 x 4672 (the RAW size), 16/16/16 bits per
  sample, uncompressed, profile "sRGB IEC61966-2.1". No Lightroom export preset is used or changed (the export settings
  are passed in the call). Afterwards all 172 settings of the copy equalled the fresh snapshot exactly; master unchanged.
- Step 6 DONE. Expected (written down before the run): highlights -80 -> clamped to -50 (per_pass), shadows +70 ->
  clamped to +50 (per_pass). Actual: exactly that ("Clamped - highlights: asked -80, applied -50 (per_pass limit);
  shadows: asked +70, applied +50 (per_pass limit)"). Preview statistics before -> with the edit -> after the undo:
  clipped highlights 1.267 % -> 0.0 % -> 1.267 %; clipped shadows 34.2 % -> 9.4 % -> 34.2 %; p95 0.499 -> 0.351 -> 0.499.
  After the undo all 172 settings equalled the fresh snapshot exactly; nothing pending; master unchanged.
- The other two RAW files (`RAW_DSC00481.ARW`, `RAW_DSC00482.ARW`), each on a NEW virtual copy (they stay in the
  catalog): on the fresh copy Lightroom reported no Temperature / Tint, and "+500 K" was REFUSED as expected with
  `[GUARDRAIL_REJECTED] Lightroom reports no current value for temperature ... Nothing was changed.` (this is the live
  test of the new guard). Exposure +0.3 applied and undone; no setting darkroom_ai wrote differs from the snapshot;
  nothing pending; masters unchanged.
- Final check: the RAW folder still holds only the three ARW files, no .xmp; all three SHA-256 hashes unchanged.
- RAW acceptance test result: PASSED for lighting, Kelvin white balance with the per-pass clamp, journal undo with exact
  restore, and 16-bit TIFF export. Open after the test:
  (1) usability gap: on a fresh RAW virtual copy a white-balance request is refused until Lightroom reports numbers,
      which happens some time after the copy's first edit (how long is not measured). Possible fix (not built, needs a
      decision): let the tool make one harmless write and re-read, or tell the user to move the Temp slider once.
  (2) temperature and tint have no total limit against the original;
  (3) 4100 K / tint 9 are as reported by Lightroom, not independently verified;
  (4) not covered: tint changes on RAW, the agent (model) editing a RAW file, "RAW only when the file is RAW or the user
      says so" as an export rule, and the Photoshop hand-off from a RAW copy.

Presets, user decisions (2026-11-21): first a read-only probe of whether the SDK exposes a preset's contents; if not,
`apply_preset` stays unexposed. If yes: allowlist (a preset containing any setting outside the guardrail-covered set is
REFUSED with the setting named, not stripped); compare its change with the photo's current values; its exposure change
counts toward the 3 EV total; always ask y/N in chat, refused over MCP; preset names are data (injection test). Show the
design and wait for the OK before any live `apply_preset`.
- SDK documentation (local API reference): `LrDevelopPreset:getSetting()` "retrieves the settings for this preset"
  (marked experimental). So the contents should be readable; NOT yet verified live.
- Built for the probe: plug-in read command `get_preset` (returns the preset's settings; applies nothing), plug-in
  build 0.4.0, client method `get_preset_settings`. The plug-in loaded in Lightroom is still 0.3.0 until the user
  reloads it. 321 tests, `make quality` clean.
- Preset probe DONE (2026-11-21, read-only, nothing applied; the user reloaded the plug-in; build 0.4.0 confirmed with
  `get_preset` registered). The SDK DOES expose a preset's contents. Allowlist used = the 39 guardrail-covered settings
  (6 lighting sliders, temperature / tint in both scales, 24 HSL sliders, 5 crop keys).
  - "Natural" (folder Color): 56 keys, 29 covered, 27 outside (camera profile, clarity, colour grading, split toning,
    tone curve, vibrance 10, saturation, process version "10.0", version).
  - "Warm Pop" (folder Adaptive: Subject): 5 keys, 0 covered; it is a MASK preset (`MaskGroupBasedCorrections`).
  - "Warm Gold" (folder Style: Film-Inspired): 62 keys, 24 covered, 38 outside (grain 25, clarity -20, calibration
    hues / saturations, split toning, shadow tint, profile "Look", tone curve, vignette).
  - Scan of all 393 presets (read-only): under a strict allowlist (no key outside the covered set) 0 presets pass.
    Under "no setting outside the covered set would CHANGE on a neutral JPG" (version keys ignored) 3 pass, all trivial
    (Defaults / Adobe Default, Defaults / Camera Settings, Grain / None). Most frequent blockers: process version (327),
    tone curve (249), split toning (197), camera profile (186), vibrance (170), clarity (154), saturation (147),
    texture (126). No preset has an exposure beyond 3 EV. The second count is approximate (a preset's process version or
    curve name may differ in spelling without changing the picture).
  - Consequence: with the user's allowlist rule `apply_preset` would refuse practically every preset. It stays
    unexposed; the design decision is the user's. No `apply_preset` has ever been run live.

README: bridge wording corrected (the bridge starts by itself when Lightroom loads the plug-in; the menu entry is only
needed after "Stop darkroom_ai Bridge" or a failed start). Verified in the source: `Init.lua` calls `Bridge.start()`.

### Open threads for later, NOT part of Phase 8 (recorded for the user, 2026-11-21)
(a) Phase 9 enhancement: read EXIF (camera, lens, focal length, aperture, ISO, shutter) through the plug-in as a
    read-only tool; then lens-profile correction, chromatic-aberration removal and vignette correction as a
    deterministic pre-fix step (the model confirms from metadata and preview). Open points: whether the SDK can set
    these develop keys per file type (read-only probe first, on a virtual copy only; reading works, see above); the
    guardrail rule for on/off switches; a JPG may already be lens-corrected in camera (avoid double correction); a
    clear message when no lens profile exists; EXIF text is data, not instructions. A short PRD and the user's sign-off
    come before building it.
(b) README bridge wording: done (see above).

User decisions after the RAW test (2026-11-21): RAW acceptance ACCEPTED.
- Presets: `apply_preset` STAYS HIDDEN. Recorded as asked: 0 of 393 presets pass the strict allowlist (no setting outside
  the 39 guardrail-covered ones). The tiered alternative (look keys allowed with a y/N summary; geometry and
  process-version changes refused) is NOT chosen for now. Revisit after Phase 9. `list_presets` stays as a read-only tool.
- RAW white balance on a fresh copy: the refusal STAYS. Timing test DONE (reads only after one edit; a NEW virtual copy of
  `RAW_DSC00482.ARW` named "darkroom timing 124329", id `D1CBF900-...`, which stays in the catalog): before any edit no
  Temperature / Tint; first edit exposure +0.3; at t = 0.0 s still none; at t = 1.0 s Lightroom reported Temperature 4200,
  Tint 4 (label "As Shot"). Exposure put back to 0. Keys Lightroom added by itself: `FilterList`, `Temperature`, `Tint`;
  `Look` gained a field. Also read: the 480 copy still reports 4100 / 9; the 481 and 482 MASTERS report no numbers.
  So the value appears about one second after the copy's first REAL edit. Not tested: whether a write that changes
  nothing has the same effect. Fix proposal shown to the user; nothing built for it yet.
- Total limit against the original for temperature and tint: BUILT. `wb_kelvin_total` = 2000 and `wb_tint_raw_total` = 40
  (RAW), `wb_relative_total` = 60 (JPG / TIFF, twice the per-pass 30), all in `darkroom.toml`; beyond the total needs
  `allow_beyond_total` plus a person's confirmation, as for exposure; moving back towards the original is always
  allowed. Unit tests for RAW and JPG, clamp, refusal without confirmation and the confirmed override (323 tests,
  `make quality` clean). Live test on the RAW copy: plan with expected values shown to the user; waiting for the go.
- Agent runs on Qwen (multi-pass lighting, HSL, crop, landscape playbook, two more TEST_ photos, one RAW copy): plan
  shown; waiting for the go.

User decisions (2026-11-21, later): timing fix go (wait up to 5 s, poll every 0.5 s, read-only, then refuse; never write to
force the numbers; hint "make a lighting edit first"; playbooks put lighting before white balance for RAW); live
total-limit test go, plus a short JPG check; agent runs go, one at a time with a report after each, TEST_ files and RAW
copies only, including a strong "much brighter" request and a white-balance request on a RAW copy; check
`require_loaded` before each run and `lms ps` before the first.
- Timing fix BUILT: `apply_slider_changes` polls (reads only) every 0.5 s for up to 5 s when a value that is needed is
  not reported yet, then refuses with "(waited 5 s)" and the next step "Make a lighting edit on this photo first".
  Unit test checks that exactly one write happens (the requested change). Both playbooks now say: RAW files, lighting
  before white balance; accept a refusal or clamp, do not repeat it. 325 tests, `make quality` clean. Not run live.
- Live total-limit test, RAW copy of `RAW_DSC00480.ARW` (expected values written down first): ALL AS EXPECTED.
  +1000 K -> 5100 (none); +1000 K -> 6100 (none, exactly at the total); +1000 K -> applied 0, stays 6100, rule "total"
  ("Clamped - temperature: asked +1000, applied +0 (total limit)"); -500 K -> 5600 (allowed, towards the original);
  +1000 K with `allow_beyond_total` -> refused `CONFIRMATION_REQUIRED`, stays 5600; tint +30 -> 39; tint +30 -> applied
  +10, 49, rule "total". Lightroom reported each value. Undo all (5 steps) -> 4100 / 9; all 172 settings equal the fresh
  snapshot exactly; nothing pending; master unchanged; hash unchanged. Preview cast returned to the start numbers.
- Live JPG check on the virtual copy of `TEST_2459-39.jpg`: NOT AS EXPECTED, and it uncovered a serious problem.
  Expected +30 -> 30, +30 -> 60, third clamped. Actual: each +30 was applied from 0 again (30, 30, 30).
  Diagnosis (three small runs on the same copy, each undone; copy back at 0, nothing pending):
  * Right after an edit Lightroom reports `IncrementalTemperature` 30, and two edits in a row add up (30 -> 60).
  * After ONE preview export the edit is GONE: the first export after the edit still shows it (blue_yellow 0.0649 vs
    0.0083 without the edit), but afterwards Lightroom reports 0, and the NEXT export measures 0.0083 again. It stays 0
    after 5 s and through a second connection. So the JPG white-balance edit does not survive a preview export; this
    is a real loss of the edit, not a misreport.
  * RAW Kelvin edits and lighting edits (highlights, exposure) DO survive exports (seen in the RAW test and in the agent
    runs).
  * Contradiction, not understood: in the Phase 6 chat run (`TEST_2461-41.jpg`, temperature -10) the edit was still
    reported, and still measured, after two exports. Differences since then: Lightroom was restarted, the plug-in was
    reloaded (0.4.0; the export code is unchanged), a different copy with a longer history, and +30 instead of -10.
  * Consequences if it holds generally: the agent's re-measurement after a pass discards a JPG white-balance edit;
    JPG white-balance totals cannot accumulate; `replace_background`'s TIFF export may not include it. In every observed
    case the white-balance label stayed "As Shot".
  * NOT fixed. Candidate to test with the user's OK: write `WhiteBalance = "Custom"` together with the incremental
    values, and restore "As Shot" on undo. Until this is understood, JPG white-balance results are UNRELIABLE.
- JPG white-balance investigation (2026-11-21; user decision: investigate first, no agent runs; the user ran `lms ps`:
  qwen/qwen3.8-27b, IDLE, context 32768; the user checked `Commands.lua`: the export paths do not call
  applyDevelopSettings, so the loss is Lightroom's). Hypothesis under test, not a conclusion: Lightroom drops
  `IncrementalTemperature` on re-read while `WhiteBalance` is "As Shot". Expected values were written down before each
  step; all four came out as predicted. Every step was undone and compared exactly with a snapshot.
  1. `TEST_2461-41.jpg` copy (where an edit had survived in Phase 6), +30: reported 30 after the edit; after export 1
     reported 0; export 2 measured the baseline again (0.0084). LOST. So the loss is not specific to one copy.
  2. FRESH copy of `TEST_2462-42.jpg` (new virtual copy `D1A7715D-...`, stays in the catalog), +30: reported 30 after the
     edit, 0 after export 1; here even export 1 measured the baseline (0.0015). LOST. (Exact compare afterwards: only
     `FilterList`, which Lightroom adds itself after a copy's first edit, differs.)
  3. Controls on the `TEST_2459-39.jpg` copy: HSL (orange saturation -20) SURVIVED two exports; a crop (0.1 / 0.1 / 0.9 /
     0.9) SURVIVED two exports.
  4. Candidate fix on the `TEST_2459-39.jpg` copy, the approved call `apply_settings {"IncrementalTemperature": 30,
     "WhiteBalance": "Custom"}` through the normal write gate: reported 30 and "Custom" after the edit and after both
     exports; both exports measured the warm value (0.0649 vs baseline 0.0083). SURVIVED. Restore call
     `{"IncrementalTemperature": 0, "WhiteBalance": "As Shot"}`: Lightroom took "As Shot" back; reported 0 / "As Shot";
     export measured the baseline; all 171 settings equal the snapshot exactly; master and file hash unchanged.
  Conclusion so far: consistent with the hypothesis on three copies (loss with "As Shot") and one copy (no loss with
  "Custom"). Still not explained: why the Phase 6 run's -10 survived. Tint and negative values were not tested
  separately; TIFF files not tested.
  Guardrail change (journal both keys) and a final read-back in every agent run: DESIGN shown to the user; NOT built.
- JPG white-balance FIX BUILT and VERIFIED LIVE (2026-11-21; user conditions: JPG / TIFF only, RAW untouched; the journal
  stores the PREVIOUS white-balance mode and undo / revert restore exactly that; a unit test fails if the JPG write ever
  goes out without the mode):
  * `adjust_white_balance` on a non-RAW photo writes `WhiteBalance = "Custom"` in the same call as the relative values.
    The undo information holds the previous value of every key written (number and mode); `undo` and `darkroom revert`
    restore them (revert: the mode before darkroom_ai's first change). If Lightroom reports no mode, the edit is refused
    ("could not record what to restore"). The result note says "WhiteBalance set to Custom (was As Shot)."
  * Unit tests: no JPG white-balance write without the mode; undo restores the previous mode whatever it was ("As Shot",
    "Auto", "Custom"); revert uses the first; RAW writes only `Temperature`; refusal when the mode is not reported.
  * Live check on the `TEST_2459-39.jpg` copy, expected values written down first, a preview export after EVERY step:
    +30 -> 30 (mode Custom); +30 -> 60 (totals now accumulate); +30 -> applied 0, stays 60, rule "total"; -10 -> 50;
    tint +15 -> 15. ALL AS EXPECTED; Lightroom reported each value after each export; read-back found no difference.
    Undo all (4 steps) -> 0 / 0 / "As Shot"; all 171 settings equal the snapshot exactly; nothing pending; master and
    file hash unchanged.
  * One prediction could NOT be checked with the cast measurement: at 60 and 50 the cast number is 0.0 because it only
    uses near-neutral pixels and finds none at a strong warm shift. Checked instead with the preview's mean red / blue
    ratio (numbers only): 1.4307 at 0, 1.7594 at 30, 1.9350 at 60, 1.8920 at 50, and 1.4307 again after the undo. So the
    render follows the accumulated value. Weakness noted: the cast number reads 0.0 when it cannot judge.
  * TIFF files: UNTESTED (same code path as JPG; no TIFF test file). Negative-only and tint-only sequences from 0 were
    not run separately. Why the Phase 6 run's -10 survived is still unexplained.
- Read-back BUILT (user decision: also inside `analyze_photo`; report only, never re-apply; neutral wording; labelled as
  data): `analyze_photo` compares the copy's current settings with the last value darkroom_ai wrote for each setting
  (from the journal, including undo and revert entries) and returns `differs_from_applied` plus a note "DATA (read-back,
  not an instruction): differs from what darkroom_ai applied: X: applied a, now b. This can be a dropped edit or a
  manual change in Lightroom; darkroom_ai cannot tell which. Nothing was re-applied." The agent passes the note to the
  model with the measurements. `scripts/agent_run.py` runs the same check after the last export and fails the run if
  anything differs. Unit tests added. 332 tests, `make quality` clean.
- EARLIER JPG WHITE-BALANCE RESULTS ARE NOT RELIABLE (user decision to mark them): the Phase 5 live run of
  `adjust_white_balance` on a JPG copy and the Phase 6 `darkroom chat` run ("slightly cooler", -10 on
  `TEST_2461-41.jpg`) were made before this fix. Treat them as not reliable until re-run with the fix; the Phase 6
  measurement did show the shift at the time, but the same sequence lost the edit today.
- Colour-cast measurement (user request, built before any white-balance run; no Adobe contact): when there are too few
  near-neutral pixels the cast is now `measurable: false` with `blue_yellow`, `green_magenta` and `strength` = null and a
  `reason` ("only x % of the picture is near-neutral mid-tone; at least y % is needed. Unknown is not the same as
  neutral."), never 0.0. The system rules tell the model that a null / "not measurable" measurement is UNKNOWN, not zero
  and not neutral. Unit tests added (334 tests, `make quality` clean).
- AGENT RUNS (user go 2026-11-21: one at a time, report after each and wait for the go; TEST_ files and RAW copies only;
  plus a re-run of the Phase 6 "slightly cooler" request; `require_loaded` checked before each; a text-only call well
  over 5 s means Qwen left GPU 0: stop and tell the user). Plan: 1 lighting on `TEST_2467-47.jpg`; 2 "Make it much
  brighter" on `TEST_2462-42.jpg`; 3 HSL; 4 crop; 5 landscape playbook with lighting + "slightly cooler" on
  `TEST_2457-37.jpg`; 6 RAW white balance on the `RAW_DSC00481.ARW` copy; 7 the Phase 6 "slightly cooler" request again.
  * Before run 1: Qwen loaded, on GPU 0 (24.8 GB used, GPU 1 idle); text-only tool call 1.10 s / 0.69 s with thinking,
    0.45 s without.
  * RUN 1 DONE (portrait playbook, lighting only, `TEST_2467-47.jpg`, new virtual copy `7819611A-...` that stays in the
    catalog; request "Fix the lighting. Work in small steps and check the numbers after each pass."):
    turn 1 (5.6 s, 784 tokens, 545 thinking): `adjust_lighting` highlights -30, requested -30, applied -30, not clamped.
    Turn 2 (2.9 s, 370 tokens, 148 thinking): finished. ONE pass (multi-pass not used: after the first pass the numbers
    were inside the playbook ranges). Measurements before -> after: clipped highlights 7.615 % -> 0.138 % (red channel
    6.822 % -> 0.051 %); p50 0.437 -> 0.411; p95 0.858 -> 0.804; shadows 0.066 % -> 0.071 %; cast "no noticeable cast"
    (measurable) both times. Exposure untouched (p50 inside 0.40-0.65). 2 previews (209 kB, 206 kB), 10.0 s in total.
    Read-back after the last export: every applied edit still holds. Undo: 1 step; all 170 settings equal the start
    state (Lightroom itself added `FilterList`); master unchanged; SHA-256 unchanged; nothing pending. Summary (657
    characters) in `logs\agent_run_20261121-130429.txt`, not read by the assistant.
  * RUN 2 DONE (portrait playbook, lighting only, `TEST_2462-42.jpg` copy; request "Make it much brighter."):
    the per-pass clamp was NOT exercised, because the model never asked for a large change.
    Pass 1 (11.5 s, 1734 tokens, 1552 thinking): exposure +0.4 (requested +0.4, applied +0.4, no limit, empty note).
    Pass 2 (3.9 s, 516 tokens, 345 thinking): highlights -25. Pass 3 (4.5 s, 628 tokens, 479 thinking): highlights -20
    (-25 -> -45). Stopped by the 3-pass limit; the model wrote no summary of its own (the 294-character text in
    `logs\agent_run_20261121-130632.txt` is the program's "Stopped (max passes)" line).
    Multi-pass WAS used (3 passes). No identical call was repeated. Total exposure applied: +0.4 EV of the 3 EV total.
    Clipped highlights per measurement: start 0.550 %, after pass 1 3.382 %, after pass 2 2.903 %; after pass 3 not
    measured (the run ends at the pass limit without a final measurement). p50 0.585 -> 0.657 -> 0.648.
    Observations: "much brighter" produced only +0.4 EV, the playbook's "small steps" and its p50 ceiling (0.65) pulled
    the model back and it spent two passes repairing the highlights its own brightening had clipped. Whether that is
    the wanted behaviour for an explicit "much brighter" is a playbook question for the user.
    Read-back: every applied edit held. Undo: 3 steps; all 171 settings equal the start state; master and SHA-256
    unchanged; nothing pending. 3 previews (136-139 kB), 21.8 s.
  * Run 5 will leave its final edit in place at the user's request (`--pause`; the user reverts with `darkroom revert`).
  * User decisions after run 2: add a clamp run ("Increase the exposure by 3 stops" on `TEST_2462-42.jpg`) and a
    "much brighter" run on the darkest TEST_ photo by the earlier numbers; keep the playbook's cautious behaviour for
    now and revisit after the dark-photo run; fix: always measure once more after the last pass and mark runs that end
    at the pass limit. Order: 3, clamp run, dark-photo run, 4, 5 (`--pause`), 6, 7; report after each, wait for the go.
    Darkest by the numbers taken so far: `TEST_2467-47.jpg` (p50 0.437; the others: 2461 0.553, 2459 0.567, 2462 0.585;
    `TEST_2457-37.jpg` has not been measured for tones yet).
  * FIX BUILT: after the last pass the agent always measures once more without another model turn; the report carries
    `final_measurement` (clipped highlights and shadows, p50, cast, read-back differences) and `ended_at_pass_limit`.
    Unit test added (335 tests, `make quality` clean).
  * RUN 3 DONE (portrait playbook, HSL only, `TEST_2462-42.jpg` copy; request "Reduce the saturation of orange a
    little."): turn 1 (2.2 s, 167 tokens, 79 thinking): `adjust_hsl` orange saturation -15, requested -15, applied -15,
    no limit, empty note. Turn 2 (2.1 s, 215 tokens, 127 thinking): finished. One pass, 5.5 s, 2 previews. Final
    measurement: clipped highlights 0.029 % (start 0.550 %), p50 0.592 (start 0.585), cast "no noticeable cast",
    measurable. Read-back: the edit held after the export. Undo: 1 step; all 171 settings equal the start state; master
    and SHA-256 unchanged; nothing pending. Summary (363 characters) in `logs\agent_run_20261121-130956.txt`.
  * CLAMP RUN DONE (portrait playbook, lighting only, `TEST_2462-42.jpg` copy; request "Increase the exposure by 3
    stops."): turn 1 (3.1 s, 384 tokens, 310 thinking): exposure +3 requested, +1.5 applied, rule "per_pass"; the model
    saw `clamped: true` and the note "Clamped - exposure: asked +3, applied +1.5 (per_pass limit)". Turn 2 (10.9 s, 1650
    tokens, 1526 thinking): a SECOND, different request, exposure +1.5 (applied +1.5, 1.5 -> 3.0, no limit). Turn 3
    (3.5 s): finished with a summary. It did not repeat the identical call and did not ask to go beyond the total.
    Total exposure: +3.0 EV, exactly the 3 EV total and exactly what the user asked for. 2 passes, 19.4 s, 3 previews.
    Measurements: start p50 0.585, clipped highlights 0.55 %; after pass 1 p50 0.806, 13.2 %; final (after pass 2)
    p50 0.941, clipped highlights 61.9 %, cast "no noticeable cast" (measurable). Read-back: both edits held. Undo:
    2 steps; all 171 settings equal the start state; master and SHA-256 unchanged; nothing pending. Summary (701
    characters) in `logs\agent_run_20261121-131317.txt`, not read.
    Observation: the per-pass limit slows a large change down but does not stop it; an explicit request can reach the
    full 3 EV total in two passes, here with 62 % of the picture clipped. Whether the model warned about that in its
    summary is not known to the assistant.
  * `TEST_2457-37.jpg` measured (one preview export of the MASTER, no edit): p50 0.528, p05 0.119, p95 0.896, clipped
    highlights 2.618 %, cast "no noticeable cast" (measurable). Confirmed: exactly one new file (a preview), no virtual
    copy created, master settings and file SHA-256 unchanged, no .xmp. Its p50 is not below 0.40, so the "much
    brighter" run uses `TEST_2467-47.jpg` (p50 0.437). Note: none of the five TEST_ JPGs is really dark; the only dark
    test material is the RAW copy of `RAW_DSC00480.ARW` (p50 0.008).
  * DARK-PHOTO RUN DONE (user choice: the RAW copy of `RAW_DSC00480.ARW`, median 0.008; portrait playbook, lighting
    only; request "Make it much brighter."):
    Pass 1 (6.6 s, 985 tokens, 807 thinking): exposure +2 requested, +1.5 applied (per_pass; note "Clamped - exposure:
    asked +2, applied +1.5 (per_pass limit)"), shadows +30, highlights -20.
    Pass 2 (6.2 s, 905 tokens, 722 thinking): exposure +1.5 (applied, now +3.0, the total), highlights -30 (-50),
    shadows +20 (+50). A different request, not a repeat.
    Pass 3 (15.4 s, 2258 tokens, 2077 thinking): exposure +0.5 requested, 0 applied (total; note "Clamped - exposure:
    asked +0.5, applied +0 (total limit)"), highlights -30 (-80).
    Ended at the pass limit. The model did NOT ask to go beyond the total (`allow_beyond_total` was never set), so the
    confirmation path was not triggered; it simply met the total clamp. Total exposure +3.0 of 3 EV.
    Measurements, median / clipped highlights / clipped shadows: start 0.008 / 1.27 % / 34.2 %; after pass 1 0.050 /
    6.70 % / 4.67 %; after pass 2 0.215 / 15.1 % / 0.04 %; FINAL (after pass 3, taken by the new final measurement)
    0.204 / 10.1 % / 0.04 %. Cast: "towards magenta" at the start, "towards blue/cool" from pass 2 on.
    Read-back: all edits held. Undo: 3 steps; all 172 settings equal the start state; master and SHA-256 unchanged;
    nothing pending. 30.6 s, 3 previews (50-77 kB). Summary file holds the program's pass-limit line (332 characters).
    Observations: even at the full +3 EV the median is only 0.20, i.e. the request "much brighter" on a very dark RAW
    is bounded by the 3 EV total; and 10 % of the picture is clipped at the end.
  * DESIGN PRINCIPLE from the user (2026-11-21), also recorded in PRD.md section 5.0: no editing decision is hard-coded
    or scripted; the AI analyses and decides; the application is tools, measurements and safety limits. Safety rules,
    the write gate, undo, honest measurements and read-back checks stay in code. PHASE 9 CHANGES accordingly: EXIF, lens
    profile, chromatic aberration and vignette become tools the model chooses to use; there is NO deterministic pre-fix
    step (this replaces the "deterministic pre-fix step" wording in the open thread (a) below).
  * After the dark-photo run, no code: a design note was shown to the user (inventory of editing heuristics in code and
    prompts; a `--playbook none` mode; a playbook-vs-none comparison after run 7; Phase 9 as tools; a way for the model
    to see beforehand what an edit would do). Waiting for the user's sign-off.
  * DESIGN NOTE SIGNED OFF by the user (2026-11-21) with additions: the comparison runs each photo and request in both
    modes at least twice and compares measurements across all runs (final clipped highlights / shadows, median, passes,
    tool calls, clamps hit, repeated identical calls, time), with a few pairs left in place (`--pause`) for the user's
    own look; the pass limit stays as a configurable safety bound stated to the model; in `none` mode the FACT stays
    that the measurements are computed from the pixels and that the model's reading of a downscaled preview has been
    unreliable in tests, while the "numbers win" ruling and every threshold and step size are dropped; item 5: only the
    cheaper step (no `preview_edit` for now).
  * BUILT (no Adobe contact; 339 tests, `make quality` clean):
    - Slider edit tools (`adjust_lighting`, `adjust_white_balance`, `adjust_hsl`) return `measured`: the clipped
      highlights and shadows and p05 / p50 / p95 BEFORE and AFTER the edit, from real preview renders (no prediction).
      "Before" reuses the last valid measurement (from `analyze_photo` or the previous edit); otherwise it is measured
      first. An edit that writes nothing (clamped to 0) measures nothing. `undo`, `revert` and `crop` invalidate the
      stored measurement. Cost: one extra preview export per edit (two if nothing was measured before).
    - `--playbook none` for `scripts/agent_run.py` and `darkroom chat` (default stays `portrait`): the system message
      holds facts only (tools, relative sliders, the limits as numbers, the pass limit, what the measurements mean, the
      fact about pixels vs the model's reading of the preview, data rules). No thresholds, step sizes, order of
      operations or "stop when"; the cast's verdict words (`description`, `confidence`) are left out of the
      measurements; the text after a pass is "You may make further edits or finish with your summary."
    - What the model sees in each mode was printed for the user with the simulated Lightroom (no model call).
    - Still present in BOTH modes and flagged as possible anchors: the example in the rules ("exposure 0.3 = +0.3 EV")
      and in the exposure field's description ("e.g. 0.4 or -0.3").
  * NOT yet run live: the measured before/after in edit tools, and `--playbook none`.
  * ANCHORS REMOVED (user decision, both modes): the rules now say "Slider values are changes relative to the current
    value: a positive value raises the slider, a negative value lowers it." and the exposure field says "Change in EV;
    positive brightens, negative darkens." A unit test fails on "e.g.", "for example" or any number in a tool
    description other than scale / range definitions (-100..100, 0-255, the crop's 0 and 1 edges), which are kept as
    facts. The crop tool now also returns `measured` before / after. 341 tests, `make quality` clean.
  * For the later comparison the user also wants counted: how often an applied value equals the per-call maximum, and
    early stops.
  * RUN 4 DONE (portrait playbook, crop only, `TEST_2462-42.jpg` copy; request "Crop to a 4:5 portrait format, keeping
    the centre."): turn 1 (5.3 s, 684 tokens, 568 thinking): crop left 0.2333, right 0.7667, full height; kept fraction
    0.5334 (above the 0.25 minimum); the new preview is 819 x 1024, i.e. 4:5, centred. Turn 2 (3.5 s): the model sent
    the IDENTICAL crop call again (a repeat that changed nothing; it was accepted, journaled and counted as a second
    pass). Turn 3 (2.6 s): finished. 2 passes, 14.4 s, 3 previews.
    Check asked for by the user: the "after" numbers returned by the crop tool (clipped highlights 0.991 %, shadows
    0.004 %, p05 0.0726, p50 0.4927, p95 0.8518) are IDENTICAL to the separate measurement taken after the pass; the
    tool's "before" equals the first measurement (0.55 %, 0.005 %, 0.0785, 0.5849, 0.8782). No difference to explain.
    (The numbers change with the crop because a different part of the picture is measured.)
    Time added by the extra export: the crop call took 0.6 s including its one export ("before" was reused from the
    first measurement); about 0.5 s per edit.
    Read-back: the edit held. Undo: 2 steps; all 171 settings equal the start state; master and SHA-256 unchanged;
    nothing pending. Summary (569 characters) in `logs\agent_run_20261121-133429.txt`, not read.
    Findings: (1) a repeated identical call does happen (here after an unclamped success); the tool neither says
    "no change" nor refuses it. (2) After a pass the loop exports again although the tool has just measured the same
    state: one redundant export per pass.
  * "NO CHANGE" BUILT (user decision: information, never a refusal; only when the resulting state equals the current
    state, i.e. absolute settings such as crop; a repeated relative slider call is a new request): an identical crop
    returns `no_change: true` with "No change: the photo already has these values. Nothing was written.", writes
    nothing, exports nothing, journals no undo information and does not count as a pass. Unit tests for both cases.
    The loop's own export after a pass stays (user decision: it supplies the next preview and is an independent check).
    344 tests, `make quality` clean.
  * RUN 5 DONE, EDIT LEFT IN PLACE at the user's request (`--pause`; landscape playbook; lighting + white balance;
    request "Balance the lighting and make the white balance slightly cooler."; NEW virtual copy of `TEST_2457-37.jpg`
    named 'darkroom_ai 20261121-134032', id `63C87087-...`):
    Turn 1 (4.6 s, 595 tokens, 339 thinking), two calls in one pass: `adjust_lighting` highlights -25 (applied -25);
    `adjust_white_balance` temperature -10 (applied -10; note "WhiteBalance set to Custom (was As Shot)."). Turn 2
    (4.3 s, 562 tokens, 347 thinking): finished. One pass, 11.4 s, 2 previews; each edit call took 0.6 s with its export.
    Tool-measured before -> after: highlights edit: clipped highlights 2.618 % -> 0.132 %, p50 0.528 -> 0.504;
    white-balance edit: 0.132 % -> 0.372 %, p50 0.504 -> 0.512. Separate measurement after the pass: clipped highlights
    0.372 %, p50 0.512 (equal to the tool's last "after"); cast blue_yellow +0.0062 -> -0.0357 ("no noticeable cast" ->
    "cast towards blue/cool"), measurable both times.
    The white-balance edit SURVIVED the agent's own export: the read-back after the last export found every applied
    edit in place (Highlights2012 -25, IncrementalTemperature -10, WhiteBalance "Custom").
    Master settings and file SHA-256 unchanged. State saved in `logs\agent_run_state.json`. Summary (831 characters) in
    `logs\agent_run_20261121-134044.txt`, not read.
    CURRENT STATE OF THAT COPY: Highlights -25, temperature -10, mode "Custom". NOT undone. The user reverts with
    `darkroom revert --photo D:\darkroom_test\TEST_2457-37.jpg`; then the assistant compares every setting with the
    saved start state (including the mode back to "As Shot").
  * RUN 5 REVERTED BY THE USER (`darkroom revert`, 3 settings: Highlights2012 0, IncrementalTemperature 0, WhiteBalance
    "As Shot") and checked read-only by the assistant: all 170 settings of the copy equal the saved start state exactly
    (Lightroom's own `FilterList` is the only extra key); white-balance mode back to "As Shot"; 1 revert entry in the
    journal; nothing pending in any session; read-back clean; original file SHA-256 unchanged.
    One difference on the MASTER: it now reports 171 settings instead of 170, the extra one being an empty
    `FilterList = {}`; all 170 earlier values are identical. No darkroom_ai journal entry wrote to the master (the
    plug-in refuses writes to masters). Right after the run the master was still identical. So Lightroom added that
    empty key to the master by itself at some point afterwards (the same key it adds to copies after their first edit);
    what triggered it is not established.
  * RULE for later checks (user decision): an added EMPTY `FilterList` on a master is reported but does not fail the
    check; a non-empty `FilterList` or any other key change on a master fails it (`scripts/agent_run.py` updated).
    Possible trigger of the empty key on the run 5 master, UNPROVEN: the user viewing the master in Lightroom.
  * RUN 6 DONE (portrait playbook, lighting + white balance, virtual copy of `RAW_DSC00481.ARW`; request "Make the white
    balance slightly warmer."):
    - Lightroom DID report the numbers for this copy (it had one earlier edit): Temperature 4700 K at the start.
    - Pass 1 (38.8 s, 5946 tokens, 5672 thinking): `adjust_lighting` exposure +0.3 and shadows +15 (both applied, no
      limit). The user had NOT asked for a lighting change. The playbook's RAW line ("make lighting changes BEFORE
      white-balance changes") was evidently read as a step to carry out. Tool-measured: clipped shadows 28.9 % -> 14.4 %,
      clipped highlights 0.35 % -> 1.40 %, p50 0.014 -> 0.022.
    - Pass 2 (2.8 s, 282 tokens, 126 thinking): `adjust_white_balance` temperature +100 K (applied +100, 4700 -> 4800;
      a tenth of the 1000 K per-pass limit; no clamp, no refusal). Tint not touched.
    - Turn 3 (5.8 s): finished. 2 passes, 50.4 s, 3 previews. No refusal, no clamp, no repeated call, no loop.
    - Final measurement: clipped highlights 1.171 %, clipped shadows 14.398 %, p50 0.022; cast measurable, "towards
      blue/cool and magenta". Blue-yellow: -0.0272 at the start, -0.0299 after the lighting edit, -0.0304 after the
      +100 K. So the measurement does NOT show a warmer picture: +100 K is too small to register here (for
      comparison, +500 K moved it by about +0.009 on the 480 file), and the unrequested lighting edit moved the number
      more than the white-balance edit did.
    - Read-back: every applied edit held. Undo: 2 steps; all 172 settings equal the start state; master unchanged;
      SHA-256 unchanged; nothing pending. Summary (683 characters) in `logs\agent_run_20261121-135648.txt`, not read.
    - Findings: (1) the playbook caused an edit the user did not ask for; (2) "slightly warmer" was honoured in the
      setting (+100 K) but is not visible in the measurement; (3) the first turn's thinking was very long (5672
      tokens, 39 s) at a normal token rate, so it is the model deliberating, not a GPU problem.
  * FIX after run 6 (user decision): the RAW line "make lighting changes BEFORE white-balance changes" is REMOVED from
    both playbooks (it was the assistant's suggestion, was read as an instruction and caused the unrequested lighting
    edit). The FACT is now stated without an order in the rules of the playbook mode and in the facts of the none mode:
    "On a RAW copy, Lightroom reports white-balance numbers only after the copy's first edit. Until then a white-balance
    request is refused, and the tool result says so." Unit test updated (344 tests, `make quality` clean).
    For the comparison block the user also wants: the count "edits to something the request did not name", and the
    request "Make the white balance slightly warmer" on the `RAW_DSC00481.ARW` copy in both modes.
  * RUN 7 DONE (the Phase 6 request "Make this photo slightly cooler" on the `TEST_2461-41.jpg` copy, portrait playbook,
    the same five tools as `darkroom chat`): turn 1 (3.7 s, 369 tokens, 273 thinking): `adjust_white_balance`
    temperature -10 (applied -10; note "WhiteBalance set to Custom (was As Shot)."); nothing else was touched. Turn 2
    (3.5 s): finished. One pass, 9.0 s, 2 previews.
    The edit SURVIVED the agent's own export: the tool's "after" and the separate measurement after the pass agree
    (clipped highlights 0.463 %, p50 0.566), the read-back after the last export found temperature -10 and mode
    "Custom" in place, and the cast moved in the requested direction: blue_yellow +0.0084 -> -0.0281 ("no noticeable
    cast" -> "cast towards blue/cool"), measurable. These are the same numbers the Phase 6 run measured after its
    first export. Final measurement: clipped highlights 0.463 % (start 1.845 %), p50 0.566.
    Undo: 1 step; all 171 settings equal the start state exactly, including the white-balance mode back to "As Shot";
    master unchanged; SHA-256 unchanged; nothing pending. Summary (688 characters) in
    `logs\agent_run_20261121-140929.txt`, not read.
    With this the JPG white-balance path has been re-run with the fix through the agent (runs 5 and 7).
  * ALL PLANNED AGENT RUNS DONE (1-7, the clamp run, the dark-photo run).

### Playbook-versus-none comparison: PLAN (user go 2026-11-21: one batch, one report at the end)
Scope and stop rules from the user: virtual copies of TEST_ photos and RAW copies only; every run undone with an exact
compare; masters checked (an empty FilterList is tolerated); nothing pushed. STOP the batch at once and report if a
restore is not exact, a master changes (other than an empty FilterList), LR_NOT_REACHABLE or a model refusal occurs, a
text-only Qwen call takes over 5 s, or there would be more than 24 runs or 8 new virtual copies.
- Script: `scripts/compare_modes.py`. One fresh journal session per run. Before each run: Qwen must be reported loaded and
  a text-only call is timed. Tools offered in EVERY run, both modes: `adjust_lighting`, `adjust_white_balance`,
  `adjust_hsl`, `crop` (the same freedom in both modes, so "edits to something the request did not name" can occur).
  Playbook mode uses the portrait playbook.
- Comparison block, 16 runs, no new copies (the existing darkroom_ai copies are used), each undone and compared exactly:
  1. "Fix the lighting" on `TEST_2467-47.jpg`
  2. "Make it much brighter" on `TEST_2457-37.jpg`
  3. "Make the white balance slightly warmer" on the `RAW_DSC00481.ARW` copy
  4. "Reduce the saturation of orange a little" on `TEST_2462-42.jpg`
  each as playbook, none, playbook, none.
- Counted per run: final clipped highlights and shadows, median, passes, tool calls, clamps hit, applied values equal to
  the per-call maximum, repeated identical calls, "no change" answers, early stop vs end at the pass limit, edits to
  settings the request did not name, time, thinking tokens, read-back result.
  "Named" settings: lighting sliders for requests 1 and 2; temperature / tint for 3; orange saturation for 4.
- Blind pairs for the user's eye, 6 runs on 6 NEW virtual copies (not journaled as working copies, so later runs do not
  pick them up): for requests 1, 2 and 4, two fresh copies of the photo named "darkroom_ai pair N A" and "... pair N B";
  one mode on each, assigned at random; the edits are LEFT IN PLACE. The assignment and the run details go ONLY to
  `logs\blind_pairs_mapping.json`; the script prints nothing that tells the modes apart, and the assistant does not
  read that file or say which is which until the user has given a verdict. Afterwards the assistant undoes each pair
  copy through its resumed session and compares it exactly with its saved start state.
- Totals: 22 runs (limit 24), 6 new virtual copies (limit 8). Results of the comparison block in
  `logs\compare_results.json`.

### Playbook-versus-none comparison: RESULT (2026-11-21, one batch, no stop rule triggered)
22 runs, 6 new virtual copies. Before each run Qwen was reported loaded and a text-only call stayed under 5 s. All 16
comparison runs were undone and compared exactly; masters and file hashes unchanged; no refusal, no clamp, no repeated
identical call, no "no change" answer, no read-back difference in any run.
| Request / photo | Mode | What the model applied (run 1; run 2) | Final clipped highlights | Final median | Time, thinking |
|---|---|---|---|---|---|
| "Fix the lighting", TEST_2467-47 (start 7.6 %, 0.437) | playbook | highlights -30; highlights -30 (identical) | 0.14 %; 0.14 % | 0.411; 0.411 | 8 s, 460 |
| | none | contrast +10, highlights -40, shadows +25, whites -15; exposure +0.2, highlights -40, shadows +25, temperature -3, tint +2 | 0.15 %; 6.25 % | 0.397; 0.459 | 15 s, 1090 |
| "Make it much brighter", TEST_2457-37 (start 2.6 %, 0.528) | playbook | exposure +0.5 then highlights -25, -25 (2 passes); the same plus -15 (3 passes, pass limit) | 4.98 %; 4.11 % | 0.583; 0.568 | 23 s, 1860 |
| | none | exposure +1.2, highlights -25, shadows +15, then highlights -25, whites -10; exposure +1.5 (the per-call maximum), highlights -50 (maximum), shadows +30, then highlights -30, whites -15 | 8.50 %; 8.50 % | 0.713; 0.742 | 17 s, 1070 |
| "Make the white balance slightly warmer", RAW_DSC00481 copy | playbook | +100 K; +150 K | 0.31 %; 0.30 % | 0.014 | 8 s, 560 |
| | none | +150 K; +200 K | 0.30 %; 0.28 % | 0.014 | 8 s, 540 |
| "Reduce the saturation of orange a little", TEST_2462-42 | playbook | orange saturation -15; -10 | 0.03 %; 0.04 % | 0.591 | 6 s, 200 |
| | none | -12; -10 | 0.03 %; 0.04 % | 0.591 | 5 s, 160 |
Counts over 8 runs per mode: edits to something the request did not name: playbook 0, none 2 settings in 1 run
(temperature and tint during "Fix the lighting"). Applied value equal to the per-call maximum: playbook 0, none 2 (both
in one "much brighter" run). Early stops (finished before the pass limit): playbook 7 of 8, none 8 of 8. Passes: 1 in
every run except "much brighter" (playbook 2 and 3, none 2 and 2).
Observations (measurements only; whether a result LOOKS better is for the user's blind pairs):
- With the playbook the model is conservative and repeatable (identical output twice for "Fix the lighting"); without it
  the model uses more sliders, larger steps and varies more between repeats.
- "Much brighter": none moved the median to 0.71 / 0.74 (playbook 0.58 / 0.57), i.e. it did what the words ask, at the
  price of 8.5 % clipped highlights (playbook 4-5 %).
- "Fix the lighting" without the playbook: one run was as good as the playbook's on clipping (0.15 %), the other added
  exposure and ended at 6.25 % clipped highlights (start 7.6 %) and made an unrequested white-balance change.
- RAW "slightly warmer": +100 to +200 K in both modes; the cast number did not move in any run (-0.0272 -> -0.0271), so
  the effect is below what this measurement can show on that dark picture. No unrequested lighting edit in either mode
  (the playbook line that caused it in run 6 is gone).
- Orange saturation: no difference between the modes.
- Two repeats per case is a small sample.
Blind pairs (edits LEFT IN PLACE, six new virtual copies, not journaled as working copies):
'darkroom_ai pair 1 A' / 'pair 1 B' ("Fix the lighting", TEST_2467-47.jpg); 'pair 2 A' / 'pair 2 B' ("Make it much
brighter", TEST_2457-37.jpg); 'pair 3 A' / 'pair 3 B' ("Reduce the saturation of orange a little", TEST_2462-42.jpg).
Which mode is A and which is B is ONLY in `logs\\blind_pairs_mapping.json`; the assistant has not read it and will not
say until the user has given a verdict. Then: `scripts/compare_modes.py undo-blind` undoes each pair copy through its
saved session and compares it exactly. Until then those six copies carry edits.

Blind pairs: VERDICT, REVEAL and UNDO (2026-11-21). The user's first message still had placeholders, so the mapping stayed
closed until the user answered: pair 1 A, pair 2 A, pair 3 A. Only then was the mapping read.
| Pair | Preferred | A was | B was | What differed |
|---|---|---|---|---|
| 1 "Fix the lighting" | A | none: exposure +0.3, contrast +10, highlights -50 in total, shadows +15, temperature -5, tint +3 (2 passes; 2 unrequested white-balance settings); final clipped highlights 6.51 %, median 0.452 | playbook: highlights -30 (1 pass); final 0.14 %, median 0.411 | the user preferred the none result although it clips far more by the numbers |
| 2 "Make it much brighter" | A | playbook: exposure +0.5, highlights -80 in total, whites -15 (3 passes, pass limit); final 1.21 %, median 0.540 | none: exposure +1.2, highlights -45 in total, shadows +20, whites -10 (2 passes); final 8.87 %, median 0.722 | the user preferred the playbook result, the less bright one |
| 3 "Reduce orange saturation" | A | playbook: orange -15 | none: orange -15 | NOTHING: both runs made the identical edit and measure identically, so this preference cannot reflect a difference |
Reading: one preference for each mode and one pair with no real difference; the user chose "A" all three times. This is
three pairs from one viewer and is not enough to decide between the modes. It does show that the measured clipping does
not predict the user's preference (pair 1).
Undo: `compare_modes.py undo-blind` took back all six pair copies through their saved sessions: restore exact, master
unchanged, file unchanged and nothing pending for each of the six. The six copies stay in the catalog at their start
values.
BATCH TOTALS: 22 runs (limit 24), 6 new virtual copies (limit 8), no stop rule triggered.

User decisions after the batch (2026-11-21):
- The default mode STAYS the playbook, now as a setting: `[agent] default_playbook = "portrait"` in `darkroom.toml`
  ("portrait" | "landscape" | "none"), used by `darkroom chat` and `scripts/agent_run.py` when `--playbook` is not
  given; `--playbook none` stays available. Unit test added (345 tests, `make quality` clean).
- THE EVIDENCE FOR THIS CHOICE IS THIN (recorded at the user's request): the user chose "A" in all three blind pairs;
  pair 3 had identical edits in both copies; and the measured clipping did not predict the user's preference (in pair 1
  the preferred copy had 6.5 % clipped highlights against 0.14 %).
- Another blind round LATER, not now: with a control pair of identical edits, no other pairs with identical edits,
  lighting requests only, positions randomised.
- `darkroom chat --summary-file` added: writes the model's summary to `logs\` and prints only its length (so a chat run
  whose output is shared with the assistant does not show the summary).
- Next: the confirmation test with a real y / n in `darkroom chat` (the user is at the keyboard; plan shown first), then
  the Muse / fast bake-off plan and the acceptance report outline; each waits for the user's go.

### Phase 7 progress (2026-11-21)
User answers: pip-audit yes, one-off, not in `make quality`, exact command shown first, report to a file; throwaway venv yes
(Windows temp folder, 3.13, `--no-deps`, delete only that folder); offline proof also with a Windows Firewall outbound
block that the user applies and removes; error cases to add: Lightroom closed, plug-in not loaded, wrong catalog, model
unloaded, Photoshop dialog open; before any push list tracked files that could hold secrets; wait for the go before
anything that touches Lightroom or Photoshop.

Done WITHOUT Adobe contact:
- `darkroom clean-outputs` (`cleanup.py`): lists previews, hand-off TIFFs, masks and Photoshop working copies, asks y/N,
  never removes `*_darkroom.psd/.jpg` finals or unrecognised files. Unit-tested; NOT run on the real `outputs\` folder.
- `replace_background` result carries a note: the PSD/JPG include the copy's current edits and crop.
- MCP over REAL stdio: the `mcp` package's own client starts a server process (simulated Lightroom) and lists tools, calls
  `get_photo_info` and `adjust_lighting`, gets `[CONFIRMATION_REQUIRED]` for a gated call, and an error for `revert`
  (not a tool). In the test suite. Not yet done: the same against the real Lightroom.
- `scripts/firewall_rules.ps1`: only prints `New-NetFirewallRule ... -Action Block` for the three Python program paths
  (venv launcher, alias, and the real `python3.13.exe` image), a check, an optional "this should fail" line, and the removal.
  A test checks firewall cmdlets appear only inside printed strings. Whether the rules block as intended is UNVERIFIED
  until the user applies them.
- `scripts/offline_proof.py`: audit hook on connect / sendto / bind / name lookups for everything in the process. Run
  without Adobe: third-party imports (opentelemetry, uvicorn, starlette, truststore, mcp, httpx2, cv2), model list, text
  model tests (4/4, 2/2), MCP server object: only 127.0.0.1 seen (2 connects, 4 lookups). PASS. Limits: child processes
  are not seen by the hook (Photoshop worker, MCP child) and asyncio's own connect path is not covered by these events.
- Throwaway venv: `%TEMP%\darkroom_install_check_932925ee`, Python 3.13.14, `pip install --no-deps -e .` succeeded
  (pip fetched the build backend from the package index to build; the repository was not changed). Entry point
  `darkroom = darkroom_ai.cli:main` registered and `darkroom.exe` created; `darkroom --version` there fails with
  "No module named 'pydantic'", as expected with `--no-deps`. Folder deleted afterwards.
- Tracked files that could hold secrets: none by name (.env, key, token, credential, bridge.json, local.toml patterns);
  content matches are harmless (the `secrets` module import, a URL-password check, test file names). `bridge.json`
  (token), `darkroom.local.toml`, logs, outputs and previews are git-ignored. Personal paths (not secrets) appear in
  PROGRESS.md (test folder, catalog file name).
- 310 tests pass, `make quality` clean.

User go (2026-11-21), order: 1 and 4, then 5 (clean-outputs), 3 (error cases one at a time), 6 (pip-audit, firewall off,
flags checked with --help first), 2 (firewall run last). Also asked for and built:
- `llm.require_loaded` (default true): `LocalLlmClient` asks the server's state list (LM Studio `/api/v0/models`) before
  every chat request and refuses with the new code `LLM_MODEL_NOT_LOADED` ("No request was sent") unless the model is
  reported as loaded; also when the state cannot be read or the model is not listed. So a request never makes the server
  load or swap a model. Used by `darkroom chat`, `darkroom models test` and `scripts/agent_run.py`.
- `doctor`: "model ... is loaded" / FAIL "listed but NOT loaded (state: ...)" / warn when the server reports no state.
- MCP: a `CONFIRMATION_REQUIRED` refusal now says that this interface cannot ask and points to `darkroom chat`.
- Human-run confirmations (`revert`, `clean-outputs`) say "This will: ..." instead of "The assistant wants to: ...".
- 314 tests pass, `make quality` clean.

Item 1 DONE (offline proof with Lightroom, `scripts/offline_proof.py --with-adobe --photo TEST_2459-39.jpg`): third-party
imports, model list, text model tests (4/4, 2/2), MCP server object, `darkroom doctor` (all ok), `get_photo_info` and
`analyze_photo` on the test photo: audit hook saw only 127.0.0.1 (8 connects, 12 lookups). PASS. Not covered by the hook:
child processes (the Photoshop worker); Photoshop was only checked through its window list here.

Item 4 DONE (MCP against the real Lightroom, `scripts/mcp_live.py`, real stdio client starting `darkroom mcp`): server
`darkroom_ai 0.1.0`; 8 tools listed (no `revert`, no `apply_preset`, no script runner); `get_photo_info` on
`TEST_2459-39.jpg` returned its working copy and all-zero sliders; a gated `adjust_lighting` (`allow_beyond_total`) was
refused with `[CONFIRMATION_REQUIRED]`; `revert` answered "There is no tool named 'revert'"; master settings unchanged.

Item 5 list shown (nothing removed; the user answers the y/N): 44 files, 807 MB would be removed (25 previews 11 MB,
4 hand-off TIFFs 390 MB, 3 masks 1 MB, 12 Photoshop working copies / masks 405 MB); 10 final PSD/JPG results kept.
Flagged: the 3 masks include `TEST_2457-37_subject.png`, the Select Subject mask whose visual check by the user is
still pending from the Phase 4 gate.

Item 5 DONE: the user ran `darkroom clean-outputs` and answered y; afterwards "Nothing to remove", 10 finals kept.

Item 3, live error cases (2026-11-21):
1. Lightroom closed (no Lightroom process): `darkroom doctor` -> `[FAIL] Lightroom: [LR_NOT_REACHABLE] Lightroom did not
   answer 'ping' in time (timed out)` + next step (open Lightroom, plug-in enabled, no dialog waiting); exit 1 after 21 s;
   the other checks still ran. `darkroom lightroom ping` -> same code and text, exit 2 after 21 s. Correct code and a
   usable next step. To improve in the message review: it waits the full 20 s connect deadline and says "did not answer in
   time" although nothing is listening; say "nothing is listening on the plug-in's port" when the connection is refused.
2. Plug-in DISABLED in the Plug-in Manager (correction from the user: the whole plug-in was disabled, not only the
   bridge stopped; so the variant "plug-in enabled but bridge stopped" was NOT tested separately; both variants end in
   "no listener" and should give the same message, unverified for the second). Lightroom open on the test catalog, no
   listener on ports 58763 / 58764: first run gave the
   same text as case 1 ("did not answer 'ping' in time"), whose next step never mentioned starting the bridge. FIXED and
   re-tested live: `[LR_NOT_REACHABLE] Nothing is listening on the Lightroom plug-in's local ports (58763 and 58764):
   Lightroom Classic is closed, or the darkroom_ai bridge is not started (waited 20 s).` Next step: open Lightroom with the
   test catalog, File > Plug-in Extras > "Start darkroom_ai Bridge", or enable the plug-in in the Plug-in Manager. This
   message now also covers case 1 (not re-run with Lightroom closed). "Did not answer in time" remains for a plug-in
   that listens but stays silent. Still waits the full 20 s connect deadline (kept: the plug-in needs time after a
   restart). Unit test added (315 tests).
3. Wrong catalog (bridge.json pointed at `D:\darkroom_test\DOES_NOT_EXIST\Dummy.lrcat`, backup outside the repo, restored
   in a `finally` block, backup deleted; target: the virtual copy of `TEST_2459-39.jpg`): all 8 checks PASSED. Ping reported
   the dummy as configured and writes NOT allowed; reads still worked; the no-op write was refused with
   `[LR_WRONG_CATALOG] Refused: the open catalog (...) is not the configured catalog (...)`, next step "Open the configured
   catalog in Lightroom, or change lightroom.test_catalog and run `darkroom lightroom setup` again. Nothing was changed.";
   the setting was unchanged; after the restore writes were allowed and the same no-op write succeeded.
   `darkroom lightroom ping` in that state: exit 0, "writes allowed: NO (different catalog open)" with both paths.
   `darkroom doctor` in that state: only "[warn] ... WRITES REFUSED (other catalog open)" without paths or next step.
   IMPROVED afterwards (not re-run live): doctor now prints FAIL with the code, both catalog paths and the next step.
4. Model unloaded (Qwen ejected in LM Studio, server running; GPU 0 at 1.9 GB): `darkroom doctor` -> `[FAIL] model of the
   active profile is listed but NOT loaded (state: not-loaded). Load it in the local model server; darkroom_ai does not
   load models by itself.` `darkroom chat ... --photo TEST_2461-41.jpg` -> `[LLM_MODEL_NOT_LOADED] 'qwen/qwen3.8-27b' is
   listed but not loaded (state: not-loaded). No request was sent.` + next step (load the model; `require_loaded = false`
   to allow on-demand loading); exit 2 after 1.1 s. Afterwards LM Studio still listed no loaded model and GPU memory was
   unchanged, so nothing was loaded on demand. Change asked for by the user and built first: chat (and
   `scripts/agent_run.py`) check the model BEFORE anything touches Lightroom; the plug-in log shows only doctor's two
   pings at that time and no command from the chat attempt. Unit test added (316 tests).
5. Photoshop with a dialog open (Preferences dialog on screen; one read-only `info` call; the check script never starts
   Photoshop): `[PS_NOT_REACHABLE] Photoshop is busy and rejected the call (likely a dialog is open).` after 0.3 s; next
   step: check that Photoshop is running and no dialog is waiting, then retry; details: reason `busy`, COM 0x8001010A.
   Nothing was opened or changed. A first attempt was INVALID and is not a result: the assistant's own check script lacked
   the `if __name__ == "__main__"` guard that the spawned worker process needs, so the worker crashed before reaching
   Photoshop and the 30 s watchdog answered instead (its text, for the record: "Photoshop is open but has not answered
   for 30 s. Open Photoshop windows: 'Preferences', 'Adobe Photoshop 2026'..."). To improve in the message review: the
   fast "busy" message could name the open dialog window, as the watchdog message does. Recovery after closing the
   dialog: DONE, the same call answered in 0.2 s (version 27.8.0, 0 open documents).
   Built afterwards: the fast "busy" error now names the open Photoshop windows ("Open Photoshop windows: 'Preferences',
   'Adobe Photoshop 2026'. Likely one of them is a dialog that is waiting.") with the next step "Bring Photoshop to the
   front, finish or close that dialog, then retry." Unit-tested (317 tests); NOT re-run live with a dialog open.

Item 6 DONE, pip-audit (2026-11-21, one-off, approved by the user, no firewall block active):
`poetry run pip-audit --progress-spinner off --desc on --output logs\pip_audit_20261121.txt` (flags checked first with the
offline `--help`, pip-audit 2.10.1). Result: "No known vulnerabilities found", exit 0, for the 74 packages installed in
`.venv` (main + dev; the `ml` extra is not installed and so was not audited). pip-audit wrote no file because it had
nothing to report; the assistant wrote the outcome to `logs\pip_audit_20261121.txt` (git-ignored). No dependency changed.

Item 2 DONE, firewall run (2026-11-21): the user added the 3 outbound block rules (real `python3.13.exe`, its alias, the
project's `.venv\Scripts\python.exe`) in an elevated PowerShell and confirmed the block works (their example.com test
failed with WinError 10013). The assistant saw the 3 rules enabled before and after. Under the block, everything worked
and no error mentioned a network:
- `darkroom doctor`: all checks passed (Lightroom 15.2.1 / plug-in 0.3.0, Photoshop running, Qwen loaded on GPU 0).
- `scripts/offline_proof.py --with-adobe --photo TEST_2459-39.jpg`: text model tests 4/4 and 2/2, MCP server object,
  doctor, `get_photo_info` + `analyze_photo`; audit hook saw only 127.0.0.1 (8 connects, 12 lookups). PASS.
- Agent run, lighting only, on the virtual copy of `TEST_2459-39.jpg`: highlights -20 (3.3 s + 2.5 s model time, 7.1 s),
  then undo; all 171 settings equal the start state; master and SHA-256 unchanged; ALL CHECKS PASSED.
- Select Subject on a copy of `TEST_2457-37.jpg` (Photoshop worker child process, Device mode): 7.2 s, coverage 69.937 %
  (same as the baseline), all checks passed. New mask `outputs\masks\TEST_2457-37_subject.png`.
What this proves: darkroom_ai's own Python processes (including the Photoshop worker child) need no connection other
than loopback. What it does NOT prove: anything about Lightroom, Photoshop or LM Studio themselves, which were not
blocked. Rules REMOVED by the user afterwards (elevated `Remove-NetFirewallRule`); the assistant checked read-only that
0 rules named `darkroom_ai offline proof*` remain.

Remaining Phase 7 items (2026-11-21): the message review and the README are shown to the user as DRAFTS and are not
committed until approved (README draft outside the repository; `README.md` unchanged). Already done earlier in this
phase: the throwaway-venv install check and the `replace_background` note. `clean-outputs` would now remove 4 files
(3 previews, 1 mask from the firewall run); left for the user to run.

Message review APPROVED by the user (all seven changes) and applied (2026-11-21):
1. `TOOL_TIMEOUT` removed (nothing raised it; timeouts surface as LR_/PS_/LLM_NOT_REACHABLE). PRD.md and CLAUDE.md do not
   list it, so neither file was changed.
2. `ADOBE_FEATURE_UNAVAILABLE` kept as reserved; nothing raises it yet (noted in `errors.py`).
3. `LR_NOT_REACHABLE` default next step now mentions File > Plug-in Extras > "Start darkroom_ai Bridge".
4. `GUARDRAIL_REJECTED` default is neutral ("Read the message above; nothing was changed."); the seven refusals that used
   the default got their own next step (unknown playbook, colour values, JPEG quality, three "never overwrite" cases,
   "no change requested", duplicate HSL channel).
5. `CONFIRMATION_REQUIRED` default message: "This action needs your explicit go-ahead."
6. `MASK_MODEL_MISSING` next step adds "Nothing is downloaded automatically."
7. `doctor` warns (does not refuse) when Lightroom is not a 15.2.x version.
A test for item 7 caught a bug in the first version of the patch (the warning sat between an `if` and its `else`, so
doctor printed both "writes allowed" and the wrong-catalog failure); fixed before commit. 318 tests, `make quality` clean.

README: the approved draft with the user's additions ("Model setup": context about 32768, choose the GPU in the model
server, load the model yourself; `glimmer` and `fast` listed as untested; placeholder paths, no personal names) is in
the repository as `README.new.md`, UNTRACKED and NOT committed. `README.md` is unchanged until the user says go.

### Phase 7 gate report (2026-11-21)
Done and verified live:
- Offline proof: audit hook over third-party imports, model tests, MCP, doctor and Lightroom read tools: only 127.0.0.1.
  Firewall run with outbound traffic of all three Python program paths blocked (block confirmed effective by the user):
  doctor, offline proof, an agent run with undo, and Select Subject all worked; no network error. Rules removed (count 0).
- Live error cases: Lightroom closed; plug-in disabled; wrong catalog; model unloaded; Photoshop dialog open (and recovery).
  Three messages were improved as a result (no listener, doctor's catalog mismatch, Photoshop busy names the windows).
- MCP over real stdio: simulated Lightroom (in the test suite) and the real Lightroom (list, read, gated call refused).
- `llm.require_loaded`: no request unless the model is reported loaded; chat checks it before touching Lightroom.
- `darkroom clean-outputs` run by the user (44 files removed, finals kept). `darkroom revert` run by the user earlier.
- pip-audit (one-off): no known vulnerabilities in the 74 installed packages; the `ml` extra is not installed/audited.
- Throwaway-venv install check (`--no-deps`): entry point registered; folder deleted.
- Tracked files that could hold secrets: none found.
Unit-tested only: the improved Photoshop "busy" text; doctor's catalog-mismatch FAIL line and Lightroom version warning;
"plug-in enabled but bridge stopped"; clean-outputs rules for unusual file names; the reworded guardrail next steps.
Not proven: that Lightroom, Photoshop or LM Studio themselves make no connections (they were never blocked); asyncio's
own connect path is not seen by the audit hook; the error codes that only unit tests trigger (11 of 20).
Open / carried to Phase 8: `glimmer` and `fast` profiles; Select Subject with two models loaded; RAW acceptance test;
`list_presets` + live `apply_preset`; ONNX mask backend and YuNet (deferred by the user); playbook ranges untuned;
a gated action confirmed with a real "y" in chat; HSL / crop / landscape playbook through the agent.
README approved by the user with three edits (the intro says darkroom_ai itself makes no internet connections and that
Lightroom, Photoshop and the model server are separate programs; `make install` / `make audit` use the network; a
different Lightroom version gives a doctor warning) and committed as `README.md`; `README.new.md` removed.
Phase 7 gate ACCEPTED. Before any push: the user wants the tracked-files secrets list, `git remote -v` and the
repository's visibility, and decides when to push. Phase 8 plan shown; waiting for the go before touching Lightroom
or Photoshop.

Still waiting: error cases 2-5; pip-audit; the firewall run. Earlier list:
offline proof with Lightroom / Photoshop and under the firewall block; the five live error
cases; MCP against the real Lightroom; `clean-outputs` on the real folder; pip-audit (command shown first). Then: review
of every error message, README, gate.

Phase 7: `darkroom chat`, `darkroom revert` and MCP over real stdio are
unit-tested only (the user will
confirm LM Studio's auto-unload or switch models by hand).

Earlier note (done): a downscaled preview of a TEST_ photo to the local model with a numeric comparison of its
brightness statement, and the GPU conflict measurement with Photoshop running. Still owed: profiles `glimmer` and `fast`.

Earlier note (done): on the user's go, send the synthetic test picture (512 x 384 JPEG, 10 kB: red square, blue circle, "47") to
`http://127.0.0.1:1234/v1/chat/completions`; then the other profiles; then the first agent run.

### Catalog facts (corrects earlier counts in this file)
- The user imported the test copies by hand on 2026-11-21. They are in the catalog as `D:\darkroom_test\TEST_2457-37.jpg` ...
  `TEST_2467-47.jpg` (same SHA-256 as the `DSC_...` copies, which are in the catalog too).
- The catalog had 79 photos at that point (user's count; the catalog database read-only shows 78 files + 1 virtual copy).
  After the hand-off run it has 80 (one more virtual copy). Earlier counts in this file (62, 67, 68, 72) were snapshots of
  earlier states and are stale.
- By folder: 67 files in `D:\Photos\Gallery\101D7500_Processed` (off limits), 10 in `D:\darkroom_test`, and 1 stray file in
  `D:\darkroom_test\lr_sdk\API Reference` (an SDK picture that came in with the import; not used).

### Hand-off spike result (2026-11-21 07:43, on a virtual copy of `TEST_2457-37.jpg`)
- Selftest: plug-in build 0.2.0; `list_commands` shows `export_tiff` among the write commands.
- Lightroom: virtual copy `darkroom handoff 20261121-074305`; `export_tiff` took 0.7 s; TIFF header 16/16/16 bits per sample,
  5568 x 3712, embedded profile "sRGB IEC61966-2.1" (sRGB was requested), 124.0 MB (uncompressed), written to
  `outputs\handoff`. So Lightroom does write a 16-bit TIFF from a JPG source.
- Guards verified LIVE in Lightroom: export from the master refused (`NOT_A_VIRTUAL_COPY`); a second export with the same name
  refused (`FILE_EXISTS`); a develop edit on the master refused (`NOT_A_VIRTUAL_COPY`); earlier, a write on a gallery photo
  refused (`PHOTO_OUTSIDE_ROOT`).
- Develop edit on the virtual copy: `Exposure2012` 0 -> 0.3 -> 0 (reverted from the recorded value); the master stayed at 0.
- Photoshop 27.8.0: opened a COPY of the TIFF (16 bit), layer mask from the PNG, fill layer below, new PSD + flattened JPG,
  closed without saving. Numeric checks passed (centre unchanged 85.8/130.1/123.2; corner 200/30/31 vs fill 200/30/30).
- Original `TEST_2457-37.jpg`: same size, modified time and SHA-256 (`F8BFDC77...46F8`) before the run, after the export and
  after the edit + revert; no `.xmp` sidecar.

### Wrong-catalog refusal test — DONE live (2026-11-21 08:00), PASSED
- `bridge.json` backed up outside the repo, pointed at `D:\darkroom_test\DOES_NOT_EXIST\Dummy.lrcat`, restored in a `finally`
  block (byte-identical afterwards, backup deleted).
- With the dummy path: `ping` reported the dummy as configured and writes NOT allowed; reads still worked; a no-op write
  (`Exposure2012` set to its current value on the `TEST_2457-37.jpg` virtual copy) was refused with `LR_WRONG_CATALOG`; the
  setting was unchanged. After the restore: writes allowed again and the same no-op write succeeded.
- The plug-in picks up `bridge.json` when the next client connects (no reload needed).
- Client bug found by the first attempt of this test and fixed: a second client object created right after another one closed
  (same process) connected at once, got one socket of the plug-in's old session and one of the new (the two ports come back
  about 0.5 s apart), and a fixed 1 s retry never recovered. Now the last-disconnect time is shared process-wide, slow connects
  are abandoned, retries back off (1.0 / 1.5 / 2.2 / 3.0 x), and connecting has its own deadline (`lightroom.connect_timeout_s`,
  20 s) separate from the command timeout. Verified against Lightroom: back-to-back clients 6/6.

### Open threads
- Rules in force: edits only on virtual copies of photos under `D:\darkroom_test`; gallery photos are off limits; Photoshop
  works only on copies inside `outputs\photoshop`; no add-photos command (user decision).
- Not tested: which TIFF compression key spelling Lightroom honours (the file is uncompressed, as intended, but both
  spellings were sent); AdobeRGB / ProPhotoRGB export; RAW files; selection restore when nothing was selected; a real
  Photoshop start/sign-in screen (unit tests only).
- Left behind: test catalog has virtual copies `darkroom handoff 20261121-074305` (of `TEST_2457-37.jpg`, settings back to
  original) and `darkroom 20261120-233520` (of a gallery photo, from the Phase 2 spike) with snapshot `darkroom-before`.
  `outputs\` holds the spike files (git-ignored). Lightroom and Photoshop were left running.

### Imaging toolkit (built 2026-11-21, no app contact)
- `imaging/io.py`: JPG/PNG/8- and 16-bit TIFF via OpenCV, RAW via rawpy; mask PNG save (never overwrites) and load.
- `imaging/analysis.py`: luma percentiles + 32-bin histogram, highlight/shadow clipping (overall and per channel), colour cast
  from near-neutral mid-tone pixels (blue-yellow and green-magenta axes) with "low" confidence when under 1% of the pixels
  are near-neutral.
- `imaging/faces.py`: `FaceDetector` Protocol, `YuNetFaceDetector` (OpenCV `FaceDetectorYN`, user-provided model file). Without
  a model: `FACE_MODEL_MISSING`; there is NO colour-based fallback (user decision). OpenCV 5.1 has no bundled Haar detector.
- `imaging/masks.py`: `MaskProvider` Protocol (image file -> same-size grayscale PNG + metrics). Backends: `SyntheticMaskProvider`
  (ellipse, tests/dry runs) and `OnnxMaskProvider` (user-provided model, GPU 1, no silent CPU fall-back, `MASK_MODEL_MISSING`
  without a model or without onnxruntime). `mask_metrics`: coverage %, soft-edge %, region count, speck count, largest region,
  and edge raggedness (compactness, hull-perimeter ratio, solidity).
- `imaging/providers.py` + `[masks]` / `[faces]` settings: `masks.backend = photoshop | onnx | synthetic` (default photoshop).
- New error codes: `MASK_FAILED`, `FACE_MODEL_MISSING`, `IMAGE_UNREADABLE`.
- Tests on synthetic images with known answers; 220 tests pass with the Lua syntax check.
- Unverified: the YuNet wrapper and real ONNX inference (no model files yet); CUDA 13 loading and GPU placement on the 3090
  (`make install-ml` has not been run - large download, ask first).

### Select Subject spike (2026-11-21 08:20, on a COPY of `TEST_2457-37.jpg`) — PASSED
- How the mode is exposed (read-only inspection of the application descriptor, 600 keys walked):
  `imageProcessingPrefs / imageProcessingSelectSubjectPrefs`, an enumeration; current value `imageProcessingModeDevice`.
  This is Preferences > Image Processing > "Select Subject and Remove Background".
- Guard: the provider reads that value and refuses unless it is exactly `imageProcessingModeDevice` (nothing is opened); the
  JSX re-reads it immediately before the `autoCutout` call and throws otherwise. Any other value, including unknown ones, is
  refused (`GUARDRAIL_REJECTED`). No per-call "cloud" key is sent.
- Evidence that the run was on-device: mode Device before and after; Photoshop had 0 established non-local TCP connections
  before and after; system-wide bytes sent during the 11 s run were 152 KB against an idle baseline of 137 KB per 10 s (the
  photo alone is 2.6 MB as a JPG). This is strong supporting evidence, not a packet capture.
- Run: copy -> `outputs\photoshop\TEST_2457-37_copy.jpg`, opened, Select Subject (`autoCutout`, `sampleAllLayers = false`),
  selection stored in a channel and painted white on a black layer, PNG saved as a copy, document closed WITHOUT saving.
  8.1 s end to end. Final mask: `outputs\masks\TEST_2457-37_subject.png` (single-channel PNG, never overwrites).
- Mask metrics: 5568 x 3712 (same as the photo); coverage 69.937 %; 1 region plus 4 specks; largest region 69.937 %;
  soft-edge pixels 0.383 %; edge raggedness: compactness 3.978, hull-perimeter ratio 1.701, solidity 0.848.
- Mask PNG viewed (mask only, downscaled; the user allowed this and nothing else): one connected silhouette of a group of six
  standing people with clean outlines; a small notch between two figures in the lower left-centre; a slightly soft patch at
  the right-most head. The original photo, overlays and composites were NOT viewed; the user compares those.
- Original `TEST_2457-37.jpg` byte-identical afterwards (SHA-256 `F8BFDC77...46F8`); document count in Photoshop unchanged.
- Note: the intermediate PNG that Photoshop writes (`..._copy_select_subject.png`) is 62 MB (RGB, barely compressed); the
  final mask is re-saved as a small single-channel PNG. The intermediate file is left in `outputs\photoshop`.
- 4 specks: tiny disconnected regions in the mask; a clean-up step (drop regions under a size threshold) is not implemented.

### Phase 4 gate (user, 2026-11-21)
- Accepted, pending the user's own visual check of the mask (`outputs\masks\TEST_2457-37_subject.png`).
- DEFERRED until the user says so: ONNX mask backend run and the YuNet face detector. Do NOT run `make install-ml` and do
  not download anything.

### Phase 4 backlog (user request)
- Speck clean-up: drop mask regions under a size threshold (the Select Subject mask had 4 specks).
- Delete the large intermediate PNG that Photoshop writes into `outputs\photoshop` (62 MB) after each run, once the final
  mask has been saved.

### Phase 5 - tool layer + guardrails (built 2026-11-21, no app contact)
User decisions: no import of results into the catalog; NO `run_photoshop_script` in v1 (no arbitrary script runner; add a
narrow typed tool if a gap appears); backgrounds = solid colour or a picture from ONE configured folder; limits accepted with a
total cap against the original and separate JPG / RAW white-balance scales; clamped results report requested vs applied.

- Nine tools in one registry (`tools/registry.py`), each with a pydantic input model that gives the JSON schema:
  `get_photo_info`, `analyze_photo`, `adjust_lighting`, `adjust_white_balance`, `adjust_hsl`, `crop`, `apply_preset`,
  `replace_background`, `undo`. Unknown fields and bad values -> `LLM_BAD_TOOL_CALL`. Every call writes one journal entry
  (also failures).
- Virtual copies only: an edit on a master creates ONE virtual copy and reuses it (also across sessions, via the journal).
  The plug-in refuses develop edits on masters as a second line.
- Limits (`safety/guardrails.py`, `[guardrails]`): per pass (exposure 1.5 EV, tone 50, HSL 30, JPG white balance 30 on the
  -100..100 scale, RAW 1000 K / tint 30 - RAW UNTESTED), total against the ORIGINAL value (exposure 3 EV; the original is the
  first "before" value in the journal), slider range. Values are clamped, and every change reports requested, applied,
  before, after, original and which limit applied. Going beyond the total needs `allow_beyond_total` AND a Confirmer saying
  yes; the default Confirmer refuses, the model cannot confirm for itself.
- White balance: JPG/TIFF -> `IncrementalTemperature` / `IncrementalTint`; RAW/DNG -> `Temperature` (Kelvin) / `Tint`.
- `crop`: box as fractions + angle; refused if it keeps less than 25% of the frame. Setting names UNVERIFIED in Lightroom.
- `apply_preset`: by exact name; plug-in commands `list_presets` / `apply_preset` (build 0.3.0) report every changed setting
  before/after; a preset that breaks the total limit is taken back and refused. UNVERIFIED in Lightroom.
- `replace_background`: virtual copy -> 16-bit TIFF -> mask provider -> Photoshop mask + background -> new PSD + JPG in
  `outputs\photoshop`; no import. Background picture: plain file name in `[backgrounds] dir` only (no path, no URL, real
  image, size limit); Photoshop gets a copy of it. New JSX `add_image_layer_below` UNVERIFIED in Photoshop.
- `undo`: last step or all, by re-applying the journaled "before" values.
- Phase 4 backlog done here: speck clean-up (`remove_specks`) in the Select Subject provider, and the 62 MB intermediate PNG
  plus the provider's working copy are deleted after the final mask is saved.
- `tools/factory.py` wires the real adapters; `scripts/tools_live.py` is the live run (not executed yet).

### Phase 5 live run (2026-11-21 08:49, virtual copy of `TEST_2459-39.jpg`) - ALL CHECKS PASSED
- Selftest first: plug-in build 0.3.0; Photoshop Select Subject mode Device.
- Tools exposed: `get_photo_info`, `analyze_photo`, `adjust_lighting`, `adjust_white_balance`, `adjust_hsl`, `crop`,
  `replace_background`, `undo` (8). `apply_preset` is NOT exposed (user decision) and was not called.
- Results:
  - `analyze_photo`: mean luma 0.5344; highlights clipped 1.245 % (red channel 1.239 %), shadows 0.566 %; no noticeable cast.
  - `adjust_lighting` +0.4 EV / shadows +20: created ONE virtual copy; mean luma 0.5344 -> 0.6045.
  - Clamp: requested +2.5 EV, applied +1.5 (`per_pass`); before 0.4, after 1.9, original 0.0. This was the only clamp.
  - `adjust_white_balance` +15 -> `IncrementalTemperature` 0 -> 15 (JPG relative slider). `adjust_hsl` orange saturation -10.
  - `crop` 5 % off each side: `CropLeft/Top` 0.05, `CropRight/Bottom` 0.95 read back - the crop setting names are now
    VERIFIED in Lightroom. A crop keeping 4 % of the frame was refused.
  - `replace_background` (colour 30/60/120): 14.7 s; mask by `photoshop-select-subject`, 1 region, 0 specks, coverage 53.1 %;
    new PSD (152.5 MB) and JPG (3.3 MB) in `outputs\photoshop`; top-left corner of the JPG measures exactly 30/60/120.
    A background path outside the folder (`..\outside.jpg`) was refused.
  - `undo` (all): 5 steps undone; the copy is back at its starting values; the master's settings never changed; original
    JPG byte-identical (SHA-256 `5BD88948...6CF2`, same modified time, no `.xmp` sidecar).
- Differences from the plan / things to know:
  - `replace_background` exports the working copy WITH its current edits. At that point the copy had the 5 % crop, +1.9 EV
    and the other edits, so the TIFF, mask and outputs are 5011 x 3341 (not 5568 x 3712) and carry those edits.
  - The 16-bit TIFF from Lightroom (95.8 MB) stays in `outputs\photoshop` as the Photoshop working copy, and a second copy
    stays in `outputs\handoff`; only the mask intermediate is deleted. Disk use per run is roughly 350 MB.
  - Not tested live: the picture-background path (`add_image_layer_below`; the user gave no file name), RAW white balance,
    `allow_beyond_total` with a real confirmation, `apply_preset`.
- Left behind: virtual copy `darkroom_ai 20261121-0849..` of `TEST_2459-39.jpg` (settings back at the start values), 13
  journal lines in `logs\journal.jsonl` (git-ignored), previews and outputs (git-ignored).

### Test material and catalog (user, 2026-11-21)
- Three RAW test files are in the catalog under the test root: `D:\darkroom_test\raw\RAW_DSC00480.ARW`, `RAW_DSC00481.ARW`,
  `RAW_DSC00482.ARW` (Sony ARW). They are copies; the originals are in `D:\DCIM`, which is OFF LIMITS.
- Catalog now: 84 images = 67 gallery (off limits) + 10 test JPG copies + 1 stray SDK picture + 3 RAW + 3 virtual copies
  (user's count). Earlier counts in this file are stale.
- No action has been taken on the RAW files yet.

### Phase 5 gate (user, 2026-11-21)
Accepted, pending the user's own visual checks of the mask and of the background JPG.

### Backlog (user requests)
- RAW acceptance test on the three ARW test files: white balance `Temperature` in Kelvin, export to 16-bit TIFF,
  highlight/shadow limits on RAW, and the rule "RAW only when the file is RAW or the user says so".
- A clean-outputs command that ASKS FIRST and never removes finals (final PSD/JPG results).
- `replace_background` must state in its result that the output includes the copy's current edits and crop.
- Presets, AFTER this live run: (1) a read-only `list_presets` tool (preset folders and names); (2) a live test of
  `apply_preset` with a name from that list, checking that undo restores EVERY setting the preset changed; (3) design question
  to raise then: presets can exceed the per-pass limits - propose how they interact with guardrails and confirmation.
  `apply_preset` stays disabled for the LLM until then.
- Deferred until the user says so: ONNX mask backend, YuNet face detector (`make install-ml` not run; nothing downloaded).

### Next step
Phase 6 - LLM layer + agent. The plan has been shown; wait for the user's go. Rules from the user: LM Studio endpoint
127.0.0.1 only; images go only to the local model; Claude does NOT view the user's photos (numeric checks or the grayscale
mask only); nothing is downloaded; no `make install-ml` without asking. The plan includes the Qwen vision test, a tool-calling
test per profile, and detection of a GPU conflict between LM Studio and Photoshop. Do not push `dev`.

## Phase 0 — Scaffold (2026-11-20) — DONE, waiting at GATE

### Done
- Branch `dev` created from `master`; Poetry 2.2.1 project (`pyproject.toml`, PEP 621, src layout, `requires-python >=3.13,<3.15`),
  using the existing in-project `.venv` (Python 3.13.14); `poetry.lock` committed.
- `poetry check`, `poetry lock`, `poetry install --with dev`, `poetry run pip check` all clean (76 packages installed; ml extra NOT installed yet).
- Makefile (single-command recipes) + `scripts/make_help.py`, `scripts/clean.py`. Ran: help, quality, doctor, offline-check, cov, build, clean-all.
- `.gitignore` extended (build output, logs, outputs, previews, models, `*.onnx`, `*.gguf`, `test_catalog/`, `*.lrcat*`, `*.lrdata/`, `darkroom.local.toml`).
- Code: `errors.py` (PRD s8 codes + 6 internal codes), `net/loopback.py` (only module allowed to open connections),
  `config.py` (TOML loader, rejects non-local `base_url` and Lightroom host), `safety/journal.py` (append-only JSONL),
  `interfaces.py` (`ActionJournal` Protocol), `cli.py` (`--version`, config-only `doctor`), example `darkroom.toml`.
- Tests: 121 passing, 99% coverage. Offline guard blocks non-loopback `connect`, `connect_ex`, UDP `sendto`, name lookups and
  asyncio (IOCP) connections; AST scan forbids network/cloud/telemetry imports outside `net/loopback.py`; scan forbids model names
  and `0.0.0.0` in `src/`.

### Decisions
- HTTP client for the LLM: httpx2 behind `net/loopback.py` (PRD s10 to be reworded to "no non-loopback traffic").
- `mcp` stays in core, stdio transport only. scikit-image is not a direct dependency.
- Packaging per the user's amendment: Poetry, groups main / win32-marked (pywin32, comtypes, photoshop-python-api) / extra `ml` / dev.
- Dependency injection: collaborators are passed through constructors against Protocols in `interfaces.py`; fakes live in `tests/fakes.py`.
  Protocols for Lightroom, Photoshop, imaging and the LLM are added in the phase that first verifies their real behavior.
- Direct dependencies are pinned exactly in `pyproject.toml`; `poetry.lock` pins the rest with hashes.
- Git: commit only to `dev`, never push without the user's say-so, check staged files before every commit.

### Follow-up after the gate (2026-11-20)
- CLAUDE.md and PRD.md updated as approved (Poetry, Makefile, Python `>=3.13,<3.15`, PRD s10 wording, scikit-image, git rules).
- `.gitattributes` (`* text=auto`) added; `pytest-timeout` 2.4.0 added to dev (60 s per test).
- `rembg` removed from the `ml` extra (user decision): masks use onnxruntime directly on user-provided ONNX files; no automatic
  model downloads. To be confirmed again in the Phase 4 plan. `requests` remains in the lock only through pip-audit (dev).

### Open threads
- `mcp` pulls `opentelemetry-api` (interface only); Phase 7 must prove it and uvicorn open no connection.
- Phase 6 httpx2 client factory: `trust_env=False`, no proxy mounts, tested with `HTTP_PROXY` set (user requirement).
- `vision` / `tool_mode` values in `darkroom.toml` are unverified starting values (Phase 6).
- Unverified: Python 3.14 (only 3.13 was run); the ml extra install and CUDA 13 loading (Phase 4); everything involving Adobe apps or a model.

## Phase 1 — lightroom-cli source review (2026-11-20) — DONE, waiting at GATE

Read-only review; nothing from it was imported, run, built or installed. Files live in a temp folder outside the project.

### What was reviewed
- PyPI `lightroom-cli` 1.2.2: sdist sha256 `fbcd1aee…9a2b`, wheel sha256 `417d6ef0…d1b5` (both match PyPI's digests).
- Git tag `v1.2.2` = commit `a4fc4f65a14ef3fc8e7b495b11a795c94020b19e` (2026-04-06).
- sdist vs tag: identical content for every shared file (differences were line endings from my checkout only). The tag additionally has
  CI workflows, docs, a Claude plugin/skill folder, version scripts and e2e/integration tests. Wheel files are identical to the sdist.

### Findings
- **Lua plugin network**: only `LrSocket.bind` with `address = "localhost"`, two fixed ports (54322 send, 54323 receive). No `LrHttp`,
  no process execution (`LrTasks.execute`, `LrShell`, `os.execute`), no `loadstring`/`dofile`. Whether "localhost" really binds
  loopback-only is unverified until Phase 2.
- **Lua plugin files**: writes/deletes one port file `lightroom_ports.txt` in the temp folder; logs through `LrLogger` at debug level,
  including every command's parameters (file paths, metadata) in Lightroom's log folder.
- **No authentication**: any local process can connect and call ~150 commands, including `catalog.removeFromCatalog`, with no check of
  which catalog is open (no command returns the catalog path).
- **Python side**: `asyncio.open_connection` to host `localhost` (host is a parameter, so not forced local); no HTTP client, no
  subprocess, no telemetry. `lr plugin install` deletes and replaces the plugin folder under `%APPDATA%\Adobe\Lightroom\Modules`;
  `lr mcp install` edits Claude Desktop's config file.
- **Dependencies**: click, rich, pydantic, platformdirs, fastmcp (a large MCP/web stack we do not otherwise need). Top-level
  package names are generic (`cli`, `mcp_server`).
- **Fit for our needs** (from reading; unverified in Lightroom):
  - Develop edits go through `LrDevelopController` (acts on the active photo in the Develop module and changes the selection);
    `getSettings` refuses photos that are not RAW/DNG or a virtual copy, which conflicts with our JPG default.
  - No snapshot apply/revert or snapshot listing; `createVirtualCopy` calls `photo:createVirtualCopy()`, which I could not confirm in the SDK.
  - Previews come from `requestJpegThumbnail` as base64 over the socket (not an export to file; may lag behind edits).
  - Hand-written JSON: the encoder does not escape backslashes or control characters (Windows paths would produce invalid JSON);
    the decoder does not unescape strings and does not handle nested arrays.
- **Windows**: its changelog says Windows support is "not yet tested on real hardware". Plugin `Info.lua` still reports 1.2.1.
- **License**: MIT (LICENSE file, copyright "motokiendo"; PyPI author "M.Endo").

### Recommendation
Write our own small plugin (ping, catalog info, selected photo, virtual copy, snapshot create/apply, set/get Develop settings, export
preview to file), reusing lightroom-cli's two-socket `LrSocket` pattern as reference with MIT attribution. Do not install lightroom-cli.

### Decision (user, 2026-11-20)
Approved: write our OWN Lightroom plugin; lightroom-cli is not installed. PRD 5.1 and the matching open question in PRD 12 updated.

### Open threads for Phase 2
- Confirm with `Get-NetTCPConnection` that `LrSocket.bind` listens on loopback only.
- Add what lightroom-cli lacks: catalog-path check before any write, a shared token per request, proper JSON escaping.
- Find out which Develop operations work without the Develop module (`photo:applyDevelopSettings` vs `LrDevelopController`).

### Next step
Phase 2 (below).

## Phase 2 — Lightroom spike (2026-11-20) — DONE (one item failed: snapshot apply), waiting at GATE

### Done (no Lightroom interaction)
- SDK reference: user-provided Lightroom Classic 15.2 SDK at `D:\darkroom_test\lr_sdk` (API Reference HTML + SDK Guide PDF). Read for
  every call used. Confirmed there: snapshots exist (`createDevelopSnapshot`, `getDevelopSnapshots`, `applyDevelopSnapshot`);
  `LrSocket.bind` listens on localhost only (no address parameter); `catalog:createVirtualCopies` works on the selection only;
  `catalog:findPhotoByUuid` is the documented lookup (`getPhotoByLocalId` is not documented).
- Plug-in `src/darkroom_ai/adapters/lightroom/plugin/darkroom.lrplugin` (Info, Init, Stop, Shutdown, Bridge, Commands, Json):
  per-request token, write refusal unless the open catalog equals `catalog_path` in `bridge.json`, photos addressed by UUID,
  LrPhoto settings calls, previews exported to files in the configured preview folder only, one request at a time, no
  parameters or token in the log. All files pass the SDK's `luac -p` syntax check (Lua 5.1.5).
- Python: `LightroomClient` (deadline on every call -> `LR_NOT_REACHABLE`), `bridge_file.py`, result models, `LightroomAdapter`
  Protocol, `darkroom lightroom setup|ping`, error codes `LR_WRONG_CATALOG` and `LR_COMMAND_FAILED`.
- Tests: fake plug-in server speaking the same protocol; 160 tests pass; `make quality` clean.
- `scripts/lightroom_spike.py`: read-only by default, edit steps only with `--write`.

### Decisions
- Photo ID = the photo's UUID (documented lookup). The catalog's numeric local ID is reported but not used for addressing.
- Virtual copy: the plug-in saves the selection, selects only the target photo, verifies that, creates the copy, restores the
  selection; if it cannot select exactly the target it refuses and creates nothing.
- `bridge.json` lives in the plug-in folder (git-ignored) and is re-read whenever a client connects.
- Preview export counts as a write (refused on the wrong catalog) and never overwrites: Lightroom renames on collision.

### Open threads
- RESOLVED (user, 2026-11-20): the TEST catalog is
  `D:\darkroom_test\photos\D7500UMNSG_NSGTH(En)07.pdf\D7500UMNSG_NSGTH(En)07.pdf.lrcat` (the earlier `catalog\DarkroomTest.lrcat`
  path does not exist). The five JPGs in `D:\darkroom_test` are copies but are NOT imported into the catalog (checked by file
  path on 2026-11-20). `darkroom.local.toml` and `bridge.json` are written (both git-ignored).
- Read-only spike run in Lightroom 15.2.1 (2026-11-20 23:0x) — PASSED:
  - Loopback only: `Get-NetTCPConnection` shows both ports on `127.0.0.1`, owned by `Lightroom` (no other address).
  - `ping` (catalog path = configured path, writes allowed), JSON round trip inside Lightroom (backslashes, quotes, unicode,
    nested arrays), wrong token rejected (`UNAUTHORIZED`), selection read by UUID, Develop settings read on a JPG.
  - `probe`: every planned LrPhoto/LrCatalog method exists (snapshots create/list/apply/delete, `applyDevelopSettings`,
    `createVirtualCopies`, `findPhotoByUuid`; the undocumented `getPhotoByLocalId` exists too but is not used).
- Three bugs found and fixed while getting there:
  1. Lightroom's Lua raises an error on reading an undefined global -> the plug-in never started. Globals are read under `pcall`.
  2. "Reload Plug-in": the old instance's shutdown stopped the new run loop. Run loops are now generation-based.
  3. Reconnecting within ~0.3 s of a disconnect hits sockets the plug-in is still closing (measured: 1 s is reliable).
     The client now waits 1 s after a disconnect and proves the link with a `ping` before sending a real command.
- Two more plug-in bugs found and fixed (verified in Lightroom at 23:22):
  4. "Reload Plug-in" creates a NEW global environment and leaves the old run loop alive (SDK Guide p.32 confirms the new
     environment). Run loops now stop when a newer instance id appears in the shared plug-in preferences (`LrPrefs`).
  5. `LrForceInitPlugin` only runs the init script at launch if the plug-in contributes a menu item (SDK Guide p.23). Added
     File > Plug-in Extras > Start/Stop. The plug-in now starts by itself when Lightroom starts.
- Safety additions: `lightroom.test_photos_dir` (the spike refuses edit steps for photos outside it) and a `find_photo` read
  command (look-up by file path, so the selection is not needed).
- WAITING (edit steps NOT run): the five test copies in `D:\darkroom_test` are NOT in the catalog (`find_photo` returns nothing
  for all five; a known catalog photo is found, so the look-up works). The user has to import them with "Add". The photo that
  was selected earlier, `D:\Photos\Gallery\101D7500_Processed\DSC_3533_final.jpg`, is not a test copy and will not be edited.
- Plug-in log location on this machine: `%LOCALAPPDATA%\Adobe\Lightroom\Logs\LrClassicLogs\darkroom_ai.log`.
- `test_lua_syntax` runs only when `DARKROOM_LUAC` points at the SDK's `luac.exe`.

### Spike result (run in Lightroom 15.2.1, 2026-11-20 23:35-23:40) — waiting at GATE
The user explicitly approved using `D:\Photos\Gallery\101D7500_Processed\DSC_3533_final.jpg` (the five test copies were never
imported). All writes went to a virtual copy of it.

Verified (with evidence from the run):
- Virtual copy created by photo ID; the plug-in had to switch the grid to "All Photographs" because the photo was not in the
  current view (a photo that is not shown cannot be selected), then switched back.
- Snapshot `darkroom-before` created (`createDevelopSnapshot` in a write gate).
- Develop settings applied to a JPG virtual copy with `photo:applyDevelopSettings` from the Library module (no Develop module
  needed): `Exposure2012` 0 -> 0.30, read back; the original stayed at 0.
- Preview export to a file via `LrExportSession` + `renditions()` + `waitForRender()`: JPEG, long edge <= 1024 px, in `previews/`.
- The edit is visible: mean brightness 97.75 -> 108.34; after undo exactly 97.75.
- Undo by re-applying the journaled "before" settings works.
- The original file is byte-identical after all runs (same size, mtime and SHA-256; no `.xmp` sidecar).
- Loopback only: both ports on `127.0.0.1`, owned by Lightroom; no other local address.
- Wrong token rejected; unknown photo ID refused; wrong-catalog refusal is covered by unit tests against the fake only
  (NOT exercised in Lightroom - it needs bridge.json pointed at a dummy path).

Failed / not working:
- `photo:applyDevelopSnapshot` had no effect on the virtual copy in all four variants (local or global snapshot id, inside or
  outside a write gate; no error raised). Undo therefore uses the before/after settings journal; the snapshot is kept as a
  marker the user can click in Lightroom. Untested: applying a snapshot to a master photo.

Findings that change later phases:
- The effective Basic-panel keys are the `...2012` ones (`Exposure2012`, `Contrast2012`, `Highlights2012`, `Shadows2012`,
  `Whites2012`, `Blacks2012`, `Clarity2012`); legacy `Exposure` is stored but inert. Process version here is 15.4. Phase 5 needs
  a name map and must check `ProcessVersion`.
- Snapshots are shared between a master and its virtual copies (the snapshot made on the copy also lists on the original).
- Reconnect needs ~1 s after a disconnect; the plug-in serves one client at a time.
- Plug-in start-up needs a menu item for `LrForceInitPlugin`; "Reload Plug-in" creates a new environment (see bugs 1-5 above).

Unverified:
- Selection restore after a virtual copy when nothing was selected before (one run ended with the first grid photo selected;
  a longer wait was added but not re-tested).
- RAW files; photos hidden by a Library filter; more than one client; long exports; behavior with a dialog open in Lightroom.
- Whether "Automatically write changes into XMP" is off in this catalog (the file hash did not change in this run).

Left in the test catalog: one virtual copy `darkroom 20261120-233520` (settings back to Exposure 0), one snapshot
`darkroom-before`, history steps on the copy. Ten preview JPEGs in `previews/` (git-ignored). Nothing was deleted.

### Decisions after the gate (user, 2026-11-20)
- Undo = settings journal; the snapshot stays a marker; no more time on snapshot apply.
- PRD 5.1 updated with the spike findings (virtual copies only, `...2012` keys / process version 15.4, ~1 s reconnect delay,
  shared snapshots).
- Edits go to files under `test_photos_dir` only. The one-off approval for `DSC_3533_final.jpg` is REVOKED; the spike's
  override flag was removed, and the plug-in itself now enforces the folder (`photos_root` in `bridge.json`) and refuses
  develop edits on photos that are not virtual copies.

### Open threads carried forward
- BEFORE PHASE 5: run the wrong-catalog refusal live in Lightroom (dummy path in `bridge.json`, then restore it).
- The five test copies in `D:\darkroom_test` must be imported into the test catalog ("Add") before any further Lightroom edit.
- The plug-in changes above need "Reload Plug-in" once (not yet reloaded or re-tested in Lightroom).

### Next step
Phase 3 (below).

## Phase 3 — Photoshop spike (2026-11-20/21) — DONE, waiting at GATE

### Done
- Adapter `src/darkroom_ai/adapters/photoshop`: COM via raw pywin32 in a child process per operation with a hard deadline
  (missed deadline -> child killed -> `PS_NOT_REACHABLE`; Photoshop is never killed), `photoshop.auto_start` (user decision: ON,
  `start_timeout_s = 120`), version check (`ADOBE_VERSION_UNSUPPORTED`, tested: 27.8), ExtendScript + Action Manager scripts in
  `jsx/`, `PhotoshopAdapter` Protocol, new code `PS_COMMAND_FAILED`.
- Safety in code: only files inside `outputs/photoshop` are opened (`prepare_copy` puts a copy there); output is always a new
  file (`name-2`, `name-3`, ... on collision); documents are closed without saving; Photoshop's own dialogs are suppressed
  during our calls and the user's setting restored.
- Spike on Photoshop 27.8.0 with a copy of `D:\darkroom_test\DSC_2457-37.jpg` (all checks passed):
  - Not running + auto-start off -> `PS_NOT_REACHABLE` ("Photoshop is not running").
  - Auto-start: COM launched Photoshop and answered in 6.1 s.
  - Opened the copy; ExtendScript returned name/size/bits/mode/layers (5568 x 3712, 8 bit, RGB, 1 layer).
  - Action Manager `executeActionGet` returned the same document (title, id, `hasBackgroundLayer`; `numberOfLayers` is 0 for a
    background-only document).
  - Added a layer (2 layers), saved a NEW layered PSD beside the copy (starts with `8BPS`); a second save got a new name.
  - Closed without saving; working copy and test photo are byte-identical (SHA-256) afterwards.
  - Timeout: a 20 s script with a 3 s deadline -> `PS_NOT_REACHABLE` after 3.0 s; Photoshop answered again afterwards.
- Library comparison: both attach to 27.8 (pywin32 0.01 s, photoshop-python-api 0.36 s). Recommendation: raw pywin32
  (already required by `mcp`, no extra packages, we need our own child-process deadline anyway); photoshop-python-api adds
  comtypes and a pinned `wheel`, and lists support only up to 2025.

### Open threads
- Modal-dialog test DONE: with a Photoshop dialog open, the call failed after 0.2 s with `PS_NOT_REACHABLE`
  ("busy ... likely a dialog is open", COM result 0x8001010A). No hang.
- DONE (user decision 2026-11-21): raw pywin32 accepted; `photoshop-python-api` and `comtypes` removed, re-locked
  (`wheel` went with them), `pip check` clean. PRD 5.2 and CLAUDE.md updated.
- Auto-start is configurable in `darkroom.toml` (`photoshop.auto_start`, `start_timeout_s`, `ready_grace_s`).
- Start / sign-in screen: `connect()` now watches Photoshop's top-level windows (plain Win32, no COM). If Photoshop is on
  screen but has not answered for `ready_grace_s` (30 s), it raises `PS_NOT_REACHABLE` naming the open windows instead of
  waiting 120 s. Verified: window listing works (saw the main window and an open 'Preferences' dialog); an already-running
  Photoshop with a dialog open answers "busy" in 0.2 s. NOT verified: a real sign-in screen (cannot be triggered here), so
  the 30 s path is covered by unit tests only.
- Unverified: 16-bit TIFF hand-off, masks/background layers, behavior on Photoshop's start/sign-in screen, UXP/batchPlay route.

## Phase 4 (start) — hand-off spike (2026-11-21) — DONE, full chain passed

### Done (no app contact)
- Lightroom plug-in command `export_tiff`: full-size 16-bit TIFF of a VIRTUAL COPY. It is a write command, so it goes through
  the same gate as all writes (token, open catalog == configured catalog, photo under `photos_root`). It writes only into
  `handoff_dir` from `bridge.json` (= `outputs\handoff`; no path is accepted from the request), refuses an existing file name
  (`FILE_EXISTS`) and additionally sets Lightroom's collision handling to "rename".
- Photoshop adapter: `apply_mask_from_png` (background -> layer, layer mask from a same-size grayscale PNG via Action Manager,
  no clipboard), `add_fill_layer_below`, `layers` (with `hasUserMask`), `save_jpg` (flattened copy, never overwrites).
- `scripts/handoff_spike.py`: Lightroom virtual copy -> TIFF -> Photoshop copy -> mask -> PSD + JPG, with numeric checks
  (TIFF header bit depth, layer/mask report, centre unchanged vs. a reference JPG, corner == fill colour, file hashes).
- 187 tests pass with the Lua syntax check; `make quality` clean. `bridge.json` rewritten with `handoff_dir` and `photos_root`.

### Run on 2026-11-21 00:12 (Photoshop half only)
- Plug-in reloaded; `ping` now reports `photos_root = D:\darkroom_test`.
- Folder rule verified LIVE in Lightroom: a preview export for a gallery photo was refused with `PHOTO_OUTSIDE_ROOT`
  (`GUARDRAIL_REJECTED`) and no file was written.
- Photoshop half PASSED on Photoshop 27.8.0 (auto-started), using a 16-bit TIFF converted locally from
  `D:\darkroom_test\DSC_2457-37.jpg` with OpenCV as a stand-in for the Lightroom export:
  - TIFF header 16/16/16 bit; Photoshop opened a COPY of it and reports 16 bit, 5568 x 3712.
  - Layer mask from the PNG (Action Manager, no clipboard); layers top to bottom: `subject` (mask) / `background`.
  - New layered PSD and flattened JPG saved beside the copy (JPG save from a 16-bit document works); closed without saving.
  - Numeric: centre region identical before/after the mask (85.9/130.1/123.4); corner = fill colour (200/30/31 vs 200/30/30).
  - TIFF and test photo byte-identical afterwards.

### Run on 2026-11-21 07:43 (full chain, Lightroom half included) — PASSED
- At 00:05-00:20 the test copies were not in the catalog (it then held 67 files, all gallery photos). The user imported them
  by hand later (as `TEST_*.jpg` plus the `DSC_*.jpg` copies); the catalog then had 79 photos. Counts earlier in this file
  were taken before that import and are stale.
- Results are in "CURRENT STATE" at the top of this file: 16-bit TIFF from a virtual copy in 0.7 s, sRGB profile embedded,
  refusals for a master and for an existing name verified live, Photoshop chain passed, original byte-identical.

### Open threads
- Still due before Phase 5: live wrong-catalog refusal test.

## darkroom-mcp pivot (2026-11-21) - decisions and P0 result
User decision (2026-11-21, "go with your recommendation"): the project becomes an MCP tool for the Hermes Agent (Darkroom profile). Hermes' single model does all the thinking; no second LLM and no agent loop in this project any more. Full PRD and the P0 memo are in the Claude project "Hermes Agent <--> LightRoom" (darkroom-mcp/PRD.md, darkroom-mcp/P0-audit-par4987.md).
Decisions:
1. Hybrid: own plug-in + tool layer stay; port pure-SDK logic (masks, write-survival check) from par4987/lightroom-mcp after audit (MIT, attribute Marcin Skalski / par4987). Not adopted wholesale.
2. Photoshop adapter/tools stay in code, OFF by default, out of v1.
3. Old chat/agent/llm code: git tag first, then park in `legacy/`. Nothing is deleted.
4. "TEST catalog only" is replaced by: reads on any open catalog; writes only on an explicit catalog allowlist AND a backup rule. Test catalog stays allowlisted; the production catalog is NOT allowlisted until P6 passes and the user says so. (CLAUDE.md wording to be updated with the user's OK when P1 starts.)
5. JPG white-balance loss is fixed right after P1, before masks and presets.
6. `mcp_servers.darkroom_ai` goes into the Darkroom profile config.yaml at the end of P1 (backup, diff shown, include filter, sampling off).
7. P5 adopts the tiered preset rule (look settings with confirmation summary; geometry / process-version refused) with exact undo. This reverses "apply_preset stays hidden", in P5 only.
P0 audit of par4987/lightroom-mcp (commit e51dc75, read-only, nothing run): no outbound network in server or plug-in, loopback + token. BUT four Lua files run PowerShell with ExecutionPolicy Bypass (SendKeys for AI Denoise and remove_from_catalog; a Lightroom-window SCREEN CAPTURE in the AI-mask helper): REJECTED. Single squashed commit, 0 stars: reference only. Portable: createNewMask ai/range logic (needs the masking tool open; may return nil even on success; verify by diffing getAllMasks), MaskGroupBasedCorrections read/write. KEY LEAD for the JPG white-balance loss: its Corrections.lua says Lightroom serves a write back on an immediate read and discards it on the next recompute, and that a tiny throwaway render is the reliable check. Hypothesis only.
Next: P1 plan shown to the user; waiting for the go. Not committed, not pushed.
User decisions (2026-11-21, "go with your recommendations") on the deploy and release questions:
8. P8 Deploy kit added after P1 and the JPG white-balance fix: AGENTS.md, a Hermes skill shipped in `hermes/skills/`, and `darkroom deploy` (plans first, asks before every write, refuses to run unattended, backs up and diffs the Hermes config).
9. Repo PRIVATE until P8 passes, then public. Another Hermes instance fetching before then uses a read-only deploy key or token.
10. MIT licence, plus a NOTICE crediting Marcin Skalski / par4987 for ported mask logic.
11. On GitHub set `dev` as the default branch; `master` is not touched. After P8 the user merges dev to master and tags a release.
12. PROGRESS.md moves to docs/PROGRESS.md before the repo goes public (after P1), with the CLAUDE.md path updated.
Interim: the user pushes `dev` privately as a backup (Claude has no GitHub credentials in this setup).
User decisions (2026-11-21, "go with your recommendation") on upgrade resilience and maturity:
13. New phase P1c "Compatibility layer" after P1 and the JPG white-balance fix: capability probing in the plug-in and a `get_capabilities` tool; `compatibility.toml` (tested / known issue / untested per Adobe version; warn, never refuse); Develop key map as data plus a drift check; `darkroom selftest` (live, test catalog only: set, read-back via a tiny throwaway render, export, undo per tool, pass/fail report per tool); plug-in/server protocol handshake; upgrade runbook in AGENTS.md and the Hermes skill. Photoshop version handling changes from refuse (ADOBE_VERSION_UNSUPPORTED) to warn; only a failed capability probe blocks a tool.
14. Adobe version freeze until P6: Creative Cloud auto-update off for Lightroom Classic (baseline 15.2.1) and Photoshop (27.8). After P6 ONE deliberate update, with `darkroom selftest` as the first upgrade drill.
15. The docs state the limits: Adobe's apps and Hermes' model stay external; Python and Poetry are prerequisites; anything Adobe does not expose through its SDK (e.g. new AI features) is outside this tool and returns ADOBE_FEATURE_UNAVAILABLE.
Status 2026-11-21 14:59: the `pre-mcp-pivot` git tag does NOT exist yet (checked); no code changes until the user confirms it. PRD v1.2 is in the Claude project.

## Design principle reaffirmed by the user (2026-11-21 15:06) - no scripted editing
The project provides granular raw-material tools; Hermes' model is the brain (looks at the picture through `get_preview`, reads facts, decides, edits, looks again); Hermes skills hold professional knowledge, not recipes. Nothing in the code may follow a fixed editing pattern, pipeline or formula, and no tool or report may state a "this photo needs X" verdict. Code keeps: measurements and facts, safety bounds (configurable; exceeding needs confirmation), undo, journal, read-back, capability probing.
Amendments to the plan: (1) "genre recipes with bounded ranges" dropped; the skill is `lr-genre-knowledge` (what professionals look for, trade-offs, failure modes, how to judge a result; no values to follow). (2) `inventory`/metadata return facts only. (3) Generic `list_develop_parameters` / `get_develop_parameters` / `set_develop_parameters` are added (P2) so the AI can reach ANY supported parameter; the friendly tools (adjust_lighting etc.) remain thin conveniences. (4) `darkroom selftest` verifies tool behaviour, never edit quality.
Also decided 2026-11-21 15:02: Claude does everything it can itself and asks only when a confirmation is really needed.
P1 status (2026-11-21 15:10): `pre-mcp-pivot` tag created locally (9d1bd93). Legacy parked: src/darkroom_ai/llm and agent, chat/models CLI code, test_llm_agent.py, 4 scripts, the [llm]/[agent] config sections moved under legacy/llm_agent (see legacy/README.md). `[llm]` is optional in config; doctor no longer checks a model server. Verified in a throwaway Linux venv (Python 3.13, copy outside the repo): ruff, ruff format, mypy strict clean; pytest 289 passed, 8 skipped (need the Windows Lua compiler), 1 failed = a Windows-path test that also fails on the untouched baseline (test_export_tiff_only_for_virtual_copies_and_never_overwrites, Linux vs backslash paths). NOT yet run on Windows: the user runs `make quality`. Nothing committed or pushed. GitHub: not reachable from Claude's side (claude.ai GitHub not linked; add_repo push access denied by the permission check; no credentials in the device shell).

## Audit against the no-scripted-editing principle (2026-11-21 15:25)
- OK: `analyze_photo` / `imaging/analysis.py` return measurements only (tones, clipping, colour cast); no verdict wording found. Slider tools (`adjust_lighting`, `adjust_white_balance`, `adjust_hsl`, `crop`) set what the model asks, bounded and journaled; the before/after measurement is a fact, not a decision. Bounds are safety limits and stay configurable.
- OK: `get_preview` (new) and the elicitation confirmer contain no editing logic.
- TO FIX (Photoshop phase): `replace_background` is a fixed pipeline (Lightroom TIFF -> subject mask -> Photoshop layers -> PSD/JPG) in one call. Decompose into granular tools the model chains itself: export_for_photoshop, create_subject_mask (on demand), open_document, apply_mask, add_layer_below / fill_layer, save_psd/jpg, close. Keep `replace_background` only as a thin convenience until then, or retire it.
- Commits so far on dev: 23c3596 (strip to MCP, legacy parked), 463786b (get_preview + elicitation). Windows `make quality` still to be run by the user.

## Windows gate for P1 (2026-11-21 15:39)
`make quality` on Windows 11, Python 3.13.14, in-project .venv: ruff clean, format clean, mypy strict clean (52 files), pytest 294 passed, 8 skipped (need DARKROOM_LUAC), 0 failed. The one Linux-only failure (Windows path test) does not occur on Windows, as expected. P1 pushed to origin/dev with tag pre-mcp-pivot (via IntelliJ). Test catalog is now D:\darkroom_test\test_catalog\test_catalog.lrcat (darkroom.local.toml, git-ignored); test photos: 118 Nikon JPG + 2 Sony ARW in D:\darkroom_test\photos.
Still open: live check through Hermes (/reload-mcp, get_photo_info, get_preview on the test catalog); P1b; P1c.

## Live results through Hermes (2026-11-21 16:37) - first end-to-end verification
Hermes (Darkroom profile, qwen/qwen3.8-27b, LM Studio context raised to 131072 because Hermes requires >= 64K) called darkroom_ai over stdio MCP against the open test catalog (120 photos, D:\darkroom_test\test_catalog\test_catalog.lrcat):
- list_catalog_photos / get_photo_metadata: camera, lens, focal length, aperture, shutter, ISO returned correctly after the LrTasks.pcall fix (plug-in 0.5.1). Sony ILCE-7M5 + FE 24-70mm F2.8 GM II (2 ARW), Nikon D7500 + 18-140mm (JPGs).
- list_develop_parameters / get_develop_parameters: lens panel read for DSC_2411-1.jpg (profile off, CA off, defringe 0, vignette midpoint 50).
- Earlier: get_selected_photos, find_photo, get_photo_info, get_preview, analyze_photo, create_virtual_copy, adjust_lighting, adjust_white_balance, undo all ran through Hermes. White balance on JPG survived the preview render (R/B 1.43 -> 1.26) in that sample.
- Guardrail seen working: a photo outside D:\darkroom_test was refused (GUARDRAIL_REJECTED).
Open: preview image reached Hermes' image cache (cache\images\img_*.jpg); not yet verified that the model sees it directly. Darkroom SOUL.md still says Nikon D7500/NEF (user also has a Sony ILCE-7M5 shooting ARW): fix with a shown diff in P7.
Commits (local, dev): up to c6a9661. Pushed to origin: up to e448450.
