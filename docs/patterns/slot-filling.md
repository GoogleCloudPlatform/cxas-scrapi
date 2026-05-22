---
title: Slot Filling Pattern
description: Build deterministic, production-ready slot-filling agents by keeping state in Python — not in the LLM's memory — and letting a DAG engine decide when to collect data and when to fire tasks.
---

# Slot Filling Pattern

Collecting multiple structured inputs from a user in a conversational flow sounds simple. In practice, it's one of the hardest things to get right on CX Agent Studio. This page documents the **Slot Filling Pattern** — the approach developed from production experience with real agents — including the architecture, the three control surfaces that govern behavior, the critical callback preemption mechanism, and a full set of stabilization gotchas distilled from shipping.

---

## The problem

CXAS has no native slot-filling primitive. The platform's XML `<taskflow>` in agent instructions relies on LLM state to track what has been collected and what to ask next. This creates four failure modes in production:

| Failure mode | What happens |
|---|---|
| **State fragility** | The LLM "forgets" a slot value mid-conversation, especially in long sessions. |
| **Premature task firing** | The LLM decides to call a backend API before all required inputs are collected. |
| **Progressive disclosure failure** | The LLM previews future steps ("After you give me your date, I'll also need your credit card") instead of asking one question at a time. |
| **Validation bypass** | The LLM accepts obviously invalid input ("party of 50") without calling the validation tool, generating its own error message instead. |

All four failures share a root cause: the LLM is being asked to manage state, control flow, and validation simultaneously. It's good at none of those things.

---

## The solution

Split the problem across two components:

- **Python (the callback)** owns state, control flow, task firing, validation, and retry logic.
- **The LLM** owns language: parsing user intent, calling the right setter tool, and generating warm responses.

The LLM never decides "should I call the booking API now?" or "have I collected enough information?" — it calls setter tools as the user provides information, and follows `_system_message` to know what to ask next. Python handles everything else.

This is the **Slot Filling Pattern** (sometimes called the slot filling DAG framework).

---

## Architecture

<figure class="diagram">
  <!-- svg-source:excalidraw -->
  <img src="../../assets/diagrams/slot-filling-flow.svg" alt="Slot Filling Data Flow">
  <figcaption>Data flows from the user through the LLM to setter tools, which write to <code>context.state</code>. The before_model_callback evaluates the DAG and either preempts the LLM or sets <code>_system_message</code> to guide the response. The after_model_callback injects stashed rich payloads on non-preempted turns.</figcaption>
</figure>

```
User ──► LLM ──► Setter Tool ──► context.state['sm']
                                        │
                                        ▼
                              before_model_callback
                                  (DAG evaluation)
                                        │
                          ┌─────────────┴──────────────┐
                          │                            │
                    Inputs ready?              Not yet ready
                          │                            │
                          ▼                            ▼
                   Fire task →               Set _system_message
                   Preempt LLM               (next question)
                   + response parts          + stash payloads
                          │                            │
                          ▼                            ▼
                   Auto-fire result          LLM relays naturally
                   (zero extra turns)               │
                                                    ▼
                                          after_model_callback
                                          (inject stashed payloads)
```

---

## The `sm` state variable

All slot filling state lives in a single session-scoped dict named `sm`. You declare it as a `STRING` type variable in your app's `variableDeclarations`.

```json
{
  "filled":         {},
  "pending":        {},
  "deferred":       {},
  "task_results":   {},
  "_retries":       {},
  "_slot_errors":   [],
  "_system_message": "",
  "_steer_back_turns": 0,
  "status":         "in_progress"
}
```

