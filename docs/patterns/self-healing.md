---
title: Self-Healing Errors
description: Return deterministic recovery instructions from failing tools using the agent_action pattern — so the LLM always knows exactly what to do when something goes wrong, without improvising.
---

# Self-Healing Errors

When a tool call fails, the LLM has to decide what to do next. Without guidance, it improvises — and LLM improvisation in error paths is one of the most common sources of unpredictable agent behavior. The LLM might apologize and stop, ask the user a question that doesn't help, retry with the same bad input, or make up a recovery step that doesn't match your business logic.

The self-healing pattern solves this by returning explicit recovery instructions from the tool itself, as a structured `agent_action` key in the error response.

---

## The `agent_action` pattern

When a tool detects a failure it can reason about, it returns an `agent_action` key alongside the error. The LLM reads this key and follows the instructions exactly, rather than improvising.

```python
def find_appointment_slots(patient_id: str, specialty: str) -> dict:
    """Find available appointment slots for a patient."""

    if not patient_id:
        return {
            'success': False,
            'error': 'patient_id is required',
            'agent_action': (
                'Ask the patient for their member ID before searching for slots. '
                'Their member ID is on their insurance card, starting with ABC-.'
            ),
        }

    slots = _query_availability(patient_id, specialty)

    if slots is None:
        return {
            'success': False,
            'error': 'API unavailable',
            'agent_action': (
                'Tell the patient you are experiencing a brief technical issue '
                'and will try again in a moment. Then retry this tool call.'
            ),
        }

    if not slots:
        return {
            'success': False,
            'error': 'no_availability',
            'agent_action': (
                f'No appointments are available for {specialty} this week. '
                'Ask the patient if they would like to check a different specialty '
                'or be added to a cancellation waitlist.'
            ),
        }

    return {'success': True, 'slots': slots}
```

The agent's instruction tells it how to handle the `agent_action` key:

```xml
<error_protocol>
When a tool returns "success": false, check the "agent_action" field.
If present, follow its instructions exactly — do not add information
or improvise a different recovery. If "agent_action" is absent,
tell the user there was a technical issue and offer to try again.
</error_protocol>
```

---

## Why deterministic recovery beats improvisation

When the LLM improvises error recovery, you get different behavior every run. The patient might receive different instructions about where to find their member ID depending on which part of the LLM's training data is activated. The LLM might suggest calling a phone number that doesn't exist, or offer to schedule a callback when that capability isn't available.

`agent_action` makes recovery deterministic:

- The message comes from your code, not from LLM generation.
- The recovery steps match your business logic exactly.
- The instructions are consistent across every run and every user.
- You can test the error path in a golden eval with an exact `agentResponse` check.

---

## Failure categorization

Structure your error responses consistently so the LLM can pattern-match on the type of failure:

=== "Validation errors"

    Input from the user or LLM doesn't meet requirements. Recovery is always: collect better input.

    ```python
    def set_appointment_date(date: str) -> dict:
        """Record the appointment date in YYYY-MM-DD format."""
        import datetime
        try:
            parsed = datetime.date.fromisoformat(date)
        except ValueError:
            return {
                'success': False,
                'error': 'invalid_date_format',
                'agent_action': (
                    'The date format was not recognized. '
                    'Ask the patient to provide the date in Month Day format, '
                    "for example 'July 15th'."
                ),
            }

        if parsed < datetime.date.today():
            return {
                'success': False,
                'error': 'past_date',
                'agent_action': (
                    'The date provided is in the past. '
                    'Ask the patient for a future date.'
                ),
            }

        context.state['sm'].setdefault('pending', {})['appointment_date'] = date
        return {'success': True, 'value': date}
    ```

