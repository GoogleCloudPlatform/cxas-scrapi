---
title: Changelogs
---

# Changelogs

`Changelogs` gives you read access to the audit trail of changes made to a CXAS app. Every time an agent, tool, guardrail, or other resource changes, CX Agent Studio records an entry. The `Changelogs` class lets you pull those entries and inspect what changed, when, and by whom.

This is helpful for compliance reporting, debugging unexpected behavior after a release, or simply understanding the history of an app before running a regression test suite.

## Quick Example

```python
from cxas_scrapi import Changelogs

app_name = "projects/my-project/locations/us/apps/my-app-id"
changelogs = Changelogs(app_name=app_name)

# Fetch recent changelog entries
entries = changelogs.list_changelogs()
for entry in entries:
    print(entry.create_time, entry.action, entry.resource_display_name)
```

## Reference

::: cxas_scrapi.core.changelogs.Changelogs
