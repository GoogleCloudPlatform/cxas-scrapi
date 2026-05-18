---
title: "Tutorial: Restaurant Reservation Agent"
description: Build Bella Notte — a complete restaurant reservation agent using the Slot Filling Pattern — from project setup through evaluations.
---

# Tutorial: Restaurant Reservation Agent

!!! tip "Recommended: Framework-based tutorial"
    This tutorial teaches the **manual approach** to slot filling — setters manage state directly, and you write the callback DAG logic by hand. For new agents, we recommend the **[Slot Filling DAG Framework Tutorial](../guides/slot-filling/tutorial.md)**, which uses a declarative config and framework callbacks that handle state management, readback, validation, and error recovery automatically.

In this tutorial you'll build **Bella Notte**, the reservation agent for a fine-dining Italian restaurant. The agent collects a party size, preferred date, available times (from a mock API), selected time, guest name, and special requests — then books the reservation and returns a confirmation number.

Bella Notte is the canonical reference implementation of the [Slot Filling Pattern](../patterns/slot-filling.md). Every architectural decision in this tutorial is explained with reference to that pattern.

**What you'll have at the end:**

- A working CXAS agent with 5 setter tools and a `before_model_callback`
- A full eval suite (golden + scenario evals)
- A clear mental model of why each piece is designed the way it is

---

## Step 1 — Project setup

=== "Folder structure"

    Pull your CXAS app with `cxas pull` first, then organize your project like this:

    ```
    bella_notte/
    ├── gecx-config.json          ← SCRAPI configuration
    ├── cxas_app/
    │   ├── app.json
    │   ├── agents/
    │   │   └── Bella_Notte_Host/
    │   │       ├── Bella_Notte_Host.json
    │   │       ├── instruction.txt
    │   │       ├── before_agent_callbacks/
    │   │       │   └── before_agent_callbacks_01/
    │   │       │       └── python_code.py
    │   │       └── before_model_callbacks/
    │   │           └── before_model_callbacks_01/
    │   │               └── python_code.py
    │   ├── tools/
    │   │   ├── set_party_size/
    │   │   │   ├── set_party_size.json
    │   │   │   └── python_function/
    │   │   │       └── python_code.py
    │   │   ├── set_preferred_date/
    │   │   │   ├── set_preferred_date.json
    │   │   │   └── python_function/
    │   │   │       └── python_code.py
    │   │   ├── set_guest_name/
    │   │   │   ├── set_guest_name.json
    │   │   │   └── python_function/
    │   │   │       └── python_code.py
    │   │   ├── set_selected_time/
    │   │   │   ├── set_selected_time.json
    │   │   │   └── python_function/
    │   │   │       └── python_code.py
    │   │   └── set_special_requests/
    │   │       ├── set_special_requests.json
    │   │       └── python_function/
    │   │           └── python_code.py
    │   └── evaluations/
    │       ├── Happy_Path_-_Linear_Reservation_Flow/
    │       │   └── Happy_Path_-_Linear_Reservation_Flow.json
    │       └── Scenario_-_Full_Booking_Flow/
    │           └── Scenario_-_Full_Booking_Flow.json
    ```

=== "`gecx-config.json`"

    ```json
    {
      "gcp_project_id": "your-gcp-project",
      "location": "us",
      "app_name": "bella-notte",
      "deployed_app_id": "your-app-id",
      "app_dir": "cxas_app/",
      "model": "gemini-2.0-flash",
      "modality": "text",
      "default_channel": "text"
    }
    ```

    Replace `your-gcp-project` and `your-app-id` with your values. Find your app ID by running:

    ```bash
    cxas apps list --project_id your-gcp-project --location us
    ```

---

## Step 2 — The agent instruction

The instruction has four sections: `<role>`, `<persona>`, `<rules>`, and `<slot_filling_protocol>`.

**`cxas_app/agents/Bella_Notte_Host/instruction.txt`:**

