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

- `"user"` — collected from the user via a setter tool (e.g., the user says "4 people" and the LLM calls `set_party_size(4)`)
- `"task:TaskName"` — populated automatically when a backend task succeeds (e.g., `available_times` is filled when `FindAvailableTimes` returns)

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
| `source` | `"user"` or `"task:TaskName"`. Determines whether the LLM asks for it or a task produces it. |
| `setter` | Tool name the LLM calls to set this slot (user-sourced slots only). |
| `ask` | Prompt template shown when this is the next slot to fill. Supports `{slot_name}` placeholders resolved from already-filled slots. |
| `requires` | List of slot names that must be filled before this slot's question is eligible. Enforces ordering constraints (e.g., can't ask "which time?" until availability is known). |
| `requires_readback` | If `True`, values stay in `pending` for user confirmation before moving to `filled`. If `False` or absent, the framework auto-promotes to `filled` immediately. |
| `readback_fmt` | Callable that produces the human-readable fragment for confirmation readback (e.g., `4` → `"4 guests"`). |
| `condition` | Optional callable `(filled) → bool`. When present, the slot is only active if the condition returns `True`. Inactive slots are skipped and don't block tasks. See Section 4.4. |
| `validation` | Optional dict with `errors` (code → message mapping), `max_retries`, and `on_exhaust` config. See Section 5b. |

### 2.2 Tasks

A **task** is a backend operation that fires when all its input slots are filled. Tasks are the "edges" in the DAG that connect user-provided data to system-derived data.

```python
"tasks": [
    {"name": "CheckAvailability",
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
| `inputs` | Slot names that must all be in `filled` before this task can fire. |
| `outputs` | `{result_key: slot_name}` — maps keys from the task's return dict into filled slots. This is how task-sourced slots get populated. The framework validates that all declared output keys are present in the result; missing keys are treated as a task failure (see Section 3.2). |
| `success_check` | Key in the result dict that must be truthy for the task to count as successful. |
| `terminal` | If `True`, successful completion ends the conversation (sets `status = "complete"`). |
| `then_say` | Message template shown on success. Supports `{slot_name}` placeholders. |
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
[open_slots]* ─┘
               (* open_slots is not an input to MakeReservation in this example,
                  but chosen_time requires it, so it's transitively required)
```

The framework walks this DAG on every callback invocation, deciding what to do next based on the current state of `filled`, `pending`, and `task_results`.

### 2.4 State

All state lives in a scoped dict stored within the global session (e.g., `sm`). Do not use a generic "sm" key if your CXAS application uses multiple DAG-powered agents, as this may cause cross-DAG-agent state corruption. For the sake of readability in this document, we will refer to this dictionary simply as sm, but in practice, it must be uniquely namespaced. 

```python
sm = {
    "filled":         {},    # confirmed values: {"num_guests": 4, "date": "2026-07-15"}
    "pending":        {},    # values awaiting user confirmation (readback)
    "task_results":   {},    # successful task outputs: {"CheckAvailability": {...}}
    "_retries":       {},    # failure counts: {"CheckAvailability": 1, "slot:num_guests": 2, "readback": 1}
    "_slot_errors":   [],    # validation errors from setter tools: [{"slot": "...", "code": "..."}]
    "_last_state":    {},    # snapshot of filled/pending from prior invocation (for change detection)
    "_readback_stall": 0,    # consecutive callback cycles with non-empty pending and no confirm/reject
    "_progress_turns": 0,    # callback cycles since last forward progress (slot fill, task fire, readback)
    "_system_message": "",   # next message for the LLM to relay (written by the callback adapter)
    "_debug_log":     [],    # structured event log (capped at 20 entries) — see Section 18
    "status":         "in_progress",  # "in_progress" | "complete" | "escalated"
}
```

The `_retries` dict uses a triple-namespace key scheme:

- **Task retries**: bare task names (`"CheckAvailability"`) — tracks backend operation failures.
- **Slot validation retries**: `"slot:"` prefix (`"slot:num_guests"`) — tracks invalid user input for a specific slot.
- **Readback retries**: literal key `"readback"` — tracks how many times the readback stall detector has rejected pending values (see Section 7.5).

No collision between the three namespaces.

---

## 3. The Callback Lifecycle

The framework is structured as three layers, all in a single file (CES platform limitations prevent splitting across tools — callables can't be JSON-serialized, tools can't call other tools, and tool `context.state` may be stale within a turn):

