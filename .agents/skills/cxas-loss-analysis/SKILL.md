---
name: cxas-loss-analysis
description: >-
  Retrieves non-contained CCAI Insights conversations (losses), uses agent intelligence to cluster them into common failure patterns, and generates a markdown report.
  Use when you need to analyze failure patterns and build targeted regression/evaluation reports.
---

# Insights Loss Analysis & Report Generator

This skill instructs you (the AI Agent) to retrieve recent conversations from CCAI Insights, isolate escalated/non-contained sessions (losses), analyze their root causes to group them into failure patterns, and write a professional Markdown report.

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
Run the lightweight data-extraction script to dump the loss transcripts into chunked JSON files in your workspace.

**Command Template**:
```bash
python3 -P .agents/skills/cxas-loss-analysis/scripts/fetch_losses.py \
  --project-id "{project_id}" \
  --location "{location}" \
  --app-id "{app_id}" \
  --limit {limit} \
  --loss-limit {loss_limit} \
  --output-file "{output_dir}/raw_losses.json"
```

*Note: Always run python using the virtual environment's executable with the `-P` flag (e.g., `.venv/bin/python -P`) to avoid path pollution.*

### Step 3: Read Transcripts & Summarize Escalations
Use the `view_file` or other file-reading tools to read the generated `{output_dir}/raw_losses.json` file. Extract the list of `chunks` (which contains paths to the chunked JSON files).

For each chunk file in the `chunks` list:
1. Read the chunk file to load the batch of transcripts.
2. For each conversation transcript:
   a. Analyze the conversation between the customer (`user`) and the virtual agent (`agent`).
   b. Identify if the user displayed **"AI aversion"**:
      - **Definition**: Sessions where the user did not meaningfully engage with the agent or expressed a strong preference for a human agent (e.g., immediately asking for "human", "agent", "representative" in the first 1-2 turns without describing their issue, or explicitly stating they do not want to talk to an AI/robot).
      - If "AI aversion" is detected, mark this session as **ignored** from the core loss analysis. Note the reason (e.g., *"AI aversion: User demanded human agent immediately"*).
   c. For non-ignored genuine losses:
      - Identify why the conversation escalated or was not contained.
      - Formulate a concise, **1-sentence primary reason for failure/escalation** (max 20 words). E.g., *"Virtual agent failed to authenticate the user due to repeated pin entry errors."*


### Step 4: Cluster Failures into Loss Patterns
Review the complete list of genuine (non-ignored) failure reasons you generated in Step 3. Using your analytical capabilities, group these failure reasons into **8 to 10 distinct, mutually exclusive failure patterns** to provide granular insights.

For each pattern, define:
1. **Pattern ID**: A simple key (e.g., `pattern_1`, `pattern_2`, ...).
2. **Name**: A short, descriptive name (e.g., *"Authentication Loop"*, *"Unsupported Customer Intent"*, *"Agent Transfer on Disambiguation"*).
3. **Description**: A clear 1-2 sentence description explaining the pattern and what triggers it.


### Step 5: Categorize All Sessions
Map every analyzed `conversation_id` to either:
- One of the 8 to 10 defined failure patterns.
- `ignored_ai_aversion` if the user displayed AI aversion.

Keep track of this mapping for the final report.

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

*Note: The containment rate is calculated over the entire set of inspected conversations. A random sample of {loss_limit} non-contained conversations was selected for detailed manual analysis and clustering. Not all conversations were analyzed.*

## Loss Patterns Distribution

| Pattern ID | Name | Count | Percentage of Genuine Losses |
| --- | --- | --- | --- |
| `pattern_1` | Pattern Name | Count | Pct% |
| ... | ... | ... | ... |

*Note: Ignored AI aversion sessions are excluded from the pattern distribution.*

## Detailed Patterns Breakdown

### `pattern_1`: Pattern Name

**Description**: Pattern description.
**Total Conversations**: Count

#### Examples & Failure Reasons:
- **Session `{conversation_id_1}`**: Failure reason from Step 3.
- **Session `{conversation_id_2}`**: Failure reason from Step 3.

---

## Appendix: Ignored Sessions (AI Aversion)
The following sessions were ignored from the pattern analysis because the user displayed AI aversion:
- **Session `{conversation_id_3}`**: AI aversion reason (e.g., *"User demanded human agent immediately"*).
- **Session `{conversation_id_4}`**: AI aversion reason.
```


### Step 7: Present Summary to User
Present a clear summary of your findings directly in the chat, pointing the user to `{output_dir}/loss_patterns_report.md` and highlighting the key patterns and the adjusted containment rate.

