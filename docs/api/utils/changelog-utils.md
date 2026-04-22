---
title: ChangelogUtils
---

# ChangelogUtils

`ChangelogUtils` provides helper methods for working with CXAS app changelog data. It builds on top of the `Changelogs` class to offer higher-level operations: formatting changelog entries for display, filtering by resource type or time range, and converting the raw changelog stream into a structured pandas DataFrame.

Use this class when you want to generate a human-readable release summary, audit changes between two deployments, or feed changelog data into a reporting dashboard.

## Quick Example

```python
from cxas_scrapi import ChangelogUtils

app_name = "projects/my-project/locations/us/apps/my-app-id"
cu = ChangelogUtils(app_name=app_name)

# Get a formatted summary of recent changes
summary = cu.get_changelog_summary(limit=20)
print(summary)

# Convert to a DataFrame for analysis or export
df = cu.changelogs_to_dataframe()
print(df[["create_time", "action", "resource_display_name", "user_email"]].head(10))

# Filter to only instruction changes
instruction_changes = df[df["resource_type"] == "Agent"]
print(instruction_changes)
```

## Reference

::: cxas_scrapi.utils.changelog_utils.ChangelogUtils
