---
title: Running Evaluations
description: End-to-end CLI workflow for running all evaluation types, using cxas ci-test, and integrating with CI pipelines.
---

# Running Evaluations

This page covers the full CLI workflow for running evaluations — both individually and as a combined suite. It also covers `cxas ci-test`, which is the recommended approach for automated CI pipelines.

---

## Running individual eval types

Each evaluation type has its own CLI command:

### Platform Goldens

```bash
# Push a golden YAML file or an entire directory (with optional tag-scoped pruning)
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/goldens/ \
  --prune-tag regression

# Run evaluations and wait for results (optionally with k-of-N voting and pass-rate gate)
cxas run \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --tags regression \
  --runs 3 \
  --min-run-passes 2 \
  --min-pass-rate 0.85 \
  --wait
```

The `--wait` flag polls until all evaluations complete. Without it, the command exits immediately and you need to check results manually.

### Tool Tests

```bash
cxas test-tools \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --test-file evals/tool_tests/order_tests.yaml
```

### Callback Tests

```bash
cxas test-callbacks \
  --app-dir cxas_app/My\ Support\ Agent
```

### Simulations (Local & Platform Milestone Evals)

You can run simulations either locally (via `cxas evals report` or Python) or directly on the CX Agent Studio platform as CXAS 2.0 Milestone evaluations:

**Run on the platform via CLI:**

```bash
# Push simulation YAML as CXAS 2.0 Milestone evaluations
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/simulations/order_sims.yaml

# Trigger all scenario/milestone evaluations and wait for results
cxas run \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --eval-type scenarios \
  --run-count 3 \
  --wait

# Or run and generate an HTML report in one step:
cxas evals report \
  --run \
  --include sims \
  --platform-sims \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --output-dir "eval-reports"
```

**Run locally via Python:**

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

from cxas_scrapi.utils.rate_limiter import RateLimiter

# Optional: configure a rate limiter to pace calls and avoid quota limits
limiter = RateLimiter(requests_per_minute=30.0)

sim_evals = SimulationEvals(
    app_name="projects/my-project/locations/us/apps/my-app",
    rate_limiter=limiter
)

test_case = {
    "steps": [
        {"goal": "Greet the agent", "success_criteria": "Agent responds with a greeting"},
    ],
    "expectations": ["Agent should be polite"],
}

eval_conv = sim_evals.simulate_conversation(test_case=test_case)
report = eval_conv.generate_report()
print(report)
```

---

## The `cxas ci-test` command

`cxas ci-test` is designed for CI/CD pipelines. It combines tool tests and platform goldens into a single command with deterministic cleanup:

```bash
cxas ci-test \
  --app-dir cxas_app/My\ Support\ Agent \
  --project-id my-project \
  --location us \
  --display-name "[CI] PR-123"
```

### What `cxas ci-test` does

1. **Creates a temporary branch app** with a deterministic display name (via `--display-name`). This ensures parallel CI runs don't interfere with each other.
2. **Pushes the current local config** to the branch app
3. **Runs tool tests and platform goldens** against the branch app
4. **Reports results** to the terminal
5. **Deletes the branch app** regardless of whether tests pass or fail

The branch app is always cleaned up, even on failure. This prevents orphaned test apps from accumulating in your project.

### `--filter-auto-metrics` flag

```bash
cxas run \
  --app-name "projects/.../apps/my-app" \
  --wait \
  --filter-auto-metrics
```

The `--filter-auto-metrics` flag filters out automatically-collected platform metrics from the results, showing only the evaluations you explicitly pushed. This is useful in CI where you want a clean pass/fail signal without noise from background platform telemetry.

---

## Exit codes

All SCRAPI evaluation commands follow consistent exit code conventions:

| Exit code | Meaning |
|-----------|---------|
| 0 | All evaluations passed |
| 1 | One or more evaluations failed |
| 2 | Command error (invalid arguments, authentication failure, API error) |

These conventions make the commands suitable for use in CI pipelines where the exit code determines whether a build passes or fails.

---

## Combining all four eval types

For a complete eval suite in CI, run all four types and collect results:

```bash
#!/bin/bash
set -e

