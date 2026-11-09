---
title: Variables
description: How to manage session state and variables in CXAS agents.
---

# Variables

CXAS variables hold session state that persists across turns and is accessible to agents, tools, and callbacks. Getting variable design right has an outsized effect on maintainability — poor variable design produces agents where state is scattered, hard to inspect, and fragile to type coercion.

---

## Use JSON schemas instead of individual variables

As an agent's complexity grows, the number of individual state fields grows with it. A reservation agent might need to track the date, time, party size, guest name, confirmation number, modification count, cancellation eligibility, and more. If each of these is its own variable, you have a "variable explosion" problem: many variables to declare, many to read in callbacks and tools, and a fragmented picture of session state.

The solution is to consolidate related state into a single JSON-typed variable.

=== "Bad"

    ```yaml
    # variables/reservation_date.yaml
    name: reservation_date
    type: STRING

    # variables/reservation_time.yaml
    name: reservation_time
    type: STRING

    # variables/party_size.yaml
    name: party_size
    type: STRING

    # variables/guest_name.yaml
    name: guest_name
    type: STRING

    # variables/confirmation_number.yaml
    name: confirmation_number
    type: STRING

    # variables/modification_count.yaml
    name: modification_count
    type: STRING
    ```

    Six separate variables for state that belongs together. In callbacks and tools, you access each one individually, and there's no single place to see the full state of a reservation.

=== "Good"

    ```yaml
    # variables/reservation_context.yaml
    name: reservation_context
    type: STRING
    default_value: "{}"
    ```

    ```python
    # In a tool or callback, read and write the full context as JSON
    import json

    def get_reservation_context(context) -> dict:
        raw = context.state.get("reservation_context", "{}")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_reservation_context(context, data: dict) -> None:
        context.state["reservation_context"] = json.dumps(data)

    # Usage
    ctx = get_reservation_context(context)
    ctx["guest_name"] = guest_name
    ctx["confirmation_number"] = result.confirmation_number
    set_reservation_context(context, ctx)
    ```

    One variable holds the full reservation state. You can inspect the entire state in one read and update it atomically.

---

## Variable types and platform limits

Declare all session variables in `app.json` under `variableDeclarations`. CXAS supports typed JSON schemas (`OBJECT`, `ARRAY`, `STRING`, `BOOLEAN`, `INTEGER`, `NUMBER`), with two important platform limits:

- **Variable count ceiling:** Keep `variableDeclarations` at or below **95 variables** (hard platform cap is 100; consolidate related fields into `OBJECT`-typed variables).
- **Session state size ceiling:** Keep total serialized session state well below **256 KB** (store lean decision fields in state, never raw multi-hundred-KB API dumps).

| Type | Notes & Runtime Traps |
|------|-----------------------|
| `OBJECT` | Native dictionary in `context.state` (`{"type": "OBJECT", "properties": {...}}`). Preferred for grouping domain context without manual `json.loads()` string wrapping. Reassign the top-level key (`state["ctx"] = updated_ctx`) after mutating nested fields. |
| `ARRAY` | Native list in `context.state` (`{"type": "ARRAY", "items": {...}}`). |
| `STRING` | Text, dates, and **all identifiers / ZIP codes / account numbers**. Always use `STRING` (never `NUMBER`) for IDs and ZIP codes — `NUMBER` deserializes integers as floats (`12345.0`) and strips leading zeros (`"02138"` → `2138.0`). |
| `BOOLEAN` | Simple flags. Because telephony/SIP headers or tools may pass `"true"`/`"false"` strings, coerce defensively (`str(val).lower() == "true"` — in Python, `bool("false")` is `True`!). |
| `INTEGER` / `NUMBER` | Arithmetic quantities and counters only (never identifiers). |

!!! warning "Date/time in the Python sandbox"
    The CXAS Python sandbox runs in UTC. Rather than calling `datetime.now()` directly for business-date logic, read the app's configured timezone date from `context.state.get("current_date")` (and include `{{current_date}}` in `<role>` so the LLM resolves relative dates like "tomorrow" accurately).

---

## Type coercion rules

All session state values are strings. The platform does not coerce types automatically.

**Reading a counter:**

```python
raw = context.state.get("modification_count", "0")
try:
    count = int(raw)
except (ValueError, TypeError):
    count = 0
```

**Reading a boolean flag:**

```python
# WRONG — Python will evaluate "false" as truthy
if context.state.get("cancellation_eligible"):
    ...

# CORRECT — compare as string
if context.state.get("cancellation_eligible") == "true":
    ...
```

**Reading JSON state:**

```python
raw = context.state.get("reservation_context", "{}")
try:
    data = json.loads(raw)
except (json.JSONDecodeError, TypeError):
    data = {}
```

---

## Naming conventions

Prefix variables intended for observability with an underscore. These are typically intermediate state values that help with debugging but aren't part of the agent's core logic.

```yaml
# Core state variable — read and written by tools
name: reservation_context
type: STRING
default_value: "{}"

# Observability variable — written by callbacks for debugging
name: _last_intent
type: STRING

# Observability variable — tracks conversation stage
name: _conversation_stage
type: STRING
```

Use descriptive names that make the variable's purpose clear without needing to look at its definition. `reservation_context` is clear; `ctx` is not. `cancellation_eligible` is clear; `flag2` is not.

---

## Variables vs. tool state

Session state is accessible in two contexts, with slightly different APIs:

| Context | Access pattern | Use case |
|---------|---------------|---------|
| Tool function | `context.state["key"]` | Read state set by a previous turn or callback; write state for future turns |
| Callback function | `callback_context.state["key"]` | Initialize state at session start; read state for dynamic prompting; write intermediate state |

Both contexts read from and write to the same underlying session state. A value written by a tool in one turn is visible to a callback in the next turn, and vice versa.

!!! note "State written in a tool is available immediately"
    If a tool writes to `context.state` and then a callback runs in the same turn, the callback will see the updated value. State is shared within the turn, not just across turns.

---

## Environment-specific variable defaults (`$env_var`)

When a session variable needs different initial defaults across environments (for example, `enable_authentication: false` in `dev` and `true` in `uat`/`prod`), set `"default": "$env_var"` on the declaration in `app.json` and supply the typed default under `app.variableDeclarations` in each stage's `environment.json`:

=== "app.json"

    ```json
    {
      "variableDeclarations": [
        {
          "name": "enable_authentication",
          "description": "Enforce caller authentication before account actions.",
          "schema": {
            "type": "BOOLEAN",
            "default": "$env_var"
          }
        }
      ]
    }
    ```

=== "environment.prod.json"

    ```json
    {
      "app": {
        "variableDeclarations": [
          {
            "name": "enable_authentication",
            "schema": {
              "type": "BOOLEAN",
              "default": true
            }
          }
        ]
      }
    }
    ```

- **Validate per stage:** `cxas lint --app-dir ./my-agent --env-file ./environment.prod.json` (rule `A017` verifies presence and schema types).
- **Deploy per stage:** `cxas push --app-dir ./my-agent --env-file ./environment.prod.json --to <prod-app>` resolves `"$env_var"` in a temporary staging copy without mutating your working tree.
- **Pull safely:** `cxas pull` preserves existing `"$env_var"` placeholders in `app.json` and syncs exported defaults into `environment.json`.
