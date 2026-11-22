# Upgrade runbook: Adobe apps, the Lightroom plug-in and darkroom_ai

Adobe versions are frozen at Lightroom Classic 15.2.1 and Photoshop 27.8 until the P6 soak passes.
After that, update **one thing at a time** and use the self-test as the drill. An untested version only
produces a warning; a failed probe is what tells you a tool may not work.

## Before you update anything
1. Make sure nothing is mid-edit; close Hermes sessions that are using darkroom.
2. Back up the catalog you write to (the test catalog, and the production catalog once it is allowlisted).
3. Run the read-only self-test and keep the result: `darkroom selftest` (it saves `outputs/selftest-<date>.json`).
   The plug-in serves one client at a time, so while Hermes' connector is connected the CLI cannot reach it:
   ask Hermes to call `run_selftest` instead, or stop the connector first (`darkroom connectors --stop-stale`).

## Updating Lightroom Classic or Photoshop
1. Install the update.
2. Open Lightroom with the test catalog. Reload the plug-in if Lightroom asks.
3. Ask Hermes to call `get_capabilities`, then `run_selftest`. Read every probe.
   - `untested` Lightroom/Photoshop version: a warning. Continue, but treat new behaviour as unverified.
   - `develop key map` fails: Adobe renamed or removed a Develop key. The tools that use it may fail or
     do nothing. Do not edit real photos until it is understood.
4. Run the write self-test once (`run_selftest` with `write=true`; it asks you first). It makes one virtual
   copy in the test catalog, changes Exposure by +0.1 EV, reads it back and puts it back.
5. If everything passes, add the new version to `src/darkroom_ai/compatibility.toml` (`tested`) with the date.
   If a feature broke, set its status to `known_issue` or `unavailable` with a note instead of hiding it.

## Updating the plug-in or darkroom_ai
1. `git pull` on `dev`, then `make install` and `make quality` (must pass).
2. Run `darkroom lightroom setup` (writes the plug-in's `bridge.json`; the plug-in loads from the repo), then in
   Lightroom open File > Plug-in Manager, select darkroom and choose **Reload Plug-in**.
3. `get_capabilities` must show `protocol_matches: true`, `missing_commands: []` and `recent_enough: true`.
   A different protocol number makes the server refuse to send commands (`LR_COMMAND_FAILED`, "plug-in speaks
   protocol N"); a missing command is named in the plug-in note.
4. Restart the connector so Hermes uses the new code: `darkroom connectors --stop-stale` (Hermes starts a fresh
   one on its next call). If Hermes does not see new tools, restart the gateway (`hermes gateway restart`).

## When the plug-in and server protocols change
Bump `PROTOCOL_VERSION` in `Commands.lua` and `EXPECTED_PROTOCOL` in `src/darkroom_ai/compat.py` together; a test
fails if they disagree. Add any new command the server uses to `REQUIRED_PLUGIN_COMMANDS`; a test fails if the Lua
plug-in does not register it.

## What this does not protect against
Anything Adobe does not expose through its SDK stays out of reach (`ADOBE_FEATURE_UNAVAILABLE`). Colour/depth range
masks are not verified, and people masks need a person picked in Lightroom; `get_capabilities`
lists the current status of each.