1. **`_get_config()`** — agent-specific: slots, tasks, executors, formatters, conditions. Replace this per project.
2. **`_run_slot_filling(config, sm)`** — the orchestrator. CES-agnostic: takes a config dict and state dict, returns `{"hide_tools": [...], "preempt": bool, "message": str|None}`. Never touches CES types (`LlmResponse`, `Part`, `llm_request`, `callback_context`).
3. **`before_model_callback()`** — thin CES adapter (~15 lines). Extracts the uniquely namespaced state for this agent (e.g., sm = context.state.setdefault("sm_bella_notte", {})), calls `_get_config()` and `_validate_config()`, passes config + state to the orchestrator, writes the returned message to `sm["_system_message"]`, applies tool visibility via `llm_request.config.hide_tool()`, and optionally preempts the LLM via `LlmResponse.from_parts()`.

CES calls the callback before EACH model invocation, including after tool results within the same turn. This re-invocation is what lets the framework see state changes from setter tools and react immediately (e.g., fire a DAG task as soon as its inputs are filled).

Here is the full lifecycle of a single callback invocation:

```
User message arrives
        │
        ▼
┌──────────────────────┐
│ before_model_callback │  (CES adapter)
└────────┬─────────────┘
         │
    ┌────▼────┐
    │ Terminal │──yes──▶ return OK (let LLM respond freely)
    │ state?  │
    └────┬────┘
         │ no
         ▼
   _get_config() + _validate_config()
         │
         ▼
┌──────────────────────┐
│ _run_slot_filling()   │  (CES-agnostic orchestrator)
│                       │
│  ┌─────────────────┐  │
│  │ Tool visibility  │  │  Compute which tools to hide
│  ├─────────────────┤  │
│  │ Slot errors      │  │  Check for setter validation errors
│  ├─────────────────┤  │
│  │ Readback stall   │  │  Detect stuck readback cycles
│  ├─────────────────┤  │
│  │ Progress stall   │  │  Detect no-progress conversations
│  ├─────────────────┤  │
│  │ DAG evaluation   │  │  Decide: readback / fire / ask next
│  ├─────────────────┤  │
│  │ Execute action   │  │  Fire task, cascade if needed
│  └─────────────────┘  │
│                       │
│  Returns: {hide_tools, │
│   preempt, message}    │
└────────┬─────────────┘
         │
         ▼
   Write message to sm["_system_message"]
   Apply hide_tools via llm_request.config.hide_tool()
         │
    ┌────▼──────────┐
    │ Preempt and   │──yes──▶ return LlmResponse (skip LLM generation)
    │ contents > 1? │
    └────┬──────────┘
         │ no
         ▼
    return OK (let LLM generate with _system_message as guidance)
```

### 3.1 compute_dag_state() — The Pure Decision Function

This is the heart of the framework. It takes the current state as arguments and returns an action dict. It **never mutates state**. Its logic is a strict priority cascade:

1. **Pending slots exist?** → `awaiting_readback`. The user must confirm or reject before anything else happens.
2. **A task's inputs are all filled and it hasn't succeeded yet?** → `fire`. Walk tasks in declaration order, return the first one ready.
3. **Otherwise** → `next_question`. Walk slots in declaration order, return the first user-sourced slot that is unfilled and whose `requires` are met.

Because it's pure, it's trivially testable: pass in state dicts, assert on the returned action.

### 3.2 execute_dag_step() — The Mutation Point

Takes the action from `compute_dag_state` and applies it. Returns the message string; the callback adapter handles writing it to state.

- **Passthrough actions** (`awaiting_readback`, `next_question`, `all_done`): returns the message directly.
- **Fire action — success**: calls the executor, validates that declared output keys are present in the result (missing keys are treated as failure), stores the result in `task_results`, writes outputs into `filled`, marks terminal tasks complete, and **cascades** (re-evaluates the DAG to fire any newly-ready tasks or set the next question).
- **Fire action — failure**: increments `_retries`, optionally clears slots, checks exhaustion, returns the appropriate message.
- **Exception safety**: the executor call is wrapped in a `try/except`. If an executor raises an exception, the framework synthesizes a failure result (`{success_check_key: False}`) and feeds it into the normal retry/exhaust path. This prevents a single executor crash from taking down the entire callback.

The separation of `compute_dag_state` (pure) from `execute_dag_step` (effectful) means the decision logic can be understood, tested, and reasoned about without worrying about mutation timing.

### 3.3 Cascading (Iterative)

