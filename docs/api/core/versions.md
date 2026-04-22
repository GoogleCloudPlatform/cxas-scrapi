---
title: Versions
---

# Versions

`Versions` lets you create and manage snapshots of a CXAS app. Every time you publish significant changes, you can tag that state as a version — giving you a rollback point if something goes wrong, and a stable target for running evaluations against a known baseline.

You can also use `Sessions` to run conversations against a specific version by passing `version_id` to the constructor or to individual `run()` calls.

## Quick Example

```python
from cxas_scrapi import Versions

app_name = "projects/my-project/locations/us/apps/my-app-id"
versions = Versions(app_name=app_name)

# List all versions
all_versions = versions.list_versions()
for v in all_versions:
    print(v.display_name, v.name)

# Get a map of version display names to resource names
versions_map = versions.get_versions_map()
print(versions_map)

# Use a specific version in a session
from cxas_scrapi import Sessions
sessions = Sessions(app_name=app_name, version_id="v1-0-0")
```

## Reference

::: cxas_scrapi.core.versions.Versions
