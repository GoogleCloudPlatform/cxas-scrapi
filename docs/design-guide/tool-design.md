---
title: Tool Design
description: How to write tools that agents call reliably and that fail gracefully.
---

# Tool Design

Tools are where most production failures originate. The model decides which tool to call, what arguments to pass, and how to interpret the response — and it makes those decisions based on the tool's name, description, and parameter schema. Poor tool design gives the model ambiguous signals, which produces unreliable behavior.

---

## Tool structure

Each tool lives in its own directory under `cxas_app/tools/`. The directory contains a JSON config and a `python_function/python_code.py` implementation:

```
tools/
└── check_availability/
    ├── check_availability.json   # Tool schema and metadata
    └── python_function/
        └── python_code.py        # Python implementation (entry `def` MUST be first)
```

The JSON config defines the tool's name, description, and parameter schema:

```json
{
  "name": "check_availability",
  "description": "Check whether a table is available at The Grand Bistro for a given date, time, and party size. Returns available=true and a slot ID if the time is open, or available=false with a reason if not.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "description": "Reservation date in YYYY-MM-DD format."
      },
      "time": {
        "type": "string",
        "description": "Requested time in HH:MM 24-hour format."
      },
      "party_size": {
        "type": "integer",
        "description": "Number of guests. Must be between 1 and 12."
      }
    },
    "required": ["date", "time", "party_size"]
  }
}
```

The Python implementation is a function with an explicit `-> dict` return annotation, a docstring, named typed parameters matching `inputSchema`, and access to the platform-injected `context` global (`context.state`) for session state:

```python
def check_availability(date: str, time: str, party_size: int) -> dict:
    """Check whether a table is available at The Grand Bistro for a given date, time,
    and party size. Returns available=true and a slot ID if the time is open,
    or available=false with a reason if not."""

    try:
        result = availability_api.query(date=date, time=time, covers=party_size)
        if result.is_available:
            return {"available": True, "slot_id": result.slot_id}
        return {"available": False, "reason": result.unavailability_reason}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "agent_action": "check_availability failed. Ask the guest if they would like to try again.",
        }
```

---

## Naming

Tool names must be in `snake_case`. The name is the primary signal the model uses to decide whether to call a tool — it has to be unambiguous.

=== "Bad"

    ```
    tool1
    handle_thing
    process
    reservation_helper
    ```

    These names don't describe what the tool does. The model has to guess, and it will guess wrong.

=== "Good"

    ```
    check_availability
    create_reservation
    cancel_reservation
    get_alternative_slots
    send_confirmation_email
    ```

    Each name is a verb phrase that describes one specific action. The model can infer when to call it from the name alone.

---

## Docstrings

The tool docstring is injected into the model's context alongside the tool schema. It is not documentation for humans — it is a behavioral specification for the model.

=== "Bad"

    ```python
    def check_availability(date: str, time: str, party_size: int) -> dict:
        """Checks availability."""
        ...
    ```

    This is useless. The model already knows the tool "checks availability" from the name. The docstring should tell it **what it returns** and **when to use it**.

=== "Good"

    ```python
    def check_availability(date: str, time: str, party_size: int) -> dict:
        """Check whether a table is available at The Grand Bistro for a given date, time,
        and party size. Returns available=true and a confirmed slot_id if open.
        Returns available=false and a reason string if the slot is unavailable.
        Call this before create_reservation — do not call create_reservation
        without a confirmed slot_id from this tool."""
        ...
    ```

    This tells the model what the return structure looks like and establishes the required call order — both things the model needs to use the tool correctly.

---

## Parameters and function signature invariants

**Place the entry `def` first in `python_code.py`.** The CXAS runtime binds the **very first top-level `def`** in `python_code.py` as the tool entry point regardless of function name or leading underscores. Always place helper functions *below* the main tool function.

**Always declare a `-> dict` return annotation.** Omitting `-> dict` prevents `before_model_callback` `Part.from_function_call(...)` from dispatching the tool reliably.

**Use explicit named, typed parameters.** The model maps argument names to values, and it uses the parameter name as a semantic signal. Explicit names make this mapping reliable.

**Never use `**kwargs`.** When a tool accepts `**kwargs`, the model doesn't know what arguments exist. It will either pass nothing or make up argument names.

**Never use `None` as a default.** If the model omits an argument, a `None` default silently passes that through to your implementation. This produces bugs that are hard to trace. Make required arguments required.