=== "API / integration errors"

    A downstream service is unavailable or returned an unexpected response. Recovery is usually: retry or escalate.

    ```python
    def book_appointment(patient_id: str, slot_id: str) -> dict:
        """Book the selected appointment slot."""
        try:
            result = _booking_api(patient_id, slot_id)
        except TimeoutError:
            return {
                'success': False,
                'error': 'api_timeout',
                'agent_action': (
                    'Tell the patient you are having trouble connecting to the '
                    'scheduling system and will try once more.'
                ),
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'api_error: {type(e).__name__}',
                'agent_action': (
                    'Inform the patient that booking is temporarily unavailable '
                    'and offer to connect them with a scheduling coordinator at '
                    '1-800-555-0100.'
                ),
            }

        return {'success': True, 'confirmation': result['confirmation_id']}
    ```

=== "Business rule errors"

    The request is valid but cannot be fulfilled by policy. Recovery depends on the specific rule — usually provide alternatives.

    ```python
    def find_appointment_slots(patient_id: str, specialty: str) -> dict:
        """Find available appointment slots."""
        patient = _get_patient(patient_id)

        if patient['network_status'] == 'out_of_network':
            return {
                'success': False,
                'error': 'out_of_network',
                'agent_action': (
                    f'The patient is out-of-network for {specialty}. '
                    'Let them know they can still be seen but will have higher '
                    'cost-sharing, or ask if they would like a referral to an '
                    'in-network provider.'
                ),
            }

        slots = _query_slots(patient_id, specialty)
        # ...
    ```

---

## Error escalation pattern

When retries are exhausted or no recovery is possible, `agent_action` can instruct the LLM to escalate:

```python
def process_refund(order_id: str, amount: float) -> dict:
    """Process a refund for the specified order."""
    # Check retry count from state
    sm = context.state.get('sm', {})
    refund_attempts = sm.get('_retries', {}).get('process_refund', 0)

    result = _refund_api(order_id, amount)

    if not result['success']:
        sm.setdefault('_retries', {})['process_refund'] = refund_attempts + 1

        if refund_attempts + 1 >= 2:
            # Exhausted retries — escalate to human
            sm['status'] = 'escalated'
            return {
                'success': False,
                'error': 'refund_failed_exhausted',
                'agent_action': (
                    'The refund system is experiencing issues. '
                    'Tell the customer you are connecting them with a billing '
                    'specialist who can process this manually, and transfer the call.'
                ),
            }

        return {
            'success': False,
            'error': 'refund_failed',
            'agent_action': (
                'The refund did not go through. Let the customer know you are '
                'trying once more, then retry this tool call.'
            ),
        }

    return {'success': True, 'refund_id': result['refund_id']}
```

!!! tip "Escalation state should be terminal"
    When you set `sm['status'] = 'escalated'` (or equivalent), ensure your `before_model_callback` checks this and returns early, letting the LLM respond freely without further state-handling or task-firing behavior. Escalated conversations should not resume normal flow.

---

## Testing self-healing paths

Write golden evals that explicitly exercise each error path:

```json
{
  "displayName": "Error Recovery - Patient ID Missing",
  "golden": {
    "turns": [
      {
        "steps": [
          {"userInput": {"text": "I'd like to schedule a cardiology appointment"}},
          {
            "expectation": {
              "note": "Check_Asks_For_Member_ID",
              "agentResponse": {
                "chunks": [{"text": "member ID"}]
              }
            }
          }
        ]
      }
    ]
  }
}
```

For API failure paths, you can inject errors by using a test-mode flag in your tool:

```python
def find_appointment_slots(patient_id: str, specialty: str) -> dict:
    """Find available appointment slots."""
    # Allow test injection of failures
    sm = context.state.get('sm', {})
    if sm.get('_test_inject_error') == 'api_timeout':
        return {
            'success': False,
            'error': 'api_timeout',
            'agent_action': 'Tell the patient you are retrying...',
        }
    # ... normal path
```

Then in your eval setup, set `sm._test_inject_error = 'api_timeout'` in the initial state to exercise that branch deterministically.
