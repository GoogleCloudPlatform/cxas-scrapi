---
name: cxas-agent-foundry
description: End-to-end GECX/CXAS/CES conversational agent lifecycle -- build agents from requirements (PRD-to-agent), create and run evals (goldens, simulations, tool tests, callback tests), debug failures, and iterate to production quality. Use this skill whenever the user mentions GECX, CXAS, CES, SCRAPI, conversational agents, voice agents, audio agents, agent evals, pushing/pulling/linting agents, or agent instructions/callbacks/tools on the Google Customer Engagement Suite platform.
---

# Agent Foundry

End-to-end lifecycle for GECX conversational agents: build, test, debug, iterate.

## Step tracking — MANDATORY (Phase 0, blocking)

**Before doing ANY work — including running setup, asking questions, or scaffolding files — initialize `<project>/todo.md` from the relevant sub-skill's checklist (verbatim).** The checklist is a contract, not a suggestion. If `todo.md` doesn't exist for the current task, refuse to proceed and create it first.

Long debug/build runs skip verification steps under pressure (e.g., pushing without linting, scaffolding without a TDD, claiming "deployed" without actually pushing). The checklist exists because of this. **The instinct to skip a step is the moment the checklist earns its keep — that's when you must consult it, not the moment to bypass it.**


## Quick Reference

```bash
# Lint: dispatch agents/lint-fixer.md sub-agent — DO NOT run `cxas lint` on the main thread.
# Lint output is verbose; keep it inside the sub-agent context.

# Push local files to platform (only after lint-fixer returns status: clean)
cxas push --app-dir <project>/cxas_app/<AppName> \
  --to projects/<project_id>/locations/<location>/apps/<app_id> \
  --project-id <project_id> --location <location>

# Pull platform state to local
cxas pull projects/<project_id>/locations/<location>/apps/<app_id> \
  --project-id <project_id> --location <location> --target-dir <project>/cxas_app/

# Run evals + triage + report (single command)
python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "what changed" --runs 5

# Inspect app architecture
python .agents/skills/cxas-agent-foundry/scripts/inspect-app.py

# Triage failures
python .agents/skills/cxas-agent-foundry/scripts/triage-results.py --last 3

# Run all 6 build-verification gates against the deployed app
python .agents/skills/cxas-agent-foundry/scripts/gate-check.py

# Tune scoring thresholds (similarity, hallucination, extra-tools)
python .agents/skills/cxas-agent-foundry/scripts/app-thresholds.py show

# Sync callback Python code into evals/callback_tests/agents/ + create test.py symlinks.
# Required for tests to be discoverable by test_all_callbacks_in_app_dir.
python .agents/skills/cxas-agent-foundry/scripts/sync-callbacks.py                  # post-push: pull from platform
python .agents/skills/cxas-agent-foundry/scripts/sync-callbacks.py --from-local <app_dir>  # pre-push: copy from local app dir

# Cold-start setup (first-time only — venv + project bootstrap)
.agents/skills/cxas-agent-foundry/scripts/setup.sh
python .agents/skills/cxas-agent-foundry/scripts/setup-project.py
```

**Disambiguation:** `gate-check.py` and `inspect-app.py` overlap on "show me the architecture" but `gate-check.py` is the answer whenever the user is about to push, finished building, or wants a verification pass. `inspect-app.py` is for a quick "what's in here" look without the verification gates. When in doubt, use `gate-check.py`.

## Sub-agents

For heavy diagnosis/analysis work that would otherwise burn main-thread context, dispatch one of these sub-agents via the `Agent` tool. Pass the contents of the relevant `.md` file as the prompt, then add the inputs the file lists.

