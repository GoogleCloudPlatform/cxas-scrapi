# cxas push

`cxas push` uploads a local agent directory to CX Agent Studio, either updating an existing app or creating a fresh one — it's how your local edits make it into the platform.

## Usage

```
cxas push [--app-dir DIR] [--to TARGET] [--env-file FILE]
          [--app-name APP] [--display-name NAME]
          --project-id PROJECT --location LOCATION
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the local agent directory to upload. |
| `--to TARGET` | No | — | Target app to overwrite. Accepts a full resource name or display name. When provided, the existing app is updated in place. |
| `--env-file FILE` | No | — | Path to a JSON environment file to inject as `environment.json` before uploading. Useful for supplying environment-specific secrets or variables without committing them. |
| `--app-name APP` | No | — | Explicit app resource name to push to (v1beta API). Use this when you want to overwrite a specific app and already know its full ID. |
| `--display-name NAME` | No | `"Pushed Agent"` | Display name for the new app when no target is specified and a new app will be created. |
| `--project-id PROJECT` | Yes | — | GCP project ID. |
| `--location LOCATION` | Yes | — | GCP location (e.g., `global`, `us-central1`). |

## What Gets Uploaded

The CLI bundles the following top-level items from `--app-dir` into a ZIP archive before uploading:

- `app.yaml` / `app.json`
- `global_instruction.txt`
- `environment.json` (or the file you pass via `--env-file`)
- `agents/`, `tools/`, `guardrails/`, `toolsets/`
- `evaluations/`, `evaluationDatasets/`, `evaluationExpectations/`
- `.github/workflows/`

Everything else in the directory (e.g. `.git`, local test output) is ignored.

## Examples

**Push the current directory to an existing app by display name:**

```bash
cxas push \
  --to "My Support Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

**Push a specific directory and create a new app with a custom display name:**

```bash
cxas push \
  --app-dir ./my-agent \
  --display-name "My Support Agent (Staging)" \
  --project-id my-gcp-project \
  --location us-central1
```

**Push using a separate environment file (keeps secrets out of source control):**

```bash
cxas push \
  --app-dir ./my-agent \
  --to projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --env-file ./secrets/env-prod.json \
  --project-id my-gcp-project \
  --location us-central1
```

## Related Commands

- [`cxas pull`](pull.md) — Download an app to a local directory first.
- [`cxas create`](create.md) — Create an empty app shell before pushing.
- [`cxas ci-test`](ci-test.md) — Run the full push + test lifecycle in CI.
