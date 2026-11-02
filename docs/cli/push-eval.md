# cxas push-eval

`cxas push-eval` reads evaluation definitions from a local YAML file or directory and syncs them to an app in CX Agent Studio — keeping your golden and milestone scenario evaluations under version control and deploying them on demand.

## Usage

```
cxas push-eval --app-name APP --file FILE
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the target app (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--file FILE` | Yes | — | Path to the YAML file or directory containing Golden (`conversations:`), Simulation/Milestone (`evals:`), or exported Scenario Milestone (`scenario.milestones`) definitions. |

## Supported YAML File Formats

`cxas push-eval` automatically detects the evaluation format from the YAML structure:

### 1. Golden Evaluations (`conversations:`)

Files with a `conversations:` key (or a single golden `turns:` definition) are parsed using `EvalUtils.load_golden_evals_from_yaml` and synced as deterministic turn-by-turn Golden evaluations:

```yaml
common_session_parameters:
  order_12345_status: "shipped"

conversations:
  - conversation: "Billing Query — Golden"
    turns:
      - user: "What is my current balance?"
        agent: "I can look up your balance. Could you provide your account number?"
```

### 2. Simulation & Milestone Evals (`evals:` → CXAS 2.0 Milestone Scenarios)

Files (or directories of files) with an `evals:` key or `steps:` list are sanitized into CXAS 2.0 Platform Milestone YAML via `EvalUtils.convert_sim_evals_to_milestone_yaml` (stripping client-only keys such as `audio_expectations`, `naturalness_metric`, and `barge_in_after_ms`, and hoisting `inject_variables` from later steps into `session_parameters`) and imported onto the platform via `EvalUtils.push_milestone_evals_from_yaml` (`POST /v1beta/{app}:importEvaluations` with `conflictResolutionStrategy: "OVERWRITE"`):

```yaml
evals:
  - name: "authenticated_billing_inquiry"
    tags: ["P0", "billing"]
    session_parameters:
      account_id: "9820598207"
    steps:
      - goal: "Ask about current account balance after authenticating"
        success_criteria: "Agent authenticates the caller and provides billing information"
        response_guide: "Provide account ID 9820598207 when asked."
        max_turns: 6
        inject_variables:
          auth_state: "verified"
    expectations:
      - "The agent must verify the caller's identity before providing account details"
```

### 3. Platform Scenario Milestone Dicts (`scenario.milestones`)

Evaluation dictionaries exported in the platform `scenario.milestones` format are normalized via `Evaluations.eval_dict_to_yaml` and `EvalUtils.sanitize_milestone_eval_dict`, then imported via the CES `:importEvaluations` endpoint:

```yaml
display_name: "Order Status Milestone Eval"
tags: ["P0"]
scenario:
  session_parameters:
    order_id: "ORD-12345"
  milestones:
    - goal: "Ask about order ORD-12345"
      successCriteria: "Agent provides shipping status"
      responseGuide: "Polite customer checking order status"
      maxTurnCount: 4
```

Each evaluation is synced independently. Existing evaluations with the same display name are overwritten in place.

## Examples

**Push golden evaluations from a YAML file:**

```bash
cxas push-eval \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --file evaluations/golden-evals.yaml
```

**Push simulation evaluations (file or directory) as CXAS 2.0 platform milestone evaluations:**

```bash
cxas push-eval \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --file evaluations/simulation-evals.yaml
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
