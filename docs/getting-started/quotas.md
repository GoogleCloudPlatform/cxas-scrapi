---
title: Quotas and Rate Limits
description: Detailed Google Cloud quotas, capacity planning, usage tiers, and rate-limiting guidelines for CXAS SCRAPI development and automated evaluations.
---

# Quotas and Rate Limits Guide

CXAS SCRAPI orchestrates conversational agent lifecycle management, real-time sessions, and high-throughput automated evaluations (such as parallel simulations). Because simulations act as interactive users communicating with agents in real-time, they generate significant traffic across both **CX Agent Studio (CES)** and **Vertex AI / Gemini Enterprise Agent Platform**.

Understanding how capacity is managed across these services is essential for planning multi-developer workflows and automated CI/CD test suites.

---

## 1. CX Agent Studio (ces.googleapis.com)

These are traditional, fixed Google Cloud project quotas that regulate server-side conversational agent interactions, design-time management operations, and real-time streaming audio channels on the GECX platform.

For full platform limits, refer to the [Google Cloud Conversational Agents Quotas Documentation](https://docs.cloud.google.com/customer-engagement-ai/conversational-agents/quotas).

### Agent LLM Token Consumption (Runtime)
*   **Metric:** `ces.googleapis.com/run_session_llm_token_consumption`
*   **Description:** Measures the total volume of LLM tokens processed per minute by your agent during active conversational sessions.
*   **How it is consumed:** On every session turn, the agent processes its full system instruction set, conversational history, variable state, tool declarations, and model output.
*   **Modality:** Enforced across **both** standard text sessions (`RunSession`) and bidirectional streaming audio sessions (`BidiRunSession`).
*   **Sizing sensitivity:** Highly sensitive to instruction length. An agent with a 6,000-word instruction set will consume ~8,500–10,000 tokens per single turn.

### Bidirectional Streaming Concurrency (Audio/Voice)
*   **Metric:** `ces.googleapis.com/ConcurrentBidiRunSession` (or `ConcurrentBidiRunSession`)
*   **Description:** The maximum number of simultaneous, persistent WebSocket audio streams permitted at any one moment.
*   **When required:** Only applicable when developing or evaluating voice agents using `--modality audio` or `--channel audio`.

### Administrative & Design-Time Limits
*   **Metric:** `ces.googleapis.com/read_requests` and `ces.googleapis.com/write_requests`
*   **Description:** Rate limits for managing agent metadata (e.g., `cxas pull`, `cxas push`, `cxas lint`, tool creation, guardrail updates).
*   **Default:** Typically 600 requests per minute (RPM), which is sufficient for standard development unless high-frequency automated scripts poll the API.

---

## 2. Vertex AI / Gemini Enterprise Agent Platform (aiplatform.googleapis.com)

SCRAPI’s automated simulation engine runs client-side, using Gemini models as the **simulated user** (generating realistic customer responses) and the **eval judge** (verifying behavioral expectations against transcripts).

Unlike traditional static GCP quotas, generative Gemini models use **Standard PayGo Usage Tiers** governed by your organization’s rolling 30-day spend. For full details, see the [Gemini Standard PayGo Documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/standard-paygo).

### Standard PayGo Usage Tiers (Tokens Per Minute)
Throughput is measured in **Tokens Per Minute (TPM)** at the organization level with automatic promotion based on spend:

| Model Family | Tier | 30-Day Org Spend | Baseline TPM (Org-Level) |
| :--- | :--- | :--- | :--- |
| **Gemini Flash & Flash-Lite**<br>(`gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-flash`) | Tier 1 | \$10 – \$250 | **2,000,000** TPM |
| | Tier 2 | \$250 – \$2,000 | **4,000,000** TPM |
| | Tier 3 | > \$2,000 | **10,000,000** TPM |
| **Gemini Pro**<br>(`gemini-2.5-pro`, `gemini-3.1-pro-preview`) | Tier 1 | \$10 – \$250 | **500,000** TPM |
| | Tier 2 | \$250 – \$2,000 | **1,000,000** TPM |
| | Tier 3 | > \$2,000 | **2,000,000** TPM |

*   **No Separate RPM Limits**: Standard PayGo does not enforce a separate requests-per-minute (RPM) ceiling per tier. Limits are based purely on token throughput.
*   **Dynamic Bursting**: Traffic is allowed to burst beyond the baseline throughput limit on a best-effort basis.
*   **Global Endpoint Routing**: By default, SCRAPI connects to the `global` location (`location = "global"`), dynamically routing requests to regions with the highest available capacity.
*   **Guaranteed Throughput**: Workloads requiring dedicated, unshared throughput without latency variation should explore [Provisioned Throughput](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/provisioned-throughput) or [Priority PayGo](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/priority-paygo).

---

## 3. Configuring Models in SCRAPI

You can configure which Gemini model to use for the simulated customer and the evaluation judge using CLI flags:

```bash
cxas evals report --run --include sims \
  --sim-user-model gemini-3.7-flash \
  --eval-model gemini-3.7-flash \
  --output-dir eval-reports/
```

---

## 4. Capacity Planning & Sizing Calculator

When multiple developers or CI/CD test jobs execute simulations simultaneously, token consumption spikes. Use the sizing benchmarks below to determine the appropriate quota requests for your GECX project.

### Estimation Formulas

$$\text{Sim Requests/Min (RPM)} = \frac{\text{Sims} \times \text{Average Turns}}{\text{Run Duration (Minutes)}} \times \text{Concurrency Factor}$$

$$\text{Agent TPM} = \text{Sim Requests/Min} \times (\text{Instruction Tokens} + \text{History Tokens})$$

### Recommended GECX Targets by Scale

| Scale / Scenario | Concurrent Workers | GECX `run_session_llm_token_consumption` | GECX `ConcurrentBidiRunSession` | Minimum Gemini Spend Tier |
| :--- | :--- | :--- | :--- | :--- |
| **Solo Developer** (Interactive test + single eval runs) | 2–5 workers | **1,500,000** TPM | **15** streams | Tier 1 (2M TPM) |
| **Small Team (4–5 Devs)** (Concurrent testing & branch pushes) | 15–20 workers | **4,000,000** TPM | **50** streams | Tier 2 (4M TPM) |
| **Automated CI/CD** (Full regression suite on PRs) | 25+ workers | **8,000,000+** TPM | **100** streams | Tier 3 (10M TPM) |

---

## 5. Troubleshooting & Error Handling

### 1. GECX Agent Token Quota Exhausted (Fixed Quota)
*   **Error Signature:**
    ```text
    WARNING - Rate limited by GECX server. ces.googleapis.com/run_session_llm_token_consumption quota exhausted.
    ```
*   **Root Cause:** The agent's prompt size multiplied by the number of concurrent turns exceeded the project's per-minute platform ceiling.
*   **Remediation:**
    1. Request a quota increase for `run_session_llm_token_consumption` in the [Google Cloud Quotas Console](https://console.cloud.google.com/iam-admin/quotas).
    2. Reduce worker parallelism in your CLI call:
       ```bash
       cxas evals report --run --include sims --sim-parallel 2
       ```
    3. Optimize your agent's instruction set to reduce per-turn token overhead.

### 2. Vertex AI Resource Exhaustion (HTTP 429)
*   **Error Signature:**
    ```text
    google.genai.errors.ClientError: 429 RESOURCE_EXHAUSTED. Quota/Rate Limit Exhausted.
    ```
*   **Root Cause:** A `429` error on Gemini **does not indicate a fixed project quota breach**. It indicates temporary resource contention on shared multi-tenant capacity or sharp, second-level traffic bursts. See [Understanding Error Code 429](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deploy/error-code-429) and [Reducing 429 Errors on Vertex AI](https://cloud.google.com/blog/products/ai-machine-learning/reduce-429-errors-on-vertex-ai).
*   **Remediation:**
    1. **Traffic Smoothing**: Avoid instantaneous second-level spikes by using SCRAPI's client-side `RateLimiter`:
       ```python
       from cxas_scrapi.utils.rate_limiter import RateLimiter

       # Pace requests smoothly
       rate_limiter = RateLimiter(requests_per_second=10)
       ```
    2. **Exponential Backoff**: SCRAPI automatically implements exponential retries with jitter for transient `429` errors.
    3. **Global Location**: Ensure client configuration references `location = "global"` to leverage multi-region dynamic load balancing.

---

## 6. How to Request GECX Quota Increases

To increase your GECX platform quotas (such as `run_session_llm_token_consumption`):

1. Open the [Google Cloud Console Quotas Page](https://console.cloud.google.com/iam-admin/quotas).
2. Set the **Service** filter to **CX Agent Studio API** (`ces.googleapis.com`).
3. Search for the target metric name (e.g., `run_session_llm_token_consumption`).
4. Select the checkbox for your deployment region and click **Edit Quotas**.
5. Enter the target value and provide a clear business justification (e.g., *"Running automated conversational simulation testing suites for CXAS agent deployment"*).
6. Submit the request. For more details on the quota request lifecycle, see [Viewing and Managing Quotas](https://cloud.google.com/docs/quotas/view-manage).