| Key | Purpose |
|---|---|
| `filled` | Confirmed slot values: `{"party_size": 4, "preferred_date": "2026-06-17"}` |
| `pending` | Values awaiting user confirmation (readback) before moving to `filled` |
| `deferred` | Values held for grouped readback (deferred mode) before task-level confirmation |
| `task_results` | Successful task outputs: `{"FindAvailableTimes": {"times": "6 PM, 7:30 PM"}}` |
| `_retries` | Failure counts — task retries (`"CheckAvailability": 1`) and slot validation retries (`"slot:party_size": 2`) |
| `_slot_errors` | Validation errors from setter tools: `[{"slot": "party_size", "code": "out_of_range"}]` |
| `_system_message` | The next message for the LLM to relay — written by the callback, read via `{{system_message}}` in the instruction |
| `_steer_back_turns` | Consecutive off-topic turns since last forward progress — drives 3-tier steer-back |
| `status` | `"in_progress"` \| `"complete"` \| `"escalated"` |

!!! tip "The `sm` variable is the source of truth"
    The LLM does not track what has been collected. If you need to know whether `party_size` has been collected, check `sm['filled']['party_size']` in Python — never ask the LLM.

---

## The four control surfaces

The Slot Filling Pattern has four places where you configure behavior. Getting all four right is the key to a stable agent.

### 1. Agent instruction (most critical)

The instruction tells the LLM its role in the pattern: call setter tools for every piece of information the user provides, relay `_system_message` naturally, and never preview future steps.

The single most important detail is including a **concrete multi-slot example** in the batching rule. Abstract instructions alone ("call ALL setter tools") are insufficient — the LLM needs to see a specific example to generalize the batching behavior reliably.

```xml
<slot_filling_protocol>
You are operating in SLOT FILLING mode. Follow these rules strictly:

1. TOOL-DRIVEN CONVERSATION: After each user message, identify EVERY piece
   of information the user provided and call ALL corresponding setter tools
   in the SAME response. For example, if the user says "table for 2 on
   June 20th under the name Johnson", call set_party_size, set_preferred_date,
   AND set_guest_name — all in one turn.

2. PROGRESSIVE DISCLOSURE: Only ask ONE question at a time.
   Never preview future steps.

3. RELAY SYSTEM MESSAGES: When _system_message is set in the system
   directive, incorporate it naturally into your response. When it contains
   specific times, names, or confirmation numbers, you MUST include those
   exact values.

4. ALWAYS CALL TOOLS: Call the setter tool for every piece of information
   the user provides, even if the value seems out of range. The system
   validates all inputs and handles errors automatically.

5. NATURAL CONVERSATION: If the user asks questions unrelated to the flow,
   answer helpfully but return to the reservation.
</slot_filling_protocol>

<system_directive>
{{system_message}}
</system_directive>
```

### 2. Tool docstrings (keep short)

Docstrings should describe **what the tool does** and **what format to prepare the argument in** — nothing else.

!!! warning "Verbose docstrings break batching"
    This is the most counterintuitive failure mode. Verbose docstrings (with caveats like "only call after party size is confirmed" or "valid range: 1-8") cause the LLM to focus on each tool individually rather than batching multiple calls. In one production test, adding two extra sentences to a docstring dropped multi-slot batching from 3/3 to 0/3. Keep docstrings to 1-2 sentences: format + trigger phrase only.

**Good** — format conversion and trigger phrase:
```python
def set_preferred_date(date: str) -> dict:
    """Record the reservation date in YYYY-MM-DD format.

    Convert natural language ('next Friday', 'June 20th') to YYYY-MM-DD.
    Call immediately when any date is mentioned.
    """
```

**Bad** — duplicates what the framework already enforces:
```python
def set_preferred_date(date: str) -> dict:
    """Record the reservation date in YYYY-MM-DD format.

    Convert natural language to YYYY-MM-DD. Only call after party size
    has been confirmed. Date must be in the future. We are open 5–10 PM.
    """
```

The second version teaches the LLM things Python already validates, giving it reasons to skip the tool call and improvise its own error message instead.

### 3. `before_model_callback` (the DAG engine)

The callback is pure Python that runs before every LLM turn. It reads `sm` state, evaluates the DAG, and either:

- Sets `sm['_system_message']` with the next question, then returns `OK` (LLM generates the response).
- Fires a task, stores the result, and **preempts** the LLM (returns the message directly, skipping model generation entirely).

