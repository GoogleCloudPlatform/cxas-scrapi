---
title: Tool Tests
description: Test your tools in isolation with operator-based assertions using ToolEvals.
---

# Tool Tests

Tool tests let you verify that your tools work correctly in isolation — without running a full conversation through the agent. You call a tool with specific inputs and assert that the output matches your expectations using a set of comparison operators.

This is the fastest and most focused way to test tool behavior. Since tools are the interface between your agent and external systems, getting them right is critical.

---

## Why test tools separately

When an agent produces a wrong answer, the cause might be:

1. The tool returned the wrong data
2. The agent misinterpreted the tool's data
3. The instruction told the agent to do the wrong thing

Testing tools in isolation eliminates cause #1 from the equation. If your tool tests pass but your golden evals fail, you know the problem is in the instruction or the agent's reasoning — not the tool's implementation.

---

## YAML format

Tool test files use the `tests:` key:

```yaml
tests:
  - name: "lookup_order_found"
    tool: "lookup_order"
    args:
      order_id: "ORD-12345"
    variables:
      order_12345_status: "shipped"
      order_12345_eta: "2026-04-18"
    expectations:
      response:
        - path: "$.status"
          operator: equals
          value: "shipped"
        - path: "$.estimated_delivery"
          operator: contains
          value: "2026-04-18"
        - path: "$.order_id"
          operator: is_not_null

  - name: "lookup_order_not_found"
    tool: "lookup_order"
    args:
      order_id: "ORD-99999"
    variables:
      order_99999_status: "not_found"
    expectations:
      response:
        - path: "$.agent_action"
          operator: is_not_null
          # Ensures the tool returns an agent_action key on error
```

### Test fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique name for this test case |
| `tool` | string | The tool display name to invoke |
| `args` | dict | Arguments to pass to the tool |
| `variables` | dict | Session variables to inject (mock data) |
| `expectations` | dict | Expectations object with `response` list |

### Assertion fields

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | JSONPath expression pointing to the field to check |
| `operator` | string | Comparison operator (see below) |
| `value` | any | Expected value (optional for some operators) |

### Operators

| Operator | Description | `value` required? |
|----------|-------------|-------------------|
| `equals` | Exact equality | Yes |
| `contains` | String/list containment | Yes |
| `greater_than` | Numeric greater-than | Yes |
| `less_than` | Numeric less-than | Yes |
| `length_equals` | Collection/string length equals | Yes |
| `length_greater_than` | Collection/string length greater-than | Yes |
| `length_less_than` | Collection/string length less-than | Yes |
| `is_null` | Field is null or missing | No |
| `is_not_null` | Field is present and not null | No |

### JSONPath expressions

The `path` field uses JSONPath syntax. Some examples:

```
$.status                    # Top-level "status" key
$.order.estimated_delivery  # Nested field
$.items[0].name             # First item in a list
$.items.length()            # Length of a list
```

---

## Running with the CLI

```bash
cxas test-tools \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/tool_tests/order_tests.yaml
```

The CLI prints a summary table and exits with code `0` if all tests pass, `1` if any fail.

### Debug mode

When a test fails and you're not sure why, use `--debug` to see the full tool request and response:

```bash
cxas test-tools \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/tool_tests/order_tests.yaml \
  --debug
```

Debug mode prints the raw API request, the raw response, and the assertion evaluation for each test.

---

## The `ToolEvals` class

For programmatic use:

```python
from cxas_scrapi.evals.tool_evals import ToolEvals

tool_evals = ToolEvals(
    app_name="projects/my-project/locations/us/apps/my-app",
)

# Load test cases from a YAML file
test_cases = tool_evals.load_tool_test_cases_from_file("evals/tool_tests/order_tests.yaml")

# Run the tests — returns a pandas DataFrame
results_df = tool_evals.run_tool_tests(test_cases)

print(results_df)
```

The returned DataFrame has columns:

| Column | Description |
|--------|-------------|
| `test_name` | Name of the test case |
| `tool` | Tool display name |
| `status` | `PASSED`, `FAILED`, or `ERROR` |
| `latency (ms)` | Time to invoke the tool |
| `errors` | Error details (on failure) |

### Filtering by pass/fail

```python
test_cases = tool_evals.load_tool_test_cases_from_file("evals/tool_tests/order_tests.yaml")
results_df = tool_evals.run_tool_tests(test_cases)

failed = results_df[results_df["status"] == "FAILED"]
if not failed.empty:
    print(f"{len(failed)} test(s) failed:")
    print(failed[["test_name", "tool", "errors"]].to_string())
```

### Summary report

The `ToolEvals` class can generate a summary report with pass rates and latency statistics:

```python
summary_df = ToolEvals.generate_report(results_df)
print(summary_df)
```

High p99 latency on a tool that the agent calls frequently is worth investigating — it adds to the overall conversation latency.

---

## Testing error handling

Every tool should handle errors gracefully by returning an `agent_action` key. Test this explicitly:

```yaml
tests:
  - name: "handles_missing_order_id"
    tool: "lookup_order"
    input:
      order_id: ""  # Empty string — edge case
    assertions:
      - path: "$.agent_action"
        operator: is_not_null
        # The tool should return a friendly error message the agent can relay

  - name: "handles_api_timeout"
    tool: "lookup_order"
    input:
      order_id: "SIMULATE_TIMEOUT"
    variables:
      lookup_order_simulate_timeout: "true"
    assertions:
      - path: "$.agent_action"
        operator: contains
        value: "try again"
```

The linter rule T001 (`tool-error-pattern`) will warn if your tool doesn't include an `agent_action` error return.

---

## Full example

Here's a complete tool test file for an order management tool:

```yaml
tests:
  - name: "order_found_shipped"
    tool: "lookup_order"
    input:
      order_id: "ORD-12345"
    variables:
      order_12345_status: "shipped"
      order_12345_eta: "2026-04-18"
    assertions:
      - path: "$.status"
        operator: equals
        value: "shipped"
      - path: "$.order_id"
        operator: equals
        value: "ORD-12345"
      - path: "$.estimated_delivery"
        operator: is_not_null

  - name: "order_found_processing"
    tool: "lookup_order"
    input:
      order_id: "ORD-67890"
    variables:
      order_67890_status: "processing"
    assertions:
      - path: "$.status"
        operator: equals
        value: "processing"

  - name: "order_not_found_returns_error"
    tool: "lookup_order"
    input:
      order_id: "ORD-INVALID"
    variables:
      order_invalid_status: "not_found"
    assertions:
      - path: "$.agent_action"
        operator: is_not_null

  - name: "empty_order_id_returns_error"
    tool: "lookup_order"
    input:
      order_id: ""
    assertions:
      - path: "$.agent_action"
        operator: is_not_null
```

```bash
cxas test-tools \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/tool_tests/order_management.yaml
```

Expected output:

```
Tool Test Results
=================
Test                              | Pass | Latency
----------------------------------|------|--------
order_found_shipped               | PASS |  234ms
order_found_processing            | PASS |  198ms
order_not_found_returns_error     | PASS |  201ms
empty_order_id_returns_error      | PASS |   45ms

4/4 tests passed. p50: 199ms, p90: 234ms
```
