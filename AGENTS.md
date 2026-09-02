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
- **`cxas-autolabel-rules`**: Author, validate, and manage Contact Center AI (CCAI) Insights Autolabeling Rules declaratively via YAML and CEL.
- **`cxas-configurable-dashboards`**: Author, validate, and manage CCAI Insights Configurable Dashboards declaratively via YAML, Vega-Lite specs, and SQL metrics queries.
- **`cxas-sim-eval`**: A utility skill for converting CXAS golden evaluations to SCRAPI SimulationEvals test cases.

## CLI Features

- **`cxas help`**: Interactive terminal help documentation detailing all available CLI commands and sub-commands. Example: `cxas help llm-lint`.
- **`cxas lint`**: Fast, deterministic static structural linter across 60+ structural, configuration, callback naming, and CES schema checks. Run this continuously while coding or in Git pre-commit hooks to ensure absolute structural validity before deployment.
- **`cxas llm-lint`**: An AI-driven semantic prompt linter for GECX sub-agent instructions. Uses Gemini to deeply review natural language rules, tone, persona consistency, and dynamic instructions (`before_agent_callbacks`) for a single sub-agent at a time. Run this when authoring prompts or preparing qualitative reviews. Example: `cxas llm-lint --agent-dir <dir>`.
- **`cxas pull`**: Export a platform CES conversational agent to local declarative files (`app.json`, `agents/`, `tools/`, `callbacks/`). Supports exporting the live draft or a specific immutable version snapshot via `--version-id <version>` (accepts display names like `v1.0`, bare IDs like `001`, or full resource paths). Example: `cxas pull <app-name> --version-id v1.0 --target-dir cxas_app/`.
- **`cxas trace search`**: Find conversations whose transcript contains a query, mirroring the console search box. Uses the server-side `ces_transcript.search(...)` full-text function (case-insensitive, substring/prefix; searches the user + agent transcript only). Use `--match {phrase,all,any}` for multi-word handling and `--snippets` to show highlighted excerpts. Example: `cxas trace search "app crashing" --app-name <app> --match all --snippets`. Run `cxas trace search --help` for details.

*Note: For detailed development workflows, linter policies, and GECX-specific conventions, refer to the documentation within the respective skills (e.g., `.agents/skills/cxas-agent-foundry/SKILL.md`).*
