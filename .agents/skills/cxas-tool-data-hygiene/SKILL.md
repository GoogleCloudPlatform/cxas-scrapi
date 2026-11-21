---
name: cxas-tool-data-hygiene
description: >
  End-to-end Tool Data Hygiene, Python Tool Code Audit, and Agent Instruction Alignment skill
  for Google Customer Engagement Suite (CXAS / GECX / CES) conversational agents. Executes the
  standardized 6-Step SOP + Step 0 Pre-Triage: extracts production tool payloads from BigQuery
  (`ccai_insights_agent_tool`), filters `NA` toolCall rows, evaluates 6-Pillar payload hygiene,
  audits sub-agent `instruction.txt` alignment (`{@TOOL: ...}` syntax and post-tool conditionals),
  inspects `python_code.py` for context memory dumps and return anti-patterns (`return context.state`),
  and applies the 5 Mandatory Human-in-the-Loop (HITL) noise-reduction filters.
---

# CXAS Tool Data Hygiene & Code Alignment Skill (`cxas-tool-data-hygiene`)

Use this skill whenever the user asks to perform **Tool Data Hygiene**, **Tool Response Hygiene Analysis**, **Tool Payload Optimization**, or **Tool Code & Instruction Alignment** for any sub-agent role or tool in the VZ CX Agent / Google Customer Engagement Suite (CXAS) workspace.

---

## 1. Single-Point Configuration (`config.py`)

All runtime variables are centralized in **one file** so users and LLM agents never have to search across multiple scripts:

- **Configuration File:** [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py)

### Variables to Update Before Running

| Variable | Description | Example |
| :--- | :--- | :--- |
| `AGENT_ROLE` | Sub-agent role in BigQuery (`role`) | `"BYOD"`, `"VZ_Billing_Agent"`, `"Equipment_upgrade"`, `"Activation"` |
| `TOOL_NAME` | Target tool (`tool_call_display_name` / `$.toolResponse.displayName`) | `"get_addLine_createCart_byod"`, `"addsim_byod"`, `"get_case_activations"` |
| `START_DATE` | Start date (`YYYY-MM-DD`) for BigQuery log extraction | `"2026-09-21"` |
| `ROW_LIMIT` | Maximum number of records to fetch per query | `500` |
| `LOCAL_SUB_AGENT_FOLDER` | Optional override if local folder under `agents/` differs from `AGENT_ROLE` | `None` (auto-resolved) |
| `OFFLINE_BQ_FILE` | Optional path to a saved JSON/CSV BigQuery export to skip live BQ execution | `None` |

---

## 2. Automated Execution Workflow

### Step A: Update `config.py` (or pass CLI overrides)
Edit `AGENT_ROLE`, `TOOL_NAME`, `START_DATE`, and `ROW_LIMIT` in [`.agents/skills/cxas-tool-data-hygiene/config.py`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/config.py), or pass `--role`, `--tool`, `--start-date`, `--limit` on the command line.

### Step B: Run the Automated Analyzer Script (Live BigQuery Extraction + Local Code/Instruction Audit)
Always run the analyzer script **without** `--dry-run-sql` so that it executes the live BigQuery SQL query via `bq query` and populates `bq_distinct_tool_responses_latest.json` and `bq_distinct_tool_responses_latest.csv`:

```bash
# Run using the values configured in .agents/skills/cxas-tool-data-hygiene/config.py
python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py

# Or run with explicit CLI overrides (which take precedence over config.py)
python3 .agents/skills/cxas-tool-data-hygiene/scripts/run_tool_data_hygiene.py \
  --role "BYOD" \
  --tool "get_addLine_createCart_byod" \
  --start-date "2026-09-21" \
  --limit 500
```

> **Safety Constraint:** Always obey the workspace's Local-Only Write Rule (`.agents/AGENTS.md`). The script executes strictly **read-only** `SELECT` queries against BigQuery (`conversation-analysis-433113.vz_vcg_wireless_chat_nga.ccai_insights_agent_tool`) with mandatory attribution (`--label datacloud:antigravity` and `CLOUDSDK_METRICS_ENVIRONMENT=datacloud.antigravity`) and writes all output reports locally to `InvestigationData/ToolDataHygiene/`.

### Step C: Review Generated Artifacts & Perform LLM Deep Synthesis
The script writes four structured artifacts to `InvestigationData/ToolDataHygiene/<AGENT_ROLE>__<TOOL_NAME>/`:
1. `bq_distinct_tool_responses_latest.json` — Cleaned BigQuery rows (`conversationName`, `startTimestamp`, `role`, `tool_call_display_name`, `chunks`, `distinct_tool_response`).
2. `bq_distinct_tool_responses_latest.csv` — CSV export of the BigQuery tool response records.
3. `hygiene_metrics_and_ast_audit_latest.json` — Structured telemetry across Steps 0–6.
4. `tool_data_hygiene_report_latest.md` — Comprehensive SOP report with exact file/line citations and recommended refactors.

