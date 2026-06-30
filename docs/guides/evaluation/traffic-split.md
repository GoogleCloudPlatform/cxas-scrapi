# Traffic Splitting Guide (SCRAPI CLI & Python SDK)

This guide describes how to set up traffic splitting (A/B testing) for your Conversational Agents (CXAS) application using either the **SCRAPI CLI (`cxas`)** or the **SCRAPI Python SDK**, and analyze the results using BigQuery logs.

## Workflow Overview

1.  **Promote Deployment & Setup Traffic Split**: Create or update a deployment with multiple app versions and allocate traffic percentages using `cxas` CLI or the Python SDK.
2.  **Verify Deployment**: Retrieve the deployment list using `cxas` CLI or Python SDK to confirm the split is active.
3.  **Analyze Logs in BigQuery**: Once your deployment receives live customer traffic, evaluate version performance (e.g., tool execution reliability and task completion rates) either manually via SQL or automatically using AI agents.

---

## 1. Promote Deployment & Setup Traffic Split

You can configure traffic splitting using either the SCRAPI CLI or the Python SDK.

### Method A: Using SCRAPI CLI (`cxas`)

The SCRAPI CLI allows you to create or promote deployments with traffic splitting. The `--traffic-split` flag allocates percentages to different version UUIDs (must sum to 100%).

#### Option 1: Create a new deployment with traffic split
If you are creating a new deployment channel:
```bash
cxas deployments create \
  --app-name projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID} \
  --deployment-id {DEPLOYMENT_ID} \
  --traffic-split "{VERSION_A_UUID}:{PERCENTAGE_A},{VERSION_B_UUID}:{PERCENTAGE_B}"
```
*Example* (70% to Version A, 30% to Version B):
```bash
cxas deployments create \
  --app-name projects/my-project/locations/us-east1/apps/my-app \
  --deployment-id split-ab-test \
  --traffic-split "1e949785-ccf7-4896-9aa9-7a8182aac807:70,2bb22a0a-eebe-454b-8822-2336f6e52403:30"
```

#### Option 2: Promote an existing deployment with traffic split
If you want to update the traffic split on the live channel:
```bash
cxas deployments promote \
  --app-name {APP_ID} \
  --deployment-id {DEPLOYMENT_ID} \
  --traffic-split "{VERSION_A_UUID}:{PERCENTAGE_A},{VERSION_B_UUID}:{PERCENTAGE_B}" \
  --project-id {PROJECT_ID} \
  --location {LOCATION}
```

### Method B: Using SCRAPI Python SDK

