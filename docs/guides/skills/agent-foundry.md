---
title: Agent Foundry Skill
description: Overview of the cxas-agent-foundry composite skill and how it routes to sub-skills.
---

# Agent Foundry Skill

`cxas-agent-foundry` is the main skill you'll interact with. It acts as an intelligent router, checking the current state of your environment and directing you to the right sub-skill for what you need to do.

Think of it as a senior engineer who knows the full development lifecycle — they'll assess where you are, ask clarifying questions, and then take over the appropriate next steps.

---

## Invoking the foundry

In [Claude Code](https://code.claude.com/docs/en/overview), the skill is automatically triggered when the AI detects relevant intent (e.g., building, testing, or debugging a CX agent). You can also reference it conversationally:

```
I want to build a new CX Agent Studio agent
```

In [Gemini CLI](https://geminicli.com/docs/get-started/installation/):

```
/cxas-agent-foundry
```

Or just describe what you want — if the AI has the skill loaded, it will route to the foundry when appropriate:

```
I want to build a new agent for handling billing questions
```

---

## What happens when you invoke it

The foundry runs an *environment readiness check* before doing anything else:

1. **Checks for `gecx-config.json`** — if it's missing, it walks you through creating it
2. **Checks credentials** — verifies ADC or OAuth token is available
3. **Checks for an existing app** — determines if this is a new project or an iteration on an existing one
4. **Checks for existing eval files** — determines where you are in the development lifecycle

Based on this check, the foundry presents an onboarding flow:

```
Environment check complete. Here's what I found:

  Project: my-gcp-project (us)
  App: "My Support Agent" — exists on platform
  Agents: 2 (support-root, billing-agent)
  Tool tests: 4 files, 12 tests
  Goldens: 2 files, 8 conversations
  Simulations: 1 file, 3 evals

What would you like to do?
  1. Build a new agent or add a new capability
  2. Run all evaluations and see the current pass rate
  3. Debug evaluation failures
```

---

## Intent routing

After the onboarding check, the foundry routes to one of three sub-skills based on your intent:

| User intent | Routes to |
|-------------|-----------|
| "Build a new agent", "Add a tool", "Create an eval" | [Build skill](build.md) |
| "Run evals", "What's the pass rate?", "Test the agent" | [Run skill](run.md) |
| "Evals are failing", "Fix the instruction", "Debug this failure" | [Debug skill](debug.md) |

The routing is done by the AI, not by a hard-coded decision tree. If your intent is ambiguous, the foundry will ask a clarifying question.

---

## Shared scripts

The foundry and its sub-skills share a set of hook scripts in `.agents/skills/cxas-agent-foundry/scripts/hooks/`:

| Script | Used by | Purpose |
|--------|---------|---------|
| `scripts/hooks/pre-agent-push-lint.sh` | All sub-skills, hooks | Runs `cxas lint` before pushing |
| `scripts/hooks/pre-agent-push.sh` | Build, Debug | Checks for platform drift before pushing |
| `scripts/hooks/post-agent-update.sh` | All sub-skills | Syncs local files after any platform update |

These scripts are registered with Claude Code's and Gemini CLI's hook frameworks (via `.claude/settings.json` and `.gemini/settings.json`) so they run automatically before/after relevant Bash commands.

---

## The `gecx-config.json` role

The foundry reads `gecx-config.json` at startup to understand your environment. All three sub-skills inherit this configuration. If the config is missing or incomplete, the foundry prompts you to fill it in before proceeding.

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

---

## Using the foundry iteratively

The power of the foundry is in the iterative loop:

```
You: I'd like to work on my agent evals

Foundry: [checks environment] Looks like you have 2 agents and some failing evals.
         What would you like to do?

You: Run the evals and tell me the pass rate

Foundry: [routes to Run skill, runs all evals, reports results]
         Current pass rate: 6/8 goldens passing (75%), 2 tool tests failing.
         Would you like me to debug the failures?

You: Yes, fix the instruction for the order lookup flow

Foundry: [routes to Debug skill, analyzes failures, proposes changes]
         I've updated the instruction. Pushing changes and re-running evals...
         Pass rate is now 8/8 goldens (100%). Done!
```

This loop — run, analyze, fix, re-run — is what the foundry is designed to facilitate.
