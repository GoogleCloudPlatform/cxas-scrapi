---
title: Apps
---

# Apps

`Apps` is your entry point into CX Agent Studio. It lets you list every app in a project, look one up by display name, create brand new ones, and move app definitions in and out through import and export. Think of it as the "file manager" for your CXAS workspace.

You'll reach for `Apps` directly whenever you want to work at the project level — for example, when writing a script that syncs apps between environments or backs up all your apps to GCS.

> **Note:** Most other classes (`Agents`, `Tools`, `Sessions`, etc.) extend `Apps` or `Common`, so you rarely need to instantiate `Apps` on its own unless you specifically need project-level operations.

## Quick Example

```python
from cxas_scrapi import Apps

apps = Apps(
    project_id="my-gcp-project",
    location="us",
    creds_path="/path/to/service_account.json",
)

# List all apps
all_apps = apps.list_apps()
for app in all_apps:
    print(app.display_name, app.name)

# Find an app by display name
my_app = apps.get_app_by_display_name("My Support Agent")

# Export it to a local zip
if my_app:
    apps.export_app(my_app.name, local_path="backup.zip")

# Import it into another project
apps.import_app(
    app_name=my_app.name,
    local_path="backup.zip",
    conflict_strategy="REPLACE",
)
```

## Reference

::: cxas_scrapi.core.apps.Apps
