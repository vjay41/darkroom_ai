# Install

Everything runs on one Windows machine. Only `make install` (and `make audit`) touch the network; the running
tool never does.

## Prerequisites
- Windows 11, Lightroom Classic (tested: 15.2.1), Python 3.13 (not 3.15 yet), Poetry 2.x, GNU make, git.
- Hermes (desktop app) with a local vision model that can call tools. darkroom_ai needs no model of its own.

## Steps
1. Clone the repository and check out `dev` (or a release tag).
2. `make install`, then `poetry run pip check`.
3. Create a **dedicated test catalog** in Lightroom with a few copies of photos you do not mind editing.
4. Create `darkroom.local.toml` next to `darkroom.toml` (it is git-ignored):
   ```toml
   [lightroom]
   test_catalog = 'D:\path\to\Test.lrcat'       # the only catalog edits are allowed in
   test_photos_dir = 'D:\path\to\test\photos'   # only photos below this folder are edited
   ```
5. See what the deploy would do, then do it:
   ```
   poetry run darkroom deploy --hermes-profile "<folder of your Hermes profile>" --dry-run
   poetry run darkroom deploy --hermes-profile "<folder of your Hermes profile>"
   ```
   It shows a diff of the profile's `config.yaml`, backs up every file it would replace and asks before each write.
   It adds the `darkroom_ai` server, copies the skills in `hermes/skills/`, and writes the plug-in's settings file.
   It refuses to run without a person at the keyboard.
6. In Lightroom: File > Plug-in Manager > Add, choose the folder `poetry run darkroom lightroom setup` prints,
   then press Reload Plug-in if it was already added. Expand all stacks in the Library module
   (Photo > Stacking > Expand All Stacks) so working copies can be shown.
7. Restart the Hermes desktop app and pick the profile you deployed to. Hermes only sees new tools after a restart.
8. Check: `poetry run darkroom doctor`, then `poetry run darkroom selftest` (read-only).

## First supervised run
Ask the agent to look at one test photo and make at most one small change. Check that it reports what it looked
at, what it changed or left alone, and how to undo it. Do not point it at your main catalog until you have done
this and decided to allowlist that catalog yourself.

## Things you do by hand
Adding the plug-in, choosing the catalog, restarting Hermes, answering the agent's confirmation prompts, and
deleting files. darkroom_ai never deletes your files.
