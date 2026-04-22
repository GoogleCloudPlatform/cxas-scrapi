---
title: Insights
description: Working with CXAS Insights and Quality AI scorecards.
---

# Insights

CXAS Insights provides conversation analytics and quality evaluation for your CX Agent Studio agents. It connects to the CCAI Insights API to give you aggregate metrics, quality scores, and the ability to automate quality assessment at scale.

---

## What CXAS Insights offers

CXAS Insights is built on **CCAI Insights** (Contact Center AI Insights), Google's conversation analytics platform. SCRAPI provides Python classes and CLI commands to interact with the Insights API directly from your agent development workflow.

The main capabilities SCRAPI exposes:

### Quality AI Scorecards

Scorecards are structured evaluation rubrics. Each scorecard has one or more *questions* — criteria that an AI system uses to evaluate whether a conversation met a specific quality bar.

Example scorecard questions:
- "Did the agent correctly identify the customer's intent?"
- "Did the agent provide accurate information?"
- "Did the agent maintain a professional tone?"
- "Was the issue resolved within the conversation?"

Scorecards can be applied to real conversation transcripts from production, giving you automated quality scores at scale — far more conversations than a human QA team could review manually.

### Insights vs. Evals

You might be wondering: how is this different from the evaluation types covered in the [Evaluation guide](../evaluation/index.md)?

| | Evaluations (SCRAPI) | Insights Scorecards |
|---|---------------------|---------------------|
| **Scope** | Development-time testing | Production conversation quality |
| **Data source** | Scripted test cases | Real customer conversations |
| **Scale** | Dozens to hundreds | Thousands to millions |
| **Purpose** | "Does the agent work correctly?" | "Is the agent serving customers well?" |
| **Timing** | Before deployment | After deployment |

Both are part of a complete quality strategy. You use evals to ensure correctness before deploying, and Insights to monitor quality after deploying.

---

## The `Insights` base class

The `Insights` class provides the HTTP client foundation for all Insights API operations:

```python
from cxas_scrapi.core.insights import Insights

insights = Insights(
    project_id="my-gcp-project",
    location="us-central1",  # or another supported region
    api_version="v1",
    creds=None,  # uses ADC by default
)
```

`Insights` handles authentication (refreshing tokens when needed), pagination, and the base HTTP request pattern for the CCAI Insights REST API.

You typically don't use `Insights` directly — instead, you use `Scorecards`, which inherits from it.

---

## The `Scorecards` class

The `Scorecards` class provides methods for managing QA scorecards and their questions:

```python
from cxas_scrapi.core.scorecards import Scorecards

scorecards = Scorecards(
    project_id="my-gcp-project",
    location="us-central1",
)
```

See [Scorecards](scorecards.md) for the full API reference and examples.

---

## CLI commands

The `cxas insights` command group provides CLI access to the Insights API:

```bash
cxas insights --help
```

Available subcommands:

| Subcommand | Description |
|------------|-------------|
| `cxas insights list` | List all scorecards |
| `cxas insights export` | Export a scorecard to JSON |
| `cxas insights import` | Import a scorecard from JSON |
| `cxas insights copy` | Copy a scorecard to another project or location |

---

## Getting started

The main Insights workflow for CX Agent Studio teams is:

1. **Design a scorecard** — define the quality criteria for your agent
2. **Create it** in the Insights API using `Scorecards.create_scorecard()`
3. **Apply it** to production conversations (configured in the Insights UI or API)
4. **Export results** for analysis and reporting

For a detailed walkthrough, see [Scorecards](scorecards.md).
