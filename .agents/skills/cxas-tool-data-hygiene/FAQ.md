# Frequently Asked Questions (FAQ) — CXAS Tool Data Hygiene Skill (`cxas-tool-data-hygiene`)

This FAQ guide answers the most common questions for engineers, analysts, and leaders using the **`cxas-tool-data-hygiene`** skill for the first time.

---

## Category 1: Getting Started & Understanding the Skill

### Q1. What is the `cxas-tool-data-hygiene` skill and what problem does it solve?
**Answer:**  
The `cxas-tool-data-hygiene` skill is an automated auditing and recommendation framework for **Google Customer Engagement Suite (CXAS / GECX / CES)** conversational agents. It solves three major production problems:
1. **Tool Response Payload Bloat (`distinct_tool_response`):** Identifies tools returning massive JSON payloads (`10,000–214,000+` characters), deep nesting (`>4` levels), double-serialized JSON strings (`\"`), high empty/null ratios, or UI layout pollution that slow down LLM latency and cause hallucinations.
2. **Python Tool Code & Session Context Memory Bloat (`python_code.py`):** Detects critical anti-patterns like `return context.state` (which dumps the entire session history back to the LLM), raw API responses saved directly into `context.set_variable(...)`, bare `return` (`None`) statements, and missing Google-style docstrings (`[T002]`).
3. **Sub-Agent Instruction Mismatches (`instruction.txt`):** Catches missing `{@TOOL: <tool_name>}` tags, tool name typos, and `<if condition="...">` checks that reference fields the tool never actually returns.

---

### Q2. Do I need to search through multiple files in the skill folder before running an analysis?
**Answer:**  
**No.** The skill is designed with a **Single-File Configuration** architecture. Every variable you need to update before a run is located in **one file**:
- [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py)

You never need to edit [`scripts/run_tool_data_hygiene.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py) or [`SKILL.md`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/SKILL.md).

---

## Category 2: Updating Variables & Changing Folder Paths

### Q3. Which variables do I need to update before running the skill, and where are they?
**Answer:**  
Open [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L18-L43) and update **Section 1 (Lines 21–33)**:
1. **`AGENT_ROLE`** *(Line 21)*: The sub-agent role name in BigQuery (e.g., `"BYOD"`, `"Equipment_upgrade"`, `"VZ_Billing_Agent"`, `"Activation"`).
2. **`TOOL_NAME`** *(Line 26)*: The exact tool display name to analyze (e.g., `"get_eligibleline_byod"`, `"addsim_byod"`, `"get_case_activations"`).
3. **`START_DATE`** *(Line 30)*: Start date in `'YYYY-MM-DD'` format for pulling production BigQuery logs (e.g., `"2026-09-28"`).
4. **`ROW_LIMIT`** *(Line 33)*: Maximum number of BigQuery rows to fetch (default: `500`).

---

### Q4. How do I change the output folder path where reports and CSV/JSON files are saved?
**Answer:**  
By default, outputs are saved under `InvestigationData/ToolDataHygiene/<AGENT_ROLE>__<TOOL_NAME>/`. You can change this in two ways:
1. **Permanent Change in `config.py` (Line 58):**  
   Open [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L57-L58) and edit `OUTPUT_DIR`:
   ```python
   OUTPUT_DIR: str = "ToolDataHygiene/Outputs"  # Or any relative path inside the workspace
   ```
2. **One-Time Override via CLI Flag (`--output-dir`):**  
   ```bash
   python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py \
     --output-dir "ToolDataHygiene/Outputs"
   ```

---

### Q5. What if my local CXAS agent codebase is stored in a different directory than `VZAmitLocal/app/Dev_-_Profile_Chat_Agent`?
**Answer:**  
Update **`LOCAL_AGENT_APP_DIR`** on **Line 55** of [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L54-L55) (or pass `--app-dir "<path>"` on the command line).

---

### Q6. What if the `role` name in BigQuery is slightly different from the folder name under `agents/` in the local repo?
**Answer:**  
1. The script automatically normalizes spaces, hyphens, and underscores (so `"Adaptive Authentication"` in BigQuery automatically matches `agents/Adaptive_Authentication/`).
2. If the folder name is completely different from the BigQuery `role`, set **`LOCAL_SUB_AGENT_FOLDER`** on **Line 38** of [`config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L38) (e.g., `LOCAL_SUB_AGENT_FOLDER = "Billing"`), or pass `--sub-agent-folder "Billing"` on the CLI.

---

## Category 3: Ways to Run the Skill (Ordered by Priority: #1 Natural Language -> #2 Python CLI)

