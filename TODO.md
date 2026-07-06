# TODO: DFCX→CXAS Migration HTML Reporter Evals Integration

This file tracks planned improvements and future feature integrations for the local local migration reporter dashboard.

## [ ] High Priority: Live Sim Evals Parsing & Dashboard Integration

Implement the Python-side aggregation and dynamic mapping logic in `MigrationAnalysisBuilder` to populate the **Evals** tab of the HTML reporter.

### Context & Objectives
The local dashboard is equipped with an **Evals Tab** and an inspector sidebar capable of rendering rich transcripts, step progress metrics, and tag summaries. However, the Python class `MigrationAnalysisBuilder` in `src/cxas_scrapi/migration/analysis_reporter.py` currently leaves the `evals` and `eval_traces` fields as empty (`None`).

### Requirements & Design Spec

1. **File Discovery**:
   * Automatically scan the target's `output_dir` and sibling `eval-reports/` directories for simulation outcomes matching the pattern `sim_results_*.json` or `sim_results.json`.
   * Sort found files by modification time to parse the most recent run.

2. **Transcripts & Metrics Parsing**:
   * Resiliently load simulation results using the `cxas_scrapi.utils.reporting.load_sim_results` utility.
   * Map individual test case runs (`passed`, `turns`, `duration_s`, `session_id`, `transcript`, `step_details`, `expectation_details`).

3. **Dynamic Agent Transfer Attribution**:
   * Since simulation runs are triggered globally against the CXAS application, they do not natively map to a single sub-agent.
   * Parse the conversation's `detailed_trace` list for routing transitions:
     ```
     Agent Transfer: Transferred to <AgentName>
     ```
   * Map the simulation test to the extracted `<AgentName>` if it exists in `snapshot.agents`.
   * If no transfer occurred, safely default attribution to the root agent (detected via `is_root` from the grouping model).

4. **Data Model Format (HTML/JS Interface)**:
   * **`snapshot.evals`** (KPI Card / pass-rate summaries):
     ```json
     {
       "<AgentName>": {
         "pass": 1,
         "total": 2,
         "pct": 50.0,
         "tags": [
           {"tag": "P0", "p": 1, "t": 1},
           {"tag": "billing", "p": 1, "t": 1}
         ]
       }
     }
     ```
   * **`snapshot.eval_traces`** (Full list + agent indexing):
     ```json
     {
       "all": [
         {
           "name": "billing_inquiry",
           "passed": true,
           "turns": 2,
           "duration_s": 1.5,
           "primary_tag": "P0",
           "goals": "1/1",
           "expectations": "1/1",
           "agent": "RootAgent",
           "tags": ["P0", "billing"],
           "step_details": [...],
           "expectation_details": [...],
           "trace_tail": [...],
           "transcript": "...",
           "session_id": "sess-uuid",
           "source_report": "sim_results_xxx.json",
           "session_parameters": {...}
         }
       ],
       "by_agent": {
         "RootAgent": [...]
       }
     }
     ```

5. **Test Coverage**:
   * Add pytest coverage to `tests/cxas_scrapi/migration/test_analysis_reporter.py` validating discovery, transfer detection, fallback attribution, and KPI calculations.
