# darkroom_ai

Local, offline AI photo-editing agent. A local vision LLM drives the user's installed Lightroom Classic 15.2.1 and
Photoshop 2026 (27.8) on Windows 11. Read PRD.md for scope, architecture, and the plan.

## Hard rules
- Runtime is fully offline: bind only to 127.0.0.1; no non-loopback network calls from our code. An automated test enforces this.
- Never overwrite originals. Use virtual copies and snapshots. Photoshop output is a NEW PSD beside the original.
- No license logic of any kind. On Adobe app problems raise clear errors (LR_NOT_REACHABLE, PS_NOT_REACHABLE,
  ADOBE_FEATURE_UNAVAILABLE, ADOBE_VERSION_UNSUPPORTED) with a plain next step. Every Adobe call has a timeout.
- Develop and test against the dedicated TEST catalog only, never the user's main catalog.
- Ask before destructive or bulk operations; show the plan first and wait for an explicit go-ahead.
- Python: requires-python is ">=3.13,<3.15"; develop on 3.13 in the in-project .venv. 3.15 is blocked (onnxruntime, onnxruntime-gpu
  and scikit-image have no 3.15 wheels as of 2026-11-20) - revisit when they ship.
- Packaging: Poetry 2.x (pyproject.toml, PEP 621, src layout). Pin direct dependencies exactly; poetry.lock is committed.
  Never install Poetry into .venv or create a second venv. Groups: main, win32-marked (pywin32; Photoshop is driven with raw pywin32),
  extra `ml` (onnxruntime-gpu), dev. First dev step: `make install`, then `poetry run pip check`.
- Use the Makefile; `make quality` (lint + format-check + typecheck + test) must pass at the end of every phase.
- Dependency injection: adapters, LLM client, imaging and journal are passed through constructors against Protocols
  (src/darkroom_ai/interfaces.py), with fakes for tests.
- Git: commit only to `dev`; never commit to master, force-push or rewrite history; push only when told. Never commit photos,
  catalogs, previews, model weights, logs, .env files or credentials; check `git status` and `git diff --stat` before every commit.
  Stage only our own files (not .github/prompts/*, .idea changes or other unrelated IDE files).
- Mask models: onnxruntime directly on user-provided ONNX files (no rembg). Never download model files automatically.
- Read third-party source (e.g. lightroom-cli) for network calls
  before using it. Do not add cloud SDKs or telemetry.
- The LLM is a swappable profile (OpenAI-compatible, localhost only). Never hard-code a model name; the config validator
  must reject non-local base_url values.
- Default file type is JPG. Support RAW when the file is RAW or the user says so. Photoshop hand-offs use 16-bit TIFF.
- Guardrails: clamp edit values (e.g. exposure +/-1.5 EV per pass); max 3 refinement passes by default.
- Prefer ONNX models over PyTorch (PyTorch + CUDA on the RTX 5090 is unverified).

## Environment
- Ryzen 9 9950X3D, 61 GB RAM; RTX 5090 32 GB (GPU 0: main LLM), RTX 3090 24 GB (GPU 1: mask/segmentation models)
- LM Studio server: http://127.0.0.1:1234/v1
- Models: qwen/qwen3.8-27b (primary), meta/muse-glimmer (candidate), google/gemma-4-e4b (fast)
- Photoshop COM ProgID: Photoshop.Application (registered as .200)

## Working style
- Show a plan before building each spike; report what worked, what failed, and what is unverified.
- Flag contradictions and risks instead of silently choosing. Keep decisions and open threads in PROGRESS.md (updated every phase);
  edit PRD.md section 12 only when one of its open questions is answered.
- Images you view are sent to Anthropic's API. Prefer numeric checks; view real photos only when needed for visual review.