APP="projects/my-project/locations/us/apps/my-app"

# 1. Callback tests (fastest, catch obvious bugs first)
cxas test-callbacks --app-dir cxas_app/My\ Support\ Agent
echo "Callback tests passed"

# 2. Tool tests (isolated, fast)
cxas test-tools --app-name "$APP" --test-file evals/tool_tests/order_tests.yaml
echo "Tool tests passed"

# 3. Platform goldens (push + run)
cxas push-eval --app-name "$APP" --file evals/goldens/order_lookup.yaml
cxas run --app-name "$APP" --eval-type goldens --wait --filter-auto-metrics
echo "Golden evals passed"

# 4. Simulations (run locally in parallel, or add --platform-sims for CXAS 2.0 Milestone evals)
cxas evals report --run --include sims --app-name "$APP" --output-dir "eval-reports" --sim-parallel 5 --use-tool-fakes
echo "Simulation evals passed"

echo "All evaluations passed!"
```

---

## Combined Evaluation Reports (`cxas evals report`)

The `cxas evals report` command runs evaluations across multiple types (simulations, goldens, tool tests, callback tests) and compiles them into a unified interactive HTML or machine-readable JSON report.

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--output-dir` | *(required)* | Directory containing or receiving evaluation results (`sim_results.json`, etc.) |
| `--output` | `combined_eval_report.html` | Custom destination file path (local or `gs://` bucket URI) |
| `--format` | `html` | Output format: `html` or `json` |
| `--run` | `False` | Trigger execution of evaluations before generating report |
| `--app-name` | `None` | CXAS app resource path (`projects/.../locations/.../apps/...`) |
| `--include` | `sims,goldens,tools,callbacks` | Comma-separated evaluation types to run/include |
| `--modality` | `text` | Evaluation modality: `text` or `audio` |
| `--sim-parallel` | `5` | Concurrency worker count for simulations |
| `--runs` | `1` | Number of runs per test case |
| `--audio-analysis` / `--no-audio-analysis` | `None` | Opt-in flag to run comprehensive audio analysis (multimodal audio checks, acoustic metrics, voice drift) and include the Audio Analysis tab. Off by default. |
| `--naturalness` / `--no-naturalness` | `None` | Run-level override for the naturalness metric. `--naturalness` grades every simulation; `--no-naturalness` skips grading; omitting it defers to each test case YAML. |
| `--capture-agent-audio` | `False` | Capture real-time agent output audio as WAV files for turn listening |
| `--single-bidi-stream` | `False` | Keep one persistent bidi WebSocket open per audio simulation |
| `--use-tool-fakes` | `False` | Enable tool fakes (bypass real tool backends) |
| `--min-run-passes` | `None` | Minimum passing runs per case for `k-of-N` majority voting (e.g., `2` with `--runs 3`) |
| `--min-pass-rate` | `None` | Minimum fraction of cases (`0.0`–`1.0`) that must pass; exits `1` if below threshold or if `0` cases ran |
| `--infra-retries` | `2` | Automatic retries for simulations that fail due to infrastructure errors or inconclusive/dropped audio turns (set to `0` to disable) |
| `--retry-parallel` | `max(1, sim_parallel // 2)` | Worker parallelism during simulation retry waves |
| `--retry-cooldown` | `60.0` | Cooldown in seconds before each simulation retry wave |
| `--golden-run-method` | `STABLE` | Replay method for golden evaluations (`STABLE` or `NAIVE`) |
| `--exclude-files` | `None` | Comma-separated list of filename substrings to exclude across all evaluation types |
| `--sim-user-voices` | `None` | Comma-separated pool of Google Cloud TTS voices for simulated callers (deterministically selected per scenario and matched by locale, e.g., `en-US`, `es-US`) |
| `--junit-xml` | `None` | Path to write a JUnit XML report with per-scenario `<testcase>` results and `<properties>` |
| `--testsuite-prefix` | `cxas_evals` | Prefix for JUnit XML `testsuite` and `classname` attributes |
| `--report-title` | `None` | Custom title for the rolling GCS `index.html` evaluation dashboard |
| `--dry-run` | `False` | Discover and filter local evaluation files and cases without making GCP/CES API calls |
| `--enriched-report` | `False` | Generate an enriched HTML report (`<report>_enriched.html`) with server-side CES audio players and VAD dead-air analysis |
| `--recordings-bucket` | `None` | Explicit GCS bucket/URI override for CES call audio recordings used by `--enriched-report` |
| `--gcs-bundle-uri` | `None` | GCS prefix (`gs://bucket/path`) to upload `report.html`, raw JSON/YAML/XML artifacts, `EVAL_STAMP-<suite>.txt`, `latest-<suite>.html`, rolling `runs_manifest.json`, and `index.html` |
| `--suite-name` | `None` | Suite identifier used for `EVAL_STAMP-<suite>.txt`, `latest-<suite>.html`, JUnit XML, and step summaries |
| `--github-step-summary` | `False` | Append a Markdown pass/fail table and report links to `$GITHUB_STEP_SUMMARY` |

