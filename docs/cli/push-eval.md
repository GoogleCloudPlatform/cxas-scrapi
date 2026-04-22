# cxas push-eval

`cxas push-eval` reads evaluation definitions from a local YAML file and syncs them to an app in CX Agent Studio — the easiest way to keep your golden evals under version control and deploy them on demand.

## Usage

```
cxas push-eval --app-name APP --file FILE
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the target app (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--file FILE` | Yes | — | Path to the YAML file containing one or more evaluation definitions. |

## YAML File Format

The YAML file should contain a list of evaluation objects. Each object is parsed using `EvalUtils.load_golden_evals_from_yaml` and then pushed via `Evaluations.update_evaluation`. A typical file looks like this:

```yaml
- displayName: "Billing Query — Golden"
  description: "Tests that the agent correctly routes billing questions."
  conversations:
    - turns:
        - userInput: "What is my current balance?"
          expectedAgentResponse: "I can look up your balance. Could you provide your account number?"
```

Each evaluation in the file is synced independently. If an evaluation with the same display name already exists, it is updated in place.

## Examples

**Push all evaluations from a YAML file:**

```bash
cxas push-eval \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --file evaluations/golden-evals.yaml
```

**Push after pulling a branch to sync evals to a new app:**

```bash
# 1. Branch the app
cxas branch "Support Agent — Prod" \
  --new-name "Support Agent — Staging" \
  --project-id my-gcp-project \
  --location us-central1

# 2. Export evals from prod
cxas export \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --output /tmp/golden.yaml

# 3. Push evals to the new staging app
cxas push-eval \
  --app-name projects/my-gcp-project/locations/us-central1/apps/staging-xyz \
  --file /tmp/golden.yaml
```

## Related Commands

- [`cxas export`](export.md) — Export existing evaluations from an app to a local YAML file.
- [`cxas run`](run.md) — Run evaluations after pushing them.
- [`cxas ci-test`](ci-test.md) — The CI lifecycle automatically picks up evaluations that have been pushed.
