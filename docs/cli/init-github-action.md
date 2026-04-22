# cxas init-github-action

`cxas init-github-action` generates a ready-to-use GitHub Actions workflow file for your agent — you answer a few questions (or pass flags) and get a workflow that automatically tests your agent on every push.

## Usage

```
cxas init-github-action [--app-dir DIR] [--app-name APP]
                         [--workload-identity-provider WIP]
                         [--service-account SA]
                         [--branch BRANCH]
                         [--no-cleanup]
                         [--install-hook]
                         [--auto-create-wif]
                         [--wif-pool-name NAME]
                         [--github-repo OWNER/REPO]
                         [--output PATH]
                         [--project-id PROJECT]
                         [--location LOCATION]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | — | Path to the agent directory. If provided, the CLI reads `app.yaml` to extract the app name and agent name automatically. |
| `--app-name APP` | No | — | Full resource name of the CXAS app. If omitted and `--app-dir` is provided, the CLI reads it from `agent_dir/app.yaml`. |
| `--workload-identity-provider WIP` | No | — | GCP Workload Identity Provider resource name. If omitted, the workflow uses `CXAS_OAUTH_TOKEN` as a secret instead. |
| `--service-account SA` | No | — | GCP Service Account email to impersonate via Workload Identity Federation. |
| `--branch BRANCH` | No | `main` | The branch that triggers the deploy job in the workflow. |
| `--no-cleanup` | No | `false` | Skip generating the cleanup workflow that deletes stale CI apps after PRs are closed. |
| `--install-hook` | No | `false` | Install a git `pre-push` hook in the repository that runs `cxas local-test` before every push. |
| `--auto-create-wif` | No | `false` | Automatically create the Workload Identity Pool, Provider, and Service Account on Google Cloud. Requires `gcloud` to be configured. |
| `--wif-pool-name NAME` | No | `github-actions-pool-scrapi` | Name of the Workload Identity Pool to create or reuse. |
| `--github-repo OWNER/REPO` | No | — | Override the GitHub repository used for Workload Identity binding (e.g., `myorg/my-repo`). If omitted, inferred from the local git remote. |
| `--output PATH` | No | `.github/workflows/test_{agent_name}.yml` | Override the output path for the generated workflow file. |
| `--project-id PROJECT` | No | — | GCP project ID. |
| `--location LOCATION` | No | — | GCP location. |

## What Gets Generated

By default, the command creates:

- **`.github/workflows/test_{agent_name}.yml`** — A CI workflow that runs on pull requests and pushes to the configured branch. It calls `cxas ci-test` using either Workload Identity Federation or an OAuth token secret.
- **`.github/workflows/cleanup_{agent_name}.yml`** — A cleanup workflow (unless `--no-cleanup` is passed) that deletes temporary CI apps when pull requests are closed.

If `--install-hook` is passed, a git `pre-push` hook is also installed at `.git/hooks/pre-push` that runs `cxas local-test` before any push.

## Examples

**Generate a workflow by pointing at the agent directory:**

```bash
cxas init-github-action --app-dir ./my-agent
```

**Generate with Workload Identity Federation (recommended for production):**

```bash
cxas init-github-action \
  --app-dir ./my-agent \
  --workload-identity-provider "projects/123/locations/global/workloadIdentityPools/github-actions-pool-scrapi/providers/github" \
  --service-account "my-ci-sa@my-gcp-project.iam.gserviceaccount.com" \
  --project-id my-gcp-project \
  --location us-central1
```

**Automatically create WIF resources and generate the workflow in one step:**

```bash
cxas init-github-action \
  --app-dir ./my-agent \
  --auto-create-wif \
  --project-id my-gcp-project \
  --location us-central1
```

**Generate a workflow that targets the `develop` branch and also installs the pre-push hook:**

```bash
cxas init-github-action \
  --app-dir ./my-agent \
  --branch develop \
  --install-hook \
  --project-id my-gcp-project \
  --location us-central1
```

**Write the workflow to a custom path:**

```bash
cxas init-github-action \
  --app-dir ./my-agent \
  --output .github/workflows/ci.yml \
  --project-id my-gcp-project \
  --location us-central1
```

## Related Commands

- [`cxas ci-test`](ci-test.md) — The command the generated workflow calls to run tests.
- [`cxas local-test`](local-test.md) — Run the same lifecycle locally before pushing.
- [`cxas init`](init.md) — Bootstrap the full project with AI skills and config files.
