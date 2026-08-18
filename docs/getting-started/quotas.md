---
title: Quotas and Rate Limits
description: Detailed Google Cloud quotas, capacity planning, and rate-limiting guidelines for CXAS SCRAPI development and automated evaluations.
---

# Quotas and Rate Limits Guide

CXAS SCRAPI orchestrates conversational agent lifecycle management, real-time sessions, and high-throughput automated evaluations (such as parallel simulations). Because simulations act as interactive users communicating with agents in real-time, they generate significant traffic across both **CX Agent Studio (CES)** and **Vertex AI**.

Default Google Cloud project quotas are often insufficient for active multi-developer teams or automated CI/CD pipelines. This guide details the specific quota metrics involved, provides capacity planning models, and explains how to prevent and troubleshoot quota exhaustion.

---

## 1. CX Agent Studio (ces.googleapis.com)

These quotas regulate server-side conversational agent interactions, design-time management operations, and real-time streaming audio channels on the GECX platform.

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

## 2. Vertex AI API (aiplatform.googleapis.com)

SCRAPI’s automated simulation engine runs client-side, using Gemini models as the **simulated user** (generating realistic customer responses) and the **eval judge** (verifying behavioral expectations against transcripts).

These quotas are enforced **per region** and **per base model** (e.g., `gemini-3.1-flash-lite`, `gemini-2.5-flash`):

### Inference Requests Per Minute (RPM)
*   **Metric:** `aiplatform.googleapis.com/generate_content_requests_per_minute_per_project_per_base_model`
*   **Description:** The number of generation requests sent per minute by the simulated user and expectation evaluation engine.

### Token Volume Per Minute (TPM)
*   **Metric:** `aiplatform.googleapis.com/tokens_per_minute_per_base_model`
*   **Description:** The combined volume of input (prompt + context) and output tokens processed per minute by the client-side Gemini models.

---

## 3. Supplementary Audio Services (Optional)

If your test suites perform client-side voice synthesis or transcription audits during audio simulations, ensure the following quotas are active:

### Text-to-Speech (texttospeech.googleapis.com)
*   **Metric:** `texttospeech.googleapis.com/synthesize_requests`
*   **Description:** Rate of TTS generation requests used to feed simulated customer speech into the bidirectional audio stream.

### Cloud Speech-to-Text (speech.googleapis.com)
*   **Metric:** `speech.googleapis.com/speech_recognition_requests`
*   **Description:** Used to transcribe raw agent audio streams back to text for multimodal assertions and audit logs.

---

## Capacity Planning & Quota Sizing

When multiple developers or CI/CD test jobs execute simulations simultaneously, token consumption spikes. Use the sizing benchmarks below to determine the appropriate quota requests for your project.

### Estimation Formulas

210673\text{Sim Requests/Min (RPM)} = \frac{\text{Sims} \times \text{Average Turns}}{\text{Run Duration (Minutes)}} \times \text{Concurrency Factor}210673

210673\text{Agent TPM} = \text{Sim Requests/Min} \times (\text{Instruction Tokens} + \text{History Tokens})210673

### Recommended Targets by Scale

| Scale / Scenario | Concurrent Workers | GECX `run_session_llm_token_consumption` | GECX `ConcurrentBidiRunSession` | Vertex AI `generate_content_requests...` | Vertex AI `tokens_per_minute...` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Solo Developer** (Interactive test + single eval runs) | 2–5 workers | **1,500,000** TPM | **15** streams | **150** RPM | **300,000** TPM |
| **Small Team (4–5 Devs)** (Concurrent testing & branch pushes) | 15–20 workers | **4,000,000** TPM | **50** streams | **500** RPM | **1,000,000** TPM |
| **Automated CI/CD** (Full regression suite on PRs) | 25+ workers | **8,000,000+** TPM | **100** streams | **1,000** RPM | **2,500,000** TPM |

---

## Troubleshooting & Error Handling

### 1. GECX Agent Token Quota Exhausted
*   **Error Signature:**
    ```text
    WARNING - Rate limited by GECX server. ces.googleapis.com/run_session_llm_token_consumption quota exhausted.
    ```
*   **Root Cause:** The agent's prompt size multiplied by the number of concurrent turns exceeded the per-minute platform ceiling.
*   **Remediation:**
    1. Request a quota increase for `run_session_llm_token_consumption` in the Google Cloud Console.
    2. Reduce worker parallelism in your CLI call:
       ```bash
       cxas evals report --run --include sims --sim-parallel 2
       ```
    3. Audit and optimize your agent's instructions (e.g., removing redundant examples or offloading static lookup tables into tool calls).

### 2. Vertex AI Rate Limit (HTTP 429)
*   **Error Signature:**
    ```text
    google.genai.errors.ClientError: 429 RESOURCE_EXHAUSTED. Quota/Rate Limit Exhausted.
    ```
*   **Root Cause:** The simulated user or LLM judge exceeded the Vertex AI RPM or TPM quota in the configured region (`us` / `us-central1`).
*   **Remediation:**
    1. Request a quota increase for the specific model (e.g., `gemini-3.1-flash-lite` or `gemini-2.5-flash`).
    2. In custom scripts, configure the SCRAPI client-side `RateLimiter`:
       ```python
       from cxas_scrapi.utils.rate_limiter import RateLimiter

       # Pace requests to 10 requests per second
       rate_limiter = RateLimiter(requests_per_second=10)
       ```

---

## How to Request Quota Increases

1. In the Google Cloud Console, navigate to **IAM & Admin > Quotas & System Limits**.
2. Set the **Service** filter to **CX Agent Studio API** or **Vertex AI API**.
3. Search for the target metric name (e.g., `run_session_llm_token_consumption` or `generate_content_requests`).
4. Select the checkbox for your deployment region and model (e.g., `gemini-3.1-flash-lite`), then click **Edit Quotas** at the top of the table.
5. Enter the target value and provide a clear business justification (e.g., *"Running automated conversational simulation testing suites for CXAS agent deployment"*).
6. Submit the request. Standard tier increases are generally auto-approved within a few minutes.
