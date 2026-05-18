# The Slot Filling DAG Framework

A design pattern for building deterministic, LLM-powered conversational agents that collect structured data from users, execute backend operations, and handle failures — all without relying on the LLM to make control-flow decisions.

---

## 1. The Problem

Conversational agents that collect structured information (booking a flight, filing a claim, placing an order) face a tension between two needs:

1. **Natural conversation.** Users speak freely. They give three pieces of information in one sentence, go off-topic, correct themselves, and say "actually, make it 6 people" in the middle of a confirmation. The LLM handles this beautifully.

2. **Deterministic control flow.** The business requires that certain fields are collected before an API is called, that failed API calls are retried exactly N times, that the user is escalated to a human after exhausting retries, and that no field is silently dropped. The LLM handles this terribly.

The Slot Filling DAG Framework solves this by splitting the problem: the LLM owns *language* (parsing user intent, generating natural responses, calling setter tools), while a deterministic Python callback owns *control flow* (what to ask next, when to fire a task, how many retries remain, when to escalate).

The LLM never decides "should I call the booking API now?" or "have I collected enough information?" — it doesn't even see the tools for actions that aren't valid yet.

---

## 2. Core Concepts

### 2.1 Slots

A **slot** is a named piece of data the conversation needs to collect or derive. Each slot has a **source**:

- `"user"` — collected from the user via a setter tool (e.g., the user says "4 people" and the LLM calls `set_reservation_basics(party_size=4)`)
- `"task:TaskName"` — populated automatically when a backend task succeeds (e.g., `available_times` is filled when `FindAvailableTimes` returns)
- `"event"` — pre-filled from external signals (telephony data, web forms, CRM lookups) injected as session variables. Requires `"event_key"` (the key in `event_data`). Pre-filled slots are written directly to `sm["filled"]` by `before_model_callback`, so the framework skips asking for them. See Section 12.1.
- `"announce"` — framework-controlled slot that delivers text (greeting, disclosure, status message) and fills a completion bit (`True`). No setter, no readback. See Section 12.3.

`source` can be a **single string** or an **ordered list** of sources. When a list is given, sources are evaluated in priority order. For example, `"source": ["event", "user"]` means: try event data first; if no event data is present, fall back to asking the user via the setter tool. The framework normalizes single strings to a one-element list internally via `_normalize_sources()`.

Slots are declared as an ordered list. The order defines the default question sequence — the framework asks for the first unfilled slot whose dependencies are met.

```python
"slots": [
    {"name": "num_guests",    "source": "user",  "setter": "set_num_guests",
     "ask": "How many guests?", "requires_readback": True},

    {"name": "date",          "source": "user",  "setter": "set_date",
     "ask": "What date works for you?", "requires_readback": True},

    {"name": "open_slots",    "source": "task:CheckAvailability"},
    #  ^ no "ask" — this slot is filled by a task, not by the user

    {"name": "chosen_time",   "source": "user",  "setter": "set_chosen_time",
     "requires": ["open_slots"],
     "ask": "We have {open_slots}. Which time?", "requires_readback": True},
    #  ^ requires: this question is blocked until open_slots is filled

    {"name": "name",          "source": "user",  "setter": "set_name",
     "ask": "What name for the reservation?", "requires_readback": True},

    {"name": "confirmation",  "source": "task:MakeReservation"},
]
```

Key properties:

| Field | Purpose |
|---|---|
| `name` | Unique identifier. Also the key in `filled`/`pending` dicts. |
| `source` | A string or ordered list of strings. Values: `"user"`, `"task:TaskName"`, `"event"`, `"announce"`. A list like `["event", "user"]` means: try event first, fall back to user. Single strings are normalized to `[source]` internally. |
| `event_key` | Key in `event_data` to read from (event-sourced slots only). Defaults to the slot name. |
| `setter` | Tool name the LLM calls to set this slot (user-sourced slots only). |
| `ask` | Prompt template shown when this is the next slot to fill. Supports `{slot_name}` placeholders resolved from already-filled slots. |
| `requires` | List of slot names that must be filled before this slot's question is eligible. Enforces ordering constraints (e.g., can't ask "which time?" until availability is known). |
| `requires_readback` | If `True`, values stay in `pending` for user confirmation before moving to `filled`. If `False` or absent, the framework auto-promotes to `filled` immediately. |
| `readback_fmt` | Format spec for readback text. Can be: a built-in name (`"date"`, `"time"`), a dict spec (`{"type": "plural", "one": "guest", "other": "guests"}`), or a lambda string (`"lambda v: f'custom {v}'"`). Compiled to a callable once at startup by `_compile_config()`. Built-in dict types: `prefix` (prepend text), `plural` (count + singular/plural noun), `none_sub` (replace none-like values). |
| `condition` | Optional lambda string (e.g., `"lambda filled: int(filled.get('party_size', 0)) >= 5"`) compiled to a callable once at startup. When present, the slot is only active if the condition returns `True`. Inactive slots are skipped and don't block tasks. See Section 4.4. |
| `hint` | Short description shown in the `<slot_filling_protocol>` block. Used with `setter` to generate the tool-selection guide the LLM sees (e.g., `"Party size / number of users" → set_reservation_basics`). |
| `setter_field` | For multi-slot setters only. Identifies which field of the multi-slot tool maps to this slot. When present, the tool returns `{"values": {"field_name": val}, "field_errors": {"field_name": "code"}}` instead of `{"value": val}`. The `after_tool_callback` routes each field independently to its mapped slot. |
| `validate_against` | Optional cross-slot validation spec: `{"response_field": "display_value", "filled_slot": "available_times", "error_code": "not_available"}`. The `after_tool_callback` checks that the setter's response field matches one of the comma-separated options in the referenced filled slot; on mismatch it signals a `_slot_errors` entry. See Section 6a. |
| `validation` | Optional dict with `errors` (code → message mapping), `max_retries`, and `on_exhaust` config. See Section 5b. |

### 2.2 Tasks

A **task** is a backend operation that fires when all its input slots are filled. Tasks are the "edges" in the DAG that connect user-provided data to system-derived data.

```python
"tasks": [
    {"name": "CheckAvailability",
     "tool": "check_availability",
     "inputs": ["num_guests", "date"],
     "outputs": {"open_slots": "open_slots"},
     "success_check": "success",
     "then_say": "We have {open_slots}. Which time works?",
     "on_failure": {
         "retry_say": "No availability for that date. Try another?",
         "max_retries": 1,
         "clear_slots": ["date"],
         "on_exhaust": {"say": "Please call us at 555-0100.", "then": "escalate"}
     }},

    {"name": "MakeReservation",
     "tool": "make_reservation",
     "inputs": ["num_guests", "date", "chosen_time", "name"],
     "outputs": {"confirmation": "confirmation"},
     "success_check": "success",
     "terminal": True,
     "then_say": "Confirmed! Number: {confirmation}.",
     "on_failure": {
         "retry_say": "Let me try once more.",
         "max_retries": 2,
         "on_exhaust": {"say": "Please call us directly.", "then": "escalate"}
     }},
]
```

Key properties:

| Field | Purpose |
|---|---|
| `tool` | CES tool name the framework calls to execute this task (e.g., `"check_availability"`). |
| `inputs` | Slot names that must all be in `filled` before this task can fire. These are passed as arguments to the tool. |
| `requires` | Slot names that must be filled before the task fires, but are NOT passed as arguments. Separates "gates" from "data." See Section 12.4. |
| `outputs` | `{result_key: slot_name}` — maps keys from the task's return dict into filled slots. This is how task-sourced slots get populated. The framework validates that all declared output keys are present in the result; missing keys are treated as a task failure (see Section 3.2). |
| `success_check` | Key in the result dict that must be truthy for the task to count as successful. |
| `terminal` | If `True`, successful completion ends the conversation (sets `status = "complete"`). |
| `readback_inputs` | Defer slot readback to grouped confirmation (7a). |
| `then_say` | Message template shown on success. Supports `{slot_name}` placeholders. |
| `condition` | Optional lambda string compiled to a callable. When present, the task only fires if the condition returns `True`. Inactive tasks are skipped during DAG evaluation. Same compilation as slot conditions. |
| `on_failure` | Retry and escalation configuration (see Section 5). |

### 2.3 The DAG

The slots and tasks implicitly define a **directed acyclic graph**:

```
[num_guests] ──┐
               ├──▶ CheckAvailability ──▶ [open_slots]
[date] ────────┘                              │
                                              ▼
[chosen_time] ─┐              (requires: open_slots)
               │
[name] ────────┤
               │
[num_guests] ──┤
               ├──▶ MakeReservation ──▶ [confirmation]
[date] ────────┤
               │
[chosen_time] ─┤
               │
[open_slots]* ─┘
               (* open_slots is not an input to MakeReservation in this example,
                  but chosen_time requires it, so it's transitively required)
```

The framework walks this DAG on every callback invocation, deciding what to do next based on the current state of `filled`, `pending`, and `task_results`.

### 2.4 State

All state lives in a session-scoped dict called `sm`
(slot manager), declared in `app.json` as a variable
with a rich default:

```python
sm = callback_context.state.get("sm", {})
```

CES persists declared variables across turns. The `sm`
variable is declared in `app.json`'s
`variableDeclarations` with a default that includes
pre-populated `_tool_selection`, `_slot_ordering`, and
`_prereq_note` so the LLM has correct tool routing
hints from the very first turn.

The `sm` dict contains:

```python
sm = {
    "filled":         {},    # confirmed values: {"num_guests": 4, "date": "2026-07-15"}
    "pending":        {},    # values awaiting user confirmation (readback)
    "deferred":       {},    # values held for grouped readback (see Section 7a)
    "task_results":   {},    # successful task outputs: {"CheckAvailability": {...}}
    "_retries":       {},    # failure counts: {"CheckAvailability": 1, "slot:num_guests": 2, "readback": 1}
    "_slot_errors":   [],    # validation errors from setter tools: [{"slot": "...", "code": "..."}]
    "_last_state":    {},    # filled/pending/deferred snapshot
    "_steer_back_turns": 0,  # user turns since last forward progress — drives 3-tier steer-back
    "_system_message": "",   # next message for the LLM to relay (written by the callback adapter)
    "_debug_log":     [],    # structured event log (opt-in via debug flag) — see Section 19.4
    "status":         "in_progress",  # "in_progress" | "complete" | "escalated"
}
```

The `_retries` dict uses a triple-namespace key scheme:

- **Task retries**: bare task names (`"CheckAvailability"`) — tracks backend operation failures.
- **Slot validation retries**: `"slot:"` prefix (`"slot:num_guests"`) — tracks invalid user input for a specific slot.
- **Readback retries**: literal key `"readback"` — reserved for future use.

No collision between the three namespaces.

---

## 3. The Callback Lifecycle

The framework is structured as five components split across CES tools and callbacks:

1. **`{config_id}_dag` tool** — per-agent: each agent has its own parameterless DAG tool (e.g., `bella_notte_dag`, `billing_dag`). Returns pure serializable data (no callables): slots, tasks (with `tool` keys), format specs, conditions.
2. **`slot_filling_engine` tool** — the CES-agnostic orchestrator. Takes `{raw_config, sm, last_user_text, event_data}`, validates/compiles config on first call, runs `_run_slot_filling()`, and returns `{"action": {...}, "sm": {...}}`. The `action` dict contains: `hide_tools`, `preempt`, `force_preempt`, `message`, `function_call` (for task fires and auto-confirm), `si_suffix` (system instruction hints), and `inline_confirmed`. Never touches CES types.
3. **`before_model_callback`** — CES adapter and SI assembler. Calls `tools.{config_id}_dag()` (via `getattr`) and `tools.slot_filling_engine()`, applies tool visibility via `llm_request.config.hide_tool()`, assembles a phase-specific system instruction suffix (`_build_phase_suffix`) that includes only the protocol block relevant to the current phase (collection OR readback, never both), injects it via a sentinel-based replacement (`_inject_phase_suffix`), and optionally preempts via `LlmResponse.from_parts()` with text and/or `function_call` parts.
4. **`before_agent_callback`** — runs once per user turn. Initializes `sm`, resolves `config_id`, and processes deferred rejections (`_rejection_snapshot`). Does NOT set prompt variables — SI assembly is handled entirely by `before_model_callback`.
5. **`after_tool_callback`** — routes setter and executor tool results into `sm` state. Reads config-derived mappings from `sm` (`_setter_slots`, `_slot_requires`, `_slot_validates`, `_executor_tasks`) to determine how to handle each tool's response. See Section 6a.

CES calls the callback before EACH model invocation, including after tool results within the same turn. This re-invocation is what lets the framework see state changes from setter tools and react immediately (e.g., fire a DAG task as soon as its inputs are filled).

Here is the full lifecycle of a single turn:

