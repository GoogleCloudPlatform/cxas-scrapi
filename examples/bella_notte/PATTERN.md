# The Slot Filling Pattern for CXAS

A reusable design pattern for implementing slot-filling conversational agents in
CES/Polysynth (CXAS).

## Problem

CXAS has no native slot-filling primitive. The existing approach (XML `<taskflow>`
in agent instructions) relies entirely on the LLM to track state, which is
fragile for complex dependency graphs and provides no progressive disclosure.

## Solution

The **Slot Filling** pattern uses Python tools to manage slot state
programmatically, while the LLM handles natural conversation. Key properties:

- **Tool-driven state**: Slot values stored in `context.state`, not LLM memory
- **Inline task firing**: When all inputs for a task are collected, it fires
  automatically within the setter tool (zero LLM round-trips)
- **Progressive disclosure**: Tools return only the next question; the LLM
  never sees future steps
- **Dependency graph**: Slots can declare `requires` dependencies, creating
  a DAG that the tools evaluate at runtime

## Architecture

```
User ──► LLM ──► Setter Tool ──► DAG Engine ──► Action Function
                      │                              │
                      ▼                              ▼
              context.state['sm']          Auto-fired task result
              (filled slots, results)     (returned in tool response)
```

### Components

| Component | What | Where |
|---|---|---|
| `sm` variable | Runtime state: `{filled, task_results, status}` | App `variableDeclarations` |
| `dag_config` variable (optional) | Declarative slot/task dependency graph | App `variableDeclarations` |
| Setter tools | One per user-collected slot | CES Python tools |
| `_next_question()` | Computes frontier slot from DAG | Inline in each tool |
| Action functions | Business logic (find availability, book, etc.) | Inline in setter tools |
| Agent instruction | Slot Filling Protocol section | Agent `instruction` |

### The `sm` State Variable

```json
{
  "filled": {},
  "task_results": {},
  "status": "in_progress"
}
```

- `filled`: `{slot_name: value}` — all collected values (user + task outputs)
- `task_results`: `{task_name: result}` — results of fired tasks
- `status`: `"in_progress"` | `"complete"` | `"escalated"`

### Setter Tool Template

Each setter tool follows this pattern:

```python
def set_<slot_name>(<param>: <type>) -> dict:
    """<description>"""
    # 1. Validate input
    if not valid(<param>):
        return {"error": True, "_system_message": "<error message>"}

    # 2. Store to state
    sm = context.state['sm']
    sm['filled']['<slot_name>'] = <param>

    # 3. DAG check: fire any tasks whose inputs are now all filled
    if _task_inputs_ready(sm, 'TaskName'):
        result = _execute_task(sm)
        if result.get('success'):
            sm['task_results']['TaskName'] = result
            sm['filled']['output_slot'] = result['output_value']

    # 4. Compute next question (progressive disclosure)
    next_q, next_slot = _next_question(sm)
    return {"stored": True, "next_question": next_q, "_system_message": next_q}
```

### The `_next_question()` Helper

This function implements the **frontier computation** — it walks the slot order,
skips filled and task-sourced slots, checks `requires` dependencies, and returns
the first askable slot:

```python
def _next_question(sm: dict) -> tuple:
    filled = sm['filled']
    # Define slot order with dependencies
    order = [
        ("slot_a", "Question for slot A?"),
        ("slot_b", "Question for slot B?"),
    ]
    # Conditionally add slots that depend on task outputs
    if 'task_output_slot' in filled:
        order.append(("slot_c", f"Choose from {filled['task_output_slot']}"))
    order += [
        ("slot_d", "Question for slot D?"),
    ]
    for slot_name, question in order:
        if slot_name not in filled:
            return question, slot_name
    return "All information collected!", None
```

### Agent Instruction Template

```xml
<slot_filling_protocol>
You are operating in SLOT FILLING mode. Follow these rules strictly:

1. TOOL-DRIVEN CONVERSATION: After each user message, call the appropriate
   setter tool to record their answer. The tool response will tell you what
   to ask next via the "_system_message" field.

2. PROGRESSIVE DISCLOSURE: Only ask ONE question at a time. Never preview
   future steps. Never tell the user how many steps remain.

3. RELAY SYSTEM MESSAGES: When a tool response contains "_system_message",
   incorporate that message naturally into your response.

4. TOOL SELECTION:
   - User mentions <topic_a> → set_slot_a
   - User mentions <topic_b> → set_slot_b
   ...

5. HANDLE ERRORS: If a tool returns "error": true, relay the _system_message
   and wait for corrected information.
</slot_filling_protocol>
```

## How to Instantiate

1. **Define your slots**: List all data to collect (user-sourced and task-sourced)
2. **Define your tasks**: Map input slots to tasks, and task outputs to output slots
3. **Create setter tools**: One CES Python tool per user-sourced slot
4. **Embed DAG logic**: In each setter, check if tasks can fire after storing
5. **Write agent instruction**: Copy the Slot Filling Protocol, customize tool
   selection rules
6. **Declare `sm` variable**: Add to App `variableDeclarations`
7. **Wire and test**: Link tools to agent, set root, run session tests

## Advantages over XML Taskflow

| Aspect | XML Taskflow | Slot Filling |
|---|---|---|
| State tracking | LLM memory (fragile) | `context.state` (deterministic) |
| Task firing | LLM decides when (unreliable) | Auto-fires when inputs ready |
| Progressive disclosure | Prompt-based (LLM sees all stages) | Tool-driven (LLM only sees next Q) |
| Validation | LLM-based (hallucinates) | Python code (exact) |
| Retry logic | Prompt-based | Programmatic with counters |
| Dependency graph | Implicit in stage ordering | Explicit in `requires` + DAG checks |

