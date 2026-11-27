# cxas test-tools

`cxas test-tools` runs unit tests against your agent's tools — either completely offline against local Python function tools (`--app-dir` / `--offline`) or by sending real requests to a deployed app (`--app-name`) — and compares the responses to expected values defined in a YAML or JSON test file.

## Usage

```
cxas test-tools [--app-name APP | --app-dir DIR [--offline]]
                --test-file FILE
                [--debug]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | One of `--app-name` or `--app-dir` | — | Full resource name of the deployed app to test against (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--app-dir DIR` | One of `--app-name` or `--app-dir` | — | Path to the local app directory containing `tools/` for offline Python function tool testing. |
| `--offline` | No | `true` when `--app-dir` is set without `--app-name` | Run tool tests completely offline against local Python function tools in `--app-dir` with zero GCP network calls. |
| `--test-file FILE` | Yes | — | Path to a YAML or JSON file (or directory) containing tool test case definitions. |
| `--debug` | No | `false` | Enable verbose debug logging for each tool execution, useful when a test is failing for unexpected reasons. |

## Test File Format

The test file contains a list of test cases (either under a top-level `tests:` key or as a list). Each case specifies the tool to call, the input arguments, optional session `variables` / `state` / `context` / `mock_tools`, and the expected output or JSONPath expectations.

```yaml
tests:
  - name: "check_balance_success"
    tool: "get_account_balance"
    args:
      account_id: "12345"
    variables:
      customer_tier: "gold"
    expectations:
      response:
        - path: "$.balance"
          operator: "equals"
          value: 450.00
        - path: "$.currency"
          operator: "equals"
          value: "USD"

  - name: "lookup_order_direct_expected"
    tool_name: "lookup_order"
    input:
      order_id: "ORD-9876"
    expected_output:
      status: "shipped"
```

When running in offline mode (`--app-dir` / `--offline`), each Python function tool in `<app_dir>/tools/<tool_dir>/python_function/python_code.py` (both `def` and `async def`) is executed in an isolated local namespace with `context` (`ToolContext` containing `state` / `variables`) and a mock `tools` dispatcher supporting CES Xbox v2 positional dict calls (`tools.<name>({...})`). Non-Python tools (e.g., OpenAPI or Connector toolsets without `python_code.py` and without an entry in `mock_tools`) are reported as `SKIPPED`.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All executed tool tests passed. |
| `1` | One or more tool tests failed/errored, or the test file contained no valid test cases. |

## Examples

**Run tool tests completely offline against a local app directory:**

```bash
cxas test-tools \
  --app-dir ./my-agent \
  --offline \
  --test-file ./my-agent/tests/tool_tests.yaml
```

**Run tool tests against a deployed app:**

```bash
cxas test-tools \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --test-file tests/tool_tests.yaml
```

**Run with debug output to trace what each tool returned:**

```bash
cxas test-tools \
  --app-dir ./my-agent \
  --test-file tests/tool_tests.yaml \
  --debug
```

## Related Commands

- [`cxas test-callbacks`](test-callbacks.md) — Run tests for your agent's callback functions.
- [`cxas test-single-callback`](test-single-callback.md) — Run tests for a single callback locally or against a deployed app.
- [`cxas run`](run.md) — Run platform-level golden evaluations.
- [`cxas ci-test`](ci-test.md) — Full CI lifecycle that includes tool tests automatically when `tests/tool_tests.yaml` is present.
