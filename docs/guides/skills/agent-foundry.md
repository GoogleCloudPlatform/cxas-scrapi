---
title: CX Agent Studio Skill
description: Overview of the cx-agent-studio router skill and how it routes to workflow references.
---

# CX Agent Studio Skill

`cx-agent-studio` is the main skill for CX Agent Studio work. It replaces the
older `cxas-agent-foundry` and `cxas-sim-eval` skills with one router that
loads focused references for setup, build, run, debug, and simulation-eval
conversion.

The page path is preserved for existing docs links, but the Gemini CLI command
is now:

```
/cx-agent-studio
```

---

## Invoking the skill

In [Claude Code](https://code.claude.com/docs/en/overview), the skill is
automatically triggered when the AI detects relevant intent, such as building,
testing, converting evals, or debugging a CX agent.

You can also describe the task conversationally:

```
I want to build a new CX Agent Studio agent for handling billing questions
```

In [Gemini CLI](https://geminicli.com/docs/get-started/installation/), invoke:

```
/cx-agent-studio
```

---

## What happens when you invoke it

The skill starts with an environment readiness check:

1. Checks for `.venv/`.
2. Checks `.active-project` and `gecx-config.json`.
3. Checks whether local `cxas_app/` content exists.
4. Routes the request to the matching reference.

If setup is missing, it loads the setup reference first. Otherwise it routes
directly to the requested workflow.

---

## Intent routing

| User intent | Routes to |
|-------------|-----------|
| "Set this project up", "connect to an app" | Setup reference |
| "Build a new agent", "add a tool", "create an eval" | [Build skill](build.md) |
| "Run evals", "what's the pass rate?", "test the agent" | [Run skill](run.md) |
| "Evals are failing", "fix the instruction", "debug this failure" | [Debug skill](debug.md) |
| "Convert golden evals to simulations" | Simulation-evals reference inside the skill |

The routing is done by the AI from the request intent. If the intent is
ambiguous, the skill asks whether you want to build/create, run, debug, or
convert evaluations.

---

## Shared scripts

The skill includes hook and helper scripts in
`.agents/skills/cx-agent-studio/scripts/`:

| Script | Purpose |
|--------|---------|
| `scripts/hooks/pre-agent-push-lint.sh` | Runs `cxas lint` before pushing |
| `scripts/hooks/pre-agent-push.sh` | Checks for platform drift before pushing |
| `scripts/hooks/post-agent-update.sh` | Syncs local files after platform updates |
| `scripts/simulation-evals/*` | Converts and runs simulation evals generated from CXAS goldens |

These scripts are registered through `.claude/settings.json` and
`.gemini/settings.json`.

---

## The `gecx-config.json` role

The skill reads `gecx-config.json` to understand your environment:

```json
{
  "gcp_project_id": "my-gcp-project",
  "location": "us",
  "app_name": "My Support Agent",
  "deployed_app_id": null,
  "model": "gemini-3.1-flash-live",
  "modality": "text"
}
```

All routed references share this configuration. If the config is missing or
incomplete, the setup route walks you through filling it in before continuing.
