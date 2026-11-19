# Vega-Lite & SQL Recipes for CCAI Insights Configurable Dashboards

This cookbook provides ready-to-use **Vega-Lite specifications** and **BigQuery SQL queries** for authoring charts in CCAI Insights Configurable Dashboards.

> [!NOTE]
> - Every `sql_query` must query `FROM {INSIGHT_DASH_VIEW}`, which CCAI Insights binds to the project's conversations view at query time.
> - Include `data: {values: []}` in each `chart_spec`. `cxas insights push-dashboards` injects `$schema` and `data: {values: []}` onto non-empty `chart_spec` mappings when omitted, while direct REST calls require both to pass server-side Vega-Lite validation.

---

## 1. Score Card (`SCORE_CARD`)

Score cards display primary operational KPIs (e.g. Total Volume, Containment Rate, CSAT, Average Handle Time).

### Recipe 1.1: Total Conversations
```yaml
chart:
  display_name: "Total Inbound Calls"
  description: "Total number of conversations in the selected period"
  chart_visualization_type: "SCORE_CARD"
  width: 4
  height: 3
  data_source:
    generative_insights:
      sql_query: >-
        SELECT COUNT(DISTINCT conversation_id) AS total_calls
        FROM {INSIGHT_DASH_VIEW}
      chart_spec:
        data: {values: []}
        mark: "text"
        encoding:
          text: {field: "total_calls", type: "quantitative"}
```

### Recipe 1.2: Virtual Agent Containment Rate (%)
```yaml
chart:
  display_name: "Containment Rate"
  description: "Percentage of conversations handled without human agent escalation"
  chart_visualization_type: "SCORE_CARD"
  width: 4
  height: 3
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          ROUND(SAFE_DIVIDE(
            COUNTIF(
              EXISTS(SELECT 1 FROM UNNEST(agent_dimension_metadata) WHERE agent_type = 'AUTOMATED_AGENT')
              AND NOT COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)
            ),
            COUNTIF(EXISTS(SELECT 1 FROM UNNEST(agent_dimension_metadata) WHERE agent_type = 'AUTOMATED_AGENT'))
          ) * 100, 1) AS containment_rate
        FROM {INSIGHT_DASH_VIEW}
      chart_spec:
        data: {values: []}
        mark: "text"
        encoding:
          text: {field: "containment_rate", type: "quantitative"}
```

---

## 2. Bar Chart (`BAR`)

Bar charts compare metrics across categorical dimensions such as contact reasons, agent teams, or call dispositions.

### Recipe 2.1: Top 10 Issue Categories (Horizontal Bar)
```yaml
chart:
  display_name: "Top Contact Drivers"
  description: "Highest volume customer issue categories"
  chart_visualization_type: "BAR"
  width: 8
  height: 6
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          issue.issue_display_name AS issue_category,
          COUNT(1) AS call_count
        FROM {INSIGHT_DASH_VIEW}
        CROSS JOIN UNNEST(issue_dimension_metadata) AS issue
        WHERE issue.issue_display_name IS NOT NULL
        GROUP BY 1
        ORDER BY call_count DESC
        LIMIT 10
      chart_spec:
        data: {values: []}
        mark: "bar"
        encoding:
          x: {field: "call_count", type: "quantitative", title: "Total Calls"}
          y: {field: "issue_category", type: "nominal", sort: "-x", title: "Issue Category"}
          color: {value: "#1a73e8"}
```

### Recipe 2.2: Virtual Agent vs Human Agent Volume (Grouped/Stacked Bar)
```yaml
chart:
  display_name: "Agent Type Volume"
  chart_visualization_type: "BAR"
  width: 6
  height: 5
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          DATE(conversation_start_time) AS call_date,
          agent.agent_type AS agent_type,
          COUNT(1) AS volume
        FROM {INSIGHT_DASH_VIEW}
        CROSS JOIN UNNEST(agent_dimension_metadata) AS agent
        GROUP BY 1, 2
        ORDER BY call_date ASC
      chart_spec:
        data: {values: []}
        mark: "bar"
        encoding:
          x: {field: "call_date", type: "temporal", title: "Date"}
          y: {field: "volume", type: "quantitative", title: "Calls"}
          color: {field: "agent_type", type: "nominal", title: "Agent"}
```

---

## 3. Line Chart (`LINE`)

Line charts track time-series metrics, SLAs, and performance trends over time.

