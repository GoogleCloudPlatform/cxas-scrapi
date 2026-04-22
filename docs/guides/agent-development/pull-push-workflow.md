---
title: Pull & Push Workflow
description: How to pull an app to local files, edit it, and push changes back to the platform.
---

# Pull & Push Workflow

The pull-push workflow is the core of how you edit agents with SCRAPI. This page explains each step in detail — what happens under the hood, what you can and can't change locally, and how to handle edge cases like the `--to` flag and drift.

---

## Overview

The platform is always the source of truth. When you run `cxas pull`, SCRAPI exports the app from the platform as a ZIP archive and unpacks it into a structured local directory. When you run `cxas push`, SCRAPI reads that directory and sends API calls to update each resource.

This means:

- You never edit resources by calling the API directly for every field — you edit files locally and push in one go
- The local directory is a snapshot. If someone else pushes a change while you're editing locally, your next push will overwrite their change (this is intentional — treat it like a Git branch)
- Not every file on disk corresponds to a separate API call. The agent JSON and instruction file together map to a single agent resource update.

---

## Pulling an app

```bash
cxas pull <app> [--target_dir DIR] [--project_id PROJECT] [--location LOCATION]
```

### Identifying the app

You can identify the app in two ways:

**By full resource name** (no `--project_id` or `--location` needed):

```bash
cxas pull projects/my-project/locations/us-central1/apps/my-app
```

**By display name** (requires `--project_id` and `--location`):

```bash
cxas pull "My Support Agent" \
  --project_id my-project \
  --location us-central1
```

Using the full resource name is slightly more robust — display names are case-sensitive and must be unique within a project.

### Target directory

By default, the app is pulled into a `cxas_app/<AppName>/` subdirectory inside your current working directory. You can change this with `--target_dir`:

```bash
cxas pull "My Support Agent" \
  --project_id my-project \
  --location us-central1 \
  --target_dir ./local-agents
```

This would create `./local-agents/My Support Agent/`.

### What gets downloaded

The pull downloads everything in the app:

- `app.json` — app-level settings including the root agent reference
- `agents/<name>/instruction.txt` — the agent's instruction text
- `agents/<name>/<name>.json` — agent configuration (tools, callbacks, child agents)
- `agents/<name>/before_model_callbacks/<cb>/python_code.py` — callback code
- `agents/<name>/after_model_callbacks/<cb>/python_code.py`
- (and so on for other callback types)
- `tools/<name>/<name>.json` — tool configuration
- `tools/<name>/python_function/python_code.py` — tool implementation
- `evaluations/` — platform golden YAML files
- `guardrails/` — guardrail configuration

---

## Editing locally

Once you've pulled, you can edit any file in the directory. Here's what each file type controls:

### `instruction.txt`

The most frequently edited file. This is the agent's natural language instruction — the text the LLM uses to decide what to do. The linter enforces the required XML structure (`<role>`, `<persona>`, `<taskflow>`).

Edit this directly in your text editor or IDE. Run `cxas lint` after editing to check for structural problems before pushing.

### `<agent_name>.json`

Controls which tools the agent can use, which callbacks are attached, and which child agents it can transfer to. Example:

```json
{
  "displayName": "Support Root Agent",
  "tools": ["lookup_order", "end_session"],
  "beforeModelCallbacks": [
    {
      "name": "inject_context",
      "pythonCode": "agents/support-root/before_model_callbacks/inject_context/python_code.py"
    }
  ],
  "childAgents": []
}
```

The `pythonCode` field is a relative path to the callback file. SCRAPI resolves this path at push time and sends the file contents to the platform.

### `python_code.py` (tools and callbacks)

Plain Python files. Edit them in your IDE with full syntax highlighting and linting. The linter rules T001-T011 and C001-C010 catch common mistakes before you push.

### `app.json`

App-level configuration including the root agent reference. You generally only edit this when changing the root agent.

---

## Pushing changes

```bash
cxas push <app_dir> [--to RESOURCE_NAME]
```

### Basic push

```bash
cxas push cxas_app/My\ Support\ Agent
```

SCRAPI reads the directory, determines which resources have changed, and sends the appropriate API calls. Resources that haven't changed are skipped (based on a diff against the platform state).

### The `--to` flag

The `--to` flag lets you push a local directory to a *different* app on the platform. This is the mechanism behind environment promotion:

```bash
# Push your local staging config to the production app
cxas push cxas_app/My\ Support\ Agent \
  --to projects/my-project/locations/us-central1/apps/my-app-production
```

This is particularly useful when you have a staging app and a production app with the same structure. You develop on staging, test, and then push the same local directory to production using `--to`.

!!! warning "The `--to` app must already exist"
    The target app must already exist on the platform. `cxas push --to` does not create a new app — it updates an existing one. If you want to clone an app, use `cxas branch` instead.

---

## Understanding drift

*Drift* happens when the platform state has changed since you last pulled. If you've been editing locally for a while, someone else may have pushed changes to the same app in the meantime.

SCRAPI's `pre-agent-push.sh` hook (used in the skills system) performs drift detection before a push. It compares the local app against the current platform state and warns you if they've diverged.

To handle drift manually:

1. Pull the latest platform state into a temporary directory
2. Compare with your local directory using `diff` or a git diff
3. Reconcile the differences
4. Push

```bash
# Pull the latest into a temp directory
cxas pull "My Support Agent" \
  --project_id my-project \
  --location us-central1 \
  --target_dir /tmp/platform-state

# Diff against your working copy
diff -r cxas_app/My\ Support\ Agent /tmp/platform-state/My\ Support\ Agent
```

---

## Recommended workflow with Git

Treating your local `cxas_app/` directory as a Git repository gives you the full benefits of version control:

```bash
# Initial setup
git init
echo "cxas_app/" >> .gitignore  # or track it — your choice
git add .
git commit -m "Initial project setup"

# Development loop
cxas pull "My Support Agent" --project_id my-project --location us-central1
# ... edit files ...
cxas lint
git diff  # review changes
git commit -m "Improve order lookup instruction"
cxas push cxas_app/My\ Support\ Agent
```

If you track `cxas_app/` in Git, you get:

- Full history of every instruction change
- Easy rollback if a push breaks something
- Code review via pull requests before pushing to production

---

## What `cxas push` does internally

Understanding what happens during a push helps you reason about partial failures:

1. SCRAPI reads `app.json` and updates app-level settings
2. For each agent directory, it reads `<agent_name>.json` and resolves any file paths in callback references
3. It sends an `update_agent` API call with the full agent config including resolved Python code
4. For each tool, it reads `<tool_name>.json` and the associated `python_code.py`, then sends an `update_tool` API call
5. For guardrails and evaluations, similar file-based reads and API updates

If a push fails partway through (e.g., a network error after updating agents but before updating tools), the platform will be in a partially updated state. The safest recovery is to pull again and re-push.

---

## Common issues

**"App not found" after pull**
: The display name is case-sensitive. Try using the full resource name instead.

**Push succeeds but changes aren't reflected**
: Check that you're pushing to the right app. Confirm with `cxas apps` or `apps.list_apps()`.

**Callback changes aren't picked up**
: The `pythonCode` path in the agent JSON must match the actual file location. After a pull, these paths are set correctly; if you've moved files manually, update the JSON to match.

**The instruction lint rules show errors you can't reproduce**
: The linter runs against the local files. Make sure you've saved your changes before running `cxas lint`.
