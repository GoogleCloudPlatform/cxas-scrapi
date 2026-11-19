# CCAI Insights Configurable Dashboards: BigQuery SQL Cookbook & `{INSIGHT_DASH_VIEW}` Schema Reference

This reference guide documents the `{INSIGHT_DASH_VIEW}` BigQuery view schema in Contact Center AI (CCAI) Insights, followed by SQL recipes for **Configurable Dashboards**.

> [!IMPORTANT]
> Every Configurable Dashboard `generative_insights.sql_query` must query `FROM {INSIGHT_DASH_VIEW}`. CCAI Insights binds `{INSIGHT_DASH_VIEW}` to the project's deduplicated conversations view at query time. Querying `FROM conversations` directly fails in Configurable Dashboards.

---

## 1. `{INSIGHT_DASH_VIEW}` Column Definitions

`{INSIGHT_DASH_VIEW}` provides a denormalized, queryable record for each processed customer conversation in CCAI Insights.

> [!NOTE]
> Projects without full conversation metadata export enabled omit fields such as `custom_labels`, `agent_display_name`, `agent_team`, and per-question text or rationale from `{INSIGHT_DASH_VIEW}`. Use `agent_id` and `normalized_score` when targeting restricted views.

Find the column definitions in the following table.

|  Name  |  Type  |  Definition |
| ---- | ---- | --------- |
| conversation_id | STRING (NULLABLE) | Unique identifier of the conversation resource. |
| conversation_create_time | TIMESTAMP (NULLABLE) | Ingestion timestamp when the conversation was loaded into Insights. |
| conversation_update_time | TIMESTAMP (NULLABLE) | Timestamp of the last mutation (update) on the conversation. |
| conversation_start_time | TIMESTAMP (NULLABLE) | Timestamp when the conversation originally occurred. |
| conversation_expiry_time | TIMESTAMP (NULLABLE) | Timestamp when the conversation is scheduled for deletion under TTL. |
| conversation_export_time | TIMESTAMP (NULLABLE) | Timestamp when the record was written to BigQuery. |
| conversation_ccai_data_source | RECORD (NULLABLE) | Source mapping (contains dialogflow_conversation resource path). |
| latest_analysis_time | TIMESTAMP (NULLABLE) | Timestamp of the most recent NLP / QM analysis execution. |
| turn_count | INTEGER (NULLABLE) | Total number of conversational turns. |
| duration_nanos | INTEGER (NULLABLE) | Total conversation duration in nanoseconds. |
| silence_percentage | FLOAT (NULLABLE) | Percentage of call duration spent in silence / dead air. |
| agent_sentiment_score | FLOAT (NULLABLE) | Overall agent sentiment score (range: -1.0 to +1.0). |
| client_sentiment_score | FLOAT (NULLABLE) | Overall client sentiment score (range: -1.0 to +1.0). |
| customer_satisfaction_rating | INTEGER (NULLABLE) | Explicit Customer Satisfaction rating (CSAT). |
| client_sentiment_data | RECORD (NULLABLE) | Turn sentiment trajectory (e.g. exponential_moving_average_of_client_turn_sentiment_score). |
| issue_dimension_metadata | RECORD (REPEATED) | Issue model topic assignments (issue_id, issue_display_name, issue_model_id). |
| agent_dimension_metadata | RECORD (REPEATED) | Metadata on human or automated agents (agent_id, agent_display_name, agent_team, agent_type, deployment_id, version_id). |
| end_user_dimension_metadata | RECORD (NULLABLE) | End-user identifiers (obfuscated_external_user_id, end_user_info). |
| qa_scorecard_results | RECORD (REPEATED) | Quality Management scorecard results (qa_scorecard_id, qa_scorecard, normalized_score, score, potential_score, qa_tag_results, qa_question_scores). |
| correlation_info | RECORD (NULLABLE) | Multi-segment call correlation & stitching IDs (full_conversation_correlation_id, correlation_types). |
| medium | STRING (NULLABLE) | Communication channel (PHONE_CALL, CHAT). |
| custom_labels | RECORD (REPEATED) | Customer-defined metadata key-value pairs (label_key, label_value). |
| knowledge_search_results | RECORD (REPEATED) | Generative Knowledge Assist (GKA) search annotations, citations, URLs, and feedback. |
| knowledge_assist_results | RECORD (REPEATED) | Proactive Knowledge Assist (PGKA) suggestions, source documents, click feedback, and dismissals. |
| dialogflow_conversation_profile_id | STRING (NULLABLE) | Dialogflow conversation profile ID. |
| summarization_results | RECORD (REPEATED) | Summaries, agent feedback summaries, edit distance insertions/deletions, and generator flags. |
| smart_reply_results | RECORD (REPEATED) | Smart Reply suggestions, display status, and click events. |
| dialogflow_interaction_data | RECORD (REPEATED) | Per-turn interaction details (intents, match types, webhooks, tool latencies, LLM/TTS latencies, end-to-end latency). |
| dialogflow_interaction_aggregated_metadata | RECORD (NULLABLE) | Aggregated interaction flags (contains_live_agent_handoff). |
| conversational_agent_tool_data | RECORD (REPEATED) | Conversation-level tool aggregation (cumulative_latency_ms, successful_invocation_count, total_invocation_count). |
| conversational_agent_playbook_data | RECORD (REPEATED) | Conversation-level playbook usage (playbook_id, display_name). |
| ai_coach_results | RECORD (REPEATED) | Real-time AI Agent Coaching suggestions, triggers, tool calls, and agent adoption. |
| total_customer_message_count | INTEGER (NULLABLE) | Total count of messages sent by the customer. |
| total_agent_message_count | INTEGER (NULLABLE) | Total count of messages sent by the agent. |
| aa_supervisor_monitoring_status | RECORD (NULLABLE) | Agent Assist supervisor monitoring state, supervisor ID/username, and escalation events. |
| ccai_transcription_metadata | RECORD (NULLABLE) | Cloud Speech-to-Text configuration (language code, alternative languages, STT model, phrase sets). |
| user_agent_interaction_events | RECORD (REPEATED) | Detailed participant event log with timestamps and roles. |
| user_agent_interaction_histogram | RECORD (REPEATED) | Interaction time interval windows (start_time, end_time). |

