---
title: Variables
---

# Variables

`Variables` manages app-level session variable definitions in CX Agent Studio. Session variables are the shared state that flows through a conversation — things like `caller_id`, `account_balance`, or `authenticated`. You can define their schemas, set defaults, and read or update them programmatically.

This class is particularly useful when you need to inject specific variable values during testing (for example, simulating an authenticated user) or when you want to audit which variables an app exposes before running a tool eval.

## Quick Example

```python
from cxas_scrapi import Variables

app_name = "projects/my-project/locations/us/apps/my-app-id"
variables = Variables(app_name=app_name)

# List all session variables defined in this app
all_vars = variables.list_variables()
for v in all_vars:
    print(v.name, v.schema)

# Get the name-to-display-name map
vars_map = variables.get_variables_map()
print(vars_map)
```

## Reference

::: cxas_scrapi.core.variables.Variables
