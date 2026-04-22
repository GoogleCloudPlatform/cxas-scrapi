---
name: cxas-agent-foundry
description: End-to-end GECX/CXAS/CES conversational agent lifecycle -- build agents from requirements (PRD-to-agent), create and run evals (goldens, simulations, tool tests, callback tests), debug failures, and iterate to production quality. Use this skill whenever the user mentions GECX, CXAS, CES, SCRAPI, conversational agents, voice agents, audio agents, agent evals, pushing/pulling/linting agents, or agent instructions/callbacks/tools on the Google Customer Engagement Suite platform.
---

# Agent Foundry

End-to-end lifecycle for GECX conversational agents: build, test, debug, iterate.

## Quick Reference

```bash
# Lint before push (catches structural issues early)
cxas lint --app-dir <project>/cxas_app/<AppName>

# Push local files to platform
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
```

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

If the intent is unclear, ask: "Are you looking to **build/create** evals, **run** them, or **debug** failures?"

## Step Tracking

**CRITICAL:** Before starting any multi-step workflow (Build, Run, or Debug), you MUST create a `todo.md` checklist in the project workspace. 

As you complete each step in the workflow, update the checklist to check it off (`[ ]` to `[x]`). Do not proceed to the next step until the current one is checked off. This ensures no steps are missed during complex builds or debug sessions.

## Before Starting

Check memory for project-specific context (app ID, variable handling rules, audio scoring workarounds). If not available, ask the user.
