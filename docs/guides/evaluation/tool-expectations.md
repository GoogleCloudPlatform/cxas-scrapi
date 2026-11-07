---
title: Tool Expectations
description: Deterministic assertions on tool calls, inputs, outputs, ordering, and session termination in SimulationEvals.
---

# Deterministic Tool Expectations

While LLM-judged `expectations` assess conversational quality and tone across the transcript, agents can sometimes state that an action occurred ("I have submitted your request") without actually executing the underlying tool.

**`tool_expectations`** provide deterministic, hard-fail assertions evaluated directly on the agent's tool execution trace. They verify that tools were invoked with correct parameters, returned expected payloads, occurred in the right sequence, and properly terminated or transferred the session.

---

## Key Benefits

- **Deterministic Evaluation**: Tool assertions execute as pure Python checks without LLM judging variance or hallucination.
- **Deep Payload Matching**: Supports nested dictionary subset matching, JSON decoding, boolean/numeric type coercion, and wildcard non-empty matching (`*`).
- **CES Envelope Unwrapping**: Automatically unwraps CES `{"result": {...}}` tool response envelopes.
- **Unified Reporting**: Tool checks fold seamlessly into simulation results as `[tool] ` prefixed expectation details, and render in HTML reports alongside step goals and conversational expectations.
- **Idempotent Scoring**: The final verdict preserves `judge_passed` while enforcing `passed = judge_passed and all_tool_checks_met`.

---

## YAML Specification

In your simulation YAML (`.yaml`, `.yml`, or `.json`), declare `tool_expectations` under each evaluation case:

```yaml
evals:
  - name: "order_placement_flow"
    tags: ["P0", "orders"]
    steps:
      - goal: "Place an order for item ITEM-42"
        success_criteria: "Agent confirms order placement"
        max_turns: 4

    expectations:
      - "The agent was polite and confirmed the order"

    tool_expectations:
      # 1. Assert lookup tool was invoked before order creation
      - type: tool_called
        tool: check_availability
        min: 1

      # 2. Prevent placing an order before availability check succeeds
      - type: tool_not_called
        tool: create_order
        before:
          tool: check_availability
          result:
            status: "available"

      # 3. Assert tool input arguments
      - type: tool_input
        tool: check_availability
        args:
          item_id: "ITEM-42"

      # 4. Assert order creation parameters using wildcards
      - type: tool_input
        tool: create_order
        args:
          item_id: "ITEM-42"
          quantity: 1
          confirmed: true

      # 5. Assert tool return value (automatically unwraps CES result envelopes)
      - type: tool_output
        tool: create_order
        result:
          is_scheduled: true
          order_id: "*"

      # 6. Ensure every call to account lookup includes account_id
      - type: every_call
        tool: lookup_account
        args:
          account_id: "10001"

      # 7. Assert ordering: check_availability must be followed by create_order
      - type: followed_by
        tool: check_availability
        next:
          tool: create_order

      # 8. Assert last tool call
      - type: last_tool
        tool: end_session

      # 9. Assert clean session termination with status
      - type: end_session
        args:
          status: "completed"
```

---

## Supported Operators

### 1. `tool_called`
Asserts that `tool` was invoked at least `min` times (default `min: 1`).

```yaml
- type: tool_called
  tool: lookup_account
  min: 1
```

### 2. `tool_not_called`
Asserts that `tool` was never called, or was not called before a qualifying marker event.

```yaml
# Never called anywhere in conversation
- type: tool_not_called
  tool: cancel_order

# Not called before availability check returned "available"
- type: tool_not_called
  tool: create_order
  before:
    tool: check_availability
    result:
      status: "available"
```

### 3. `tool_input`
Asserts that at least `min` calls to `tool` had argument payloads matching `args` (as a subset).

```yaml
- type: tool_input
  tool: create_order
  args:
    item_id: "ITEM-42"
    confirmed: true
  min: 1
```

### 4. `tool_output`
Asserts that at least `min` responses from `tool` returned payloads matching `result` (as a subset). Automatically unwraps CES `{"result": {...}}` response envelopes.

```yaml
- type: tool_output
  tool: create_order
  result:
    status: "confirmed"
    order_id: "*"
```

### 5. `last_tool`
Asserts that the final tool call made during the conversation was `tool`, optionally matching `args`.

```yaml
- type: last_tool
  tool: end_session
  args:
    reason: "resolved"
```

### 6. `every_call`
Asserts that every invocation of `tool` satisfied the provided `args` subset. If the tool was never called, this check passes.

```yaml
- type: every_call
  tool: update_profile
  args:
    verified: true
```

### 7. `followed_by`
Asserts that every invocation of `tool` was eventually followed by a subsequent call matching `next` (`tool` and optional `args`).

```yaml
- type: followed_by
  tool: check_availability
  next:
    tool: create_order
```

### 8. `terminal_tool`
Asserts that `tool` was called at most once, and that no other tool calls followed it.

```yaml
- type: terminal_tool
  tool: end_session
```

### 9. `end_session` (and alias `handoff`)
Asserts that the conversation concluded with an `end_session` tool call matching specified arguments or metadata.

```yaml
- type: end_session
  args:
    status: "completed"
```

---

## Value Matching Rules

- **Subset Matching**: Actual dictionary payloads may contain additional keys; only keys specified in the expectation are verified.
- **Wildcard (`*`)**: Matches any non-empty string, list, dictionary, or non-None value.
- **Type Coercion**:
  - **Booleans**: Matches booleans and case-insensitive strings (`True`, `"true"`, `"false"`).
  - **Numbers**: Compares numerically (e.g. `2` matches `2.0` or `"2"`).
  - **Strings & JSON**: String representations containing valid JSON are automatically parsed into dictionaries before matching.

---

## Trace Validation

Every run evaluated with `tool_expectations` automatically includes a built-in `trace_parsed` check ensuring that all tool execution lines in the simulation trace were successfully decoded without syntax or deserialization errors.

---

## Python API Usage

You can also evaluate tool expectations programmatically using `tool_expectations.apply_to_result`:

```python
from cxas_scrapi.evals import tool_expectations

test_case = {
    "name": "account_lookup_sim",
    "tool_expectations": [
        {"type": "tool_called", "tool": "lookup_account"},
        {"type": "last_tool", "tool": "end_session"},
    ],
}

# result is the dict returned by SimulationEvals._run_single_simulation_job
checks = tool_expectations.apply_to_result(test_case, result)
print(f"Passed: {result['passed']} (expectations: {result['expectations']})")
```
