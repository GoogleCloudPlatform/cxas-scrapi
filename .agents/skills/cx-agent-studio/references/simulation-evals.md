# Simulation Eval Conversion

Convert turn-by-turn CX Agent Studio golden evaluations into high-level,
goal-oriented SCRAPI `SimulationEvals` test cases. Use this route when the user
asks to convert CXAS/CES golden evals, infer tool expectations, or run the
converted simulations.

## Inputs

Before executing scripts, get:

- Full app resource name, for example
  `projects/<project>/locations/<location>/apps/<app>`.
- Base output directory for fetched data and generated simulation evals.

## Workflow

1. Verify the environment:

   ```bash
   python -c "import cxas_scrapi"
   gcloud auth list
   ```

2. Fetch evaluations and agent tool configuration:

   ```bash
   python .agents/skills/cx-agent-studio/scripts/simulation-evals/fetch_app_data.py \
     --app-name "projects/.../locations/.../apps/..." \
     --output-dir /path/to/output_directory
   ```

3. Fetch full tool schemas:

   ```bash
   python .agents/skills/cx-agent-studio/scripts/simulation-evals/fetch_tool_schemas.py \
     --app-name "projects/.../locations/.../apps/..." \
     --output-dir /path/to/output_directory
   ```

4. Convert fetched goldens into simulation eval test cases:

   ```bash
   python .agents/skills/cx-agent-studio/scripts/simulation-evals/convert_eval.py \
     --output-dir /path/to/output_directory \
     --parallelism 5
   ```

5. Run a slice of converted simulations:

   ```bash
   python .agents/skills/cx-agent-studio/scripts/simulation-evals/run_evals.py \
     --app-name "projects/.../locations/.../apps/..." \
     --output-dir /path/to/output_directory \
     --parallelism 5 \
     --start-index 0 \
     --end-index 10
   ```

## Output Layout

Use the same base output directory throughout:

- `golden_evals/`: fetched CXAS golden evaluations.
- `tools/`: fetched tool schemas.
- `agent_tools.json`: app/agent tool configuration.
- `sim_evals/`: converted SCRAPI simulation eval cases.

## Diagnostics

If the app has the `intercept_and_score_reasoning` tool enabled, `run_evals.py`
extracts reasoning diagnostics for failures and adds suggestions to the HTML
report.

Use these signals when summarizing failures:

- Overthinking: long internal monologue, usually complex or circular
  instructions.
- Hedging: uncertainty language, usually missing edge case handling.
- Backtracking: abandoned or corrected plans, usually unclear triggers or state
  transitions.
