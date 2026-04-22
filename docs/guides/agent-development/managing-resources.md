---
title: Managing Resources
description: Working with Tools, Guardrails, Variables, Deployments, Versions, and Changelogs via the SCRAPI Python API.
---

# Managing Resources

Beyond agents themselves, CX Agent Studio has a rich set of supporting resources — tools, guardrails, session variables, deployments, versioned snapshots, and changelogs. This page covers each one: what it does, how to instantiate the client class, and the key methods you'll use day to day.

---

## Tools

Tools are the functions your agents call during conversations. SCRAPI's `Tools` class wraps both the platform's tool management API and a few convenience methods for working with tool metadata.

### Constructor

```python
from cxas_scrapi.core.tools import Tools

tools = Tools(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
    creds=None,  # Optional — uses ADC by default
)
```

The `app_name` is the full resource name of your app. The class derives `project_id` and `location` from this string automatically.

### Key methods

| Method | Description |
|--------|-------------|
| `list_tools()` | Returns all tools in the app |
| `get_tool(tool_id)` | Gets a specific tool by its ID |
| `get_tools_map(reverse=False)` | Returns a dict mapping tool names to display names (or reversed) |
| `create_tool(tool_id, display_name, ...)` | Creates a new tool |
| `update_tool(tool_name, **kwargs)` | Updates tool fields |
| `delete_tool(tool_name)` | Deletes a tool |
| `execute_tool(tool_display_name, args)` | Invokes a tool directly for testing |

### Example: listing tools and running one directly

```python
from cxas_scrapi.core.tools import Tools

tools = Tools(app_name="projects/my-project/locations/us/apps/my-app")

# List all tools
for tool in tools.list_tools():
    print(tool.display_name, tool.name)

# Run a tool directly (useful for debugging)
result = tools.execute_tool(
    tool_display_name="lookup_order",
    args={"order_id": "ORD-12345"},
)
print(result)
```

!!! tip "Using `get_tools_map`"
    When you need to go from a display name (what the LLM sees) to a resource name (what the API needs), `get_tools_map(reverse=True)` gives you `display_name -> resource_name`.

---

## Guardrails

Guardrails filter or transform inputs and outputs to enforce content policies, safety rules, or business logic.

### Constructor

```python
from cxas_scrapi.core.guardrails import Guardrails

guardrails = Guardrails(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
)
```

### Key methods

| Method | Description |
|--------|-------------|
| `list_guardrails()` | Returns all guardrails in the app |
| `get_guardrail(guardrail_id)` | Gets a specific guardrail |
| `create_guardrail(guardrail_id, display_name, payload)` | Creates a new guardrail |
| `update_guardrail(guardrail_id, **kwargs)` | Updates guardrail fields |
| `delete_guardrail(guardrail_id)` | Deletes a guardrail |

### Example: creating a content safety guardrail

```python
from cxas_scrapi.core.guardrails import Guardrails

guardrails = Guardrails(app_name="projects/.../apps/my-app")

guardrail = guardrails.create_guardrail(
    guardrail_id="content-safety",
    display_name="Content Safety",
    payload={
        "messageTemplates": [
            {"responseMessage": "I can't help with that request."}
        ],
    },
)
```

---

## Variables

Session variables let you inject dynamic data into conversations — things like the authenticated user's account ID, their region, or feature flags. The `Variables` class manages these at the app level.

### Constructor

```python
from cxas_scrapi.core.variables import Variables

variables = Variables(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
)
```

### Key methods

| Method | Description |
|--------|-------------|
| `list_variables()` | Lists all session variables |
| `get_variable(variable_name)` | Gets a specific variable by name |
| `create_variable(variable_name, variable_type, variable_value)` | Creates a variable |
| `update_variable(variable_name, variable_type, variable_value)` | Updates a variable |
| `delete_variable(variable_name)` | Deletes a variable |

### Example: declaring a session variable

```python
from cxas_scrapi.core.variables import Variables

variables = Variables(app_name="projects/.../apps/my-app")

variables.create_variable(
    variable_name="account_id",
    variable_type="STRING",
    variable_value="",
)
```