```python
def before_model_callback(callback_context, llm_request):
    sm = callback_context.state['sm']

    # Terminal state — let the LLM respond freely
    if sm.get('status') in ('complete', 'escalated'):
        return None

    # Evaluate DAG: check if any task inputs are now satisfied
    filled = sm.get('filled', {})

    if _task_inputs_ready(sm, 'FindAvailableTimes'):
        result = _execute_find_times(filled)
        sm['task_results']['FindAvailableTimes'] = result
        sm['filled']['available_times'] = result['times']

    if _task_inputs_ready(sm, 'BookReservation'):
        result = _execute_book(filled)
        sm['task_results']['BookReservation'] = result
        sm['filled']['confirmation_number'] = result['confirmation']
        sm['status'] = 'complete'

    # Set _system_message for the LLM
    next_q, _ = _next_question(sm)
    sm['_system_message'] = next_q

    # Preempt if a task just fired (more than the initial user message in context)
    if _task_just_fired(sm) and llm_request.contents and len(llm_request.contents) > 1:
        from google.cloud.aiplatform_v1beta1.types import content as gapic_content
        return LlmResponse.from_parts(
            parts=[Part.from_text(text=sm['_system_message'])]
        )

    return None
```

### 4. `after_model_callback` (payload injection)

When the engine doesn't preempt — the LLM generates a response — rich payloads (cards, chips) can't be included in `before_model_callback`'s return value because there is no return value. Instead, the engine stashes payloads in `sm["_pending_payloads"]` or `sm["_pending_question_payloads"]`, and the `after_model_callback` appends them to the LLM's output.

```python
def after_model_callback(callback_context, llm_response):
    sm = callback_context.state.get("sm", {})

    announce = sm.pop("_pending_payloads", None)
    question = sm.pop("_pending_question_payloads", None)

    if not announce and not question:
        return None

    # Guard: only inject on first model call per turn
    for event in reversed(callback_context.events):
        if event.is_user():
            break
        if event.is_agent() and event.parts():
            return None

    extra_parts = _extract_payload_parts(announce or question)
    combined = list(llm_response.content.parts) + extra_parts
    return LlmResponse.from_parts(parts=combined)
```

This callback is pure framework code — copy it unchanged from the reference implementation.

---

## Setter tool template

Each user-collected slot gets one Python setter tool (or a multi-slot setter covering related slots). The tool validates the input and returns a structured result. The `after_tool_callback` handles all state routing — setters never access `sm` directly.

**Single-slot setter:**

```python
def set_party_size(party_size: str) -> dict:
    """Record the number of guests (1-8).

    Convert natural language: 'just me'=1, 'a couple'=2, 'four of us'=4.
    """
    try:
        count = int(party_size)
    except (ValueError, TypeError):
        return {"error": True, "error_code": "parse_error"}

    if count < 1 or count > 8:
        return {"error": True, "error_code": "out_of_range"}

    return {"stored": True, "value": count}
```

**Multi-slot setter:**

```python
def set_reservation_basics(
    party_size: int = 0,
    preferred_date: str = "",
) -> dict:
    """Record party size and/or preferred date.

    Parse natural language for party size and dates.
    Call when the guest mentions either value.
    """
    values = {}
    field_errors = {}

    if party_size:
        try:
            size = int(party_size)
        except (ValueError, TypeError):
            field_errors["party_size"] = "parse_error"
            size = None
        if size is not None:
            if not (1 <= size <= 8):
                field_errors["party_size"] = "out_of_range"
            else:
                values["party_size"] = size

    if preferred_date:
        # validation logic...
        values["preferred_date"] = str(preferred_date).strip()

    if not values and not field_errors:
        return {"error": True, "error_code": "no_input"}

    result = {"stored": True, "values": values}
    if field_errors:
        result["field_errors"] = field_errors
    return result
```

**Key design principles for setters:**

