# CCAI Insights Configurable Dashboards: BigQuery SQL Cookbook & `conversations` Table Reference

This reference guide provides a complete specification of the mirrored **`conversations`** BigQuery table schema in Contact Center AI (CCAI) Insights, followed by production-ready SQL recipes for authoring **Configurable Dashboards**.

---

## 1. `conversations` Table Column Definitions

The `conversations` table provides a denormalized, queryable record for each processed customer conversation in CCAI Insights.

| Column Name | Data Type | Mode | Description | Example Values |
| :--- | :--- | :--- | :--- | :--- |
| `conversation_id` | `STRING` | `REQUIRED` | Unique identifier for the conversation resource (`projects/*/locations/*/conversations/*`). | `"conv-89412a-45b7"` |
| `project_id` | `STRING` | `REQUIRED` | Google Cloud project ID hosting the CCAI Insights dataset. | `"my-cx-project"` |
| `location` | `STRING` | `REQUIRED` | Google Cloud region where the conversation is stored. | `"us-central1"`, `"global"` |
| `start_time` | `TIMESTAMP` | `REQUIRED` | The timestamp when the conversation began (call connect or session start). | `2026-08-26 14:30:00 UTC` |
| `end_time` | `TIMESTAMP` | `NULLABLE` | The timestamp when the conversation ended. | `2026-08-26 14:38:15 UTC` |
| `duration_seconds` | `FLOAT64` | `NULLABLE` | Total duration of the conversation in seconds (`end_time - start_time`). | `495.0`, `120.5` |
| `agent_id` | `STRING` | `NULLABLE` | Identifier or user ID of the primary human or virtual agent. | `"agent_sarah_102"`, `"billing-bot-v2"` |
| `agent_name` | `STRING` | `NULLABLE` | Display name of the agent assigned to or handling the conversation. | `"Sarah Jenkins"`, `"Virtual Assistant"` |
| `agent_type` | `STRING` | `NULLABLE` | Category of the agent handling the interaction: `VIRTUAL_AGENT`, `HUMAN_AGENT`, or `HYBRID`. | `"VIRTUAL_AGENT"` |
| `medium` | `STRING` | `NULLABLE` | Communication channel/medium: `PHONE_CALL`, `CHAT`, `SMS`, `EMAIL`. | `"PHONE_CALL"`, `"CHAT"` |
| `language_code` | `STRING` | `NULLABLE` | BCP-47 language tag detected or configured for the interaction. | `"en-US"`, `"es-US"`, `"fr-CA"` |
| `turn_count` | `INT64` | `NULLABLE` | Total number of conversational dialogue turns between customer and agent. | `14`, `28` |
| `sentiment_category` | `STRING` | `NULLABLE` | Overall customer sentiment classification: `POSITIVE`, `NEUTRAL`, `NEGATIVE`. | `"NEGATIVE"`, `"POSITIVE"` |
| `sentiment_score` | `FLOAT64` | `NULLABLE` | Numerical sentiment score ranging from `-1.0` (very negative) to `+1.0` (very positive). | `-0.65`, `0.82` |
| `sentiment_magnitude` | `FLOAT64` | `NULLABLE` | Magnitude / emotional strength of the sentiment (ranges from `0.0` to `+inf`). | `3.4`, `0.8` |
| `issue_category` | `STRING` | `NULLABLE` | Primary topic model cluster or issue category identified by CCAI Insights. | `"Billing & Invoicing"`, `"Password Reset"` |
| `issue_subcategory` | `STRING` | `NULLABLE` | Granular issue subtype or sub-intent cluster. | `"Overcharge Dispute"`, `"Account Unlock"` |
| `summary` | `STRING` | `NULLABLE` | Generative AI conversation summary (executive summary of customer intent, action, and resolution). | `"Customer called regarding fee waiver..."` |
| `qa_score` | `FLOAT64` | `NULLABLE` | Overall Scorecard QA evaluation score (percentage normalized `0.0` - `1.0` or `0` - `100`). | `0.92`, `85.0` |
| `compliance_violation` | `BOOL` | `NULLABLE` | Boolean flag indicating whether a compliance or critical scorecard rule was violated. | `TRUE`, `FALSE` |
| `labels` | `STRING` | `NULLABLE` | Comma-separated or formatted string containing conversation metadata and autolabel keys. | `"tier=vip,escalated=true,region=west"` |
| `silence_percentage` | `FLOAT64` | `NULLABLE` | Percentage of call duration consisting of silence / dead air (`0.0` to `100.0`). | `12.5` |
| `interruption_count` | `INT64` | `NULLABLE` | Number of times participants spoke simultaneously / interrupted each other. | `3`, `0` |
| `hold_duration_seconds` | `FLOAT64` | `NULLABLE` | Total duration customer spent on hold across all hold events. | `45.0`, `0.0` |

---

## 2. SQL Recipes by Operational Category

### 2.1 Volume & Capacity Metrics

#### Total Inbound Volume (Scorecard)
```sql
SELECT
  COUNT(DISTINCT conversation_id) AS total_conversations
FROM conversations
WHERE start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
```

#### Hourly Call Volume Heatmap / Distribution (Bar Chart)
```sql
SELECT
  EXTRACT(HOUR FROM start_time) AS hour_of_day,
  COUNT(1) AS call_volume
FROM conversations
WHERE start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY 1
ORDER BY hour_of_day ASC
```