When a non-terminal task succeeds, it may fill slots that are inputs to the next question or even another task. Rather than waiting for the next user message to re-evaluate, `execute_dag_step` **immediately re-evaluates** the DAG using a `while action == "fire"` loop and processes cascaded actions iteratively. This avoids recursion depth issues in agents with many chained tasks.

Example cascade:

1. User provides `num_guests` and `date`.
2. DAG evaluator sees `CheckAvailability` has all inputs → fires it.
3. `CheckAvailability` succeeds → fills `open_slots` → `execute_dag_step` cascades.
4. Cascaded DAG evaluation: `open_slots` is now filled, so `chosen_time` (which `requires: ["open_slots"]`) becomes the next question.
5. The system message becomes "We have 6 PM, 7:30 PM. Which time?" — all in one callback invocation, no extra round-trip.

### 3.4 Config Validation

The callback adapter calls `_validate_config(config)` before passing the config to the orchestrator. This surfaces configuration errors as early failures rather than runtime mysteries. The function raises `ValueError` with a descriptive message if any check fails:

1. **No duplicate slot names** — every name in slots must be unique.
2. **Task inputs reference valid slots** — every name in a task's `inputs` list exists in slots.
3. **Task outputs reference valid slots** — every `slot_name` in a task's `outputs` values exists in slots.
4. **Task-sourced slots have matching tasks** — a slot with `source: "task:X"` has a task named `X` in tasks.
5. **Executors exist for all tasks** — every task name has a corresponding key in executors.
6. **Requires reference valid slots** — every name in a slot's `requires` list exists in slots.
7. **No circular requires** — the `requires` graph is acyclic (detects cycles via DFS).
8. **Callables are callable** — slot and task `condition` fields must be callable if present.

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
| All other setters (unfilled, dependencies met) | Yes | User can provide any eligible info in any order |

### 4.3 Fresh Pending Detection

When a setter tool just created pending values (the prior invocation had no pending, this one does), the framework also hides `confirm_pending` and `reject_pending`. This forces the LLM to read back the values to the user before confirming. Without this, the LLM sometimes calls `reject_pending` → setter → `confirm_pending` all in one turn, skipping the readback step entirely.

Additionally, during fresh pending the **setter for each slot already in `pending`** is also hidden. This prevents the LLM from re-calling the same setter during the readback phase (e.g., `set_special_requests` right after `set_special_requests` just ran), which would otherwise push the pending state from "fresh" to "awaiting_confirmation" and allow `confirm_pending` to fire on the next invocation — skipping the readback entirely.

### 4.4 Conditional Slots

Slots can have a `condition` callable that determines whether the slot is active. Inactive slots are automatically hidden (setter not visible), skipped by the question-finding logic, and don't block tasks that list them as inputs.

```python
{"name": "contact_phone", "source": "user", "setter": "set_phone",
 "condition": lambda filled: int(filled.get("num_guests", 0)) >= 5,
 "ask": "For large parties, we need a contact number."}
```

The framework also handles **auto-deactivation**: if a slot was previously filled but its condition becomes `False` (e.g., user corrects party size from 6 to 3), the framework removes the value from `filled`/`pending` so the DAG doesn't wait for it.

### 4.5 Why Tool Visibility Matters

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
2. If `on_exhaust.then == "escalate"`, sets `sm["status"] = "escalated"`.
3. On the next callback, the terminal state guard catches `status == "escalated"` and lets the LLM respond freely (no more slot collection).

### 5.4 Retry State Tracking

Retry counts live in `sm["_retries"]`, a dict with dual-namespace keys:

- **Task retries**: `{"CheckAvailability": 1}` — bare task name.
- **Slot validation retries**: `{"slot:num_guests": 2}` — `"slot:"` prefix prevents collision with task names.

This field:

- Starts empty (`{}`) — zero overhead in the happy path.
- Only gets populated when a task or slot validation actually fails.
- Is cleared for a task when it succeeds (`retries.pop(task_name, None)`), or for a slot when its setter succeeds (`retries.pop("slot:name", None)`).
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

When a setter tool detects invalid input, it writes an error signal to `sm["_slot_errors"]` and returns `{"error": True}` — no message, no retry logic:

```python
def set_num_guests(count: int) -> dict:
    # Always use the unique namespace to prevent cross-DAG Agent pollution
    sm = context.state["sm_bella_notte"]
    if not (1 <= count <= 8):
        sm.setdefault("_slot_errors", []).append(
            {"slot": "num_guests", "code": "out_of_range"}
        )
        return {"error": True}
    # ... normal success path
```

On success, the setter clears any prior retry count for its slot:

