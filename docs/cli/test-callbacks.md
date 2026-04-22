# cxas test-callbacks

`cxas test-callbacks` discovers and runs pytest-based unit tests for every callback in your app directory, so you can catch bugs in your callback logic before deploying anything.

## Usage

```
cxas test-callbacks --app-dir DIR
                    [--agent-name NAME]
                    [--callback-type TYPE]
                    [--callback-name NAME]
                    [--log-file PATH]
                    [--pytest-args ARGS]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | Yes | — | Path to the app directory that contains the `agents/` subdirectory. The CLI searches this directory for callbacks and their accompanying test files. |
| `--agent-name NAME` | No | — | Narrow the test run to callbacks belonging to a specific agent (e.g., `pilot`). Skips all other agents. |
| `--callback-type TYPE` | No | — | Only run callbacks of this type (e.g., `before_call`, `after_call`, `webhook`). |
| `--callback-name NAME` | No | — | Run tests for a single callback by name. |
| `--log-file PATH` | No | — | Path to a file where pytest's full output will be written. Useful for saving logs in CI. |
| `--pytest-args ARGS` | No | — | Comma-separated list of extra arguments to pass directly to pytest (e.g., `"-v,-s"` for verbose output with live stdout). |

## How Discovery Works

The CLI delegates discovery to `CallbackEvals.test_all_callbacks_in_app_dir`. It walks the app directory, finds callback Python files, and looks for adjacent test files following your project's naming conventions. All discovered tests are executed with pytest.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All callback tests passed. |
| `1` | One or more callback tests failed, or no test files were found. |

## Examples

**Run all callback tests in the app directory:**

```bash
cxas test-callbacks --app-dir ./my-agent
```

**Run only callbacks for the `pilot` agent:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --agent-name pilot
```

**Run only `before_call` callbacks, with verbose output, and save logs:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --callback-type before_call \
  --log-file /tmp/callback-test.log \
  --pytest-args "-v,-s"
```

**Narrow to a single callback by name:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --agent-name pilot \
  --callback-name validate_session
```

## Related Commands

- [`cxas test-single-callback`](test-single-callback.md) — Run tests for one specific callback using an explicit test file path.
- [`cxas test-tools`](test-tools.md) — Run unit tests for your agent's tools.
- [`cxas ci-test`](ci-test.md) — Full CI lifecycle that wires together tool tests and platform evaluations.