---

## 2. SQL Recipes by Operational Category

### 2.1 Volume & Capacity Metrics

#### Total Inbound Volume (Scorecard)
```sql
SELECT
  COUNT(DISTINCT conversation_id) AS total_conversations
FROM {INSIGHT_DASH_VIEW}
```

#### Hourly Call Volume Heatmap / Distribution (Bar Chart)
```sql
SELECT
  EXTRACT(HOUR FROM conversation_start_time) AS hour_of_day,
  COUNT(1) AS call_volume
FROM {INSIGHT_DASH_VIEW}
GROUP BY 1
ORDER BY hour_of_day ASC
```

#### Volume Breakdown by Channel / Medium (Pie Chart)
```sql
SELECT
  COALESCE(medium, 'UNKNOWN') AS medium,
  COUNT(1) AS volume
FROM {INSIGHT_DASH_VIEW}
GROUP BY 1
ORDER BY volume DESC
```

---

### 2.2 Virtual Agent Containment & Escalation

#### Virtual Agent Containment Rate (%) (Scorecard)
```sql
SELECT
  ROUND(SAFE_DIVIDE(
    COUNTIF(
      EXISTS(SELECT 1 FROM UNNEST(agent_dimension_metadata) WHERE agent_type = 'AUTOMATED_AGENT')
      AND NOT COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)
    ),
    COUNTIF(EXISTS(SELECT 1 FROM UNNEST(agent_dimension_metadata) WHERE agent_type = 'AUTOMATED_AGENT'))
  ) * 100, 1) AS containment_percentage
FROM {INSIGHT_DASH_VIEW}
```

#### Daily Escalation Trend (Line Chart)
```sql
SELECT
  DATE(conversation_start_time) AS call_date,
  COUNT(1) AS total_calls,
  COUNTIF(COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)) AS escalated_calls,
  ROUND(SAFE_DIVIDE(
    COUNTIF(COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)),
    COUNT(1)
  ) * 100, 1) AS escalation_rate
FROM {INSIGHT_DASH_VIEW}
GROUP BY 1
ORDER BY call_date ASC
```

---

### 2.3 Handle Time & Silence Analysis

#### Average Handle Time (AHT) in Seconds by Issue Category (Bar Chart)
```sql
SELECT
  issue.issue_display_name AS issue_category,
  ROUND(AVG(duration_nanos / 1e9), 0) AS avg_duration_sec
FROM {INSIGHT_DASH_VIEW}
CROSS JOIN UNNEST(issue_dimension_metadata) AS issue
WHERE duration_nanos > 0
GROUP BY 1
ORDER BY avg_duration_sec DESC
LIMIT 10
```

#### High-Silence Outlier Conversations (Table)
```sql
-- Substitute agent.agent_display_name when full conversation metadata export is enabled.
SELECT
  conversation_id,
  agent.agent_id AS agent_id,
  ROUND(duration_nanos / 1e9, 0) AS duration_seconds,
  silence_percentage,
  turn_count
FROM {INSIGHT_DASH_VIEW}
LEFT JOIN UNNEST(agent_dimension_metadata) AS agent
WHERE silence_percentage > 25.0 AND (duration_nanos / 1e9) > 180
ORDER BY silence_percentage DESC
LIMIT 25
```

