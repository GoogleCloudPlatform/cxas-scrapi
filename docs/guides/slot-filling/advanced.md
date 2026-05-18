---
title: Advanced Patterns
description: Conditional slots, event pre-filling, announce slots, deferred readback groups, and engine internals for slot-filling agents.
---

# Advanced Patterns

The [tutorial](tutorial.md) covered the core slot-filling workflow — slots, tasks, validation, readback, and dependencies. This page covers patterns for more complex scenarios: conditional branches, event-driven pre-filling, announcement slots, grouped confirmation, and the engine's safety mechanisms.

All examples continue building on the Bella Notte reservation agent from the tutorial.

---

## Conditional slots

Sometimes you need a slot only under certain conditions. Bella Notte requires a contact phone number for large parties (5 or more guests) but not for smaller ones. A **conditional slot** appears or disappears based on the values of other filled slots.

```python
{
    "name": "large_party_phone",
    "source": "user",
    "setter": "set_large_party_phone",
    "condition": "lambda filled: int(filled.get('party_size', 0)) >= 5",
    "ask": (
        "For parties of 5 or more, we require a contact phone number"
        " in case we need to reach you. What's the best number?"
    ),
    "requires_readback": True,
    "readback_fmt": {"type": "prefix", "text": "contact phone"},
    "validation": {
        "max_retries": 3,
        "errors": {
            "invalid_phone": (
                "Could you provide a phone number with at least 7 digits?"
            ),
        },
        "on_exhaust": {
            "say": "I'm having trouble with the phone number. Please call us at 555-0100.",
            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
        },
    },
},
```

### How conditions work

The `condition` field is a lambda string that receives the current `filled` dictionary and returns a boolean. The engine evaluates it on every turn.

**When a condition is `False`:**

