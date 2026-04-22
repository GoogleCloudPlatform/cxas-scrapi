---
name: cxas_sim_eval
description: >-
  Converts CXAS golden evaluations to SCRAPI SimulationEvals test cases.
  Use when generating high-level, goal-oriented test cases from turn-by-turn evaluation JSONs,
  and when enriching test expectations with inferred tool calls.
---

# CXAS Evaluation to Simulation Converter

This skill helps convert turn-by-turn CXAS golden evaluations into high-level, goal-oriented test cases for the SCRAPI `SimulationEvals` framework. It analyzes the agent's tools to enrich expectations with specific tool calls.

---

## Steps

### 1. Check Environment
Ensure `cxas_scrapi` is installed as a python package. You can check this by running:
```bash
python -c "import cxas_scrapi"
```

Ensure `gcloud` is authenticated properly:
```bash
gcloud auth list
```
If needed, login with:
```bash
gcloud auth login
```

### 2. Get App Name and Output Directory
> [!IMPORTANT]
> You MUST ask the user for the full resource name of the app/agent (e.g., `projects/.../locations/.../apps/...`) and the base output directory before proceeding with any execution steps.

Ask the user for these values.

### 3. Fetch Evaluations
Fetch the list of evaluations using the CES API. Save each evaluation as a JSON file named after its display name under `[output_dir]/golden_evals/`.

### 4. Fetch Tool Schemas
Fetch the full schemas for all tools available in the app and save them under `[output_dir]/tools/`.

### 5. Fetch Agent Tools Configuration
Fetch the list of tools and toolsets used by the agent and save the configuration (e.g., to `[output_dir]/agent_tools.json`).

### 6. Convert Evaluations
Run the conversion script (`convert_eval.py`) to process the fetched evaluations and save the converted test cases under `[output_dir]/sim_evals/`.

## Automation Scripts

Three scripts are available to automate the process:

### 1. Fetch Evaluations and Agent Config
`scripts/fetch_app_data.py`

Fetches evaluations and the list of tools used by the agent from the CES API.

Usage:
```bash
python .agents/skills/cxas_sim_eval/scripts/fetch_app_data.py \
  --app-name "projects/.../locations/.../apps/..." \
  --output-dir /path/to/output_directory
```

### 2. Fetch Tool Schemas
`scripts/fetch_tool_schemas.py`

Fetches the full schemas for all tools available in the app.

Usage:
```bash
python .agents/skills/cxas_sim_eval/scripts/fetch_tool_schemas.py \
  --app-name "projects/.../locations/.../apps/..." \
  --output-dir /path/to/output_directory
```

### 3. Convert Evaluations
`scripts/convert_eval.py`

Converts the fetched evaluations to simulation test cases, using the fetched tool schemas to infer expectations.

Usage:
```bash
python .agents/skills/cxas_sim_eval/scripts/convert_eval.py \
  --output-dir /path/to/output_directory \
  --parallelism 5
```

### 4. Run Evaluations
`scripts/run_evals.py`

Runs the simulation evaluations, logs raw results, and generates a combined HTML report.

**Cognitive Diagnostics Analysis**:
If the agent has the `intercept_and_score_reasoning` tool enabled, this script will automatically extract and analyze the agent's internal monologue for failed evaluations. It detects issues like overthinking, hesitation, and backtracking. Furthermore, it correlates these diagnostics with the agent's instructions to generate **actionable suggestions** for improvement directly in the HTML report.

Usage:
```bash
python .agents/skills/cxas_sim_eval/scripts/run_evals.py \
  --app-name "projects/.../locations/.../apps/..." \
  --output-dir /path/to/output_directory \
  --parallelism 5 \
  --start-index 0 \
  --end-index 10
```

## Interpreting Cognitive Diagnostics

When running evaluations with the `intercept_and_score_reasoning` tool enabled, the system extracts diagnostics to help you identify issues in agent reasoning.

### Key Signals

1. **Overthinking (Verbosity)**
   - **Symptom**: Internal monologue exceeds 350 or 600 characters.
   - **Meaning**: The agent is struggling to process complex or circular instructions.
   - **Fix**: Simplify instructions. Break down complex tasks into smaller, linear steps.

2. **Hedging**
   - **Symptom**: Use of words like "might be", "guess", "unsure", "assume".
   - **Meaning**: The agent is uncertain about its next action, often due to missing edge case handling.
   - **Fix**: Add explicit instructions for the scenario the agent is unsure about.

3. **Backtracking**
   - **Symptom**: Use of words like "wait", "actually", "on second thought".
   - **Meaning**: The agent is abandoning a plan mid-turn or correcting itself, indicating unclear triggers.
   - **Fix**: Clarify triggers and state transitions in instructions.