Variables declared here can be injected at session start via the Sessions API's `session_parameters` field, and read inside callbacks via `callback_context.session.session_parameters`.

---

## Deployments

A deployment makes a specific version of your app available to handle live traffic. You can have multiple deployments — for example, a production deployment and a staging one.

### Constructor

```python
from cxas_scrapi.core.deployments import Deployments

deployments = Deployments(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
)
```

### Key methods

| Method | Description |
|--------|-------------|
| `list_deployments()` | Lists all deployments |
| `get_deployment(deployment_id)` | Gets a specific deployment |
| `create_deployment(deployment_id, display_name, app_version)` | Creates a new deployment |
| `update_deployment(deployment_id, **kwargs)` | Updates deployment settings |
| `delete_deployment(deployment_id)` | Deletes a deployment |

### Example: creating a deployment

```python
from cxas_scrapi.core.deployments import Deployments

deployments = Deployments(app_name="projects/.../apps/my-app")

deployment = deployments.create_deployment(
    deployment_id="production",
    display_name="Production",
    app_version="draft",  # or a specific version ID
)

print(deployment.name)
# projects/.../apps/my-app/deployments/production
```

The `deployed_app_id` in `gecx-config.json` should point to this deployment's resource name when using the skills system.

---

## Versions

Versions are immutable snapshots of your app at a point in time. Creating a version before a major change gives you a safe rollback point.

### Constructor

```python
from cxas_scrapi.core.versions import Versions

versions = Versions(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
)
```

### Key methods

| Method | Description |
|--------|-------------|
| `list_versions()` | Lists all versions |
| `get_version(version_id)` | Gets a specific version |
| `get_versions_map(reverse=False)` | Returns a dict mapping version names to display names |
| `revert_version(version_id)` | Restores the app to a previous version |
| `delete_version(version_id)` | Deletes a version |

### Example: listing and reverting versions

```python
from cxas_scrapi.core.versions import Versions

versions = Versions(app_name="projects/.../apps/my-app")

# List available versions
for version in versions.list_versions():
    print(f"{version.display_name}: {version.name}")

# Revert to a previous version
versions.revert_version("v1-0-0")
```

!!! warning "Versions are immutable"
    Once created, a version cannot be edited. You can restore it (which overwrites the draft) or delete it, but you cannot change its contents.

---

## Changelogs

Changelogs give you a structured audit trail of changes made to your app. Every push, version creation, and agent update appears in the changelog. This is useful for debugging regressions — you can see exactly what changed and when.

### Constructor

```python
from cxas_scrapi.core.changelogs import Changelogs

changelogs = Changelogs(
    app_name="projects/my-project/locations/us-central1/apps/my-app",
)
```

### Key methods

| Method | Description |
|--------|-------------|
| `list_changelogs()` | Returns recent changelog entries |
| `get_changelog(changelog_id)` | Gets a specific entry |

### Example: reviewing recent changes

```python
from cxas_scrapi.core.changelogs import Changelogs

changelogs = Changelogs(app_name="projects/.../apps/my-app")

for entry in changelogs.list_changelogs():
    print(f"{entry.create_time}: {entry.display_name} — {entry.description}")
```

The `cxas branch` command uses changelogs internally to perform drift detection before a push — see [Branching](branching.md) for details.

---

## Working with multiple resource types together

A common pattern is to chain these classes when preparing for a release:

```python
from cxas_scrapi.core.versions import Versions
from cxas_scrapi.core.deployments import Deployments
from cxas_scrapi.core.changelogs import Changelogs

app_name = "projects/my-project/locations/us/apps/my-app"

# 1. Check what changed
changelogs = Changelogs(app_name=app_name)
recent = changelogs.list_changelogs()
if recent:
    print(f"Latest change: {recent[0].display_name}")

# 2. List available versions
versions = Versions(app_name=app_name)
for v in versions.list_versions():
    print(f"Version: {v.display_name} ({v.name})")

# 3. Update the deployment to point to a specific version
deployments = Deployments(app_name=app_name)
deployments.update_deployment(
    deployment_id="production",
    app_version="v2-0-0",
)

print("Release complete.")
```
