---
title: Creating Agents
description: Step-by-step guide to creating apps, agents, tools, and callbacks with SCRAPI.
---

# Creating Agents

This guide walks you through creating a CX Agent Studio app from scratch. You'll create an app, add an agent, configure it as the root agent, add a tool, and attach a callback — all using both the Python API and the CLI.

---

## Before you start

Make sure you have:

- SCRAPI installed (`pip install cxas-scrapi`)
- A Google Cloud project with CX Agent Studio enabled
- Valid credentials set up (see [Authentication](../../getting-started/authentication.md))

---

## Step 1: Create an App

An *App* is the top-level container. Every agent, tool, and guardrail belongs to an app. Think of it as your project.

=== "Python"

    ```python
    from cxas_scrapi.core.apps import Apps

    apps = Apps(project_id="my-gcp-project", location="us")

    app = apps.create_app(
        app_id="my-support-agent",
        display_name="My Support Agent",
        description="Handles customer support queries",
    )

    print(app.name)
    # projects/my-gcp-project/locations/us/apps/my-support-agent
    ```

    The `app_id` becomes part of the resource name — choose something short and descriptive, using only lowercase letters, hyphens, and numbers. The `display_name` is the human-readable label that appears in the console.

=== "CLI"

    ```bash
    cxas create "My Support Agent" \
      --app_name my-support-agent \
      --description "Handles customer support queries" \
      --project_id my-gcp-project \
      --location us
    ```

    The CLI prints the full resource name after creation:

    ```
    App created successfully: projects/my-gcp-project/locations/us/apps/my-support-agent
    ```

---

## Step 2: Add an Agent

Agents are the conversational entities inside your app. Each agent has instructions (written in a structured natural language format), a set of tools it can call, and optional callbacks that run before or after model inference.

=== "Python"

    ```python
    from cxas_scrapi.core.agents import Agents

    # app_name is the full resource name from Step 1
    app_name = "projects/my-gcp-project/locations/us/apps/my-support-agent"

    agents = Agents(app_name=app_name)

    agent = agents.create_agent(
        agent_id="support-root",
        display_name="Support Root Agent",
    )

    print(agent.name)
    # projects/my-gcp-project/locations/us/apps/my-support-agent/agents/support-root
    ```

=== "CLI"

    Agent creation is done through the Python API or by pulling the app, adding an agent directory, and pushing back:

    ```bash
    # Pull the app to get the local directory structure
    cxas pull "My Support Agent" \
      --project_id my-gcp-project \
      --location us

    # Create the agent directory and config, then push
    cxas push cxas_app/My\ Support\ Agent
    ```

---

## Step 3: Set the Root Agent

Every app needs a *root agent* — the agent that receives the first user message. You set this on the app after creating the agent.

=== "Python"

    ```python
    agent_name = "projects/my-gcp-project/locations/us/apps/my-support-agent/agents/support-root"

    apps.update_app(
        app_name=app.name,
        root_agent=agent_name,
    )
    ```

=== "CLI"

    The root agent is set through the Python API or by editing the app config locally:

    ```bash
    # Pull the app, edit app.json to set "rootAgent", then push
    cxas pull "My Support Agent" \
      --project_id my-gcp-project \
      --location us

    # Edit cxas_app/My Support Agent/app.json and set "rootAgent"
    cxas push cxas_app/My\ Support\ Agent
    ```

---

## Step 4: Create a Tool

Tools are the functions your agent can call during a conversation. For Python tools, you write a Python function; SCRAPI and the platform handle the rest.

=== "Python"

    ```python
    from cxas_scrapi.core.tools import Tools

    tools = Tools(app_name=app_name)

    # Create a Python tool
    tool = tools.create_tool(
        tool_id="lookup_order",
        display_name="lookup_order",
        description="Looks up a customer order by order ID",
        payload={
            "python_code": """
    def lookup_order(order_id: str) -> dict:
        \"\"\"Looks up a customer order by its ID.\"\"\"
        # Your implementation here
        if not order_id:
            return {"agent_action": "I couldn't find an order ID. Could you share it?"}
        return {
            "order_id": order_id,
            "status": "shipped",
            "estimated_delivery": "2026-04-18",
        }
    """,
        },
    )
    ```

