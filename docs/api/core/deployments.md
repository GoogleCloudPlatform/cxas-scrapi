---
title: Deployments
---

# Deployments

`Deployments` helps you manage the deployment configurations of a CXAS app. A deployment in CX Agent Studio represents an environment configuration (such as a telephony integration or a channel-specific setup) that determines how your app is published and reachable.

Use this class when you want to list deployments, inspect their configuration, or script deployment updates as part of a release pipeline.

## Quick Example

```python
from cxas_scrapi import Deployments

app_name = "projects/my-project/locations/us/apps/my-app-id"
deployments = Deployments(app_name=app_name)

# List all deployments
all_deployments = deployments.list_deployments()
for d in all_deployments:
    print(d.display_name, d.name)

# Create a Sessions client pinned to a specific deployment
from cxas_scrapi import Sessions
sessions = Sessions(
    app_name=app_name,
    deployment_id="my-deployment-id",
)
response = sessions.run(session_id="test-1", text="Hello")
```

## Reference

::: cxas_scrapi.core.deployments.Deployments
