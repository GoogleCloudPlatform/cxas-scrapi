---
title: GitOps & Multi-Team Releases
description: Developer-first GitOps guide for CX Agent Studio using --profile overlays, ephemeral workspaces, native locks, stateless drift gates, snapshot evals, and SemVer promotion with instant rollback.
---

# GitOps & Multi-Team Releases

This guide describes the recommended **GitOps workflow** for developing, testing, and promoting CX Agent Studio agents across `dev`, `qa`, and `prod` environments without stash tax, config collisions, or accidental overwrites.

> **Looking for basic Git branching concepts?** Start with [Team Collaboration](team-collaboration.md). This page builds on it with the full `--profile`, `workspace`, `lock`, `drift`, `snapshot`, and `init-gitops` toolchain.

---

## 1. Repository Layout (`cxas init-gitops`)

Bootstrap a new or existing repository with a single command:

```bash
cxas init-gitops \
  --project-id my-gcp-project \
  --location us \
  --agent-name support-agent
```

This creates:

```text
.
├── src/agent/                              # Tracked CES app source (app.json, agents/, tools/)
├── evals/
│   └── contracts/                          # Cross-team contract evaluations
├── .cxas/profiles/
│   ├── dev.yml                             # Unprotected dev target
│   ├── qa.yml                              # protected: true
│   ├── prod.yml                            # protected: true
│   └── ws.local.yml                        # Gitignored personal workspace profile
├── envs/
│   ├── env-dev.example.json                # Committed schema template
│   ├── env-qa.example.json
│   └── env-prod.example.json
├── docs/contracts/
│   ├── README.md                           # Sub-agent interface contract guide
│   └── _template.md                        # Contract starter template
├── scripts/
│   ├── bump-version.py                     # Conventional-Commit SemVer tag helper
│   └── promote.sh                          # (--model branches) dev->qa / qa->main helper
├── cxaslint.yaml                           # Commented opt-in governance rules
├── CODEOWNERS
└── .github/
    ├── PULL_REQUEST_TEMPLATE/release.md
    └── workflows/
        ├── cxas_ci.yml                     # PR title lint + lint + snapshot ci-test
        ├── cxas_release.yml                # SemVer tag promotion (locked + drift-gated)
        ├── cxas_cleanup.yml                # PR-close & nightly workspace sweep
        └── cxas_promote.yml                # (--model branches) weekly dev->qa PR
```

### Conventional Commits, Promotion Models & Opt-In Governance

- **PR/MR title lint & SemVer helper:** Every provider CI pipeline validates PR/MR titles against Conventional Commits, and `scripts/bump-version.py` computes (and with `--apply`, tags) the next `vX.Y.Z` release from commit history.
- **Tag vs. branch promotion (`--model tags|branches`):** Default `--model tags` uses SemVer tag releases. Teams using long-lived `dev`/`qa`/`main` branches can pass `--model branches` to also scaffold `scripts/promote.sh` and a weekly scheduled promotion workflow (`cxas_promote.yml`, GitLab `promote-dev-to-qa`, or `cloudbuild-promote.yaml`).
- **Opt-in contract & variable governance:** Uncomment `require_agent_contracts: true` (rule `S014`) or `allowed_variable_prefixes` (rule `V105`) in `cxaslint.yaml` when your team is ready to enforce sub-agent contract docs or session variable naming conventions. Both rules are completely silent by default.

---

## 2. Zero-Stash Developer Loop (`--profile` & `workspace`)

Instead of editing tracked `app.json` or `gecx-config.json` to point at a personal sandbox or experiment with a new model, create a gitignored local profile or spin up an ephemeral branch workspace:

```bash
git checkout -b feat/billing-handoff
cxas workspace up --profile dev --team billing
export CXAS_PROFILE=ws.local

# Preview merged config diffs without calling CES
cxas push --dry-run

# Push to your personal workspace (tracked files stay clean!)
cxas push

# Run snapshot-pinned evals so concurrent edits never cause flakiness
cxas run --tags billing --snapshot --wait

# Tear down when finished
cxas workspace down --yes
```

### Safe Pulls & Capturing Console Tweaks

