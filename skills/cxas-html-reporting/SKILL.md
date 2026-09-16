---
name: cxas-html-reporting
description: Generates a standalone, static HTML evaluation report for CXAS test case executions, or a rich structured machine-readable JSON telemetry dump (`metrics.json`). Parses 100% of Google Cloud CES `v1beta` `evaluation.proto` schema fields, suppresses tool-order false alarms, generates deep links to internal CES console, categorizes failures into 5 execution dimensions, and runs an automated live project-wide REST prompt/topology linter audit.
---

# CXAS HTML & Agentic Reporting Generator (`cxas-html-reporting`)

This skill generates a self-contained, interactive single-file HTML report for Customer Engagement Suite (`CES` / `CXAS`) evaluation test executions, or an exhaustive structured JSON payload (`metrics.json`) for automated root-cause analysis and self-healing by coding subagents.

It communicates directly with the Cloud evaluation REST endpoints to fetch execution summaries, triggers LRO trace exports (`results:export`), unpacks raw ZIP telemetry (`conversations/*.json` and `evaluationResults/*.json`), and audits live agent prompt instructions and parent-child routing hierarchy against production Agent Studio standards.

---

## Diagnostic Taxonomy (5 Execution Categories + 1 Project Audit Dimension)

When analyzing evaluation test failures, findings are structured into six distinct dimensions:

### Execution Failure Dimensions
1. **Tool Calls** (`tab-tool-calls`):
   * API schema mismatch or missing mandatory parameter names.
   * Fractional parameter correctness scores (`toolInvocationResult.parameter_correctness_score < 1.0`).
   * Tool sequence ordering deviations (`tool_ordered_invocation_score < 1.0`) **only flagged when overall tool invocation outcome equals `FAIL`** (suppressing noise on passing sequences).
2. **State & Variables** (`tab-variables`):
   * Dynamic template variable string binding failures (`{variable_name}`).
   * Missing session/context variable initializations or end-user identity token binding gaps.
3. **Generative & Phrasing** (`tab-semantic`):
   * Factually inaccurate or unscripted output (`hallucinationResult` score `0`).
   * Semantic Similarity numeric drops (`semanticSimilarityResult` on `0`–`4` consistency scale: *Not Consistent*, *Partially Consistent*, *Mostly Consistent*, *Fully Consistent*).
   * User Goal Satisfaction penalties (`userGoalSatisfactionResult` score `0`).
   * Custom evaluator rubric score failures (`rubricOutcomes` score `< 0.7`).
4. **Agent Handovers** (`tab-handovers`):
   * Subagent handoff/transfer expectation mismatches (`observedAgentTransfer` with outcome `FAIL`).
   * Unauthorized horizontal or diagonal lateral transfers between specialist subagents.
   * Unexpected escalations to `main` (`root-agent`) or missed expected handoffs.
5. **System & Infrastructure** (`tab-system`):
   * Quota exhaustion (`QUOTA_EXHAUSTED`).
   * Platform runtime engine crashes (`RUNTIME_FAILURE`).
   * Simulator or mock runner failure (`USER_SIMULATION_FAILURE`).
   * Trace or transcript retrieval timeouts (`CONVERSATION_RETRIEVAL_FAILURE`).

### Global Architecture Dimension
6. **Project Architecture & Prompt Linter Audit** (`tab-linter` / `projectLinterAudit`):
   * Queries `GET /v1beta/{app_id}/agents?pageSize=100` over REST API to audit all live application agents.
   * Evaluates 12 production SCRAPI rules:
     - Inactive backticked pills (e.g. `` `{@TOOL: name}` `` or `` `{@AGENT: name}` ``).
     - Attached JSON tools missing active reference pills (`{@TOOL: name}`) in instruction prompt text.
     - Single-Parent Directed Tree Topology Rule (non-root subagents cannot declare `childAgents` or trigger lateral sibling handoffs).
     - Raw inequality unescaped markup (e.g., `<15` in prompt text).
     - Universal silent handover voice guardrails.

