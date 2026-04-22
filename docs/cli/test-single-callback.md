# cxas test-single-callback

`cxas test-single-callback` runs the tests in a specific test file against one callback in a deployed app — useful when you've just edited a single callback and want fast, targeted feedback without running the full suite.

## Usage

```
cxas test-single-callback --app-name APP
                           --agent-name NAME
                           --callback-type TYPE
                           --test-file-path PATH
                           [--log-file PATH]
                           [--pytest-args ARGS]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the app that owns the callback (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--agent-name NAME` | Yes | — | Name of the agent that owns the callback (e.g., `pilot`). |
| `--callback-type TYPE` | Yes | — | Type of the callback to test (e.g., `before_call`, `after_call`, `webhook`). |
| `--test-file-path PATH` | Yes | — | Absolute or relative path to the Python pytest test file to execute. |
| `--log-file PATH` | No | — | Path to a file where pytest output will be written. |
| `--pytest-args ARGS` | No | — | Comma-separated extra arguments forwarded to pytest (e.g., `"-v,-s"`). |

## When to Use This vs. `cxas test-callbacks`

| Scenario | Command |
|----------|---------|
| You want to run tests for every callback in the app | [`cxas test-callbacks`](test-callbacks.md) |
| You changed one callback and want targeted feedback | `cxas test-single-callback` |
| You're in CI and want comprehensive coverage | [`cxas ci-test`](ci-test.md) |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All tests in the file passed. |
| `1` | One or more tests failed, or the file contained no valid test cases. |

## Examples

**Run a specific callback test file:**

```bash
cxas test-single-callback \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --agent-name pilot \
  --callback-type before_call \
  --test-file-path ./my-agent/agents/pilot/callbacks/before_call_test.py
```

**Run with verbose pytest output and save logs:**

```bash
cxas test-single-callback \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --agent-name pilot \
  --callback-type after_call \
  --test-file-path ./my-agent/agents/pilot/callbacks/after_call_test.py \
  --log-file /tmp/after-call-test.log \
  --pytest-args "-v,-s"
```

**Use in a pre-push hook or quick local check:**

```bash
APP="projects/my-gcp-project/locations/us-central1/apps/abc123"

cxas test-single-callback \
  --app-name "$APP" \
  --agent-name pilot \
  --callback-type webhook \
  --test-file-path ./agents/pilot/callbacks/webhook_test.py \
  --pytest-args "-v"
```

## Related Commands

- [`cxas test-callbacks`](test-callbacks.md) — Run tests for all callbacks discovered in an app directory.
- [`cxas test-tools`](test-tools.md) — Run unit tests for your agent's tools.
- [`cxas run`](run.md) — Run platform-level evaluations.
