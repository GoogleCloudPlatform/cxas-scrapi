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
# Push golden files to the platform
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/goldens/order_lookup.yaml

# Run evaluations and wait for results
cxas run --app-name "projects/my-project/locations/us/apps/my-app" --wait
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

### Local Simulations

Local simulations don't have a dedicated CLI command — you run them via Python:

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

sim_evals = SimulationEvals(app_name="projects/my-project/locations/us/apps/my-app")

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
cxas run --app-name "$APP" --wait --filter-auto-metrics
echo "Golden evals passed"

# 4. Local simulations (slowest — run last)
# Use the skills system's sim runner for parallel execution:
python .agents/skills/cxas-agent-foundry/scripts/scrapi-sim-runner.py run --parallel 5
echo "Simulation evals passed"

echo "All evaluations passed!"
```

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
