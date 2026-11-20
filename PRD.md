# darkroom_ai — Product Requirements Document

Status: DRAFT v1 (approved direction, pending user review)
Date: 2026-11-20

## 1. Problem
Editing photos in Lightroom Classic and Photoshop is manual. The user wants to describe an edit in plain language
and have a **local vision LLM** carry it out in the **installed** Adobe apps, with **no internet dependency in our code**.

## 2. Goals / Success criteria
1. Lightroom Classic: set exposure, tone, white balance, HSL, crop, and masks on the selected photo; snapshot-based undo.
2. Preview -> analyze -> adjust loop (default max 3 passes) using numeric analysis plus the VLM's visual review.
3. Photoshop on demand: background replacement and layer-level edits, saved as a NEW layered PSD beside the original.
4. Models are swappable via config profiles; `darkroom doctor` and `darkroom models test` verify and compare them.
5. Zero non-loopback traffic from our code, enforced by an automated test.
6. Every action logged and reversible. Originals are never overwritten.
7. Adobe problems produce clear, actionable errors (see 8). darkroom_ai contains NO license logic.

## 3. Scope (v1)
In: Lightroom adapter, imaging/analysis toolkit, local mask model, Photoshop adapter, tool layer with guardrails,
command-line chat (`darkroom "..."`), MCP server, two playbooks (portrait, landscape), model profiles, doctor/test commands.

Out: cloud/generative features (e.g. Generative Fill), GUI, video, license handling or workarounds, multi-user use.

## 4. Environment (verified 2026-11-20)
- Windows 11; AMD Ryzen 9 9950X3D (16C/32T); 61 GB RAM
- GPUs: RTX 5090 32 GB (index 0), RTX 3090 24 GB (index 1)
- Lightroom Classic 15.2.1; Photoshop 2026 v27.8 (COM `Photoshop.Application.200` registered)
- Python: 3.14 and 3.13 installed -> requires-python ">=3.13,<3.15"; develop on **3.13 in the in-project .venv**, managed by Poetry 2.x
- LM Studio at D:\LM Studio; server http://127.0.0.1:1234/v1
- Installed models: qwen/qwen3.8-27b (17.7 GB), meta/muse-glimmer (28B, 18.2 GB), google/gemma-4-e4b (6.3 GB)
- Windows wheels exist for pywin32, numpy, opencv, rawpy, onnxruntime-gpu, pillow, mcp (3.13 and 3.14).
  scikit-image is not a dependency. No 3.15 wheels yet for onnxruntime(-gpu), scikit-image.
  NOT yet checked: PyTorch + CUDA for the RTX 5090 (prefer ONNX models).

## 5. Architecture
```
User ──► CLI chat / MCP client ──► agent loop ──► local VLM (LM Studio, localhost)
                                        │ structured tool calls
        ┌───────────────┬───────────────┼──────────────────┐
   Lightroom adapter  Photoshop adapter  Imaging toolkit   Safety/log
   (TCP localhost)    (Windows COM)      (OpenCV, rawpy,   (snapshots, guardrails,
        │                  │              ONNX masks)       action journal)
   Lua plugin in LrC    Photoshop.exe
```

### 5.1 Lightroom connection
Our OWN Lua plugin (.lrplugin, in this repository) runs inside Lightroom and exposes JSON commands over localhost TCP sockets
(LrSocket). Python sends commands (read photo, get/apply Develop settings, create virtual copy, snapshot, export preview to a file).
Rules for the plugin: loopback only; a per-request token; photos are addressed by catalog ID, not by selection; ANY write is refused
unless the open catalog's path equals the configured catalog (the TEST catalog during development); Develop edits use the LrPhoto
settings calls so JPG works; no network or process calls.
Decision (Phase 1, 2026-11-20): `lightroom-cli` 1.2.2 was reviewed and is NOT installed or adopted (no authentication, no catalog
check, no snapshot revert, refuses plain JPG, hand-written JSON that breaks on Windows paths, heavy dependency, untested on Windows).
Its two-socket LrSocket pattern is used as a reference only (MIT, with attribution). Fallback if the plugin route fails: write
Develop settings via XMP/preset files.
Verified in the Phase 2 spike (Lightroom Classic 15.2.1, 2026-11-20):
- Develop edits via the LrPhoto settings calls work on JPG from the Library module; the Develop module is not needed.
- ALL edits go to virtual copies, never to master photos. During development, edits are only made to photos whose files are
  under the configured test-photo folder.
- Undo = the before/after settings journal (re-apply the recorded "before" values). Applying a snapshot had no effect on a
  virtual copy, so a snapshot is only a marker the user can click. Snapshots are shared between a master and its virtual copies.
- The effective Basic-panel settings are the `...2012` keys (`Exposure2012`, `Contrast2012`, `Highlights2012`, `Shadows2012`,
  `Whites2012`, `Blacks2012`, `Clarity2012`) under process version 15.4; the legacy `Exposure` key is stored but does not change
  the image. The tool layer maps friendly names to these keys and checks the process version.
