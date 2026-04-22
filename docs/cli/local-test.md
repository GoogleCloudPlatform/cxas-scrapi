# cxas local-test

`cxas local-test` builds a Docker image from your agent directory and runs the full `ci-test` lifecycle inside the container — so you can catch issues on your laptop before they hit your CI pipeline.

## Usage

```
cxas local-test [--app-dir DIR]
                --project-id PROJECT
                --location LOCATION
                [--env-file FILE]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the agent directory. This directory must contain a `Dockerfile`. |
| `--project-id PROJECT` | Yes | — | GCP project ID, forwarded to the container as an environment variable. |
| `--location LOCATION` | Yes | — | GCP location, forwarded to the container (e.g., `global`, `us-central1`). |
| `--env-file FILE` | No | — | Path to a JSON environment file, forwarded to the container and injected as `environment.json` inside the `ci-test` run. |

## How It Works

`cxas local-test` does two things:

1. **Builds** a Docker image from your agent directory using `docker build -t <agent-name>-local-test <agent_dir>`.
2. **Runs** `cxas ci-test` inside the container, mounting the agent directory at `/workspace` and forwarding your GCP credentials.

### Credential Handling

The CLI automatically detects your credentials and forwards them to the container:

- If `CXAS_OAUTH_TOKEN` is set in your environment, it's passed directly.
- Otherwise, it mounts your Application Default Credentials file (from `GOOGLE_APPLICATION_CREDENTIALS` or `~/.config/gcloud/application_default_credentials.json`) into the container as a read-only volume.

The display name used for the temporary app inside the container is `[Local] <agent-name>`.

## Prerequisites

- Docker must be installed and running.
- Your agent directory must contain a `Dockerfile`.
- You need valid GCP credentials in your environment.

## Examples

**Run local tests from the current directory:**

```bash
cxas local-test \
  --project-id my-gcp-project \
  --location us-central1
```

**Run for a specific agent directory:**

```bash
cxas local-test \
  --app-dir ./my-agent \
  --project-id my-gcp-project \
  --location us-central1
```

**Use a secrets environment file:**

```bash
cxas local-test \
  --app-dir ./my-agent \
  --project-id my-gcp-project \
  --location us-central1 \
  --env-file ./secrets/env-local.json
```

**Quick smoke test before opening a PR:**

```bash
# Authenticate with gcloud first
gcloud auth application-default login

# Then run local tests
cxas local-test --project-id my-gcp-project --location us-central1
```

## Related Commands

- [`cxas ci-test`](ci-test.md) — The same lifecycle without Docker, used directly in CI.
- [`cxas push`](push.md) — Push without running tests.
- [`cxas init-github-action`](init-github-action.md) — Generate a GitHub Actions workflow with an optional pre-push hook that calls `cxas local-test` automatically.
