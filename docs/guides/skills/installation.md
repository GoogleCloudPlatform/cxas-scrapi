---
title: Installing Skills
description: Using cxas init to install AI skills into your project.
---

# Installing Skills

`cxas init` copies the bundled SCRAPI skills into your project and sets up the integration files for Claude Code and Gemini CLI. Run it once per project.

---

## Basic usage

```bash
cxas init
```

Run this from your project root. It creates the following structure:

```
your-project/
├── .agents/
│   └── skills/
│       ├── cxas-agent-foundry/
│       │   ├── SKILL.md
│       │   ├── references/          # Sub-skill definitions (build, run, debug)
│       │   ├── scripts/
│       │   │   └── hooks/           # Hook scripts for pre-push and post-update
│       │   │       ├── pre-agent-push-lint.sh
│       │   │       ├── pre-agent-push.sh
│       │   │       └── post-agent-update.sh
│       │   ├── hooks/               # Legacy hook scripts
│       │   └── assets/              # Project templates
│       └── cxas_sim_eval/
│           └── SKILL.md
├── .claude/
│   └── settings.json          # Registers hooks with Claude Code
├── .gemini/
│   └── settings.json          # Registers hooks with Gemini CLI
└── AGENTS.md                  # Overview for the AI assistant
```

---

## Flags

| Flag | Description |
|------|-------------|
| `--target-dir DIR` | Install into `DIR` instead of the current directory |
| `--force` | Overwrite existing files (by default, `cxas init` skips files that already exist) |

### Installing into a specific directory

```bash
cxas init --target-dir /path/to/my-agent-project
```

### Updating skills after a SCRAPI upgrade

When you upgrade `cxas-scrapi`, the bundled skills may have been updated. Use `--force` to overwrite the existing skill files:

```bash
pip install --upgrade cxas-scrapi
cxas init --force
```

`--force` overwrites skill files but does not overwrite `gecx-config.json` — your project configuration is preserved.

---

## Setting up `gecx-config.json`

After running `cxas init`, create `gecx-config.json` in your project root with your project details:

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

### Field reference

| Field | Required | Description |
|-------|----------|-------------|
| `gcp_project_id` | Yes | Your Google Cloud project ID |
| `location` | Yes | CX Agent Studio location (e.g., `us`) |
| `app_name` | Yes | The display name of your app |
| `deployed_app_id` | No | Full resource name of the deployment to use for simulations (`null` for new apps) |
| `model` | No | Gemini model for simulation evals (default: `gemini-3-flash` for text, `gemini-3.1-flash-live` for audio) |
| `modality` | No | `text` or `audio` (default: `text`) |

The `deployed_app_id` is set after the first push. Skills use it for Local Simulations and Turn Evals, which require a live deployment.

---

## What each installed file does

### `.agents/skills/`

Contains the skill definition files. Each `SKILL.md` is a Markdown file with YAML frontmatter:

```markdown
---
name: cxas-agent-foundry
description: Composite skill for building, running, and debugging CX Agent Studio agents
---

# CXAS Agent Foundry

You are an expert CX Agent Studio engineer...
```

The AI reads this file as part of its context when the skill is invoked. The foundry loads sub-skills (build, run, debug) from `references/` as needed.

### `.agents/skills/cxas-agent-foundry/scripts/hooks/`

Shell scripts that run at key points in the development loop. These are registered with the AI assistant's tool execution framework via `.claude/settings.json` and `.gemini/settings.json`.

- `pre-agent-push-lint.sh` — Runs `cxas lint` before every push
- `pre-agent-push.sh` — Checks for drift before every push
- `post-agent-update.sh` — Runs `cxas pull` after every platform update

### `.claude/settings.json`

Registers hooks with Claude Code:

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

### `.gemini/settings.json`

Similar configuration for Gemini CLI, using `BeforeTool`/`AfterTool` hooks with the `run_shell_command` matcher.

### `AGENTS.md`

A high-level overview file that the AI assistant reads to understand the project structure. It includes:

- What the project is about
- Which apps and agents exist
- Where the eval files live
- Key conventions (naming, structure)

You can edit `AGENTS.md` to add project-specific context that helps the AI make better decisions.

---

## Verifying the installation

After running `cxas init`, verify that Claude Code or Gemini CLI picks up the skills:

In Claude Code, the skill is automatically triggered when the AI detects relevant intent. Try asking:
```
I want to build a new CX agent
```

In Gemini CLI:
```
/cxas-agent-foundry
```

You should see the foundry skill respond with an environment readiness check and prompt for what you'd like to do.
