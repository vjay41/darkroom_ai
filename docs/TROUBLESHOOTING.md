# Troubleshooting

Start with `get_app_status` / `darkroom doctor`, then `get_capabilities`. Error codes are in `ERRORS.md`. Every error
carries a plain next step; this page adds the situations that took real effort to find.

| Symptom | Likely cause | What to do |
|---|---|---|
| `LR_NOT_REACHABLE` | Lightroom closed, plug-in not enabled, bridge stopped, or another client holds the plug-in | Open Lightroom; Plug-in Manager shows the plug-in enabled (Reload Plug-in); *Plug-in Extras > Start darkroom_ai Bridge*; if a CLI command is failing while Hermes is connected, run `darkroom connectors --stop-all`. |
| `LR_WRONG_CATALOG` | The open catalog is not the allowlisted one | Open the configured catalog or change `test_catalog` in `darkroom.local.toml`, then `darkroom lightroom setup` and reload the plug-in. |
| Token errors from the plug-in | `bridge.json` and the plug-in disagree | `darkroom lightroom setup` (add `--rotate-token` for a new one), then Reload Plug-in. |
| Protocol mismatch on connect | Plug-in and server are from different versions | `git pull`, `darkroom lightroom setup`, Reload Plug-in. |
| `CONNECTOR_STALE` or a `connector_warning` | The source changed on disk after the connector started | `darkroom connectors --stop-stale` (or `--stop-all`); the agent's next call starts a fresh connector. |
| A new tool is missing from Hermes | Hermes loaded its tool list at start, or the wrong profile is selected | Restart the Hermes desktop app and select the profile you deployed to. |
| `LR_BUSY_USER` on a mask tool | The person is using Lightroom, or the working copy cannot be shown | Stop clicking in Lightroom and retry once. If it repeats or a mask takes about a minute and returns without an id, the working copy is probably inside a **collapsed stack**: Library module, *Photo > Stacking > Expand All Stacks*. |
| People mask times out | Nobody picked a person in Lightroom's panel and pressed Create Mask | Run it again with Develop showing the working copy; pick the person, press Create Mask (or Return) within about 60 s. |
| An edit "applied" but the preview did not change | Lightroom dropped the write on recompute, or a legacy key was used | Compare `differs_from_applied` in `analyze_photo`; use the `...2012` keys; read back with `get_develop_parameters`. |
| Clipping numbers differ between two measurements | Different preview sizes | Measure at the same `long_edge`. |
| `FACE_MODEL_MISSING` | No face model configured | Put a YuNet ONNX file on the machine and set `[faces] model` in `darkroom.local.toml`. Nothing is downloaded automatically. |
| `ADOBE_VERSION_UNSUPPORTED` / untested version warning | A version outside `compatibility.toml` | Follow `UPGRADE.md`; run `darkroom selftest`. It warns; it does not block. |
| `ADOBE_FEATURE_UNAVAILABLE` | The Adobe app reports a feature as disabled | Likely a licence, trial or settings matter inside the Adobe app; fix it there. darkroom_ai has no licence logic. |
| Deploy refuses to run | No person at the keyboard | Run `darkroom deploy` yourself in a terminal; `--dry-run` shows the plan anywhere. |
| Tests skipped: Lua syntax | `DARKROOM_LUAC` not set | Point it at the SDK's `luac.exe`. |
| `git` says a `.lock` file exists | An earlier command was interrupted or could not delete its lock | Make sure no git is running, then remove or rename the stale `.git/*.lock`. |

## Reading the journal
`logs/journal.jsonl` has one JSON line per call: `tool`, `args` (what was asked), `applied` (what really happened after
the guardrails), `undo`, `status`, `error`. It records completed calls only. To see what happened to one photo, filter
by `applied.photo_id`.

## When in doubt
Stop, report the exact error text and what was checked, and ask. Do not retry a failing mask or write in a loop, and
never point the tools at a catalog you were not told is allowed.