```python
    sm.get("_retries", {}).pop("slot:num_guests", None)
```

### 5b.3 `_handle_slot_errors()` — The Orchestrator

The callback's `_handle_slot_errors()` function processes these signals before DAG evaluation. Unlike early versions that only processed the first error, it now processes **all** errors from a single turn and joins their messages:

1. **Pop** `sm["_slot_errors"]` (clears after reading).
2. **For each error**:
   a. **Increment** `sm["_retries"]["slot:<name>"]`.
   b. **Check exhaustion**: if retries >= `max_retries`, use `on_exhaust.say` and optionally escalate. Short-circuits immediately on any exhaustion.
   c. **Look up** the error message from the slot's `validation.errors` config using the error code.
   d. **Resolve placeholders**: messages support `{slot_name}` placeholders (e.g., `"We have {available_times}. Which works?"`) resolved from `sm["filled"]` via `.format(**filled)`. Unknown placeholders are left as-is (protected by `KeyError` catch).
3. **Join** all resolved messages with a space.
4. **Return** the combined message (the callback adapter writes it to `sm["_system_message"]`).
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

```python
import datetime

def set_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format.

    Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD.
    """
    # Always use the agent's unique namespace to prevent cross-DAG Agent pollution
    sm = context.state["sm_bella_notte"]
    # 1. Validate format via strptime (catches impossible dates like 2026-13-45)
    date = str(date).strip()
    try:
        parsed = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        sm.setdefault("_slot_errors", []).append(
            {"slot": "date", "code": "invalid_format"}
        )
        return {"error": True}

    # 2. Validate semantics — reject past dates
    if parsed < datetime.date.today():
        sm.setdefault("_slot_errors", []).append(
            {"slot": "date", "code": "past_date"}
        )
        return {"error": True}

    # 3. Clear any prior retry count on success
    sm.get("_retries", {}).pop("slot:date", None)
    sm.pop("_readback_stall", None)

    # 4. Always write to pending — the framework handles promotion
    sm.setdefault("pending", {})["date"] = date

    # 5. Return a result (the LLM sees this as the tool response)
    return {"stored": True, "value": date}
```

### 6.2 Design Principles

**Setters are thin.** They validate input, write to state, and return. They contain zero DAG logic, zero control flow, and zero knowledge of other slots. The DAG framework handles everything else in the callback. On success, setters perform two resets: clearing their slot's retry count (`_retries["slot:<name>"]`) and resetting the readback stall counter (`_readback_stall`). Both are safe no-ops when no retries or stalls are active.

**Setters always write to `pending`.** The framework's auto-promote logic (in `_run_slot_filling`) decides what happens next: slots with `requires_readback: True` in the config stay in `pending` for user confirmation, while slots without readback are automatically promoted to `filled` before DAG evaluation. This keeps the setter simple — it doesn't need to know the readback policy.

**Setters signal errors, not messages.** On invalid input, setters append `{"slot": "name", "code": "error_code"}` to `sm["_slot_errors"]` and return `{"error": True}`. The callback resolves the user-facing message from the slot's `validation.errors` config (see Section 5b). This keeps error messaging centralized and enables retry tracking.

**The LLM does the parsing.** The setter for `date` expects YYYY-MM-DD — the LLM converts "next Thursday" to "2026-07-17" before calling the tool. This plays to the LLM's strength (language understanding) while keeping the setter deterministic (string validation).

**Multi-slot messages work naturally.** If the user says "4 people, next Friday," the LLM calls both `set_num_guests(4)` and `set_date("2026-07-18")` in the same response. Both values land in state. The next callback invocation sees both slots filled and proceeds accordingly (possibly firing a task if both were the last missing inputs).

---

## 7. Readback and Confirmation

Readback is the pattern where the agent repeats collected values back to the user for confirmation before proceeding. This is especially important for high-stakes operations (financial transactions, medical records, legal agreements) where a misheard or misunderstood value could have consequences.

### 7.1 How It Works

1. **Setter writes to `pending`** (all setters always write to pending). Slots with `requires_readback: True` in the config stay there; slots without it are auto-promoted to `filled` by the framework before DAG evaluation.
2. **Callback invocation**: `compute_dag_state` sees non-empty `pending` → returns `awaiting_readback`.
3. **Readback message**: `_build_readback()` formats each pending value using its `readback_fmt` and joins them: *"Just to confirm — 4 guests, on July 15th. Is that correct?"*
4. **User confirms**: LLM calls `confirm_pending()` → moves all pending values to `filled` → next callback fires the DAG forward.
5. **User rejects**: LLM calls `reject_pending()` → discards all pending values → next callback re-asks for those slots.
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

