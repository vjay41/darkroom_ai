# darkroom_ai — progress log

Running log, updated at the end of every phase. PRD.md section 12 is edited only when one of its open questions is answered.
Plan of record: phases 0-8 with a GATE after each (stop and wait for an explicit "proceed").

## CURRENT STATE — 2026-11-21: Phase 7 in progress: items 1, 4, 5 done; live error cases 1-2 of 5 done (the plug-in bridge is STOPPED right now); next: cases 3-5 with the user, pip-audit, firewall run

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
| 7 Hardening | In progress (see "Phase 7 progress") |
| 8 Acceptance | Not started |

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
2. Plug-in bridge stopped (Lightroom open on the test catalog, no listener on ports 58763 / 58764): first run gave the
   same text as case 1 ("did not answer 'ping' in time"), whose next step never mentioned starting the bridge. FIXED and
   re-tested live: `[LR_NOT_REACHABLE] Nothing is listening on the Lightroom plug-in's local ports (58763 and 58764):
   Lightroom Classic is closed, or the darkroom_ai bridge is not started (waited 20 s).` Next step: open Lightroom with the
   test catalog, File > Plug-in Extras > "Start darkroom_ai Bridge", or enable the plug-in in the Plug-in Manager. This
   message now also covers case 1 (not re-run with Lightroom closed). "Did not answer in time" remains for a plug-in
   that listens but stays silent. Still waits the full 20 s connect deadline (kept: the plug-in needs time after a
   restart). Unit test added (315 tests).
3-5 (wrong catalog, model unloaded, Photoshop dialog open): pending, one at a time.

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
