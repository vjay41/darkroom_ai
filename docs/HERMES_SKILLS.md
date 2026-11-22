# Hermes skills (the knowledge layer)

The agent's professional judgment lives in skills, not in code. They ship in `hermes/skills/<name>/SKILL.md` and are
installed into a Hermes profile (`skills/photo/<name>/SKILL.md`) by `darkroom deploy`, which asks before each write and
backs up anything it replaces.

## The skills
| Skill | What it carries |
|---|---|
| `lr-masking` | When a mask beats a global edit, how each mask kind behaves, how to judge a mask, known failure modes (Develop takeover, collapsed stacks, wrong photo shown). |
| `lr-lens-corrections` | What lens profile, chromatic aberration, defringe and vignette fix; JPG versus RAW; how to judge honestly at preview size. |
| `lr-tone-curve` | What the combined and colour curves do; read, change and judge a curve with the guardrails. |
| `lr-presets` | What a preset can silently change (masks, colour, frame), how to read the changed-settings list, when to undo or refuse. |
| `lr-genre-knowledge` | What matters and what goes wrong in portraits, ceremonies and events, landscapes, low light, food and product. Principles only. |
| `lr-safe-operations` | What the tools protect, which actions need a person's go-ahead, checks before and after a write, recognising a stale connector or wrong photo, when to stop and ask. |
| `lr-user-guidance` | How to talk with a beginner photographer: state the intent, explain in plain words, show before and after, admit limits, teach a little. |
| `lr-upgrade` | How to read `get_capabilities` and `run_selftest` after an Adobe or darkroom_ai update and what to report. |
| `lr-noise-reduction` | Telling luminance from colour noise, measuring it (`noise_luma`, `noise_chroma`), global versus masked reduction, the cost in detail, small passes and re-measuring. |
| `lr-sharpening-clarity` | What sharpening, texture, clarity and dehaze do, protecting skin, halos, order of work, checking with `edge_strength` and the picture. |
| `lr-skin-tone` | Measuring skin patches with `measure_region`, telling a colour cast from a skin-only problem, choosing white balance, HSL or a local mask, keeping faces consistent. |
| `lr-batch-operations` | Working through many photos in phases: scope, numeric triage (`analyze_batch`), group, pilot, chunked rollout with stop conditions, review; the batch write tools (plan, one go-ahead per chunk, stop at first failure, `undo_batch`). |

## Rules for writing a skill
1. **Knowledge, not recipes.** No slider values, no fixed order of steps, no per-genre numbers. Describe how things
   behave, what usually goes wrong and how to judge the result. Photos differ.
2. **Name only real tools** and list every tool the body mentions in `metadata.hermes.uses_tools`.
3. **Stay short** (the test caps the body at 5000 characters): skills cost context every time they load.
4. **Address "the user"**, not a person's name. Skills ship publicly.
5. **Be honest about limits**: say what is unverified (`get_capabilities` is the source of truth), and tell the agent to
   say what it cannot judge at preview size.

## Format
```
---
name: lr-example            # must equal the folder name
description: "One sentence on what it carries and when to load it."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, knowledge]
    related_skills: [lr-safe-operations]
    uses_tools: [get_preview, analyze_photo, undo]
---
# Title
...
```
`tests/unit/test_hermes_skills.py` checks: the expected set of skills, complete frontmatter, `name` equals the folder,
every `uses_tools` entry is a registered tool, every tool mentioned in backticks is listed, and no slider-word-plus-number
pattern appears.

## How the skills relate to the rest of a Hermes profile
A profile may also have other skills (for example operating rules, a diagnosis skill, a command-line editing skill).
Those are the owner's; `darkroom deploy` only touches the files it lists. If another skill carries editing numbers,
add a "reference only, not a recipe" note rather than letting it compete with these. The agent should use the
`darkroom_ai` tools described in `TOOLS.md` for Lightroom work.

## Operating tips learned from live runs
- A new session may show the old tool list until the Hermes desktop app is restarted with the right profile selected.
- Approval prompts for gated actions appear in Hermes; only the person answers them.
- The agent ranks a catalog cheaply with `analyze_batch` (numbers only) and looks at pictures only for a pilot or a
  flagged photo; one image analysis costs minutes of model time.

## Developer skill (separate from the photo skills)
`hermes/dev-skills/darkroom-dev-review/SKILL.md` lets Hermes review this project's code and its use of the Lightroom
SDK and write patch *proposals* to `outputs/proposals/`. It is deliberately **not** in `hermes/skills/`, so
`darkroom deploy` does not install it into the photo-editing profile, and it is read-and-propose only: it may not edit
`src/`, `tests/`, `hermes/` or the plug-in, change the safety rules, commit, push, restart connectors or run write
tools. A person (or the supervising developer assistant) applies and tests any proposal. The reason: an agent that
could rewrite its own guardrails or tools would remove the safety model (see `DESIGN.md`).