---

## Majority Voting (`k-of-N`), Pass-Rate Gates, & GCS Report Bundles

In CI pipelines, `cxas evals report` can execute multiple runs per case, retry transient infrastructure glitches, enforce a fail-closed pass-rate gate, upload a complete report bundle to GCS, and publish a Markdown summary to `$GITHUB_STEP_SUMMARY` in a single command:

```bash
cxas evals report \
  --run \
  --include sims \
  --app-name "$APP" \
  --simulation-dir evals/simulations/ \
  --output-dir eval-reports/audio \
  --modality audio \
  --runs 3 \
  --min-run-passes 2 \
  --min-pass-rate 0.85 \
  --sim-parallel 6 \
  --single-bidi-stream \
  --skip-playback-wait \
  --use-tool-fakes \
  --suite-name sims-audio \
  --gcs-bundle-uri "gs://my-eval-bucket/evals/main/${GITHUB_RUN_ID}-sims-audio" \
  --github-step-summary
```

What this enables automatically:
1. **`k-of-N` Majority Voting (`--runs 3 --min-run-passes 2`)**: Groups runs by evaluation name and marks a case as passed when at least `k` of `N` runs pass.
2. **Fail-Closed Pass-Rate Gate (`--min-pass-rate 0.85`)**: Exits with code `1` if the reconciled case pass rate falls below `85%` or if `0` evaluations ran (preventing typoed `--filter-tags` or `--filter-names` from silently passing CI).
3. **Automatic Infrastructure & Dropped-Turn Retries (defaults: `--infra-retries 2`, `--retry-cooldown 60`, `--retry-parallel max(1, sim_parallel // 2)`)**: Enabled out of the box — automatically retries simulation runs that encounter session errors, empty judge verdicts, or silent WebSocket caller-turn drops on audio sessions while stepping concurrency down by half during retry waves. Override with `--infra-retries N` (or `--infra-retries 0` to disable).
4. **GCS Report Bundle, Rolling `runs_manifest.json`, & `index.html` Dashboard (`--gcs-bundle-uri`, `--suite-name`, `--report-title`)**: Uploads `report.html` (and `report_enriched.html` when `--enriched-report` is enabled), `sim_results.json`, `ces_results.json`, `sim_cases.yaml`, `EVAL_STAMP-<suite>.txt`, `junit*.xml`, cache-busted `latest-<suite>.html` / `latest-<suite>_enriched.html` / `latest-EVAL_STAMP-<suite>.txt` pointers, and maintains a rolling 100-run `runs_manifest.json` + self-contained `index.html` dashboard at the parent GCS prefix.
5. **JUnit XML Export (`--junit-xml`, `--testsuite-prefix`)**: Writes a standard JUnit XML report with per-scenario `<testcase>` entries across simulations, goldens, tools, and callbacks, including `<system-out>` summaries, `<failure>` details, `<properties>` for published GCS URLs, and a `__suite_pass_rate_gate__` failure entry when `--min-pass-rate` fails.
6. **Server-Side CES Audio & Dead-Air VAD Enricher (`--enriched-report`, `--recordings-bucket`)**: Fetches stereo call recordings from the CES app's GCS `logging_settings.audio_recording_config.gcs_bucket` (or `--recordings-bucket`), computes caller-to-agent Voice Activity Detection (VAD) dead-air latency per turn, and generates `<report>_enriched.html` with inline `<audio>` players and turn-seek buttons.
7. **Local Discovery Mode (`--dry-run`) & File Exclusion (`--exclude-files`)**: Validates and prints discovered simulation, golden, tool, callback, and shadow files/cases after applying `--filter-files`, `--exclude-files`, `--filter-tags`, and `--filter-names` without making any GCP/CES network calls.
8. **GitHub Actions Step Summary (`--github-step-summary`, default: `False`)**: In GitHub Actions, the runner sets the `$GITHUB_STEP_SUMMARY` environment variable to a file path whose Markdown contents are rendered directly on the workflow run's **Summary** page in the GitHub UI. When `--github-step-summary` is passed, SCRAPI appends a Markdown section containing the suite title, overall `X / Y cases passed (k-of-N pass rule)` headline, clickable `[Open HTML Report]` / `[Latest for branch]` GCS links (when `--gcs-bundle-uri` is set), and a per-case `Status | Case | Runs Passed` table. When `$GITHUB_STEP_SUMMARY` is unset (e.g., local runs), this flag is a safe no-op.