If `_is_affirmative` returns `True`, the callback:

1. Merges `pending → filled` directly (no LLM call, no `confirm_pending` tool).
2. Sets `sm["_readback_transition"] = True` to trigger preemption with the next question.
3. Resets `sm["_progress_turns"] = 0` (confirmation is forward progress).
4. Pops `sm["_readback_stall"]` (confirmation resolves any stall).
5. Transitions `phase` to `"collection"`.

The auto-confirm gate **only fires on user-turn callbacks** (`last_user_text` is non-empty). Post-tool re-invocations (where the last content is a function response, not a user message) never trigger auto-confirm, preventing double-firing within the same turn.

**Correction detection:** Messages like "Yes, but make it 6" are NOT auto-confirmed because "but" is a correction signal. Messages like "Yes, that's correct" ARE auto-confirmed because punctuation is stripped from words before comparison ("that's" → "thats", no correction signals). Only messages where `len(words) <= 5` are considered (longer messages are left to the LLM).

**Why 5 words?** Short affirmatives are unambiguous. Longer messages may contain corrections, clarifications, or new information that the LLM should process. Capping at 5 words bounds the false-positive risk.

### 7.4 Readback Transition Preemption

After the user confirms a readback — either via `confirm_pending` (tool call) or auto-confirm (callback short-circuit) — the framework preempts the LLM with the next question rather than relying on the LLM to generate it. This prevents a class of failures where the LLM, confused by complex multi-turn conversation context (especially after reject → re-ask → confirm cycles), fails to produce a valid response.

**How it works:**

1. `confirm_pending` or auto-confirm sets `sm["_readback_transition"] = True`.
2. The callback pops this flag before DAG evaluation.
3. After the DAG computes the next action, if `_readback_transition` was true and the action is `next_question`, the callback preempts with `"{prefix} {next_question}"` (prefix chosen randomly from `confirm_transition_prefix` list in config, e.g. `["Wonderful!", "Perfect!", "Great!"]`).
4. If the DAG action is `fire` instead (e.g., confirming the date triggers FindAvailableTimes), the existing task fire preemption handles it — the readback transition flag is consumed but the task preemption takes precedence.

The random prefix list ensures preempted post-confirmation responses still sound warm and varied, passing persona evaluation checks. `reject_pending` does **not** set this flag — after rejections, the LLM generates its own response naturally (e.g., "Okay, my apologies! How many people will be in your party?"), which preserves conversational warmth.

### 7.5 Readback and Tool Visibility Interaction

During readback, tool visibility ensures only valid actions are available:

- `confirm_pending` and `reject_pending` are visible (the expected actions).
- Setters for slots that ARE in `pending` are visible (inline correction).
- Everything else is hidden.

This prevents the LLM from "moving on" to the next question while values are still unconfirmed.

### 7.6 Readback Stall Detection

A safety net for the case where the LLM fails to call `confirm_pending` or `reject_pending` despite pending values existing. This can happen when complex conversation context confuses the LLM into generating a response without using either readback tool.

**The problem:** Without stall detection, a stuck readback creates an infinite loop — the callback sets `_system_message` to the readback prompt every turn, but the LLM never acts on it.

**How it works:**

1. On each callback invocation, after popping `_readback_transition`, if `pending` is non-empty and `_readback_transition` was not set (meaning neither `confirm_pending` nor `reject_pending` was called this cycle), increment `sm["_readback_stall"]`.
2. When `_readback_stall >= 3` (threshold), the framework **rejects** all pending values (clears `sm["pending"]`), resets the stall counter, and increments `sm["_retries"]["readback"]`.
3. The readback retry count is checked against the `readback_retry` config (part of the config dict returned by `_get_config()`):

```python
"readback_retry": {
    "max_retries": 2,
    "on_exhaust": {
        "say": "I'm having trouble processing your details. "
               "Please call us at 555-0100 and we'll help you directly.",
        "then": "escalate",
    },
}
```

4. If readback retries are exhausted, the `on_exhaust` path fires — the escalation message is preempted and `status` is set to `"escalated"`.
5. If retries remain, the rejected slots re-enter the DAG as unfilled, and the framework re-asks for them on the next pass.

**Why reject, not auto-confirm?** Auto-confirming stalled values would silently commit potentially incorrect data (the user may have said "no" but the LLM failed to call `reject_pending`). Rejecting is always safe — it asks the user again, and the retry budget bounds how many times this can happen.