- `cxas pull --profile <name>` preserves tracked base values for any keys overridden by `overrides.app.json`/`app.yaml` (unless `--accept-profile-overrides` is passed).
- `cxas pull --profile dev --branch fix/console-tweak` creates a clean Git branch before pulling ad-hoc Cloud Console edits so they can be reviewed in a PR.

---

## 3. Release Guardrails: Push vs. Deploy vs. Rollback

| Action | Command | What It Changes |
|--------|---------|-----------------|
| **Push draft + version** | `cxas push --profile prod --with-lock --create-version --version-name v1.2.0 --fail-if-version-exists --on-drift=fail` | Updates CES draft atomically (unlock → push → re-lock), records `cxas.last_push`, and creates immutable version `v1.2.0`. |
| **Promote traffic** | `cxas deployments promote --profile prod --version v1.2.0` | Resolves `v1.2.0` and flips `Deployment.app_version` to serve live traffic. |
| **Instant rollback** | `cxas rollback --profile prod --deployment live --version v1.1.0 --allow-protected` | Flips `Deployment.app_version` back to `v1.1.0` and warns if other deployments still point to the broken version. |

### Guardrail Guarantees

- **`protected: true`:** Local mutations (`push`, `delete`, `deployments promote`, `rollback`) refuse to run against `qa`/`prod` unless `--allow-protected` or `CI=true` is set.
- **Native locks (`cxas lock` / `--with-lock`):** Foreign edit locks are never overridden by `--force`; `--with-lock` guarantees re-locking in `finally` even if a push fails mid-flight.
- **Stateless drift gate (`--on-drift=fail`):** Compares live CES changelogs against `cxas.last_push` stored in `App.metadata` (ignoring lock/metadata bookkeeping) so stateless CI runners detect out-of-band Console tweaks with zero local state files.

---

## 4. Alternative CI Providers (`--provider gitlab` & `--provider ssm`)

`cxas init-gitops` defaults to `--provider github` (SHA-pinned GitHub Actions), and also scaffolds ready-to-use pipelines for GitLab CI (`--provider gitlab`) and GCP Secure Source Manager + Cloud Build (`--provider ssm`):

```bash
# GitLab CI (.gitlab-ci.yml + .gitlab/merge_request_templates/release.md)
cxas init-gitops --provider gitlab --project-id my-gcp-project --location us

# GCP Secure Source Manager + Cloud Build (cloudbuild-{ci,release,cleanup}.yaml)
cxas init-gitops --provider ssm --project-id my-gcp-project --location us
```

Reference layouts:

### GitLab CI (`.gitlab-ci.yml`)

```yaml
stages: [validate, test, release]

lint_and_profiles:
  stage: validate
  image: python:3.11-slim
  script:
    - pip install cxas-scrapi
    - cxas profile validate --all
    - cxas lint --app-dir src/agent

pr_evals:
  stage: test
  image: python:3.11-slim
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  script:
    - pip install cxas-scrapi
    - cxas ci-test
        --app-dir src/agent
        --project-id "$PROJECT_ID"
        --location "$LOCATION"
        --display-name "[CI] PR-${CI_MERGE_REQUEST_IID}"
        --evals-dir evals/
        --snapshot
        --runs 3
        --min-pass-rate 0.9

release_tag:
  stage: release
  image: python:3.11-slim
  rules:
    - if: '$CI_COMMIT_TAG =~ /^v[0-9]+\.[0-9]+\.[0-9]+$/'
  script:
    - pip install cxas-scrapi
    - cxas push --profile prod --with-lock --create-version --version-name "$CI_COMMIT_TAG" --fail-if-version-exists --on-drift=fail
    - cxas deployments promote --profile prod --version "$CI_COMMIT_TAG"
```

### GCP Secure Source Manager / Cloud Build (`cloudbuild.yaml`)

```yaml
steps:
  - name: python:3.11-slim
    entrypoint: bash
    args:
      - -euo
      - pipefail
      - -c
      - |
        pip install --quiet cxas-scrapi
        cxas profile validate --all
        cxas lint --app-dir src/agent
        cxas push \
          --profile "${_TARGET_ENV}" \
          --with-lock \
          --create-version \
          --version-name "${TAG_NAME}" \
          --fail-if-version-exists \
          --on-drift=fail
        cxas deployments promote \
          --profile "${_TARGET_ENV}" \
          --version "${TAG_NAME}"
substitutions:
  _TARGET_ENV: prod
```
