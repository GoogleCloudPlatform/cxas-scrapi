---
title: Configuration Reference
description: Complete field reference for slot-filling DAG configuration — slots, tasks, and global settings.
---

# Configuration Reference

This page documents every field in the `{config_id}_dag` dictionary. Use it alongside the [Tutorial](tutorial.md) and [Advanced Patterns](advanced.md) for context on how each field is used in practice.

---

## Top-level structure

The `{config_id}_dag()` function (e.g. `bella_notte_dag()`) returns a dictionary with these top-level keys:

```python
{
    "slots": [ ... ],                     # Required
    "tasks": [ ... ],                     # Required (can be empty list)
    "confirm_transition_prefix": [ ... ], # Optional
    "steer_back": { ... },               # Optional
}
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `slots` | list[dict] | Yes | Ordered list of slot definitions. Order determines question priority. |
| `tasks` | list[dict] | Yes | Ordered list of task definitions. Order determines firing priority. |
| `confirm_transition_prefix` | list[str] | No | Transition phrases used after readback confirmation. One is picked randomly. |
| `steer_back` | dict | No | 3-tier steer-back config for off-topic conversations (soft directive, hard preempt, escalate). |

---

## Slot configuration

Each slot is a dictionary in the `slots` list. The available fields depend on the slot's `source`.

### Common fields (all slot types)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique identifier. Used in `requires`, `inputs`, `outputs`, and `{placeholder}` references. |
| `source` | str or list[str] | Yes | Where the value comes from. See [source types](#source-types). |
| `condition` | str | No | Lambda string evaluated each turn. Returns `True` to activate, `False` to deactivate. Receives `filled` dict as argument. Example: `"lambda filled: int(filled.get('party_size', 0)) >= 5"` |
| `requires` | list[str] | No | Slot names that must be in `filled` before this slot is asked or its setter is visible. |

### Source types

| Source | Description | Required fields |
|--------|-------------|-----------------|
| `"user"` | User provides the value via a setter tool | `setter`, `ask` |
| `"task:TaskName"` | Value populated when task `TaskName` succeeds | None (task's `outputs` maps to this slot) |
| `"event"` | Pre-filled from external event data | `event_key` |
| `"announce"` | Framework delivers a message, no input needed | `message` |
| `["event", "user"]` | Try event first, fall back to user if no event data | `event_key`, `setter`, `ask` |

### User-sourced slot fields

These fields apply when `source` is `"user"` or includes `"user"` in a list.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `setter` | str | Yes | Tool name the LLM calls to record the value. Must match a tool in the app. |
| `ask` | str | Yes | Question text. Supports `{slot_name}` placeholders resolved from `filled`. |
| `hint` | str | No | Short description for tool selection guidance. Helps the LLM pick the right setter when the user provides multiple values. |
| `requires_readback` | bool | No | If `True`, value goes to `pending` for confirmation before `filled`. Default: `False`. |
| `readback_fmt` | str or dict | No | How to format the value during readback. See [readback formats](#readback-formats). |
| `response` | list[dict] | No | Rich response parts (payloads, chips) delivered alongside the slot's question. See [Rich Response Payloads](advanced.md#rich-response-payloads). |
| `validation` | dict | No | Validation error messages, retry limits, and escalation. See [validation config](#validation-config). |
| `validate_against` | dict | No | Cross-slot validation. See [cross-slot validation](#cross-slot-validation). |
| `setter_field` | str | No | For multi-slot setters. Identifies which field of the multi-slot tool maps to this slot. The tool returns `{"values": {...}, "field_errors": {...}}` instead of `{"value": ...}`. |

### Event-sourced slot fields

These fields apply when `source` is `"event"` or includes `"event"` in a list.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_key` | str | Yes | Key to look up in the `event_data` dictionary passed via session variables. |

### Announce slot fields

