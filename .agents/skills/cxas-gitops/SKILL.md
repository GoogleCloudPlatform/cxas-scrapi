---
name: cxas-gitops
description: >
  Developer-first GitOps workflow for CX Agent Studio (CXAS) using --profile
  overlays, ephemeral feature-branch workspaces, native CES edit locks,
  stateless drift gates, snapshot evaluations, and SemVer tag promotions with
  instant rollback. Use when setting up CI/CD for CXAS agents, onboarding a
  multi-team repository, debugging drift/lock errors, or promoting/rolling back
  releases.
---

# CXAS GitOps Skill

## Core Principle

**Git is the source of truth; CES apps are deployment targets.**
Never edit tracked `app.json` or `gecx-config.json` to target a personal sandbox
or test model settings — use `.cxas/profiles/*.local.yml` and `CXAS_PROFILE`.

## 1. Bootstrap a GitOps Repository

```bash
cxas init-gitops \
  --provider github \   # or gitlab | ssm (GCP Secure Source Manager + Cloud Build)
  --project-id <gcp-project> \
  --location <ces-location> \
  --agent-name <agent-slug>
```

Scaffolds `src/agent/`, `evals/contracts/`, `.cxas/profiles/{dev,qa,prod}.yml`
(`protected: true` on `qa`/`prod`), `envs/*.example.json`, `CODEOWNERS`,
`.gitignore`, release checklist, and provider-specific CI/CD pipelines
(SHA-pinned GitHub Actions, `.gitlab-ci.yml`, or Cloud Build `cloudbuild-*.yaml`).

Validate profiles immediately:

```bash
cxas profile validate --all
```

## 2. Daily Zero-Stash Developer Loop

```bash
git checkout -b feat/my-change
cxas workspace up --profile dev --team <team>
export CXAS_PROFILE=ws.local

# Edit instructions/tools/callbacks locally, then iterate:
cxas lint --app-dir src/agent
cxas push --dry-run
cxas push
cxas run --tags smoke --snapshot --wait

# Capture ad-hoc Console tweaks into a clean Git branch:
cxas pull --profile dev --branch fix/console-tweak

# Tear down sandbox when done:
cxas workspace down --yes
```

## 3. PR Gate (CI)

```bash
cxas profile validate --all
cxas lint --app-dir src/agent
cxas ci-test \
  --app-dir src/agent \
  --project-id <project> \
  --location <location> \
  --display-name "[CI] PR-${PR_NUMBER}" \
  --evals-dir evals/ \
  --snapshot \
  --runs 3 \
  --min-pass-rate 0.9
```

## 4. Protected Release & Instant Rollback

```bash
# Tag-driven CI release (CI=true satisfies protected: true)
cxas push \
  --profile prod \
  --with-lock \
  --create-version \
  --version-name v1.2.0 \
  --fail-if-version-exists \
  --on-drift=fail

cxas deployments promote --profile prod --version v1.2.0

# Instant rollback if production regresses
cxas rollback --profile prod --deployment live --version v1.1.0 --allow-protected
```

## Troubleshooting

- **Protected profile blocked locally:** Pass `--allow-protected` (or preview safely with `--dry-run`).
- **Drift detected (`--on-drift=fail`):** Run `cxas pull --profile <env> --branch fix/console-drift` to inspect and merge Console edits into Git, or pass `--on-drift=overwrite` to overwrite intentionally.
- **App locked by another user:** Check `cxas lock-status --profile <env>`. Never bypass another engineer's active lock without coordinating (`cxas unlock --force` if truly abandoned).
