# cxas run

`cxas run` triggers one or more evaluations against a deployed app and, optionally, waits for the results and fails fast if any test doesn't pass — exactly what you want in a CI pipeline.

## Usage

```
cxas run --app-name APP
         [--evaluation-id EVAL]
         [--display-name-prefix PREFIX]
         [--tags TAG ...]
         [--eval-type goldens|scenarios|all]
         [--run-count COUNT]
         [--golden-run-method STABLE|NAIVE]
         [--wait]
         [--filter-auto-metrics]
         [--modality text|audio]
         [--background-noise PRESET_OR_PATH]
         [--bg-noise-snr SNR_DB|LEVEL]
         [--bg-noise-level LOW|MEDIUM|HIGH]
         [--burst-noise BURST_PATH ...]
         [--burst-noise-snr BURST_SNR_DB|LEVEL]
         [--burst-noise-level LOW|MEDIUM|HIGH]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the app to evaluate (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--evaluation-id EVAL` | No* | — | Full resource name of a specific evaluation to run. |
| `--display-name-prefix PREFIX` | No* | — | Run all evaluations whose display name starts with this string. |
| `--tags TAG ...` | No* | — | Space-separated list of tags. Runs all evaluations that have at least one of the specified tags. |
| `--eval-type goldens\|scenarios\|all` | No* | — | Run all evaluations of a specific category (`goldens`, `scenarios` — including CXAS 2.0 Milestone evaluations — or `all`). |
| `--run-count COUNT` | No | — | Number of times to run each evaluation (e.g., `--run-count 5` for statistical confidence across scenario runs). |
| `--golden-run-method STABLE\|NAIVE` | No | `STABLE` | Replay method for golden evaluations (`STABLE` replays each turn in isolation; `NAIVE` carries session state across turns). |
| `--wait` | No | `false` | Block until all triggered evaluations complete and exit with code `0` on pass or `1` on fail. Without this flag the command fires the run and returns immediately. |
| `--filter-auto-metrics` | No | `false` | When waiting for results, ignore automated LLM metrics (semantic similarity, hallucination) and only assess custom expectations and tool invocation results. Useful when you care about business-logic correctness rather than language quality scores. |
| `--modality text\|audio` | No | `text` | The modality to use when executing the evaluation. |
| `--background-noise PRESET` | No | — | Built-in preset name (`cafe`, `call_center`, `traffic`, `cross_talk`, `baby_crying`, `kids_noise`, `dog_barking`, `tv_radio`, `kitchen`, `wind`, `white`, `pink`, `brown`), path to a custom WAV audio file, or shorthand `<preset>:<level>` (e.g., `cafe:HIGH`, `traffic:LOW`). |
| `--bg-noise-snr SNR_DB\|LEVEL` | No | `15.0` (`MEDIUM`) | Target Signal-to-Noise Ratio (SNR) in dB or categorical level (`LOW` = 20 dB, `MEDIUM` = 12 dB, `HIGH` = 5 dB). Lower dB = louder noise. |
| `--bg-noise-level LEVEL` | No | — | Categorical background noise level (`LOW`, `MEDIUM`, `HIGH`). Alias for `--bg-noise-snr`. |
| `--burst-noise PATH ...` | No | — | One or more audio files or burst presets (`dog_barking`, etc.) to inject intermittently. |
| `--burst-noise-snr SNR_DB\|LEVEL` | No | `5.0` (`HIGH`) | Target Signal-to-Noise Ratio (SNR) in dB or categorical level (`LOW`, `MEDIUM`, `HIGH`) for burst noise. |
| `--burst-noise-level LEVEL` | No | — | Categorical burst noise level (`LOW`, `MEDIUM`, `HIGH`). Alias for `--burst-noise-snr`. |

*You must provide at least one of `--evaluation-id`, `--display-name-prefix`, `--tags`, or `--eval-type`.

## How Waiting Works

When you pass `--wait`, the CLI:

1. Snapshots the current evaluation results before triggering.
2. Triggers the specified evaluation(s).
3. Polls every 5 seconds for up to 10 minutes until all new results reach `COMPLETED` or `ERROR` state (accounting for `--run-count` when multiple runs per evaluation are requested).
4. Prints a summary of passed / failed / errored turns or milestones.
5. Exits `0` if all evaluations passed, `1` otherwise.

If a test fails, the CLI prints a breakdown of each failed turn or milestone, including the failure type, expected vs. actual value, and (if applicable) a score.

## Examples

**Run a specific evaluation and wait for the result:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --wait
```

**Run all milestone/scenario evaluations 3 times each and wait for completion:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --eval-type scenarios \
  --run-count 3 \
  --wait
```

**Run all evaluations whose names start with `"Billing"` and assess only custom expectations:**

```bash
cxas run \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --display-name-prefix "Billing" \
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
