# CXAS SCRAPI (GECX / CES) & Google ADK Integration Guide

This reference details how Vector Steering integrates natively with `cxas-scrapi` local agent workspaces (`app.json`, `environment.json`, `toolsets/`, `tools/`, `agents/`) as well as standalone Google ADK Python agents.

---

## 1. Native `cxas-scrapi` OpenAPI Toolset & `environment.json` Contract

In CX Agent Studio (CES), Python tools execute inside a sandboxed runtime without raw internet sockets; outbound HTTPS calls route through declared **OpenAPI Toolsets** (`toolsets/<toolset_name>/`).

1. **Tool Invocation Namespace**:
   CES exposes each OpenAPI operation inside Python tools as:
   ```python
   tools.<toolset_displayName>_<operationId>(payload)
   ```
   When `toolset_name: "vector_intent_router"` and `operation_id: "classify_intent"`, `scaffold_agent_tools.py` wires `tools.vector_intent_router_classify_intent(payload)` automatically.
2. **Environment-Specific URL Resolution (`$env_var` + `environment.json`)**:
   Passing `--sync-env-json` to `scaffold_agent_tools.py` sets `url: $env_var` inside `open_api_schema.yaml` and writes the target Cloud Function URL into `<app_dir>/environment.json` under `toolsets.vector_intent_router.openApiToolset.url`. When you run `uv run cxas push <app_dir> --to <app> --env-file <app_dir>/environment.json`, SCRAPI resolves `$env_var` cleanly per environment (dev, UAT, prod) without mutating committed schema files.

---

## 2. Structured Session State (`_vector_steering_state`) & `cxas lint` Hygiene

To prevent session variable sprawl and satisfy `cxas lint` (`V100` / `V101` / `V102`), Vector Steering registers:
1. `Intent` (`STRING`): Canonical resolved intent consumed by child-agent `transferRules` (`Intent == 'cancel_order'`).
2. `tenant_id` (`STRING`): Optional active brand, plan, or tier identifier.
3. `_vector_steering_state` (`OBJECT`): Encapsulates multi-turn disambiguation state (`mode`, `last_query`, `previous_top_intents`, `no_match_count`).

Pass `--merge-app-vars` to `scaffold_agent_tools.py` to merge missing variable declarations into `<app_dir>/app.json`, and `--wire-steering-agent <AgentName>` to register `intent_classifier`, `update_intent`, and `vector_intent_router` on your steering sub-agent JSON.

---

## 3. Two-Turn Disambiguation & Zero-Hop Fast Path

1. **Turn 1 (Ambiguous Utterance)**:
   - Caller: *"I need to check my card balance."*
   - `intent_classifier` queries the Cloud Function -> receives `check_credit_balance` (`0.78`) and `check_gift_card_balance` (`0.76`).
   - Because `0.78 - 0.76 = 0.02 < 0.08` (`disambiguation_delta`), `processed_action` is `"DISAMBIGUATION_REQUIRED"`, enriching each candidate with `intent_description` and saving `previous_top_intents`.
   - Gemini either auto-resolves from prior conversation history or asks one concise clarification question in natural customer language.
2. **Turn 2 (Zero-Hop Resolution)**:
   - Caller: *"My gift card."*
   - **Zero-Hop Fast Path**: The steering prompt maps the caller's choice directly to `check_gift_card_balance` from Turn 1's candidates and invokes `update_intent(Intent="check_gift_card_balance")` with `0ms` extra vector DB latency.
   - **Re-Query Safety Net**: If the caller switches topics completely on Turn 2 and `intent_classifier` is called again while `_vector_steering_state.mode == "single"`, it intersects against `previous_top_intents` above `single_mode_min_confidence` (`0.60`) or promotes a high-confidence topic switch automatically.

---

## 4. Optional Single-Pass `before_model_callback` Optimization

In ultra-latency-sensitive Turn-1 voice deployments, you can eliminate the tool-call LLM round-trip by invoking the OpenAPI/vector lookup inside a `before_model_callback` when `Intent` is empty, injecting only the Top-3 contrastive candidates into a dynamic instruction turn variable so Gemini resolves or clarifies in a **single model pass**.

---

## 5. Calibrating Thresholds with `cxas trace search` & `cxas evals report`

Use `uv run cxas trace search` and `uv run cxas evals report` to inspect live or simulated turns and tune `vector_steering/vector_steering.yaml`:

| Observed Trace Symptom | Adjustment in `vector_steering/vector_steering.yaml` |
| :--- | :--- |
| Casual/out-of-scope remarks trigger specialist flows | Raise `similarity_threshold` from `0.75` -> `0.78–0.80` |
| Paraphrased valid requests land in `NO_MATCH` | Add 10–15 7-axis examples to `training_phrases.csv` or lower `similarity_threshold` to `0.70–0.72` |
| Agent guesses wrong between two sibling intents | Increase `disambiguation_delta` from `0.08` -> `0.10–0.12` |
| Agent asks unnecessary clarification questions | Decrease `disambiguation_delta` from `0.08` -> `0.05` and sharpen `intent_description` exclusions |

---

## 6. Standalone Google ADK Target (`--target adk`)

When building a pure-Python **Google ADK** agent outside CES:
```bash
uv run python .agents/skills/cxas-vector-steering/scripts/scaffold_agent_tools.py \
  --config vector_steering/vector_steering.yaml \
  --app-dir adk_agent/vector_steering_bundle \
  --target adk
```
This compiles `vector_steering_adk_tools.py` with native OIDC `google.oauth2.id_token.fetch_id_token` HTTP transport and `tool_context.state` bindings.
