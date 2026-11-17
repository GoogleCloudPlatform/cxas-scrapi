# Tool Data Hygiene SOP Reference: Production Case Studies & Python Refactoring Patterns

This reference document accompanies [`SKILL.md`](file:///usr/local/google/home/kamittt/Documents/New/VZCXAgent/.agents/skills/cxas-tool-data-hygiene/SKILL.md) and provides concrete production examples from the VZ CX Agent (`Dev_-_Profile_Chat_Agent`) across all 6 SOP steps.

---

## 1. Real Production Case Studies

### Case Study 1: `ONEBOT_BILLING_INIT` (`Billing` Role) — Extreme Payload Bloat, Double JSON Serialization & Instruction Leakage
- **Observed Telemetry:** ~12,500 to 21,000+ characters (~3,000–5,200 tokens) per invocation; 85+ keys per response; >40% empty/null fields; nesting depth up to 6 levels.
- **Key Findings:**
  1. **Double-Serialized JSON Strings:** Fields `billOverviewDetails`, `billIncreaseList`, and `billDetails` were returned as escaped JSON strings (`"{\"billCycleDate\":\"...\"}"`) inside the outer JSON object, forcing the LLM to mentally unescape nested strings.
  2. **UI Layout Pollution:** Contained `pageLayout`, `carousel`, `quickReplies`, and CSS/button structures meant for the web frontend rather than the LLM.
  3. **Instruction Leakage Inside Data Fields:** Included `nextBillText_instructions` containing raw LLM prompt instructions inside a backend data field.
  4. **Duplicate Entity Keys & Stringified Numerics:** Multiple MDN variations (`mtn`, `mdn`, `PhoneNumber`) and stringified dollar amounts (`"$47.41"`).
- **Remediation:** Parse escaped JSON strings via `json.loads()` inside the Python tool wrapper, strip UI layout/instruction fields, normalize numeric values to floats, and return a flat, 8–12 key conversational dictionary.

---

### Case Study 2: `addsim_byod` (`BYOD` Role) — Critical P0 `return context.state` Anti-Pattern
- **Observed Telemetry:** Average `15,745` characters (~3,936 tokens) per response; 288 keys per payload; max nesting depth of 9 levels.
- **Root Cause (`tools/addsim_byod/python_function/python_code.py`):**
  - Line 21: `context.set_variable("addsim_byod_response", response)` stored the raw unpruned API response into session memory.
  - Line 70: `return context.state` returned the **entire session state dictionary** (`context.state`, including `init_data`, `ALL_CASE_DATA`, `ALL_LINES_DATA`, `CUSTOMER_DATA`, and prior tool dumps) back to the LLM as the tool's output!
- **Remediation:**
  1. Replace `return context.state` on Line 70 with a targeted dictionary returning only the fields needed by `BYOD/instruction.txt`:
     ```python
     return {
         "status": "SUCCESS",
         "existingSimCard": response.get("data", {}).get("existingSimCard", ""),
         "iccId": response.get("data", {}).get("iccId", ""),
     }
     ```
  2. Prune `context.set_variable("addsim_byod_response", ...)` so only essential keys are stored in session memory.

---

### Case Study 3: `get_case_activations` (`Equipment_upgrade` Role) — Instruction Typo, Conditional Schema Mismatch & Raw API Return
- **Observed Telemetry:** Average `597` characters (~149 tokens), 20 keys (`accountNumber`, `applicationId`, `caseDetails`, `channelId`, `errorCode`, `errorMessage`, `flowType`, etc.).
- **Root Cause Across Steps 4, 5 & 6:**
  1. **Step 4 (Instruction Typo):** In `agents/Equipment_upgrade/instruction.txt` (Line 236), the prompt wrote:
     `Invoke the tool get_case with inputs...` instead of `{@TOOL: get_case_activations}`.
  2. **Step 4 (Conditional Variable Mismatch):** Immediately after calling the tool (Lines 238–244), `instruction.txt` checked:
     - `<if condition="flow == 'NakedPortIn'">`
     - `<if condition="statusCode == '00000'">`
     However, `get_case_activations` never returned `flow` or `statusCode` (it returned `flowType` and `errorCode`, and extracted `caseId`, `orderNumber`, `locationCode`, `applicationRefId`, `mtn` into `context.variables["case_data"]`).
  3. **Step 5 & 6 (Raw API Dump & Return):** In `tools/get_case_activations/python_function/python_code.py`, Line 121 stored the raw response `context.variables["get_case_activations_response"] = gca` and Line 155 executed `return gca`.
- **Remediation:**
  - Fix `instruction.txt` Line 236 to use `{@TOOL: get_case_activations}`.
  - Align `instruction.txt` conditional checks with the actual keys returned by `get_case_activations` (or explicitly return `flow` and `statusCode` from `python_code.py`).

---

### Case Study 4: `get_addLine_createCart_byod` (`BYOD` Role) — Clean Tool Response vs. Session Context Dump (HITL Filter #3 & #4 Example)
- **Observed Telemetry:** `distinct_tool_response` is `100%` clean: `{"data": {"newLineNumber": "1"}}` (29 chars, ~7 tokens, depth 2).
- **Root Cause (`tools/get_addLine_createCart_byod/python_function/python_code.py`):**
  - **Step 3 Verdict:** *"All good from tool response data hygiene point of view. No tool response data hygiene recommendation needed."*
  - **Step 5 Finding:** Line 23 executed `context.set_variable("get_addLine_createCart_byod_response", response)` (dumping the raw API response into session memory) and Line 25 immediately called `context.get_variable("get_addLine_createCart_byod_response")` even though `response` was already in local scope.
- **Key Lesson:** Keep Step 3 (Tool Response Hygiene) and Step 5 (Python Code / Context Memory Hygiene) cleanly separated so the team does not waste time redesigning an already-optimal 29-character response schema.

---

### Case Study 5: `submit_fee_waiver` (`Billing` Role) & `get_pin` (`Customer_identification` Role) — 100% Silent Empty `{"result": ""}` Responses
- **Observed Telemetry:** Every `toolResponse` row in BigQuery returned `{"result": ""}` (14 characters).
- **Root Cause:** When upstream API headers or pre-conditions failed, or when `python_code.py` caught an exception / fell through an `if/else` branch without an explicit structured error dict, the tool returned an empty string `""`.
- **Impact:** The LLM received `{"result": ""}` with zero indication of whether the fee waiver succeeded or failed, causing hallucinated confirmations.
- **Remediation:** Always return an explicit status contract:
  ```python
  return {
      "status": "SUCCESS" if is_valid else "ERROR",
      "errorCode": error_code or None,
      "message": user_safe_summary,
  }
  ```

---

### Case Study 6: `get_email` (`Customer_identification` Role) — Premature Session Context Memory Wipe
- **Root Cause (`tools/get_email/python_function/python_code.py`):** Executed `context.set_variable("enteredEmail", {})` at the start of the function, wiping the customer's previously captured email address before validation completed.
- **Remediation:** Only clear or overwrite session context variables after validation succeeds or when explicitly resetting state.

---

### Case Study 7: `manage_roles_by_tag` (`Account_management` Role) — String `"NaN"` Literals & Redundant Role Lists
- **Observed Telemetry:** Returned `"NaN"` string literals (from upstream pandas/JSON serialization) and repeated role arrays across multiple nested keys.
- **Remediation:** Sanitize `"NaN"`, `"null"`, and `"undefined"` string literals to Python `None` (or omit empty keys) inside the Python tool wrapper before returning.

---

## 2. Standard Python Tool Wrapper (DTO) Refactoring Pattern

When a tool fails Step 3 (Response Hygiene), Step 5 (Context Memory Hygiene), or Step 6 (`return` Statement Hygiene), recommend refactoring `python_code.py` using this standardized Data Transfer Object (DTO) pattern:

```python
from typing import Any, Dict
import json


def clean_tool_wrapper(param_a: str, param_b: str = "") -> Dict[str, Any]:
    """Executes <tool_name> and returns a pruned, LLM-ready conversational payload.

    Args:
        param_a: Primary identifier required by the backend service.
        param_b: Optional secondary filter parameter.

    Returns:
        A flat dictionary containing `status` and only the fields required by
        the sub-agent's instruction.txt.
    """
    try:
        raw_resp = tools.some_openapi_endpoint(param_a=param_a, param_b=param_b)
        payload = raw_resp.json() if hasattr(raw_resp, "json") else (raw_resp or {})
    except Exception as exc:
        return {
            "status": "ERROR",
            "errorCode": "TOOL_EXECUTION_FAILED",
            "errorMessage": f"Unable to retrieve data: { type(exc).__name__ }",
        }

    data = payload.get("data") or {}
    # If a nested field is double-serialized JSON, parse it safely:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}

    # Build a pruned, flat dictionary containing ONLY fields referenced by instruction.txt
    clean_result = {
        "status": "SUCCESS" if payload.get("statusCode") in ("00000", "200", 200) else "ERROR",
        "statusCode": str(payload.get("statusCode", "")),
        "orderNumber": data.get("orderNumber") or None,
        "lineNumber": data.get("newLineNumber") or None,
    }

    # Save ONLY the pruned dictionary (never the raw API payload) into session memory
    context.set_variable("clean_tool_data", clean_result)

    # Return the native Python dict directly (never `return context.state` or `return json.dumps(...)`)
    return clean_result
```
