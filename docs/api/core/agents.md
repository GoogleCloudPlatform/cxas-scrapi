---
title: Agents
---

# Agents

`Agents` lets you create, update, list, and delete agents within a specific CXAS app. CXAS supports three flavors of agents: **LLM agents** (powered by Gemini), **DFCX agents** (wrapping a Dialogflow CX agent), and **workflow agents** (declarative routing logic). `Agents` handles all three.

Because `Agents` extends `Apps`, you automatically get all the app-level operations too — a nice bonus when you're scripting end-to-end app management.

## Quick Example

```python
from cxas_scrapi import Agents

app_name = "projects/my-project/locations/us/apps/my-app-id"

agents = Agents(
    app_name=app_name,
    creds_path="/path/to/service_account.json",
)

# List all agents in this app
all_agents = agents.list_agents()
for agent in all_agents:
    print(agent.display_name)

# Create a new LLM agent
new_agent = agents.create_agent(
    display_name="Billing Agent",
    agent_type="llm",
    model="gemini-2.5-flash",
    instruction="You are a helpful billing assistant.",
)
print("Created:", new_agent.name)

# Update its instruction
agents.update_agent(
    new_agent.name,
    instruction="You are a concise and friendly billing assistant.",
)

# Delete it when you're done
agents.delete_agent(new_agent.name)
```

## Reference

::: cxas_scrapi.core.agents.Agents
