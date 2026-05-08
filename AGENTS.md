# cxas-scrapi

This repository is a workspace and SDK for building and managing GECX (Google Customer Engagement Suite) conversational agents.

## Repository Structure

```
cxas-scrapi/                    # SDK source code
.agents/skills/                 # Collection of reusable agent skills
└── cx-agent-studio/            # Router skill for the end-to-end agent lifecycle
└── ...
<project_name>/                 # (Optional) App-specific agent workspaces managed by skills (e.g., cymbal/)
.venv/                          # Shared virtual environment
AGENTS.md                       # Workspace overview (this file)
.active-project                 # (Optional) Points to the currently active project folder
```

## Setup

Run the setup script to create a virtual environment and install the `cxas-scrapi` SDK from the local source:

```bash
.agents/skills/cx-agent-studio/scripts/setup.sh          # Full setup (install + configure)
.agents/skills/cx-agent-studio/scripts/setup.sh --configure  # Reconfigure only
source .venv/bin/activate
```

Requires Python 3.

## Available Skills

This workspace provides a specialized AI skill to assist with development.

- **`cx-agent-studio`**: The primary skill for the end-to-end GECX agent lifecycle. Use this for building agents from PRDs, generating and running evals, converting golden evals to SCRAPI SimulationEvals, debugging failures, and syncing code.

*Note: For detailed development workflows, linter policies, and GECX-specific conventions, refer to `.agents/skills/cx-agent-studio/SKILL.md` and its `references/` files.*