- **Setters are thin.** No DAG logic, no control flow, no knowledge of other slots.
- **Setters return structured results.** `{"stored": True, "value": ...}` for single-slot, `{"stored": True, "values": {...}}` for multi-slot. The `after_tool_callback` handles all state routing.
- **Setters signal errors, not messages.** Return `{"error": True, "error_code": "..."}` with a code; the callback resolves the user-facing message from your config.
- **The LLM does the parsing.** The setter for `preferred_date` expects `YYYY-MM-DD` — the LLM converts "next Thursday" to `"2026-07-17"` before calling the tool. This plays to the LLM's strength while keeping the setter deterministic.

---

## The `_next_question()` helper

This function walks the ordered slot list and returns the first unfilled slot whose dependencies are satisfied. It's the core of the progressive disclosure mechanism — the LLM only ever sees the current question, never future ones.

```python
def _next_question(sm: dict) -> tuple[str, str | None]:
    filled = sm.get('filled', {})

    order = [
        ('party_size',    'How many guests will be joining you?'),
        ('preferred_date', 'What date were you thinking?'),
    ]

    # available_times is a task output slot — only appears in the order
    # after the FindAvailableTimes task has populated it
    if 'available_times' in filled:
        times = filled['available_times']
        order.append(('selected_time', f'We have {times}. Which time works for you?'))

    order += [
        ('guest_name',       'What name should I put the reservation under?'),
        ('special_requests', 'Any special requests, or shall I note none?'),
    ]

    for slot_name, question in order:
        if slot_name not in filled:
            return question, slot_name

    return 'All information collected!', None
```

!!! note "Task output slots in the order list"
    Notice that `available_times` (populated by the `FindAvailableTimes` task) and `selected_time` (collected after times are known) are added **conditionally** — only after the task has run. This is the DAG dependency in action: `selected_time` cannot be asked until `available_times` is filled.

---

## DAG config concept

For more complex agents, you can declare the slot/task dependency graph explicitly rather than encoding it procedurally in `_next_question`. The declarative config makes the DAG visible as data — easier to validate, modify, and test.

```python
config = {
    'slots': [
        {'name': 'party_size',    'source': 'user',
         'setter': 'set_reservation_basics',
         'setter_field': 'party_size',
         'ask': 'How many guests will be joining you?',
         'requires_readback': True,
         'validation': {
             'errors': {
                 'out_of_range': 'We accept parties of 1 to 8 guests.',
                 'parse_error':  "I didn't catch the number of guests.",
             },
             'max_retries': 3,
         }},
        {'name': 'preferred_date', 'source': 'user',
         'setter': 'set_reservation_basics',
         'setter_field': 'preferred_date',
         'ask': 'What date were you thinking?',
         'requires_readback': True},
        {'name': 'available_times', 'source': 'task:FindAvailableTimes'},
        {'name': 'selected_time',   'source': 'user',
         'setter': 'set_selected_time',
         'requires': ['available_times'],
         'ask': 'We have {available_times}. Which time works for you?',
         'requires_readback': True},
        {'name': 'guest_name',      'source': 'user',
         'setter': 'set_guest_info',
         'setter_field': 'guest_name',
         'ask': 'What name should I put the reservation under?',
         'requires_readback': True},
        {'name': 'special_requests', 'source': 'user',
         'setter': 'set_guest_info',
         'setter_field': 'special_requests',
         'ask': 'Any special requests, or shall I note none?',
         'requires_readback': True},
    ],
    'tasks': [
        {'name': 'FindAvailableTimes',
         'inputs': ['party_size', 'preferred_date'],
         'outputs': {'times': 'available_times'},
         'success_check': 'success',
         'then_say': 'We have {available_times}. Which time works for you?'},
        {'name': 'BookReservation',
         'inputs': ['party_size', 'preferred_date', 'selected_time',
                    'guest_name', 'special_requests'],
         'outputs': {'confirmation': 'confirmation_number'},
         'success_check': 'success',
         'terminal': True,
         'then_say': "You're confirmed! Your number is {confirmation_number}."},
    ],
}
```