```
User message arrives
        │
        ▼
┌──────────────────────┐
│ before_agent_callback │  (once per turn)
│  • Initialize sm      │
│  • Resolve config_id  │
│  • Process deferred   │
│    rejections         │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────────┐
│ before_model_callback     │  (before EACH model call)
│  • Hide {config_id}_dag,  │
│    slot_filling_engine    │
│  • Terminal state? → OK   │
│  • tools.{config_id}_dag()│
│  • tools.slot_filling_    │
│    engine()               │
│  • Apply hide_tools       │
│  • Build phase suffix     │
│    (collection OR readback│
│     — never both)         │
│  • Inject via sentinel    │
│  • Preempt? → LlmResponse │
│    (text + function_call) │
└────────┬─────────────────┘
         │
    ┌────▼──────────────────────┐
    │ Preempt and               │──yes──▶ return LlmResponse
    │ (contents > 1 or          │         (text and/or function_call)
    │  force_preempt)?          │
    └────┬──────────────────────┘
         │ no
         ▼
    LLM generates (guided by instruction + system_directive)
         │
         ▼
    LLM calls setter/readback tool(s)
         │
         ▼
┌──────────────────────────┐
│ after_tool_callback       │  (after EACH tool call)
│  • Setter → write pending │
│    or _slot_errors        │
│  • Executor → write       │
│    task_results, filled,  │
│    _task_just_completed   │
└────────┬─────────────────┘
         │
         ▼
    before_model_callback re-invokes
    (CES calls it again after tool results)
```

Inside `slot_filling_engine`, the orchestrator `_run_slot_filling()` runs this pipeline:

```
┌─────────────────────┐
│ State change detect  │  Reset stall counters on progress
├─────────────────────┤
│ Auto-promote/route   │  Non-readback → filled; deferred routing
├─────────────────────┤
│ Auto-deactivate      │  Clear slots whose condition is False
├─────────────────────┤
│ Auto-confirm check   │  Pure affirmative? → function_call(confirm_pending)
├─────────────────────┤
│ Inline-confirm       │  Affirmative + new content? → silent confirm
├─────────────────────┤
│ Tool visibility      │  Compute hide_tools
├─────────────────────┤
│ Slot errors          │  Check _slot_errors from setter validation
├─────────────────────┤
│ Steer-back           │  3-tier recovery: soft SI → hard preempt → escalate
├─────────────────────┤
│ Post-executor        │  Process _task_just_completed results
├─────────────────────┤
│ Announce cascade     │  Fire consecutive announce slots
├─────────────────────┤
│ DAG evaluation       │  Decide: fire / readback / ask next
├─────────────────────┤
│ Build SI suffix      │  readback_scope / deferred_collection hints
└─────────────────────┘
```

### 3.0a Gate-Slot Context Preservation

When a config specifies a `gate_slot`, `before_model_callback` returns early (skipping the engine) until that slot is filled. Three mechanisms improve multi-slot extraction during the gate phase:

1. **Tool selection hints**: The gate early return builds tool hints from the config's slot definitions (`hint → setter`) and includes them in the collection block, so the LLM knows which setter to call for each piece of information.

2. **User text capture**: During the gate early return, the callback saves the user's original message to `callback_context.state["_gate_user_text"]`. On the first post-gate engine invocation, this text is injected as a `<user_context>` block in the SI suffix, reminding the LLM to extract slot values from the original message rather than re-asking.

3. **Forward-only `last_user_text`**: The general `last_user_text` extraction only checks `contents[-1]` (not a backward scan), so post-tool re-invocations correctly get empty `last_user_text` — this prevents the steer-back counter from double-incrementing within a single user turn. The backward scan is used ONLY for the `_gate_user_text` capture in the gate early return.

### 3.1 compute_dag_state() — The Pure Decision Function

This is the heart of the framework. It takes the current state as arguments and returns an action dict. It **never mutates state**. Its logic is a strict priority cascade:

1. **A task's inputs (and `requires`) are all filled and it hasn't succeeded yet?** → `fire`. Walk tasks in declaration order, return the first one ready. Task firing is checked BEFORE readback so that unrelated pending items (e.g., promoted from a deferred group) don't block a task whose inputs are all in `filled`.
2. **Pending slots exist?** → `awaiting_readback`. The user must confirm or reject before anything else happens.
3. **Otherwise** → `next_question` or `announce`. Walk slots in declaration order via `_find_next_slot_action`, return the first eligible announce slot (framework-controlled) or user-sourced slot that is unfilled and whose `requires` are met.

Because it's pure, it's trivially testable: pass in state dicts, assert on the returned action.

### 3.2 Task Execution via `function_call`

When the DAG evaluates to a `fire` action, the engine does
NOT call the executor directly. Instead, it returns a
`function_call` instruction to the CES adapter:

```python
return {
    "hide_tools": fire_hide,
    "preempt": True,
    "force_preempt": any_announce_preempt,
    "function_call": {"name": tool_name, "args": args},
    "message": combined_msg,
}
```

The CES adapter builds a `LlmResponse` with both the
message text and a `Part.from_function_call()`. CES then
executes the tool, and the `after_tool_callback` processes
the result:

1. **Executor success**: `after_tool_callback` writes the
   result to `sm["task_results"]`, maps output keys to
   `sm["filled"]` (validating all declared output keys are
   present), and sets `sm["_task_just_completed"]`.
2. **Executor failure**: `after_tool_callback` still writes
   to `task_results` and sets `_task_just_completed`. The
   engine's `_handle_post_executor` handles retry/exhaust
   logic on the next `before_model_callback` invocation.

On the next callback cycle, `_handle_post_executor` checks
`_task_just_completed` and applies the result:

- **Success**: clears task retries, extracts `then_say`
  message, marks terminal tasks complete. If non-terminal,
  the DAG re-evaluates and may fire another task via a
  second `function_call` return.
- **Failure**: increments `_retries`, optionally clears
  slots via `on_failure.clear_slots`, checks exhaustion,
  returns the appropriate message.

This indirect execution model (engine → function_call →
CES tool → after_tool_callback → next engine invocation)
means cascading happens across callback invocations rather
than in a single Python loop. The separation between
`compute_dag_state` (pure) and the execution path
(effectful) is preserved — the decision logic remains
testable with plain dicts.

### 3.3 Cascading

When a non-terminal task succeeds, it may fill slots that
are inputs to the next question or even another task. The
cascading behavior differs by action type:

**Announce cascading (within a single invocation):**
Consecutive announce slots cascade in a tight `while` loop
within `_run_slot_filling`. Each announce slot is filled
(`filled[name] = True`), the DAG re-evaluates, and if the
next action is also an announce, the loop continues. All
messages are concatenated. This means a welcome greeting
followed by the first question delivers both in one
response.

**Task cascading (across invocations):** When
`_handle_post_executor` processes a successful non-terminal
task, the engine continues to `_compute_dag_state`. If
another task is now ready, a `function_call` is returned.
CES executes the tool, `after_tool_callback` processes the
result, and the next `before_model_callback` handles the
second task's result. Cascading therefore happens across
callback invocations, not in a single Python loop — but
the user still experiences it as seamless because no user
input is needed between cascaded tasks.

Example cascade:

1. User provides `num_guests` and `date`.
2. DAG evaluator sees `CheckAvailability` has all inputs → fires it.
3. `CheckAvailability` succeeds → fills `open_slots` → DAG re-evaluates and cascades.
4. Cascaded DAG evaluation: `open_slots` is now filled, so `chosen_time` (which `requires: ["open_slots"]`) becomes the next question.
5. The system message becomes "We have 6 PM, 7:30 PM. Which time?" — all in one callback invocation, no extra round-trip.

### 3.4 Config Validation

The callback adapter calls `_validate_config(config)` before passing the config to the orchestrator. This surfaces configuration errors as early failures rather than runtime mysteries. The function raises `ValueError` with a descriptive message if any check fails:

1. **No duplicate slot names** — every name in slots must be unique.
2. **Task inputs reference valid slots** — every name in a task's `inputs` list exists in slots.
3. **Task outputs reference valid slots** — every `slot_name` in a task's `outputs` values exists in slots.
4. **Task-sourced slots have matching tasks** — a slot with `source: "task:X"` has a task named `X` in tasks.
5. **Tasks have tool keys** — every task has a `"tool"` key specifying the CES tool to call.
6. **Task requires reference valid slots** — every name in a task's `requires` list exists in slots.
7. **Requires reference valid slots** — every name in a slot's `requires` list exists in slots.
8. **No circular requires** — the `requires` graph is acyclic (detects cycles via DFS).
9. **Conditions are valid** — slot and task `condition` fields must be callable or lambda strings if present.
10. **Announce slots have message** — slots with `source: "announce"` must have a `message` field and must NOT have a `setter`.

---

## 4. Tool Visibility

A critical design principle: **the LLM should only see tools that are valid right now**. This is not just for efficiency — it prevents entire classes of bugs where the LLM calls the wrong tool at the wrong time.

The framework enforces tool visibility rules on every callback invocation:

### 4.1 During Readback (pending slots exist)

When the user has provided values that are awaiting confirmation:

| Tool | Visible? | Why |
|---|---|---|
| `confirm_pending` | Yes | User can accept the readback |
| `reject_pending` | Yes | User can reject the readback |
| Setters for slots IN pending | Yes | User may inline-correct ("No, make it 6 people") |
| Setters for unfilled, dependency-met slots NOT in pending | Yes | Can capture info volunteered during readback |
| Setters for slots NOT in pending whose `requires` are unmet | **Hidden** | Dependencies not satisfied |

### 4.2 Outside Readback (no pending slots)

| Tool | Visible? | Why |
|---|---|---|
| `confirm_pending` | **Hidden** | Nothing to confirm |
| `reject_pending` | **Hidden** | Nothing to reject |
| Setters for filled slots | **Hidden** | Re-setting would corrupt state |
| Setters whose `requires` are unmet | **Hidden** | Can't set `chosen_time` before availability is known |
| Setters for inactive (condition=False) slots | **Hidden** | Slot not relevant in current state |
| Executor tools (`find_available_times`, etc.) | **Hidden** | Tasks fire via the engine's `function_call`, not the LLM |
| All other setters (unfilled, active, dependencies met) | Yes | User can provide any eligible info in any order |

### 4.3 Fresh Pending Detection

When a setter tool just created pending values (the prior invocation had no pending, this one does), the framework also hides `confirm_pending` and `reject_pending`. This forces the LLM to read back the values to the user before confirming. Without this, the LLM sometimes calls `reject_pending` → setter → `confirm_pending` all in one turn, skipping the readback step entirely.

Additionally, during fresh pending the **setter for each slot already in `pending`** is also hidden. This prevents the LLM from re-calling the same setter during the readback phase (e.g., `set_guest_info` right after `set_guest_info` just ran), which would otherwise push the pending state from "fresh" to "awaiting_confirmation" and allow `confirm_pending` to fire on the next invocation — skipping the readback entirely.

### 4.4 Conditional Slots

Slots can have a `condition` — a lambda string compiled to a callable at startup — that determines whether the slot is active. Inactive slots are automatically hidden (setter not visible), skipped by the question-finding logic, and don't block tasks that list them as inputs.

```python
{"name": "contact_phone", "source": "user", "setter": "set_phone",
 "condition": "lambda filled: int(filled.get('num_guests', 0)) >= 5",
 "ask": "For large parties, we need a contact number."}
```

The framework also handles **auto-deactivation**: if a slot was previously filled but its condition becomes `False` (e.g., user corrects party size from 6 to 3), the framework removes the value from `filled`/`pending` so the DAG doesn't wait for it.

### 4.5 Multi-Slot Setters

A **multi-slot setter** is a single tool that maps to multiple slots. For example, `set_reservation_basics` fills both `party_size` and `preferred_date`, with each parameter optional. This reduces the number of tools the LLM sees and improves call accuracy.

**Config**: Each slot has a `setter_field` that names its parameter within the multi-slot tool:

```python
{"name": "party_size",    "setter": "set_reservation_basics", "setter_field": "party_size", ...}
{"name": "preferred_date","setter": "set_reservation_basics", "setter_field": "preferred_date", ...}
```

**Return format**: Multi-slot setters return `{"stored": True, "values": {...}, "field_errors": {...}}` instead of `{"stored": True, "value": ...}`. Each field is routed independently — valid fields go to `pending` even if other fields have errors.

**Tool hiding**: A multi-slot setter stays visible as long as ANY of its mapped slots still needs filling. It's only hidden when ALL mapped slots are filled, inactive, or pending (during fresh readback).

**State**: The `_multi_setter_slots` mapping (`tool_name → {field_name: slot_name}`) is built during config loading alongside `_setter_slots`.

### 4.6 Why Tool Visibility Matters

Without tool visibility, the LLM might:

- Call `set_chosen_time("7 PM")` before availability is known (the user asked for 7 PM proactively).
- Call `confirm_pending()` when nothing is pending (the user said "yes" to an unrelated question).
- Re-call `set_num_guests(4)` after it's already filled, corrupting state.

Tool visibility makes these errors *structurally impossible*. The LLM literally cannot call a tool it cannot see.

Visibility is scoped **per-turn** — it's applied on each callback invocation based on the current state, with no persistence needed. If the state changes (e.g., user confirms readback → pending becomes empty), the next callback invocation will compute fresh visibility rules.