After running the script, **read `tool_data_hygiene_report_latest.md`**, inspect the referenced local files (`instruction.txt` and `python_code.py`), and synthesize final recommendations following the **6-Step SOP** and **5 Mandatory HITL Filters** below.

---

## 3. The 6-Step SOP & LLM Evaluation Rules

### Step 0: Pre-Triage & Tool Category Exemption Check
Before writing payload-pruning recommendations, check if the tool belongs to one of the **4 Exempt Categories** (configured in `config.py`):
1. **Internal Reasoning / Chain-of-Thought (CoT) Tools** (`setCurrentActivities`, `think`, `report_thought`): Do NOT recommend schema pruning; verify only that internal thought strings are not echoed to the customer.
2. **Knowledge / Web Search & Datastore Tools** (`customer_website_default_datastore_google`, `troubleshoot_datastore`, `VZ_internal_datastore`, `check_destination_coverage`, `chatbot_doc_datastore`, `one_search_datastore`, `conversational_intelligence_ds`): Evaluated via Vertex AI Search chunk size and grounding settings—not Python wrapper pruning.
3. **UI Widget, Carousel & Template Rendering Tools** (`calendar`, `showTemplate`, `show_2fa_template`, `managePinTemplate`): Require UI layout fields by design for the chat client.
4. **Static Prompt-Injection & Telemetry Utility Tools** (`rule_book`, `set_transfer_context`, `record_user_feedback`, `set_user_utterance`): Do NOT require payload pruning unless double-serialized (`\"`) or failing silently.

### Step 1: Dynamic BigQuery Log Extraction (Supports Any CXAS Project & Table Schema)
Executes a parameterized read-only SQL query against `BQ_PROJECT_DATASET_TABLE` configured in `config.py` (or `--table` CLI flag) using one of **4 Dynamic Schema Modes** (`BQ_SCHEMA_MODE` / `--schema-mode`):
1. **`"ccai_chunks_json"` (Default):** Standard CXAS / CCAI Insights nested JSON payload schema (`chunks` column with `$.toolCall` and `$.toolResponse.response`). All column names (`BQ_COL_CONVERSATION_ID`, `BQ_COL_TIMESTAMP`, `BQ_COL_AGENT_ROLE`, `BQ_COL_TOOL_NAME`, `BQ_COL_PAYLOAD`) and JSON paths (`BQ_JSON_PATH_*`) are customizable in `config.py`.
2. **`"flat_columns"`:** For any BigQuery table where the tool name and tool response JSON/string are stored directly in flat columns (`BQ_COL_TOOL_NAME`, `BQ_COL_PAYLOAD`). Set `BQ_COL_AGENT_ROLE = None` if your table does not have a sub-agent role column.
3. **`"auto"` (Zero-Config Auto-Detection):** Automatically inspects the target BigQuery table's schema (`bq show --schema --format=json`) to detect column names (`session_id`/`conversation_id`, `timestamp`/`created_at`, `tool_name`, `tool_response`/`chunks`) and selects the appropriate SQL query automatically.
4. **`"custom_sql"`:** Uses `CUSTOM_BQ_SQL_QUERY` in `config.py` (with `{table}`, `{agent_role}`, `{tool_name}`, `{start_date}`, `{row_limit}` placeholders) for arbitrary schemas (`UNNEST`, multi-table `JOIN`s). Any returned columns are automatically normalized via `normalize_bq_rows()`.

### Step 2: Filtering Out `"NA"` (`toolCall`) Rows
- Each tool execution logs two rows in `chunks`: a `toolCall:` request (where `distinct_tool_response` is `'NA'`) and a `toolResponse:` payload.
- **Rule:** Always exclude `distinct_tool_response == 'NA'` rows from response hygiene statistics.
- **Exception:** If **100% of rows** return `'NA'`, check whether the tool is an asynchronous webhook (`disconnect_webhook`) or a silent failure (`{"result": ""}`).

