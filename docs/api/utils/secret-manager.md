---
title: SecretManagerUtils
---

# SecretManagerUtils

`SecretManagerUtils` is a lightweight wrapper around the GCP Secret Manager API. It makes it easy to store and retrieve secrets (API keys, credentials, connection strings) from a notebook or automation script without manually constructing Secret Manager resource names.

A common pattern is to store a service account JSON as a Secret Manager secret, then retrieve it at runtime to authenticate other CXAS Scrapi classes — keeping secrets out of your source code and environment variables.

## Quick Example

```python
from cxas_scrapi import SecretManagerUtils

sm = SecretManagerUtils(project_id="my-gcp-project")

# Create a new secret (or get it if it already exists)
secret_version = sm.create_or_get_secret(
    secret_id="my-api-key",
    payload="super-secret-value-123",
)
print("Secret version:", secret_version)

# Retrieve the secret value later
value = sm.get_secret(secret_id="my-api-key")
print("Retrieved:", value)

# Use a secret to authenticate another class
import json
creds_json = sm.get_secret(secret_id="my-service-account-json")
creds_dict = json.loads(creds_json)

from cxas_scrapi import Sessions
sessions = Sessions(
    app_name="projects/my-project/locations/us/apps/my-app",
    creds_dict=creds_dict,
)
```

## Reference

::: cxas_scrapi.utils.secret_manager_utils.SecretManagerUtils
