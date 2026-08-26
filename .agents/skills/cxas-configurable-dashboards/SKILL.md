---
name: cxas-configurable-dashboards
description: >-
  Author, validate, and manage Contact Center AI (CCAI) Insights Configurable Dashboards.
  Use when users want to define multi-tab analytics dashboards, configure Vega-Lite charts
  and SQL queries, maintain declarative dashboards.yaml configurations, or synchronize dashboards to GCP projects.
---

# CCAI Insights Configurable Dashboards Skill

This skill guides you in authoring, refining, validating, and synchronizing **Contact Center AI (CCAI) Insights Configurable Dashboards**.

CCAI Insights Configurable Dashboards allow users to build customizable, multi-tab reporting views with rich visualization widgets (Score Cards, Bar/Line charts, Pie charts, Tables, Sankey diagrams) powered by Vega-Lite specifications and SQL queries against conversation metrics.

______________________________________________________________________

## 1. Overview & Declarative YAML Schema

Dashboards are defined declaratively in `dashboards.yaml`:

```yaml
version: "1.0"
project_id: "your-gcp-project-id"
location: "us-central1"

dashboards:
  - dashboard_id: "executive_kpis"
    display_name: "Executive Contact Center KPIs"
    description: "High-level summary of inbound call volumes, virtual agent containment, and quality."
    date_range:
      relative:
        quantity: 7
        unit: "DAY"

    root_container:
      display_name: "Root"
      widgets:
        - container:
            display_name: "Overview Tab"
            description: "Operational summary metrics"
            widgets:
              # Tile 1: Total Volume Scorecard
              - chart:
                  display_name: "Total Conversations"
                  chart_visualization_type: "SCORE_CARD"
                  width: 4
                  height: 3
                  data_source:
                    generative_insights:
                      sql_query: "SELECT COUNT(DISTINCT conversation_id) AS total_calls FROM conversations"
                      chart_spec:
                        mark: "text"
                        encoding:
                          text: {field: "total_calls", type: "quantitative"}

              # Tile 2: Top Contact Drivers Bar Chart
              - chart:
                  display_name: "Top Contact Drivers"
                  chart_visualization_type: "BAR"
                  width: 8
                  height: 6
                  data_source:
                    generative_insights:
                      sql_query: >-
                        SELECT issue_category, COUNT(1) AS volume
                        FROM conversations
                        WHERE issue_category IS NOT NULL
                        GROUP BY 1
                        ORDER BY volume DESC
                        LIMIT 10
                      chart_spec:
                        mark: "bar"
                        encoding:
                          x: {field: "volume", type: "quantitative", title: "Calls"}
                          y: {field: "issue_category", type: "nominal", sort: "-x", title: "Category"}
```

### Core Structural Requirements

1. **Root Container Constraint** (`ValidateDashboardStructure`):
   - Every dashboard must have a `root_container`.
   - Direct widgets in `root_container` **must all be `Container` widgets** representing tabs/sections.
2. **Widgets within Tabs**:
   - Each tab container contains child widgets (`container` for sub-grouping, `chart` for visualizations, or `chart_reference` for linked charts).
3. **Chart Visualizations**:
   - `chart_visualization_type`: `SCORE_CARD`, `BAR`, `LINE`, `AREA`, `PIE`, `SCATTER`, `TABLE`, `SANKEY`.
   - `data_source`: Contains `generative_insights` with `sql_query` and Vega-Lite `chart_spec`.

______________________________________________________________________

## 2. Vega-Lite & SQL Recipes

Refer to the [Vega-Lite & SQL Cookbook](references/vega_cookbook.md) for full chart patterns, encodings, and BigQuery query templates.

### Common Patterns

- **Scorecard (Single KPI)**:
  ```yaml
  chart_visualization_type: "SCORE_CARD"
  data_source:
    generative_insights:
      sql_query: "SELECT COUNT(1) AS total FROM conversations"
      chart_spec:
        mark: "text"
        encoding:
          text: {field: "total", type: "quantitative"}
  ```
- **Time Series Trend (Line Chart)**:
  ```yaml
  chart_visualization_type: "LINE"
  data_source:
    generative_insights:
      sql_query: "SELECT DATE(start_time) AS date, COUNT(1) AS calls FROM conversations GROUP BY 1"
      chart_spec:
        mark: "line"
        encoding:
          x: {field: "date", type: "temporal"}
          y: {field: "calls", type: "quantitative"}
  ```

______________________________________________________________________

## 3. Step-by-Step Workflow

### Step 1: Ingest Requirements
- Ask the user what operational metrics, KPIs, or tabs they need (e.g. Agent QA performance, Containment %, Top Contact Drivers, CSAT trends).
- Identify the target GCP project ID and location.

### Step 2: Draft or Edit Declarative YAML
- Create or update `dashboards.yaml` in the user's workspace.
- Structure tabs inside `root_container.widgets`.
- Add scorecards, bar charts, and line charts with matching Vega-Lite specs and SQL queries.

### Step 3: Compare with Active Remote Dashboards (`diff`)
Run `diff` to preview additions, modifications, and deletions:
```bash
uv run cxas insights diff-dashboards --file dashboards.yaml
```

### Step 4: Dry-Run Deploy
Verify planned operations against GCP without mutating resources:
```bash
uv run cxas insights push-dashboards --file dashboards.yaml --dry-run
```

### Step 5: Push to GCP
Deploy new and updated dashboards to Contact Center AI Insights:
```bash
uv run cxas insights push-dashboards --file dashboards.yaml
```

If deleting obsolete remote dashboards:
```bash
uv run cxas insights push-dashboards --file dashboards.yaml --force
```

______________________________________________________________________

## 4. CLI Command Reference

- **Pull Remote Dashboards**:
  ```bash
  uv run cxas insights pull-dashboards --parent projects/PROJECT_ID/locations/LOCATION [--out dashboards.yaml]
  ```
- **Diff Dashboards**:
  ```bash
  uv run cxas insights diff-dashboards --file dashboards.yaml
  ```
- **Push / Sync Dashboards**:
  ```bash
  uv run cxas insights push-dashboards --file dashboards.yaml [--dry-run] [--force]
  ```
- **List Dashboards**:
  ```bash
  uv run cxas insights list-dashboards --parent projects/PROJECT_ID/locations/LOCATION
  ```
- **Get Dashboard**:
  ```bash
  uv run cxas insights get-dashboard --dashboard-name projects/PROJECT_ID/locations/LOCATION/dashboards/DASHBOARD_ID
  ```
- **Delete Dashboard**:
  ```bash
  uv run cxas insights delete-dashboard --dashboard-name projects/PROJECT_ID/locations/LOCATION/dashboards/DASHBOARD_ID
  ```
