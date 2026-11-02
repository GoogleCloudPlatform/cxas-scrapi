# cxas test-single-callback

`cxas test-single-callback` runs the tests in a specific test file against one callback in a local app directory (`--app-dir`) or a deployed app (`--app-name`) — useful when you've just edited a single callback and want fast, targeted feedback without running the full suite.

## Usage

```
cxas test-single-callback [--app-name APP | --app-dir DIR]
                           --agent-name NAME
                           --callback-type TYPE
                           --test-file-path PATH
                           [--log-file PATH]
                           [--pytest-args ARGS]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | One of `--app-name` or `--app-dir` | — | Full resource name of the deployed app that owns the callback (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--app-dir DIR` | One of `--app-name` or `--app-dir` | — | Path to the local app directory for offline callback execution (no GCP credentials or network required). |
| `--agent-name NAME` | Yes | — | Name or display name of the agent that owns the callback (e.g., `pilot`). |
| `--callback-type TYPE` | Yes | — | Type of the callback to test (e.g., `before_model`, `after_model`, `before_tool`, `after_tool`, `before_agent`, `after_agent`). |
| `--test-file-path PATH` | Yes | — | Absolute or relative path to the Python pytest test file to execute. |
| `--log-file PATH` | No | — | Path to a file where pytest output will be written. |
| `--pytest-args ARGS` | No | — | Comma-separated extra arguments forwarded to pytest (e.g., `"-v,-s"`). |

## When to Use This vs. `cxas test-callbacks`

| Scenario | Command |
|----------|---------|
| You want to run tests for every callback in the app | [`cxas test-callbacks`](test-callbacks.md) |
| You changed one callback locally and want instant offline feedback | `cxas test-single-callback --app-dir ...` |
| You want to test against a callback fetched from a deployed app | `cxas test-single-callback --app-name ...` |
| You're in CI and want comprehensive coverage | [`cxas ci-test`](ci-test.md) |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All tests in the file passed. |
| `1` | One or more tests failed, or the file contained no valid test cases. |

## Examples

**Run a single callback test completely offline against a local app directory:**

```bash
cxas test-single-callback \
  --app-dir ./my-agent \
  --agent-name pilot \
  --callback-type before_model \
  --test-file-path ./my-agent/agents/pilot/before_model_callbacks/cb1/test.py
```

**Run a specific callback test file against a deployed app:**

```bash
cxas test-single-callback \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --agent-name pilot \
  --callback-type before_model_callback \
  --test-file-path ./my-agent/agents/pilot/before_model_callbacks/cb1/test.py
```

**Run with verbose pytest output and save logs:**

```bash
cxas test-single-callback \
  --app-dir ./my-agent \
  --agent-name pilot \
  --callback-type after_model \
  --test-file-path ./my-agent/agents/pilot/after_model_callbacks/cb1/test.py \
  --log-file /tmp/after-model-test.log \
  --pytest-args=-v,-s
```

## Related Commands

- [`cxas test-callbacks`](test-callbacks.md) — Run tests for all callbacks discovered in an app directory.
- [`cxas test-tools`](test-tools.md) — Run unit tests for your agent's tools.
- [`cxas run`](run.md) — Run platform-level evaluations.
