# The Slot Filling DAG Framework

A reusable design pattern for building deterministic, LLM-powered conversational agents that collect structured data from users, execute backend operations, and handle failures — all without relying on the LLM to make control-flow decisions.

> **Full reference:** See [`slot_filling_dag_framework.md`](slot_filling_dag_framework.md) in this directory for the complete framework specification (1000+ lines covering every mechanism, guarantee, and edge case).

---

## When to Use This Pattern

**Use slot filling when the agent's primary job is collecting structured data to fire backend operations.** The more of these apply, the stronger the case:

- Multiple fields to collect with dependencies between them (e.g., can't pick a time until availability is known)
- Validation rules with specific error messages and retry limits
- Backend tasks that fire automatically when inputs are ready
- Escalation paths when retries exhaust
- Readback/confirmation before committing values
- The conversation must always make forward progress (no infinite loops)

**Examples:** restaurant reservations, flight booking, insurance claims intake, patient registration, order placement, account onboarding.

**Don't use slot filling for:**
- Simple Q&A or knowledge-base lookup agents
- Single-tool agents with no multi-step collection
- Agents where the LLM needs judgment-based control flow (triage, troubleshooting)
- Agents with only 1-2 fields and no dependencies

For these simpler cases, use XML `<taskflow>` instructions or the trigger pattern (see `references/gecx-design-guide.md`).

---

## Architecture Overview

The framework splits the problem: the LLM owns **language** (parsing user intent, generating natural responses), while a deterministic Python callback owns **control flow** (what to ask next, when to fire a task, how many retries remain, when to escalate).

### Three-Layer Architecture

Everything lives in one callback file (CES limitation: callables can't cross tool boundaries):

```
┌──────────────────────────────────────────────────────────────┐
│ 1. _get_config()          — Agent-specific                   │
│    Slots, tasks, executors, formatters, conditions.          │
│    Replace this per project.                                 │
├──────────────────────────────────────────────────────────────┤
│ 2. _run_slot_filling()    — CES-agnostic orchestrator        │
│    Takes config + state, returns action dict.                │
│    Never touches CES types. Reusable across projects.        │
├──────────────────────────────────────────────────────────────┤
│ 3. before_model_callback() — Thin CES adapter (~20 lines)   │
│    Reads state, calls orchestrator, writes _system_message,  │
│    applies tool visibility, handles preemption.              │
└──────────────────────────────────────────────────────────────┘
```

To create a new agent, replace only `_get_config()`. Everything below it is copied unchanged.

### What the LLM Owns vs. What Python Owns

| Concern | Owner | Mechanism |
|---|---|---|
| Understanding user intent | LLM | Tool selection based on user message |
| Parsing values from natural language | LLM | Tool arguments (e.g., "next Friday" → "2026-07-18") |
| Generating natural responses | LLM | Free-form text guided by `_system_message` |
| Deciding what to ask next | Python | DAG evaluation → `_find_next_question` |
| Deciding when to fire a task | Python | DAG evaluation → input readiness check |
| Counting retries and escalating | Python | `_retries` dict + `max_retries` config |
| Preventing invalid tool calls | Python | `_compute_hidden_tools` per turn |
| Handling validation errors | Python | `_handle_slot_errors` → config-driven messages |

**The LLM is a language interface, not a state machine.**

---

## Key Mechanisms

### Tool Visibility (Primary Control Mechanism)

The LLM should only see tools that are valid right now. This prevents entire classes of bugs where the LLM calls the wrong tool at the wrong time.

- **During readback**: hide all setters except inline-correction targets, show confirm/reject
- **Outside readback**: hide confirm/reject, hide filled slots, hide dependency-blocked slots
- **Fresh pending**: also hide confirm/reject to force readback before confirming

The LLM literally cannot call a tool it cannot see. This is more reliable than any prompt instruction.

### Setter Tool Pattern

Setters are thin — they validate, write to `pending`, and return. They contain zero DAG logic and zero knowledge of other slots.

```python
def set_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format.

    Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD.
    """
    sm = context.state["sm"]
    try:
        parsed = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        sm.setdefault("_slot_errors", []).append(
            {"slot": "date", "code": "invalid_format"}
        )
        return {"error": True}

    sm.setdefault("pending", {})["date"] = date
    return {"stored": True, "value": date}
```

On error, setters signal via `_slot_errors` — the orchestrator resolves the user-facing message from config, tracks retries, and escalates when exhausted.

### Preemption

When the framework knows exactly what to say (task result, error message, post-confirmation transition), it skips the LLM entirely via `LlmResponse.from_parts()`. Faster, deterministic, and consistent.

### Readback and Confirmation

Values go to `pending` first, get read back to the user for confirmation, then move to `filled`. The framework handles inline corrections, stall detection, and retry limits.

---

## Writing Prompts and Tool Docstrings

A critical design principle: **if the orchestrator enforces it, don't prompt for it.**

Every constraint in a prompt or docstring adds cognitive load. When constraints duplicate what the orchestrator already enforces deterministically, they cause the LLM to second-guess tool calls, pre-filter input, or generate its own error messages — bypassing the framework's error handling.

**Tool docstrings should describe what the tool does and what format to prepare, not when or whether to call it:**

```python
# GOOD — tells the LLM how to prepare the argument
"""Record the preferred date in YYYY-MM-DD format.
Convert natural language ('this Friday', 'July 4th') to YYYY-MM-DD."""

# BAD — duplicates orchestrator enforcement
"""Record the preferred date in YYYY-MM-DD format.
Only call after party size has been confirmed.  ← tool visibility handles this
Date must be in the future.                     ← code validates, config has error msg"""
```

**Agent instructions should describe the LLM's role, not the framework's job:**
- DO: "Call the matching setter tool for each piece of reservation info"
- DO: "Use sm._system_message as the basis for your response"
- DON'T: "Never call set_time before availability is shown" (tool is hidden)
- DON'T: "If the tool returns error, show the error message" (preempted before the LLM responds)

See Section 11 of the full framework doc for the complete decision checklist.

---

## Files in This Example

| File | Purpose |
|---|---|
| [`slot_filling_dag_framework.md`](slot_filling_dag_framework.md) | Full framework reference (1000+ lines) |
| [`callback.py`](callback.py) | Complete callback with config + framework |
| [`agent_instruction.md`](agent_instruction.md) | Agent instruction with slot filling protocol |
| [`tools/`](tools/) | All setter tools (one per user-sourced slot) |

### Quick Start

1. Read `slot_filling_dag_framework.md` to understand the concepts
2. Study `callback.py` — the top half (`_get_config()`) is agent-specific, the bottom half is the reusable framework
3. To build a new agent: copy `callback.py`, replace `_get_config()` with your slots/tasks/executors, create matching setter tools

---

## Advantages over Alternative Approaches

| Aspect | XML Taskflow | Trigger Pattern | Slot Filling Framework |
|---|---|---|---|
| State tracking | LLM memory (fragile) | State variable + callback | `context.state["sm"]` (deterministic) |
| Task firing | LLM decides (unreliable) | Callback on trigger flag | Auto-fires when inputs ready (DAG) |
| Tool visibility | All tools always visible | All tools always visible | Dynamic per-turn (only valid tools shown) |
| Validation | LLM-based (hallucinates) | Tool-level | Python code + config-driven error messages |
| Retry/escalation | Prompt-based | Callback-based (manual) | Automatic with configurable bounds |
| Multi-slot input | Hard (LLM must track) | Not designed for it | Natural (multiple setters in one turn) |
| Dependencies | Implicit in stage order | Manual in callback | Explicit `requires` + DAG evaluation |
| Best for | Simple flows, few fields | Deterministic single actions | Complex multi-field collection |
