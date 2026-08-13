---
name: cxas-eval-coverage
description: >-
  Calculates and generates static coverage reports for Gemini Enterprise for Customer Experience (GECX) conversational agents by mapping evaluations against tools, callbacks, agent transfers and instructions.
  Use when analyzing unit test / evaluation coverage of a conversational agent or identifying gap areas (un-tested code or commands) in GECX agent workspace evaluations.
  Don't use for generic python code coverage (use standard coverage tools) or running execution-level/simulation evaluations themselves.
---

# CXAS Evaluation Coverage Analyzer Skill

Use this skill to assess how comprehensively existing evaluations cover the
agent's capabilities, specifically its tools, callbacks, agent transfers and
instructions.

## Core Workflow Steps

1. **Define Workspace Paths**: Identify the location of:

   - The agent project root folder
   - The tools folder (`tools/`)
   - The evaluations folders (note that you may not find all these folders or
     find other folders containing evals, identify which folders to ingest
     based on whether or not their content is evals):
     - `evaluations/`
     - `evaluationDatasets/`
     - `evaluationExpectations/`
     - `evals/`
   - The output directory for the coverage report, if there is no folder
     named `coverage_reports`, then create one at the root of the agent
     directory and output the coverage report there.

1. **Run the Coverage Analysis Script**: Execute the `calculate_coverage.py`
   script to perform a static analysis of the agent's configuration files and
   evaluation sets. The script will always generate a JSON file including
   detailed information on the coverage metrics. Use `--output-file` to specify
   the JSON file path. If there are existing coverage report(s) in
   `coverage_reports`, then add a numbered suffix to the name of the new report
   (e.g. `coverage_report_1.json`). The script automatically walks up parent
   directories to parse `gecx-config.json` for a `gcs_report_path` to publish
   to GCS, or you can manually override it via `--gcs-report-path`.

1. **Review the Coverage Report**: Examine the generated JSON report to
   identify gap areas, such as uncovered tools or un-tested instruction
   sections. Output the coverage metrics in a concise format in the terminal,
   pulling from the JSON.

1. **Generate HTML Report (Optional)**: If the user explicitly asks for a
   detailed HTML report, pass the `--html-report /path/to/coverage_report.html`
   flag to `calculate_coverage.py` to generate it alongside the JSON report.

   ## Automation Scripts

### Calculate Coverage

`scripts/calculate_coverage.py`

Computes evaluation coverage percentages and generates a comprehensive report.

Usage:

```bash
uv run python .agents/skills/cxas-eval-coverage/scripts/calculate_coverage.py \
    --agent_dir /path/to/agent \
    --output_file /path/to/coverage_reports/coverage_report.json \
    --html_report /path/to/coverage_reports/coverage_report.html \
    --project_id project_id \
    --location location \
    --gcs_report_path gs://my-cxas-evals-reports/<deployed_app_id>/coverage-reports/
    --concurrency 2
```

*Note: The `--model` flag allows you to choose the Gemini model (default is
`gemini-2.5-flash`, but `gemini-3.1-pro` can be used for higher reasoning
accuracy).*

Supported Coverage Metrics:

- **Tool Coverage**: Scans the `tools/` directory and marks a tool as covered
  if and only if it has an associated unit test (using `ToolEvals` via a
  `tests:` block in YAML/JSON test files).
- **Callback Coverage**: Checks for unit tests associated with each callback.
- **Instruction Segment Coverage**: Uses an XML tag fallback structure
  combined with an **LLM categorization pass** to filter out non-testable
  conversational fillers (maintaining line-by-line traceability) before
  performing vector-similarity-driven coverage analysis.
