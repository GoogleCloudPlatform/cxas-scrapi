# cxas run

`cxas run` triggers one or more evaluations against a deployed app and, optionally, waits for the results and fails fast if any test doesn't pass — exactly what you want in a CI pipeline.

## Usage

```
cxas run --app-name APP
         [--evaluation-id EVAL]
         [--display-name_prefix PREFIX]
         [--tags TAG ...]
         [--wait]
         [--filter-auto-metrics]
         [--modality text|audio]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the app to evaluate (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--evaluation-id EVAL` | No* | — | Full resource name of a specific evaluation to run. |
| `--display-name_prefix PREFIX` | No* | — | Run all evaluations whose display name starts with this string. |
| `--tags TAG ...` | No* | — | Space-separated list of tags. Runs all evaluations that have at least one of the specified tags. |
| `--wait` | No | `false` | Block until all triggered evaluations complete and exit with code `0` on pass or `1` on fail. Without this flag the command fires the run and returns immediately. |
| `--filter-auto-metrics` | No | `false` | When waiting for results, ignore automated LLM metrics (semantic similarity, hallucination) and only assess custom expectations and tool invocation results. Useful when you care about business-logic correctness rather than language quality scores. |
| `--modality text\|audio` | No | `text` | The modality to use when executing the evaluation. |

*You must provide at least one of `--evaluation-id`, `--display-name_prefix`, or `--tags`.

## How Waiting Works

When you pass `--wait`, the CLI:

1. Snapshots the current evaluation results before triggering.
2. Triggers the specified evaluation(s).
3. Polls every 5 seconds for up to 10 minutes until all new results reach `COMPLETED` or `ERROR` state.
4. Prints a summary of passed / failed / errored turns.
5. Exits `0` if all evaluations passed, `1` otherwise.

If a test fails, the CLI prints a breakdown of each failed turn, including the failure type, expected vs. actual value, and (if applicable) a score.

## Examples

**Run a specific evaluation and wait for the result:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --wait
```

**Run all evaluations whose names start with `"Billing"` and assess only custom expectations:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --display-name_prefix "Billing" \
  --wait \
  --filter-auto-metrics
```

**Run all evaluations tagged `smoke` or `regression` in audio modality:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --tags smoke regression \
  --wait \
  --modality audio
```

**Fire and forget (trigger without waiting):**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001
```

## Related Commands

- [`cxas push-eval`](push-eval.md) — Push evaluation definitions before running them.
- [`cxas export`](export.md) — Export evaluation definitions to a file.
- [`cxas ci-test`](ci-test.md) — The CI lifecycle that runs evaluations automatically as part of a push.
