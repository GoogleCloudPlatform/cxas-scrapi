# Tool Design & Conversational Pacing

This reference defines tool docstring contracts, conversational pacing
directives, payload contamination prevention, and execution standards for the
dialogue reasoning model in the Gemini Composite V1 architecture.

--------------------------------------------------------------------------------

## 1. Tool Docstring Engineering

In Gemini Composite V1, tool docstrings serve as explicit prompt instructions
for the reasoning model. Because the model operates at low thinking effort
to minimize conversational latency, docstrings must provide unambiguous
execution contracts for all tools that are visible to the model. Only tools configured in the `agent_name.json` will be visible to the model. And as such will be provided in the prompt to the model.

### Source of Truth & Preservation Rules for Tool Docstrings:
- **Preserve Existing Documentation:** Never wipe or replace existing function descriptions, parameter explanations, or return signatures. Additive enhancement only.
- **Python Tools (`tools/<name>/python_function/python_code.py`):** The Python function docstring inside `python_code.py` is the **exclusive canonical source of truth** in CXAS/CES. Always audit and update docstrings directly in the Python source code rather than mutating the `.json` configuration file.
- **Non-Python Tools:** The tool description in `<name>.json` (`openApiTool.description` or `clientFunction.description`) is the source of truth.

### Mandatory Tool Docstring Sections:

1.  **Summary & Capabilities**: Clear description of what the tool executes.
2.  **Execution Guidelines (When to Call & When NOT to Call)**: Explicit
    operational boundaries to prevent ungrounded or hallucinated tool
    invocations.
3.  **Conversational Pacing Directive (for Latency-Sensitive Tools)**: Prompt
    instructing the model to speak a brief spoken phrase before tool execution.
4.  **Parameter Specifications**: Types, valid values, and default behaviors.
5.  **Context Argument Synthesis Rules**: How to rewrite brief caller fragments
    into complete semantic statements.

### Example in `tools/manage_service_appointment/python_function/python_code.py`:

```python
def manage_service_appointment(
    action_code: str,
    date_requested: str,
    slot_id: str | None = None,
    user_intent_summary: str | None = None,
) -> dict[str, Any]:
  """Checks availability, schedules, and modifies service appointments.

  Handles actions:
  - 'CHECK_SLOTS': Retrieves available time windows for the requested date.
  - 'BOOK_SLOT': Confirms and reserves a selected appointment window upon user consent.
  - 'CANCEL_SLOT': Cancels an existing reserved appointment.

  When NOT to call:
  - Do NOT call when the user is asking general informational questions about service types.
  - Do NOT call 'BOOK_SLOT' before the user explicitly selects and confirms a specific time window.

  Before calling this tool, speak a brief, natural conversational pacing phrase using conversational speech texture (e.g., 'Let me check the available time slots for you.' or 'Reserving that time window now.').

  Args:
    action_code: Action to perform ('CHECK_SLOTS', 'BOOK_SLOT', 'CANCEL_SLOT').
    date_requested: Target appointment date (YYYY-MM-DD).
    slot_id: Selected time slot identifier (required for 'BOOK_SLOT').
    user_intent_summary: Full synthesized summary of the user's explicit request.

  Returns:
    Dict containing execution status and appointment details.
  """
  ...
```

--------------------------------------------------------------------------------

## 2. Spoken Conversational Pacing Phrases & Selective Application

In a composite voice architecture, tool execution introduces an unavoidable
processing delay before the model generates the tool result and streams text
to the TTS engine. Without a pacing phrase on long-running tools, the caller
experiences dead air.

### Selective Application by Tool Category:

Apply engineering judgment to distinguish tools that need pacing phrases from fast local tools:

| Tool Category | Examples | Pacing Phrase Needed? | Rationale |
| :--- | :--- | :---: | :--- |
| **Remote Lookups & Search** | `search_faq`, `get_available_slots`, `lookup_account` | **YES** | Remote API / database queries take 300ms–2s; prevents dead air. |
| **State Mutations & Transactions** | `book_appointment`, `cancel_slot`, `modify_order` | **YES** | Backend transactions take time to commit; keeps caller engaged. |
| **Outbound Dispatch & Notifications** | `send_tracker_link`, `dispatch_callback` | **YES** | External notification dispatch latency. |
| **Diagnostics & Trouble Trees** | `process_trouble_tree`, `run_diagnostic` | **YES** | Heavy multi-step reasoning / backend queries. |
| **Session Wrap-up & Termination** | `exit_conversation`, `call_wrap_up`, `end_call` | **NO (Exempt)** | Saying "One moment while I check..." before saying goodbye sounds robotic and unnatural. |
| **Intent Classification & Routing** | `classify_user_intent`, `triage_route` | **NO (Exempt)** | Fast, synchronous internal routing checks. |
| **Local State Getters / Setters** | `get_agent_context`, `set_variable` | **NO (Exempt)** | In-memory session context access. |
| **Test & Mock Tools** | `mock_transfer`, `test_call_lifecycle` | **NO (Exempt)** | Local test fixtures. |

### Pacing Rules:

-   **Mandatory Directive for Latency Tools**: Tool docstrings must explicitly direct the model to
    emit a brief, natural conversational bridge phrase *before* invoking the
    function call (e.g., *"One moment while I look that up for you..."*, *"Let
    me check the schedule for that day..."*).
-   **No Premature Claims**: The model must never predict the tool outcome or
    quote result values in the pacing phrase before the tool has returned its
    payload.

--------------------------------------------------------------------------------

## 3. Foreign Tool Payload Contamination Protection

Backend databases, knowledge bases, and API integrations frequently return raw
JSON payloads, English error strings, or system identifiers (e.g., `{"status":
"Out of stock", "category": "Equipment"}`).

### Vulnerability:

Without explicit boundary instructions, LLMs often quote or parrot raw tool
output strings verbatim into non-English or specialized sessions, causing sudden
language flips or broken persona tone.

### Mitigation:

Instruct the agent to formulate search queries in the API's required format, but
synthesize all spoken customer responses strictly in the session language
(`{{user_language}}`):

```xml
<taskflow>
    <step name="ExecuteLookup">
        <action>
            1. Formulate search parameters in the backend's expected query format.
            2. Invoke {@TOOL: lookup_tool} with structured query parameters.
            3. Synthesize the spoken customer response strictly in {{user_language}} using natural conversational voice texture. Do not quote raw backend metadata verbatim.
        </action>
    </step>
</taskflow>
```

--------------------------------------------------------------------------------

## 4. Declared Tool Synchronization & Pre-Classification Ordering

1.  **Tool Declaration Synchronization**: Every `{@TOOL: tool_name}` referenced
    in instruction prompts MUST be declared in the agent's configuration `.json`
    under `"tools": ["tool_name"]`. Undeclared tools throw fatal
    `ToolNotFoundError` exceptions at runtime.
2.  **Pre-Classification Terminology Resolution**: When supporting localized
    product names, regional plan tiers, or specialized acronyms, invoke
    terminology resolution tools *before* intent classification or
    knowledge-base search to prevent misclassification.
3.  **Elimination of Deprecated Language-Switching Tools**: Deprecate dynamic
    language-switching tools (`language_switcher`, `en_to_es`). Session language
    is established at IVR/session initialization; dynamic tools add latency and risk hallucination.