The `requires` field on `selected_time` enforces the dependency at two levels:

1. The question is skipped in `_next_question` until `available_times` is filled.
2. The setter tool is **hidden** from the LLM's tool list until the dependency is met — so the LLM structurally cannot call `set_selected_time` before availability is known.

---

## The callback preemption mechanism

!!! warning "Callback Preemption — the most critical architectural detail"
    When the callback fires a task and preempts the LLM, **the LLM does not get a turn**. Any setter tools the LLM had queued in the current response but not yet called are **permanently lost**. This is why the agent instruction must require all setter tools to be called in the **same response** — deferring any setter to a later turn creates a race condition with the callback.

Preemption uses `LlmResponse.from_parts()` to bypass LLM generation entirely:

```python
if task_fired and llm_request.contents and len(llm_request.contents) > 1:
    return LlmResponse.from_parts(
        parts=[Part.from_text(text=sm['_system_message'])]
    )
```

The `len(llm_request.contents) > 1` guard prevents preempting on the first user message — the LLM should always generate a natural greeting rather than a canned "How many guests?" on the opening turn.

**The full flow:**

```
User message
    → LLM calls setter tool(s) in one response
    → before_model_callback fires
        → Check DAG: are all task inputs now satisfied?
            YES → fire task, store result in sm
                → If multi-content (not first turn): PREEMPT
                    → LlmResponse returned directly — LLM skipped
                    → Task result delivered verbatim
            NO → compute next question
                → Set sm['_system_message']
                → Return OK → LLM generates response using _system_message
```

**Five preemption triggers** (not just task firing):

1. **Task fires** — task result message delivered verbatim.
2. **Slot validation error** — error message from config delivered verbatim.
3. **Readback transition** — after `confirm_pending`, the next question is delivered with a warm prefix.
4. **Steer-back (hard)** — off-topic for `hard_after` turns; forced re-ask of question/readback.
5. **Steer-back (escalate)** — off-topic for `escalate_after` turns; escalation.

---

## Step-by-step: adding slot filling to your agent

=== "Step 1 — Define your slots"

    List every piece of information you need to collect, in the natural order to ask for it, and every backend task that fires from those inputs.

    For a restaurant reservation:
    - `party_size` (user) → enables `FindAvailableTimes`
    - `preferred_date` (user) → enables `FindAvailableTimes`
    - `available_times` (task output from `FindAvailableTimes`)
    - `selected_time` (user, requires `available_times`)
    - `guest_name` (user)
    - `special_requests` (user) → enables `BookReservation`

=== "Step 2 — Add the `sm` variable"

    In your CX Agent Studio app's variable declarations, add an `sm` variable of type `STRING` with this initial value:

    ```json
    {"filled": {}, "pending": {}, "task_results": {}, "_retries": {},
     "_slot_errors": [], "_system_message": "", "status": "in_progress"}
    ```

=== "Step 3 — Write setter tools"

    One Python tool per user-collected slot (or a multi-slot setter covering related slots). Follow the template: validate → return `{"stored": True, "value": ...}` (or `{"values": {...}}` for multi-slot). For invalid input, return `{"error": True, "error_code": "..."}`.

    Keep docstrings to 2 sentences: format + trigger phrase.

=== "Step 4 — Write `_next_question()`"

    An ordered list of `(slot_name, question)` pairs. Task output slots (like `available_times`) are added conditionally after their task has run. Return the first slot not in `sm['filled']`.

