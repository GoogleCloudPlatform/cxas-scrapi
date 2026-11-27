# cxas test-callbacks

`cxas test-callbacks` discovers and runs offline per-callback unit tests (`test.py`), multi-callback chain/turn/session tests (`test_chain.py`), static cross-callback linting (`C101–C109`), and dual-mode (`per_callback` vs `combined`) parity checks across your CES app directory.

## Usage

```
cxas test-callbacks --app-dir DIR
                    [--agent-name NAME]
                    [--callback-type TYPE]
                    [--callback-name NAME]
                    [--mode {per_callback,combined,both}]
                    [--chain-only | --no-chain]
                    [--lint]
                    [--lint-only | --compare-modes]
                    [--record-fixtures]
                    [--fixtures-dir DIR]
                    [--update-snapshots]
                    [--fail-on STATUSES]
                    [--llm SNAPSHOT_OR_ID]
                    [--image-version {v1,v2}]
                    [--log-file PATH]
                    [--pytest-args ARGS]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | Yes | — | Path to the app directory that contains `app.json` and the `agents/` subdirectory. |
| `--agent-name NAME` (`--agent`) | No | `*` | Narrow execution to callbacks belonging to a specific agent (glob patterns supported). |
| `--callback-type TYPE` | No | `*_callbacks` | Filter by callback stage directory (e.g., `before_agent_callbacks`, `before_model_callbacks`, `after_model_callbacks`, `before_tool_callbacks`, `after_tool_callbacks`, `after_agent_callbacks`). |
| `--callback-name NAME` | No | `*` | Run tests for a single callback by name. |
| `--mode {per_callback,combined,both}` | No | `per_callback` | Execution mode for multi-callback chain tests (`test_chain.py`). |
| `--chain-only` | No | `false` | Run only multi-callback chain tests (`test_chain.py`) and skip single-callback `test.py` files. |
| `--no-chain` | No | `false` | Skip multi-callback chain tests (`test_chain.py`) and run only single-callback `test.py` files. |
| `--lint` | No | `false` | Run cross-callback chain static analysis (`C101–C109`) in addition to pytest tests (or `--compare-modes`). |
| `--lint-only` | No | `false` | Run only static cross-callback chain linting (`C101–C109`) without executing pytest. |
| `--compare-modes` | No | `false` | Run `per_callback` vs `combined` parity comparison across configured callback chains (using `compare_fixtures/` when present) without executing pytest. |
| `--record-fixtures` | No | `false` | Record stage inputs executed during pytest chain runs into `agents/<agent>/compare_fixtures/<stage>/*.json` (or `--fixtures-dir`). |
| `--fixtures-dir DIR` | No | — | Custom directory for reading or recording `compare_modes` stage fixtures. |
| `--update-snapshots` (`--cxas-update-snapshots`) | No | `false` | Update stored `.snap.json` snapshot files with current trace outputs. |
| `--fail-on STATUSES` | No | `failed,error,risk` | Comma-separated statuses (`failed`, `error`, `risk`, `warning`, `inconclusive`, `skipped`, `info`) that cause exit code `1`. |
| `--llm SNAPSHOT_OR_ID` | No | — | LLM snapshot JSON path, registered adapter identifier, or `mock` injected via `CXAS_CALLBACK_LLM`. |
| `--image-version {v1,v2}` | No | `v1` | CES sandbox image version (`v2` enables `orjson` non-string dict key checks `C108` and V2 runtime semantics). |
| `--log-file PATH` | No | — | File path to write full pytest stdout/stderr logs. |
| `--pytest-args ARGS` | No | — | Comma-separated extra arguments forwarded directly to pytest (e.g., `"-v,-s"`). |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All matching checks passed (no row status matched `--fail-on`). |
| `1` | One or more checks matched `--fail-on` (default: `FAILED`, `ERROR`, `RISK`), no valid tests/callbacks were found, or 100% of discovered tests were `SKIPPED` (`0` passed, unless `--fail-on` is given explicitly). |
| `2` | Invalid CLI flag combination or invalid `--fail-on` value. |

## Examples

**Run all single-callback and multi-callback chain tests:**

```bash
cxas test-callbacks --app-dir ./my-agent
```

**Run chain tests across both `per_callback` and `combined` modes plus `C101–C109` lint:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --chain-only \
  --mode both \
  --lint
```

**Run static chain lint (`C101–C109`) under V2 sandbox semantics:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --lint-only \
  --image-version v2
```

**Compare `per_callback` vs `combined` execution parity across recorded fixtures:**

```bash
cxas test-callbacks \
  --app-dir ./my-agent \
  --compare-modes
```

## Related Commands

- [`cxas test-single-callback`](test-single-callback.md) — Run tests for one specific callback using an explicit test file path.
- [`cxas test-tools`](test-tools.md) — Run unit tests for your agent's tools.
- [`cxas ci-test`](ci-test.md) — Full CI lifecycle that wires together tool tests and platform evaluations.
