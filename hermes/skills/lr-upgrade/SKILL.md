---
name: lr-upgrade
description: "Knowledge for after an Adobe or darkroom_ai update: how to read get_capabilities and run_selftest, what an untested version means, and what to report. Guides Hermes; the runbook lives in docs/UPGRADE.md."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, upgrade, knowledge]
    related_skills: [lr-safe-operations]
    uses_tools: [get_capabilities, run_selftest, get_app_status]
---

# After an update

The repository's `docs/UPGRADE.md` is the step-by-step runbook for the person. This skill is how Hermes
reads the results and what to tell him. Adobe versions are frozen until the soak test has passed; after
that they are updated one at a time.

## Reading `get_capabilities`
- **Version status:** `tested` is what was verified. `untested` is a warning, not a block: carry on, but
  treat new behaviour as unverified. `known_issue` names a problem; read the note.
- **Plug-in block:** the protocol must match, no commands may be missing, and the plug-in must be recent
  enough. Otherwise say what the note says: update the plug-in and reload it in Lightroom.
- **Feature list:** features that are untested, have a known issue or are unavailable are listed. Tell
  Vijay before using one.

## Reading `run_selftest`
- It checks, probe by probe, that the installed apps still behave as expected. Report every probe, not
  only the failures, and say which tools a failed probe could affect.
- A drift in the Develop key map means Adobe renamed or removed a setting. The matching tools may fail or
  silently do nothing; do not edit real photos until that is understood.
- The write test makes one virtual copy in the test catalog and changes then restores one value; it asks
  for a go-ahead first. A selftest checks that tools work, never that an edit is good.
- If the self-test cannot reach Lightroom, the connector may be holding the one-client plug-in or the bridge
  may be stopped; report the exact error.

## What not to do
- Do not decide on your own that a new version is fine. Report what was checked and what was not.
- Do not edit the compatibility data or settings yourself; propose the change and let the person approve it.