=== "Step 5 — Write `before_model_callback`"

    Check terminal state first. Evaluate the DAG (check whether each task's inputs are all in `sm['filled']`). Fire any ready tasks. Set `sm['_system_message']`. Preempt if a task fired and this is not the first turn.

=== "Step 6 — Write the agent instruction"

    Include the `<slot_filling_protocol>` block. Include a **concrete multi-slot example** in the batching rule. End with a `<system_directive>{{system_message}}</system_directive>` block so the LLM can see `_system_message`.

=== "Step 7 — Write evaluations"

    Cover: single slot per turn, multi-slot batching, error recovery, out-of-range input, natural language parsing, conversational detours, and the full happy path end-to-end.

---

## Slot Filling vs. XML Taskflow

| Aspect | XML Taskflow | Slot Filling |
|---|---|---|
| State tracking | LLM memory — degrades over long conversations | `context.state` — deterministic, persists across turns |
| Task firing | LLM decides when (unreliable, premature or late) | Auto-fires when inputs ready — exact, every time |
| Progressive disclosure | Prompt-based — LLM sees all stages and can preview | Tool-driven — LLM only sees the current question |
| Validation | LLM-based — hallucinates error messages | Python code — exact validation, config-driven messages |
| Retry logic | LLM improvises — counts reset each turn | `_retries` dict — persists across turns, bounded by `max_retries` |
| Dependency enforcement | Prompt-based (fragile) | Tool visibility — LLM cannot call a hidden setter |
| Testability | Hard — non-deterministic LLM state | Easy — pure Python DAG functions, testable with dicts |

---

## Writing evaluations for slot-filling agents

<figure class="diagram">
  <img src="../../assets/diagrams/eval-workflow.svg" alt="Evaluation Workflow">
  <figcaption>Golden evals test deterministic turn-by-turn behavior; scenario evals test open-ended goals. Both feed into the scoring pipeline.</figcaption>
</figure>

A healthy eval suite for a slot-filling agent covers five categories:

| Category | What to test |
|---|---|
| **Happy path** | Single slot per turn; multi-slot batching (2+ slots in one message); the full end-to-end flow |
| **Error recovery** | Invalid input (out of range, wrong format); the agent re-asks after error |
| **Guard rails** | Out-of-scope requests; off-topic detours that return to the flow |
| **NLP parsing** | Natural language dates ("next Friday"), quantities ("a couple"), times ("seven thirty PM") |
| **Conversational detours** | User asks a question mid-flow; agent answers and returns to collection |

**Golden eval structure for multi-slot batching:**

```json
{
  "golden": {
    "turns": [
      {
        "steps": [
          {"userInput": {"text": "Table for 2 on June 20th under Johnson"}},
          {"expectation": {"toolCall": {"tool": "set_party_size",
                                        "args": {"size": 2.0}}}},
          {"expectation": {"toolCall": {"tool": "set_preferred_date",
                                        "args": {"date": "2026-06-20"}}}},
          {"expectation": {"toolCall": {"tool": "set_guest_name",
                                        "args": {"name": "Johnson"}}}},
          {"expectation": {"agentResponse": {"chunks": [{"text": "Johnson"}]}}}
        ]
      }
    ]
  }
}
```

!!! warning "`agentResponse` is required in every turn"
    Every `expectations` block must include an `agentResponse` check, even if it only verifies one word. An evaluation step with only a `toolCall` expectation is flagged as `INVALID` by the platform, and `INVALID = FAIL`. This is the most common cause of unexpected evaluation failures.

**Scenario eval for end-to-end flow:**

```json
{
  "scenario": {
    "task": "Make a dinner reservation for 4 people on June 17th. When asked for a time, choose 7 PM. Name is Garcia. No special requests.",
    "rubrics": [
      "The agent must collect party size, date, time, name, and special requests.",
      "The agent must be warm and inviting."
    ],
    "maxTurns": 20
  }
}
```

---

## The 7 stabilization gotchas

These are the failure modes encountered shipping slot-filling agents to production, distilled into concrete, actionable items.

??? "1. Missing `agentResponse` in evaluation expectations"
    Every turn in a golden eval must have an `agentResponse` expectation in its `steps` block. If a step only checks `toolCall` with no `agentResponse`, the platform marks it `INVALID`, which counts as a failure.

    **Fix:** Add even a minimal `agentResponse` check — a single word that should appear in the response — to every turn.

??? "2. Verbose docstrings break multi-slot batching"
    Adding validation details ("valid range: 1-8"), prerequisite caveats ("only call after date is confirmed"), or business rules ("we're closed on Mondays") to tool docstrings regresses batching. The LLM reads each tool's docstring separately, which fragments its attention away from the batching rule in the instruction.

    **Fix:** Keep docstrings to 2 sentences. Put all business rules in the agent instruction or enforce them in Python code.

??? "3. Missing concrete example in the batching instruction"
    The instruction rule "call ALL setter tools in the SAME response" is abstract. The LLM needs a specific, realistic example to generalize the behavior.

    **Fix:** Add a concrete example like: *"if the user says 'table for 2 on June 20th under the name Johnson', call set_party_size, set_preferred_date, AND set_guest_name — all in one turn."*

??? "4. Callback preemption races with deferred tool calls"
    If the LLM calls setter A in one tool call but defers setter B to the next response, and the callback fires between them (e.g., because setter A completed all of task X's inputs), setter B is never called. The preempted response overwrites whatever the LLM was about to say.

    **Fix:** The agent instruction must require all setters to be called in one response. The concrete example in gotcha 3 helps drive this behavior.

??? "5. Task output slots missing from `_next_question` order"
    If `available_times` (populated by a task) is not in the `_next_question` order, `selected_time` will never appear as the next question — the function will fall through to `guest_name` instead.

    **Fix:** Add task output slots to the `order` list conditionally: check that the slot is in `sm['filled']` before including the dependent slot's question in the list.

??? "6. `_system_message` missing from some code paths"
    Every code path through the callback must set `sm['_system_message']`. If an error branch or early-return path forgets to set it, the LLM will see a stale message from the previous turn and relay incorrect guidance.

    **Fix:** Set `sm['_system_message']` at the end of the main path, and explicitly `sm.pop('_system_message', None)` in any path that should clear it.

??? "7. Terminal task status not checked before firing tasks"
    After `BookReservation` fires and sets `sm['status'] = 'complete'`, the callback must check this status before re-evaluating the DAG. Without the check, the callback may attempt to re-fire `BookReservation` on every subsequent turn (all inputs are still in `filled`, and the task's inputs-ready check would pass again).

    **Fix:** Add a terminal state guard at the top of the callback:
    ```python
    if sm.get('status') in ('complete', 'escalated'):
        return None
    ```

---

## Reference implementation

The Bella Notte restaurant reservation agent is the canonical reference implementation of this pattern. It implements:

- 9 slots: `welcome`, `party_size`, `large_party_phone`, `preferred_date`, `available_times`, `selected_time`, `guest_name`, `special_requests`, `confirmation_number`
- 2 tasks: `FindAvailableTimes` (fires after `party_size` + `preferred_date`), `BookReservation` (terminal, fires after all required slots)
- 4 setter tools: `set_reservation_basics` (multi-slot: party_size + preferred_date), `set_guest_info` (multi-slot: guest_name + special_requests), `set_selected_time`, `set_large_party_phone`
- Rich response payloads: welcome cards, suggestion chips, confirmation info cards
- 20+ golden evals, 5+ scenario evals

[Build it yourself in the Restaurant Reservation Tutorial →](../tutorials/restaurant-reservation.md)

---

## Detailed guides

For a hands-on, step-by-step walkthrough of building a slot-filling agent — from two simple slots to the full DAG with conditional logic, deferred readback, and error handling — see the **Slot Filling Guide**:

- [Slot Filling Overview](../guides/slot-filling/index.md) — mental model, key concepts, and architecture
- [Tutorial: Building an Agent](../guides/slot-filling/tutorial.md) — progressive tutorial using the Bella Notte example
- [Advanced Patterns](../guides/slot-filling/advanced.md) — conditional slots, event pre-filling, announce slots, deferred readback
- [Configuration Reference](../guides/slot-filling/reference.md) — complete field-by-field reference for the DAG config