### Recipe 3.1: Daily Call Volume & Escalations Over Time
```yaml
chart:
  display_name: "Daily Conversation Volume Trend"
  description: "Total conversations vs escalated conversations per day"
  chart_visualization_type: "LINE"
  width: 12
  height: 6
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          DATE(conversation_start_time) AS date,
          COUNT(1) AS total_conversations,
          COUNTIF(
            COALESCE(dialogflow_interaction_aggregated_metadata.contains_live_agent_handoff, FALSE)
          ) AS escalations
        FROM {INSIGHT_DASH_VIEW}
        GROUP BY 1
        ORDER BY date ASC
      chart_spec:
        data: {values: []}
        transform:
          - fold: ["total_conversations", "escalations"]
            as: ["metric", "count"]
        mark: {"type": "line", "point": true}
        encoding:
          x: {field: "date", type: "temporal", title: "Date"}
          y: {field: "count", type: "quantitative", title: "Calls"}
          color: {field: "metric", type: "nominal", title: "Series"}
```

### Recipe 3.2: Average Handle Time (AHT) in Seconds
```yaml
chart:
  display_name: "Average Handle Time (Seconds)"
  chart_visualization_type: "LINE"
  width: 12
  height: 5
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          DATE(conversation_start_time) AS date,
          ROUND(AVG(duration_nanos / 1e9), 0) AS avg_duration_sec
        FROM {INSIGHT_DASH_VIEW}
        WHERE duration_nanos > 0
        GROUP BY 1
        ORDER BY date ASC
      chart_spec:
        data: {values: []}
        mark: "line"
        encoding:
          x: {field: "date", type: "temporal", title: "Date"}
          y: {field: "avg_duration_sec", type: "quantitative", title: "Duration (sec)"}
          color: {value: "#e37400"}
```

---

## 4. Pie / Donut Chart (`PIE`)

Pie charts show proportional shares of categorical attributes.

### Recipe 4.1: Customer Sentiment Distribution
```yaml
chart:
  display_name: "Customer Sentiment Breakdown"
  chart_visualization_type: "PIE"
  width: 6
  height: 5
  data_source:
    generative_insights:
      sql_query: >-
        SELECT
          CASE
            WHEN client_sentiment_score > 0.25 THEN 'POSITIVE'
            WHEN client_sentiment_score < -0.25 THEN 'NEGATIVE'
            ELSE 'NEUTRAL'
          END AS sentiment_category,
          COUNT(1) AS count
        FROM {INSIGHT_DASH_VIEW}
        WHERE client_sentiment_score IS NOT NULL
        GROUP BY 1
      chart_spec:
        data: {values: []}
        mark: {"type": "arc", "innerRadius": 50}
        encoding:
          theta: {field: "count", type: "quantitative"}
          color: {field: "sentiment_category", type: "nominal", title: "Sentiment"}
```

---

## 5. Table (`TABLE`)

Tables show detailed multi-column agent summaries or recent high-impact conversations.

### Recipe 5.1: Agent Quality Scorecard Summary
```yaml
chart:
  display_name: "Agent QA Leaderboard"
  chart_visualization_type: "TABLE"
  width: 12
  height: 6
  data_source:
    generative_insights:
      sql_query: >-
        -- Substitute agent.agent_display_name when full conversation metadata export is enabled.
        SELECT
          agent.agent_id AS agent_id,
          COUNT(DISTINCT conversation_id) AS evaluated_calls,
          ROUND(AVG(qa.normalized_score) * 100, 1) AS avg_qa_percentage,
          COUNT(DISTINCT IF(qa.normalized_score < 0.70, conversation_id, NULL)) AS low_score_calls
        FROM {INSIGHT_DASH_VIEW}
        CROSS JOIN UNNEST(agent_dimension_metadata) AS agent
        CROSS JOIN UNNEST(qa_scorecard_results) AS qa
        WHERE agent.agent_id IS NOT NULL AND qa.normalized_score IS NOT NULL
        GROUP BY 1
        ORDER BY avg_qa_percentage DESC
      chart_spec:
        data: {values: []}
        mark: "text"
        encoding:
          col01: {field: "agent_id", type: "nominal", title: "Agent ID"}
          col02: {field: "evaluated_calls", type: "quantitative", title: "Evaluated Calls"}
          col03: {field: "avg_qa_percentage", type: "quantitative", title: "Avg QA (%)"}
          col04: {field: "low_score_calls", type: "quantitative", title: "Low Score Calls"}
```
