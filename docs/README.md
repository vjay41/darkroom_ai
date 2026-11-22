# Documentation index

darkroom_ai is a local, offline MCP tool that lets an AI agent inspect a Lightroom Classic catalog and edit photos.
The agent is the brain; this project is the hands. Start here.

## If you have ten minutes (human or LLM)
1. `../README.md` - what it is, safety rules, what works and what does not.
2. `ARCHITECTURE.md` - components, the path of one tool call, process model, where things live.
3. `DESIGN.md` - the principles and safety model that must stay true, the default limits, the decision log.
4. `PROGRESS.md` - current status, open threads, next steps.

## Reference
| Document | Read it when |
|---|---|
| `TOOLS.md` *(generated)* | You need a tool's parameters, limits and description. |
| `ERRORS.md` *(generated)* | You need to know what an error code means and what to do. |
| `PRD_ADDENDUM_BATCH_WRITES.md` | You are asked to build batch writes: it is a draft that needs the owner's sign-off first. |
| `PRD_ADDENDUM_CATALOG_MANAGEMENT.md` | You are asked to build catalog management (list catalogs, import, organise): it is approved, follow its section 6 decisions. |
| `SDK_COVERAGE.md` | You wonder whether a Lightroom SDK capability has a tool, or why one was left out. |
| `LIGHTROOM_PLUGIN.md` | You touch the Lua plug-in, the socket protocol, or wonder why something is SDK-limited. |
| `HERMES_SKILLS.md` | You write or change agent knowledge skills. |
| `TESTING.md` | You need to run, add or trust tests, selftest, or the soak. |
| `CONTRIBUTING.md` | You are about to change something: rules, workflow, recipes, pitfalls. |
| `INSTALL.md` | You are setting it up on a machine. |
| `UPGRADE.md` | Lightroom, Photoshop or darkroom_ai is being updated. |
| `TROUBLESHOOTING.md` | Something is not working. |
| `../PRD.md` | You want the approved requirements and decisions in full. |
| `../AGENTS.md` | You are an AI agent working in or deploying this repo. |

## One-screen mental model
```
 person <-> Hermes (agent + skills + one local vision model)
                 | MCP over stdio
                 v
           darkroom mcp (validate, clamp, journal, confirm, measure)
                 | JSON over two 127.0.0.1 sockets + token
                 v
           Lua plug-in in Lightroom Classic  ->  Adobe SDK  ->  allowlisted (test) catalog
```
Rules in one line each: offline and loopback only; originals are never overwritten (virtual copies, journal, undo);
test catalog only until the owner says otherwise; no tool decides what an edit should be; skills hold knowledge, not
recipes; the agent never approves its own actions.

## Keeping these docs true
`TOOLS.md` and `ERRORS.md` are generated from the code (`poetry run python scripts/gen_docs.py`) and a test fails if
they are stale. The rest is hand-written: update the matching document in the same change that alters the behaviour,
and record status and decisions in `PROGRESS.md`.