**Counter resets:** The stall counter is reset in three places:

- **`confirm_pending` and `reject_pending`** clear `_readback_stall` and `_retries["readback"]` on success, so successful readback cycles reset the stall detector completely.
- **All setter tools** clear `_readback_stall` on success (writing to pending or filled). This is critical for inline corrections during readback — when the user says "actually, make it 6," the setter call proves the user is actively engaged, not that the LLM is stuck. Without this reset, the stall counter would accumulate across the initial setter, the user's correction message, and the correction setter, hitting the threshold of 3 and falsely rejecting the pending values.

### 7.7 Global Progress Stall Detection

A broader safety net that catches **any** conversation making no forward progress — not just readback loops. The readback stall detector (Section 7.6) handles one specific failure mode; the progress stall detector is global.

**The problem:** The LLM could loop on off-topic chat, repeat the same question without the user providing new info, or otherwise spin without advancing through the DAG. Without a global bound, these conversations run indefinitely.

**How it works:**

1. On each callback invocation where `last_user_text` is non-empty, increment `sm["_progress_turns"]`. Post-tool and post-preemption callbacks (where `last_user_text` is empty) do **not** increment the counter — only genuine user turns count toward the stall limit. Without this gate, auto-confirm preemption would add an extra count every time it fired, making the effective limit unreliable.
2. When `_progress_turns >= max_turns` (default 8), the framework escalates via the `progress_stall` config (part of the config dict returned by `_get_config()`):

```python
"progress_stall": {
    "max_turns": 4,
    "on_exhaust": {
        "say": "I'm having trouble completing your request. "
               "Please call us at 555-0100 and we'll help you directly.",
        "then": "escalate",
    },
}
```

3. On escalation, the exhaust message is preempted and `status` is set to `"escalated"`.

**Counter resets (any forward progress resets to 0):**

The framework compares `filled` and `pending` against a snapshot from the prior invocation (`_last_state`). Any change (new slot filled, new value pending, pending cleared by confirmation) resets `_progress_turns` to 0. This means setters, `confirm_pending`, `reject_pending`, and task execution all implicitly reset the counter — any state change counts as progress.

**Why 4 turns?** This is tight enough to catch the LLM going off the rails quickly — 4 consecutive turns with no slot fill, task fire, or readback action means the conversation is stuck. The counter resets on every meaningful state change, so normal flows (including corrections and brief off-topic detours) never approach this limit. Agents with more complex flows may increase this.

**Relationship to readback stall:** The two detectors are independent. The readback stall fires at 3 cycles when pending is non-empty and no readback tool is called. The progress stall fires at 4 turns with no state change of any kind. A conversation can trigger the readback stall (which rejects pending, counting as a state change that resets the progress counter) without ever hitting the progress stall. See Section 7.6 for readback stall details.

---

## 8. LLM Preemption

Sometimes the framework can generate the complete response without the LLM. When a task fires and produces a message (via `then_say` or `retry_say`), the framework can **preempt** the LLM call entirely — returning a pre-built response and skipping the model generation.

### 8.1 Why Preempt?

- **Latency**: skipping a model call saves 1-3 seconds.
- **Determinism**: the message comes from the task config, not from LLM generation, so it's exactly what was intended.
- **Cost**: no tokens consumed for a response that was already known.

### 8.2 Five Preemption Triggers

The framework preempts the LLM in five situations:

1. **Task fire**: A task executed and produced a result message (via `then_say`, `retry_say`, or `on_exhaust.say`). The message is delivered verbatim.
2. **Slot validation error**: A setter tool signaled an error via `_slot_errors`, and `_handle_slot_errors()` resolved the message from the slot's `validation.errors` config. The error message is delivered verbatim.
3. **Readback transition**: `confirm_pending` or auto-confirm just committed values, and the DAG's next action is `next_question`. The message is delivered with a randomly chosen prefix from `confirm_transition_prefix` (e.g. `"Wonderful!"`, `"Perfect!"`, `"Great!"`) for warmth.
4. **Readback stall exhaustion**: The readback stall detector has rejected pending values too many times and ``readback_retry.on_exhaust`` fires. The escalation message is delivered verbatim.
5. **Progress stall**: The global progress counter has exceeded ``progress_stall.max_turns`` with no forward progress. The escalation message is delivered verbatim.

All five use `LlmResponse.from_parts()` to skip the model call entirely.

### 8.3 When Not to Preempt