### Step 3: 6-Pillar Tool Response Hygiene Analysis
Evaluate valid `distinct_tool_response` JSON payloads across six pillars:
1. **Payload Bloat & Token Consumption:** Flag responses exceeding `2,000` characters (~500 tokens) or extreme dumps (`10,000–21,000+` chars like `ONEBOT_BILLING_INIT` or `addsim_byod`).
2. **Sparsity, Nulls, Empty Strings & `"NaN"` Literals:** Flag high empty-field ratios (`>35%` empty keys across `>=8` keys), silent `{"result": ""}` responses (`submit_fee_waiver`, `get_pin`), or string `"NaN"` literals (`manage_roles_by_tag`).
3. **Structural Complexity, Deep Nesting & Double Serialization:** Flag nesting depth `>3` levels or double-encoded JSON strings (`\"` inside `result` or `billOverviewDetails`) that force LLM parsing hallucinations.
4. **Redundant / Duplicate Entities & UI/System Noise:** Flag duplicate phone keys (`mtn`, `mdn`, `PhoneNumber`, `phone_number`) and non-conversational UI/routing fields (`pageLayout`, `carousel`, `quickReplies`, `sorId`, `lnpMsgType`, `sessionJWT`).
5. **Key Casing & Data Type Consistency:** Flag mixed naming styles (`PascalCase`, `camelCase`, `snake_case`, `UPPER_SNAKE`) in the same payload, stringified booleans (`"true"`, `"Y"`, `"N"`), and currency symbols inside numeric fields (`"$47.41"`).
6. **Security, PII & Exception / Prompt Leakage:** Flag raw Python tracebacks, stack traces, internal hostnames/tokens, or embedded prompt instructions inside data fields (`nextBillText_instructions`).

### Step 4: Sub-Agent Instruction (`instruction.txt`) vs. Tool Response & Context Session Alignment Audit
Inspect `VZAmitLocal/app/Dev_-_Profile_Chat_Agent/agents/<SubAgent>/instruction.txt` for:
1. **Exact Invocation Syntax:** Must use `{@TOOL: <tool_name>}`. Flag plain-text mentions or tool name typos (e.g., `get_case` instead of `{@TOOL: get_case_activations}`).
2. **Two-Stage Variable Cross-Reference (`distinct_tool_response` -> Session Context `context.state`):**
   - Extract every variable referenced in the instruction block around the tool call across all CXAS conventions: curly braces (`{var}`), template brackets (`[var]`), bulleted variable lists (`- var`), backtick/quoted field extractions (`` `var` ``, `'var'`), and conditional checks (`var == "val"`).
   - **Check 1 (Tool Response):** Check whether the variable name is returned in `distinct_tool_response` (from BigQuery) or `return {...}` in `python_code.py`.
   - **Check 2 (Context Session Window):** If the variable is **NOT** in the tool response, check whether `python_code.py` saves that variable in the session context window (`context.state["var"] = ...`, `context.set_variable("var", ...)`, `context.variables["var"] = ...`). Also check if an earlier runtime bug in `python_code.py` (such as an unsafe `context.state["..."]` bracket read raising `KeyError`) prevents the context assignment line from executing.
   - **Explicit Missing Variable List (`missing_from_both_response_and_context`):** Output a dedicated list of all variable names referenced in `instruction.txt` that are **NEITHER returned by the tool response NOR saved in the context session**, along with closest available key suggestions (e.g., `deviceModel` in `get_past_device_byod` -> closest available key in tool response is `productName`).
3. **Reachability:** Flag tools that exist in BigQuery or `tools/` but have zero invocation references in `instruction.txt`.

### Step 5: Python Tool Code (`python_code.py`) & Session Context Audit
Inspect `VZAmitLocal/app/Dev_-_Profile_Chat_Agent/tools/<ToolName>/python_function/python_code.py` for:
1. **Raw API Dumps to Session Memory (`RAW_API_RESPONSE_CONTEXT_DUMP`):** Never pass an unpruned `response.json()` directly into `context.set_variable("var", raw_response)` (Bloats `context.state` across the entire session).
2. **Duplicate Session Context Storage (`DUPLICATE_CONTEXT_STATE_ASSIGNMENT`):** Flag when the exact same unpruned list/dict is saved under multiple keys in `context.state` (e.g., saving `inactive_device_list` to both `context.state["inactive_device_list"]` and `context.state["past_devices"]`).
3. **Stale Context State in Return (`STALE_CONTEXT_STATE_IN_RETURN`):** Flag when `return` reads from `context.state.get("key")` while the `else:` branch does not reset `context.state["key"]`, leaking stale turn data or returning `None`.
4. **Premature Context Memory Wipes (`CONTEXT_MEMORY_WIPE_CHECK`):** Flag `context.set_variable("var", {})` when it erases user input needed in later turns (e.g., `get_email` wiping `enteredEmail`).
5. **Redundant Context Re-Fetching (`REDUNDANT_CONTEXT_REFETCH`):** Flag `context.set_variable("x", val)` followed immediately by `context.get_variable("x")` in the same function scope.
6. **Latent Python Bugs & Security Issues:**
   - Unsafe direct bracket reads `context.state["key"]` raising `KeyError` (`UNSAFE_CONTEXT_STATE_BRACKET_READ`).
   - Unsafe `.get("key").strip()` without fallback `""` raising `AttributeError` on `None` (`UNSAFE_NONE_METHOD_CALL`).
   - Unsafe chained `.get("key", "").get("subkey")` raising `AttributeError: 'str' object has no attribute 'get'` (`modify_paper_free_billing`).
   - Plaintext `print(request)` or `print(headers)` logging sensitive auth tokens/PII (`SENSITIVE_REQUEST_LOGGING`).
   - Hardcoded API keys (`apikey` / `x-soe-apikey` fallback literals) or invalid `"NOT FOUND"` default for `soedc` header.
   - Missing Google-style docstrings (`MISSING_DOCSTRING_[T002]`), missing return type annotations, or mismatched return type annotations (`RETURN_TYPE_ANNOTATION_MISMATCH`, e.g., `-> str` when returning a `dict`).