---

## Audio Analysis & Voice Quality Reports

For voice and audio agents, `cxas evals report` supports deep perceptual and acoustic analysis via the `--audio-analysis` flag:

```bash
cxas evals report \
  --run \
  --app-name "$APP" \
  --modality audio \
  --audio-analysis \
  --output-dir "eval-reports"
```

When `--audio-analysis` is enabled (opt-in; off by default):
1. **Multimodal Audio Checks**: Evaluates 5 Gemini perceptual checks (voice consistency, no long pauses, agent having trouble, agent looping, agent cutoff) directly against conversation turn audio.
2. **Signal-Level Acoustic Consistency**: Calculates ITU-R BS.1770 integrated loudness (LUFS), articulation rate (syllables/sec), median pitch (F0 in Hz and semitones), and within-turn loudness range.
3. **Voice Drift & Seam Detection**: Detects cross-turn timbre and pitch seams, abrupt loudness steps, audio truncation, and cached-versus-fresh TTS boundaries.
4. **Naturalness Grading**: Automatically activates the 5-point naturalness rubric across all conversation runs.
5. **Interactive Report Tabs**: Generates the **Audio Analysis** tab with KPI summary tiles, distribution charts, and issue breakdowns, alongside the **Conversations** run explorer.

When `--audio-analysis` is omitted (or `--no-audio-analysis` is specified), the report suppresses audio-specific KPI tiles and the Audio Analysis tab, rendering a clean text-focused report while keeping the interactive Conversations explorer available.

---

## GitHub Actions integration

Use `cxas init-github-action` to generate a pre-built GitHub Actions workflow with Workload Identity Federation authentication.

A minimal example:

```yaml
- name: Run CXAS evaluations
  run: |
    cxas ci-test \
      --app-dir cxas_app/My\ Support\ Agent \
      --project-id "${{ env.PROJECT_ID }}" \
      --location us \
      --display-name "[CI] ${{ github.ref_name }}-${{ github.sha }}"
```

The exit code from `cxas ci-test` automatically fails the GitHub Actions step if any test fails.
