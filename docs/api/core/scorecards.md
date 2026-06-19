---
title: Scorecards
---

# Scorecards

`Scorecards` connects CXAS Scrapi to the **CCAI Insights QA Scorecards** API. QA Scorecards let you evaluate agent performance by defining rubrics and specific questions (such as whether the agent greeted the customer properly, resolved the customer's query, or escalated when necessary).

This class inherits from `Insights` and provides basic CRUDL operations for scorecards, revision history, and the questions within a scorecard revision.

## Quick Example

```python
from cxas_scrapi import Scorecards

scorecards_client = Scorecards(
    project_id="my-gcp-project",
    location="us-central1",
)

# List all scorecards
scorecards = scorecards_client.list_scorecards()
for sc in scorecards:
    print(sc.get("displayName"), sc.get("name"))

# Get latest revision for a scorecard
revision = scorecards_client.get_latest_revision("projects/my-gcp-project/locations/us-central1/qaScorecards/my-scorecard")
print(revision.get("name"))
```

## Reference

::: cxas_scrapi.core.scorecards.Scorecards