You can programmatically configure traffic splitting by utilizing the [Deployments](https://googlecloudplatform.github.io/cxas-scrapi/stable/api/core/deployments/) class. 

To configure a split, you first create the deployment (pointing to a default version or draft) using [Deployments.create_deployment](https://googlecloudplatform.github.io/cxas-scrapi/stable/api/core/deployments/#cxas_scrapi.core.deployments.Deployments.create_deployment), and then apply the traffic split via [Deployments.update_deployment](https://googlecloudplatform.github.io/cxas-scrapi/stable/api/core/deployments/#cxas_scrapi.core.deployments.Deployments.update_deployment) by providing an `ExperimentConfig` object.

```python
from cxas_scrapi.core.deployments import Deployments
from google.cloud.ces_v1beta import types

# 1. Initialize the Deployments client
app_name = "projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}"
deployments_client = Deployments(app_name=app_name)

# 2. Define the traffic split using ExperimentConfig
experiment_config = types.ExperimentConfig(
    version_release=types.ExperimentConfig.VersionRelease(
        traffic_allocations=[
            types.ExperimentConfig.VersionRelease.TrafficAllocation(
                app_version=f"{app_name}/versions/{VERSION_A_UUID}",
                traffic_percentage=70,
            ),
            types.ExperimentConfig.VersionRelease.TrafficAllocation(
                app_version=f"{app_name}/versions/{VERSION_B_UUID}",
                traffic_percentage=30,
            ),
        ]
    )
)

# 3. Apply the split to an existing deployment
deployments_client.update_deployment(
    deployment_id="{DEPLOYMENT_ID}",
    experiment_config=experiment_config,
)
```

---

## 2. Verify Deployment Status

Verify that the traffic split has been successfully applied by listing the deployments.

### Method A: Using SCRAPI CLI (`cxas`)

```bash
cxas deployments list \
  --app-name projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}
```

The output will display the active deployments, their states (`RUNNING`), and the configured traffic allocations per version.

### Method B: Using SCRAPI Python SDK

Use the [Deployments.list_deployments](https://googlecloudplatform.github.io/cxas-scrapi/stable/api/core/deployments/#cxas_scrapi.core.deployments.Deployments.list_deployments) method to programmatically retrieve active deployments and inspect their traffic allocations.

```python
from cxas_scrapi.core.deployments import Deployments

deployments_client = Deployments(app_name="projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}")
deployments = deployments_client.list_deployments()

for d in deployments:
  print(f"Deployment: {d.name}")
  print(f"  State: {d.state}")
  if d.experiment_config and d.experiment_config.version_release:
    allocations = d.experiment_config.version_release.traffic_allocations
    for alloc in allocations:
      print(f"    Version: {alloc.app_version} -> {alloc.traffic_percentage}%")
```

---

## 3. Analyze Logs in BigQuery

Once your traffic split deployment is active and has processed sufficient live customer conversation volume, you can evaluate the performance of your versions. You can do this either **manually** by running a BigQuery SQL query, or **automatically** by delegating the task to AI agents.

### Method A: Automated Analysis via AI Agents [Recommended]

If you use AI coding assistants or custom orchestration agents equipped with tool-calling capabilities (such as BigQuery query tools), you can fully automate the log analysis workflow:

*   **How it works**:
    1.  The agent detects the active deployment allocations using the `cxas deployments list` CLI command.
    2.  It constructs the appropriate BigQuery SQL query with required evaluation time windows and filters.
    3.  It executes the query against your conversation logs (`conversational_agents_logs.v1beta_logs`).
    4.  It maps tool executions or response patterns back to each candidate app version.
    5.  It aggregates version metrics (such as tool reliability or task completion rate) and presents a comparative summary report.

*   **How to use it**:
    Provide a prompt to your AI agent specifying your app and evaluation window:
    > *"Which version of app {APP_ID} under project {PROJECT_ID} performed better between {START_TIME} and {END_TIME}?"*

---

### Method B: Manual Log Analysis (SQL)

If you prefer to run the analysis manually:

1.  **Construct the SQL Query**: Use the template below to extract the version ID, the tool called, and the tool's output from the interaction logs.
    ```sql
    SELECT
      app_version_id,
      tool_call.name AS tool_name,
      tool_call.output AS tool_result_message,
      COUNT(1) AS count
    FROM
      `{PROJECT_ID}.conversational_agents_logs.v1beta_logs`,
      UNNEST(json_payload.query_result.generations) AS generation,
      UNNEST(generation.tool_calls) AS tool_call
    WHERE
      json_payload.resource = 'projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}'
      AND timestamp >= TIMESTAMP('{START_TIME_YYYY-MM-DD HH:MM:SS}', '{TIMEZONE}')
      AND timestamp <= TIMESTAMP('{END_TIME_YYYY-MM-DD HH:MM:SS}', '{TIMEZONE}')
    GROUP BY
      app_version_id, tool_name, tool_result_message
    ORDER BY
      app_version_id, count DESC
    ```
2.  **Execute the Query**: Run the query using the official BigQuery CLI tool (`bq`):
    ```bash
    bq query --use_legacy_sql=false "[CONSTRUCTED_SQL]"
    ```
3.  **Evaluate Performance**:
    *   **Map Tools to Versions**: Identify which tool executions or response patterns correspond to each candidate app version.
    *   **Calculate Metrics**: Depending on your application KPIs, compare metrics across versions, such as:
        *   **Tool Execution Reliability**: Percentage of successful tool calls vs. tool execution errors or fallback responses.
        *   **Task Completion Rate**: Ratio of successfully resolved user turns across each allocated version.
    *   **Conclude**: Compare these metrics to decide whether to promote your candidate version to 100% of deployment traffic.
