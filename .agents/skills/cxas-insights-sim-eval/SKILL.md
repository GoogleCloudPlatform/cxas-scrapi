---
name: cxas-insights-sim-eval
description: >-
  Extracts successful CCAI Insights conversations and automatically reverse-engineers SCRAPI SimulationEvals test cases.
  Use when you need to mine contained production or experimental sessions to expand your evaluation suite.
---

# Insights Simulation Evaluation Generator

This skill defines instructions for the agent to automatically reverse-engineer high-level, goal-oriented simulation test cases from successful CCAI Insights interactions.

---

## Execution Routine

When the user invokes this skill, follow these explicit execution steps in order:

### 1. Parameter Verification
Ensure you have the following required details from the user:
- `project_id`: GCP Project ID hosting Insights.
- `location`: Insights location (e.g., `us`).
- `app_id`: The target CXAS App ID to filter conversations for (e.g., `db9ee866-28db-458b-b835-78137c974779`).
- `output_dir`: Base output directory where the `sim_evals/` folder will be populated.
- `limit`: Maximum candidate transcripts to extract (default: 5).

### 2. Mine Candidate Transcripts
Run the underlying extraction script to query Insights and dump candidate transcripts. Output the file to a temporary local path (e.g., `./candidate_transcripts.json`).

**Command Template**:
```bash
python .agents/skills/cxas-insights-sim-eval/scripts/generate_evals.py \
  --project-id "{project_id}" \
  --location "{location}" \
  --app-id "{app_id}" \
  --limit {limit} \
  --output-file "./candidate_transcripts.json"
```

### 3. Read Extracted Data
Read the array of JSON objects dumped by the script in `./candidate_transcripts.json`. Each object has a `conversation_id` and a `transcript` string.

### 4. Evaluation Generation Loop
For each conversation object retrieved, act autonomously to generate the evaluation schema. Analyze the `transcript` using the **Generation Instructions** below.

### 5. Save Output Files
Parse the JSON output. Save each resulting schema as a separate `.json` file inside the `sim_evals/` sub-folder of the user's requested `output_dir`. 
- **File Naming**: Create a clean filename using the short name and conversation ID: `[output_dir]/sim_evals/[safe_goal_name]_[conversation_id].json`.

---

## Generation Instructions

Use the following instructions to analyze each extracted transcript and generate its corresponding evaluation test case:

You are an advanced Test Case Generator AI. Your purpose is to analyze the transcript of a successful customer service conversation and reverse-engineer a simulation test case.

You will receive a conversation transcript between an `END_USER` and an `AUTOMATED_AGENT`. This conversation was flagged as successfully contained by the agent.

Your task is to extract the user's overarching goal, break it down into logical steps, and identify key agent expectations to create a test case for a User Simulator.

**Output Requirements:**
You must output a single JSON object representing the evaluation test case. The JSON must adhere to this structure:

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
      "static_utterance": "<Optional: The exact first message from the user in the transcript, to seed the conversation>"
    }
  ],
  "expectations": [
    "<Global expectation 1 for the agent (e.g., 'Agent must use the lookup_account tool')>",
    "<Global expectation 2 for the agent (e.g., 'Agent must not transfer to a human')>"
  ]
}
```

**Guidelines for Generation:**
1. **`steps`**: 
   - For most straightforward interactions, a single step is sufficient.
   - If the conversation clearly had distinct phases (e.g., Phase 1: Authenticate, Phase 2: Request Refund), you can create multiple steps.
   - The `static_utterance` in the first step should be the user's actual first message from the transcript.
2. **`expectations`**:
   - Look at what the agent *actually* did successfully. If it called a specific tool (indicated by `[Tool Call]` or tool names in the text), add an expectation that the agent must use that tool.
   - Since this was a contained session, include an expectation like "Agent must successfully resolve the issue without escalating".
3. **Output format**: Output ONLY the JSON object. Do not include markdown formatting or explanatory text.
