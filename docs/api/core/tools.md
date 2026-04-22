---
title: Tools
---

# Tools

`Tools` gives you full CRUD access to the tools and toolsets defined in a CXAS app. It understands both simple Python function tools and more complex OpenAPI toolsets — so whether you're creating a lightweight helper function or wiring up an external REST API, this class has you covered.

Beyond management, `Tools` can also *execute* a tool directly via `execute_tool()` — without needing to run a full agent session. This is incredibly useful for unit testing tools in isolation or debugging unexpected responses.

## Quick Example

```python
from cxas_scrapi import Tools

app_name = "projects/my-project/locations/us/apps/my-app-id"
tools = Tools(app_name=app_name)

# List everything
all_tools = tools.list_tools()
for t in all_tools:
    print(t.display_name, t.name)

# Get a name-to-display-name map
tools_map = tools.get_tools_map()

# Execute a tool directly (great for testing)
result = tools.execute_tool(
    tool_display_name="lookup_account",
    args={"customer_id": "C-1234"},
)
print(result)

# Create a new Python function tool
tools.create_tool(
    tool_id="greet_user",
    display_name="greet_user",
    payload={
        "python_code": "def greet_user(name: str) -> dict:\n    return {'greeting': f'Hello, {name}!'}",
    },
    tool_type="python_function",
)
```

## Reference

::: cxas_scrapi.core.tools.Tools
