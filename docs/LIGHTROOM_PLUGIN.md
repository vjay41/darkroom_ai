# The Lightroom plug-in (`darkroom.lrplugin`)

A small Lua plug-in that runs inside Lightroom Classic and does the real work through Adobe's SDK. It lives in
`src/darkroom_ai/adapters/lightroom/plugin/darkroom.lrplugin/`. Version 0.9.4, protocol 1.

## Files
| File | Role |
|---|---|
| `Info.lua` | Plug-in manifest; adds *File > Plug-in Extras > Start/Stop darkroom_ai Bridge*. |
| `Init.lua`, `Start.lua`, `Stop.lua`, `Shutdown.lua` | Lifecycle. The bridge starts when Lightroom loads the plug-in. |
| `Bridge.lua` | The localhost socket server, token check, catalog guard, one-request-at-a-time queue, time budgets and watchdog. |
| `Commands.lua` | All command handlers (`read` and `write` tables) and `PLUGIN_VERSION` / `PROTOCOL_VERSION`. |
| `Json.lua` | JSON encode/decode (Lightroom has none built in). |
| `bridge.json` | Per-install settings and token. Git-ignored; written by `darkroom lightroom setup`. |

## Transport
Two `LrSocket` connections, both on 127.0.0.1 (default ports 58763 requests, 58764 replies, from `darkroom.toml`):
one JSON object per line.
```
Request : {"id": "...", "token": "...", "command": "...", "params": {...}}
Response: {"id": "...", "ok": true,  "result": {...}}
          {"id": "...", "ok": false, "error": {"code": "...", "message": "..."}}
```
- Every request must carry the token from `bridge.json` (at least 32 characters). Parameters and the token are never
  logged.
- Write commands are refused unless the open catalog's path equals `catalog_path` in `bridge.json`
  (`LR_WRONG_CATALOG` on the Python side).
- Requests are handled one at a time, in order; at most 2 may wait. A command that overruns its budget is cut off
  and a watchdog answers the client, after which new work is refused instead of queued.
- Time budgets: default 60 s; `export_tiff` and `import_folder` 300 s; `export_preview` 120 s; `create_mask` and
  `set_mask_values` 90 s. A response larger than 8 MiB is replaced by an error.
- The plug-in answers a `ping` handshake with its version and protocol; the client refuses a different protocol.

## Commands
Read: `ping`, `list_commands`, `probe`, `selftest`, `get_photo`, `get_metadata`, `get_selection`, `list_photos`,
`find_photo`, `list_presets`, `get_preset`, `list_snapshots`, `scan_folder`, `inspect_masks`.
Write: `create_virtual_copy`, `apply_settings`, `apply_preset`, `create_snapshot`, `apply_snapshot`, `create_mask`,
`mask_operation`, `set_mask_values`, `import_folder`, `export_preview`, `export_tiff` (the two exports write a file,
so they sit behind the same catalog guard).
The authoritative required set is `REQUIRED_PLUGIN_COMMANDS` in `src/darkroom_ai/compat.py`; a test compares it with
`Commands.lua` so the two cannot drift.

## SDK limits that shaped the design
- **No UI automation, no process calls, no network.** The plug-in uses only SDK calls.
- **Masks:** the SDK call for gradient, radial and luminance masks only arms a drawing tool and waits for a drag, so
  those masks are *written as Lightroom's own stored mask data* (`MaskGroupBasedCorrections`) in a format captured
  from masks drawn by hand in Lightroom 15.2.1. AI masks use `createNewMask` while Develop shows the photo; people
  masks need the person picked in Lightroom's own panel and *Create Mask* pressed there, which the tool hands to the
  person and then verifies. Success is always verified by reading the stored table back, never assumed.
- **Edits can be dropped.** Lightroom may accept a write, serve it back on an immediate read, and discard it when it
  next recomputes settings. The tools therefore verify with real preview renders and report differences between what
  was applied and what is there now (`analyze_photo`).
- **Stacks:** the SDK has no stack operations. A virtual copy in a collapsed stack cannot be shown in Develop, which
  breaks mask tools (symptoms: busy errors, long waits, no mask id). The fix is manual: Library module, *Photo >
  Stacking > Expand All Stacks*. Detecting it automatically needs plug-in work (see `PROGRESS.md`, next steps).
- **Colour range and depth range masks** are not available through the scripting interface.
- **One client at a time** (see `ARCHITECTURE.md`).

## Changing the plug-in
1. Edit the Lua, bump `PLUGIN_VERSION` (and `PROTOCOL_VERSION` only for an incompatible change; also
   `EXPECTED_PROTOCOL` in `compat.py`).
2. If you add a command, add it to `REQUIRED_PLUGIN_COMMANDS`, the simulated adapter (`simulated.py`), the test
   fake (`tests/fake_lightroom.py`) and `interfaces.py`.
3. Syntax-check with the SDK's `luac.exe`: set `DARKROOM_LUAC` to its path and run the tests (8 tests are skipped
   without it).
4. In Lightroom use *Reload Plug-in* in the Plug-in Manager, then `darkroom lightroom ping` and `darkroom selftest`.
5. Live-test on the test catalog only.
