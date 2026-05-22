---
title: "Tutorial: Building a Slot-Filling Agent"
description: Step-by-step guide to building a restaurant reservation agent using the Slot Filling DAG Framework, introducing one concept at a time.
---

# Tutorial: Building a Slot-Filling Agent

This tutorial walks you through building **Bella Notte**, a restaurant reservation agent that collects guest information, checks availability, and books a table. You'll start with two simple slots and progressively add validation, tasks, readback, and error handling — each step introducing one new concept.

---

## Before you start

Make sure you have:

- SCRAPI installed (`pip install cxas-scrapi`)
- A Google Cloud project with CX Agent Studio enabled
- An app created and pulled locally (see [Creating Agents](../agent-development/creating-agents.md))
- Familiarity with [tools](../agent-development/creating-agents.md#step-4-create-a-tool) and [callbacks](../agent-development/creating-agents.md#step-7-add-a-callback-optional)

---

## The app structure

A slot-filling agent uses four framework callbacks that you copy into your app, plus agent-specific configuration and setter tools that you write yourself:

```
cxas_app/Bella Notte/
├── app.json
├── agents/Bella_Notte_Host/
│   ├── instruction.md
│   ├── before_agent_callbacks/
│   │   └── before_agent_callbacks_01/python_code.py    # Framework (copy)
│   ├── before_model_callbacks/
│   │   └── before_model_callbacks_01/python_code.py    # Framework (copy)
│   ├── after_model_callbacks/
│   │   └── after_model_callbacks_01/python_code.py     # Framework (copy)
│   └── after_tool_callbacks/
│       └── after_tool_callbacks_01/python_code.py      # Framework (copy)
├── tools/
│   ├── bella_notte_dag/python_function/python_code.py  # Your config
│   ├── slot_filling_engine/python_function/python_code.py  # Framework (copy)
│   ├── confirm_pending/python_function/python_code.py  # Framework (copy)
│   ├── reject_pending/python_function/python_code.py   # Framework (copy)
│   ├── set_party_size/python_function/python_code.py       # Your setter
│   ├── set_preferred_date/python_function/python_code.py   # Your setter
│   ├── set_guest_name/python_function/python_code.py       # Your setter
│   ├── set_special_requests/python_function/python_code.py # Your setter
│   └── end_session/...                                 # Platform built-in
```

The four callbacks, the engine tool, and the confirm/reject tools are **framework code** — you copy them unchanged into every slot-filling agent. The `bella_notte_dag` tool and the setter tools are **your code** — you write them for each agent.

!!! tip "Multi-slot setters"
    The reference implementation uses multi-slot setters (`set_reservation_basics`, `set_guest_info`) that handle related fields together. This tutorial starts with individual setters for clarity — see [Advanced Patterns](advanced.md) for the multi-slot approach.

!!! tip "Framework files"
    The framework files (engine, callbacks, confirm/reject) are available in the `examples/bella_notte/` directory of the SCRAPI repository. Copy them into your app and they work as-is.

---

## Step 1: Two simple slots

Let's start with the simplest possible slot-filling agent — one that collects a party size and a guest name, then ends the conversation. No validation, no tasks, no readback.

### The DAG config

The `bella_notte_dag` tool returns a Python dictionary that defines your slots and tasks. Here's the minimal version:

```python
# tools/bella_notte_dag/python_function/python_code.py

from typing import Any


def bella_notte_dag() -> dict[str, Any]:
    """Return the DAG config for the Bella Notte Host."""
    return {
        "slots": [
            {
                "name": "party_size",
                "source": "user",
                "setter": "set_party_size",
                "ask": "How many guests will be dining?",
            },
            {
                "name": "guest_name",
                "source": "user",
                "setter": "set_guest_name",
                "ask": "What name should I put the reservation under?",
            },
        ],
        "tasks": [],
    }
```

Each slot declares:

- **`name`** — a unique identifier used throughout the framework
- **`source`** — where the value comes from (`"user"` means the user provides it)
- **`setter`** — the tool the LLM calls to record the value
- **`ask`** — the question the framework tells the LLM to ask

**Slot order matters.** The framework asks for the first unfilled slot in declaration order. Here, it will ask for `party_size` first, then `guest_name`.

### The setter tools

Setter tools are pure validators. They receive input, validate it, and return a structured result. They never access framework state — that's the callback's job.

```python
# tools/set_party_size/python_function/python_code.py

def set_party_size(party_size: str) -> dict:
    """Record the number of guests for the reservation.

    Convert the user's input to a number. For example, "four" should
    be converted to 4 before calling this tool.
    """
    try:
        count = int(party_size)
    except (ValueError, TypeError):
        return {"error": True, "error_code": "parse_error"}

    return {"stored": True, "value": count}
```

```python
# tools/set_guest_name/python_function/python_code.py

def set_guest_name(guest_name: str) -> dict:
    """Record the name for the reservation.

    Accept any format the user provides — first name, full name,
    nickname. Do not ask for a specific format.
    """
    name = str(guest_name).strip()
    if not name:
        return {"error": True, "error_code": "empty_name"}

    return {"stored": True, "value": name}
```

The pattern is always the same:

- **Success:** return `{"stored": True, "value": <the_value>}`
- **Error:** return `{"error": True, "error_code": "<code>"}`

The LLM reads the tool's docstring to understand what arguments to pass. Write docstrings that tell the LLM how to prepare the input — "convert 'four' to 4", "accept any format".

!!! info "Why setters don't access state"
    Setters are intentionally stateless. They validate one value and return a result. The `after_tool_callback` reads the result and routes it into the framework's state dictionary. This separation keeps setters simple, testable, and reusable.

### What happens at runtime

Here's the conversation flow with this minimal config:

```
User: Hi, I'd like to make a reservation.

  Engine evaluates → party_size is first unfilled slot
  Engine tells LLM: "Ask: How many guests will be dining?"
  LLM generates: "Welcome! How many guests will be dining?"

User: 4

  LLM calls set_party_size("4")
  Callback routes result → filled["party_size"] = 4
  Engine evaluates → guest_name is next unfilled slot
  Engine tells LLM: "Ask: What name should I put the reservation under?"
  LLM generates: "And what name should I put the reservation under?"

User: Maria

  LLM calls set_guest_name("Maria")
  Callback routes result → filled["guest_name"] = "Maria"
  Engine evaluates → all slots filled, no tasks → all_done
```

At this point all slots are filled. Without any tasks, the conversation is complete.

---

## Step 2: Add validation

Right now, a user could say "a million" and the setter would happily store `1000000`. Let's add validation rules with error messages and retry limits.

Update the `party_size` slot in your DAG config:

```python
{
    "name": "party_size",
    "source": "user",
    "setter": "set_party_size",
    "ask": "How many guests will be dining?",
    "validation": {
        "max_retries": 3,
        "errors": {
            "out_of_range": (
                "We accept reservations for parties of 1 to 8."
                " For larger parties, please contact our events"
                " team at events@bellanotte.com."
            ),
            "parse_error": (
                "I didn't catch the number of guests."
                " How many will be dining?"
            ),
        },
        "on_exhaust": {
            "say": (
                "I'm having trouble with the party size."
                " Please call us at 555-0100."
            ),
            "then": {
                "tool": "end_session",
                "args": {"reason": "retry_exhausted"},
            },
        },
    },
},
```

And update the setter to return the right error codes:

```python
def set_party_size(party_size: str) -> dict:
    """Record the number of guests (1-8).

    Convert the user's input to a number. For example, "four" should
    be converted to 4 before calling this tool.
    """
    try:
        count = int(party_size)
    except (ValueError, TypeError):
        return {"error": True, "error_code": "parse_error"}

    if count < 1 or count > 8:
        return {"error": True, "error_code": "out_of_range"}

    return {"stored": True, "value": count}
```

### How validation works

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-validation.svg" alt="Validation flow: setter returns error code, framework looks up error message from config, increments retry counter, delivers message to user. After max_retries, escalates.">
  <figcaption>The validation cycle — setters signal errors with codes, the framework handles messaging and retry counting.</figcaption>
</figure>

1. The setter returns `{"error": True, "error_code": "out_of_range"}`
2. The `after_tool_callback` sees the error and appends it to `_slot_errors`
3. The engine's `_handle_slot_errors()` function:
    - Looks up `"out_of_range"` in the slot's `validation.errors` config
    - Increments the retry counter for this slot
    - Returns the error message as a preempted response (the LLM doesn't run)
4. If retries reach `max_retries`, the `on_exhaust` config fires — delivering the escalation message and calling the specified tool

The error codes connect the setter to the config. The setter says *what* went wrong (`"out_of_range"`); the config says *how to respond* (the message text). This means you can change error messages without touching setter code.

!!! tip "Error message placeholders"
    Error messages support `{slot_name}` placeholders that resolve to filled slot values. For example: `"That time isn't available. We have {available_times}."` This is useful for validation errors that need to reference other collected data.

---

## Step 3: Add readback

For a restaurant reservation, you want to confirm critical details before proceeding. Readback makes the agent repeat values back to the user for confirmation.

Add `requires_readback` and `readback_fmt` to your slots:

```python
{
    "name": "party_size",
    "source": "user",
    "setter": "set_party_size",
    "ask": "How many guests will be dining?",
    "requires_readback": True,
    "readback_fmt": {
        "type": "plural",
        "one": "guest",
        "other": "guests",
    },
    "validation": { ... },  # same as before
},
{
    "name": "guest_name",
    "source": "user",
    "setter": "set_guest_name",
    "ask": "What name should I put the reservation under?",
    "requires_readback": True,
    "readback_fmt": {
        "type": "prefix",
        "text": "under the name",
    },
},
```

### The readback lifecycle

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-lifecycle.svg" alt="Slot lifecycle: unfilled → pending (via setter) → filled (via confirm) or back to unfilled (via reject). Non-readback slots skip pending and go directly to filled.">
  <figcaption>The slot lifecycle — readback slots pass through "pending" for confirmation. Non-readback slots promote directly to "filled".</figcaption>
</figure>

When a setter succeeds for a readback slot:

1. The value goes into **`pending`**, not `filled`
2. The engine sees pending values and enters **readback mode**
3. The LLM reads back the value: *"So that's 4 guests, is that correct?"*
4. The user responds:
    - **"Yes"** → `confirm_pending` fires, value moves to `filled`
    - **"No, 5"** → LLM calls `set_party_size("5")`, new value replaces pending
    - **"No"** → `reject_pending` fires, value cleared, slot re-entered

### Readback formats

The `readback_fmt` field controls how the value is presented during readback. Several built-in formats are available:

| Format | Config | Example output |
|--------|--------|----------------|
| Plural | `{"type": "plural", "one": "guest", "other": "guests"}` | "4 guests" |
| Prefix | `{"type": "prefix", "text": "under the name"}` | "under the name Maria" |
| Date | `"date"` | "Friday, June 19th" |
| Time | `"time"` | "7:30 PM" |
| None-sub | `{"type": "none_sub", "default": "no special requests"}` | "no special requests" (when value is "none") |
| Raw | (omit `readback_fmt`) | The raw value as-is |

### Auto-confirm

When the user responds to a readback with a simple affirmative — "yes", "yeah", "correct", "that's right" — the framework short-circuits and confirms automatically without running the LLM. This makes confirmations fast and deterministic.

The auto-confirm detects single-word affirmatives and short phrases (up to 5 words) that don't contain correction signals like "but", "actually", "wait", or "not".

### Inline confirm

Sometimes the user confirms *and* provides new information in the same message: *"Yes, and I have a shellfish allergy."* The framework detects this pattern — it silently confirms the pending values and lets the LLM process the new content in collection mode.

---

## Step 4: Add a task

So far our agent collects data but doesn't do anything with it. Let's add a backend task that checks availability based on the party size and date.

First, add a `preferred_date` slot and an `available_times` task-sourced slot:

```python
"slots": [
    {
        "name": "party_size",
        "source": "user",
        "setter": "set_party_size",
        "ask": "How many guests will be dining?",
        "requires_readback": True,
        "readback_fmt": {"type": "plural", "one": "guest", "other": "guests"},
        "validation": { ... },
    },
    {
        "name": "preferred_date",
        "source": "user",
        "setter": "set_preferred_date",
        "ask": "What date would you like to come in?",
        "requires_readback": True,
        "readback_fmt": "date",
        "validation": {
            "max_retries": 3,
            "errors": {
                "invalid_format": (
                    "Could you provide the date?"
                    " For example, 2026-06-17 for June 17th."
                ),
                "past_date": (
                    "That date is in the past."
                    " Could you provide a future date?"
                ),
            },
            "on_exhaust": {
                "say": "I'm having trouble with the date. Please call us at 555-0100.",
                "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
            },
        },
    },
    {
        "name": "available_times",
        "source": "task:FindAvailableTimes",
    },
    {
        "name": "guest_name",
        "source": "user",
        "setter": "set_guest_name",
        "ask": "What name should I put the reservation under?",
        "requires_readback": True,
        "readback_fmt": {"type": "prefix", "text": "under the name"},
    },
],
```

Now add the task:

```python
"tasks": [
    {
        "name": "FindAvailableTimes",
        "tool": "find_available_times",
        "inputs": ["party_size", "preferred_date"],
        "outputs": {"available_times": "available_times"},
        "success_check": "success",
        "then_say": (
            "Great choice! We have availability at {available_times}."
            " Which time works best for you?"
        ),
        "on_failure": {
            "retry_say": (
                "We don't have availability for that date and"
                " party size. Could you try a different date?"
            ),
            "max_retries": 1,
            "clear_slots": ["preferred_date"],
            "on_exhaust": {
                "say": "I'm unable to find availability. Please call us at 555-0100.",
                "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
            },
        },
    },
],
```

And the executor tool:

```python
# tools/find_available_times/python_function/python_code.py

import json
from typing import Any


def find_available_times(party_size: int, preferred_date: str) -> dict[str, Any]:
    """Check restaurant availability for the given party size and date."""
    # In a real agent, this calls your reservation backend
    available = ["6:00 PM", "7:30 PM", "9:00 PM"]

    if not available:
        return {"success": False, "error": "no_availability"}

    return {
        "success": True,
        "available_times": ", ".join(available),
    }
```

### How tasks fire

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-task-fire.svg" alt="Task firing: when all input slots are in filled state, the engine fires the task automatically via a preempted function call. Task results map to output slots.">
  <figcaption>Tasks fire automatically when all inputs are filled — the engine preempts the LLM with the tool call.</figcaption>
</figure>

The engine checks task readiness on every turn, in declaration order:

1. Are all `inputs` in `filled`? (conditional inputs that are inactive are skipped)
2. Has this task already succeeded?
3. If ready and not yet succeeded → **fire**

When a task fires, the engine:

- Preempts the LLM with a `function_call` to the task's tool
- Passes `filled` slot values as arguments
- On success (`success_check` is truthy in the result), maps `outputs` to `filled` slots
- On failure, follows the `on_failure` config

### Task failure and retry

The `on_failure` block controls what happens when a task fails:

```python
"on_failure": {
    "retry_say": "We don't have availability. Could you try a different date?",
    "max_retries": 1,
    "clear_slots": ["preferred_date"],
    "on_exhaust": {
        "say": "I'm unable to find availability. Please call us.",
        "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
    },
},
```

- **`retry_say`** — the message shown to the user on failure
- **`max_retries`** — how many times to retry before escalating
- **`clear_slots`** — which slots to clear, sending the user back to re-enter them
- **`on_exhaust`** — what to do when retries run out

There are two retry modes:

1. **Re-collect retry** (`clear_slots` specified) — clears the named slots and asks the user for them again. The user gets a fresh chance with different inputs.
2. **Same-input retry** (no `clear_slots`) — retries the task with the same inputs on the next engine pass. Useful for transient backend errors.

---

## Step 5: Slot dependencies

The `available_times` slot is filled by a task, but what about `selected_time` — the slot where the user picks one of the available times? It should only be asked *after* availability is known.

Add `selected_time` with a `requires` dependency:

```python
{
    "name": "selected_time",
    "source": "user",
    "setter": "set_selected_time",
    "requires": ["available_times"],
    "ask": (
        "We have availability at {available_times}."
        " Which time works best for you?"
    ),
    "requires_readback": True,
    "readback_fmt": "time",
    "validation": {
        "max_retries": 3,
        "errors": {
            "prereq_not_met": (
                "I need to check availability first."
                " How many guests, and what date?"
            ),
            "not_available": (
                "That time isn't available."
                " We have {available_times} — would any of those work?"
            ),
        },
        "on_exhaust": {
            "say": "I'm having trouble with time selection. Please call us at 555-0100.",
            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
        },
    },
},
```

### How dependencies work

The `requires` field lists slots that must be in `filled` before this slot is asked or its setter is visible:

- **Question gating:** The engine skips `selected_time` when finding the next question if `available_times` isn't filled yet
- **Tool visibility:** `set_selected_time` is hidden from the LLM until `available_times` is filled
- **Prerequisite check:** If the LLM somehow calls `set_selected_time` before `available_times` is filled, the `after_tool_callback` catches it and routes to the `"prereq_not_met"` error

### Cross-slot validation

Notice the `validate_against` field — it lets you validate one slot's value against another slot's data:

```python
{
    "name": "selected_time",
    ...
    "validate_against": {
        "response_field": "display_value",
        "filled_slot": "available_times",
        "error_code": "not_available",
    },
},
```

This checks whether the setter's `display_value` output appears in the filled `available_times` value. If the user picks "8:00 PM" but only "6:00 PM, 7:30 PM, 9:00 PM" are available, the framework rejects it with the `"not_available"` error message.

!!! info "Question placeholders"
    The `ask` field supports `{slot_name}` placeholders. When the engine builds the question, it substitutes filled slot values. So `"We have {available_times}"` becomes `"We have 6:00 PM, 7:30 PM, 9:00 PM"`.

---

## Step 6: The terminal task

Now let's add the booking task — the final step that creates the reservation. This task takes all collected data and returns a confirmation number.

Add a `confirmation_number` slot and the `BookReservation` task:

```python
# In the slots list:
{
    "name": "special_requests",
    "source": "user",
    "setter": "set_special_requests",
    "ask": "Do you have any special requests or dietary needs?",
    "requires_readback": True,
    "readback_fmt": {"type": "none_sub", "default": "no special requests"},
},
{
    "name": "confirmation_number",
    "source": "task:BookReservation",
},

# In the tasks list:
{
    "name": "BookReservation",
    "tool": "book_reservation",
    "inputs": [
        "party_size",
        "preferred_date",
        "selected_time",
        "guest_name",
        "special_requests",
    ],
    "outputs": {"confirmation_number": "confirmation_number"},
    "success_check": "success",
    "terminal": True,
    "then_say": (
        "Your reservation is confirmed!"
        " Your confirmation number is {confirmation_number}."
        " We look forward to welcoming you to Bella Notte!"
    ),
    "on_failure": {
        "retry_say": "I'm having trouble completing the reservation. Let me try once more.",
        "max_retries": 2,
        "on_exhaust": {
            "say": (
                "I wasn't able to complete your reservation."
                " Please call us at 555-0100."
            ),
            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
        },
    },
},
```

### Terminal tasks

Setting `"terminal": True` means: when this task succeeds, the conversation is complete. The framework sets `status` to `"complete"` and short-circuits all future callback invocations.

The `then_say` message is delivered to the user with `{confirmation_number}` replaced by the actual value from the task result.

### Deferred readback

For the booking task, you want to confirm *all* reservation details together — not one at a time. That's what `readback_inputs` does:

```python
{
    "name": "BookReservation",
    ...
    "readback_inputs": True,
    ...
},
```

When `readback_inputs` is `True`, the framework **defers** readback for the task's input slots. Instead of confirming each value individually as it's collected, the framework silently accumulates them. Once all inputs are ready, it presents them all at once for group confirmation:

```
Agent: Let me confirm your reservation details:
       4 guests, Friday June 19th, at 7:30 PM,
       under the name Maria, no special requests.
       Is everything correct?
```

!!! tip "Deferred vs. individual readback"
    Use `readback_inputs: True` on terminal tasks where you want a summary confirmation. Use individual readback (just `requires_readback: True` on slots) when each value should be confirmed as it's collected — useful when values feed into non-terminal tasks like availability checks.

---

## Step 7: Global configuration

The DAG config supports global settings that control conversation drift recovery and confirmation transitions:

```python
def bella_notte_dag() -> dict[str, Any]:
    return {
        "slots": [ ... ],
        "tasks": [ ... ],

        "confirm_transition_prefix": [
            "Wonderful!", "Perfect!", "Great!",
            "Excellent!", "Lovely!",
        ],

        "steer_back": {
            "soft_after": 2,
            "hard_after": 4,
            "escalate_after": 6,
            "on_exhaust": {
                "say": (
                    "I'm having trouble completing your reservation."
                    " Please call us at 555-0100."
                ),
                "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
            },
        },
    }
```

- **`confirm_transition_prefix`** — when the user confirms a readback and the engine moves to the next question, it picks a random prefix from this list to start the transition: *"Wonderful! What date would you like to come in?"*

- **`steer_back`** — 3-tier recovery for off-topic conversations. After `soft_after` turns of no progress, the engine injects a steering directive in the system instruction. After `hard_after` turns, it preempts the LLM and forces the next question. After `escalate_after` turns, it escalates.

---

## Step 8: The instruction file

The agent's instruction file works with the framework through static variables. The framework injects a `{{slot_filling_protocol}}` block into the instruction at runtime. There is no separate `{{readback_protocol}}` variable — the `before_model_callback` dynamically switches between collection and readback protocol content within the single block via `_build_phase_suffix`.

```xml
<!-- agents/Bella_Notte_Host/instruction.md -->

<role>
You are a friendly host at Bella Notte, an Italian restaurant. Your job
is to help guests make dinner reservations.
</role>

<persona>
You are warm, professional, and efficient. You speak naturally but stay
focused on completing the reservation. You never make up information
about availability or confirmation numbers.
</persona>

<taskflow>
  {{slot_filling_protocol}}
</taskflow>
```

The `{{slot_filling_protocol}}` variable is populated by the `before_agent_callback` with instructions that tell the LLM how to use the setter tools, how to handle readback, and what the current slot ordering is. You don't write these instructions — the framework generates them from your DAG config.

!!! warning "Don't duplicate framework constraints in your instruction"
    If the framework enforces a constraint (like "don't ask for date before party size"), don't also write it in your instruction. Duplicate constraints cause the LLM to second-guess the framework, leading to worse behavior.

---

## The complete DAG

Here's the full DAG for the Bella Notte agent you've built:

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-complete-dag.svg" alt="Complete DAG showing all slots and tasks for the Bella Notte reservation agent, from party_size through BookReservation.">
  <figcaption>The complete Bella Notte reservation DAG — seven slots, two tasks, and one conditional branch.</figcaption>
</figure>

```
User slots:     party_size → preferred_date → selected_time → guest_name → special_requests
                     │              │               ↑
                     └──────────────┘               │
                            ↓                       │
Task:           FindAvailableTimes ──→ available_times
                                                    │
All slots ──────────────────────────────────────────┘
    ↓
Task:           BookReservation ──→ confirmation_number (terminal)
```

---

## A complete conversation

Here's what a full conversation looks like with the finished agent:

```
User:  Hi, I'd like to book a table.
Agent: Welcome to Bella Notte! How many guests will be dining?

User:  4 people
Agent: So that's 4 guests — is that right?

User:  Yes
Agent: Perfect! What date would you like to come in?

User:  This Friday
Agent: That's Friday, June 19th — correct?

User:  Yep
Agent: Great choice! We have availability at 6:00 PM, 7:30 PM, and 9:00 PM.
       Which time works best for you?

User:  7:30
Agent: 7:30 PM — does that work?

User:  Yes
Agent: Wonderful! What name should I put the reservation under?

User:  Maria Chen
Agent: Under the name Maria Chen — correct?

User:  Yes, and no special dietary needs
Agent: Let me confirm your reservation: 4 guests, Friday June 19th,
       at 7:30 PM, under the name Maria Chen, no special requests.
       Is everything correct?

User:  Looks good!
Agent: Your reservation is confirmed! Your confirmation number is BN-20260619-001.
       We look forward to welcoming you to Bella Notte!
```

---

## What's next

You now have a fully functional slot-filling agent. From here:

- [Advanced Patterns](advanced.md) — conditional slots, event pre-filling, announce slots, and engine internals
- [Configuration Reference](reference.md) — complete field reference for every config option
