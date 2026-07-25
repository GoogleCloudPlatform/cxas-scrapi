---
name: cxas-interactive-eval-reporter
description: Universal, domain-agnostic interactive evaluation reporter for GECX/CXAS apps. Automatically runs evals, uses Gemini LLM to dynamically cluster failure categories, dynamically scans all session parameters for filtering, and compiles rich HTML/MHTML dashboard reports.
---

# CXAS Interactive Eval Reporter (Universal & Dynamic)

This skill provides an enterprise-ready, domain-agnostic evaluation reporter that transforms raw SCRAPI/GECX simulation results into rich, interactive HTML/MHTML dashboards for **any** conversational agent across **any** domain (telecom, finance, retail, healthcare, etc.).

## Key Features

1. **End-to-End Execution**:
   - Optionally triggers simulation evaluations via `cxas run --app-name <APP_NAME>` before generating reports, or ingests existing `sim_results.json` files.

2. **LLM AI Failure Categorization**:
   - Uses **Gemini LLM** to dynamically analyze failed test expectation text and transcript justifications.
   - Automatically discovers 3–8 domain-agnostic diagnostic categories (e.g. *Tool Call Missing*, *Safety/Guardrail Violation*, *Out-of-Scope Fallback*, *Sim Infrastructure Timeout*, *Instruction Mismatch*) without domain hardcoding.

3. **Dynamic Session Parameter Filters**:
   - Automatically scans test entries for **all** session parameters present in `session_parameters` (`account_type`, `user_tier`, `device_os`, `region`, etc.).
   - Dynamically builds dedicated filter controls for every parameter key found and performs multi-parameter cross-filtering.

4. **Scorecard Metrics & Trace Viewers**:
   - Displays **Overall Pass Rate** and **Adjusted Pass Rate** (excluding `Sim Related` infra glitches).
   - Expandable turn-by-turn trace cards displaying user dialogs, agent responses, tool calls, and expectation callout boxes.

## Usage

### Mode 1: Auto-Run Evals + Generate Interactive Report (All-in-One)
If you don't have a `sim_results.json` yet, pass `--run` to automatically trigger the simulation evaluation suite first, then build the report:

```bash
uv run python .agents/skills/cxas-interactive-eval-reporter/scripts/generate_report.py \
  --run \
  --output path/to/interactive_report.html \
  --title "My Agent Simulation Report"
```

### Mode 2: Generate Report from Existing Evals JSON
If you already have a `sim_results.json` file from a prior test run:

```bash
uv run python .agents/skills/cxas-interactive-eval-reporter/scripts/generate_report.py \
  --input path/to/sim_results.json \
  --output path/to/interactive_report.html \
  --title "My Agent Simulation Report"
```

### Command Line Options

- `--run`, `--run-evals`: (Optional) Automatically execute simulation evaluations first before generating the report.
- `--input`, `-i`: (Optional) Path to existing `sim_results.json` file.
- `--app-name`: (Optional) CXAS App Resource Name/ID.
- `--output`, `-o`: (Optional) Destination path for output HTML file. Defaults to `interactive_simulation_report.html`.
- `--title`: (Optional) Custom header title for the dashboard.
