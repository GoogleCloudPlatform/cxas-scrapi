---
title: Error Handling
description: Patterns for building agents that recover gracefully from tool failures and unexpected inputs.
---

# Error Handling

Agent failures in production fall into a predictable set of categories: invalid inputs, upstream API errors, and business rule violations. Each category needs a recovery path. Without one, the agent either crashes silently or produces a confusing response that leaves the user stuck.

The core discipline is simple: **never return a bare error string**. Always return a structured dict that tells the agent what to do next.

---

## The `agent_action` pattern

The `agent_action` key is the standard mechanism for deterministic recovery. When a tool returns a dict containing `agent_action`, the agent reads it as an instruction and acts on it — presenting a message, asking a follow-up question, or escalating to a human.

=== "Bad"

    ```python
    def check_availability(date: str, time: str, party_size: int, context) -> dict:
        try:
            result = availability_api.query(date=date, time=time, covers=party_size)
            return {"available": True, "slot_id": result.slot_id}
        except Exception as e:
            return f"Error: {e}"  # Bare string — agent doesn't know what to do
    ```

    When the tool returns a bare string on failure, the agent sees unstructured text and improvises a response. The improvisation is unpredictable.

=== "Good"

    ```python
    def check_availability(date: str, time: str, party_size: int, context) -> dict:
        try:
            result = availability_api.query(date=date, time=time, covers=party_size)
            return {"available": True, "slot_id": result.slot_id}
        except Exception as e:
            return {
                "agent_action": (
                    "The availability system is temporarily unavailable. "
                    "Apologize and ask the guest to try again in a moment."
                )
            }
    ```

    The `agent_action` value is a direct instruction to the agent. It specifies exactly what the agent should say or do, removing ambiguity from the recovery path.

---

## Failure categories

Structure your error handling around three categories:

| Category | Cause | Recovery pattern |
|----------|-------|-----------------|
| **Validation failure** | Required input missing, wrong format, out-of-range value | Return `agent_action` asking the agent to re-collect the specific field |
| **API failure** | Upstream service unavailable, timeout, unexpected response | Return `agent_action` asking the agent to apologize and suggest retry |
| **Business rule failure** | Valid input, but violates a policy (party too large, date too far out) | Return `agent_action` with the specific policy explanation |

---

## Early validation

Validate inputs before calling external APIs. If you know the call will fail — because a required field is missing, a value is out of range, or a precondition isn't met — fail fast with a clear `agent_action` instead of making an API call that will fail for the same reason.

```python
def create_reservation(
    date: str,
    time: str,
    party_size: int,
    guest_name: str,
    context
) -> dict:
    """Create a confirmed reservation at The Grand Bistro."""

    # Validate before hitting the API
    if not guest_name or not guest_name.strip():
        return {
            "agent_action": "Guest name is required. Ask the guest for their name."
        }

    if party_size > 12:
        return {
            "agent_action": (
                f"The Grand Bistro cannot accommodate parties of {party_size}. "
                "The maximum is 12 guests. Direct the guest to events@grandbistro.com "
                "for larger party inquiries."
            )
        }

    try:
        result = reservation_api.create(
            date=date,
            time=time,
            covers=party_size,
            name=guest_name
        )
        return {
            "confirmation_number": result.confirmation_number,
            "date": date,
            "time": time,
            "party_size": party_size,
            "guest_name": guest_name
        }

    except reservation_api.ConflictError:
        return {
            "agent_action": (
                "The slot is no longer available — another guest just booked it. "
                "Call get_alternative_slots and offer two nearby times."
            )
        }

    except reservation_api.AuthError:
        return {
            "agent_action": (
                "There is a system configuration issue. "
                "Apologize and offer to transfer the guest to the host stand."
            )
        }

    except Exception as e:
        return {
            "agent_action": (
                "An unexpected error occurred with the reservation system. "
                "Apologize and ask the guest to try again in a moment."
            )
        }
```

This tool demonstrates the full pattern:

1. Validate required fields before any API call
2. Validate business rules (party size limit) before any API call
3. Catch specific exceptions with specific `agent_action` responses
4. Catch the generic `Exception` as a last-resort fallback

---

## Exception handling rules

**Always have a bare `except Exception` catch.** Specific exception catches miss unexpected error types. The bare catch prevents unhandled exceptions from crashing the tool call and leaving the agent without a response.

**Never swallow exceptions silently.** If you catch an exception without returning an `agent_action`, the tool returns `None`, which the agent interprets as success. This produces silent failures that are hard to trace.

**Log the actual exception.** The `agent_action` response is for the agent; logging is for the developer. Log the full exception before returning the structured response.

```python
import logging

logger = logging.getLogger(__name__)

def check_availability(date: str, time: str, party_size: int, context) -> dict:
    """..."""
    try:
        result = availability_api.query(date=date, time=time, covers=party_size)
        return {"available": True, "slot_id": result.slot_id}
    except Exception as e:
        logger.exception("check_availability failed for date=%s time=%s size=%d", date, time, party_size)
        return {
            "agent_action": "The availability system is temporarily unavailable. Ask the guest to try again."
        }
```

!!! warning "Don't expose internal errors to the agent"
    The `agent_action` string goes directly into the agent's context. Don't include stack traces, exception class names, or internal system details in `agent_action` values. They will appear in the agent's response. Log that information separately.
