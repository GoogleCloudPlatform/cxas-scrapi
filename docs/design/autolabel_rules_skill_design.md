# Design Document: `cxas-autolabel-rules` Skill & Insights Autolabeling Architecture

* **Author:** Gokulnath Babu & Jetski
* **Status:** Draft / Proposed
* **Repository Path:** `docs/design/autolabel_rules_skill_design.md`
* **Target Components:** 
  * Skill: `.agents/skills/cxas-autolabel-rules/`
  * Core SDK: `src/cxas_scrapi/core/insights.py`
  * CLI: `src/cxas_scrapi/cli/insights_cli.py`
* **Reference Documentation:** [Customer Experience Insights Autolabeling Rules](https://docs.cloud.google.com/gemini-enterprise-cx/insights/autolabel-correlation-rules)

---

## 1. Executive Summary

This document specifies a new agent skill (`cxas-autolabel-rules`) and corresponding Core SDK / CLI enhancements in `cxas-scrapi` dedicated exclusively to **Contact Center AI (CCAI) Insights Autolabeling Rules**.

Autolabeling rules enrich ingested conversations with custom key-value metadata evaluated via Common Expression Language (CEL). This design provides a streamlined declarative (GitOps / IaC) workflow allowing developers and AI agents to:
1. **Define & Author** CEL labeling rules from natural language specifications with automatic syntax and helper function validation.
2. **Maintain & Version** rules in a human-readable declarative YAML schema (`autolabel_rules.yaml`).
3. **Synchronize & Deploy** rules to GCP projects (`pull`, `push`, `diff`).

---

## 2. Background & Problem Statement

### 2.1 What are Autolabeling Rules?
CCAI Insights evaluates `AutoLabelingRule` resources at conversation ingestion/import time. Each rule specifies:
* `labelKey`: The key name of the label (e.g., `escalation_risk`, `agent_category`, `customer_tier`).
* `labelKeyType`: Custom (`LABEL_KEY_TYPE_CUSTOM`) or standard.
* `conditions`: An ordered list of `LabelingCondition` objects containing:
  * `condition`: A boolean CEL expression (e.g., `conversation.turn_count > 10`). Empty string `""` evaluates to `true` (acting as a fallback default).
  * `value`: A string CEL expression evaluated if `condition` is true (e.g., `'long_call'`).
* **Evaluation Semantics**: The first condition in the list that evaluates to `true` sets the label's value.

### 2.2 Challenges
* **CEL Complexity**: Writing CEL expressions against nested conversation runtime annotations, subagent transitions, and sentiment structures is error-prone.
* **Lack of Local Infrastructure-as-Code (IaC)**: Managing rules solely through cloud consoles or ad-hoc curl scripts leads to configuration drift across environments (dev -> staging -> prod).

---

## 3. Architecture & Component Breakdown

```
┌─────────────────────────────────────────────────────────────┐
│ 1. AI Agent Skill (.agents/skills/cxas-autolabel-rules)     │
│    - SKILL.md: Natural Language <-> CEL Prompt Engineering   │
│    - scripts/sync_rules.py: Push / Pull / Diff runner       │
└──────────────────────────────┬──────────────────────────────┘
                               │ uses
┌──────────────────────────────▼──────────────────────────────┐
│ 2. CLI Tooling (src/cxas_scrapi/cli/insights_cli.py)        │
│    - cxas insights autolabel pull / push / diff             │
│    - Static schema & CEL structure validation               │
└──────────────────────────────┬──────────────────────────────┘
                               │ calls
┌──────────────────────────────▼──────────────────────────────┐
│ 3. Core SDK (src/cxas_scrapi/core/insights.py)              │
│    - REST CRUD methods for AutoLabelingRule API             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Declarative YAML Schema Specification

Rules are stored in a version-controlled YAML file (e.g., `autolabel_rules.yaml`):

```yaml
version: "1.0"
project_id: "my-gcp-project"
location: "us-central1"

autolabeling_rules:
  - rule_id: "agent_category"
    display_name: "Agent Category Classifier"
    label_key: "agent_category"
    label_key_type: "LABEL_KEY_TYPE_CUSTOM"
    active: true
    conditions:
      - condition: "conversation.agent_id == 'vip_agent'"
        value: "'vip_support'"
      - condition: "get_last_subagent() == 'billing_agent'"
        value: "'billing'"
      - condition: ""  # Fallback default
        value: "'general'"

  - rule_id: "escalation_risk"
    display_name: "High Escalation Risk Indicator"
    label_key: "escalation_risk"
    label_key_type: "LABEL_KEY_TYPE_CUSTOM"
    active: true
    conditions:
      - condition: "has_same_caller_conversation_in_n_hours(-24) && conversation.turn_count > 10"
        value: "'repeat_caller_long_session'"
      - condition: "has(conversation.latest_analysis.analysis_result.call_analysis_metadata.sentiments) && conversation.latest_analysis.analysis_result.call_analysis_metadata.sentiments.exists(s, s.sentiment_data.score < -0.5)"
        value: "'negative_sentiment'"
      - condition: ""
        value: "'normal'"

  - rule_id: "extracted_order_id"
    display_name: "Extract Order ID from Session Parameters"
    label_key: "order_id"
    active: true
    conditions:
      - condition: "get_session_params('order_id') != ''"
        value: "get_session_params('order_id')"
      - condition: ""
        value: "'none'"
```

---

## 5. Core SDK Enhancements (`src/cxas_scrapi/core/insights.py`)

Extend `Insights` with native `AutoLabelingRule` operations verified against the Google Cloud CCAI Insights REST / RPC specifications:

| Method | HTTP Endpoint | Description |
| :--- | :--- | :--- |
| `list_autolabeling_rules()` | `GET /v1/{parent}/autoLabelingRules` | Lists all rules in the project/location. |
| `get_autolabeling_rule(name)` | `GET /v1/{name}` | Retrieves a rule by resource name or rule ID. |
| `create_autolabeling_rule(rule_dict, rule_id)` | `POST /v1/{parent}/autoLabelingRules?autoLabelingRuleId={rule_id}` | Creates a new autolabeling rule. |
| `update_autolabeling_rule(name, rule_dict, update_mask)` | `PATCH /v1/{name}?updateMask={fields}` | Updates an existing rule. |
| `delete_autolabeling_rule(name)` | `DELETE /v1/{name}` | Deletes a rule. |

*(Note: API endpoint paths and query parameter names like `autoLabelingRuleId` and `updateMask` have been verified against the official Contact Center Insights v1 Discovery and protobuf specifications).*

---

## 6. CLI Commands Specification (`cxas insights autolabel`)

The `cxas insights autolabel` subcommands provide terminal operations for GitOps:

1. **`cxas insights autolabel pull`**:
   * Fetches all cloud `autoLabelingRules` from the project and exports them into `autolabel_rules.yaml`.
   * Flag: `--out <path>` (defaults to `./autolabel_rules.yaml`).
2. **`cxas insights autolabel diff`**:
   * Compares the local YAML configuration against the active rules in GCP.
   * Outputs additions, modifications, and deletions with colorized diffs.
3. **`cxas insights autolabel push`**:
   * Deploys local rules to the project, updating existing rules and creating new ones.
   * Flags: `--dry-run`, `--force` (optionally delete remote rules missing from local YAML).

---

## 7. AI Skill Specification (`.agents/skills/cxas-autolabel-rules/`)

### 7.1 Directory Layout
```
.agents/skills/cxas-autolabel-rules/
├── SKILL.md
├── scripts/
│   └── sync_rules.py       # Standalone sync runner for pulling/pushing YAML
└── references/
    ├── cel_cookbook.md     # Ready-to-use CEL snippets for GECX/CES scenarios
    └── schema.json         # JSON schema for validating autolabel_rules.yaml
```

### 7.2 Key Capabilities in `SKILL.md`

#### A. Natural Language to CEL Formulation
The skill converts business objectives into valid CEL syntax using verified CCAI Insights helper functions:
* **Session Parameter Extraction**: `get_session_params('param_key')`
* **Sub-Agent Tracking**: `get_last_subagent(['escalation_handler'])`
* **Caller History**: `has_same_caller_conversation_in_n_hours(-24)`
* **Existing Label Lookup**: `get_label('existing_key')`
* **Turn & Duration Filters**: `conversation.turn_count > 12`, `conversation.duration > duration('120s')`

#### B. Validation & Safety Checks
* Enforces that every rule ends with an empty condition fallback (`condition: ""`).
* Checks CEL expression syntax against character limits (max 256 chars/value, 100 labels max).
* Validates condition ordering (most specific condition first, default fallback last).

---

## 8. Future Work & Extensions

1. **Rule Evaluation & Backtesting against Test Conversations**:
   * Calling `POST /v1/{parent}/autoLabelingRules:test` to evaluate rules against synthetic or sample conversation JSON objects. Deferred to future iterations to keep the initial developer experience lightweight and friction-free.
2. **CI/CD Automation**:
   * Running `cxas insights autolabel diff` in GitHub Actions / pre-commit to prevent configuration drift.

---

## 9. Verification & Testing Plan

1. **Unit Tests**:
   * Core SDK tests under `tests/cxas_scrapi/core/test_insights_autolabel.py`.
   * CLI tests under `tests/cxas_scrapi/cli/test_insights_cli.py`.
   * YAML serialization/deserialization validation.
2. **Skill Linter & Format**:
   * `uv run mdformat .agents/skills/cxas-autolabel-rules/SKILL.md`
   * `uv run ruff check --fix`
   * Brand compliance check via `scripts/check_brands.py`.
