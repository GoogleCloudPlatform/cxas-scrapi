---
title: CallbackEvals
---

# CallbackEvals

`CallbackEvals` brings proper unit testing to your CXAS agent callbacks. It runs pytest-based tests against your callback Python code, either by reading it from a local app directory or by fetching it live from the CXAS API.

The two main methods are:

- **`test_all_callbacks_in_app_dir()`** — scans your local app directory, discovers every `test.py` alongside each callback's `python_code.py`, and runs them all. Returns a DataFrame with results for each test.
- **`test_single_callback_for_agent()`** — fetches a specific agent's callback from the CXAS API and runs the test file you point it to. Useful for CI jobs where you want to verify a specific agent hasn't regressed.

Both methods return a pandas DataFrame with columns: `agent_name`, `callback_type`, `test_name`, `status`, and `error_message`.

## Quick Example

```python
from cxas_scrapi import CallbackEvals

ce = CallbackEvals()

# Run all callback tests discovered in your local app directory
results_df = ce.test_all_callbacks_in_app_dir(
    app_dir="./cxas_app/My_Agent_App",
)
print(results_df)

# Run tests for a specific agent and callback type (fetched from the API)
results_df = ce.test_single_callback_for_agent(
    app_name="projects/my-project/locations/us/apps/my-app-id",
    agent_name="root_agent",
    callback_type="before_model_callback",
    test_file_path="evals/callback_tests/tests/root_agent/before_model_callbacks/before_model/test.py",
)
print(results_df[["test_name", "status", "error_message"]])
```

Your `test.py` files look just like standard pytest:

```python
import python_code  # auto-injected by CallbackEvals

def test_callback_returns_correct_format():
    result = python_code.before_model_callback(handler=None, request={"text": "hi"})
    assert result is not None
```

## Reference

::: cxas_scrapi.evals.callback_evals.CallbackEvals
