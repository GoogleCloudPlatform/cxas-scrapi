# cxas lint

`cxas lint` checks your app directory for best-practice violations, structural problems, and schema errors across 60+ rules, so you can catch issues in code review rather than at runtime.

## Usage

```
cxas lint [--app-dir DIR]
          [--fix]
          [--only CATEGORY]
          [--rule IDS]
          [--json]
          [--list-rules]
          [--validate-only]
          [--agent DIR]
          [--tool DIR]
          [--toolset DIR]
          [--guardrail DIR]
          [--evaluation DIR]
          [--evaluation-expectations DIR]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the root of the app directory to lint. The linter discovers the app structure automatically. |
| `--fix` | No | `false` | Print fix suggestions alongside each issue. Does not automatically modify files. |
| `--only CATEGORY` | No | — | Limit linting to a single category. See [Rule Categories](#rule-categories) below. |
| `--rule IDS` | No | — | Run only specific rules. Accepts a comma-separated list of rule IDs (e.g., `I003,C005`). |
| `--json` | No | `false` | Output results as a JSON array instead of the human-readable format. Useful for integrating with other tools. |
| `--list-rules` | No | `false` | Print all available lint rules with their IDs, categories, and descriptions, then exit. |
| `--validate-only` | No | `false` | Run only `structure`, `config`, and `schema` rules. Skips instruction quality and eval checks. |
| `--agent DIR` | No | — | Validate a single agent directory against the CES schema instead of linting the whole app. |
| `--tool DIR` | No | — | Validate a single tool directory. |
| `--toolset DIR` | No | — | Validate a single toolset directory. |
| `--guardrail DIR` | No | — | Validate a single guardrail directory. |
| `--evaluation DIR` | No | — | Validate a single evaluation directory. |
| `--evaluation-expectations DIR` | No | — | Validate a single evaluation expectations directory. |

## Rule Categories

| Category | Prefix | Description |
|----------|--------|-------------|
| `instructions` | `I` | Agent instruction quality, clarity, length, and formatting. |
| `callbacks` | `CB` | Callback file structure, naming, and implementation patterns. |
| `tools` | `T` | Tool definition quality, parameter descriptions, and schema correctness. |
| `evals` | `E` | Evaluation structure, turn count, and expectation quality. |
| `config` | `C` | `app.yaml`/`app.json` configuration correctness. |
| `structure` | `S` | Directory layout, required files, and naming conventions. |
| `schema` | `SC` | JSON/YAML schema validation against the CES resource schemas. |

## Examples

**Lint the current directory:**

```bash
cxas lint
```

**Lint a specific app directory and show fix suggestions:**

```bash
cxas lint --app-dir ./my-agent --fix
```

**Run only instruction-quality rules:**

```bash
cxas lint --only instructions
```

**Run specific rules by ID:**

```bash
cxas lint --rule I003,C005,S002
```

**Output results as JSON (for CI dashboards or custom reporters):**

```bash
cxas lint --json
```

**List all available rules:**

```bash
cxas lint --list-rules
```

**Quick structural validation only (no instruction or eval checks):**

```bash
cxas lint --validate-only
```

**Validate a single agent directory:**

```bash
cxas lint --agent ./my-agent/agents/pilot
```

**Validate a single tool:**

```bash
cxas lint --tool ./my-agent/tools/get_account_balance
```

**Use in CI to fail the build on any lint issue:**

```bash
cxas lint --json | jq 'if length > 0 then error else empty end'
```

## Related Commands

- [`cxas init`](init.md) — Bootstrap a project with the skills and configs that help you write lint-clean agents.
- [`cxas push`](push.md) — Push your linted app to CX Agent Studio.
- [`cxas ci-test`](ci-test.md) — Full CI lifecycle (add `cxas lint` as a step before `cxas ci-test` for maximum coverage).