### Step 6: Python `return` Statement Audit & Actionable Recommendations (Step 7)
Inspect every `return` statement in `python_code.py` and generate concrete **Recommendations** for every detected finding:
1. **`return context.state` (CRITICAL P0 Anti-Pattern):** Never return `context.state` as a tool response (`addsim_byod`). It dumps the entire session history (`~15,745 chars`) back to the LLM.
2. **`return <raw_api_var>` (`RAW_API_RESPONSE_RETURNED_DIRECTLY`):** Never return the unpruned `.json()` object directly (`return gca` in `get_case_activations`). Prune and return only the conversational fields.
3. **`return json.dumps(...)` (`DOUBLE_SERIALIZATION_IN_RETURN`):** Always return a native Python `dict`; CXAS serializes it to JSON automatically.
4. **Bare `return` (`BARE_NONE_RETURN`):** Never use a bare `return` (`None`); return an explicit status/error dictionary.
5. **Mandatory Recommendations Section:** Every generated report (`tool_data_hygiene_report_latest.md`) must include concrete, actionable recommendations in both **Section 1 (Tracker Column D)** and **Section 7 (`Actionable Recommendations & Remediation Plan`)** covering Tool Response Hygiene, Instruction Variable Alignment, and Python Tool Code / Context Session fixes.

---

## 4. Mandatory 5-Filter Human-in-the-Loop (HITL) Governance Rules

To prevent over-engineering and irrelevant recommendations, **apply these 5 filters before finalizing any report**:

1. **Filter 1 — Exempt Tool Category Check:** Do NOT recommend schema pruning for `think`, FAQ/Datastore search tools, UI Widget tools, or static prompt-injection helper tools.
2. **Filter 2 — Cross-Agent Shared Variable Dependency Check:** Before recommending removal of a field or `context.set_variable(...)` key, verify whether a downstream transfer target (`Billing`, `Equipment_upgrade`, `Customer_identification`, `Payment`) reads that variable.
3. **Filter 3 — Compact Payload Over-Engineering Filter:** If a tool's `distinct_tool_response` is already compact (`<= 800` chars), flat (`<= 3` levels), and free of double-serialization or leaks (e.g., `get_addLine_createCart_byod`, `set_byod_compatibility_check`, `set_tradein_details`, `validate_device_id`), **explicitly mark Tool Response Data Hygiene as `"All good from tool response data hygiene point of view"`** and do NOT invent minor key-renaming tasks.
4. **Filter 4 — Distinguish Tool Response Hygiene vs. Python Code/Instruction Bugs:** Keep `distinct_tool_response` hygiene verdicts separate from `python_code.py` (`context.set_variable` / docstring) or `instruction.txt` findings so engineering teams know exactly which artifact needs modification.
5. **Filter 5 — Backend API Freeze vs. Python Wrapper Scope:** Never propose modifying upstream VZ SOE / OneBot backend microservices. All pruning, flattening, and type casting must be implemented inside the **CXAS Python Function Tool wrapper (`python_code.py`)** or `instruction.txt`.

---

## 5. Additional Reference Material
For real-world production case studies (`ONEBOT_BILLING_INIT`, `addsim_byod`, `submit_fee_waiver`, `get_case_activations`, `get_email`, `get_pin`, `manage_roles_by_tag`) and copy-pasteable Python DTO wrapper templates, read:
- [`references/sop_and_case_studies.md`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/references/sop_and_case_studies.md)
