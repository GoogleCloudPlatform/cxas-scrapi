---
title: CLI Quickstart
description: Manage your CX Agent Studio agents from the terminal in five practical steps.
---

# CLI Quickstart

The `cxas` command-line tool is your day-to-day companion for managing CX Agent Studio agents. This quickstart takes you through five practical tasks: discovering your Apps, pulling agent configs to disk, linting them for issues, pushing changes back to the platform, and running tool tests.

By the end, you'll understand the core pull → lint → edit → push workflow that most SCRAPI users follow.

---

## Prerequisites

Before you start, make sure you have:

- [x] SCRAPI installed: `pip install cxas-scrapi`
- [x] Authentication configured (see [Authentication](authentication.md))
- [x] A GCP project with at least one CX Agent Studio App

Verify that the CLI is available:

```sh
cxas --help
```

You should see a list of available subcommands. If you see `command not found`, check that your virtual environment is activated and SCRAPI is installed.

---

## Task 1 — Discover your Apps

Before you can work with an agent, you need to know what Apps exist in your project. Use `cxas apps list` to see them:

```sh
cxas apps list --project-id my-gcp-project --location us
```

**Example output:**

```
Apps:
 Display Name                  Name
 My Customer Service Bot       projects/my-gcp-project/locations/us/apps/abc123
 Internal HR Assistant         projects/my-gcp-project/locations/us/apps/def456
```

!!! tip "What's an App ID?"
    The short App ID (`abc123`) is the last part of the full resource name `projects/.../locations/.../apps/abc123`. Commands such as `apps get` and `pull` accept the full resource name or the app's display name.

If you already know the app's display name and want its details:

```sh
cxas apps get --project-id my-gcp-project --location us app-name
```

---

## Task 2 — Pull an App's configuration

Once you know your App ID, use `cxas pull` to download the agent configuration to your local machine:

```sh
cxas pull projects/my-gcp-project/locations/us/apps/abc123
```

This creates a local directory structure that mirrors the agent's configuration on the platform:

```
./My_Customer_Service_Bot/
├── app.json
├── agents/
│   └── main-agent/
│       ├── main-agent.json
│       └── instruction.txt
├── tools/
│   └── check_order_status/
│       └── check_order_status.json
└── guardrails/
```

Each JSON file represents a resource from the platform. This is your local "source of truth" — the files you'll edit, lint, version control, and push back.

Change into the exported app directory containing `app.json` before continuing. Use the directory name created by your export.

!!! info "The core workflow"
    The SCRAPI workflow is: **create resources on the platform → pull them locally → edit them → lint → push**. Start by creating resources on the platform, then pull them down to edit.

---

## Task 3 — Lint your configuration

Before making any changes (or as part of your CI pipeline), run the linter to check the pulled configuration against SCRAPI's 60+ best-practice rules:

```sh
cxas lint
```

SCRAPI looks for an optional `cxaslint.yaml` file in your current directory to find the agent configs to lint. Pull does not create this file. If you're in the directory created by `cxas pull`, it should just work.

**Example output (clean):**

```
Linting agent configuration...
============================================================
Lint Results
============================================================
  No issues found! Your configuration looks great.
============================================================
```

**Example output (with issues):**

```
Linting agent configuration...
============================================================
Lint Results
============================================================
  [WARNING] tools/check_order_status/python_function/python_code.py [T002] Tool is missing a docstring.
  [ERROR]   agents/main-agent/instruction.txt [I001] Missing required XML tag: <role>.
============================================================
2 issues found (1 error, 1 warning)
```

Errors (marked `[ERROR]`) indicate things you should fix before pushing. Warnings are gentler suggestions worth reviewing.

!!! tip "Configuring the linter"
    You can enable, disable, or configure individual rules in `cxaslint.yaml`. See the [Linting Guide](../guides/linting/index.md) for full details.

---

## Task 4 — Make a change and push

Let's say you want to update a tool description. Open the relevant JSON file and edit the description, keeping the other fields:

```json
{
  "displayName": "Check Order Status",
  "description": "Looks up the current status and estimated delivery date for a given order ID."
}
```

Once you're happy with your changes, run the linter one more time to make sure everything is clean:

```sh
cxas lint
```

Then push the changes back to the platform:

```sh
cxas push --to projects/my-gcp-project/locations/us/apps/abc123
```

**Example output:**

```
Pushing app from ....
Pushing to existing app... Overwriting if supported.
Uploading to CES...
Waiting for import to complete...
Successfully pushed to: projects/my-gcp-project/locations/us/apps/abc123
```

Your changes are now live on the platform.

!!! warning "Lint before you push"
    It's a good habit to always run `cxas lint` before `cxas push`. Pushing a configuration with errors can cause your agent to behave unexpectedly. In CI pipelines, SCRAPI can enforce this automatically.

---

## Task 5 — Run tool tests

After pushing changes, use `cxas test-tools` to verify that your tools behave correctly:

```sh
cxas test-tools --app-name projects/my-gcp-project/locations/us/apps/abc123 --test-file tests/tool_tests.yaml
```

Use the path to your local [tool test file](../guides/evaluation/tool-tests.md). This runs your tool evaluation test cases against the live platform and reports the results:

**Example output:**

```
Running tool tests for App: projects/my-gcp-project/locations/us/apps/abc123

  [PASS] check_order_status: basic lookup
  [PASS] check_order_status: invalid order ID returns error
  [PASS] lookup_customer: returns customer record
  [FAIL] lookup_customer: handles missing email gracefully

============================================================
Results: 3 passed, 1 failed
============================================================
```

If any tests fail, the output will include details about what was expected versus what was returned, making it easier to diagnose the issue.

---

## The full workflow at a glance

Here's the complete pull → lint → edit → push → test loop:

```sh
# 1. See your Apps
cxas apps list --project-id my-project --location us

# 2. Pull configuration locally
cxas pull projects/my-project/locations/us/apps/abc123

# Use the exported app directory (replace with its actual name)
cd My_Customer_Service_Bot

# 3. Check for issues
cxas lint

# 4. ... edit your JSON files ...

# 5. Lint again to confirm all clear
cxas lint

# 6. Push to platform
cxas push --to projects/my-project/locations/us/apps/abc123

# 7. Run tests
cxas test-tools --app-name projects/my-project/locations/us/apps/abc123 --test-file tests/tool_tests.yaml
```

---

## Other commands worth knowing

| Command | What it does |
|---|---|
| `cxas create` | Create a new app on the platform |
| `cxas delete` | Delete an app from the platform |
| `cxas branch` | Create a branch of your App for safe experimentation |
| `cxas run-session text APP_RESOURCE` | Run a full conversation through the agent interactively |
| `cxas test-callbacks` | Run callback (webhook) evaluation tests |
| `cxas push-eval` | Upload evaluation test cases to the platform |
| `cxas export` | Export an evaluation to YAML or JSON |
| `cxas init` | Initialize a new SCRAPI project in the current directory |
| `cxas init-github-action` | Generate a GitHub Actions workflow file |
| `cxas insights` | Fetch and display conversation insights |

---

## What's next?

- Read the [CLI Reference](../cli/index.md) for full documentation of every command and flag
- Learn about the [Key Concepts](concepts.md) behind Apps, Agents, and the resource hierarchy
- Set up [AI Skills](../guides/skills/index.md) for AI-assisted agent development
- Explore the [Linting Guide](../guides/linting/index.md) to customize your lint rules