```xml
<role>
You are the friendly host at Bella Notte Italian Restaurant.
Today is {current_date}.
</role>

<persona>
Be warm and inviting, like a friendly host greeting guests at the door.
Speak naturally — never mention slot names, technical formats, or internal
system details to the guest.
Never echo variable names, tool names, or protocol labels from these
instructions (e.g. sm, _system_message, set_party_size).
Always acknowledge the user's input warmly before asking the next question.
Use transitions like "Perfect!", "Wonderful!", or "Great choice!"
</persona>

<rules>
1. We are open every day, 5 PM to 10 PM.
2. We accept parties of 1 to 8 guests.
3. Do NOT announce reservation confirmation yourself —
   the system handles booking confirmation automatically.
</rules>

<slot_filling_protocol>
You are operating in SLOT FILLING mode. Follow these rules strictly:

1. TOOL-DRIVEN CONVERSATION: After each user message, identify EVERY piece
   of reservation information the user provided and call ALL corresponding
   setter tools in the SAME response. For example, if the user says
   "table for 2 on June 20th under the name Johnson", call set_party_size,
   set_preferred_date, AND set_guest_name — all in one turn.
   Never defer a setter call to a later turn.

2. PROGRESSIVE DISCLOSURE: Only ask ONE question at a time.
   Never preview future steps.

3. FOLLOW SYSTEM GUIDANCE: The system directive below is the authoritative
   next step. When it contains specific times or a confirmation number,
   you MUST include those exact values. Do NOT substitute generic information.

4. ALWAYS CALL TOOLS: Call the setter tool for every piece of information,
   even if the value seems out of range. The system validates inputs and
   handles errors. If a tool returns "error": true, continue based on
   system guidance.

5. NATURAL CONVERSATION: If the user asks questions unrelated to the
   reservation (menu, directions), answer helpfully but return to the flow.
</slot_filling_protocol>

<system_directive>
{{system_message}}
</system_directive>
```