| Sub-agent | Reasoning intensity | When to use |
|---|---|---|
| `agents/triage-failure.md` | HIGH | Diagnose ONE failing eval. Fan out for the top 5 failures by category priority in parallel. Iterate on more after the first batch returns. |
| `agents/tdd-writer.md` | HIGH | Reverse-engineer a TDD from an existing agent OR draft from PRD. Returns the TDD + open-questions handoff; main thread runs the show/ask/iterate loop with the user (sub-agents can't ask). |
| `agents/scaffolder.md` | MEDIUM | Bulk-generate all agent code (agent JSONs, instruction.txt, tool python_code, callbacks, app.json) from an APPROVED TDD. One dispatch replaces 30-60 main-thread file writes. |
| `agents/coverage-analyst.md` | MEDIUM | Generate a full eval coverage report against an agent's architecture. |
| `agents/eval-writer.md` | MEDIUM | Generate evals for one entire eval TYPE (all goldens, all sims, etc.) — reads TDD's Coverage Map itself. Max 4 dispatches per build. |
| `agents/lint-fixer.md` | LOW (mechanical) | Run `cxas lint` and mechanically fix all errors + deterministic warnings until clean. Never run lint on main thread. |

For running evals: there is no sub-agent. Use `scripts/run-and-report.py --json-summary <path> > /dev/null 2>&1` and read the summary file — see `references/debug.md` → "Quick Start". The work was deterministic, so it lives in the script.

**Reasoning intensity** is a hint to the runtime: HIGH sub-agents benefit from more thinking budget / a stronger model, LOW sub-agents are recipe-driven and don't. Each sub-agent file repeats this hint at the top with a one-line justification.

## Environment Readiness Check (run BEFORE routing)

Before routing to any sub-skill, check these signals in order:

1. **Virtualenv exists?** -- Check if `.venv/` directory exists
2. **Config exists?** -- Check if `.active-project` file exists and the referenced `<project>/gecx-config.json` exists
3. **Has built before?** -- Check if any `<project>/cxas_app/` directory has content

| Signal | Action |
|--------|--------|
| No `.venv/` or no config | **First-time setup needed.** Load `references/setup.md` before doing anything else. |
| `gecx-config.json` exists but no `cxas_app/` content | Returning user, new project. Route normally. |
| All exist | Returning user. Route normally. |

## Detect Intent and Route

Read what the user wants and load the appropriate sub-skill:

| User says... | Phase | Load |
|-------------|-------|------|
| "Build me an agent from this PRD" | Build | `references/build.md` |
| **"Create a new cxas app", "Make a new agent", "Set up an agent", "I wanna build an agent"** | **Build** | **`references/build.md`** |
| "Create evals for my agent" | Build | `references/build.md` |
| "Generate tool tests", "create callback tests" | Build | `references/build.md` |
| "Update evals -- requirements changed" | Build | `references/build.md` |
| "Update the TDD" | Build | `references/build.md` |
| "Run evals", "push evals", "check results" | Run | `references/run.md` |
| "Run tool tests", "test the callbacks" | Run | `references/run.md` |
| "Generate a report" | Run | `references/run.md` |
| "Why is this eval failing", "get to 90%" | Debug | `references/debug.md` |
| "Fix the failing evals", "debug the agent" | Debug | `references/debug.md` |
| "Tool test is failing", "callback test broke" | Debug | `references/debug.md` |
| **"Edit the agent's instructions", "tweak the auth tool", "fix the greeting", "update this callback"** | **Build** (Edit cycle) | **`references/build.md` → "Editing an Existing Agent"** |

**Any phrasing that implies creating, building, or setting up an agent/app routes to `references/build.md` — even if it sounds like "just create the app shell."** "Create a new cxas app" is NOT a shortcut to scaffolding; it triggers the full build flow (todo.md → interview/PRD → TDD + approval → scaffold → lint → evals → push). Skipping the interview / TDD because the user said "create" instead of "build" is a routing failure.

**Editing an existing agent** (instruction tweak, tool change, callback fix) routes to build.md's "Editing an Existing Agent" section — the standard pull → edit → lint → push → run-evals cycle. Don't skip lint or the eval run after — silent regressions are how 90% rates drop to 70%.

If the intent is unclear, ask: "Are you looking to **build/create** evals, **run** them, or **debug** failures?"

## Before Starting

Check memory for project-specific context (app ID, variable handling rules, audio scoring workarounds). If not available, ask the user.
