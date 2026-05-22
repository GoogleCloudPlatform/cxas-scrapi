---
title: Callbacks
description: How to use callback handlers for dynamic prompting, state initialization, and slot-filling.
---

# Callbacks

Callbacks are server-side Python functions that fire at specific points in the conversation lifecycle. They let you inspect and modify requests and responses before the model sees them, inject dynamic content into instructions, preempt the model entirely with a pre-computed response, and maintain session state across turns.

Used well, callbacks let you build more capable agents without inflating instruction length. Used carelessly, they introduce subtle bugs — particularly the turn-guard omission, described below.

<figure class="diagram">
  <img src="../../assets/diagrams/callback-lifecycle.svg" alt="Callback Lifecycle">
</figure>

---

## The four callback types

| Callback | When it fires | Primary use |
|----------|--------------|------------|
| `before_agent_callback` | At the start of **every turn**, before any model call | State initialization, silence detection, dynamic prompting |
| `after_agent_callback` | After the agent generates a response, before it's sent to the user | Response transformation, logging, state cleanup |
| `before_model_callback` | Before the model is called — can preempt the model entirely | DAG/slot-filling logic, returning a deterministic response |
| `after_model_callback` | After the model responds, before tool calls are executed | Inspecting or modifying model output |

---

## Callback signatures

The platform makes several types available as globals — you do not import them:

```python
# Available as platform globals (no import needed):
# Part, Content, LlmResponse, LlmRequest, CallbackContext

def before_agent_callback(
    callback_context: CallbackContext,
) -> Content | None:
    """Return Content to override the agent response; return None to proceed normally."""
    ...

def after_agent_callback(
    callback_context: CallbackContext,
    response: Content,
) -> Content | None:
    """Return modified Content or None to pass the original response through."""
    ...

def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Return LlmResponse to preempt the model; return None to proceed normally."""
    ...

def after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Return modified LlmResponse or None to pass the original through."""
    ...
```

---

## Critical: the turn guard

`before_agent_callback` fires on **every turn** — including turns in the middle of an ongoing conversation. This is the most important thing to understand about callbacks.

If you write initialization logic in `before_agent_callback` without a guard, it will re-run on every turn and overwrite state that the agent built up during the conversation.

**Always add an early-return guard:**

```python
def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    state = callback_context.state

    # Guard: only run initialization logic on the first turn
    if state.get("_initialized") == "true":
        return None  # Not the first turn — skip initialization

    # First-turn initialization
    state["_initialized"] = "true"
    state["reservation_context"] = "{}"
    state["_conversation_stage"] = "greeting"

    return None  # Proceed normally
```

!!! warning "Missing the turn guard is a common bug"
    A callback without a turn guard will reset session state on every user message. The agent will appear to "forget" everything from previous turns. This is one of the most frequent bugs in production callback implementations. Always add the guard.

---

## State initialization pattern

The first-turn guard is also the right place to initialize session state with values derived from the incoming request — user metadata, session context, or configuration that should be available throughout the conversation.

```python
def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    state = callback_context.state

    if state.get("_initialized") == "true":
        return None

    state["_initialized"] = "true"

    # Initialize reservation context with empty state
    import json
    state["reservation_context"] = json.dumps({
        "date": None,
        "time": None,
        "party_size": None,
        "guest_name": None,
        "confirmation_number": None,
        "modification_count": 0
    })

    # Capture any user metadata passed in with the session
    user_id = callback_context.session.get_parameter("user_id", "")
    if user_id:
        state["user_id"] = user_id

    return None
```

---

## Session start detection

Detect the first turn to trigger a specific greeting or initial action:

```python
def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    state = callback_context.state

    if state.get("_session_started") == "true":
        return None

    state["_session_started"] = "true"

    # Return a scripted greeting for the first turn
    return Content(parts=[
        Part(text=(
            "Welcome to Bella Notte. I can help you make, modify, or cancel a reservation. "
            "What can I do for you today?"
        ))
    ])
```

When `before_agent_callback` returns a `Content` object, the platform sends it directly to the user without calling the model. This is how you deliver scripted opening messages or handle cases where calling the model is unnecessary.

---

## Silence and no-input detection

Detect empty or silence-only input and respond without invoking the model:

```python
def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    state = callback_context.state

    # Guard for session init (shown above)
    if state.get("_initialized") != "true":
        state["_initialized"] = "true"
        return None

    # Detect silence or empty input
    user_input = callback_context.user_input_text or ""
    if not user_input.strip():
        silence_count = int(state.get("_silence_count", "0"))
        silence_count += 1
        state["_silence_count"] = str(silence_count)

        if silence_count >= 3:
            return Content(parts=[Part(text="I'm going to end this session. Please call us back when you're ready.")])

        return Content(parts=[Part(text="I'm sorry, I didn't catch that. Are you still there?")])

    # Clear silence count on valid input
    state["_silence_count"] = "0"
    return None
```

---