---

### 2.4 Customer Sentiment & Satisfaction (CSAT)

#### Customer Sentiment Breakdown (Donut Chart)
```sql
SELECT
  CASE
    WHEN client_sentiment_score > 0.25 THEN 'POSITIVE'
    WHEN client_sentiment_score < -0.25 THEN 'NEGATIVE'
    ELSE 'NEUTRAL'
  END AS sentiment_category,
  COUNT(1) AS count,
  ROUND(SAFE_DIVIDE(COUNT(1) * 100.0, SUM(COUNT(1)) OVER ()), 1) AS percentage
FROM {INSIGHT_DASH_VIEW}
WHERE client_sentiment_score IS NOT NULL
GROUP BY 1
ORDER BY count DESC
```

#### Average Sentiment Score Trend by Day (Line Chart)
```sql
SELECT
  DATE(conversation_start_time) AS date,
  ROUND(AVG(client_sentiment_score), 2) AS avg_sentiment_score
FROM {INSIGHT_DASH_VIEW}
WHERE client_sentiment_score IS NOT NULL
GROUP BY 1
ORDER BY date ASC
```

---

### 2.5 Quality Assurance (QA) & Scorecard Adherence

#### Agent QA Scorecard Leaderboard (Table)
```sql
-- Substitute agent.agent_display_name when full conversation metadata export is enabled.
SELECT
  agent.agent_id AS agent_id,
  COUNT(DISTINCT conversation_id) AS evaluated_conversations,
  ROUND(AVG(qa.normalized_score) * 100, 1) AS average_qa_score
FROM {INSIGHT_DASH_VIEW}
CROSS JOIN UNNEST(agent_dimension_metadata) AS agent
CROSS JOIN UNNEST(qa_scorecard_results) AS qa
WHERE agent.agent_id IS NOT NULL AND qa.normalized_score IS NOT NULL
GROUP BY 1
HAVING evaluated_conversations >= 5
ORDER BY average_qa_score DESC
```

#### Low QA Evaluation Trend Over Time (Line / Area Chart)
```sql
SELECT
  DATE(conversation_start_time) AS date,
  COUNTIF(qa.normalized_score < 0.70) AS low_score_count,
  ROUND(SAFE_DIVIDE(COUNTIF(qa.normalized_score < 0.70), COUNT(1)) * 100, 2) AS low_score_percentage
FROM {INSIGHT_DASH_VIEW}
CROSS JOIN UNNEST(qa_scorecard_results) AS qa
WHERE qa.normalized_score IS NOT NULL
GROUP BY 1
ORDER BY date ASC
```

---

### 2.6 Topic Modeling & Contact Drivers

#### Top 10 Contact Drivers with Escalation Share (Stacked Bar Chart)
```sql
SELECT
  issue.issue_display_name AS issue_category,
  COUNT(1) AS total_conversations,
  COUNTIF(COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)) AS escalated_count
FROM {INSIGHT_DASH_VIEW}
CROSS JOIN UNNEST(issue_dimension_metadata) AS issue
GROUP BY 1
ORDER BY total_conversations DESC
LIMIT 10
```

---

### 2.7 Autolabels & Custom Metadata Extraction

#### Extracting Autolabel Key-Value Pairs from `custom_labels` (REPEATED)
```sql
-- Requires full conversation metadata export (custom_labels is omitted from restricted views).
SELECT
  labels.label_value AS agent_domain,
  COUNT(1) AS total_calls,
  ROUND(AVG(duration_nanos / 1e9), 0) AS avg_handle_time_sec
FROM {INSIGHT_DASH_VIEW}
CROSS JOIN UNNEST(custom_labels) AS labels
WHERE labels.label_key = 'agent_domain'
GROUP BY 1
ORDER BY total_calls DESC
```

---

## 3. BigQuery SQL Authoring Guidelines for Configurable Dashboards

1. **Partition Pruning**: Always filter by `conversation_start_time` (or use the dashboard-level date range filter) to optimize BigQuery scan cost and dashboard latency.
2. **Safe Division**: Use `SAFE_DIVIDE(numerator, denominator)` instead of standard `/` to prevent divide-by-zero runtime exceptions.
3. **Null Handling**: Wrap string and category fields in `COALESCE(field, 'UNKNOWN')` to avoid orphaned Vega-Lite legend keys.
4. **Column Naming**: Name SQL projection columns matching the `field` encodings defined in the Vega-Lite `chart_spec` (e.g. `total_conversations`, `call_date`, `avg_qa_score`).
