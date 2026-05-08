---
name: cx-agent-studio
description: End-to-end CX Agent Studio, GECX, CES, and SCRAPI agent development skill. Use when building, configuring, linting, pulling, pushing, evaluating, debugging, or iterating conversational agents, voice/audio agents, tools, callbacks, guardrails, sessions, app instructions, TDDs, golden evals, simulation evals, tool tests, callback tests, or converting CXAS golden evaluations into SCRAPI SimulationEvals.
---

# CX Agent Studio

Router skill for CX Agent Studio agent lifecycle work. Keep this file loaded as
the always-on control plane, then load only the reference that matches the
task.

## Before Routing

Check these signals in order:

1. `.venv/` exists.
2. `.active-project` exists and points to a project with `gecx-config.json`.
3. A project contains `cxas_app/` content.

| Signal | Action |
| --- | --- |
| Missing `.venv/` or config | Load `references/setup.md` before anything else. |
| Config exists, no `cxas_app/` content | Route normally; this is a new project or empty local checkout. |
| All signals exist | Route normally. |

For build, run, and debug workflows, initialize the task's `<project>/todo.md`
from the checklist in the routed reference before doing the work. The checklist
is the execution contract.

## Commands

| Command | Use for | Load |
| --- | --- | --- |
| `setup [target]` | First-time install, configuration, existing app connection | `references/setup.md` |
| `build [request]` | New agents/apps, PRD-to-agent, TDDs, eval authoring, tool/callback changes, instruction edits | `references/build.md` |
| `run [target]` | Running goldens, simulations, tool tests, callback tests, reports, pass-rate checks | `references/run.md` |
| `debug [failure]` | Failing evals/tests, regressions, pass-rate recovery, scoring threshold issues | `references/debug.md` |
| `sim-eval [target]` | Convert CXAS golden evaluations to SCRAPI SimulationEvals | `references/simulation-evals.md` |
| `convert-evals [target]` | Alias for golden-to-simulation conversion | `references/simulation-evals.md` |

## Routing Rules

1. No argument: show the command table grouped by setup, build, run, debug, and
   simulation-eval conversion. Ask what the user wants to do.
2. First word matches a command: load that reference and follow it. Everything
   after the command is the target or request.
3. Natural-language request: infer the route from intent, then load exactly one
   primary reference.

Route creating, setting up, or editing an agent/app to `references/build.md`,
even when the wording sounds like a small shell-only task. Route "run evals",
"check pass rate", and "generate a report" to `references/run.md`. Route
"why did this fail", "fix failing evals", and "get to 90%" to
`references/debug.md`. Route golden-to-simulation conversion to
`references/simulation-evals.md`.

If intent is unclear, ask: "Are you looking to build/create, run, debug, or
convert evaluations?"

## Common Commands

```bash
# Lint: dispatch agents/lint-fixer.md as a sub-agent. Do not run verbose lint
# on the main thread unless no sub-agent tool exists.

cxas push --app-dir <project>/cxas_app/<AppName> \
  --to projects/<project_id>/locations/<location>/apps/<app_id> \
  --project-id <project_id> --location <location>

cxas pull projects/<project_id>/locations/<location>/apps/<app_id> \
  --project-id <project_id> --location <location> \
  --target-dir <project>/cxas_app/

python .agents/skills/cx-agent-studio/scripts/run-and-report.py --message "what changed" --runs 5
python .agents/skills/cx-agent-studio/scripts/gate-check.py
python .agents/skills/cx-agent-studio/scripts/inspect-app.py
python .agents/skills/cx-agent-studio/scripts/triage-results.py --last 3
python .agents/skills/cx-agent-studio/scripts/app-thresholds.py show

.agents/skills/cx-agent-studio/scripts/setup.sh
python .agents/skills/cx-agent-studio/scripts/setup-project.py
```

Use `gate-check.py` when the user is about to push, finished building, or wants
a verification pass. Use `inspect-app.py` only for a quick architecture look.

## Sub-Agents

For heavy diagnosis or generation work, dispatch the matching prompt from
`agents/` and pass the inputs listed inside that file.

| Sub-agent | Use for |
| --- | --- |
| `agents/triage-failure.md` | Diagnose one failing eval. |
| `agents/tdd-writer.md` | Draft or reverse-engineer a TDD. |
| `agents/scaffolder.md` | Generate app files from an approved TDD. |
| `agents/coverage-analyst.md` | Produce eval coverage analysis. |
| `agents/eval-writer.md` | Generate an eval type from the TDD. |
| `agents/lint-fixer.md` | Run `cxas lint` and fix errors/warnings until clean. |

For eval execution there is no sub-agent. Use
`scripts/run-and-report.py --json-summary <path> > /dev/null 2>&1`, then read
the summary file. See `references/debug.md`.

## Memory

Before touching a project, check memory for project-specific app IDs, variable
rules, audio scoring workarounds, and prior debugging context. If missing and
needed, ask the user.