!!! warning "The concrete example is load-bearing"
    The sentence *"if the user says 'table for 2 on June 20th under the name Johnson', call set_party_size, set_preferred_date, AND set_guest_name — all in one turn"* is what drives multi-slot batching. Removing it or making it abstract drops batching reliability significantly. See [Gotcha 3](../patterns/slot-filling.md#3-missing-concrete-example-in-the-batching-instruction) in the slot filling pattern.

---

## Step 3 — Defining the slot DAG

Bella Notte's conversation collects 7 slots and fires 2 tasks:

```
party_size ──┐
             ├──▶ FindAvailableTimes ──▶ available_times
preferred_date─┘                               │
                                               ▼
                                        selected_time (requires available_times)
                                               │
guest_name ───────────────────────────────────┤
                                               │
special_requests ─────────────────────────────┤
                                               │
                                               ▼
                                        BookReservation (terminal)
                                               │
                                               ▼
                                        confirmation_number
```

| Slot | Source | Required by | Notes |
|---|---|---|---|
| `party_size` | User | `FindAvailableTimes` | 1–8 guests |
| `preferred_date` | User | `FindAvailableTimes` | YYYY-MM-DD format |
| `available_times` | Task: `FindAvailableTimes` | `selected_time` | Comma-separated times |
| `selected_time` | User | `BookReservation` | Requires `available_times` |
| `guest_name` | User | `BookReservation` | Any name format accepted |
| `special_requests` | User | `BookReservation` | "none" is a valid value |
| `confirmation_number` | Task: `BookReservation` | — | Terminal output |

---

## Step 4 — Writing the setter tools

=== "`set_party_size.py`"

    ```python
    """Setter tool for the party_size slot."""

    from typing import Any


    def set_party_size(size: int) -> dict[str, Any]:
        """Record the number of guests.

        Parse natural language: 'just me'=1, 'a couple'=2, 'four of us'=4.
        Call immediately when party size is mentioned.
        """
        sm = context.state['sm']  # (1)

        if not isinstance(size, int):
            try:
                size = int(size)
            except (ValueError, TypeError):
                sm.setdefault('_slot_errors', []).append(
                    {'slot': 'party_size', 'code': 'parse_error'}
                )
                return {'error': True}

        if not (1 <= size <= 8):
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'party_size', 'code': 'out_of_range'}
            )
            return {'error': True}

        # Clear prior retry count on success
        sm.get('_retries', {}).pop('slot:party_size', None)

        # Write to pending — the callback promotes to filled after readback
        sm.setdefault('pending', {})['party_size'] = size

        return {'stored': True, 'value': size}
    ```

    1. `context` is a CXAS built-in available in all Python tools.

    !!! note "Why write to `pending`, not `filled`?"
        The readback protocol (in the Bella Notte instruction) requires the agent to confirm each value with the user before proceeding. Setter tools write to `pending`; the `confirm_pending` tool moves values to `filled` after the user says "yes".

=== "`set_preferred_date.py`"

    ```python
    """Setter tool for the preferred_date slot."""

    import datetime
    from typing import Any


    def set_preferred_date(date: str) -> dict[str, Any]:
        """Record the reservation date in YYYY-MM-DD format.

        Convert natural language ('next Friday', 'June 20th') to YYYY-MM-DD.
        Call immediately when any date is mentioned.
        """
        sm = context.state['sm']
        date = str(date).strip()

        try:
            parsed = datetime.datetime.strptime(date, '%Y-%m-%d').date()
        except ValueError:
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'preferred_date', 'code': 'invalid_format'}
            )
            return {'error': True}

        if parsed < datetime.date.today():
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'preferred_date', 'code': 'past_date'}
            )
            return {'error': True}

        sm.get('_retries', {}).pop('slot:preferred_date', None)
        sm.setdefault('pending', {})['preferred_date'] = date

        return {'stored': True, 'value': date}
    ```

=== "`set_selected_time.py`"

    ```python
    """Setter tool for the selected_time slot."""

    from typing import Any


    def set_selected_time(time: str) -> dict[str, Any]:
        """Record the guest's chosen time in HH:MM 24-hour format.

        Convert natural language like '7 PM' to '19:00', '7:30 PM' to '19:30'.
        """
        sm = context.state['sm']

        # Guard: available_times must be in filled before this slot is valid.
        # Tool visibility handles this — this check is a safety net.
        if 'available_times' not in sm.get('filled', {}):
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'selected_time', 'code': 'prereq_not_met'}
            )
            return {'error': True}

        time = str(time).strip()

        # Convert 24h input to 12h for comparison with available options
        try:
            parts = time.split(':')
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            period = 'AM' if h < 12 else 'PM'
            h12 = h % 12 or 12
            time_12h = f'{h12}:{m:02d} {period}'
        except (ValueError, IndexError):
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'selected_time', 'code': 'invalid_format'}
            )
            return {'error': True}

        available = [t.strip() for t in sm['filled']['available_times'].split(',')]
        if time_12h not in available:
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'selected_time', 'code': 'not_available'}
            )
            return {'error': True}

        sm.get('_retries', {}).pop('slot:selected_time', None)
        sm.setdefault('pending', {})['selected_time'] = time

        return {'stored': True, 'value': time}
    ```

=== "`set_guest_name.py` and `set_special_requests.py`"

    These two tools are simpler — minimal validation, mostly write-through:

    ```python
    # set_guest_name.py
    def set_guest_name(name: str) -> dict:
        """Record the name for the reservation.

        Accept any format — first name, last name, full name, nickname.
        """
        sm = context.state['sm']
        name = str(name).strip()
        if not name:
            sm.setdefault('_slot_errors', []).append(
                {'slot': 'guest_name', 'code': 'empty'}
            )
            return {'error': True}
        sm.setdefault('pending', {})['guest_name'] = name
        return {'stored': True, 'value': name}
    ```

    ```python
    # set_special_requests.py
    def set_special_requests(requests: str) -> dict:
        """Record special requests or 'none' if the guest has none.

        Call whenever the user mentions any special request or says 'none'.
        """
        sm = context.state['sm']
        requests = str(requests).strip()
        sm.setdefault('pending', {})['special_requests'] = requests
        return {'stored': True, 'value': requests}
    ```

---

## Step 5 — The `before_model_callback`

The callback is the DAG engine. It runs before every LLM turn and decides what to do next based on `sm` state.

**`cxas_app/callbacks/before_model_callback/python_function/python_code.py`:**

```python
"""before_model_callback for the Bella Notte slot filling agent."""

import random
import string


# ── Executor functions ────────────────────────────────────────────────────────

def _find_available_times(filled: dict) -> dict:
    """Mock availability lookup. Replace with real API in production."""
    # Simulate different availability by date
    date = filled.get('preferred_date', '')
    times_by_day = {
        'Mon': '6:00 PM, 7:30 PM, 9:00 PM',
        'Tue': '6:00 PM, 7:00 PM, 8:30 PM',
        'Wed': '5:30 PM, 7:00 PM, 8:00 PM, 9:00 PM',
    }
    import datetime
    try:
        day_abbr = datetime.date.fromisoformat(date).strftime('%a')
        times = times_by_day.get(day_abbr, '6:00 PM, 7:00 PM, 8:00 PM')
    except ValueError:
        times = '6:00 PM, 7:00 PM, 8:00 PM'
    return {'success': True, 'times': times}


def _book_reservation(filled: dict) -> dict:
    """Mock booking API. Replace with real API in production."""
    confirmation = 'BN-' + ''.join(random.choices(string.digits, k=6))
    return {'success': True, 'confirmation': confirmation}


# ── DAG helpers ───────────────────────────────────────────────────────────────

def _task_inputs_ready(sm: dict, task_name: str) -> bool:
    """Check whether all inputs for a task are in sm['filled']."""
    filled = sm.get('filled', {})
    task_results = sm.get('task_results', {})
    inputs = {
        'FindAvailableTimes': ['party_size', 'preferred_date'],
        'BookReservation': [
            'party_size', 'preferred_date', 'selected_time',
            'guest_name', 'special_requests',
        ],
    }
    if task_name in task_results:
        return False  # Already ran successfully
    return all(s in filled for s in inputs.get(task_name, []))


def _next_question(sm: dict) -> tuple:
    """Return (question_text, slot_name) for the first unfilled slot."""
    filled = sm.get('filled', {})

    order = [
        ('party_size',    'How many guests will be joining you?'),
        ('preferred_date', 'What date were you thinking?'),
    ]

    if 'available_times' in filled:
        times = filled['available_times']
        order.append(
            ('selected_time', f'We have openings at {times}. Which time works for you?')
        )

    order += [
        ('guest_name',       'What name should I put the reservation under?'),
        ('special_requests', 'Do you have any special requests? '
                             "(If not, just say 'none'.)"),
    ]

    for slot_name, question in order:
        if slot_name not in filled:
            return question, slot_name

    return 'All information has been collected.', None


# ── Main callback ─────────────────────────────────────────────────────────────

def before_model_callback(callback_context, llm_request):  # (1)
    sm = callback_context.state.get('sm', {})
    if not sm:
        sm = {
            'filled': {}, 'pending': {}, 'task_results': {},
            '_retries': {}, '_slot_errors': [],
            '_system_message': '', 'status': 'in_progress',
        }
        callback_context.state['sm'] = sm

    # 1. Terminal state guard — let the LLM respond freely
    if sm.get('status') in ('complete', 'escalated'):
        return None

    # 2. Handle slot validation errors from setter tools
    errors = sm.pop('_slot_errors', [])
    if errors:
        error_messages = {
            ('party_size', 'out_of_range'):    'We accept parties of 1 to 8 guests. How many guests will be dining?',
            ('party_size', 'parse_error'):     "I didn't catch the number of guests. How many will be in your party?",
            ('preferred_date', 'past_date'):   'That date is in the past. Could you provide a future date?',
            ('preferred_date', 'invalid_format'): "I couldn't parse that date. Could you say it in another way?",
            ('selected_time', 'not_available'): 'That time is not available. Please choose from the options shown.',
            ('selected_time', 'prereq_not_met'): 'Please wait for available times to be presented first.',
        }
        msgs = []
        for err in errors:
            key = (err.get('slot'), err.get('code'))
            msg = error_messages.get(key, 'There was an issue with that value. Please try again.')
            msgs.append(msg)
        combined = ' '.join(msgs)
        sm['_system_message'] = combined
        if llm_request.contents and len(llm_request.contents) > 1:
            from google.cloud.aiplatform_v1beta1.types.content import Part
            from google.cloud.aiplatform_v1beta1.types.prediction_service import GenerateContentResponse as LlmResponse
            return LlmResponse.from_parts(parts=[Part.from_text(text=combined)])
        return None

    # 3. Check if FindAvailableTimes should fire
    task_fired = False
    if _task_inputs_ready(sm, 'FindAvailableTimes'):
        result = _find_available_times(sm['filled'])
        sm['task_results']['FindAvailableTimes'] = result
        if result.get('success'):
            sm['filled']['available_times'] = result['times']
            task_fired = True

    # 4. Check if BookReservation should fire
    if _task_inputs_ready(sm, 'BookReservation'):
        result = _book_reservation(sm['filled'])
        sm['task_results']['BookReservation'] = result
        if result.get('success'):
            sm['filled']['confirmation_number'] = result['confirmation']
            sm['status'] = 'complete'
            msg = (
                f"Wonderful! Your reservation at Bella Notte is confirmed. "
                f"Your confirmation number is {result['confirmation']}. "
                f"We look forward to welcoming you!"
            )
            sm['_system_message'] = msg
            task_fired = True

    # 5. Set _system_message with the next question
    if not sm.get('status') == 'complete':
        next_q, _ = _next_question(sm)
        sm['_system_message'] = next_q

    # 6. Preempt the LLM when a task just fired (skip model generation)
    if task_fired and llm_request.contents and len(llm_request.contents) > 1:
        from google.cloud.aiplatform_v1beta1.types.content import Part
        from google.cloud.aiplatform_v1beta1.types.prediction_service import GenerateContentResponse as LlmResponse
        return LlmResponse.from_parts(
            parts=[Part.from_text(text=sm['_system_message'])]
        )

    return None
```

1. The callback signature must be exactly `before_model_callback(callback_context, llm_request)`.

!!! warning "Preemption guard: `len(llm_request.contents) > 1`"
    The `len(llm_request.contents) > 1` check prevents preempting on the first user message. On the opening turn, the LLM should generate a natural greeting. Without this check, the first response would be a canned "How many guests?" — which fails persona evals.

---

## Step 6 — Variables declaration

The `sm` variable must be declared in your app so the platform initializes it for each session.

**`cxas_app/variableDeclarations/sm.json`:**

```json
{
  "name": "sm",
  "displayName": "Slot Filling State",
  "description": "Tracks slot filling state for the reservation flow.",
  "dataType": "STRING",
  "defaultValue": "{\"filled\": {}, \"pending\": {}, \"task_results\": {}, \"_retries\": {}, \"_slot_errors\": [], \"_system_message\": \"\", \"status\": \"in_progress\"}"
}
```

!!! note "Type is `STRING`, not `OBJECT`"
    CX Agent Studio session variables that hold complex state are declared as `STRING` and parsed as JSON in Python. The callback reads `context.state['sm']` — the platform deserializes the JSON automatically.

---

## Step 7 — Writing evaluations

A golden eval checks exact tool calls and specific response content. A scenario eval runs a full simulated conversation and evaluates it holistically.

=== "Golden eval — linear happy path (single slot per turn)"

    This eval checks that the agent asks for party size first, calls the right tool, reads back the value, and moves on after confirmation.

    **`evaluations/Happy_Path_Linear_Flow.json`:**

    ```json
    {
      "displayName": "Happy Path - Linear Reservation Flow",
      "description": "Full linear flow: party_size → date → time → name → requests → booking.",
      "tags": ["slot-filling", "happy-path", "e2e"],
      "golden": {
        "turns": [
          {
            "steps": [
              {"userInput": {"text": "I'd like to make a reservation please"}},
              {
                "expectation": {
                  "note": "Check_Asks_Party_Size",
                  "agentResponse": {
                    "chunks": [{"text": "guests"}]
                  }
                }
              }
            ]
          },
          {
            "steps": [
              {"userInput": {"text": "Table for four please"}},
              {
                "expectation": {
                  "note": "Check_SetPartySize_Called",
                  "toolCall": {
                    "tool": "set_party_size",
                    "args": {"size": 4.0}
                  }
                }
              },
              {
                "expectation": {
                  "note": "Check_Readback_Contains_4",
                  "agentResponse": {
                    "chunks": [{"text": "4"}]
                  }
                }
              }
            ]
          },
          {
            "steps": [
              {"userInput": {"text": "Yes, that's correct"}},
              {
                "expectation": {
                  "note": "Check_ConfirmPending_Called",
                  "toolCall": {"tool": "confirm_pending"}
                }
              },
              {
                "expectation": {
                  "note": "Check_Asks_For_Date",
                  "agentResponse": {
                    "chunks": [{"text": "date"}]
                  }
                }
              }
            ]
          }
        ]
      }
    }
    ```

=== "Golden eval — multi-slot batching (critical)"

    This eval is the most important one. It verifies that three setter tools fire in a single turn — the core batching behavior.

    ```json
    {
      "displayName": "Multi-Slot - Three Fields in One Message",
      "description": "User gives party size, date, AND name in one message. All three setter tools must fire in one turn.",
      "tags": ["slot-filling", "multi-slot", "critical"],
      "golden": {
        "turns": [
          {
            "steps": [
              {
                "userInput": {
                  "text": "I'd like to book a table for 2 on June 20th under the name Johnson"
                }
              },
              {
                "expectation": {
                  "note": "Check_SetPartySize_Called",
                  "toolCall": {
                    "tool": "set_party_size",
                    "args": {"size": 2.0}
                  }
                }
              },
              {
                "expectation": {
                  "note": "Check_SetPreferredDate_Called",
                  "toolCall": {
                    "tool": "set_preferred_date",
                    "args": {"date": "2026-06-20"}
                  }
                }
              },
              {
                "expectation": {
                  "note": "Check_SetGuestName_Called",
                  "toolCall": {
                    "tool": "set_guest_name",
                    "args": {"name": "Johnson"}
                  }
                }
              },
              {
                "expectation": {
                  "note": "Check_Readback_All_Three",
                  "agentResponse": {
                    "chunks": [{"text": "Johnson"}]
                  }
                }
              }
            ]
          }
        ]
      }
    }
    ```

=== "Scenario eval — full simulated conversation"

    Scenario evals run a full multi-turn conversation with a simulated user. They check overall behavior rather than exact tool calls.

    **`evaluations/Scenario_Full_Booking.json`:**

    ```json
    {
      "displayName": "Scenario - Full Booking Flow (Simulated User)",
      "description": "Simulated user completes a full reservation. Tests the entire slot filling pipeline.",
      "tags": ["slot-filling", "scenario", "e2e"],
      "scenario": {
        "task": "Make a dinner reservation at Bella Notte for 4 people on June 17th, 2026. When asked for a time, choose 7 PM. The reservation name is Garcia. No special requests. When the agent reads back information, confirm with 'yes'.",
        "rubrics": [
          "The agent must collect all required information: party size, date, time, name, and special requests.",
          "The agent must confirm each piece of information with the guest before proceeding.",
          "The agent must be warm and inviting, like a restaurant host."
        ],
        "userFacts": [
          {"name": "party_size",    "value": "4"},
          {"name": "preferred_date", "value": "June 17th, 2026"},
          {"name": "preferred_time", "value": "7 PM"},
          {"name": "guest_name",    "value": "Garcia"},
          {"name": "special_requests", "value": "none"}
        ],
        "maxTurns": 20
      }
    }
    ```

---

## Step 8 — Running evals and iterating

=== "Push and run evals"

    After writing your tools and callback, push to the platform and run your evals:

    ```bash
    # Push the app
    cxas push --project_id your-gcp-project --location us --app_name bella-notte

    # Push evaluations
    cxas push-eval --project_id your-gcp-project --location us \
        --app_name bella-notte \
        --eval_file evaluations/Happy_Path_Linear_Flow.json

    # Run evals using the AI skill
    python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py \
        --message "run evals" --runs 3
    ```

=== "Smoke test first"

    !!! tip "Always smoke test before a full suite run"
        Run 2-3 evals manually after pushing to verify the agent responds at all before investing time in a full suite run. A misconfigured callback or missing variable will fail silently — the agent simply won't respond — and a full suite run will report 0/N passing without a useful error.

    ```bash
    # Run a single eval to verify the agent is live
    cxas run --eval "Happy Path - Linear Reservation Flow" --turns 2
    ```

=== "Reading results"

    Golden eval results show which `toolCall` and `agentResponse` expectations passed or failed. Common failure patterns and their fixes:

    | Failure | Likely cause | Fix |
    |---|---|---|
    | `toolCall` expectation failed | LLM didn't call the setter | Check docstring verbosity; add batching example to instruction |
    | `agentResponse` expectation failed | Response didn't contain expected text | Check `_system_message` is being set and relayed |
    | `INVALID` result | Missing `agentResponse` expectation in the turn | Add `agentResponse` to every turn in the eval |
    | Task didn't fire | Inputs not all in `filled` | Check `_task_inputs_ready` logic; verify readback confirmation flow |
    | Preemption not happening | `len(llm_request.contents) > 1` failed | Check that prior turns have content; verify callback is registered |

=== "Iterating"

    The typical iteration loop after a failing eval:

    1. Look at the specific expectation that failed.
    2. Add a debug print to the callback or tool to see `sm` state at the point of failure.
    3. Push the change with `cxas push`.
    4. Re-run the failing eval (not the full suite).
    5. Repeat until passing, then run the full suite.

    After stabilizing the happy path, add evals for error recovery:

    ```bash
    # Run evals with 5 runs per eval to catch flakiness
    python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py \
        --message "what changed" --runs 5
    ```

---

## What to build next

You now have a working Bella Notte agent with a full eval suite. The natural extensions are:

- **[Stabilization gotchas](../patterns/slot-filling.md#the-7-stabilization-gotchas)** — read the full set of 7 gotchas in the Slot Filling pattern for advanced edge cases (steer-back, conditional slots).
- **Conditional slots** — the Bella Notte agent can be extended with a `contact_phone` slot that only appears for parties of 5 or more. See the `condition` field in the [DAG config concept](../patterns/slot-filling.md#dag-config-concept).
- **Real availability API** — replace the mock `_find_available_times` function with a real HTTP call to your reservation system.
- **[Dynamic Prompting](../patterns/dynamic-prompting.md)** — add a different, shorter instruction for the post-booking phase so the LLM doesn't keep trying to collect reservation details after booking is complete.
- **[Self-Healing Errors](../patterns/self-healing.md)** — add `agent_action` to the booking tool so the LLM has explicit recovery instructions when the API fails.