### Q7. How many ways can I run this skill, and what is the recommended priority order?
**Answer:**  
There are **5 ways** to run the skill, ordered from **highest priority (Natural Language in Chat)** to **secondary priority (Python CLI in Terminal)**:

1. **🥇 Way 1 — Natural Language Prompt Using `config.py` (Priority #1 — Primary & Recommended):**  
   Update `AGENT_ROLE`, `TOOL_NAME`, `START_DATE`, and `ROW_LIMIT` in [`config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py), then simply ask your AI coding agent in plain English:
   > *"Run the `cxas-tool-data-hygiene` skill using the current variables in `config.py` and give me the analysis and recommendations."*
   *(Why this is #1: The agent automatically runs the BigQuery extraction + AST code check behind the scenes AND performs the LLM semantic review, instruction vs. tool response vs. context session variable verification, and HITL filtering.)*

2. **🥇 Way 2 — Natural Language Prompt with On-the-Fly Overrides (Without Editing `config.py`):**  
   Ask your AI coding agent in plain English to run the skill for any specific Sub-Agent and Tool directly in chat:
   > *"Use the `cxas-tool-data-hygiene` skill to analyze role `BYOD` and tool `get_past_device_byod` from `2026-09-28` with limit `500`, check which instruction variables are missing from both the tool response and context session, and give me the recommendations."*

3. **🥈 Way 3 — Zero-Flag Python CLI Run via `config.py` (Secondary / Terminal Option):**  
   If you want to run the helper script directly from the terminal using the values in `config.py`:
   ```bash
   python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py
   ```

4. **🥈 Way 4 — Python CLI Flag Override Run (Terminal Without Editing `config.py`):**  
   ```bash
   python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py \
     --role "BYOD" \
     --tool "get_past_device_byod" \
     --start-date "2026-09-28" \
     --limit 500
   ```

5. **🥈 Way 5 — Offline File or Batch Loop Python CLI Run:**  
   Re-run the analysis on a previously saved `.json` or `.csv` BigQuery file without querying BigQuery again, or loop over multiple tools in bash:
   ```bash
   # Offline re-run:
   python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py \
     --role "BYOD" \
     --tool "get_past_device_byod" \
     --offline-file "InvestigationData/ToolDataHygiene/BYOD__get_past_device_byod/bq_distinct_tool_responses_latest.json"

   # Batch loop across multiple tools:
   for tool in "get_eligibleline_byod" "get_past_device_byod" "addsim_byod"; do
     python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py --role "BYOD" --tool "$tool"
   done
   ```

---

### Q8. Can I run this skill in Natural Language across different LLMs like Google Antigravity, Anthropic Claude, or Cursor?
**Answer:**  
**Yes.** The skill uses a model-agnostic design that prioritizes Natural Language execution across any LLM:
- **Google Antigravity / Gemini CLI (Recommended):** Automatically discovers `.agents/skills/cxas-tool-data-hygiene/SKILL.md` when you ask in natural language to run `cxas-tool-data-hygiene`.
- **Anthropic Claude Code / Cursor / Windsurf:** Ask in natural language: *"Read `.agents/skills/cxas-tool-data-hygiene/SKILL.md` and `.agents/skills/cxas-tool-data-hygiene/config.py`, run the `cxas-tool-data-hygiene` skill, and give me the analysis and recommendations."*
- **Claude Desktop / Web UI (No Terminal Access):** Upload `SKILL.md` along with the generated `tool_data_hygiene_report_latest.md` or `hygiene_metrics_and_ast_audit_latest.json` to the chat and ask the LLM in natural language to review it.

---

## Category 4: BigQuery Data Extraction & Understanding the Logs

### Q9. Which Google Cloud services and BigQuery table does this skill use?
**Answer:**  
1. **Google BigQuery:** Queries the production CCAI Insights agent/tool logs table:
   - **Project:** `conversation-analysis-433113`
   - **Dataset:** `vz_vcg_wireless_chat_nga`
   - **Table:** `ccai_insights_agent_tool`
   - **Full Table Name:** `` `conversation-analysis-433113.vz_vcg_wireless_chat_nga.ccai_insights_agent_tool` ``
2. **Google Gemini Customer Experience Agent Studio (CXAS / GECX / CES):** Audits the local exported CXAS agent files (`agents/<SubAgent>/instruction.txt`, `agents/<SubAgent>/<SubAgent>.json`, and `tools/<ToolName>/python_function/python_code.py`).

---

### Q10. Why does BigQuery return `'NA'` for half of the rows (e.g., 250 `'NA'` rows out of 500 total rows)?
**Answer:**  
In `ccai_insights_agent_tool`, every tool execution is logged as **two separate rows** in the `chunks` column:
1. **A `toolCall` row (Request):** Where `tool_call_display_name` is populated and `$.toolCall` contains the input arguments sent to the tool. On this row, our SQL `CASE` statement sets `distinct_tool_response = 'NA'`.
2. **A `toolResponse` row (Output):** Where `tool_call_display_name IS NULL` and `$.toolResponse.response` contains the actual JSON payload returned by the tool.

In **Step 2**, the skill automatically filters out the `'NA'` (`toolCall`) rows so that only real `toolResponse` payloads are measured for token bloat, nesting depth, and sparsity.

---

### Q11. Does running this skill modify any production BigQuery tables or CXAS cloud resources?
**Answer:**  
**Never.** The skill is strictly **100% Read-Only** in the cloud:
- It only runs `SELECT` queries against BigQuery.
- It never modifies `conversation-analysis-433113` or the CES application `VZ-custom-ccai-external`.
- All output files are written strictly to your local workspace (`InvestigationData/ToolDataHygiene/`).

---

### Q11a. Can anyone use this skill for a different Google CXAS app that has a different BigQuery table schema or different column names?
**Answer:**  
**Yes! The skill is 100% dynamic and schema-agnostic.**  
In [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L38-L90), any team building an agent on Google CXAS can pull tool responses from **any BigQuery schema** using **4 Dynamic Schema Modes** (`BQ_SCHEMA_MODE` in `config.py` or `--schema-mode` on the CLI):

| Mode (`BQ_SCHEMA_MODE`) | Use Case | How It Works |
| :--- | :--- | :--- |
| **1. `"auto"`** *(Zero-Config Auto-Detection)* | Point the skill at **any new BigQuery table** without manually mapping column names. | Runs `auto_detect_bq_schema` (`bq show --schema --format=json`) to inspect the table's columns, automatically matches conversation ID (`conversation_id`, `session_id`, `call_id`), timestamp (`timestamp`, `created_at`, `startTimestamp`), agent role (`role`, `agent_name`, or `None`), tool name (`tool_name`, `function_name`, `tool_call_display_name`), and payload (`chunks`, `tool_response`, `response_payload`, `output_json`), and builds the right SQL query automatically. |
| **2. `"flat_columns"`** | BigQuery tables where the **tool name** and **tool response JSON/string** are stored directly in flat columns (no nested `chunks` JSON). | Dynamically builds a clean SQL query against your configured `BQ_COL_TOOL_NAME` and `BQ_COL_PAYLOAD` columns. If the table does not have a sub-agent role column, setting `BQ_COL_AGENT_ROLE = None` automatically skips the role filter. |
| **3. `"ccai_chunks_json"`** *(Default)* | Standard CXAS / CCAI Insights nested JSON schemas (like `chunks` or `turn_payload`). | Uses configurable column names (`BQ_COL_CONVERSATION_ID`, `BQ_COL_TIMESTAMP`, `BQ_COL_AGENT_ROLE`, `BQ_COL_TOOL_NAME`, `BQ_COL_PAYLOAD`) and customizable JSON paths (`BQ_JSON_PATH_TOOL_CALL`, `BQ_JSON_PATH_TOOL_RESP_NAME`, `BQ_JSON_PATH_TOOL_RESP_DATA`). |
| **4. `"custom_sql"`** | Complex schemas requiring `UNNEST(...)`, multi-table `JOIN`s, or custom `WHERE` clauses. | Executes `CUSTOM_BQ_SQL_QUERY` from `config.py` using `{table}`, `{agent_role}`, `{tool_name}`, `{start_date}`, and `{row_limit}` placeholders, and passes the results through `normalize_bq_rows` so all downstream steps (Steps 2–7) work seamlessly. |

---

## Category 5: Output Files & Interpreting the Report

### Q12. What are the 4 output files generated in each `<AGENT_ROLE>__<TOOL_NAME>` folder?
**Answer:**  
1. **`bq_distinct_tool_responses_latest.json`**: Full JSON array of the non-`'NA'` tool response rows pulled from BigQuery.
2. **`bq_distinct_tool_responses_latest.csv`**: Spreadsheet-ready CSV containing `conversationName`, `startTimestamp`, `role`, `tool_call_display_name`, `chunks`, and `distinct_tool_response`.
3. **`hygiene_metrics_and_ast_audit_latest.json`**: Structured JSON summary of all Step 0–6 metrics, observed schema keys, instruction checks, and Python AST code findings.
4. **`tool_data_hygiene_report_latest.md`**: The human-readable Markdown report containing a **Ready-to-Paste Tracker Table** in Section 1, followed by detailed Step 0–6 findings and the HITL checklist.

---

### Q13. Why does a tool sometimes say `"CLEAN_NO_RECOMMENDATION_NEEDED"` in Step 3, yet still list issues in Step 5/6?
**Answer:**  
This is one of the most important features of the skill (**HITL Filter #3 & #4**):
- **Step 3 measures what the tool returns to the LLM (`distinct_tool_response`).** For example, `get_eligibleline_byod` returns `{"result":{"Status":"Success"}}` (`31` chars)—which is 100% clean!
- **Steps 4, 5 & 6 measure the local `instruction.txt` and `python_code.py`.** Even if a tool returns a clean 31-character response, its Python code might still be saving duplicate unpruned arrays into `context.state`, executing a bare `return` (`None`) on errors, or missing a docstring (`[T002]`).
- Separating these categories ensures engineers don't waste time redesigning an already-clean tool response when the real fix is inside `python_code.py` or `instruction.txt`.

---

## Category 6: Human-in-the-Loop (HITL) Review & Next Manual Steps

### Q14. Why is Human-in-the-Loop (HITL) manual review required after the skill generates the report?
**Answer:**  
While the automated script and LLM accurately measure payload sizes, nesting depth, and AST code patterns, a human reviewer must validate project context that code alone cannot know:
1. **Upstream Backend API Freeze:** The VZ agent team cannot rewrite shared upstream SOE backend microservices—any payload pruning must be done inside the CXAS Python wrapper (`python_code.py`).
2. **Cross-Agent State Dependencies:** Before pruning a key from `context.set_variable(...)` or `context.state[...]`, a human must verify whether a downstream transferred sub-agent or client-side UI template (`NGARESP_TEMPLATE_DATAMAP`) reads that key.
3. **Avoiding Pedantic Nitpicks:** On small payloads (`< 220` chars), changing `"true"` (string) to `true` (boolean) would break existing `instruction.txt` string comparisons for zero token savings and should be dropped.

---

### Q15. Which tools are automatically exempt from Tool Data Hygiene payload pruning (Step 0)?
**Answer:**  
Configured in Section 3 of [`config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py#L77-L104), four categories of tools are fast-tracked as **Exempt**:
1. **Chain-of-Thought (CoT) Platform Tools:** `setCurrentActivities`
2. **Web Search / Knowledge Base Datastore Tools:** `customer_website_default_datastore_google`, `troubleshoot_datastore`, `VZ_internal_datastore`, `check_destination_coverage`, `chatbot_doc_datastore`, `one_search_datastore`, `conversational_intelligence_ds`
3. **Client-Side UI Widget & Template Tools:** `calendar`, `showTemplate`, `show_2fa_template`, `managePinTemplate`
4. **Static Prompt & Telemetry Tools:** `rule_book`, `set_transfer_context`, `record_user_feedback`, `set_user_utterance`

---

### Q16. What do I do after reviewing the generated report?
**Answer:**  
1. Copy the curated summary from **Section 1 (`Ready-to-Paste Tracker Summary`)** of `tool_data_hygiene_report_latest.md` into the **`Tool Hygiene`** tab of [`ToolDataHygiene/VZ_Chat  Hillclimbing Tracker.xlsx`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/ToolDataHygiene/VZ_Chat%20%20Hillclimbing%20Tracker.xlsx).
2. For tools requiring code or prompt changes, paste the detailed analysis and before/after recommendation into [`ToolDataHygiene/VZ_ToolData_DetailAnalysis&Recommendation.docx`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/ToolDataHygiene/VZ_ToolData_DetailAnalysis&Recommendation.docx).

---

## Category 7: Troubleshooting Common Issues

### Q17. What if `bq_distinct_tool_responses_latest.json` is empty (`[]`) or `0` rows are fetched from BigQuery?
**Answer:**  
Check these three common causes:
1. **`START_DATE` is too recent:** If the tool had low traffic in the last 24–48 hours, move `START_DATE` back by 7–14 days in `config.py` (e.g., `"2026-09-15"`).
2. **`AGENT_ROLE` or `TOOL_NAME` spelling/casing mismatch:** BigQuery string comparisons (`role = '...'` and `tool_call_display_name IN ('...')`) are **case-sensitive**. Verify the exact role and tool name in BigQuery.
3. **100% of rows returned `'NA'` (`toolCall` only):** Some asynchronous webhooks (e.g., `disconnect_webhook`) log `toolCall` events without a synchronous `toolResponse` block. Check `Total BigQuery Rows Fetched` vs. `'NA' Rows Ignored` in Section 3 of the Markdown report.

---

### Q18. What if the `bq` CLI fails with an authentication error?
**Answer:**  
Run the following commands in your terminal to refresh your Google Cloud credentials:
```bash
gcloud auth login
gcloud auth application-default login
```
Then re-run `python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py`.
