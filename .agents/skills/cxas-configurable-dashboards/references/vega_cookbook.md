# Vega-Lite & SQL Recipes for CCAI Insights Configurable Dashboards

This cookbook provides ready-to-use **Vega-Lite specifications** and **BigQuery SQL queries** for authoring charts in CCAI Insights Configurable Dashboards.

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
        FROM conversations
      chart_spec:
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
            COUNTIF(NOT REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')),
            COUNT(1)
          ) * 100, 1) AS containment_rate
        FROM conversations
      chart_spec:
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
          issue_category,
          COUNT(1) AS call_count
        FROM conversations
        WHERE issue_category IS NOT NULL
        GROUP BY 1
        ORDER BY call_count DESC
        LIMIT 10
      chart_spec:
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
          DATE(start_time) AS call_date,
          agent_type,
          COUNT(1) AS volume
        FROM conversations
        GROUP BY 1, 2
        ORDER BY call_date ASC
      chart_spec:
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
          DATE(start_time) AS date,
          COUNT(1) AS total_conversations,
          COUNTIF(REGEXP_CONTAINS(COALESCE(labels, ''), '(?i)escalat')) AS escalations
        FROM conversations
        GROUP BY 1
        ORDER BY date ASC
      chart_spec:
        mark: {"type": "line", "point": true}
        encoding:
          x: {field: "date", type: "temporal", title: "Date"}
          y: {field: "total_conversations", type: "quantitative", title: "Calls"}
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
          DATE(start_time) AS date,
          ROUND(AVG(duration_seconds), 0) AS avg_duration_sec
        FROM conversations
        GROUP BY 1
        ORDER BY date ASC
      chart_spec:
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
          sentiment_category,
          COUNT(1) AS count
        FROM conversations
        WHERE sentiment_category IS NOT NULL
        GROUP BY 1
      chart_spec:
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
        SELECT
          agent_name,
          COUNT(1) AS evaluated_calls,
          ROUND(AVG(qa_score) * 100, 1) AS avg_qa_percentage,
          COUNTIF(compliance_violation = TRUE) AS compliance_violations
        FROM conversations
        WHERE agent_name IS NOT NULL
        GROUP BY 1
        ORDER BY avg_qa_percentage DESC
      chart_spec:
        mark: "table"
```