**Prefer OpenAPI Toolsets (`tools.*` / `async_tools.*`) for external REST services.** Configure external HTTP integrations as OpenAPI Toolsets so authentication credentials stay managed by the platform, and invoke them from Python wrapper tools via `tools.<Toolset>_<OperationId>(...)` (using `await asyncio.gather(async_tools.<Toolset>_<OperationId>(...), ...)` rather than `ThreadPoolExecutor` wrapping synchronous `tools.*` calls when fanning out in parallel). When a Python tool must make a direct HTTP request without an OpenAPI Toolset, use the sandbox's pre-injected `ces_requests` global (`requests` and `urllib` imports are disallowed in the CXAS sandbox).

=== "Bad"

    ```python
    def create_reservation(**kwargs):
        """Create a reservation."""
        date = kwargs.get("date")
        time = kwargs.get("time")
        name = kwargs.get("name")
        ...
    ```

=== "Good"

    ```python
    def create_reservation(
        date: str,
        time: str,
        party_size: int,
        guest_name: str,
    ) -> dict:
        """Create a confirmed reservation at The Grand Bistro. Requires a valid slot_id
        from check_availability. Returns a confirmation_number on success."""
        ...
    ```

---

## Common pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| `**kwargs` parameters | Model can't see parameter schema; passes wrong or no args | Use explicit named parameters |
| `None` defaults | Silent failures when model omits an argument | Make required parameters required |
| Tool explosion | Too many tools overwhelm the model's selection | Merge tools that are always called together |
| High-cardinality string args | Model invents values for open-ended enums | Enumerate valid values in the schema description |
| Sequential calls in instructions | Brittle; instructions say "call A then B" but model skips steps | Use tool wrappers that call both internally |
| Ambiguous overlapping tools | Model picks the wrong tool | Rename or merge; make each tool's scope distinct |

---

## Tool wrappers

When a user-facing action always requires multiple internal calls in sequence, wrap them in a single tool. This moves the sequencing logic out of instructions (which the model may skip) and into Python (which always executes).

For example: scheduling a restaurant event requires checking availability, holding the slot, and creating the calendar entry. These three calls must happen in order and all succeed together.

=== "Bad (three separate tools)"

    ```python
    # Instructions say: "Call check_availability, then hold_slot, then create_event"
    # Model sometimes calls create_event without holding the slot first.

    def check_availability(date: str, time: str, party_size: int) -> dict: ...
    def hold_slot(slot_id: str) -> dict: ...
    def create_event(slot_id: str, guest_name: str) -> dict: ...
    ```

=== "Good (one wrapper tool)"

    ```python
    def schedule_event(date: str, time: str, party_size: int, guest_name: str) -> dict:
        """Reserve a table and create the calendar event in one atomic operation.
        Handles availability check, slot hold, and event creation internally.
        Returns confirmation_number on success."""

        slot = _check_availability_internal(date, time, party_size)
        if not slot.is_available:
            return {"success": False, "agent_action": f"No availability: {slot.reason}. Offer alternatives."}

        held = _hold_slot_internal(slot.slot_id)
        if not held:
            return {"success": False, "agent_action": "Failed to hold the slot. Ask the guest to try again."}

        event = _create_event_internal(slot.slot_id, guest_name)
        return {"success": True, "confirmation_number": event.confirmation_number}


    # Place helper functions BELOW the main entry `def`!
    def _check_availability_internal(date: str, time: str, party_size: int): ...
    def _hold_slot_internal(slot_id: str): ...
    def _create_event_internal(slot_id: str, guest_name: str): ...
    ```

The wrapper approach also makes testing simpler: you have one tool to test instead of three, and one failure surface to handle.

---

## Accessing session state

Tools access session state through the platform-injected `context` global (`context.state`), **never** as a function parameter. Read verified identity/account identifiers directly from `context.state` rather than trusting LLM-supplied arguments (IDOR prevention).

```python
def create_reservation(date: str, time: str, party_size: int, guest_name: str) -> dict:
    """Create a confirmed reservation."""

    # Read session state via the `context` global (do NOT put `context` in parameters!)
    existing_reservation = context.state.get("reservation_id", "")

    if existing_reservation:
        return {
            "success": False,
            "agent_action": "Guest already has a reservation. Ask if they want to modify it.",
        }

    # ... proceed with creation
```

!!! warning "`context` is a sandbox global — never a function parameter or schema property"
    Do **not** include `context` in either the Python function parameter list or `inputSchema` in the JSON config file. The CXAS sandbox provides `context` (and `tools` / `async_tools`) automatically at module scope. Adding `context` to the function signature causes parameter-schema mismatches (`T001`/`T003`) and runtime invocation errors.