- The slot's setter tool is **hidden** from the LLM
- The engine **skips** the slot when finding the next question
- Tasks that list the slot as an input treat it as **satisfied** (inactive slots don't block)
- If the slot was previously filled and the condition becomes `False`, the value is **auto-cleared** from `filled`, `pending`, and `deferred`

That last point is important. If the user initially says "6 guests" (activating `large_party_phone`), provides a phone number, but then corrects party size to "3" — the framework automatically removes the phone number from state because the condition is no longer met.

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-conditional.svg" alt="Conditional slot activation: party_size >= 5 activates large_party_phone slot, party_size < 5 deactivates and auto-clears it.">
  <figcaption>Conditional slots activate and deactivate based on other filled values — auto-clearing when deactivated.</figcaption>
</figure>

!!! tip "Condition best practices"
    - Always use `filled.get('slot_name', default)` with a safe default — the slot may not be filled yet
    - Keep conditions simple — they run on every turn
    - Use `int()` or `str()` casts when comparing values that may arrive as different types

### Conditional tasks

Tasks can also have conditions. A conditional task only fires when its condition is `True` *and* all its inputs are filled:

```python
{
    "name": "NotifyEventsTeam",
    "tool": "notify_events_team",
    "condition": "lambda filled: int(filled.get('party_size', 0)) >= 10",
    "inputs": ["party_size", "preferred_date", "guest_name"],
    "outputs": {},
    "success_check": "success",
    "then_say": "I've notified our events team about your large party.",
},
```

---

## Announce slots

Sometimes you need the agent to deliver a specific message — a greeting, a legal disclosure, or a policy statement — without collecting any data. **Announce slots** handle this.

```python
{
    "name": "welcome",
    "source": "announce",
    "message": "Welcome to Bella Notte! I'd be happy to help you with a reservation.",
    "preempt": False,
},
```

Announce slots have a `source` of `"announce"` and a `message` field instead of a `setter` and `ask`. They fire in declaration order alongside other slots.

### Preempt vs. non-preempt

- **`preempt: False`** (default) — the message is included in the system instruction, and the LLM wraps it in natural language. The LLM might combine it with other content, rephrase it slightly, or add a greeting.
- **`preempt: True`** — the message is delivered verbatim as a preempted response. The LLM doesn't run. Use this for legally required disclosures where exact wording matters.

```python
{
    "name": "recording_disclosure",
    "source": "announce",
    "message": "This call may be recorded for quality assurance purposes.",
    "preempt": True,
},
```

### Announce cascading

Multiple consecutive announce slots fire in a single engine invocation. If you have a welcome message followed by a disclosure, both fire before the first user-sourced slot is asked — the user doesn't need to respond between them.

### Gating tasks on announcements

Use task `requires` (not `inputs`) to gate a task on an announce slot being delivered:

```python
# Slot
{
    "name": "terms_disclosure",
    "source": "announce",
    "message": "By proceeding, you agree to our cancellation policy...",
    "preempt": True,
},

# Task
{
    "name": "BookReservation",
    ...
    "requires": ["terms_disclosure"],
    ...
},
```

The `requires` field on tasks lists slots that must be filled (gates) but aren't passed as arguments. The booking task won't fire until the disclosure has been delivered — even if all `inputs` are ready.

---

## Event-driven pre-filling

When your agent receives calls through a telephony integration or a web form, some data may already be known before the conversation starts. **Event sources** let you pre-fill slots from external data.

### Configuring event slots

Declare a slot with multiple sources in priority order:

```python
{
    "name": "party_size",
    "source": ["event", "user"],
    "event_key": "party_size",
    "setter": "set_reservation_basics",
    "setter_field": "party_size",
    "ask": "How many guests will be dining?",
    ...
},
```

The `source` field is a list. The framework tries sources in order:

1. **`"event"`** — check `event_data` for a key matching `event_key`
2. **`"user"`** — if no event data, ask the user via the setter tool

### Injecting event data

Pass event data when starting a session:

```python
from cxas_scrapi.core.sessions import Sessions

sessions = Sessions(app_name=app_name)

response = sessions.run(
    agent_name=agent_name,
    message="I'd like to make a reservation.",
    variables={
        "event_data": {
            "party_size": 4,
            "guest_name": "Maria Chen",
        },
    },
)
```

The `before_agent_callback` reads `event_data` from session variables and fills matching slots directly to `filled` — no readback needed, because event data is authoritative (it came from your system, not from voice recognition).

### Partial pre-filling

If only some slots have event data, the framework fills what it can and asks the user for the rest. The conversation starts further along, skipping questions the system already knows the answer to.

---

## Deferred readback groups

The tutorial introduced `readback_inputs: True` on the `BookReservation` task. This section explains the full mechanics of deferred readback.

### The problem

Individual readback confirms each value as it's collected:

```
Agent: 4 guests — correct?          ← readback #1
User:  Yes
Agent: Friday, June 19th — right?   ← readback #2
User:  Yes
Agent: 7:30 PM — correct?           ← readback #3
User:  Yes
```

This is verbose. For a terminal task with many inputs, you want a single grouped confirmation:

```
Agent: Let me confirm: 4 guests, Friday June 19th, 7:30 PM,
       under the name Maria, no special requests. All correct?
User:  Yes
```

### How deferred readback works

<figure class="diagram">
  <img src="../../assets/diagrams/slot-filling-deferred.svg" alt="Deferred readback: values accumulate in 'deferred' state instead of 'pending'. When all inputs for a readback_inputs task are collected, they promote to pending together for grouped confirmation.">
  <figcaption>Deferred readback — values accumulate silently until the group is complete, then promote for one combined confirmation.</figcaption>
</figure>

1. A slot has `requires_readback: True`
2. Its owning task has `readback_inputs: True`
3. When the setter succeeds, instead of going to `pending`, the value goes to **`deferred`**
4. The framework collects values silently — no readback prompt between slots
5. When all user-sourced inputs for the task are in `deferred`, the group **completes**
6. All deferred values promote to `pending` at once
7. The LLM presents a single grouped readback with all values

### Eligibility rules

A slot is deferred-eligible only when ALL four conditions hold:

1. `requires_readback: True` on the slot
2. Input to at least one task with `readback_inputs: True`
3. NOT input to any incomplete task WITHOUT `readback_inputs` (prevents deferring a value that's needed by a non-deferred task)
4. No task-sourced `requires` (the slot can't depend on task output — those values aren't stable enough to defer)

Slots that don't meet all four conditions fall back to individual readback.

### System instruction hints

The framework communicates deferred state to the LLM via XML tags in the system instruction:

- **`<deferred_collection>`** — when values just routed to deferred, tells the LLM "these values were noted, proceed to the next question without reading them back"
- **`<readback_scope>`** — when values promoted from deferred to pending, uses a stronger format with slot labels to ensure the LLM reads back all values

---

## Tool visibility

One of the framework's most important control mechanisms is **tool visibility** — hiding and showing tools based on the current state. The LLM can only call tools it can see, making invalid operations impossible without relying on prompt instructions.

### Visibility rules

**During collection (no pending values):**

| Tool type | Visible when | Hidden when |
|-----------|-------------|-------------|
| Setter for unfilled slot | Dependencies met, condition active | Deps unmet, condition inactive, or already filled |
| `confirm_pending` | Never (nothing to confirm) | Always |
| `reject_pending` | Never (nothing to reject) | Always |
| Executor tools | Never (tasks fire via engine) | Always |

**During readback (pending values exist):**

| Tool type | Visible when | Hidden when |
|-----------|-------------|-------------|
| `confirm_pending` | Always (user can accept) | Fresh pending (force readback first) |
| `reject_pending` | Always (user can reject) | Fresh pending (force readback first) |
| Setter for pending slot | Always (inline correction) | — |
| Setter for other unfilled slots | Dependencies met, condition active | Deps unmet or inactive |
| Setter for filled slots | Never | Always |
| Executor tools | Never | Always |

**Fresh pending detection:** When a setter just created pending values (prior turn had no pending, this turn does), the framework hides `confirm_pending` and `reject_pending` for one cycle. This forces the LLM to read back the value before the user can confirm — preventing the LLM from silently accepting without presenting the value.

---

## Preemption

When the framework knows exactly what to say, it skips the LLM entirely. This is called **preemption** — the engine returns a pre-built response instead of letting the LLM generate one.

### When the engine preempts

| Trigger | What happens |
|---------|-------------|
| Task ready to fire | Engine returns `function_call` to the executor tool |
| Auto-confirm | Pure affirmative during readback → `confirm_pending` fires |
| Validation error | Setter returned error → config-driven error message |
| Readback transition | After confirm, next question delivered with transition prefix |
| Steer-back (hard) | Off-topic for `hard_after` turns → forced re-ask |
| Steer-back (escalate) | Off-topic for `escalate_after` turns → escalation |
| Announce (preempt) | Announce slot with `preempt: True` → verbatim message |

### First-turn guard

The engine never preempts on the very first turn of a conversation. This lets the LLM generate a natural greeting that incorporates the framework's first question, rather than starting with a canned response.

---

## Rich Response Payloads

The framework can deliver **rich response parts** — info cards, suggestion chips, description panels — alongside the LLM's text output. These are declared in the DAG config and delivered automatically.

### Where response fields go

| Config location | Field | When delivered |
|----------------|-------|---------------|
| Slot definition | `response` | When the slot's question is asked |
| Announce slot | `response` | When the announce message is delivered |
| Task definition | `then_response` | When the task succeeds |
| Validation errors | `error_responses` | When a specific error code fires |

### Response part format

Each response part is a dictionary with a `type` field:

```python
"response": [
    {"type": "text", "text": "Welcome! How can I help?"},
    {"type": "payload", "data": {
        "richContent": [[{
            "type": "info",
            "title": "Bella Notte",
            "subtitle": "Restaurant Reservations",
        }]],
    }},
]
```

| Type | Description |
|------|-------------|
| `text` | Plain text part. Delivered as `Part.from_text()`. |
| `payload` | Structured payload (cards, chips). Delivered as `Part.from_json()`. |
| `audio` | Audio URI. Delivered as `Part.from_audio()`. |
| `end_session` | Ends the session. Delivered as `Part.from_end_session()`. |
| `transfer` | Transfers to another agent. Delivered as `Part.from_agent_transfer()`. |

### Two delivery paths

Response parts take different paths depending on whether the engine preempts:

```
                     ┌─────────────┐
                     │  DAG config │
                     │  response   │
                     └──────┬──────┘
                            │
                   ┌────────┴────────┐
                   │                 │
              Preempted?         Not preempted
                   │                 │
           ┌──────┴──────┐   ┌──────┴──────┐
           │before_model  │   │  Engine     │
           │builds Parts  │   │  stashes in │
           │and returns   │   │  sm state   │
           │LlmResponse   │   └──────┬──────┘
           └─────────────┘          │
                              ┌──────┴──────┐
                              │after_model   │
                              │reads stash,  │
                              │appends Parts │
                              │to LLM output │
                              └─────────────┘
```

**Preempted turns** (validation errors, readback transitions, task execution): The `before_model_callback` builds CES `Part` objects directly from the response list and returns them as an `LlmResponse`.

**Non-preempted turns** (first question, LLM-generated responses): The engine stashes response parts in `sm["_pending_payloads"]` or `sm["_pending_question_payloads"]`. The `after_model_callback` reads the stash and appends the parts to the LLM's output.

### Question payload injection

For user-sourced slots, question payloads are only injected when:

1. There are no announce payloads in the same turn (announce takes priority)
2. The target slot is still unfilled (the LLM didn't call the setter in this response)

This prevents duplicate chips when the user answers a question before seeing the chips.

### Variable substitution

Response parts support `{slot_name}` placeholders, resolved from `filled` values:

```python
"then_response": [
    {"type": "text", "text": "Confirmed! #{confirmation_number}"},
    {"type": "payload", "data": {
        "richContent": [[{
            "type": "info",
            "title": "Reservation Confirmed",
            "subtitle": "#{confirmation_number}",
            "text": "{party_size} guests on {preferred_date}",
        }]],
    }},
]
```

### Channel-aware responses

Use `channel_responses` on tasks to deliver different payloads per channel:

```python
"channel_responses": {
    "web": [
        {"type": "payload", "data": {"richContent": [[...]]}},
    ],
    "voice": [
        {"type": "text", "text": "Your confirmation number is..."},
    ],
},
```

The engine checks `sm.get("channel", "")` against the keys. If a matching channel is found, that response replaces the default `then_response`.

### Multi-model-call guard

The `after_model_callback` includes a guard to prevent duplicate payload injection. It checks for any prior agent output in the current turn — if the agent already produced text, tool calls, or payloads, the callback skips injection. This handles scenarios where the LLM makes multiple model calls in a single turn (e.g., calling a setter then generating text).

---

## Steer-back: conversation drift recovery

The framework includes a 3-tier mechanism for conversations that go off-topic — the **steer-back** system. It uses a single counter (`_steer_back_turns`) that increments on every user turn that doesn't produce forward progress, and resets to 0 when the conversation advances.

### The three tiers

| Tier | Trigger | Behavior | LLM runs? |
|------|---------|----------|-----------|
| Soft | `soft_after` turns (default 2) | Inject `<steer_back>` directive in system instruction | Yes — LLM incorporates guidance |
| Hard | `hard_after` turns (default 4) | Preempt with next question or readback re-ask | No — verbatim message |
| Escalate | `escalate_after` turns (default 6) | Fire `on_exhaust` (end session, transfer) | No — escalation |

### Configuration

```python
"steer_back": {
    "soft_after": 2,
    "hard_after": 4,
    "escalate_after": 6,
    "on_exhaust": {
        "say": "I'm having trouble completing your reservation. Please call us at 555-0100.",
        "then": {"tool": "end_session", "args": {"reason": "steer_back_exhausted"}},
    },
},
```

### How it works

1. On each user turn with non-empty text, `_steer_back_turns` increments.
2. **Soft:** When `_steer_back_turns >= soft_after`, the engine returns a `steer_back_directive` — a natural-language instruction like "The conversation has drifted — steer back. Ask for the date." The `before_model_callback` appends it as a `<steer_back>` tag in the system instruction. The LLM runs normally and can incorporate the guidance.
3. **Hard:** When `_steer_back_turns >= hard_after`, the engine preempts. If pending values exist, it re-asks the readback confirmation. If not, it preempts with the next question. After a hard preempt, the engine sets `_hard_steer_yielded = True` — on the next user turn, the engine yields (doesn't increment the counter), letting the LLM process the user's response to the forced question.
4. **Escalate:** When `_steer_back_turns >= escalate_after`, the engine fires `on_exhaust` — delivering the escalation message and calling the configured tool (typically `end_session`).
5. The counter resets to 0 whenever forward progress occurs: a new slot is filled, a value is confirmed, a task completes, etc.

This replaces the older `readback_retry` and `progress_stall` mechanisms with a single unified counter and 3-tier response.

---

## Task cascading

When a non-terminal task succeeds and its output fills a slot that makes *another* task ready, the engine fires the next task immediately — without waiting for a new user turn. This is **task cascading**.

```
party_size + preferred_date → FindAvailableTimes → available_times
                                                        ↓
                                    (if CheckWaitlist was also ready)
                              available_times → CheckWaitlist → waitlist_status
```

The user experiences this as a single response, even though two backend calls happened.

---

## The `fill_slots()` utility

For advanced scenarios, you can fill slots programmatically from any Python context — a callback, a tool, or a test:

```python
from slot_filling_engine import fill_slots

# Fill multiple slots at once
fill_slots(
    sm=callback_context.state["sm"],
    config=compiled_config,
    values={
        "guest_name": "Maria Chen",
        "party_size": 4,
    },
    skip_readback=True,  # True → filled; False → pending
)
```

Use this for:

- Pre-filling slots from a CRM lookup mid-conversation
- Setting slots from webhook data received after the session started
- Test setup (filling prerequisites before testing a specific interaction)

---

## Putting it all together

Here's the complete `bella_notte_dag` config with all advanced features — conditional slots, announce slots, deferred readback, and full error handling:

??? note "Complete bella_notte_dag config (click to expand)"

    ```python
    def bella_notte_dag() -> dict[str, Any]:
        return {
            "slots": [
                {
                    "name": "welcome",
                    "source": "announce",
                    "message": (
                        "Welcome to Bella Notte! I'd be happy"
                        " to help you with a reservation."
                    ),
                    "preempt": False,
                },
                {
                    "name": "party_size",
                    "source": ["event", "user"],
                    "event_key": "party_size",
                    "setter": "set_reservation_basics",
                    "setter_field": "party_size",
                    "hint": "Party size / number of guests",
                    "ask": "How many guests will be dining?",
                    "readback_fmt": {
                        "type": "plural", "one": "guest", "other": "guests",
                    },
                    "requires_readback": True,
                    "validation": {
                        "max_retries": 3,
                        "errors": {
                            "out_of_range": (
                                "We accept reservations for parties of 1 to 8."
                                " For larger parties, contact events@bellanotte.com."
                            ),
                            "parse_error": "I didn't catch the number. How many guests?",
                        },
                        "on_exhaust": {
                            "say": "Trouble with party size. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "large_party_phone",
                    "source": "user",
                    "setter": "set_large_party_phone",
                    "hint": "Contact phone (parties of 5+)",
                    "condition": "lambda filled: int(filled.get('party_size', 0)) >= 5",
                    "ask": (
                        "For parties of 5 or more, we need a contact number."
                        " What's the best phone number?"
                    ),
                    "readback_fmt": {"type": "prefix", "text": "contact phone"},
                    "requires_readback": True,
                    "validation": {
                        "max_retries": 3,
                        "errors": {
                            "invalid_phone": "Could you provide a number with at least 7 digits?",
                        },
                        "on_exhaust": {
                            "say": "Trouble with phone number. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "preferred_date",
                    "source": "user",
                    "setter": "set_reservation_basics",
                    "setter_field": "preferred_date",
                    "hint": "Date",
                    "ask": "What date would you like to come in?",
                    "readback_fmt": "date",
                    "requires_readback": True,
                    "validation": {
                        "max_retries": 3,
                        "errors": {
                            "invalid_format": "Could you provide the date? E.g., 2026-06-17.",
                            "past_date": "That date is in the past. Try a future date?",
                        },
                        "on_exhaust": {
                            "say": "Trouble with the date. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "available_times",
                    "source": "task:FindAvailableTimes",
                },
                {
                    "name": "selected_time",
                    "source": "user",
                    "setter": "set_selected_time",
                    "hint": "Time (from presented options)",
                    "requires": ["available_times"],
                    "validate_against": {
                        "response_field": "display_value",
                        "filled_slot": "available_times",
                        "error_code": "not_available",
                    },
                    "ask": "We have {available_times}. Which time works best?",
                    "readback_fmt": "time",
                    "requires_readback": True,
                    "validation": {
                        "max_retries": 3,
                        "errors": {
                            "prereq_not_met": "I need to check availability first.",
                            "not_available": (
                                "That time isn't available."
                                " We have {available_times} — any of those work?"
                            ),
                        },
                        "on_exhaust": {
                            "say": "Trouble with time selection. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "guest_name",
                    "source": "user",
                    "setter": "set_guest_info",
                    "setter_field": "guest_name",
                    "hint": "Name (any format)",
                    "ask": "What name should I put the reservation under?",
                    "readback_fmt": {"type": "prefix", "text": "under the name"},
                    "requires_readback": True,
                    "validation": {
                        "max_retries": 3,
                        "errors": {
                            "empty_name": "I didn't catch the name. What name for the reservation?",
                        },
                        "on_exhaust": {
                            "say": "Trouble with the name. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "special_requests",
                    "source": "user",
                    "setter": "set_guest_info",
                    "setter_field": "special_requests",
                    "hint": "Special requests or 'none'",
                    "ask": "Do you have any special requests or dietary needs?",
                    "readback_fmt": {"type": "none_sub", "default": "no special requests"},
                    "requires_readback": True,
                },
                {
                    "name": "confirmation_number",
                    "source": "task:BookReservation",
                },
            ],
            "tasks": [
                {
                    "name": "FindAvailableTimes",
                    "tool": "find_available_times",
                    "inputs": ["party_size", "preferred_date"],
                    "outputs": {"available_times": "available_times"},
                    "success_check": "success",
                    "then_say": (
                        "Great! We have availability at {available_times}."
                        " Which time works best?"
                    ),
                    "on_failure": {
                        "retry_say": "No availability for that date. Try another?",
                        "max_retries": 1,
                        "clear_slots": ["preferred_date"],
                        "on_exhaust": {
                            "say": "Unable to find availability. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
                {
                    "name": "BookReservation",
                    "tool": "book_reservation",
                    "inputs": [
                        "party_size", "large_party_phone", "preferred_date",
                        "selected_time", "guest_name", "special_requests",
                    ],
                    "readback_inputs": True,
                    "outputs": {"confirmation_number": "confirmation_number"},
                    "success_check": "success",
                    "terminal": True,
                    "then_say": (
                        "Confirmed! Your number is {confirmation_number}."
                        " We look forward to welcoming you!"
                    ),
                    "on_failure": {
                        "retry_say": "Trouble completing the reservation. Let me try again.",
                        "max_retries": 2,
                        "on_exhaust": {
                            "say": "Unable to complete reservation. Please call 555-0100.",
                            "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                        },
                    },
                },
            ],
            "confirm_transition_prefix": ["Wonderful!", "Perfect!", "Great!", "Excellent!"],
            "steer_back": {
                "soft_after": 2,
                "hard_after": 4,
                "escalate_after": 6,
                "on_exhaust": {
                    "say": "Trouble completing reservation. Please call 555-0100.",
                    "then": {"tool": "end_session", "args": {"reason": "steer_back_exhausted"}},
                },
            },
        }
    ```

---

## What's next

- [Configuration Reference](reference.md) — complete field-by-field reference for every config option
- [Tutorial](tutorial.md) — if you skipped ahead, start from the beginning