- **First turn**: the LLM should generate a natural greeting, not a canned "How many guests?" (checked via `len(llm_request.contents) > 1`).
- **After reject_pending**: the LLM handles rejections naturally, producing warm responses like "Okay, my apologies! How many people will be in your party?"
- **Setter success**: after a setter writes to pending, the readback prompt is set as `_system_message` and the LLM wraps it in natural language.
- **Readback stall (not exhausted)**: when the stall detector rejects pending but retries remain, the framework does not preempt — the DAG re-asks for the slots via normal `_system_message`, giving the LLM a chance to re-collect naturally.

### 8.4 The _system_message Contract

The orchestrator (`_run_slot_filling`) returns the message in its result dict but never writes to `sm["_system_message"]` directly. The callback adapter is responsible for writing it:

```python
if result.get("message"):
    sm["_system_message"] = result["message"]
else:
    sm.pop("_system_message", None)
```

This keeps the orchestrator CES-agnostic — it doesn't know about the `_system_message` state convention. The agent's instruction prompt includes a protocol like:

```
If sm._system_message is set, relay that information to the user naturally.
Do not skip it, do not add information beyond what it says, but do wrap it
in warm, natural phrasing.
```

This gives the LLM freedom to add personality ("Wonderful choice!") while ensuring the framework's content is delivered. The `else` branch clears stale messages when the orchestrator has nothing to say, preventing cross-turn leakage.

---

## 9. Guarantees

The framework provides the following guarantees, none of which depend on LLM behavior:

### 9.1 Ordering Guarantee

Slots with `requires` will never be asked before their dependencies are filled. The LLM cannot even call the setter for a blocked slot — it's hidden via tool visibility.

### 9.2 Completeness Guarantee

No task fires until ALL its input slots are in `filled`. This is enforced by `compute_dag_state`, which checks `all(s in filled for s in task["inputs"])` before returning a `fire` action.

### 9.3 Idempotency Guarantee

`compute_dag_state` is a pure function of `(tasks, slots, filled, pending, task_results)`. Given the same state, it always returns the same action. The callback can be re-invoked safely — it will produce the same decision.

### 9.4 Retry Bound Guarantee

A task will fail at most `max_retries` times before the `on_exhaust` path is taken. The framework counts failures in `_retries`, and this count is never reset except by a successful execution of that same task. The same guarantee applies to:

- **Slot validation retries** (tracked under `"slot:<name>"` keys) — a slot will reject at most `max_retries` times before escalation.
- **Readback retries** (tracked under `"readback"` key) — the readback stall detector will reject pending values at most ``readback_retry.max_retries`` times before escalation. This bounds the total number of times the conversation can get stuck in a readback loop.

### 9.5 Terminal State Guarantee

Once `status` is `"complete"` or `"escalated"`, the callback's first check short-circuits: it returns `OK` without evaluating the DAG or modifying state. The conversation cannot regress to slot-filling after reaching a terminal state.

### 9.6 No-Corruption Guarantee (via Tool Visibility)

- Filled slots cannot be overwritten (setter is hidden).
- Slots whose `requires` are unmet cannot be set prematurely (setter is hidden). Slots with no unmet dependencies can be set in any order.
- Readback cannot be confirmed when nothing is pending (tool is hidden).
- Readback cannot be bypassed (all non-readback setters are hidden except for inline corrections).

### 9.7 Cascade Guarantee

When a non-terminal task succeeds and fills output slots, the framework immediately re-evaluates the DAG. If another task is now ready, it fires in the same callback invocation. The user never waits an extra turn for a task that could have fired immediately.

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
| Executing backend operations | Python | `execute_dag_step` → executor function |
| Counting retries | Python | `_retries` dict in session state |
| Deciding when to escalate | Python | `retries >= max_retries` check |
| Enforcing slot dependencies | Python | `requires` check + tool visibility |
| Preventing invalid tool calls | Python | `_compute_hidden_tools` per turn — hides filled, inactive, and dependency-blocked setters |
| Generating readback text | Python | `_build_readback` with configured formatters |
| Confirming/rejecting readback | LLM | Calls `confirm_pending` / `reject_pending` based on user response |
| Short-circuiting confirmation for clear affirmatives | Python | `_is_affirmative()` + auto-confirm block in `_run_slot_filling` — merges pending without LLM call |
| Handling slot validation errors | Python | `_handle_slot_errors` resolves message from config, tracks retries |
| Detecting readback stalls | Python | `_readback_stall` counter, reject-on-stall, retry via `readback_retry` config |
| Validating task output shape | Python | Checks declared `outputs` keys are present in executor result |
| Writing messages to state | Python | Callback adapter writes orchestrator's returned message to `sm["_system_message"]` |
| Transitioning after confirmation | Python | `_readback_transition` flag triggers preemption with next question |
| Config validation | Python | `_validate_config()` called by adapter before orchestrator — catches misconfig early |
| Executor exception safety | Python | `try/except` in `execute_dag_step` synthesizes failure result on crash |
| Detecting global progress stalls | Python | `_progress_turns` counter, escalation via `progress_stall` config |
| Observability | Python | `_log_event()` appends structured events to `_debug_log` (capped at 20) |

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
| "Only accept parties of 1-8" | Setter validates, config has error msg | `set_party_size` code + `validation.errors` |
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

