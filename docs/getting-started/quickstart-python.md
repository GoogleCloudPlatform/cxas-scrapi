---
title: Python Quickstart
description: Go from import to your first API response in under five minutes.
---

# Python Quickstart

This quickstart walks you through four practical tasks using the SCRAPI Python API — from listing your Apps all the way to running an evaluation. Each task builds on the last, so by the end you'll have a solid feel for how the library hangs together.

---

## Prerequisites

Before you start, make sure you have:

- [x] SCRAPI installed: `pip install cxas-scrapi`
- [x] Authentication configured (see [Authentication](authentication.md))
- [x] A GCP project with at least one CX Agent Studio App

You'll need your GCP **project ID** and the **location** of your App. Locations are typically `us`, `global`, or a regional identifier like `us-central1`.

---

## Task 1 — List your Apps

An "App" is the top-level container in CX Agent Studio. Let's start by listing all the Apps in your project:

```python
from cxas_scrapi import Apps

# Replace with your GCP project ID and location
PROJECT_ID = "my-gcp-project"
LOCATION = "us"

# Create the Apps client
app_client = Apps(project_id=PROJECT_ID, location=LOCATION)

# List all Apps in the project
apps = app_client.list_apps()

for app in apps:
    print(f"App: {app.display_name}")
    print(f"  Resource name: {app.name}")
    print()
```

**What you'll see:**

```
App: My Customer Service Bot
  Resource name: projects/my-gcp-project/locations/us/apps/abc123

App: Internal HR Assistant
  Resource name: projects/my-gcp-project/locations/us/apps/def456
```

The `app.name` field — that long resource name — is what SCRAPI calls the `app_name`. You'll use it to identify your App when working with Agents, Tools, Evaluations, and other resources. Copy it — you'll need it in the tasks below.

!!! tip "Using `get_apps_map()`"
    `list_apps()` returns raw API objects. If you prefer a simple dictionary keyed by display name, use `app_client.get_apps_map()` instead.

---

## Task 2 — List Agents in an App

Each App contains one or more Agents. Let's see what's inside your App:

```python
from cxas_scrapi import Agents

# Use the full resource name from Task 1
APP_NAME = "projects/my-gcp-project/locations/us/apps/abc123"

agent_client = Agents(app_name=APP_NAME)

# List all Agents in the App
agents = agent_client.list_agents()

for agent in agents:
    print(f"Agent: {agent.display_name}")
    print(f"  Resource name: {agent.name}")
    print()
```

**What you'll see:**

```
Agent: Main Playbook Agent
  Resource name: projects/my-gcp-project/locations/us/apps/abc123/agents/agent-001

Agent: Escalation Handler
  Resource name: projects/my-gcp-project/locations/us/apps/abc123/agents/agent-002
```

!!! info "One App, many Agents"
    A single App typically has one main Agent (handling the primary conversation) and may have additional Agents for specific workflows, escalations, or specialized tasks. See [Key Concepts](concepts.md) for more detail.

---

## Task 3 — Send a test message

Now let's interact with an agent by sending it a message. The `Sessions` class manages conversations:

```python
from cxas_scrapi import Sessions

APP_NAME = "projects/my-gcp-project/locations/us/apps/abc123"

session_client = Sessions(app_name=APP_NAME)

# Create a new session (a unique conversation thread)
session_id = session_client.create_session_id()
print(f"Session ID: {session_id}")

# Send a message to the agent
response = session_client.run(
    session_id=session_id,
    text="Hello! What can you help me with today?",
)

# Print the agent's response
session_client.parse_result(response)
```

**What you'll see:**

```
Session ID: 550e8400-e29b-41d4-a716-446655440000
AGENT RESPONSE: Hi there! I can help you with account inquiries, billing questions, ...
```

Think of a session as a single conversation thread — like a chat window. Each time you call `run` with the same `session_id`, the agent remembers the context from earlier in that conversation.

!!! tip "Generating session IDs"
    `create_session_id()` generates a UUID for you. You can also provide your own string — just make sure it's unique per conversation.

---

## Task 4 — Run an evaluation

Evaluations let you systematically test your agent's behavior. Here's how to run a simple tool evaluation — which checks that your agent's tools return the values you expect:

```python
from cxas_scrapi import ToolEvals

APP_NAME = "projects/my-gcp-project/locations/us/apps/abc123"

# Initialize the ToolEvals client
tool_evals = ToolEvals(app_name=APP_NAME)

# Load test cases from a YAML file
test_cases = tool_evals.load_tool_test_cases_from_file("evals/tool_tests/order_tests.yaml")

# Run the tests — returns a pandas DataFrame
results_df = tool_evals.run_tool_tests(test_cases)

# Print results
print(results_df[["test_name", "tool", "status"]].to_string())
```

The YAML test file format looks like this:

```yaml
tests:
  - name: check_order_status_returns_data
    tool: check_order_status
    args:
      order_id: "ORD-12345"
```

**What you'll see:**

```
Running test: check_order_status_returns_data (check_order_status)
PASSED: check_order_status --> check_order_status_returns_data
```

!!! info "Five evaluation types"
    SCRAPI supports five evaluation types: `ToolEvals`, `CallbackEvals`, `SimulationEvals`, `GuardrailEvals`, and `TurnEvals`. Each one tests a different aspect of your agent's behavior. See the [Evaluation Guide](../guides/evaluation/index.md) for a full walkthrough.

---

## Putting it all together

Here's a simple script that combines all four tasks:

```python
from cxas_scrapi import Apps, Agents, Sessions

PROJECT_ID = "my-gcp-project"
LOCATION = "us"

# 1. Find your App
app_client = Apps(project_id=PROJECT_ID, location=LOCATION)
apps = app_client.list_apps()
my_app = apps[0]  # Take the first one for this example
print(f"Using App: {my_app.display_name}")

app_name = my_app.name

# 2. List its Agents
agent_client = Agents(app_name=app_name)
agents = agent_client.list_agents()
print(f"Found {len(agents)} agent(s)")

# 3. Send a test message
session_client = Sessions(app_name=app_name)
session_id = session_client.create_session_id()

response = session_client.run(
    session_id=session_id,
    text="Hello!",
)
session_client.parse_result(response)
```

---

## What's next?

You've covered the basics! Here are some good next steps:

- Learn about the [CLI Quickstart](quickstart-cli.md) to manage agents from your terminal
- Dive into [Key Concepts](concepts.md) to understand the resource hierarchy
- Explore the [Evaluation Guide](../guides/evaluation/index.md) for all five eval types
- Read the [full API Reference](../api/index.md) for every class and method
