---
title: Callbacks
---

# Callbacks

`Callbacks` manages the Python code callbacks that run at various points in an agent's lifecycle. CXAS supports six callback types:

- **before_model_callbacks** — run before the LLM is called (great for injecting context or sanitizing input)
- **after_model_callbacks** — run after the LLM responds (useful for post-processing or logging)
- **before_agent_callbacks** — run when an agent is about to handle a turn
- **after_agent_callbacks** — run after an agent completes its turn
- **before_tool_callbacks** — run before a tool is executed
- **after_tool_callbacks** — run after a tool returns a result

Use this class when you need to programmatically read or update callback code — for example, syncing callback functions from a local code repository to CXAS, or auditing what callbacks are active on each agent.

## Quick Example

```python
from cxas_scrapi import Callbacks, Agents

app_name = "projects/my-project/locations/us/apps/my-app-id"
callbacks = Callbacks(app_name=app_name)

# List callbacks for a specific agent (returns a dict of {type: [callbacks]})
agents = Agents(app_name=app_name)
agent = agents.list_agents()[0]  # Get the first agent

agent_callbacks = callbacks.list_callbacks(agent.name)
for cb_type, cb_list in agent_callbacks.items():
    for cb in cb_list:
        print(f"{cb_type}: {cb.description} (disabled={cb.disabled})")
```

## Reference

::: cxas_scrapi.core.callbacks.Callbacks