These fields apply when `source` is `"announce"`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | str | Yes | The message to deliver. Supports `{slot_name}` placeholders. |
| `preempt` | bool | No | If `True`, message is delivered verbatim (LLM doesn't run). If `False` (default), LLM wraps it naturally. |
| `response` | list[dict] | No | Rich response parts (payloads, cards) delivered with the announce message. See [Rich Response Payloads](advanced.md#rich-response-payloads). |

---

## Readback formats

The `readback_fmt` field controls how a value is presented during readback confirmation.

### Built-in string formats

| Value | Description | Example |
|-------|-------------|---------|
| `"date"` | Formats YYYY-MM-DD as a human-readable date | `"2026-06-19"` → `"Friday, June 19th"` |
| `"time"` | Formats time values | `"19:30"` → `"7:30 PM"` |

### Dictionary formats

| Config | Description | Example |
|--------|-------------|---------|
| `{"type": "plural", "one": "guest", "other": "guests"}` | Pluralizes based on numeric value | `4` → `"4 guests"`, `1` → `"1 guest"` |
| `{"type": "prefix", "text": "under the name"}` | Prepends a label | `"Maria"` → `"under the name Maria"` |
| `{"type": "none_sub", "default": "no special requests"}` | Substitutes a default when value is `"none"` or empty | `"none"` → `"no special requests"` |

---

## Validation config

The `validation` field on a slot controls error handling and retries.

```python
"validation": {
    "max_retries": 3,
    "errors": {
        "error_code_1": "Message for this error.",
        "error_code_2": "Another message with {slot_name} placeholder.",
    },
    "on_exhaust": {
        "say": "Escalation message to the user.",
        "then": "escalate",
    },
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `max_retries` | int | Yes | Maximum number of validation failures before escalation. |
| `errors` | dict[str, str] | Yes | Maps error codes (returned by setter) to user-facing messages. Supports `{slot_name}` placeholders. |
| `error_responses` | dict[str, list[dict]] | No | Maps error codes to rich response parts delivered alongside the error message. Keys match `errors` keys. |
| `on_exhaust` | dict | Yes | Action when retries are exhausted. See [escalation config](#escalation-config). |

### Error code protocol

Setters signal errors by returning `{"error": True, "error_code": "code_name"}`. The `code_name` must match a key in the `errors` dictionary. If the code isn't found, the framework uses a generic retry message.

---

## Cross-slot validation

The `validate_against` field on a slot enables validation of one slot's value against data in another filled slot.

```python
"validate_against": {
    "response_field": "display_value",
    "filled_slot": "available_times",
    "error_code": "not_available",
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `response_field` | str | Yes | Key in the setter's response dict to validate. |
| `filled_slot` | str | Yes | Name of a filled slot whose value is checked against. |
| `error_code` | str | Yes | Error code to use if validation fails. Must have a corresponding entry in `validation.errors`. |

The framework checks whether the setter's `response_field` value appears in the filled slot's value. If not, it triggers the specified error code.

---

## Task configuration

Each task is a dictionary in the `tasks` list.

```python
{
    "name": "FindAvailableTimes",
    "tool": "find_available_times",
    "inputs": ["party_size", "preferred_date"],
    "requires": [],
    "outputs": {"available_times": "available_times"},
    "success_check": "success",
    "terminal": False,
    "readback_inputs": False,
    "then_say": "We have {available_times}. Which time?",
    "condition": None,
    "on_failure": { ... },
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique identifier for the task. |
| `tool` | str | Yes | Name of the executor tool to call. Must match a tool in the app. |
| `inputs` | list[str] | Yes | Slot names whose values are passed as arguments. All must be in `filled` (or inactive) before the task fires. |
| `requires` | list[str] | No | Slot names that must be in `filled` (gates) but aren't passed as arguments. Use for gating on announce slots. |
| `outputs` | dict[str, str] | Yes | Maps result keys to slot names. `{"result_key": "slot_name"}` — when the task succeeds, `result["result_key"]` is stored in `filled["slot_name"]`. |
| `success_check` | str | Yes | Key in the task result that must be truthy for the task to count as successful. |
| `terminal` | bool | No | If `True`, successful completion ends the conversation. Default: `False`. |
| `readback_inputs` | bool | No | If `True`, enables deferred readback for this task's input slots. Default: `False`. |
| `then_say` | str | No | Message shown to the user on success. Supports `{slot_name}` placeholders resolved from `filled` and `task_results`. |
| `then_response` | list[dict] | No | Rich response parts delivered on task success. When present, replaces `then_say` as the preemption response. Supports `{slot_name}` variable substitution. |
| `condition` | str | No | Lambda string. Task only fires when condition is `True`. Receives `filled` dict. |
| `channel_responses` | dict[str, list[dict]] | No | Channel-specific response overrides. Keys are channel identifiers; values replace the default `then_response` for that channel. |
| `on_failure` | dict | No | Failure handling config. See [task failure config](#task-failure-config). |

---

## Task failure config

The `on_failure` field on a task controls retry behavior when the task fails.

```python
"on_failure": {
    "retry_say": "That didn't work. Want to try a different date?",
    "max_retries": 2,
    "clear_slots": ["preferred_date"],
    "on_exhaust": {
        "say": "I can't complete this. Please call us.",
        "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
    },
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `retry_say` | str | Yes | Message shown to the user on each failure. |
| `max_retries` | int | Yes | Maximum retry attempts before escalation. |
| `clear_slots` | list[str] | No | Slots to clear from `filled` on failure. The framework re-asks for these slots. Omit for same-input retry. |
| `on_exhaust` | dict | Yes | Action when retries are exhausted. See [escalation config](#escalation-config). |

### Retry modes

- **Re-collect retry** (with `clear_slots`): Clears named slots and re-asks the user. Good for "no availability" scenarios where different inputs might succeed.
- **Same-input retry** (without `clear_slots`): Retries with the same inputs on the next engine pass. Good for transient backend errors.

---

## Escalation config

The `on_exhaust` field appears in slot validation, task failure, and steer-back configs. It always has the same structure:

```python
"on_exhaust": {
    "say": "Message to the user before escalating.",
    "then": "escalate",
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `say` | str | Yes | Message delivered to the user. |
| `then` | str or dict | Yes | The escalation action. See below. |

### `then` formats

| Format | Description | Example |
|--------|-------------|---------|
| `"escalate"` | String tool name. Framework calls this tool. | `"escalate"` |
| `{"tool": "...", "args": {...}}` | Tool name with arguments. | `{"tool": "end_session", "args": {"reason": "retry_exhausted"}}` |

---

## Global: steer-back

Controls the 3-tier steer-back mechanism for off-topic conversations.

```python
"steer_back": {
    "soft_after": 2,
    "hard_after": 4,
    "escalate_after": 6,
    "on_exhaust": {
        "say": "Having trouble completing your request.",
        "then": {"tool": "end_session", "args": {"reason": "steer_back_exhausted"}},
    },
},
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `soft_after` | int | Yes | Consecutive off-topic turns before injecting a steering directive in the system instruction. |
| `hard_after` | int | Yes | Consecutive off-topic turns before preempting with the next question (or readback re-ask). |
| `escalate_after` | int | Yes | Consecutive off-topic turns before escalation. |
| `on_exhaust` | dict | Yes | Action when steer-back reaches the escalate tier. |

**How it works:**

- **Soft (tier 1):** After `soft_after` turns, the engine adds a `<steer_back>` directive to the system instruction. The LLM still runs and can incorporate the guidance.
- **Hard (tier 2):** After `hard_after` turns, the engine preempts — re-asks the readback question or the next collection question. Then yields one turn so the LLM can process the user's response.
- **Escalate (tier 3):** After `escalate_after` turns, fires `on_exhaust`. Typically ends the session or transfers to a human.

The counter resets to 0 whenever forward progress occurs (new slot filled, value confirmed, etc.).

---

## Global: confirm transition prefix

```python
"confirm_transition_prefix": ["Wonderful!", "Perfect!", "Great!"],
```

A list of transition phrases. After the user confirms a readback and the engine moves to the next question, it picks one randomly as a prefix: *"Wonderful! What date would you like?"*

If omitted, no prefix is added — the engine delivers the next question directly.

---

## State dictionary reference

All framework state lives in `callback_context.state["sm"]`. You generally don't modify this directly — the engine and callbacks manage it — but it's useful for debugging and advanced customization.

| Key | Type | Description |
|-----|------|-------------|
| `filled` | dict[str, Any] | Confirmed slot values. |
| `pending` | dict[str, Any] | Values awaiting readback confirmation. |
| `deferred` | dict[str, Any] | Values held for grouped readback (deferred mode). |
| `task_results` | dict[str, dict] | Full response from each completed task. |
| `_retries` | dict[str, int] | Retry counters. Keys: `"TaskName"` (task), `"slot:name"` (validation). |
| `_slot_errors` | list[dict] | Validation errors from the current turn's setter calls. |
| `_steer_back_turns` | int | Consecutive off-topic turns since last forward progress. Drives the 3-tier steer-back mechanism. |
| `_system_message` | str | Next message for the LLM (set by engine, consumed by callback). |
| `_pending_payloads` | list[dict] | Announce-slot response parts stashed for `after_model_callback` injection. |
| `_pending_question_payloads` | dict | Question-slot response parts with `slot` name for conditional injection. |
| `_debug_mode` | bool | Opt-in debug logging. Set to `True` in session state to enable. |
| `_debug_log` | list[str] | Debug log entries (visible in simulator trace). Populated when `_debug_mode` is `True`. |
| `status` | str | `"in_progress"`, `"complete"`, or `"escalated"`. |

---

## Setter tool protocol

Every setter tool follows the same return protocol:

### Success response

```python
return {"stored": True, "value": <the_validated_value>}
```

The `after_tool_callback` reads `"value"` and routes it to `pending` (or `deferred`).

### Error response

```python
return {"error": True, "error_code": "<code>"}
```

The `after_tool_callback` appends the error to `_slot_errors`. The engine looks up the code in the slot's `validation.errors` config and delivers the message.

### With cross-slot validation

When a slot has `validate_against`, include the validation field in the success response:

```python
return {"stored": True, "value": "7:30 PM", "display_value": "7:30 PM"}
```

The `after_tool_callback` checks `response["display_value"]` against `filled["available_times"]`.

### Multi-slot success response

```python
return {"stored": True, "values": {"party_size": 4, "preferred_date": "2026-06-19"}}
```

When a slot has `setter_field` in the config, the `after_tool_callback` reads the matching field from `values`. Each field is routed independently — valid fields go to `pending` even if other fields have errors:

```python
return {"stored": True, "values": {"party_size": 4}, "field_errors": {"preferred_date": "past_date"}}
```

---

## Executor tool protocol

Executor tools (called by tasks) return a result dictionary. The framework checks the `success_check` key:

### Success response

```python
return {
    "success": True,
    "available_times": "6:00 PM, 7:30 PM, 9:00 PM",
}
```

The `after_tool_callback` maps `outputs` keys from the result to `filled` slots.

### Failure response

```python
return {
    "success": False,
    "error": "no_availability",
}
```

The engine follows the task's `on_failure` config.

---

## Quick-start template

Copy this template to start a new slot-filling agent. Replace the slot and task definitions with your own.

```python
from typing import Any


def my_agent_dag() -> dict[str, Any]:
    """Return the slot-filling DAG configuration."""
    return {
        "slots": [
            {
                "name": "slot_1",
                "source": "user",
                "setter": "set_slot_1",
                "ask": "What is value 1?",
            },
            {
                "name": "slot_2",
                "source": "user",
                "setter": "set_slot_2",
                "ask": "What is value 2?",
            },
            {
                "name": "result",
                "source": "task:MyTask",
            },
        ],
        "tasks": [
            {
                "name": "MyTask",
                "tool": "my_backend_tool",
                "inputs": ["slot_1", "slot_2"],
                "outputs": {"result": "result"},
                "success_check": "success",
                "terminal": True,
                "then_say": "Done! Result: {result}",
                "on_failure": {
                    "retry_say": "That didn't work. Try different values?",
                    "max_retries": 1,
                    "clear_slots": ["slot_1"],
                    "on_exhaust": {
                        "say": "Unable to complete. Please try again later.",
                        "then": {"tool": "end_session", "args": {"reason": "retry_exhausted"}},
                    },
                },
            },
        ],
    }
```
