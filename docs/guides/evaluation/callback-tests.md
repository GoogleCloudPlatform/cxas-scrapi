---
title: Callback Tests
description: Unit testing your callback Python code with pytest and the CallbackEvals class.
---

# Callback Tests

Callback tests are Python unit tests for your agent's callback code. Because callbacks run *inside* the platform's execution environment, bugs in callback code can be hard to debug after the fact. Testing them locally with pytest — before they're deployed — catches issues early and makes your development loop much faster.

Unlike the other eval types, callback tests are just pytest. You write test functions, use mocks for the platform objects that callbacks receive, and assert on the output. SCRAPI provides the `CallbackEvals` class to orchestrate running tests across all your agents' callbacks.

---

## Directory structure

Callback tests live alongside the callback code they're testing:

```
cxas_app/<AppName>/agents/<agent_name>/
├── before_model_callbacks/
│   └── inject_context/
│       ├── python_code.py        # Callback implementation
│       └── test_inject_context.py  # Tests for this callback
├── after_model_callbacks/
│   └── log_response/
│       ├── python_code.py
│       └── test_log_response.py
```

The test file should be named `test_<callback_name>.py` and placed in the same directory as `python_code.py`.

---

## Writing a callback test

Callbacks receive platform-specific objects (`CallbackContext`, `LlmRequest`, `LlmResponse`). In tests, you mock these with simple Python objects. Here's a complete example:

```python
# test_inject_context.py

from unittest.mock import MagicMock, patch
import pytest

# Import the callback function directly
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from python_code import before_model_callback


class MockSessionParameters:
    def __init__(self, params):
        self._params = params

    def get(self, key, default=None):
        return self._params.get(key, default)


class MockSession:
    def __init__(self, params):
        self.session_parameters = MockSessionParameters(params)


class MockCallbackContext:
    def __init__(self, session_params=None):
        self.session = MockSession(session_params or {})


class MockLlmRequest:
    def __init__(self):
        self.system_instruction = None


def test_injects_account_id_when_present():
    context = MockCallbackContext(session_params={"account_id": "ACC-123"})
    request = MockLlmRequest()

    result = before_model_callback(context, request)

    # The callback should return None (continue normally)
    assert result is None
    # And should have modified the request
    assert "ACC-123" in str(request.system_instruction or "")


def test_does_not_fail_when_account_id_missing():
    context = MockCallbackContext(session_params={})
    request = MockLlmRequest()

    # Should not raise an exception
    result = before_model_callback(context, request)
    assert result is None


def test_returns_none_for_normal_execution():
    """Callbacks should return None unless they want to override the LLM."""
    context = MockCallbackContext()
    request = MockLlmRequest()

    result = before_model_callback(context, request)
    assert result is None
```

---

## Running with the CLI

Run all callback tests across all agents in an app:

```bash
cxas test-callbacks \
  --app-dir cxas_app/My\ Support\ Agent
```

Run tests for a specific agent:

```bash
cxas test-callbacks \
  --app-dir cxas_app/My\ Support\ Agent \
  --agent support-root
```

### Log files

By default, pytest output is printed to the terminal. You can save it to a log file:

```bash
cxas test-callbacks \
  --app-dir cxas_app/My\ Support\ Agent \
  --log-file test-results/callback-tests.log
```

### Exit codes

| Exit code | Meaning |
|-----------|---------|
| 0 | All tests passed |
| 1 | One or more tests failed |
| 2 | Collection error (syntax error, import error) |

---

## Testing a single callback

When iterating on a specific callback, use `cxas test-single-callback` to run just that callback's tests:

```bash
cxas test-single-callback \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --agent-name "support-root" \
  --callback-type "before_model_callback" \
  --test-file-path cxas_app/My\ Support\ Agent/agents/support-root/before_model_callbacks/inject_context/test_inject_context.py
```

This command fetches the callback code from the platform (not from your local files), writes it to a temporary location, and runs the test against it. This is useful for verifying that what's actually deployed matches your expectations.

---

## The `CallbackEvals` class

For programmatic orchestration:

```python
from cxas_scrapi.evals.callback_evals import CallbackEvals

cb_evals = CallbackEvals()

# Run all callback tests in an app directory
results_df = cb_evals.test_all_callbacks_in_app_dir(
    app_dir="cxas_app/My Support Agent",
    log_file="test-results/callbacks.log",
)

print(results_df)
```

### Testing a single callback from the platform

```python
results_df = cb_evals.test_single_callback_for_agent(
    app_name="projects/my-project/locations/us/apps/my-app",
    agent_name="Support Root Agent",
    callback_type="before_model_callback",
    test_file_path="path/to/test_inject_context.py",
    log_file="test-results/inject_context.log",
    pytest_args=["--tb=short"],
)
```

The results DataFrame has columns:

| Column | Description |
|--------|-------------|
| `agent_name` | The agent being tested |
| `callback_type` | Type of callback (e.g., `before_model_callbacks`) |
| `test_name` | Name of the pytest test |
| `status` | `PASSED`, `FAILED`, or `ERROR` |
| `error_message` | Error details (on failure) |

---

## What to test

Good callback tests cover these scenarios:

**Happy path**
: Normal inputs produce the expected output or return `None` (proceed normally).

**Missing session parameters**
: The callback doesn't crash when expected session data is absent. Callbacks must be defensive.

**Return value behavior**
: Verify when the callback returns `None` vs. an overriding `LlmResponse`. Returning the wrong type causes silent failures on the platform.

**Side effects**
: If the callback logs, updates state, or calls external services, verify those behaviors with mocks.

**Error handling**
: If the callback's external service is unavailable, what happens? It should not raise unhandled exceptions.

---

## Common patterns

### Mocking an external API call

```python
from unittest.mock import patch, MagicMock

def test_callback_handles_api_failure():
    with patch("python_code.requests.get") as mock_get:
        mock_get.side_effect = Exception("Network error")

        context = MockCallbackContext()
        request = MockLlmRequest()

        # Should not raise — the callback should handle the exception gracefully
        result = before_model_callback(context, request)
        assert result is None
```

### Testing that a specific LlmResponse is returned

```python
def test_returns_override_response_for_blocked_user():
    context = MockCallbackContext(session_params={"user_blocked": "true"})
    request = MockLlmRequest()

    result = before_model_callback(context, request)

    assert result is not None  # Callback should intercept
    # Check the response content
    assert "blocked" in str(result).lower() or result is not None
```

---

## Linter integration

The callback linter rules (C001-C010) catch many issues before you even write tests:

- **C001**: Wrong function name for the callback type
- **C002**: Wrong number of arguments
- **C006**: Bare `except:` without logging
- **C008**: Missing typing imports that cause `NameError` at runtime
- **C010**: Invalid Python syntax

Running `cxas lint` before `cxas test-callbacks` is a good habit — fix the lint errors first, then verify behavior with tests.
