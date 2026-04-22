---
title: Guardrails
---

# Guardrails

`Guardrails` lets you manage the content safety guardrail resources attached to a CXAS app. Guardrails are protective layers that intercept model inputs and outputs — they can block harmful content, enforce topic restrictions, and ensure your agent stays on-brand.

You'll reach for this class when you want to programmatically audit which guardrails are active on an app, update guardrail configurations as part of a CI/CD pipeline, or create new guardrails from code rather than the Cloud Console.

## Quick Example

```python
from cxas_scrapi import Guardrails

app_name = "projects/my-project/locations/us/apps/my-app-id"
guardrails = Guardrails(app_name=app_name)

# See what's there
all_guardrails = guardrails.list_guardrails()
for g in all_guardrails:
    print(g.display_name, g.name)

# Get the name-to-display-name map
guardrails_map = guardrails.get_guardrails_map()
print(guardrails_map)
```

## Reference

::: cxas_scrapi.core.guardrails.Guardrails
