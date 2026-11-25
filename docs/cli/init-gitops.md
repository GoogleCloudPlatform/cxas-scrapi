# cxas init-gitops

`cxas init-gitops` scaffolds a developer-first GitOps repository layout for CX Agent Studio: environment profiles (`.cxas/profiles/{dev,qa,prod}.yml`), example environment files, `CODEOWNERS`, `.gitignore` safety rules, a release PR checklist, and SHA-pinned GitHub Actions workflows.

## Usage

```bash
cxas init-gitops [--provider github|gitlab|ssm]
                 [--model tags|branches]
                 [--target-dir DIR]
                 [--project-id PROJECT]
                 [--location LOCATION]
                 [--agent-name NAME]
                 [--app-dir DIR]
                 [--workload-identity-provider WIP]
                 [--service-account SA]
                 [--force]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--provider PROVIDER` | No | `github` | CI/CD provider template: `github` (GitHub Actions), `gitlab` (`.gitlab-ci.yml`), or `ssm` (GCP Secure Source Manager + Cloud Build). |
| `--model MODEL` | No | `tags` | Release promotion model: `tags` (SemVer tag-driven, default) or `branches` (adds `scripts/promote.sh` and weekly `dev -> qa` promotion workflow). |
| `--target-dir DIR` | No | `.` | Directory to scaffold into. |
| `--project-id PROJECT` | No | `YOUR_GCP_PROJECT_ID` | GCP project ID written into profiles and CI workflows. |
| `--location LOCATION` | No | `us` | CES location written into profiles and CI workflows. |
| `--agent-name NAME` | No | `cxas-agent` | Logical agent name used to derive default `app_id` slugs (`<name>-dev`, `<name>-qa`, `<name>-prod`). |
| `--app-dir DIR` | No | `src/agent` | Relative path to the tracked agent source directory. |
| `--workload-identity-provider WIP` | No | `${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}` | GCP Workload Identity Provider resource name for GitHub Actions OIDC auth. |
| `--service-account SA` | No | `${{ secrets.GCP_SERVICE_ACCOUNT }}` | GCP service account email for GitHub Actions. |
| `--force` | No | `false` | Overwrite existing scaffolded files. |

## Generated Files

### Common Scaffold (All Providers)

- `src/agent/` and `evals/contracts/`
- `.cxas/profiles/dev.yml` (`protected: false`), `qa.yml` (`protected: true`), `prod.yml` (`protected: true`)
- `envs/env-dev.example.json`, `envs/env-qa.example.json`, `envs/env-prod.example.json`
- `CODEOWNERS` and `.gitignore` (`*.local.*`, `envs/*.json`, `!envs/*.example.json`)
- `docs/contracts/README.md` and `docs/contracts/_template.md` (sub-agent interface contract starter)
- `cxaslint.yaml` (commented starter config showing opt-in `require_agent_contracts` / `allowed_variable_prefixes`)
- `scripts/bump-version.py` (stdlib-only SemVer tag-bump helper inspecting Conventional Commits since latest `v*` tag; supports `--apply`)
- `scripts/promote.sh` (only with `--model branches`: pre-flight checked `dev -> qa` / `qa -> main` PR/MR helper)

### Provider-Specific Pipelines

- **`--provider github` (default):**
  - `.github/PULL_REQUEST_TEMPLATE/release.md`
  - `.github/workflows/cxas_ci.yml` (Conventional Commit `pr-title-lint` job + PR gate: `cxas profile validate`, `cxas lint`, `cxas ci-test --evals-dir evals/ --snapshot --runs 3 --min-pass-rate 0.9`)
  - `.github/workflows/cxas_release.yml` (SemVer tag promotion: atomic `--with-lock` push + `--fail-if-version-exists` + `--on-drift=fail` + `deployments promote`)
  - `.github/workflows/cxas_cleanup.yml` (PR-close `cxas workspace down` + nightly `cxas workspace cleanup --all-users --include-ci --yes`)
  - `.github/workflows/cxas_promote.yml` (only with `--model branches`: Monday 09:00 UTC scheduled + manual `dev -> qa` promotion PR via `gh pr create`)
  - All GitHub Actions workflows pin third-party actions to full 40-character commit SHAs, declare minimal `permissions:`, and set `persist-credentials: false` on `actions/checkout`.
- **`--provider gitlab`:**
  - `.gitlab-ci.yml` with stages `[lint, test, release, cleanup]`, `mr-title-lint` job, masked/protected `ENV_DEV_JSON`/`ENV_QA_JSON`/`ENV_PROD_JSON` handling, manual `prod` promotion gate, and (with `--model branches`) scheduled `promote-dev-to-qa` job.
  - `.gitlab/merge_request_templates/release.md`
- **`--provider ssm` (GCP Secure Source Manager + Cloud Build):**
  - `cloudbuild-ci.yaml` (with optional `_PR_TITLE` Conventional Commit check), `cloudbuild-release.yaml`, `cloudbuild-cleanup.yaml`, and (with `--model branches`) `cloudbuild-promote.yaml` using Secret Manager `availableSecrets` and native Cloud Build service account auth.
  - `docs/release-checklist.md`

## Related Commands

- [`cxas profile`](profile.md) — Inspect and validate generated profiles.
- [`cxas workspace`](workspace.md) — Spin up and tear down ephemeral feature-branch workspaces.
- [`cxas ci-test`](ci-test.md) — Run the PR gate locally or in CI.
- [`cxas init-github-action`](init-github-action.md) — Legacy Docker-based GitHub Actions generator (preserved for backward compatibility).