- The plug-in serves one client at a time and needs about 1 s after a disconnect before a new connection works; the client waits.
- Previews are exported to files with an export session. A photo that is not shown in the Library grid cannot be selected, so
  the plug-in switches to "All Photographs" for the moment a virtual copy is created.

### 5.2 Photoshop connection
Python attaches via COM with raw pywin32 to Photoshop 2026 (decision 2026-11-21 after the Phase 3 spike; photoshop-python-api
was compared and dropped). Every COM call runs in a child process with a deadline. Uses ExtendScript and Action Manager for full coverage.
There is no headless mode. With `photoshop.auto_start` on (default) Photoshop is launched through COM when it is not running;
with it off, a closed Photoshop gives PS_NOT_REACHABLE. Verified on Photoshop 27.8 in the Phase 3 spike.
Backup route if COM misbehaves: UXP/batchPlay.

### 5.3 Lightroom <-> Photoshop hand-off
Lightroom exports 16-bit TIFF copy -> mask generated locally (ONNX segmentation on the 3090) -> Photoshop applies mask, new
background layer, edge refinement -> saves layered PSD beside original -> import PSD into the (test) catalog. Original untouched.

### 5.4 Typical flow (lighting edit)
1. Pick playbook. 2. Snapshot + virtual copy. 3. Export small preview; measure histogram, clipping, skin-tone level, color cast.
4. VLM sees preview + numbers, proposes edits as structured values; guardrails clamp them. 5. Apply, re-export, compare; <=3 passes.
6. Report changes + undo (revert to snapshot).

## 6. File types
Default JPG. RAW supported when the file is RAW or the user says so explicitly. In both cases Lightroom edits and exports
JPEG previews; Photoshop hand-offs use 16-bit TIFF.

## 7. Models
OpenAI-compatible, localhost-only profiles in `darkroom.toml`:
- primary: `qwen/qwen3.8-27b` (on RTX 5090; consider a higher-quality quant later)
- glimmer: `meta/muse-glimmer` (bake-off candidate; vision/tool-calling unverified)
- fast: `google/gemma-4-e4b` (quick drafts)
Each profile sets endpoint, model id, vision flag, tool mode (native | json_schema | prompt), temperature, image limits.
Config validator rejects non-local `base_url`. Mask/segmentation models run on the RTX 3090, independent of the LLM choice.
LM Studio vision models need their vision projector (mmproj); verify the eye icon / an image test.
Final model choice is decided by a bake-off (valid tool-call rate, guardrail adherence, review consistency, speed).

## 8. Error taxonomy (no license logic)
| Code | Meaning | Message intent |
|---|---|---|
| LR_NOT_REACHABLE | Lightroom plugin socket silent | Check Lightroom is open, plugin enabled, no dialog (sign-in/trial/license) waiting |
| PS_NOT_REACHABLE | Photoshop COM timeout / cannot start | Check for license/sign-in/trial dialog |
| ADOBE_FEATURE_UNAVAILABLE | App reports a feature disabled (e.g. Develop) | Likely license/trial issue; fix in Adobe app, retry |
| ADOBE_VERSION_UNSUPPORTED | Untested app version | Show detected vs tested versions |
| LLM_NOT_REACHABLE / LLM_NO_VISION / LLM_BAD_TOOL_CALL | Model server/model problems | Say what failed and how to fix |
Every Adobe call has a timeout; messages say "likely", never claim a certain license diagnosis.

## 9. Safety & guardrails
Dedicated TEST catalog during development. Virtual copies + snapshots. Clamped edit values (e.g. exposure +/-1.5 EV per pass),
max 3 passes. Action journal. Confirmation before destructive or bulk actions.

## 10. Offline & privacy
Our code talks to 127.0.0.1 only; no non-loopback traffic at runtime. All network access goes through one loopback-only module
(httpx2 for the local model server; MCP over stdio). Dependencies pinned and reviewed; automated test fails on non-loopback connections. Adobe apps keep their own network/license behavior (user's responsibility). During development the user
allows Claude Code to view real test photos (images viewed by Claude Code are sent to Anthropic's API); numeric-only tests send nothing.

## 11. Plan
1. Review `lightroom-cli` source for network calls/unexpected behavior; report before installing. (Done: not adopted, own plugin.)
2. Spike 1 — Lightroom on test catalog: plugin loads, ping, set one Develop value on a virtual copy, export preview.
3. Spike 2 — Photoshop COM on 2026: hello world, one layered edit on a copy.
4. Imaging toolkit + local mask model (ONNX, RTX 3090).
5. Tool layer, guardrails, error codes.
6. Agent loop, model profiles, `doctor`, `models test`; CLI chat + MCP server.
7. Offline verification test.
8. Bake-off on test photos; pick default model; two playbooks.

## 12. Open questions
- Does qwen/qwen3.8-27b have its vision projector? (user running image test)
- Which Lightroom controls does the SDK expose (AI masks, Denoise)? Fallback: UI automation.
- Does COM work as expected on Photoshop 2026?
- ~~Is lightroom-cli sound on Windows, or do we write our own plugin?~~ ANSWERED 2026-11-20: we write our own plugin (see 5.1).