To create a new agent with this framework, replace one function: `_get_config()`. This function returns a dict containing:

1. **Slots** — what data to collect, in what order, with formatters, conditions, and validation rules.
2. **Tasks** — what backend operations to fire, with inputs, outputs, success checks, and retry config.
3. **Executors** — the business logic functions keyed by task name. These receive the `filled` dict and return a result dict.
4. **Readback tools** — the tool names for confirm/reject (typically `["confirm_pending", "reject_pending"]`).
5. **Readback retry** and **progress stall** configs — escalation rules for stuck conversations.

Formatters, conditions, and executors are defined as closures inside `_get_config()`, so they can reference each other and use domain-specific imports.

Everything below `_get_config()` in the file — the callback adapter, the orchestrator, and all framework internals — is copied unchanged.

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

### 17.1 Why Pure compute_dag_state + Effectful execute_dag_step?

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

2. **No partial readback.** When pending slots exist, ALL pending values are shown in readback. You can't confirm some and leave others pending.

3. **Single-agent scope.** The framework manages one agent's flow. Multi-agent routing (e.g., transferring from a booking agent to a billing agent) is handled externally. Note: When the overall application contains multiple DAG-powered agents, you must ensure each agent's callback and setter tools use uniquely namespaced state keys (e.g., sm_booking, sm_billing) to freeze and protect their DAG state during potential handoffs.

4. **No undo after confirmation.** Once values move from pending to filled via `confirm_pending`, they can't be changed (the setter is hidden). The user would need to explicitly ask to start over, which the framework doesn't support.

---

## 19. Observability (`_debug_log`)

The framework records structured events to `sm["_debug_log"]` for post-hoc debugging. The log is a list of dicts, capped at the 20 most recent entries to bound state size.

### 19.1 The `_log_event` Helper

```python
def _log_event(sm, event, **data):
    log = sm.setdefault("_debug_log", [])
    log.append({"event": event, **data})
    if len(log) > 20:
        del log[:-20]
```

### 19.2 Events

| Event | Where | Data | Meaning |
|---|---|---|---|
| `slot_error` | `_handle_slot_errors` | `slot`, `code`, `retries` | Setter signaled invalid input |
| `slot_error_exhaust` | `_handle_slot_errors` | `slot` | Slot validation retries exhausted → escalation |
| `task_fire` | `execute_dag_step` | `task`, `success` | Executor called, result recorded |
| `task_exception` | `execute_dag_step` | `task` | Executor raised an exception (synthetic failure used) |
| `task_exhaust` | `execute_dag_step` | `task` | Task retries exhausted → escalation |
| `progress_detected` | `_run_slot_filling` | — | State change detected, stall counters reset |
| `slot_deactivated` | `_run_slot_filling` | `slot`, `source` | Conditional slot removed from filled/pending (condition became False) |
| `stall_detected` | `_run_slot_filling` | `retries` | Readback stall counter hit threshold, pending rejected |
| `progress_stall` | `_run_slot_filling` | `turns` | Global progress counter hit threshold, escalating |
| `dag_action` | `_run_slot_filling` | `action`, `task` | DAG evaluator returned an action |
| `preempt` | `_run_slot_filling` | `trigger` | LLM preempted; trigger is one of: `slot_error`, `task_fire`, `readback_transition`, `stall_exhaust`, `progress_stall` |

### 19.3 Usage

The log is useful for:

- **Debugging eval failures**: inspect the sequence of events that led to an unexpected state.
- **Tracing retry behavior**: see which slots/tasks failed, how many retries occurred, and whether escalation was triggered.
- **Verifying preemption**: confirm which preemption trigger fired and when.

The 20-entry cap means only recent events are retained. For a typical 5-slot agent, this covers 2-3 complete conversation turns — enough to diagnose most issues without unbounded state growth.
