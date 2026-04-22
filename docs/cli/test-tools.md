# cxas test-tools

`cxas test-tools` runs unit tests against your agent's tools by sending real requests to the deployed app and comparing the responses to expected values defined in a YAML or JSON test file.

## Usage

```
cxas test-tools --app-name APP --test-file FILE [--debug]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the deployed app to test against (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--test-file FILE` | Yes | — | Path to a YAML or JSON file containing tool test case definitions. |
| `--debug` | No | `false` | Enable verbose debug logging for each tool execution, useful when a test is failing for unexpected reasons. |

## Test File Format

The test file contains a list of test cases. Each case specifies the tool to call, the input parameters, and the expected output. A minimal example:

```yaml
- tool_name: "get_account_balance"
  inputs:
    account_id: "12345"
  expected:
    balance: 450.00
    currency: "USD"

- tool_name: "lookup_order"
  inputs:
    order_id: "ORD-9876"
  expected:
    status: "shipped"
```

The test runner calls each tool, compares the actual response fields to the expected values, and reports `PASSED` or `FAILED` per test case.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All tool tests passed. |
| `1` | One or more tool tests failed, or the test file contained no valid test cases. |

## Examples

**Run tool tests from the default location:**

```bash
cxas test-tools \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --test-file tests/tool_tests.yaml
```

**Run with debug output to trace what each tool returned:**

```bash
cxas test-tools \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --test-file tests/tool_tests.yaml \
  --debug
```

**Use in a CI pipeline after pushing the app:**

```bash
APP_NAME=$(cxas push --app-dir ./my-agent --display-name "[CI] Test" \
  --project-id my-gcp-project --location us-central1)

cxas test-tools \
  --app-name "$APP_NAME" \
  --test-file ./my-agent/tests/tool_tests.yaml
```

## Related Commands

- [`cxas test-callbacks`](test-callbacks.md) — Run tests for your agent's callback functions.
- [`cxas run`](run.md) — Run platform-level golden evaluations.
- [`cxas ci-test`](ci-test.md) — Full CI lifecycle that includes tool tests automatically when `tests/tool_tests.yaml` is present.
