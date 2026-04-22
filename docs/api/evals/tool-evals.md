---
title: ToolEvals
---

# ToolEvals

`ToolEvals` lets you write and run unit tests for your CXAS tools — without needing a full agent session. Tests are described in simple YAML files, which makes them easy to version-control alongside your tool code and review in pull requests.

Each test case specifies a tool name, the input `args`, optional `variables` (session state), and `expectations` that assert things about the response using the `Operator` enum:

| Operator | Meaning |
|---|---|
| `equals` | exact match |
| `contains` | substring or element check |
| `greater_than` / `less_than` | numeric comparison |
| `length_equals` / `length_greater_than` / `length_less_than` | collection size |
| `is_null` / `is_not_null` | presence check |

`run_tool_tests()` returns a pandas DataFrame with columns for test name, tool, status, latency, and errors — easy to save as a CSV or display in a notebook.

## Quick Example

```python
from cxas_scrapi import ToolEvals

app_name = "projects/my-project/locations/us/apps/my-app-id"
te = ToolEvals(app_name=app_name)

# Load tests from a YAML file
test_cases = te.load_tool_test_cases_from_file("tool_tests/lookup_account.yaml")

# Run them and get a results DataFrame
results_df = te.run_tool_tests(test_cases)
print(results_df[["test_name", "tool", "status", "latency (ms)"]])

# Generate a summary report
report_df = ToolEvals.generate_report(results_df)
print(report_df)
```

A minimal YAML test file looks like this:

```yaml
tests:
  - name: lookup_known_customer
    tool: lookup_account
    args:
      customer_id: "C-1234"
    expectations:
      response:
        - path: "$.account_status"
          operator: equals
          value: "active"
        - path: "$.balance"
          operator: is_not_null
```

## Reference

::: cxas_scrapi.evals.tool_evals.ToolEvals

::: cxas_scrapi.evals.tool_evals.Operator

::: cxas_scrapi.evals.tool_evals.ToolTestCase

::: cxas_scrapi.evals.tool_evals.Expectation
