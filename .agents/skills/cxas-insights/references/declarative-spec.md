# Declarative Configuration Specification: `insights_config.yaml`

This document defines the schema and options for declarative Insights configuration files used by the `InsightsReconciler` engine and the `cxas-insights` skill.

---

## Schema Overview

```yaml
version: 1
project_id: "my-gcp-project"
location: "us-central1"

scorecards:
  - template: "scorecards/customer_satisfaction.yaml"
    scorecard_id: "csat-scorecard"  # Optional. Defaults to slugified displayName.
    deploy: true                   # Automatically deploy revision to READY state.
    apply_to:
      - rule_id: "live_csat_streaming_rule"
        display_name: "Streaming CSAT Rule"
        filter: "latest_agent_version = 'v2'"
        percentage: 100
      - backfill:
        filter: "create_time >= '2026-01-01T00:00:00Z'"
        percentage: 100
```

---

## Field Reference

### Top-Level Fields

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `version` | Integer | Yes | Configuration version. Currently `1`. |
| `project_id` | String | Yes | Google Cloud Project ID hosting CCAI Insights. |
| `location` | String | No | Region (e.g. `us-central1`, `global`). Defaults to `us-central1`. |
| `scorecards` | List | Yes | List of scorecard definitions to manage. |

---

### `scorecards[]` Object

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `template` | String | Yes | Relative or absolute path to the local scorecard YAML or JSON template file. |
| `scorecard_id` | String | No | Unique ID for the scorecard under `projects/*/locations/*/qaScorecards/*`. If omitted, generated from `displayName`. |
| `deploy` | Boolean | No | Whether to deploy the revision. Defaults to `true`. |
| `apply_to` | List | No | List of streaming analysis rules and historical backfills to configure for this scorecard. |

---

### `apply_to[]` Instructions

Each entry in `apply_to` can be either a **Streaming Rule** or a **Historical Backfill**:

#### 1. Streaming Rule (`rule_id`)
Configures a real-time `AnalysisRule` that automatically analyzes new incoming conversations.

```yaml
- rule_id: "live_csat_streaming_rule"
  display_name: "Streaming CSAT Rule for V2 Agent"
  filter: "agent_id = 'my-agent-uuid'"
  percentage: 100
  active: true
```

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `rule_id` | String | (Required) | Unique identifier for the analysis rule. |
| `display_name` | String | `rule_id` | Human-readable name for the rule. |
| `filter` | String | `""` | CEL filter string matching conversation metadata. |
| `percentage` | Number (1-100) | `100` | Traffic sampling percentage to evaluate. |
| `active` | Boolean | `true` | Whether the rule is active and running. |

#### 2. Historical Backfill (`backfill`)
Performs coverage gap analysis and triggers chunked `bulkAnalyze` operations on historical conversations missing this scorecard.

```yaml
- backfill: true
  filter: "create_time >= '2026-06-01T00:00:00Z'"
  percentage: 100
```

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `backfill` | Boolean / Key | (Required) | Flags instruction as a historical backfill. |
| `filter` | String | `""` | CEL filter defining the historical time window or conversation scope. |
| `percentage` | Number (1-100) | `100` | Percentage of matching missing conversations to analyze. |