---

## Command-Line Arguments (`generate_report.py`)

| Flag | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--app-id` | **Yes** | — | Full resource path: `projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}` |
| `--output` | **Yes** | — | Target filepath for `.html` or `.json` report file |
| `--format` | No | `html` | Options: `html` (interactive human dashboard) or `json` (machine-readable agent telemetry) |
| `--env` | No | `prod` | Environment selector: `dev` (`ces-console-dev.corp.google.com`) or `prod` (`ces-console.corp.google.com`) |
| `--app-dir` | No | `""` | Optional path to local source directory on disk to include static `scrapi_cli.py check-lint` bundle check |

---

## When to Use This Skill

Invoke this skill whenever:
- The user requests an **HTML or summary evaluation report** after running CXAS golden test cases.
- You need a structured breakdown of why specific evaluation runs failed.
- You need deep links to inspect failed turns directly in the internal CXAS Web Console.
- You want to run a global architectural health check on an entire live CXAS application.

---

## Step-by-Step Execution Workflow

### Step 1: Run the Generator Script
```bash
# Generate interactive human-facing HTML report:
python3 generate_report.py \
  --app-id="projects/connectors-incubation-test-1/locations/us-east1/apps/72d4c6b5-75d2-44b8-b369-84d2223361dd" \
  --output="/tmp/cxas_report.html" \
  --format="html" \
  --env="dev"

# Generate structured machine-readable JSON payload for automated remediation:
python3 generate_report.py \
  --app-id="projects/connectors-incubation-test-1/locations/us-east1/apps/72d4c6b5-75d2-44b8-b369-84d2223361dd" \
  --output="/tmp/metrics.json" \
  --format="json" \
  --env="dev"
```

### Step 2: Machine-Readable JSON Schema (`--format=json`)
When invoked with `--format=json`, the output schema provides full telemetry:

```json
{
  "total": 10,
  "passed": 1,
  "failed": 9,
  "schemaVersion": "ces.v1beta.evaluation.proto",
  "projectLinterAudit": {
    "totalIssues": 108,
    "issues": [
      "Agent [billing-flow]: Illegal lateral transfer pill '{@AGENT: closure-flow}'. Specialist subagents must return exclusively to main."
    ]
  },
  "results": [
    {
      "displayName": "Golden - Escalation Summary Enrichment",
      "evaluationStatus": "FAIL",
      "toolErrors": [
        "[Routing/Transfer Failure (Turn 2)]: Agent transfer to 'main' failed expectation (Outcome: FAIL)."
      ],
      "categorizedIssues": {
        "Tool Calls": [],
        "State & Variables": [],
        "Generative & Phrasing": [],
        "Agent Handovers": [
          "[Routing/Transfer Failure (Turn 2)]: Agent transfer to 'main' failed expectation (Outcome: FAIL)."
        ],
        "System & Infrastructure": []
      },
      "detailedTelemetry": {
        "semanticSimilarity": [
          {
            "turn": 1,
            "score": 4,
            "label": "Fully Consistent",
            "outcome": "PASS",
            "explanation": "Both responses identify total bill increase reason..."
          }
        ],
        "toolInvocation": [
          {
            "turn": 1,
            "parameterCorrectnessScore": 1.0,
            "explanation": "No parameters found."
          }
        ],
        "agentTransfers": [
          {
            "turn": 2,
            "transfer": {
              "displayName": "main",
              "targetAgent": "projects/.../agents/root-agent"
            },
            "outcome": "FAIL"
          }
        ]
      }
    }
  ]
}
```

### Step 3: Embed & Present
1. Read `/tmp/metrics.json` via `view_file` to understand diagnostic failure root-causes.
2. If rendering HTML in chat, embed `/tmp/cxas_report.html` using an `<agent-embed>` frame.
3. Formulate high-impact prescriptive recommendations telling the developer how to modify `agents/<agent_name>/instruction.txt` or `tools/` definitions to resolve all failing test cases.