## Dynamic prompting

Dynamic prompting injects context-specific instructions into the agent's system prompt on each turn, rather than keeping a large static instruction set. This keeps the active instruction set small (improving model reliability) while allowing the agent's behavior to adapt based on conversation state.

```python
def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    state = callback_context.state

    if state.get("_initialized") != "true":
        state["_initialized"] = "true"
        state["_conversation_stage"] = "gathering_details"
        return None

    stage = state.get("_conversation_stage", "gathering_details")

    # Inject stage-specific instructions into the system prompt
    if stage == "gathering_details":
        callback_context.agent_instruction_override = """
<current_stage>gathering_details</current_stage>
<active_task>
Collect the four required fields in order: date, time, party_size, guest_name.
Ask for only the next missing field. Do not ask for multiple fields at once.
Once all four are collected, call check_availability.
</active_task>
"""
    elif stage == "confirming_slot":
        callback_context.agent_instruction_override = """
<current_stage>confirming_slot</current_stage>
<active_task>
Present the available slot to the guest and ask for confirmation.
If they confirm, call create_reservation.
If they decline, call get_alternative_slots and offer two options.
</active_task>
"""
    elif stage == "confirmed":
        callback_context.agent_instruction_override = """
<current_stage>confirmed</current_stage>
<active_task>
The reservation is confirmed. Read back the confirmation number and full details.
Offer to help with anything else.
</active_task>
"""

    return None
```

!!! tip "Progressive disclosure"
    Dynamic prompting is a form of progressive disclosure: the agent only sees instructions relevant to the current stage of the conversation. This is particularly effective for multi-step flows like reservation creation, where there are 4–6 distinct stages, each with its own rules and actions.

---

## `before_model_callback` for DAG and slot-filling

`before_model_callback` fires just before the model is called and can return an `LlmResponse` to preempt the model entirely. This is the correct hook for deterministic slot-filling logic — where you want to check whether all required slots are collected and short-circuit the model call when they are.

```python
def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    import json

    state = callback_context.state
    stage = state.get("_conversation_stage", "gathering_details")

    if stage != "gathering_details":
        return None  # Not in slot-filling stage — let the model handle it

    raw = state.get("reservation_context", "{}")
    try:
        ctx = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        ctx = {}

    # Check whether all required slots are filled
    required = ["date", "time", "party_size", "guest_name"]
    missing = [field for field in required if not ctx.get(field)]

    if not missing:
        # All slots collected — transition stage and preempt model
        state["_conversation_stage"] = "confirming_slot"
        return LlmResponse(
            content=Content(parts=[
                Part(
                    function_call={"name": "check_availability", "args": {
                        "date": ctx["date"],
                        "time": ctx["time"],
                        "party_size": ctx["party_size"]
                    }}
                )
            ])
        )

    # Not all slots filled — let the model collect the next missing field
    return None
```

When `before_model_callback` returns an `LlmResponse`, the platform treats it as if the model produced that response. The model is not called at all. This makes the transition from slot-filling to action deterministic — the model cannot skip the `check_availability` call because the callback forces it.

!!! note "Preemption vs. guidance"
    Use `before_model_callback` preemption for transitions that must be deterministic: "when all slots are filled, always call `check_availability`." Use dynamic prompting (`before_agent_callback`) for behavioral guidance: "in the `gathering_details` stage, ask for one field at a time." The two patterns complement each other.

---

## `after_model_callback` for payload injection

`after_model_callback` fires after the model generates a response and can modify or replace it. A key use case is **rich payload injection** — appending UI payloads (info cards, suggestion chips) to the model's text output on turns where the engine didn't preempt.

```python
import json as json_lib
from typing import Optional


def after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
    """Append stashed payloads to the LLM's response."""
    sm = callback_context.state.get("sm", {})

    # Pop stashed payloads (written by before_model_callback)
    payloads = sm.pop("_pending_payloads", None)
    if not payloads:
        return None

    # Guard: skip if prior agent output exists in this turn
    for event in reversed(callback_context.events):
        if event.is_user():
            break
        if event.is_agent() and event.parts():
            return None

    # Extract payload parts and append to the LLM's output
    extra_parts = []
    for rp in payloads:
        if rp.get("type") == "payload":
            extra_parts.append(
                Part.from_json(json_lib.dumps(rp["data"]))
            )

    if not extra_parts:
        return None

    combined = list(llm_response.content.parts) + extra_parts
    return LlmResponse.from_parts(parts=combined)
```

The pattern works in tandem with `before_model_callback`: on preempted turns, `before_model_callback` includes payload parts directly in the returned `LlmResponse`. On non-preempted turns, it stashes them in session state for `after_model_callback` to pick up.

!!! tip "Multi-model-call guard"
    The guard checking `callback_context.events` prevents duplicate injection when the LLM makes multiple model calls in a single turn (e.g., calling a setter tool then generating text). Without this guard, payloads could be appended to every model response in the turn.
