# darkroom_ai

Local, offline AI photo-editing agent. A local vision LLM drives the installed Lightroom Classic and
Photoshop on Windows. See `PRD.md` for scope and `PROGRESS.md` for the current state.

Status: early development (phase 0 scaffold). Install, Adobe setup and troubleshooting are written in phase 7.

## Developer quick start
Requires Python 3.13 or 3.14, Poetry 2.x and GNU make.

```
make install     # core + dev dependencies into .venv
make quality     # lint + format check + type check + tests
make help        # all targets
```
