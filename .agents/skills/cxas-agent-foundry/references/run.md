# Agent Foundry: Eval Runner

Run evals, triage failures, and generate reports for GECX conversational agents.

## Table of Contents

- [Before Starting](#before-starting)
- [Four Eval Types](#four-eval-types)
- [Run Everything](#run-everything)
- [Choosing Golden vs Sim](#choosing-golden-vs-sim)
- [Filtering](#filtering)
- [Audio Scoring](#audio-scoring)
- [Reporting](#reporting)

### Load additional references as needed:
- **Eval YAML formats and templates**: `references/eval-templates.md`
- **Generating and interpreting reports**: `references/generating-reports.md`
- **Eval scoring thresholds and schemas**: `references/api-schemas/evaluations.md`

## Run Steps

Initialize your `todo.md` checklist with:
1. Run Goldens, Sims & Tool Tests
2. Triage Results & Generate Report

## Before Starting

Check memory for project-specific context (app ID, variable handling rules, known platform bugs). If not available, ask the user for:
1. **App name** -- full resource path (`projects/{project}/locations/{location}/apps/{app_id}`)
2. **Eval file locations** -- where goldens and simulations YAML files live (in `<project>/evals/`, relative to the active project folder)
3. **Variable handling** -- which variables the agent derives automatically vs needs as overrides (check the agent's `before_agent_callback` if unsure)

**CRITICAL: Evaluation Channel Enforcement**
If the app's `gecx-config.json` specifies `"modality": "audio"`, you MUST NOT run evaluations in text mode. The runner scripts will now throw a fatal error if you attempt to bypass this. When running eval scripts, either omit the `--channel` flag to rely on the default config, or explicitly pass `--channel audio`. Never pass `--channel text` for an audio agent.

## Four Eval Types

- **Conversation-level:** goldens, simulations (test end-to-end agent behavior)
- **Component-level:** tool tests, callback tests (test individual pieces in isolation)

### 1. Platform Goldens -- deterministic flows
Turn-by-turn **ideal** conversations. The platform replays user inputs and scores agent responses via semantic similarity and tool call matching.

**Use for:** routing, escalation, auth checks -- any flow where the agent path is predictable and callbacks enforce the behavior.

**Design principle:** Goldens represent ideal PRD behavior, not current agent behavior. Capturing agent transcripts as goldens is circular.

**Run goldens at least 5 times** using the high-level reporting script (`run-and-report.py`) to average across runs.

### 2. Local Simulations -- open-ended flows
Uses SCRAPI's Sessions API with Gemini as the sim user to test flows where the conversation varies each run. Runs locally (not on the platform), supports parallel execution (~1 min for the full suite).

**Use for:** troubleshooting cadence, multi-step failures, knowledge base queries, any flow where tool responses determine the agent's path.

**Default filter:** `run-and-report.py` runs sims with `--priority P0` by default. To run all priorities, pass `--priority P0,P1,P2` to the script.

### 3. Tool Tests -- isolated tool validation (runs locally)
Tests individual tools with specific inputs and validates outputs. These run against the deployed app via SCRAPI -- not pushed to the platform as eval objects.

```python
from cxas_scrapi.evals.tool_evals import ToolEvals

tool_evals = ToolEvals(app_name=app_name)
test_cases = tool_evals.load_tool_tests_from_dir("<project>/evals/tool_tests")
results_df = tool_evals.run_tool_tests(test_cases)
```

### 4. Callback Tests -- isolated callback validation (runs locally)
Tests agent callbacks using pytest against local callback code. These never touch the platform -- they import the callback Python directly and test with mock objects.

```python
from cxas_scrapi.evals.callback_evals import CallbackEvals

cb = CallbackEvals()
results_df = cb.test_all_callbacks_in_app_dir(app_dir="<project>/evals/callback_tests")
```

## Run Everything

```bash
# Single command: run evals + triage + generate iteration report
python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "what changed" --auto-revert
```

The script reads the channel from `gecx-config.json` automatically. It runs all eval types, triages failures, and generates an iteration report -- no need to run triage separately.

To re-triage with different options (e.g., averaging across multiple runs), use the triage script directly:

```bash
python .agents/skills/cxas-agent-foundry/scripts/triage-results.py           # latest run
python .agents/skills/cxas-agent-foundry/scripts/triage-results.py --last 3  # average across runs
```

This categorizes each failure (timeout, tool missing, text mismatch, scoring inconsistency) so you know what to fix vs what's a platform issue.

## Choosing Golden vs Sim

See `interview-guide.md` -> "Golden vs Scenario Decision" for the decision table. Key rule: if a golden keeps failing because responses inherently vary (KB-dependent), convert to a sim.

## Filtering

All commands support `--priority P0` and `--tag <tag>`.

## Audio Scoring

**Goldens:** Use `evaluation_status` directly (1=PASS, 2=FAIL). The `--audio` flag does NOT apply to goldens.

**Sims:** The sim runner handles audio scoring automatically. Tool expectations (`expect_tools`) cause silent failures in audio mode -- use `expect_criteria` (LLM judges) instead.

## Reporting

`run-and-report.py` automatically generates an iteration report after each run. Reports are saved to `<project>/eval-reports/iterations/`.

**For a combined cross-iteration trend report:**
```bash
python .agents/skills/cxas-agent-foundry/scripts/generate-combined-report.py
```

This produces an HTML report in `<project>/eval-reports/combined/` showing pass rate trends, per-eval stability, and regression detection across all iterations.

For guidance on interpreting reports (key metrics, triage categories, when to adjust vs fix), see `references/generating-reports.md`.
