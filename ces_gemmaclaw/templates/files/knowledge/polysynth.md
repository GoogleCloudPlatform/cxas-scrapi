# Knowledge: Polysynth Agents, Evaluation, and Hill Climbing

Learn how to model session parameters, handle telephone deflections, and optimize pass rates using automated hill climbing testing loops.

## 1. Polysynth Session variables vs State Divergence
*   In the live CCAS / Dialogflow CX integration runtime, `variables` (session parameters) and `state` (webhook persistent state) can diverge.
*   For crucial parameters (e.g. `store_number`), **always set them explicitly in both `variables` and `state`** inside callbacks to ensure propagation.
*   When building telephony deflection payloads (e.g., in `after_model_callback`), always use a fallback chain:
    `store_number = state.get("store_number") or variables.get("store_number") or ""`
    This prevents empty parameters from creating malformed SIP deflections.

## 2. Two-Tier Evaluation Strategy & Hill Climbing
To optimize agent pass rates against non-deterministic LLM behaviors, use the two-tier evaluation strategy:

1.  **Golden Conversations (Unit Tests):** Asserts every turn of the conversation.
2.  **Scenario Testing:** Simulates users and judges against unseen data scenarios.

### 🔄 The Automated Heartbeat / Hill Climbing Loop:
Repeat this continuous cycle to achieve a green, stable test suite:
1.  **Deploy changes:** Pull/import latest configuration changes into the Polysynth sandbox.
2.  **Convert Evals:** Convert your JSON scenarios to temporary YAML files.
3.  **Push Evals:** Push both Golden Conversations and Scenarios to the target app:
    `cxas push-eval --app_name projects/ces-deployment-dev/locations/us/apps/[APP_ID] --file /tmp/scenarios.yaml`
4.  **Run Evals (Audio Modality):** Trigger the test suite E2E in audio modality to evaluate Semantic Similarity scoring:
    `cxas run --app_name projects/ces-deployment-dev/locations/us/apps/[APP_ID] --modality audio`
5.  **Audit & Optimise:** Analyze failure logs, tweak prompts inside files, compile locally, and iterate until you get at least **10 consecutive rounds of consistent passes** before merging!