#### Volume Breakdown by Channel / Medium (Pie Chart)
```sql
SELECT
  COALESCE(medium, 'UNKNOWN') AS medium,
  COUNT(1) AS volume
FROM conversations
GROUP BY 1
ORDER BY volume DESC
```

---

### 2.2 Virtual Agent Containment & Escalation

#### Virtual Agent Containment Rate (%) (Scorecard)
```sql
SELECT
  ROUND(SAFE_DIVIDE(
    COUNTIF(agent_type = 'VIRTUAL_AGENT' AND NOT REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')),
    COUNTIF(agent_type = 'VIRTUAL_AGENT' OR REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)virtual_agent'))
  ) * 100, 1) AS containment_percentage
FROM conversations
WHERE start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
```

#### Daily Escalation Trend (Line Chart)
```sql
SELECT
  DATE(start_time) AS call_date,
  COUNT(1) AS total_calls,
  COUNTIF(REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')) AS escalated_calls,
  ROUND(SAFE_DIVIDE(
    COUNTIF(REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')),
    COUNT(1)
  ) * 100, 1) AS escalation_rate
FROM conversations
GROUP BY 1
ORDER BY call_date ASC
```

---

### 2.3 Handle Time & Silence Analysis

#### Average Handle Time (AHT) in Seconds by Issue Category (Bar Chart)
```sql
SELECT
  issue_category,
  ROUND(AVG(duration_seconds), 0) AS avg_duration_sec,
  ROUND(AVG(hold_duration_seconds), 0) AS avg_hold_sec
FROM conversations
WHERE issue_category IS NOT NULL AND duration_seconds > 0
GROUP BY 1
ORDER BY avg_duration_sec DESC
LIMIT 10
```

#### High-Silence Outlier Conversations (Table)
```sql
SELECT
  conversation_id,
  agent_name,
  duration_seconds,
  silence_percentage,
  interruption_count,
  summary
FROM conversations
WHERE silence_percentage > 25.0 AND duration_seconds > 180
ORDER BY silence_percentage DESC
LIMIT 25
```

---

### 2.4 Customer Sentiment & Satisfaction (CSAT)

#### Customer Sentiment Breakdown (Donut Chart)
```sql
SELECT
  COALESCE(sentiment_category, 'NEUTRAL') AS sentiment,
  COUNT(1) AS count,
  ROUND(COUNT(1) * 100.0 / SUM(COUNT(1)) OVER(), 1) AS percentage
FROM conversations
GROUP BY 1
ORDER BY count DESC
```

#### Average Sentiment Score Trend by Agent Group (Line Chart)
```sql
SELECT
  DATE(start_time) AS date,
  ROUND(AVG(sentiment_score), 2) AS avg_sentiment_score
FROM conversations
WHERE sentiment_score IS NOT NULL
GROUP BY 1
ORDER BY date ASC
```

---

### 2.5 Quality Assurance (QA) & Scorecard Adherence

#### Agent QA Scorecard Leaderboard (Table)
```sql
SELECT
  agent_name,
  COUNT(1) AS evaluated_conversations,
  ROUND(AVG(qa_score) * 100, 1) AS average_qa_score,
  COUNTIF(compliance_violation = TRUE) AS compliance_violations
FROM conversations
WHERE agent_name IS NOT NULL AND qa_score IS NOT NULL
GROUP BY 1
HAVING evaluated_conversations >= 5
ORDER BY average_qa_score DESC
```

#### Compliance Violation Rate Over Time (Line / Area Chart)
```sql
SELECT
  DATE(start_time) AS date,
  COUNTIF(compliance_violation = TRUE) AS violation_count,
  ROUND(SAFE_DIVIDE(COUNTIF(compliance_violation = TRUE), COUNT(1)) * 100, 2) AS violation_percentage
FROM conversations
GROUP BY 1
ORDER BY date ASC
```

---

### 2.6 Topic Modeling & Contact Drivers

#### Top 10 Contact Drivers with Escalation Share (Stacked Bar Chart)
```sql
SELECT
  issue_category,
  COUNT(1) AS total_conversations,
  COUNTIF(REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')) AS escalated_count
FROM conversations
WHERE issue_category IS NOT NULL
GROUP BY 1
ORDER BY total_conversations DESC
LIMIT 10
```

---

### 2.7 Autolabels & Custom Metadata Extraction

#### Extracting Autolabel Key-Value Pairs from `labels` String
```sql
SELECT
  REGEXP_EXTRACT(labels, r'agent_domain=([^,]+)') AS agent_domain,
  COUNT(1) AS total_calls,
  ROUND(AVG(duration_seconds), 0) AS avg_handle_time
FROM conversations
WHERE REGEXP_CONTAINS(COALESCE(labels, ''), r'agent_domain=')
GROUP BY 1
ORDER BY total_calls DESC
```

---

## 3. BigQuery SQL Authoring Guidelines for Configurable Dashboards

1. **Partition Pruning**: Always filter by `start_time` (or use the dashboard-level date range filter) to optimize BigQuery scan cost and dashboard latency.
2. **Safe Division**: Use `SAFE_DIVIDE(numerator, denominator)` instead of standard `/` to prevent divide-by-zero runtime exceptions.
3. **Null Handling**: Wrap string and category fields in `COALESCE(field, 'UNKNOWN')` to avoid orphaned Vega-Lite legend keys.
4. **Column Naming**: Name SQL projection columns matching the `field` encodings defined in the Vega-Lite `chart_spec` (e.g. `total_conversations`, `call_date`, `avg_qa_score`).
