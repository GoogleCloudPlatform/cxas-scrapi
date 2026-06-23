# cxas-scrapi

This repository is a workspace and SDK for building and managing GECX (Google Customer Engagement Suite) conversational agents.

## Repository Structure

```
cxas-scrapi/                    # SDK source code
.agents/skills/                 # Collection of reusable agent skills
├── cxas-agent-foundry/         # Composite skill for end-to-end agent lifecycle
├── cxas-sim-eval/              # Skill for converting evals
└── ...
<project_name>/                 # (Optional) App-specific agent workspaces managed by skills (e.g., cymbal/)
.venv/                          # Shared virtual environment
AGENTS.md                       # Workspace overview (this file)
.active-project                 # (Optional) Points to the currently active project folder
```

## Setup

Run the setup script to create a virtual environment and install the `cxas-scrapi` SDK from the local source:

```bash
.agents/skills/cxas-agent-foundry/scripts/setup.sh          # Full setup (install + configure)
.agents/skills/cxas-agent-foundry/scripts/setup.sh --configure  # Reconfigure only
```

Requires Python 3.10+ and [astral-uv](https://docs.astral.sh/uv/getting-started/installation/).
  - Always execute `cxas` commands using `uv run cxas` instead of using .venv/.

## Available Skills

This workspace provides several specialized AI skills to assist with development. 

- **`cxas-agent-foundry`**: The primary skill for the end-to-end GECX agent lifecycle. Use this for building agents from PRDs, generating and running evals, debugging failures, and syncing code.
- **`cxas-sim-eval`**: A utility skill for converting CXAS golden evaluations to SCRAPI SimulationEvals test cases.

## CLI Features

- **`cxas llm-lint`**: An AI-driven semantic linter for GECX sub-agent instructions. It reviews natural language rules and style guidelines using Gemini for a single sub-agent at a time. Run `cxas llm-lint --help` for details.
- **`cxas trace search`**: Find conversations whose transcript contains a query, mirroring the console search box. Uses the server-side `ces_transcript.search(...)` full-text function (case-insensitive, substring/prefix; searches the user + agent transcript only). Use `--match {phrase,all,any}` for multi-word handling and `--snippets` to show highlighted excerpts. Example: `cxas trace search "app crashing" --app-name <app> --match all --snippets`. Run `cxas trace search --help` for details.

*Note: For detailed development workflows, linter policies, and GECX-specific conventions, refer to the documentation within the respective skills (e.g., `.agents/skills/cxas-agent-foundry/SKILL.md`).*


