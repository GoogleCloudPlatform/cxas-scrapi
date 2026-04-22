# cxas-scrapi

This repository is a workspace and SDK for building and managing GECX (Google Customer Engagement Suite) conversational agents.

## Repository Structure

```
cxas-scrapi/                    # SDK source code
.agents/skills/                 # Collection of reusable agent skills
├── cxas-agent-foundry/         # Composite skill for end-to-end agent lifecycle
├── cxas_sim_eval/              # Skill for converting evals
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
source .venv/bin/activate
```

Requires Python 3.

## Available Skills

This workspace provides several specialized AI skills to assist with development. 

- **`cxas-agent-foundry`**: The primary skill for the end-to-end GECX agent lifecycle. Use this for building agents from PRDs, generating and running evals, debugging failures, and syncing code.
- **`cxas_sim_eval`**: A utility skill for converting CXAS golden evaluations to SCRAPI SimulationEvals test cases.

*Note: For detailed development workflows, linter policies, and GECX-specific conventions, refer to the documentation within the respective skills (e.g., `.agents/skills/cxas-agent-foundry/SKILL.md`).*