=== "CLI"

    Tools are created via the Python API (as shown above), or by creating the tool on the platform UI and then pulling locally:

    ```bash
    # Pull the app to get the local directory structure
    cxas pull "My Support Agent" \
      --project_id my-gcp-project \
      --location us

    # Create the tool directory and python_code.py, then push
    # cxas_app/My Support Agent/tools/lookup_order/python_function/python_code.py
    cxas push cxas_app/My\ Support\ Agent
    ```

---

## Step 5: Associate the Tool with an Agent

Creating a tool doesn't automatically give it to an agent. You need to associate the tool with the agent explicitly.

=== "Python"

    ```python
    from cxas_scrapi.core.agents import Agents

    agents = Agents(app_name=app_name)

    # Get the current agent config (use the full resource name)
    current_agent = agents.get_agent(agent.name)

    # Update the agent's tool list (use full resource names for tools)
    agents.update_agent(
        agent_name=agent.name,
        tools=[tool.name],
    )
    ```

    !!! tip "Always include `end_session`"
        The root agent should always have `end_session` in its tools list. This is a platform built-in tool that lets the agent terminate conversations cleanly. You can reference it by its full resource name (e.g., `{app_name}/tools/end_session`). The linter rule A005 will flag it if you forget.

=== "CLI"

    After pulling, open `cxas_app/<AppName>/agents/support-root/support-root.json` and add your tool to the `tools` array, then push:

    ```json
    {
      "displayName": "Support Root Agent",
      "tools": ["lookup_order", "end_session"]
    }
    ```

    ```bash
    cxas push cxas_app/My\ Support\ Agent
    ```

---

## Step 6: Write the Agent's Instruction

The instruction file is the most important part of your agent. It tells the LLM what role to play, how to behave, and when to call each tool.

Pull the app to get the local files, then edit `instruction.txt`:

```
cxas_app/My Support Agent/agents/support-root/instruction.txt
```

A well-structured instruction uses the required XML tags:

```xml
<role>
You are a friendly customer support agent for Acme Corp. Your job is to
help customers with questions about their orders and products.
</role>

<persona>
You are warm, empathetic, and concise. You always acknowledge the customer's
concern before jumping into solutions. You never make up information.
</persona>

<taskflow>
  <subtask name="order_lookup">
    <trigger>The customer asks about the status of their order</trigger>
    <step>Ask for their order ID if they haven't provided it</step>
    <step>Call {@TOOL: lookup_order} with the order ID</step>
    <step>Relay the status and estimated delivery date to the customer</step>
  </subtask>

  <subtask name="end_conversation">
    <trigger>The customer says goodbye or indicates they are done</trigger>
    <step>Thank them for contacting us</step>
    <step>Call {@TOOL: end_session}</step>
  </subtask>
</taskflow>
```

!!! info "Why XML structure?"
    The `<role>`, `<persona>`, and `<taskflow>` structure is a GECX design best practice. The linter rule I001 enforces this. The structured format helps the LLM understand which part of the instruction is context (role/persona) versus action (taskflow).

After editing, lint and push:

```bash
cxas lint
cxas push cxas_app/My\ Support\ Agent
```

---

## Step 7: Add a Callback (optional)

Callbacks are Python functions that run before or after the model receives a request or produces a response. Common uses include injecting context into the prompt, logging, or overriding responses.

=== "Python"

    ```python
    callback_code = """
    from typing import Optional

    def before_model_callback(callback_context, llm_request):
        # Inject user's account ID into the prompt
        account_id = callback_context.state.get("account_id")
        if account_id:
            system_note = f"User account ID: {account_id}"
        return None  # None means "proceed normally"
    """

    agents.update_agent(
        agent_name=agent.name,
        before_model_callbacks=[{
            "python_code": callback_code,
            "description": "Injects user account context into the prompt",
        }],
    )
    ```

=== "CLI"

    After pulling, create or edit the callback file at:

    ```
    cxas_app/<AppName>/agents/<agent_name>/before_model_callbacks/<cb_name>/python_code.py
    ```

    Then update the agent JSON to reference it, and push.

---

## Summary

You now have a complete agent setup:

1. App created and named
2. Root agent created and set
3. Tool created, implemented, and associated
4. Instruction written with proper XML structure
5. Callback attached (optional)

From here, you can write evaluations to test the agent's behavior — see the [Evaluation guide](../evaluation/index.md).
