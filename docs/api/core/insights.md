---
title: Insights
---

# Insights

`Insights` connects CXAS Scrapi to the **CCAI Insights** API — Google Cloud's contact center analytics platform. Through this class you can access conversation analytics, manage scorecards, and pull quality AI data that helps you understand how well your agent is performing at scale.

The class uses a REST-based client (rather than a gRPC SDK) because the Insights API has a different endpoint pattern from the CES API used by other CXAS classes. The authentication flow is the same — your credentials from `Common` are reused automatically.

## Quick Example

```python
from cxas_scrapi import Insights

insights = Insights(
    project_id="my-gcp-project",
    location="us-central1",
    creds_path="/path/to/service_account.json",
)

# List all conversations (conversations indexed in Insights)
conversations = insights.list_conversations()
for conv in conversations:
    print(conv.get("name"), conv.get("duration"))

# List all scorecards
scorecards = insights.list_scorecards()
for sc in scorecards:
    print(sc.get("displayName"))
```

## Reference

::: cxas_scrapi.core.insights.Insights
