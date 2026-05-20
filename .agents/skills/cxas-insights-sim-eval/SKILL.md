---
name: cxas-insights-sim-eval
description: >-
  Retrieves non-contained CCAI Insights conversations (losses), uses agent intelligence to cluster them into common failure patterns, generates a markdown report, and creates representative SimulationEvals test cases.
  Use when you need to analyze failure patterns and build targeted regression/evaluation suites to verify bug fixes.
---

# Insights Loss Analysis & Simulation Evaluation Generator

This skill instructs you (the AI Agent) to retrieve recent conversations from CCAI Insights, isolate escalated/non-contained sessions (losses), analyze their root causes to group them into failure patterns, write a professional Markdown report, and automatically reverse-engineer representative Simulation Evals test cases.

---

## Execution Routine

Follow these steps in exact sequence:

### Step 1: Parameter Verification
Verify that the user has provided the following required parameters:
- `project_id`: GCP Project ID hosting Insights.
- `location`: Insights location (e.g., `us`).
- `app_id`: Target CXAS App ID (e.g., `db9ee866-28db-458b-b835-78137c974779`).
- `output_dir`: Directory where the final report and test cases will be saved.
- `limit`: Maximum raw conversations to inspect (default: 1000).
- `loss_limit`: Maximum loss transcripts to fetch and analyze (default: 100).

### Step 2: Extract Loss Transcripts
Run the lightweight data-extraction script to dump the loss transcripts into a combined JSON file in your workspace.

**Command Template**:
```bash
python -P .agents/skills/cxas-insights-sim-eval/scripts/fetch_losses.py \
  --project-id "{project_id}" \
  --location "{location}" \
  --app-id "{app_id}" \
  --limit {limit} \
  --loss-limit {loss_limit} \
  --output-file "{output_dir}/raw_losses.json"
```

*Note: Always run python using the virtual environment's executable with the `-P` flag (e.g., `.venv/bin/python -P`) to avoid path pollution.*

### Step 3: Read Transcripts & Summarize Escalations
Use the `view_file` or other file-reading tools to read the generated `{output_dir}/raw_losses.json` file. Extract the `total_inspected`, `total_losses`, and the array of `transcripts` (containing `conversation_id` and `transcript`).

For each conversation transcript:
1. Analyze the conversation between the customer (`user`) and the virtual agent (`agent`).
2. Identify why the conversation escalated or was not contained.
3. Formulate a concise, **1-sentence primary reason for failure/escalation** (max 20 words). E.g., *"Virtual agent failed to authenticate the user due to repeated pin entry errors."*

### Step 4: Cluster Failures into Loss Patterns
Review the complete list of failure reasons you generated in Step 3. Using your analytical capabilities, group these failure reasons into **3 to 7 distinct, mutually exclusive failure patterns**.

For each pattern, define:
1. **Pattern ID**: A simple key (e.g., `pattern_1`, `pattern_2`, ...).
2. **Name**: A short, descriptive name (e.g., *"Authentication Loop"*, *"Unsupported Customer Intent"*, *"Agent Transfer on Disambiguation"*).
3. **Description**: A clear 1-2 sentence description explaining the pattern and what triggers it.

### Step 5: Categorize All Sessions
Map every analyzed `conversation_id` to one of the defined patterns. Keep track of this mapping for the final report.

### Step 6: Write the Markdown Report
Compile your analysis into a structured Markdown report and write it to `{output_dir}/loss_patterns_report.md`. Use the following structure:

```markdown
# Loss Patterns Analysis Report

**Project**: `{project_id}`
**App ID**: `{app_id}`

## Executive Summary

- **Total Conversations Inspected**: {total_inspected}
- **Non-Contained Conversations (Losses)**: {total_losses}
- **Containment Rate**: {containment_rate}%

## Loss Patterns Distribution

| Pattern ID | Name | Count | Percentage |
| --- | --- | --- | --- |
| `pattern_1` | Pattern Name | Count | Pct% |

## Detailed Patterns Breakdown

### `pattern_1`: Pattern Name

**Description**: Pattern description.
**Total Conversations**: Count

#### Examples & Failure Reasons:
- **Session `{conversation_id_1}`**: Failure reason from Step 3.
- **Session `{conversation_id_2}`**: Failure reason from Step 3.

---
```

### Step 7: Generate Representative Simulation Evals
For each identified failure pattern:
1. Select the **first conversation ID** mapped to it as the representative session.
2. Analyze the original transcript again to reverse-engineer a `SimulationEval` JSON test case representing the user's **intended goal** and the **successful resolution path** (so that when the bug is fixed, the simulator can verify it can be successfully completed).
3. Save the JSON file to `{output_dir}/sim_evals/[safe_pattern_name]_[conversation_id].json`.

Ensure the JSON adheres precisely to this structure:

```json
{
  "name": "Generated Eval: <Short descriptive name based on user goal>",
  "session_parameters": {},
  "steps": [
    {
      "goal": "<The specific objective the user is trying to achieve in this step>",
      "success_criteria": "<How the user knows the agent successfully handled this step (e.g., 'Agent provided the balance')>",
      "response_guide": "<Instructions for the user simulator on how to respond to agent questions (e.g., 'Provide the account number if asked')>",
      "max_turns": 10,
      "static_utterance": "<Optional: The exact first user message from the transcript, to seed the conversation>"
    }
  ],
  "expectations": [
    "<Global expectation for the agent (e.g., 'Agent must successfully resolve the issue without escalating')>"
  ]
}
```

### Step 8: Present Summary to User
Present a clear summary of your findings directly in the chat, pointing the user to `{output_dir}/loss_patterns_report.md` and the generated evaluations inside `{output_dir}/sim_evals/`.
