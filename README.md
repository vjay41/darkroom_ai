# darkroom_ai

A local, offline photo-editing assistant for Windows. A vision model running on this computer
looks at measurements and a small preview of one photo and edits it through the installed
Lightroom Classic and Photoshop. darkroom_ai itself makes no internet connections; Lightroom,
Photoshop and the model server are separate programs.

Scope and design: `PRD.md`. Current state, test evidence and open points: `PROGRESS.md`.

## Safety rules the program enforces
- Only loopback (127.0.0.1) connections. A non-local model address is rejected in the config.
- Originals are never changed. Lightroom edits go to a virtual copy; Photoshop works on copies
  and writes a NEW PSD and JPG under `outputs\photoshop`. Existing files are never overwritten.
- Lightroom edits are refused unless the open catalog is the configured one and the photo is in
  the configured photo folder.
- Every slider change is clamped (per pass and in total) and reported as requested vs applied.
- The model can only call the listed tools. It cannot confirm anything for itself, cannot run
  scripts, and can only undo edits made in its own session.
- A request is sent to the model only if the model server reports the model as already loaded.
- Photoshop's Select Subject is used only in Device mode.

## Tested with
Windows 11, Python 3.13, Lightroom Classic 15.2.1, Photoshop 2026 (27.8), LM Studio as the local
model server. Other versions are untested: a different Lightroom version gives a warning in
`darkroom doctor`, and an untested Photoshop version is reported as `ADOBE_VERSION_UNSUPPORTED`.

## Setup
1. Install: Python 3.13, Poetry 2.x and GNU make, then `make install` (into the in-project `.venv`).
   `make install` and `make audit` download packages or contact a package index; only the
   running app is offline.
2. Create `darkroom.local.toml` next to `darkroom.toml` (it is git-ignored):
   ```toml
   [lightroom]
   test_catalog = 'D:\path\to\Your.lrcat'      # the only catalog edits are allowed in
   test_photos_dir = 'D:\path\to\photo\folder' # only photos below this folder are edited
   ```
3. Lightroom: run `poetry run darkroom lightroom setup`, then in Lightroom choose
   File > Plug-in Manager > Add and pick the folder the command printed. Start the connection
   with File > Plug-in Extras > "Start darkroom_ai Bridge" (needed after each Lightroom start).
4. Photoshop: Preferences > Image Processing > "Select Subject and Remove Background" = Device.
5. Model: see "Model setup" below.
6. Check: `poetry run darkroom doctor`.

## Model setup
The model runs in a local, OpenAI-compatible model server on this computer (tested: LM Studio at
`http://127.0.0.1:1234/v1`). The profiles are in `darkroom.toml` under `[llm.profiles.*]`;
`active_profile` picks one. A profile's address must be a loopback address.

- Load the model yourself, in the model server, before you use darkroom_ai. The program checks
  the server's list and refuses with `LLM_MODEL_NOT_LOADED` if the model is not loaded; it never
  relies on the server loading a model on demand (`require_loaded = true` under `[llm]`).
- Context length: about 32768 is enough. The default of a large model (for example 128k) only
  costs GPU memory.
- GPU: choose the GPU for the model in the model server. Photoshop's Select Subject also needs
  GPU memory; `darkroom doctor` shows where both run and warns when a GPU that Photoshop uses is
  short of free memory.
- The model must accept images (a vision model) and tool calls. `darkroom models test` checks
  both without touching Lightroom or your photos.
- Tested live: the `primary` profile only. The `glimmer` and `fast` profiles in `darkroom.toml`
  are UNTESTED starting values.

## Commands
| Command | What it does |
|---|---|
| `darkroom doctor` | Checks config, Lightroom, Photoshop, the model and GPU memory. |
| `darkroom chat "request" [--photo PATH] [--playbook portrait\|landscape]` | Lets the model edit one photo (lighting, white balance, colour, crop). Asks y/N before anything gated. |
| `darkroom revert [--photo PATH]` | Puts the photo's virtual copy back to its values from before darkroom_ai's first edit. Asks first. Only for you, not for the model. |
| `darkroom clean-outputs` | Lists and, after y, removes intermediate files (previews, hand-off TIFFs, masks, working copies). Final PSD/JPG results are kept. |
| `darkroom models test [profile ...]` | Tests a model profile: a synthetic picture, tool calls on a simulated Lightroom, prompt injection. |
| `darkroom mcp` | Serves the same tools over MCP (stdio). Requests that need a confirmation are refused there. |
| `darkroom lightroom setup` / `ping` | Writes the plug-in's settings file / checks that the plug-in answers. |

Run them as `poetry run darkroom ...` from the repository.

## Where things go
- `outputs\photoshop\*_darkroom.psd` / `.jpg`: final results. `outputs\handoff`, `outputs\masks`,
  `previews`: intermediates. `logs\journal.jsonl`: every action, with what is needed to undo it.
- All of these folders are git-ignored.

## If something goes wrong
Every error has a code, a plain message and a next step. The common ones:

| Code | Usual cause |
|---|---|
| `LR_NOT_REACHABLE` | Lightroom is closed, the plug-in is disabled or its bridge is not started, or a dialog is waiting. |
| `LR_WRONG_CATALOG` | A different catalog is open than the configured one. Nothing is changed. |
| `PS_NOT_REACHABLE` | Photoshop is closed or a dialog is open; the message names the open windows. |
| `LLM_NOT_REACHABLE` / `LLM_MODEL_NOT_LOADED` | The model server is not running / the model is not loaded. |
| `GUARDRAIL_REJECTED` | The request is outside a safety rule; the message says which. |
| `CONFIRMATION_REQUIRED` | The action needs your y/N in `darkroom chat`. |

## Proving it stays offline
- `make offline-check`: tests that fail on any non-loopback connection or network import.
- `poetry run python scripts\offline_proof.py [--with-adobe --photo PATH]`: records every address
  the process touches.
- `powershell -File scripts\firewall_rules.ps1`: PRINTS Windows Firewall commands that block
  Python's outbound traffic, for you to apply and remove yourself. darkroom_ai keeps working with
  the block on. This says nothing about what Lightroom, Photoshop or the model server do.

## Limits and known gaps
- Tested live with one model profile (`primary`) and two test photos; the `glimmer` and `fast`
  profiles are untested. See `PROGRESS.md` for what is
  unit-tested only and what is deferred (other model profiles, RAW files, presets, the ONNX mask
  backend, face detection).
- The model's own impression of brightness is unreliable; the playbooks rely on measured numbers.
- A virtual copy made by darkroom_ai stays in the catalog after a revert.

## Development
`make quality` (lint, format check, type check, tests) must pass before every commit. `make help`
lists all targets. `make audit` runs pip-audit and uses the network; it is not part of `make quality`.
