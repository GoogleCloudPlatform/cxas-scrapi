---
title: Hooks Reference
description: Reference for pre-agent-push-lint.sh, pre-agent-push.sh, and post-agent-update.sh hooks.
---

# Hooks Reference

SCRAPI installs three shell script hooks when you run `cxas init`. These hooks integrate with Claude Code's and Gemini CLI's tool execution framework to run automatically at key points in the development loop — before a push, after an update — without you having to remember to run them manually.

---

## Hook overview

| Hook | Registered for | What it does |
|------|----------------|-------------|
| `pre-agent-push-lint.sh` | Before `cxas push` | Runs `cxas lint` and blocks on errors |
| `pre-agent-push.sh` | Before `cxas push` | Detects drift between local and platform, blocks if stale |
| `post-agent-update.sh` | After agent updates | Pulls fresh state and syncs callbacks |

All three hooks live in `.agents/skills/cxas-agent-foundry/scripts/hooks/`.

---

## How hooks work

The hook scripts are designed to work with both Claude Code and Gemini CLI. They:

1. Read JSON input from stdin to determine which agent is calling and what command is being run
2. Only activate when the command matches (e.g., `cxas push` for pre-push hooks)
3. Return structured JSON output to allow or block the operation

---

## `pre-agent-push-lint.sh`

**Purpose:** Run the linter before every push and block if there are errors.

**When it runs:** Before any `cxas push` command executed by the AI assistant.

**Effect:** If there are any `error`-severity lint findings, the push is blocked. The AI will see the lint output and typically fix the errors before retrying the push. The hook runs `cxas lint --json` and parses the results to count errors.

**Bypassing:** If you need to push despite lint errors (not recommended), you can run `cxas push` directly from your terminal, bypassing the hook. Or temporarily disable the hook (see below).

---

## `pre-agent-push.sh`

**Purpose:** Detect drift between your local files and the current platform state before pushing.

This hook compares your local `cxas_app/` directory against the current platform state by pulling a fresh copy to a temporary directory and diffing them. If the platform has changed since you last pulled (i.e., someone else pushed, or you made changes via the platform console), this hook blocks the push.

It also validates that the push target matches the `deployed_app_id` in `gecx-config.json`.

**When it runs:** Before any `cxas push` command executed by the AI assistant.

**Effect:** Blocks the push if drift is detected. The AI sees the blocking message and typically asks you to pull first to merge platform changes before retrying the push.

---

## `post-agent-update.sh`

**Purpose:** Automatically sync local files after an agent update to keep local state consistent.

This hook triggers after `update_agent` commands and does two things:

1. Pulls the latest agent state from the platform to your local `cxas_app/` directory
2. Syncs callbacks from the platform to your local `evals/callback_tests/` directory

**When it runs:** After any agent update command executed by the AI assistant.

**Effect:** Ensures your local `cxas_app/` directory always reflects the true platform state, including any normalizations the platform applies during import. Also reminds the AI to run callback tests and update the TDD changelog.

---

## How hooks are registered

Hooks are registered in `.claude/settings.json` (for Claude Code) and `.gemini/settings.json` (for Gemini CLI) using their tool execution hook mechanisms.

### Claude Code (`.claude/settings.json`)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push.sh",
            "timeout": 30
          },
          {
            "type": "command",
            "command": ".agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push-lint.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".agents/skills/cxas-agent-foundry/scripts/hooks/post-agent-update.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### Gemini CLI (`.gemini/settings.json`)

Similar configuration, using Gemini CLI's `BeforeTool`/`AfterTool` hooks with the `run_shell_command` matcher. Hook scripts use `$GEMINI_PROJECT_DIR` to resolve paths.

---

## Disabling a hook

If you want to temporarily disable a hook (e.g., you're doing a bulk update and don't want to wait for drift checks on every push), the simplest way is to rename the file:

```bash
mv .agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push.sh \
   .agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push.sh.disabled
```

The hook framework only executes files that match the expected name. Restore it when you're done:

```bash
mv .agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push.sh.disabled \
   .agents/skills/cxas-agent-foundry/scripts/hooks/pre-agent-push.sh
```

!!! tip "Bypassing for a single push"
    If you want to bypass hooks for just one push (not disable them permanently), run `cxas push` directly from your terminal instead of asking the AI to push. The hooks only run when the AI executes the command.

---

## Customizing hooks

You can edit the hook scripts to add your own logic. Since hooks are plain shell scripts that read JSON from stdin and output JSON, you have full flexibility to add any logic that makes sense for your team.

The hooks use `resolve-project.sh` to locate the active project directory and `gecx-config.json` to read project settings (using `jq` to parse the `gcp_project_id`, `location`, and `deployed_app_id` fields).