---

## 5. The Retry State Machine

Tasks can fail. The framework provides a deterministic retry mechanism configured entirely in the task definition — no LLM involvement in deciding whether to retry or when to escalate.

### 5.1 Retry Configuration

```python
"on_failure": {
    "retry_say": "No availability for that date. Try another?",
    "max_retries": 1,
    "clear_slots": ["date"],
    "on_exhaust": {
        "say": "Please call us at 555-0100.",
        "then": "escalate"
    }
}
```

The `then` field is a general-purpose tool call. It accepts either a string (tool name with no args) or a dict with `tool` and `args`:

```python
# String shorthand — calls tool with empty args:
"then": "escalate"

# Dict form — calls tool with arguments:
"then": {
    "tool": "transfer_to_agent",
    "args": {
        "reason": "retry_exhausted",
        "guest_name": "{guest_name}",   # {slot} placeholders
        "party_size": "{party_size}",   # filled from slot values
    }
}
```

Arg values can contain `{slot_name}` placeholders that are resolved from filled slots at exhaust time.

### 5.2 Two Retry Modes

**Re-collect retry** (task has `clear_slots`):

When the task fails, the framework clears the specified slots from `filled`. On the next callback pass, those slots are missing, so the DAG asks the user for them again. Once the user provides new values, the task re-fires with updated inputs.

Use case: "No availability for that date" → clear `date` → ask user for a new date → re-check availability.

```
Task fires → fails
  → increment _retries["CheckAvailability"] (now 1)
  → clear filled["date"]
  → show retry_say: "No availability. Try another date?"
  → user says "How about Saturday?"
  → LLM calls set_date("2026-07-19")
  → next callback: DAG sees CheckAvailability inputs ready → fires again
  → if it fails again and retries >= max_retries → exhaust
```

**Same-input retry** (task has no `clear_slots`):

The task is simply retried with the same inputs on the next callback pass. The `retry_say` is shown to the user as a status update, and the user's next message (even just "okay") triggers the retry.

Use case: transient API failure → "Let me try once more." → user says "sure" → re-fire with same inputs.

```
Task fires → fails
  → increment _retries["MakeReservation"] (now 1)
  → show retry_say: "Let me try once more."
  → user says "okay"
  → next callback: DAG sees MakeReservation inputs still ready,
    task hasn't succeeded → fires again
  → if it fails again → check retries < max_retries → retry or exhaust
```

### 5.3 Exhaustion

When `_retries[task_name] >= max_retries`, the framework:

1. Shows the `on_exhaust.say` message ("Please call us at 555-0100.").
2. Resolves `on_exhaust.then` to a tool call (string or dict form) and includes it as a `function_call` in the preempted response. Sets `sm["status"] = "escalated"`.
3. On the next callback, the terminal state guard catches `status == "escalated"` and lets the LLM respond freely (no more slot collection).

### 5.4 Retry State Tracking

Retry counts live in `sm["_retries"]`, a dict with triple-namespace keys:

- **Task retries**: `{"CheckAvailability": 1}` — bare task name.
- **Slot validation retries**: `{"slot:num_guests": 2}` — `"slot:"` prefix prevents collision with task names.
- **Readback retries**: `{"readback": 1}` — reserved for future use.

This field:

- Starts empty (`{}`) — zero overhead in the happy path.
- Only gets populated when a task or slot validation actually fails.
- Is cleared for a task when it succeeds (`retries.pop(task_name, None)` in `_handle_post_executor`), or for a slot when a state change is detected (`retries.pop("slot:name", None)` in `_handle_state_change` when a new slot appears in `filled` or `pending`).
- Persists across turns (it's in the session state), so the framework remembers failure counts even across many user messages.

---

## 5b. Slot Validation and the `_slot_errors` Signal

Setter tools can reject invalid input (e.g., party size of 15 when the limit is 8). The framework centralizes error messaging and retry tracking — setter tools only signal *what* went wrong, not *what to say about it*.

### 5b.1 Slot Validation Config

Each slot can declare a `validation` block in its config:

```python
{
    "name": "num_guests",
    "source": "user",
    "setter": "set_num_guests",
    "ask": "How many guests?",
    "validation": {
        "max_retries": 3,
        "errors": {
            "out_of_range": "We accept parties of 1 to 8. For larger groups, contact events@example.com.",
            "parse_error": "I didn't catch the number of guests. How many will be dining?",
            "past_date": "That date is in the past. Could you provide a future date?",
        },
        "on_exhaust": {
            "say": "I'm having trouble with the party size. Please call us at 555-0100.",
            "then": "escalate",
        },
    },
}
```

### 5b.2 The `_slot_errors` Signal

When a setter tool detects invalid input, it returns `{"error": True, "error_code": "..."}`. The `after_tool_callback` reads this and appends the error to `sm["_slot_errors"]`:

```python
def set_num_guests(count: int) -> dict:
    if not (1 <= count <= 8):
        return {"error": True, "error_code": "out_of_range"}
    return {"stored": True, "value": count}
```

The setter does not access `sm` — all state routing is handled by `after_tool_callback` (Section 6a).

### 5b.3 `_handle_slot_errors()` — The Orchestrator

The callback's `_handle_slot_errors()` function processes these signals before DAG evaluation. Unlike early versions that only processed the first error, it now processes **all** errors from a single turn and joins their messages:

1. **Pop** `sm["_slot_errors"]` (clears after reading).
2. **For each error**:
   a. **Increment** `sm["_retries"]["slot:<name>"]`.
   b. **Check exhaustion**: if retries >= `max_retries`, use `on_exhaust.say` and resolve `on_exhaust.then` to a tool call (string or dict form). Short-circuits immediately on any exhaustion.
   c. **Look up** the error message from the slot's `validation.errors` config using the error code.
   d. **Resolve placeholders**: messages support `{slot_name}` placeholders (e.g., `"We have {available_times}. Which works?"`) resolved from `sm["filled"]` via `.format(**filled)`. Unknown placeholders are left as-is (protected by `KeyError` catch).
3. **Join** all resolved messages with a space.
4. **Return** the combined message (the engine writes it to `sm["_next_directive"]` and the callback adapter delivers it via preemption).
5. **Preempt** the LLM with the error message (same pattern as task fire preemption).

### 5b.4 Why Not Let Tools Return Messages Directly?

The original design had setters return `{"error": True, "_system_message": "..."}`. This worked but scattered error messaging across N tools. Centralizing in the slot config:

- Makes error messages visible in one place (the config table, not buried in tool code).
- Enables retry tracking and escalation for slot validation (previously only tasks had this).
- Ensures deterministic delivery via preemption (the LLM never sees the error, never improvises).

---

## 6. The Setter Tool Pattern

Setter tools are the bridge between the LLM's natural language understanding and the framework's structured state. Each user-sourced slot has a corresponding setter tool.

### 6.1 Anatomy of a Setter

Setters are **pure validation functions**. They do not access `sm` or write to session state — they validate input and return a result dict. The `after_tool_callback` (Section 6a) reads the result and manages all state routing.

```python
import datetime

def set_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format.

    Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD.
    """
    # 1. Validate format via strptime
    date = str(date).strip()
    try:
        parsed = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        return {"error": True, "error_code": "invalid_format"}

    # 2. Validate semantics — reject past dates
    if parsed < datetime.date.today():
        return {"error": True, "error_code": "past_date"}

    # 3. Return success (after_tool_callback writes to pending)
    return {"stored": True, "value": date}
```

### 6.2 Design Principles

**Setters are pure validators.** They validate input and return a result dict. They contain zero state management, zero DAG logic, zero control flow, and zero knowledge of other slots. All state routing is handled by the `after_tool_callback` (Section 6a).

**Setters return structured results.** On success: `{"stored": True, "value": <value>}`. On failure: `{"error": True, "error_code": "<code>"}`. The `after_tool_callback` reads these results and writes to the appropriate `sm` dicts (`pending` on success, `_slot_errors` on failure).

**Setters never access `sm`.** Unlike earlier versions where setters wrote directly to `sm["pending"]` and cleared retry counts, the current architecture has setters return data and the `after_tool_callback` handle all state mutations. This makes setters trivially testable and ensures state management is centralized.

**The LLM does the parsing.** The setter for `date` expects YYYY-MM-DD — the LLM converts "next Thursday" to "2026-07-17" before calling the tool. This plays to the LLM's strength (language understanding) while keeping the setter deterministic (string validation).

**Multi-slot messages work naturally.** If the user says "4 people, next Friday," the LLM calls both `set_num_guests(4)` and `set_date("2026-07-18")` in the same response. The `after_tool_callback` routes both results to `pending`. The next `before_model_callback` invocation sees both slots and proceeds accordingly (possibly firing a task if both were the last missing inputs).

---

## 6a. The `after_tool_callback` — State Routing

The `after_tool_callback` is the bridge between setter/executor tool results and the `sm` state machine. It runs after EACH tool call and routes results into the appropriate state dicts. It reads config-derived mappings from `sm` (populated by `slot_filling_engine` on its first invocation):

- `_setter_slots`: `{tool_name: slot_name}` — maps setter tools to their slot.
- `_slot_requires`: `{slot_name: [required_slot_names]}` — prerequisite validation.
- `_slot_validates`: `{slot_name: {response_field, filled_slot, error_code}}` — cross-slot validation.
- `_executor_tasks`: `{tool_name: {task_name, outputs, success_check, terminal}}` — maps executor tools to task metadata.

### 6a.1 Setter Result Routing

When a setter tool is called:

1. **Error result** (`{"error": True, "error_code": "..."}`) — appends `{"slot": slot_name, "code": error_code}` to `sm["_slot_errors"]`. The engine's `_handle_slot_errors` processes these on the next invocation.

2. **Success result** (`{"stored": True, "value": ...}`) — the callback performs two additional checks before writing to `pending`:
   - **Prerequisite check**: If the slot has `requires` in the config, verify all required slots are in `filled`. If not, signal a `"prereq_not_met"` error via `_slot_errors` instead of writing to pending.
   - **Cross-slot validation** (`validate_against`): If configured, check that the setter's `response_field` matches one of the comma-separated options in the referenced `filled_slot`. If not, signal the configured `error_code` via `_slot_errors`.

3. **Write to pending**: If both checks pass, write `pending[slot_name] = value`.

4. **Process inferred slots**: If the tool result includes an `"inferred"` dict (e.g., `{"inferred": {"service_type": "Internet"}}`), write each inferred slot to `pending` if it's not already in `filled`. This enables one setter to auto-fill related slots — for example, `set_issue_description("modem not turning on")` can infer `service_type = "Internet"` from the device keyword. The inferred values go through readback like any other pending value.

### 6a.2 Executor Result Routing

When an executor tool (e.g., `find_available_times`, `book_reservation`) is called:

1. Write the full response to `sm["task_results"][task_name]`.
2. If the response's `success_check` key is truthy AND all declared `outputs` keys are present, map output values to `sm["filled"]`.
3. Set `sm["_task_just_completed"] = task_name` so the engine's `_handle_post_executor` can process the result.

### 6a.3 Why Centralize State Routing?

This design means setter tools are pure validators — they don't need to know about `sm`, prerequisites, cross-slot validation, or the deferred/pending/filled lifecycle. All state logic is in one place (the callback), making it easy to add new validation modes without touching individual setter tools.

---

## 7. Readback and Confirmation

Readback is the pattern where the agent repeats collected values back to the user for confirmation before proceeding. This is especially important for high-stakes operations (financial transactions, medical records, legal agreements) where a misheard or misunderstood value could have consequences.

### 7.1 How It Works

1. **Setter writes to `pending`** (all setters always write to pending). Slots with `requires_readback: True` in the config stay there; slots without it are auto-promoted to `filled` by the framework before DAG evaluation.
2. **Callback invocation**: `compute_dag_state` sees non-empty `pending` → returns `awaiting_readback`.
3. **Readback message**: `_build_readback()` formats each pending value using its `readback_fmt` and joins them: *"Just to confirm — 4 guests, on July 15th. Is that correct?"*
4. **User confirms**: LLM calls `confirm_pending()` → moves all pending values to `filled` → next callback fires the DAG forward.
5. **User rejects**: LLM calls `reject_pending()` → sets `_rejection_requested` and `_rejection_snapshot` flags. The actual rejection is deferred to the next `before_agent_callback` invocation, which pops the snapshot and removes the rejected slots from `pending`. This deferred pattern ensures the rejection is processed at the start of a fresh turn.
6. **User inline-corrects**: User says *"No, make it 6 people"* → LLM calls `set_num_guests(6)` (setter is visible for pending slots) → overwrites the value in pending → next callback shows updated readback.

### 7.2 Batched Readback

Multiple slots can be confirmed together. If the user provides `num_guests` and `date` in the same message and both have readback configured, both land in `pending`, and the readback shows both:

*"Just to confirm — 6 guests, on July 15th. Is that correct?"*

The user confirms once, and both move to `filled` together.

### 7.3 Auto-Confirm: Deterministic Confirmation Short-Circuit

Relying on the LLM to call `confirm_pending` when the user says "yes" introduces flakiness — the LLM sometimes misidentifies the intent, loops back into readback, or re-reads values instead of confirming. For **short, pure affirmatives** the framework bypasses the LLM entirely.

**How it works:**

When the callback fires at the start of a user turn (i.e., the user just sent a message, not a post-tool re-invocation) and the current phase is `awaiting_confirmation`, the callback checks whether the user's message is a pure affirmative using `_is_affirmative()`:

```python
_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "correct", "right",
    "sure", "sounds good", "ok", "okay", "perfect", "great",
    "exactly", "confirmed", "absolutely", "that's right",
    "that's correct", "looks right", ...
})
_CORRECTION_SIGNALS = frozenset({
    "but", "actually", "wait", "change", "not", "no",
    "wrong", "instead", "different", ...
})
_STRIP_PUNCT = str.maketrans("", "", ".,;:!?\"'")

def _is_affirmative(text: str) -> bool:
    normalized = text.lower().strip().rstrip(".,!? ")
    if normalized in _AFFIRMATIVES:
        return True
    # Strip punctuation from each word so "Yes, that's correct"
    # matches the same as "Yes that's correct".
    words = [w.translate(_STRIP_PUNCT) for w in normalized.split()]
    if len(words) <= 5 and words and words[0] in _AFFIRMATIVES:
        return not any(w in _CORRECTION_SIGNALS for w in words[1:])
    return False
```

If `_is_affirmative` returns `True`, the engine's `_try_auto_confirm` returns a preemptive result:

1. Sets `sm["_auto_confirm_pending"] = True` (prevents the upcoming state change from resetting the steer-back counter).
2. Returns a `function_call` for `confirm_pending` — the CES adapter builds a `LlmResponse` with `Part.from_function_call(name="confirm_pending", args={})`. CES then executes `confirm_pending`, which merges `pending → filled` and sets `_readback_transition = True`.
4. The next `before_model_callback` invocation sees the state change from `confirm_pending` and processes the transition (next question or task fire).

The auto-confirm gate **only fires when the phase is `awaiting_confirmation`** (pending slots exist from a prior turn) **and** `last_user_text` is non-empty. Post-tool re-invocations (where the last content is a function response, not a user message) never trigger auto-confirm, preventing double-firing within the same turn.

**Correction detection:** Messages like "Yes, but make it 6" are NOT auto-confirmed because "but" is a correction signal. Messages like "Yes, that's correct" ARE auto-confirmed because punctuation is stripped from words before comparison ("that's" → "thats", no correction signals). Only messages where `len(words) <= 5` are considered (longer messages are left to the LLM).

**Why 5 words?** Short affirmatives are unambiguous. Longer messages may contain corrections, clarifications, or new information that the LLM should process. Capping at 5 words bounds the false-positive risk.

### 7.3a Inline Confirm: Confirmation with Additional Content

When a user's message **starts** with an affirmative but contains additional content — e.g., "Yea, also my wife has a shellfish allergy" — neither auto-confirm (which skips the LLM entirely) nor normal LLM processing (which might ignore the implicit confirmation) is correct. The **inline confirm** path handles this case.

**Detection (`_starts_affirmative`):**

Unlike `_is_affirmative` which requires the entire message to be a pure affirmative, `_starts_affirmative` only checks that the **first word** is an affirmative and no correction signals appear in the first few words. It accepts messages of any length.

```python
def _starts_affirmative(text: str) -> bool:
    words = [w.translate(_STRIP_PUNCT) for w in normalized.split()]
    if not words or words[0] not in _AFFIRMATIVES:
        return False
    return not any(w in _CORRECTION_SIGNALS for w in words[1:4])
```

**How it works:**

1. `_try_auto_confirm` detects `_starts_affirmative` and sets `sm["_inline_confirm"] = True`, then returns `None` (no preemption — the engine continues normally).
2. Later in `_run_slot_filling`, `_apply_inline_confirm` consumes the flag:
   - Moves all `pending → filled` (the implicit confirmation).
   - Resets phase to `"collection"` so the LLM gets setter instructions.
   - Returns `inline_confirmed = True`.
3. The DAG evaluates and sees a `"fire"` action (task prerequisites are met), but the **task fire is deferred** when `inline_confirmed` is `True` — the LLM needs to run first to call setters for the new content.
4. The `before_model_callback` sees `inline_confirmed` and assembles a collection-phase SI suffix via `_build_phase_suffix`. The sentinel-based injection (`_inject_phase_suffix`) replaces the previous readback suffix with the collection protocol, so the LLM gets collection instructions instead of readback.
5. The LLM runs with collection instructions, calls the appropriate setter(s), and the task fires on the next callback cycle.

**Why not just use auto-confirm?** Auto-confirm preempts with a `confirm_pending` tool call, which skips the LLM entirely. The new content in the user's message ("shellfish allergy") would be lost — no setter would be called for it.

**Why not let the LLM handle it?** Without inline confirm, the LLM sees readback instructions and may re-read values instead of processing the new content. The framework must explicitly switch to collection mode.

### 7.4 Readback Transition Preemption

After the user confirms a readback — either via `confirm_pending` (tool call) or auto-confirm (callback short-circuit) — the framework preempts the LLM with the next question rather than relying on the LLM to generate it. This prevents a class of failures where the LLM, confused by complex multi-turn conversation context (especially after reject → re-ask → confirm cycles), fails to produce a valid response.

**How it works:**

1. `confirm_pending` or auto-confirm sets `sm["_readback_transition"] = True`.
2. The callback pops this flag before DAG evaluation.
3. After the DAG computes the next action, if `_readback_transition` was true and the action is `next_question`, the callback preempts with `"{prefix} {next_question}"` (prefix chosen randomly from `confirm_transition_prefix` list in config, e.g. `["Wonderful!", "Perfect!", "Great!"]`).
4. If the DAG action is `fire` instead (e.g., confirming the date triggers FindAvailableTimes), the existing task fire preemption handles it — the readback transition flag is consumed but the task preemption takes precedence.

The random prefix list ensures preempted post-confirmation responses still sound warm and varied, passing persona evaluation checks. `reject_pending` does **not** set this flag — after rejections, the LLM generates its own response naturally (e.g., "Okay, my apologies! How many people will be in your party?"), which preserves conversational warmth.

### 7.5 Readback and Tool Visibility Interaction

During readback (non-fresh), tool visibility ensures appropriate actions are available:

- `confirm_pending` and `reject_pending` are visible (the expected actions).
- Setters for slots that ARE in `pending` are visible (inline correction).
- Setters for unfilled, active, dependency-met slots NOT in pending are also visible (can capture info volunteered during readback — see Section 4.1).
- Setters for filled, inactive, or dependency-blocked slots are hidden.
- Executor tools are hidden (tasks fire via the engine, not the LLM).

During fresh readback, `confirm_pending`/`reject_pending` AND the setters for slots in pending are additionally hidden (see Section 4.3).

### 7.6 Steer-Back (Unified Stall Recovery)

A 3-tier mechanism that progressively recovers conversations making no forward progress — whether the user is off-topic, confused, or the LLM missed a tool call.

**The problem:** Conversations stall for many reasons (off-topic chat, vague answers, missed tool calls, stuck readback). A single threshold that escalates immediately is too blunt — most stalls recover with a nudge.

**How it works — 3 tiers:**

| Tier | Trigger | Action | LLM runs? |
|------|---------|--------|-----------|
| Soft | `soft_after` turns (default 2) | Inject `<steer_back>` directive in SI | Yes — LLM rephrases |
| Hard | `hard_after` turns (default 4) | Preempt with the next question | No — engine generates |
| Escalate | `escalate_after` turns (default 6) | End session + escalation message | No — preempted |

1. On each callback invocation where `last_user_text` is non-empty, increment `sm["_steer_back_turns"]`. Post-tool and post-preemption callbacks (where `last_user_text` is empty) do **not** increment — only genuine user turns count.

2. **Tier 1 (soft):** When `_steer_back_turns >= soft_after`, the engine returns a `steer_back_directive` string (e.g. `"The conversation has drifted — steer back. Ask for the date."`). The `before_model_callback` appends this as a `<steer_back>` tag in the SI suffix. The LLM still runs and can incorporate the guidance naturally.

3. **Tier 2 (hard):** When `_steer_back_turns >= hard_after`, the engine preempts — if pending, it re-asks the readback confirmation; otherwise it preempts with the next question from `_find_next_question`. No LLM call. **Yield:** After preempting, the engine sets `_hard_steer_yielded = True`. On the *next* user turn, this flag is popped and the engine returns `None` without incrementing the counter — the LLM runs normally so it can process the user's response to the forced question. If the user provides valid info, the state change resets the counter and recovery succeeds. If not, the counter advances on the turn after the yield.

4. **Tier 3 (escalate):** When `_steer_back_turns >= escalate_after`, the engine fires `on_exhaust` — preempts with the escalation message and optional `function_call` (e.g. `end_session`). Sets `sm["status"] = "escalated"`.

**Config:**

```python
"steer_back": {
    "soft_after": 2,
    "hard_after": 4,
    "escalate_after": 6,
    "on_exhaust": {
        "say": "I'm having trouble completing your reservation. "
               "Please call us at 555-0100 and we'll help you directly.",
        "then": {
            "tool": "end_session",
            "args": {"reason": "retry_exhausted", "session_escalated": True},
        },
    },
}
```

**Counter resets (any forward progress resets to 0):**

The framework compares `filled`, `pending`, and `deferred`
against a snapshot from the prior invocation (`_last_state`).
Any change (new slot filled, new value pending, pending
cleared by confirmation) resets `_steer_back_turns` to 0.
This means setters, `confirm_pending`, `reject_pending`, and
task execution all implicitly reset the counter — any state
change counts as progress. Exception: when
`_auto_confirm_pending` is set (auto-confirm just fired), the
state change from `confirm_pending` does NOT reset the
counter — auto-confirm already incremented it, and resetting
would lose that increment.

**Why this replaces readback stall + progress stall:** The old
system had two independent detectors — readback stall (3
cycles with non-empty pending) that destructively rejected
values, and progress stall (N turns with no state change)
that immediately escalated. Both were blunt: readback stall
threw away pending data, and progress stall had no recovery
attempt. The unified steer-back tries softer interventions
first (SI injection, then preemption) before escalating,
recovering most stalls without data loss or session
termination.

---

## 7a. Deferred Readback (Grouped Confirmation)

Standard readback (Section 7) confirms each slot
immediately after it's set. This is correct for slots like
`party_size` or `date` that gate downstream tasks -- the
user should confirm before the system acts on them. But for
slots that are all inputs to the same terminal task (e.g.,
`guest_name` and `special_requests` are both inputs to
`BookReservation`), confirming each one individually creates
unnecessary friction:

```
Agent: "Under the name Chen. Correct?"  <- readback
User:  "Yes"
Agent: "Any special requests?"
User:  "No"
Agent: "No special requests. Correct?"  <- readback
User:  "Yes"
```

With deferred readback, the framework collects these values
silently and confirms them together in a single grouped
readback:

```
Agent: "What name for the reservation?"
User:  "Chen"
Agent: "Any special requests?"                      <- name silently deferred
User:  "No"
Agent: "Let me confirm -- name Chen, no special     <- grouped readback
        requests. Is that correct?"
User:  "Yes"
```

### 7a.1 How It Works

Deferred readback is enabled per-task via the
`readback_inputs: True` flag on a task definition:

```python
{
    "name": "BookReservation",
    "inputs": ["party_size", "date", "time", "guest_name", "special_requests"],
    "readback_inputs": True,   # enables deferred readback
    "terminal": True,
    ...
}
```

When `readback_inputs` is `True`, user-sourced input slots
for that task follow a different lifecycle:

1. **Setter writes to `pending`** (all setters always write
   to pending -- this is unchanged).
2. **Auto-promote + route**: During
   `_auto_promote_and_route`, the framework checks if the
   slot is **deferred-eligible** (see Section 7a.2). If so,
   the value is moved from `pending` to `deferred` instead
   of staying in `pending` for immediate readback.
3. **No readback**: Because the value is no longer in
   `pending`, the LLM doesn't receive a readback prompt for
   it. The framework injects a `<deferred_collection>` hint
   into the system instruction telling the LLM to proceed
   to the next question instead of reading back.
4. **Group completion**: When all user-sourced active inputs
   for the task are accounted for (in `filled`, `pending`,
   or `deferred`), the deferred values promote from
   `deferred` to `pending` for a single grouped readback.
5. **Grouped readback**: The LLM receives a
   `<readback_scope>` hint listing ALL promoted values with
   their slot labels, presented in a stronger format ("You
   MUST confirm ALL of the following values together").

### 7a.2 Deferred Eligibility

A slot is deferred-eligible when ALL four conditions hold
(`_compute_deferred_eligible`):

1. **`requires_readback: True`** -- only readback slots can
   be deferred (non-readback slots auto-promote to `filled`
   directly).
2. **Input to at least one task with
   `readback_inputs: True`** -- the slot must belong to a
   task that uses grouped confirmation.
3. **NOT input to any incomplete task WITHOUT
   `readback_inputs`** -- if the slot also feeds a task that
   needs immediate confirmation (e.g.,
   `FindAvailableTimes`), it must be confirmed immediately
   so that task can fire.
4. **No task-sourced `requires`** -- if the slot depends on
   a task output (e.g., `selected_time` requires
   `available_times`), it cannot be deferred because it's
   gated by the task execution and should be confirmed in
   the normal flow after the task fires.

**Example**: In the Bella Notte config:

- `party_size` and `preferred_date` are inputs to
  `FindAvailableTimes` (no `readback_inputs`) AND
  `BookReservation` (`readback_inputs: True`). Condition 3
  fails -- they are NOT deferred, because
  `FindAvailableTimes` needs their confirmed values to fire.
- `selected_time` requires `available_times`
  (task-sourced). Condition 4 fails -- NOT deferred.
- `guest_name` and `special_requests` are inputs ONLY to
  `BookReservation` (which has `readback_inputs: True`),
  have no task-sourced requires. All conditions pass --
  they ARE deferred.

### 7a.3 Group Completion

`_check_deferred_groups` runs on every callback invocation
(as part of `_auto_promote_and_route`). For each task with
`readback_inputs: True`:

1. Walk the task's `inputs` list, considering only
   user-sourced active slots.
2. Skip slots already in `filled` or `pending` (these are
   accounted for).
3. Check if remaining slots are in `deferred`.
4. If ALL remaining user-sourced inputs are accounted for,
   the group is **complete**. Move all deferred values for
   this group from `deferred` to `pending`.

**When does a group complete?** The last deferred-eligible
input arriving triggers completion. In the Bella Notte
example, `guest_name` and `special_requests` are the only
deferred slots. When the user provides the second one (e.g.,
special requests after the name),
`_check_deferred_groups` sees both are in `deferred` and
`party_size`, `date`, `time` are all in `filled` -- the
group is complete -- both promote to `pending`.

### 7a.4 System Instruction Hints

The framework communicates deferred readback state to the
LLM via two XML-tagged hints injected into
`system_instruction`:

**`<deferred_collection>`** -- Injected when values were
just routed to deferred (`fresh_deferred` is true) and no
pending values exist:

```
<deferred_collection>
The value(s) just collected (guest_name) are noted and will be confirmed
later together with related information. Do NOT read them back or ask for
confirmation now. Instead, proceed to ask: Do you have any special requests
or dietary needs?
</deferred_collection>
```

This prevents the LLM from reading back a value that the
framework deliberately deferred. Without it, the LLM would
see that it just called `set_guest_info` and naturally want
to confirm the name.

**`<readback_scope>`** -- Injected when values promoted from
deferred are now in `pending` (`promoted_from_deferred` is
true). Uses a stronger format with slot labels:

```
<readback_scope>
You MUST confirm ALL of the following values together in a single
readback -- do NOT omit any:
  - guest_name: under the name Chen
  - special_requests: no special requests
</readback_scope>
```

The stronger wording ("MUST", "do NOT omit") and labeled
format ensure the LLM reads back ALL deferred values, not
just the most recent one. Without this reinforcement, the
LLM tends to read back only the last value it set (e.g.,
just the special requests) and silently drop the earlier
deferred values (e.g., the name).

**Hint lifecycle**: The `before_model_callback` uses a
sentinel-based approach (`_FRAMEWORK_SENTINEL =
"<!-- slot-framework -->"`) to cleanly replace the
previous framework suffix on each invocation.
`_inject_phase_suffix` finds the sentinel in the system
instruction, strips everything after it, and appends the
new phase-specific content. This is critical because
`system_instruction` is the same object across
`before_model_callback` invocations within a turn. Without
replacing, a `<deferred_collection>` from an earlier
invocation (saying "don't read back") would persist
alongside a later `<readback_scope>` (saying "MUST read
back") -- contradictory instructions that confuse the LLM.

### 7a.5 State Flow Diagram

```
                     Setter writes to pending
                              │
                              ▼
                    ┌─────────────────┐
                    │ Deferred-       │
              no    │ eligible?       │   yes
           ┌────── │ (Section 7a.2)  │ ──────┐
           │        └─────────────────┘        │
           ▼                                   ▼
     stays in pending                 moves to deferred
     (normal readback)                (<deferred_collection> hint)
           │                                   │
           ▼                                   ▼
     LLM reads back                   ┌─────────────────┐
     immediately                      │ Group complete?  │
                                no    │ (Section 7a.3)  │   yes
                             ┌─────── └─────────────────┘ ──────┐
                             │                                   │
                             ▼                                   ▼
                       stays in deferred               promotes to pending
                       (collect next slot)             (<readback_scope> hint)
                                                               │
                                                               ▼
                                                      grouped readback
                                                      (user confirms all)
```

### 7a.6 Interaction with Other Features

**Tool visibility**: Deferred values do not trigger the
`awaiting_readback` phase -- `compute_dag_state` only checks
`pending`, not `deferred`. The LLM continues to see setters
for the next unfilled slot. `confirm_pending` and
`reject_pending` remain hidden until values actually reach
`pending` via group promotion.

**Auto-confirm**: Works normally on grouped readbacks. When
the user says "yes" to confirm promoted deferred values,
auto-confirm merges all pending values to `filled`.

**Conditional slots**: `_deactivate_conditional_slots` also
clears deferred values when a slot's condition becomes
`False`. Auto-deactivation handles all three dicts
(`filled`, `pending`, `deferred`).

**`_find_next_question`**: The question-finder skips slots
that are in `deferred` (in addition to `filled` and
`pending`), so the framework doesn't re-ask for a value
that's already been collected but is waiting for group
completion.

**Fresh pending detection**: When deferred values promote to
`pending`, this counts as `fresh_pending`, which triggers
the normal readback flow (hide
`confirm_pending`/`reject_pending` for one cycle to force
the LLM to read back before confirming).

### 7a.7 Worked Example: Deferred Readback

Using the Bella Notte reservation agent:

```
Turn 1-4: Collect party_size, date, time (normal flow with readback)
  filled = {party_size: 4, preferred_date: "2026-06-17",
            available_times: "...", selected_time: "20:30"}

Turn 5: User provides name
  LLM calls: set_guest_info(guest_name="Chen")
  pending = {guest_name: "Chen"}
  _auto_promote_and_route:
    guest_name is deferred-eligible → deferred = {guest_name: "Chen"}
    _check_deferred_groups: special_requests not yet collected → not complete
  Result: no pending, fresh_deferred=True
  <deferred_collection> hint: "guest_name noted,
    proceed to ask special requests"
  LLM: "Any special requests or dietary needs?"

Turn 6: User says "No"
  LLM calls: set_guest_info(special_requests="None")
  pending = {special_requests: "None"}
  _auto_promote_and_route:
    special_requests is deferred-eligible
      → deferred = {guest_name: "Chen",
                     special_requests: "None"}
    _check_deferred_groups: all BookReservation inputs accounted for → COMPLETE
    → pending = {guest_name: "Chen", special_requests: "None"}
    → deferred = {}
  promoted_from_deferred = True, fresh_pending = True
  <readback_scope> hint: "MUST confirm ALL:
    - guest_name: under the name Chen
    - special_requests: no special requests"
  LLM: "Let me confirm -- reservation under Chen, no special requests.
        Is that correct?"

Turn 7: User says "Yes"
  auto-confirm: pending → filled
  BookReservation fires → confirmation number returned
  Preempt: "Your reservation is confirmed! Number: BN-..."
```

---

## 8. LLM Preemption

Sometimes the framework can generate the complete response
without the LLM. When a task fires and produces a message
(via `then_say` or `retry_say`), the framework can
**preempt** the LLM call entirely -- returning a pre-built
response and skipping the model generation.

### 8.1 Why Preempt?

- **Latency**: skipping a model call saves 1-3 seconds.
- **Determinism**: the message comes from the task config, not from LLM generation, so it's exactly what was intended.
- **Cost**: no tokens consumed for a response that was already known.

### 8.2 Preemption Triggers

The framework preempts the LLM in these situations:

1. **Task fire**: The DAG evaluates a task as ready. The engine returns a `function_call` with the executor tool name and args. The CES adapter builds a `LlmResponse` with both a text message (from `then_say`) and a `Part.from_function_call()`.
2. **Auto-confirm**: The user's message is a pure affirmative during readback. The engine returns a `function_call` for `confirm_pending` with `force_preempt: True`.
3. **Slot validation error**: A setter tool signaled an error via `_slot_errors`, and `_handle_slot_errors()` resolved the message from the slot's `validation.errors` config. The error message is delivered verbatim.
4. **Readback transition**: `confirm_pending` (via LLM call or auto-confirm) just committed values, and the DAG's next action is `next_question`. The message is delivered with a randomly chosen prefix from `confirm_transition_prefix` (e.g. `"Wonderful!"`, `"Perfect!"`, `"Great!"`) for warmth.
5. **Steer-back (hard)**: The steer-back counter has reached `hard_after` turns with no forward progress. The engine preempts with the next question (or readback re-ask) via `force_preempt: True`.
6. **Steer-back (escalate)**: The steer-back counter has reached `escalate_after` turns. The escalation message is delivered verbatim with an optional `function_call` (e.g. `end_session`).
8. **Announce slots** with `preempt: True`: Delivered verbatim via `force_preempt`. Announce slots with `preempt: False` use `_system_message` guidance instead.

All use `LlmResponse.from_parts()` to skip the model call. Preemption fires when `preempt` is True AND either `contents > 1` (not the first turn) OR `force_preempt` is True (bypasses the first-turn guard).

### 8.3 When Not to Preempt

- **First turn**: the LLM should generate a natural greeting, not a canned "How many guests?" (checked via `len(llm_request.contents) > 1`).
- **After reject_pending**: the LLM handles rejections naturally, producing warm responses like "Okay, my apologies! How many people will be in your party?"
- **Setter success**: after a setter writes to pending, the readback prompt is set as `_system_message` and the LLM wraps it in natural language.
- **Steer-back (soft)**: when the steer-back counter reaches `soft_after`, the engine does NOT preempt — it injects a `<steer_back>` directive in the SI suffix and lets the LLM run. The LLM can incorporate the guidance naturally. Only tiers 2 (hard) and 3 (escalate) preempt.

### 8.4 The Directive Contract

The orchestrator (`_run_slot_filling`) returns the message in its result dict. The `before_model_callback` wraps it in a `<system_directive>` XML tag as part of the phase-specific SI suffix assembled by `_build_phase_suffix`.

The LLM sees directives appended to the end of the instruction via sentinel-based injection:

```
<system_directive>
We have availability at 6:00 PM, 7:30 PM, 9:00 PM. Which time works best?
</system_directive>
```

The `<slot_filling_protocol>` block tells the LLM to "relay it to the user naturally — include exact values or confirmation numbers." This gives the LLM freedom to add personality while ensuring the framework's content is delivered.

The engine also returns an `si_suffix` field containing `<readback_scope>` and `<deferred_collection>` XML tags. The `_build_phase_suffix` function includes these in the appropriate phase suffix, and `_inject_phase_suffix` replaces any previous framework suffix via the sentinel marker. This ensures readback hints are always current and don't accumulate.

---

## 8a. Rich Response Payloads

By default, preempted messages are delivered as plain text via
`Part.from_text()`. Rich response payloads extend preemption to
support structured UI content — buttons, cards, deep links,
SSML audio, session control — delivered as typed response parts
that map directly to Dialogflow CX's `ResponseMessage` model.

### Response Part Types

Each response part has a `type` field that maps to a CES
`Part.from_*()` factory:

| `type` | Maps to | Dialogflow CX equivalent |
|--------|---------|--------------------------|
| `"text"` | `Part.from_text(text)` | `ResponseMessage.text` |
| `"payload"` | `Part.from_json(data)` | `ResponseMessage.payload` |
| `"audio"` | `Part.from_audio(uri, ...)` | `ResponseMessage.play_audio` |
| `"end_session"` | `Part.from_end_session(...)` | `ResponseMessage.end_interaction` |
| `"transfer"` | `Part.from_agent_transfer(...)` | `ResponseMessage.live_agent_handoff` |

### Declaring Responses in the DAG Config

Any message-producing location in the config can define a
`response` list — an ordered sequence of response parts. A
single string field (`ask`, `message`, `then_say`) is shorthand
for `[{"type": "text", "text": "..."}]`. When both a string
field and `response` are present, `response` takes priority for
preempted output; the string is still used for system
instruction directives and logging.

**User slot with buttons:**
```python
{
    "name": "seating_preference",
    "source": "user",
    "setter": "set_seating_preference",
    "ask": "Indoor, outdoor, or bar seating?",
    "response": [
        {"type": "payload", "data": {
            "messageType": "static",
            "scenarios": [{
                "name": "StaticResponse",
                "responses": [
                    {"text": "For {party_size} guests on {date}:",
                     "type": "text"},
                    {"text": "Choose seating:", "type": "text"},
                    {"buttonType": "event", "text": "Indoor",
                     "type": "button"},
                    {"buttonType": "event", "text": "Outdoor",
                     "type": "button"},
                ],
            }],
        }},
    ],
}
```

**Announce slot with payload:**
```python
{
    "name": "welcome",
    "source": "announce",
    "message": "Let's get started.",
    "response": [
        {"type": "payload", "data": {
            "messageType": "static",
            "scenarios": [{"name": "StaticResponse",
                           "responses": [{"text": "Let's get started.",
                                          "type": "text"}]}],
        }},
    ],
}
```

**Task with end_session:**
```python
{
    "name": "ConfirmReservation",
    "tool": "confirm_reservation",
    "then_say": "Reservation confirmed for {guest_name}!",
    "then_response": [
        {"type": "payload", "data": {"messageType": "static", ...}},
        {"type": "end_session", "reason": "completed"},
    ],
}
```

### Response Fields by Location

| Location | Text field | Response field |
|----------|-----------|----------------|
| Announce slot | `message` | `response` |
| User slot (ask) | `ask` | `response` |
| Task success | `then_say` | `then_response` |
| Task retry | `retry_say` | `retry_response` |
| Task exhaust | `on_exhaust.say` | `on_exhaust.response` |
| Validation error | `errors[code]` | `error_responses[code]` |
| Readback exhaust | `on_exhaust.say` | `on_exhaust.response` |
| Progress exhaust | `on_exhaust.say` | `on_exhaust.response` |

### Channel-Aware Responses

Dialogflow CX supports a `channel` field on each
`ResponseMessage` for routing different responses to different
surfaces (web, mobile, telephony). The framework supports this
via `channel_responses` dicts that override the default `response`
for specific channels:

```python
{
    "name": "occasion",
    "source": "user",
    "setter": "set_occasion",
    "ask": "Is this for a special occasion?",
    "response": [
        {"type": "text", "text": "Any special occasion?"},
    ],
    "channel_responses": {
        "MOBILE": [
            {"type": "payload", "data": {
                "messageType": "static",
                "scenarios": [{
                    "name": "StaticResponse",
                    "responses": [
                        {"text": "Select an occasion:", "type": "text"},
                        {"buttonType": "event", "text": "Birthday",
                         "type": "button"},
                    ],
                }],
            }},
        ],
    },
}
```

**Resolution order:** The engine reads `channel` from
`sm["channel"]` (set by the before_model_callback from
`callback_context.state`). If `channel_responses[channel]`
exists, it is used; otherwise the default `response` is used;
otherwise the string field (`ask`/`message`/`say`) is used as
plain text.

Channel field naming follows the pattern
`channel_{response_field}`:

- `channel_responses` for `response`
- `channel_then_response` for `then_response`
- `channel_retry_response` for `retry_response`
- Exhaust and error responses use nested dicts in
  `on_exhaust.channel_responses` and
  `channel_error_responses`

### Variable Substitution

All string values within response parts support `{slot_name}`
substitution from filled slots, identical to `ask` and
`then_say`:

```python
{"type": "payload", "data": {
    "scenarios": [{"responses": [
        {"text": "Table for {party_size} on {date}.", "type": "text"},
    ]}],
}}
```

Substitution is applied recursively to all strings in the
response part dicts after channel resolution and before delivery
to the callback.

### How It Works

1. **Engine** resolves channel overrides and applies variable
   substitution, returning a `"response"` key in the action
   dict alongside the existing `"message"` key
2. **before_model_callback** checks for `result["response"]`
   in the preemption block. If present, iterates the response
   parts and maps each `type` to the corresponding
   `Part.from_*()` factory. Falls back to
   `Part.from_text(result["message"])` when no response parts
   are defined
3. **`function_call`** handling is unchanged — it runs after
   the response/message block and can coexist with response
   parts

### Payload Delivery Mechanics

Rich payloads (`Part.from_json()`) are only delivered to the
client on **preempted turns** — turns where the callback returns
an `LlmResponse` that bypasses the LLM. The engine includes
`response` parts in its action dict only when `preempt` is True.
This means payloads are delivered at these moments:

| Preemption trigger | Typical payloads |
|-------------------|------------------|
| Announce slot (`preempt: True`) | Welcome cards, disclosure banners, chips for first question |
| Task fire (success `then_response`) | Confirmation cards, end_session signals |
| Task retry/exhaust (`on_failure`) | Error cards, escalation deep links |
| Validation error (`error_responses`) | Re-prompt chips, error banners |
| Readback/progress stall exhaust | Escalation cards |

On non-preempted turns, payloads are delivered via the
**after_model_callback injection** path (see below).

**CES output format:** CES maps `Part.from_text()` to
`output.text` and `Part.from_json()` to `output.payload`.
Multiple parts in a single `LlmResponse` produce multiple
`SessionOutput` entries — each text/payload part becomes a
separate output. A chat frontend reads `output.payload` to
render rich UI elements (cards, chips, buttons) alongside the
text responses.

### after_model_callback Payload Injection

When `response` parts exist but the engine is NOT preempting
(e.g., welcome card with `preempt: False`, party-size chips on
a regular question turn), the engine stashes them in
`sm["_pending_payloads"]`. The `after_model_callback` then:

1. Reads `sm["_pending_payloads"]` from
   `callback_context.state`
2. Guards against multi-model-call turns — if the agent
   already produced output in an earlier model call this
   turn, skips injection to avoid duplicates
3. Converts each response descriptor to a CES Part (same
   type→factory mapping as the preemption block)
4. Appends the Parts AFTER the LLM's existing parts (text
   first, payloads second)
5. Clears `_pending_payloads` to prevent re-injection

```
Engine runs → combined_response exists
  ├─ preempt=True  → response in engine result
  │                  → before_model dispatches
  └─ preempt=False → stashed in sm._pending_payloads
                       → LLM runs naturally
                       → after_model reads stash
                       → appends Parts to response
                       → clears stash
```

This allows rich UI elements (cards, chips) to accompany the
LLM's natural text without sacrificing the LLM's ability to
process user input and call setter tools.

### Backward Compatibility

- Slots without `response` fields continue to work exactly as
  before via `Part.from_text()`
- The `ask`/`message`/`say` string fields remain the primary mechanism for system instruction directives
- Existing evals are unaffected since they don't check payload content

---

## 9. Guarantees

The framework provides the following guarantees, none of which depend on LLM behavior:

### 9.1 Ordering Guarantee

Slots with `requires` will never be asked before their dependencies are filled. The LLM cannot even call the setter for a blocked slot — it's hidden via tool visibility.

### 9.2 Completeness Guarantee

No task fires until ALL its active input slots AND active `requires` slots are in `filled`. This is enforced by `compute_dag_state`, which checks both `all(s in filled for s in active_inputs)` and `all(r in filled for r in active_requires)` before returning a `fire` action. Inactive slots (whose `condition` evaluates to `False`) are excluded from both checks.

### 9.3 Idempotency Guarantee

`compute_dag_state` is a pure function of `(tasks, slots, filled, pending, task_results)`. Given the same state, it always returns the same action. The callback can be re-invoked safely — it will produce the same decision.

### 9.4 Retry Bound Guarantee

A task will fail at most `max_retries` times before the `on_exhaust` path is taken. The framework counts failures in `_retries`, and this count is never reset except by a successful execution of that same task. The same guarantee applies to:

- **Slot validation retries** (tracked under `"slot:<name>"` keys) — a slot will reject at most `max_retries` times before escalation.
- **Steer-back escalation** — the 3-tier steer-back mechanism (Section 8) escalates after `escalate_after` consecutive off-topic turns, bounding how long the conversation can stall.

### 9.5 Terminal State Guarantee

Once `status` is `"complete"` or `"escalated"`, the callback's first check short-circuits: it returns `OK` without evaluating the DAG or modifying state. The conversation cannot regress to slot-filling after reaching a terminal state.

### 9.6 No-Corruption Guarantee (via Tool Visibility)

- Filled slots cannot be overwritten (setter is hidden).
- Slots whose `requires` are unmet cannot be set prematurely (setter is hidden). Slots with no unmet dependencies can be set in any order.
- Readback cannot be confirmed when nothing is pending (tool is hidden).
- Readback cannot be bypassed (all non-readback setters are hidden except for inline corrections).

### 9.7 Cascade Guarantee

When a non-terminal task succeeds and fills output slots, the framework re-evaluates the DAG. If another task is now ready, it fires via a `function_call` return on the same engine invocation (the second task executes on the subsequent callback cycle via CES tool dispatch). Announce slots cascade within a single invocation. The user never waits for an extra user turn for a task that could have fired immediately.

---

## 10. Architecture: What the LLM Owns vs. What Python Owns

| Concern | Owner | Mechanism |
|---|---|---|
| Understanding user intent | LLM | Tool selection based on user message |
| Parsing values from natural language | LLM | Tool arguments (e.g., "next Friday" → "2026-07-18") |
| Generating natural responses | LLM | Free-form text generation guided by `_system_message` |
| Handling off-topic conversation | LLM | Responds naturally, returns to slot-filling on next callback |
| Deciding what to ask next | Python | `compute_dag_state` → `_find_next_question` |
| Deciding when to fire a task | Python | `compute_dag_state` → input readiness check |
| Executing backend operations | Python | `function_call` return → CES tool dispatch → `_handle_post_executor` |
| Counting retries | Python | `_retries` dict in session state |
| Deciding when to escalate | Python | `retries >= max_retries` check |
| Enforcing slot dependencies | Python | `requires` check + tool visibility |
| Preventing invalid tool calls | Python | `_compute_hidden_tools` per turn — hides filled, inactive, and dependency-blocked setters |
| Generating readback text | Python | `_build_readback` with compiled format specs |
| Confirming/rejecting readback | LLM | Calls `confirm_pending` / `reject_pending` based on user response |
| Short-circuiting confirmation for clear affirmatives | Python | `_is_affirmative()` + auto-confirm in `_try_auto_confirm` — preempts with `function_call(confirm_pending)` without LLM call |
| Confirming + processing new content in one message | Python + LLM | `_starts_affirmative()` + `_apply_inline_confirm` silently confirms, then LLM runs with collection instructions to call setters |
| Handling slot validation errors | Python | `_handle_slot_errors` resolves message from config, tracks retries |
| Detecting conversation drift | Python | `_steer_back_turns` counter, 3-tier recovery via `steer_back` config (soft SI → hard preempt → escalate) |
| Validating task output shape | Python | `after_tool_callback` checks declared `outputs` keys are present in executor result |
| Routing setter results to state | Python | `after_tool_callback` reads setter results, writes to `pending` or `_slot_errors`, enforces prerequisites and cross-slot validation |
| Writing directives to state | Python | Engine returns `message` in result; `before_model_callback` wraps in `<system_directive>` via `_build_phase_suffix` |
| Managing instruction phases | Python | `before_model_callback` assembles phase-specific SI suffix (collection OR readback) via `_build_phase_suffix` based on engine result and SM state |
| Transitioning after confirmation | Python | `_readback_transition` flag triggers preemption with next question |
| Config validation | Python | `_validate_config()` called by engine on first invocation — catches misconfig early |
| Escalating stuck conversations | Python | `_steer_back_turns >= escalate_after` triggers `on_exhaust` via `steer_back` config |
| Deferred routing | Python | Deferred eligibility + group completion |
| Deferred hints | Python | `<deferred_collection>`, `<readback_scope>` hints in `si_suffix` |
| Deferred rejections | Python | `reject_pending` sets `_rejection_snapshot`; `before_agent_callback` processes it at the start of the next turn |
| Observability | Python | `_log()` emits structured `[slot-filling:tag]` print lines for each engine event |

The key insight: **the LLM is a language interface, not a state machine.** It translates between human language and structured tool calls. Python handles everything else.

---

## 11. Writing Prompts and Tool Docstrings

The framework's value comes from deterministic enforcement — tool visibility, validation, retry tracking, and preemption all happen in Python, not in the LLM's head. Prompts and tool docstrings should respect this boundary. When they duplicate what the orchestrator already enforces, they add cognitive load and actively cause failures.

### 11.1 The Core Principle

**If the orchestrator enforces it, don't prompt for it.**

Every constraint in a prompt or docstring is something the LLM must evaluate on every turn. Constraints that duplicate deterministic enforcement don't make the system safer — the orchestrator already prevents the bad state. Instead, they give the LLM reasons to second-guess tool calls, pre-filter input, or generate its own error messages, all of which bypass the framework's error handling.

### 11.2 What Goes Wrong

When a tool docstring says *"Only valid after available times have been presented to the guest"*, the LLM now has two reasons to avoid calling the tool early: the docstring warning AND the fact that it can't see the tool (hidden by `_compute_hidden_tools`). The first is redundant. Worse, if the user says "I'd like the 7 PM slot" before times are shown, the LLM may decide — based on the docstring — to refuse the call and explain why, instead of calling the tool and letting the framework return a proper error with retry tracking.

Similarly, when a docstring says *"Record the number of guests (integer, 1-8)"*, the LLM learns the valid range. If the user says "table for 12", the LLM may tell them directly that 12 is out of range — without ever calling the setter. The `_slot_errors` signal is never emitted, `_handle_slot_errors` never runs, the retry counter never increments, and the config's carefully crafted error message is never delivered.

The LLM's improvised error message may be wrong, inconsistent with the business's tone, or missing critical details (like an events team email for large parties). The framework's error message, defined in config, is exactly right every time.

### 11.3 What Belongs in Tool Docstrings

Tool docstrings should describe **what the tool does** and **what format the LLM should prepare**, not **when or whether to call it**.

**Good** (tells the LLM how to prepare the argument):
```python
def set_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format.

    Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD.
    """
```

**Bad** (duplicates orchestrator enforcement):
```python
def set_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format.

    Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD.
    Only call after party size has been confirmed.  # ← tool visibility handles this
    Date must be in the future.                     # ← code validates, config has error msg
    """
```

### 11.4 What Belongs in Agent Instructions

Agent instructions should tell the LLM what it owns (Section 10): parsing user intent, calling the right setter, relaying `_system_message`, and maintaining a warm persona. They should NOT tell the LLM about:

| Don't instruct | Why not | What handles it |
|---|---|---|
| "Don't call X before Y" | Tool is hidden until deps are met | `_compute_hidden_tools` + `requires` |
| "If the tool returns error, show the error message" | Error is preempted before the LLM responds | `_handle_slot_errors` + preemption |
| "Don't call other tools after a setter" | Irrelevant tools are hidden | `_compute_hidden_tools` (fresh pending) |
| "Only accept parties of 1-8" | Setter validates, config has error msg | `set_reservation_basics` code + `validation.errors` |
| "Escalate after 3 failed attempts" | Retry counter + exhaust logic | `_retries` + `on_exhaust` config |

Each of these instructions is a constraint the LLM must track, weigh against the conversation, and potentially act on — all for behavior the framework already guarantees. Removing them makes the prompt shorter, reduces the chance of the LLM overriding the framework, and lets the LLM focus on what it's good at: language.

### 11.5 The Decision Checklist

When writing a prompt rule or docstring constraint, ask:

1. **Is this enforced by code?** (validation in setter, tool visibility, retry logic) → Don't prompt for it.
2. **Is this enforced by tool visibility?** (hidden tools, `requires` deps) → Don't prompt for it.
3. **Does this tell the LLM how to prepare an argument?** (format conversion, parsing guidance) → Keep it in the docstring.
4. **Does this tell the LLM about its persona or communication style?** (warmth, tone, what to relay) → Keep it in the instruction.
5. **Does this tell the LLM about its role in the framework?** (use `_system_message`, call setters for each piece of info) → Keep it in the instruction.

Rules 3-5 describe things only the LLM can do. Rules 1-2 describe things the LLM shouldn't try to do.

---

## 12. Adding a New Agent

To create a new agent with this framework, create a new
`{config_id}_dag` tool directory under `tools/`. The tool
should be a parameterless function returning a config dict
containing:

1. **Slots** -- what data to collect, in what order, with
   format specs, conditions, and validation rules.
2. **Tasks** -- what backend operations to fire, with a
   `tool` key naming the CES tool, inputs, outputs, success
   checks, and retry config.
3. **Steer-back** config -- 3-tier recovery rules for
   stuck conversations (soft SI, hard preempt, escalate).

The `confirm_pending` and `reject_pending` readback tools
are hardcoded in the framework -- they don't appear in the
agent config.

All config values are pure serializable data — no callables.
Formatters use general-purpose type specs (e.g.,
`{"type": "plural", "one": "guest", "other": "guests"}`) or
named built-ins (`"date"`, `"time"`). Conditions are lambda
strings (e.g., `"lambda filled: ..."`). Both are compiled to
callables once at startup by `_compile_config()`.

The `slot_filling_engine` tool, the three callbacks
(`before_agent_callback`, `before_model_callback`,
`after_tool_callback`), and the `confirm_pending` and
`reject_pending` tools are framework components — copied
unchanged per project. The agent's `instruction.txt`
contains only agent-specific content (role, persona,
rules/guidelines); protocol blocks are injected
dynamically by `before_model_callback`.

### 12.1 Event-Driven Slot Pre-Filling

Slots can be pre-populated from external signals (telephony
data, web forms, CRM lookups) injected via
`sessions.run(variables={"event_data": {...}})`.

#### Declaring the event_data variable

CES only exposes **declared** variables in
`callback_context.state`. Declare `event_data` in
`app.json`:

```json
{
    "name": "event_data",
    "schema": {"type": "OBJECT", "default": {}}
}
```

#### Config-driven source

Mark a slot as event-sourced in `_get_config()` by
including `"event"` in its `source` list and specifying
an `"event_key"`. Include `"user"` in the list so the
slot's setter remains visible when no event data is
present:

```python
{
    "name": "party_size",
    "source": ["event", "user"],
    "event_key": "party_size",
    "setter": "set_reservation_basics",
    "setter_field": "party_size",
    "ask": "How many guests will be dining?",
    ...
}
```

The `source` list is evaluated in priority order: the
framework tries `"event"` first (check `event_data`),
then falls back to `"user"` (show setter, ask the user).
All `source` values are normalized to lists internally
via `_normalize_sources()`, so single-string sources
like `"user"` remain backward compatible.

#### Pre-fill location: `slot_filling_engine`

Event pre-fill runs inside `slot_filling_engine`, after
config compilation but before DAG evaluation. It runs
on every engine call but is idempotent — `fill_slots()`
skips slots that are already filled:

```python
if event_data:
    event_values = {}
    for slot_def in config["slots"]:
        if "event" not in _normalize_sources(
            slot_def.get("source", "user"),
        ):
            continue
        key = slot_def.get("event_key", slot_def["name"])
        value = event_data.get(key)
        if value is not None:
            event_values[slot_def["name"]] = value
    if event_values:
        result = fill_slots(sm, config, event_values)
        if result["filled"]:
            sm["_event_prefilled_this_turn"] = True
```

Event pre-fill uses `fill_slots()` (Section 12.2) with
`skip_readback=True` (the default), writing trusted event
data directly to `filled`.

#### Timing: instruction assembly

Since `before_model_callback` assembles the SI suffix
after running the engine, it always has up-to-date
information. When `event_prefilled` is set, the engine's
result includes `si_suffix` with the correct next
question (computed from the DAG, which sees the
event-filled slot). The `_build_phase_suffix` function
handles this via the `event_prefilled` flag — it skips
the standard directive injection and uses the engine's
`si_suffix` directly, which already contains the
`<system_directive>` with the correct next question.

#### Tool visibility for event-sourced slots

The tool selection generator includes any slot with
`"user"` in its source list (whether `"user"`,
`["event", "user"]`, or any other combination). Filled event slots are excluded from
tool selection (same as any other filled slot). This
means:

- **With event data**: slot is pre-filled → setter hidden
  → LLM skips to next slot.
- **Without event data**: slot is unfilled → setter
  visible → LLM asks the user normally.

#### Why `filled` and not `pending`

Event data comes from the system (telephony, CRM), not
from the user's spoken input. There's nothing to read back
and confirm — the data is authoritative. Writing to
`filled` skips the readback/confirm cycle entirely.

#### Idempotency

Event pre-fill runs on every engine call, but
`fill_slots()` skips slots already in `filled`, so
re-processing the same event data is safe. If the user
later corrects a pre-filled value via a setter tool, the
correction takes precedence.

#### Testing with CES evals

Inject event data via the standard CES eval format using
`userInput.variables` and `userInput.event` as separate
steps in the same turn:

```json
{
  "turns": [{
    "steps": [
      {"userInput": {"variables": {"event_data": {"party_size": 4}}}},
      {"userInput": {"event": {"event": "welcome"}}},
      {"expectation": {"agentResponse": {"chunks": [{"text": "welcome"}]}}}
    ]
  }, {
    "steps": [
      {"userInput": {"text": "I'd like to make a reservation"}},
      {"expectation": {
        "note": "Agent should ask for date, NOT party size",
        "agentResponse": {"chunks": [{"text": "date"}]}
      }}
    ]
  }]
}
```

For programmatic testing via SCRAPI:

```python
sessions.run(
    session_id=sid,
    event="WELCOME",
    variables={"event_data": {"party_size": 4}}
)
```

### 12.2 Programmatic Slot Filling: `fill_slots()`

The `fill_slots()` function lets you fill slots from any
Python context -- callbacks, tools, test harnesses, or
external integrations. The DAG engine advances
automatically on the next `before_model_callback`
invocation.

```python
def fill_slots(
    sm: dict[str, Any],
    config: dict[str, Any],
    values: dict[str, Any],
    skip_readback: bool = True,
) -> dict[str, list[str]]:
```

**Parameters:**

| Param | Type | Description |
|---|---|---|
| `sm` | dict | The state machine dict (`callback_context.state["sm"]`). |
| `config` | dict | The compiled DAG config from `_compile_config()`. |
| `values` | dict | `{slot_name: value}` pairs to fill. |
| `skip_readback` | bool | `True` (default): write to `filled` (no confirmation). `False`: write to `pending` (triggers readback). |

**Returns:** `{"filled": [names], "skipped": [names]}`.

**Skip reasons:** A slot is skipped if it is unknown
(not in the config), already filled, or its condition is
not met (inactive).

**Example — pre-fill from a CRM lookup:**

```python
sm = callback_context.state.get("sm", {})
config = _compile_config(_get_config())
fill_slots(sm, config, {
    "guest_name": crm_record["name"],
    "party_size": crm_record["default_party_size"],
})
```

**Example — route through readback:**

```python
fill_slots(sm, config, {"party_size": 4},
           skip_readback=False)
```

The value lands in `pending` and the framework shows a
readback prompt on the next callback invocation.

### 12.3 Announce Slots

An **announce slot** is a framework-controlled slot that
delivers text at a specific point in the DAG — a
greeting, a disclosure, a mid-flow status message — and
then fills a completion-bit (`True`) that unblocks
downstream slots and tasks. Announce slots have no setter,
no readback, and their value is never consumed as a task
argument.

#### Config

```python
{
    "name": "welcome",
    "source": "announce",
    "message": (
        "Welcome to Bella Notte! I'd be happy"
        " to help you with a reservation."
    ),
    "preempt": False,
}
```

| Field | Purpose |
|---|---|
| `source` | `"announce"` — framework-controlled, no setter. |
| `message` | Text to deliver. Supports `{slot_name}` placeholders resolved from `filled`. |
| `preempt` | `True` (default): deliver verbatim via `LlmResponse` (deterministic). `False`: set as `_system_message` guidance (LLM wraps in natural language). |
| `requires` | Standard — gates on other slots. |
| `condition` | Standard — lambda string for conditional announces. |

**Validation:** Announce slots must have a `message` field
and must NOT have a `setter`. Both are enforced by
`_validate_config()`.

#### Two Delivery Modes

**Preempt (`True`)**: The callback returns
`LlmResponse.from_parts(text=message)`. The user sees
the exact text. Uses `force_preempt` to bypass the
first-turn guard so welcome greetings work on the very
first message.

**Guided (`False`)**: The callback sets
`sm["_system_message"] = message`. The LLM generates a
natural response incorporating the guidance. Good for
greetings where you want the LLM's personality to show.

#### Cascade Behavior

When the DAG evaluator returns an announce action,
`_run_slot_filling` immediately fills the announce slot
(`filled[name] = True`) and re-evaluates the DAG. If the
next action is also an announce, it cascades — all
consecutive announce slots fire in a single callback
invocation, with their messages concatenated. The loop
continues until a non-announce action is found
(`fire`, `next_question`, `awaiting_readback`, or
`all_done`).

This means an announce slot followed by a user question
delivers both the announcement and the question in a
single response. For example, a welcome greeting
(`preempt: False`) followed by the `party_size` question
produces: *"Welcome to Bella Notte! I'd be happy to help
with a reservation. How many guests will be dining?"*

#### Interaction with Other Features

- **Tool visibility**: Announce slots have no setter, so
  `_compute_hidden_tools` is unaffected.
- **Deferred readback**: Announce slots are not
  user-sourced, so they are never deferred-eligible.
- **Auto-confirm**: No pending values from announce slots.
- **`_find_next_question`**: Kept as-is for deferred
  hints (only finds user questions, not announces).
  `_find_next_slot_action` is used for the main DAG
  evaluation path.

### 12.4 Task `requires`

Tasks can declare a `requires` list of slot names that
must be filled before the task fires, but are NOT passed
as arguments to the tool. This separates "gates" from
"data."

```python
{
    "name": "BookReservation",
    "tool": "book_reservation",
    "inputs": ["party_size", "date", ...],
    "requires": ["terms_disclosure"],
    ...
}
```

**Semantics:** Slots listed in `requires` must be filled
(and active, per their `condition`) before the task can
fire. Unlike `inputs`, `requires` slots are not passed
to the tool as arguments.

**Use case:** Gating a task on an announce slot (e.g., a
terms disclosure must be delivered before booking) or on
any slot that provides context but isn't a tool argument.

**Validation:** Each name in `requires` must exist in the
slot config, enforced by `_validate_config()`.

Without task `requires`, the only way to gate a task on
a non-argument slot is via transitive slot `requires` —
which doesn't work when the gate comes AFTER all input
slots are collected but BEFORE the task fires.

---

## 13. Worked Example: Happy Path

Using the toy reservation agent from Section 2:

```
Turn 1: User opens conversation
  Callback: status=in_progress, filled={}, pending={}
  DAG: no pending, no task ready → next_question: "How many guests?"
  Tool visibility: hide confirm/reject (nothing pending)
  Result: _system_message = "How many guests?"
  LLM: "Welcome! How many guests will be joining us today?"

Turn 2: User says "4 people for next Friday"
  LLM calls: set_num_guests(4), set_date("2026-07-18")
  Both write to filled: {num_guests: 4, date: "2026-07-18"}
  Callback: DAG sees CheckAvailability inputs ready → fire
  Executor: CheckAvailability({num_guests: 4, date: "2026-07-18"})
    → returns {open_slots: "6 PM, 7:30 PM, 9 PM", success: True}
  Cascade: open_slots now filled → chosen_time question unblocked
  _system_message: "We have 6 PM, 7:30 PM, 9 PM. Which time?"
  Preempt: task fired + contents > 1 → return LlmResponse directly
  User sees: "We have 6 PM, 7:30 PM, 9 PM. Which time?"

Turn 3: User says "7:30"
  LLM calls: set_chosen_time("19:30")
  Callback: DAG → next_question: "What name for the reservation?"
  Tool visibility: hide set_num_guests (filled), hide set_date (filled),
                   hide set_chosen_time (filled), show set_name
  Result: _system_message = "What name for the reservation?"
  LLM: "Great choice! And what name should I put this under?"

Turn 4: User says "Sarah Chen"
  LLM calls: set_name("Sarah Chen")
  Callback: DAG sees MakeReservation inputs ready → fire
  Executor: MakeReservation({num_guests:4, date:"2026-07-18",
                             chosen_time:"19:30", name:"Sarah Chen"})
    → returns {confirmation: "RES-4821", success: True}
  status → "complete"
  Preempt: "Confirmed! Number: RES-4821."

Turn 5+: Any further messages
  Callback: status == "complete" → return OK immediately
  LLM responds freely (e.g., "Is there anything else I can help with?")
```

---

## 14. Worked Example: Failure and Retry

```
Turn 2 (variant): CheckAvailability fails
  Executor returns {success: False, reason: "fully booked"}
  _retries["CheckAvailability"] = 1
  clear_slots: ["date"] → remove date from filled
  max_retries: 1 → 1 >= 1 → NOT YET (retries start at 0, incremented to 1)
  
  Wait — let's trace more carefully:
  _retries["CheckAvailability"] was 0 (not present), now set to 1
  max_retries is 1
  1 >= 1 → exhausted!
  
  on_exhaust: {say: "Please call us at 555-0100.", then: "escalate"}
  status → "escalated"
  _system_message: "Please call us at 555-0100."
```

If `max_retries` were 2 instead:

```
  _retries["CheckAvailability"] = 1
  1 >= 2 → not exhausted
  clear_slots: ["date"] → filled becomes {num_guests: 4}
  retry_say: "No availability for that date. Try another?"

Turn 3: User says "How about Saturday?"
  LLM calls: set_date("2026-07-19")
  Callback: DAG sees CheckAvailability inputs ready again → fire
  Executor: CheckAvailability({num_guests: 4, date: "2026-07-19"})
    → returns {open_slots: "8 PM", success: True}
  _retries["CheckAvailability"] cleared (success!)
  Cascade: chosen_time question unblocked
  Continues normally...
```

---

## 15. Worked Example: Inline Correction During Readback

```
Turn 2: User says "4 people"
  LLM calls: set_num_guests(4)
  Value goes to pending (readback configured): pending = {num_guests: 4}
  Callback: DAG sees pending → awaiting_readback
  Tool visibility: show confirm_pending, reject_pending, set_num_guests
                   hide set_date, set_chosen_time, set_name
  _system_message: "Just to confirm — 4 guests. Is that correct?"
  LLM: "Just to confirm, you said 4 guests. Is that right?"

Turn 3: User says "Actually, make it 6"
  LLM calls: set_num_guests(6)  ← visible because num_guests is in pending
  pending = {num_guests: 6}  ← overwritten
  Callback: DAG sees pending → awaiting_readback (again, with updated value)
  _system_message: "Just to confirm — 6 guests. Is that correct?"
  LLM: "Updated to 6 guests. Is that correct?"

Turn 4: User says "Yes"
  LLM calls: confirm_pending()
  confirm_pending sets _readback_transition = True
  pending → filled: filled = {num_guests: 6}
  Callback: pops _readback_transition (True)
  DAG → next_question: "What date works for you?"
  Readback transition + msg → preempt with "Wonderful! What date works for you?"
  User sees: "Wonderful! What date works for you?"
```

---

## 16. Comparison with Alternative Approaches

### 16.1 Prompt-Only Slot Filling

*"Just tell the LLM to collect these fields in order."*

Problems: the LLM skips fields, asks for the same field twice, calls the API before all fields are collected, forgets retry counts across turns, and generates its own retry messages that may not match business requirements.

### 16.2 Finite State Machine

*"Model each conversation state explicitly."*

Problems: state explosion when users provide multiple values at once, go off-topic, or correct previous answers. An FSM for a 5-slot agent with readback and retries would need dozens of states. The DAG approach handles these cases naturally because it's evaluated from scratch on each turn — it doesn't care *how* the current state was reached, only *what* state exists now.

### 16.3 Sequential Script

*"Ask question 1, then question 2, then question 3."*

Problems: can't handle multi-slot input ("4 people next Friday"), can't handle out-of-order input, can't handle corrections, can't handle going back. The DAG approach handles all of these because slots are independent — the order they're filled doesn't matter, only whether they're filled.

### 16.4 LLM-Driven Control Flow

*"Let the LLM decide when to call the API based on a system prompt."*

Problems: non-deterministic retry behavior, no guaranteed retry bound, the LLM may "decide" to skip escalation, no structural guarantee that all inputs are collected before the API call, and the retry count resets if the LLM "forgets" about previous failures.

---

## 17. Design Decisions and Trade-offs

### 17.1 Why Pure compute_dag_state + Effectful Execution?

Separating the decision from the execution makes the system testable. You can unit-test `compute_dag_state` with plain dicts — no mocks, no state setup, no side effects. The tests are fast and deterministic.

### 17.2 Why Ordered Lists Instead of a Graph Data Structure?

Slots and tasks are declared as ordered lists, not as an explicit graph with nodes and edges. The ordering is the priority — the first unfilled slot is asked next, the first ready task is fired next. This is simpler to configure and covers the vast majority of conversational flows. A true graph structure would be needed only for agents with parallel branches (which this framework doesn't support — see limitations).

### 17.3 Why Tool Visibility Instead of Validation?

You could let the LLM call any tool and reject invalid calls. But rejection wastes a model turn (the LLM generated tokens, called the tool, got an error, then has to try again). Tool visibility prevents the attempt entirely — the LLM can't call what it can't see.

### 17.4 Why Preemption Instead of Always Using the LLM?

When a task fires and we know exactly what to say, the LLM adds latency and randomness for no value. Preemption is faster and more predictable. The trade-off is that preempted messages lack the LLM's personality — but for task results and error messages, consistency matters more than warmth.

---

## 18. Limitations

1. **No parallel branches.** The DAG is linear — tasks fire in order. If two tasks could fire independently (e.g., checking availability AND verifying identity simultaneously), the framework fires them one at a time.

2. **No partial readback within a group.** When pending
   slots exist, ALL pending values are shown in readback.
   You can't confirm some and leave others pending. However,
   deferred readback (Section 7a) enables *grouped*
   confirmation where some slots are confirmed immediately
   and others are deferred until their group is complete.

3. **No multi-agent routing.** Multiple agents can each
   run their own DAG with isolated state (Section 12.1),
   but agent transfer/routing is handled externally --
   the framework does not manage handoffs between agents.

4. **No undo after confirmation.** Once values move from pending to filled via `confirm_pending`, they can't be changed (the setter is hidden). The user would need to explicitly ask to start over, which the framework doesn't support.

---

## 19. Observability (Structured Logging)

The engine emits structured log lines via `print()` for real-time debugging and post-hoc analysis. Each line is tagged with a `[slot-filling:<tag>]` prefix.

### 19.1 The `_log` Helper

```python
def _log(tag, **data):
    parts = " ".join(f"{k}={v!r}" for k, v in data.items())
    print(f"[slot-filling:{tag}]" + (f" {parts}" if parts else ""))
```

### 19.2 Log Tags

| Tag | Where | Data | Meaning |
|---|---|---|---|
| `progress` | `_log_progress` | `pending+`, `confirmed`, `task+`, `rejected` | State changes since last turn |
| `invoke` | `_log_invoke` | `n`, `phase`, `filled`, `pending`, `deferred`, `fresh`, `hidden`, `asking`/`rb`/`fired`/`done`/`preempted` | Per-invocation snapshot of engine state and decision |
| `slot_error` | `_handle_slot_errors` | `slot`, `code`, `retries` | Setter signaled invalid input |
| `slot_error_exhaust` | `_handle_slot_errors` | `slot` | Slot validation retries exhausted → escalation |
| `task` | `_handle_post_executor` | `name`, `ok` | Task executor result processed |
| `task_exhaust` | `_handle_post_executor` | `name` | Task retries exhausted → escalation |
| `slot_deactivated` | `_deactivate_conditional_slots` | `slot`, `source` | Conditional slot removed from filled/pending/deferred |
| `steer_back_soft` | `_handle_steer_back` | `turns`, `directive` | Soft steer-back: SI directive injected |
| `steer_back_hard` | `_handle_steer_back` | `turns`, `msg` | Hard steer-back: preempted with question/readback |
| `steer_back_escalate` | `_handle_steer_back` | `turns` | Steer-back exhausted → escalation |
| `steer_back_yield` | `_handle_steer_back` | `turns` | Post-hard-steer yield: LLM runs normally to process user's response |
| `auto_confirm` | `_try_auto_confirm` | `user_msg` | Pure affirmative detected → function_call(confirm_pending) |
| `auto_confirm_inline` | `_apply_inline_confirm` | `committed` | Inline confirm: pending slots silently committed |
| `announce` | `_run_slot_filling` | `slot` | Announce slot fired |
| `fill_slots` | `fill_slots` | `slot`, `value` | Programmatic slot fill (event pre-fill, etc.) |
| `re_deferred` | `_run_slot_filling` | `slot`, `task` | Promoted-from-deferred slot re-deferred because a different task is about to fire |
| `payload_route` | `_run_slot_filling` | `path`, `count` | Rich response payload routing decision (preempt_dispatch, stash_unconditional, stash_question, none) |

### 19.3 Usage

The `invoke` tag is the most detailed — it logs the full engine state on every invocation, including which slots are filled/pending/deferred, which tools are hidden, and what action was taken (asking a question, reading back, firing a task, preempting, or completing). The `n` field is a monotonically increasing invocation counter for correlating events within a session.

Log lines are always emitted via `print()`. They are useful for:

- **Debugging eval failures**: inspect the sequence of engine decisions that led to an unexpected state.
- **Tracing retry behavior**: see which slots/tasks failed, how many retries occurred, and whether escalation was triggered.
- **Verifying preemption**: confirm which preemption trigger fired and when.
- **Understanding deferred routing**: track when slots are routed to deferred vs. pending, and when groups complete.

### 19.4 Debug Mode (`_debug_log`)

Since `print()` output runs on the CES server and is not visible in eval transcripts, an opt-in debug mode accumulates log entries in the engine result so they appear in conversation state.

**Enabling debug mode:**

- **Tool evals** — add `debug: true` to `input_data`:
  ```yaml
  args:
    input_data:
      raw_config: *config
      sm: { ... }
      last_user_text: "yes"
      debug: true
  ```
  The `_debug_log` key appears in the tool response alongside `action` and `sm`.

- **Simulation / turn evals** — set `session_parameters`:
  ```yaml
  session_parameters:
    debug: true
  ```
  The `before_model_callback` reads `callback_context.session.session_parameters.get("debug")`, passes it to the engine, and stashes the returned `_debug_log` in `callback_context.state["_debug_log"]`.

**How it works:** When `debug` is truthy, `_log()` appends each formatted log line to a module-level `_DEBUG_LOG` list (in addition to the normal `print()`). The list is reset at the start of each `slot_filling_engine()` invocation and returned as `_debug_log` in the result dict. When `debug` is not set, zero overhead — the `_DEBUG_LOG` list is never touched.
