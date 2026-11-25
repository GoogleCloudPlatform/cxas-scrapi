# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""GitOps repository scaffolding (`cxas init-gitops`).

Generates a lean, developer-first GitOps layout with:
- ``src/agent/`` and ``evals/contracts/``
- ``.cxas/profiles/{dev,qa,prod}.yml`` (``protected: true`` on ``qa``/``prod``)
- ``envs/{env-dev,env-qa,env-prod}.example.json``
- ``CODEOWNERS``, ``.gitignore`` safety entries, and release PR template
- SHA-pinned, minimal-permission GitHub Actions workflows (``cxas_ci.yml``,
  ``cxas_release.yml``, ``cxas_cleanup.yml``) with
  ``persist-credentials: false``
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import argparse

# Pinned 40-char commit SHAs for GitHub Actions (#573 compliance).
SHA_CHECKOUT = (
    "11bd71901bbe5b1630ceea73d27597364c9af683"  # actions/checkout@v4.2.2
)
SHA_SETUP_PYTHON = (
    "a26af69be951a213d495a4c3e4e4022e16d87065"  # actions/setup-python@v5.6.0
)
SHA_SETUP_UV = (
    "08807647e7069bb48b6ef5acd8ec9567f424441b"  # astral-sh/setup-uv@v6
)
# google-github-actions/auth@v2.1.8
SHA_GCP_AUTH = "6fc4af4b145ae7821d527454aa9bd537d1f2dc5f"

DEFAULT_WIP = "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
DEFAULT_SA = "${{ secrets.GCP_SERVICE_ACCOUNT }}"

SUPPORTED_PROVIDERS = ("github", "gitlab", "ssm")

_GITIGNORE_ENTRIES = (
    "# CXAS GitOps local overrides & secrets",
    "*.local.*",
    "envs/*.json",
    "!envs/*.example.json",
)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return cleaned or "cxas-agent"


def _render_ci_workflow(
    *,
    project_id: str,
    location: str,
    app_dir: str,
    wip: str,
    sa: str,
) -> str:
    return f"""name: CXAS PR Gate (Lint & Snapshot Evals)

on:
  pull_request:
    paths:
      - '{app_dir}/**'
      - 'evals/**'
      - '.cxas/profiles/**'

permissions:
  contents: read
  id-token: write

env:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
  GCP_WIP: "{wip}"
  GCP_SERVICE_ACCOUNT: "{sa}"

jobs:
  pr-gate:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@{SHA_CHECKOUT} # v4.2.2
        with:
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@{SHA_SETUP_PYTHON} # v5.6.0
        with:
          python-version: "3.11"

      - name: Set up uv
        uses: astral-sh/setup-uv@{SHA_SETUP_UV} # v6
        with:
          enable-cache: true

      - name: Install cxas-scrapi
        run: uv tool install cxas-scrapi

      - name: Validate profiles and lint agent
        run: |
          cxas profile validate --all
          cxas lint --app-dir "{app_dir}"

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@{SHA_GCP_AUTH} # v2.1.8
        with:
          workload_identity_provider: ${{{{ env.GCP_WIP }}}}
          service_account: ${{{{ env.GCP_SERVICE_ACCOUNT }}}}

      - name: Run ephemeral PR CI test with snapshot evals
        run: |
          PR_NUM="${{{{ github.event.pull_request.number }}}}"
          cxas ci-test \\
            --app-dir "{app_dir}" \\
            --project-id "${{{{ env.PROJECT_ID }}}}" \\
            --location "${{{{ env.LOCATION }}}}" \\
            --display-name "[CI] PR-${{PR_NUM}}" \\
            --evals-dir evals/ \\
            --snapshot \\
            --runs 3 \\
            --min-pass-rate 0.9
"""


def _render_release_workflow(
    *,
    wip: str,
    sa: str,
) -> str:
    return f"""name: CXAS Protected Release & Promotion

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      environment:
        description: Target profile (qa or prod)
        required: true
        type: choice
        options:
          - qa
          - prod
      version_tag:
        description: Git tag to release (e.g. v1.2.0)
        required: true
        type: string

permissions:
  contents: read
  id-token: write

env:
  GCP_WIP: "{wip}"
  GCP_SERVICE_ACCOUNT: "{sa}"

jobs:
  promote:
    runs-on: ubuntu-latest
    environment: ${{{{ inputs.environment || 'qa' }}}}
    steps:
      - name: Checkout release tag
        uses: actions/checkout@{SHA_CHECKOUT} # v4.2.2
        with:
          ref: ${{{{ inputs.version_tag || github.ref_name }}}}
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@{SHA_SETUP_PYTHON} # v5.6.0
        with:
          python-version: "3.11"

      - name: Set up uv
        uses: astral-sh/setup-uv@{SHA_SETUP_UV} # v6
        with:
          enable-cache: true

      - name: Install cxas-scrapi
        run: uv tool install cxas-scrapi

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@{SHA_GCP_AUTH} # v2.1.8
        with:
          workload_identity_provider: ${{{{ env.GCP_WIP }}}}
          service_account: ${{{{ env.GCP_SERVICE_ACCOUNT }}}}

      - name: Atomic locked push, version snapshot, and promote
        env:
          TARGET_ENV: ${{{{ inputs.environment || 'qa' }}}}
          RELEASE_TAG: ${{{{ inputs.version_tag || github.ref_name }}}}
        run: |
          cxas push \\
            --profile "${{TARGET_ENV}}" \\
            --with-lock \\
            --create-version \\
            --version-name "${{RELEASE_TAG}}" \\
            --fail-if-version-exists \\
            --on-drift=fail
          cxas deployments promote \\
            --profile "${{TARGET_ENV}}" \\
            --version "${{RELEASE_TAG}}"
"""


def _render_cleanup_workflow(
    *,
    project_id: str,
    location: str,
    wip: str,
    sa: str,
) -> str:
    return f"""name: CXAS Ephemeral Workspace Cleanup

on:
  pull_request:
    types: [closed]
  schedule:
    - cron: '0 3 * * *'
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

env:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
  GCP_WIP: "{wip}"
  GCP_SERVICE_ACCOUNT: "{sa}"

jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@{SHA_CHECKOUT} # v4.2.2
        with:
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@{SHA_SETUP_PYTHON} # v5.6.0
        with:
          python-version: "3.11"

      - name: Set up uv
        uses: astral-sh/setup-uv@{SHA_SETUP_UV} # v6
        with:
          enable-cache: true

      - name: Install cxas-scrapi
        run: uv tool install cxas-scrapi

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@{SHA_GCP_AUTH} # v2.1.8
        with:
          workload_identity_provider: ${{{{ env.GCP_WIP }}}}
          service_account: ${{{{ env.GCP_SERVICE_ACCOUNT }}}}

      - name: Tear down PR workspace on PR close
        if: github.event_name == 'pull_request'
        run: |
          cxas workspace down \\
            --pr "${{{{ github.event.pull_request.number }}}}" \\
            --project-id "${{{{ env.PROJECT_ID }}}}" \\
            --location "${{{{ env.LOCATION }}}}" \\
            --yes || true

      - name: Sweep expired workspaces & orphaned CI apps
        if: github.event_name != 'pull_request'
        run: |
          cxas workspace cleanup \\
            --all-users \\
            --include-ci \\
            --yes \\
            --project-id "${{{{ env.PROJECT_ID }}}}" \\
            --location "${{{{ env.LOCATION }}}}"
"""


def _render_gitlab_ci(
    *,
    project_id: str,
    location: str,
    app_dir: str,
) -> str:
    return f"""stages:
  - lint
  - test
  - release
  - cleanup

default:
  image: python:3.11-slim

variables:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
  APP_DIR: "{app_dir}"
  CI: "true"

.install_cxas: &install_cxas
  before_script:
    - pip install --quiet cxas-scrapi

lint_and_validate:
  stage: lint
  <<: *install_cxas
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_TAG =~ /^v[0-9]+\\.[0-9]+\\.[0-9]+$/'
  script:
    - cxas profile validate --all
    - cxas lint --app-dir "$APP_DIR"

pr_snapshot_evals:
  stage: test
  <<: *install_cxas
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  script:
    - |
      if [ -n "${{ENV_DEV_JSON:-}}" ]; then
        mkdir -p envs
        printf '%s' "$ENV_DEV_JSON" > envs/env-dev.json
      fi
    - >
      cxas ci-test
      --app-dir "$APP_DIR"
      --project-id "$PROJECT_ID"
      --location "$LOCATION"
      --display-name "[CI] PR-${{CI_MERGE_REQUEST_IID}}"
      --evals-dir evals/
      --snapshot
      --runs 3
      --min-pass-rate 0.9

release_qa:
  stage: release
  <<: *install_cxas
  environment:
    name: qa
  rules:
    - if: '$CI_COMMIT_TAG =~ /^v[0-9]+\\.[0-9]+\\.[0-9]+$/'
  script:
    - |
      if [ -n "${{ENV_QA_JSON:-}}" ]; then
        mkdir -p envs
        printf '%s' "$ENV_QA_JSON" > envs/env-qa.json
      fi
    - >
      cxas push
      --profile qa
      --with-lock
      --create-version
      --version-name "$CI_COMMIT_TAG"
      --fail-if-version-exists
      --on-drift=fail
    - cxas deployments promote --profile qa --version "$CI_COMMIT_TAG"

release_prod:
  stage: release
  <<: *install_cxas
  needs: [release_qa]
  environment:
    name: prod
  when: manual
  rules:
    - if: '$CI_COMMIT_TAG =~ /^v[0-9]+\\.[0-9]+\\.[0-9]+$/'
  script:
    - |
      if [ -n "${{ENV_PROD_JSON:-}}" ]; then
        mkdir -p envs
        printf '%s' "$ENV_PROD_JSON" > envs/env-prod.json
      fi
    - >
      cxas push
      --profile prod
      --with-lock
      --create-version
      --version-name "$CI_COMMIT_TAG"
      --fail-if-version-exists
      --on-drift=fail
    - cxas deployments promote --profile prod --version "$CI_COMMIT_TAG"

nightly_workspace_cleanup:
  stage: cleanup
  <<: *install_cxas
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
  script:
    - >
      cxas workspace cleanup
      --all-users
      --include-ci
      --yes
      --project-id "$PROJECT_ID"
      --location "$LOCATION"
"""


def _render_cloudbuild_ci(
    *,
    project_id: str,
    location: str,
    app_dir: str,
) -> str:
    return f"""# Cloud Build PR Gate for GCP Secure Source Manager (SSM)
substitutions:
  _PROJECT_ID: "{project_id}"
  _LOCATION: "{location}"
  _APP_DIR: "{app_dir}"
  _PR_NUMBER: "0"

availableSecrets:
  secretManager:
    - versionName: projects/$PROJECT_ID/secrets/ENV_DEV_JSON/versions/latest
      env: ENV_DEV_JSON

steps:
  - id: lint-and-ci-test
    name: python:3.11-slim
    entrypoint: bash
    secretEnv:
      - ENV_DEV_JSON
    env:
      - CI=true
    args:
      - -euo
      - pipefail
      - -c
      - |
        pip install --quiet cxas-scrapi
        if [ -n "${{ENV_DEV_JSON:-}}" ]; then
          mkdir -p envs
          printf '%s' "$ENV_DEV_JSON" > envs/env-dev.json
        fi
        cxas profile validate --all
        cxas lint --app-dir "${{_APP_DIR}}"
        cxas ci-test \\
          --app-dir "${{_APP_DIR}}" \\
          --project-id "${{_PROJECT_ID}}" \\
          --location "${{_LOCATION}}" \\
          --display-name "[CI] PR-${{_PR_NUMBER}}" \\
          --evals-dir evals/ \\
          --snapshot \\
          --runs 3 \\
          --min-pass-rate 0.9
"""


def _render_cloudbuild_release(
    *,
    project_id: str,
    location: str,
) -> str:
    return f"""# Cloud Build Tag Release & Promotion (GCP SSM)
substitutions:
  _PROJECT_ID: "{project_id}"
  _LOCATION: "{location}"
  _TARGET_ENV: "prod"
  _TAG_NAME: "v0.0.1"

availableSecrets:
  secretManager:
    - versionName: projects/$PROJECT_ID/secrets/ENV_PROD_JSON/versions/latest
      env: ENV_PROD_JSON

steps:
  - id: push-and-promote
    name: python:3.11-slim
    entrypoint: bash
    secretEnv:
      - ENV_PROD_JSON
    env:
      - CI=true
    args:
      - -euo
      - pipefail
      - -c
      - |
        pip install --quiet cxas-scrapi
        if [ -n "${{ENV_PROD_JSON:-}}" ]; then
          mkdir -p envs
          printf '%s' "$ENV_PROD_JSON" > "envs/env-${{_TARGET_ENV}}.json"
        fi
        cxas push \\
          --profile "${{_TARGET_ENV}}" \\
          --with-lock \\
          --create-version \\
          --version-name "${{_TAG_NAME}}" \\
          --fail-if-version-exists \\
          --on-drift=fail
        cxas deployments promote \\
          --profile "${{_TARGET_ENV}}" \\
          --version "${{_TAG_NAME}}"
"""


def _render_cloudbuild_cleanup(
    *,
    project_id: str,
    location: str,
) -> str:
    return f"""# Cloud Build Nightly Workspace Cleanup (GCP SSM)
substitutions:
  _PROJECT_ID: "{project_id}"
  _LOCATION: "{location}"

steps:
  - id: cleanup-workspaces
    name: python:3.11-slim
    entrypoint: bash
    env:
      - CI=true
    args:
      - -euo
      - pipefail
      - -c
      - |
        pip install --quiet cxas-scrapi
        cxas workspace cleanup \\
          --all-users \\
          --include-ci \\
          --yes \\
          --project-id "${{_PROJECT_ID}}" \\
          --location "${{_LOCATION}}"
"""


def _render_codeowners(app_dir: str) -> str:
    return f"""# CXAS GitOps CODEOWNERS
# Replace @org/cxas-platform-owners with your team handle(s).
{app_dir}/app.json                     @org/cxas-platform-owners
{app_dir}/global_instruction.txt       @org/cxas-platform-owners
.cxas/profiles/qa.yml                  @org/cxas-platform-owners
.cxas/profiles/prod.yml                @org/cxas-platform-owners
evals/contracts/                       @org/cxas-platform-owners
"""


def _render_release_pr_template() -> str:
    return """## CXAS Agent Release Checklist

- [ ] `cxas lint --app-dir src/agent` passes with zero errors
- [ ] `cxas profile validate --all` passes
- [ ] PR snapshot evals (`cxas ci-test --snapshot --runs 3`) pass gate
- [ ] No unintended out-of-band Console drift (`--on-drift=fail` verified)
- [ ] Target SemVer tag (`vX.Y.Z`) and rollback target version documented below

### Rollback Command

```bash
cxas rollback --profile prod --version <previous-tag> --deployment live
```
"""


def _write_if_needed(path: Path, content: str, *, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _ensure_gitignore(root: Path) -> Path:
    gitignore = root / ".gitignore"
    existing = (
        gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    )
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [e for e in _GITIGNORE_ENTRIES if e not in existing_lines]
    if missing:
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        gitignore.write_text(
            existing + prefix + "\n".join(missing) + "\n", encoding="utf-8"
        )
    return gitignore


def scaffold_gitops_repo(
    target_dir: str | Path = ".",
    *,
    provider: str = "github",
    project_id: str = "YOUR_GCP_PROJECT_ID",
    location: str = "us",
    agent_name: str = "cxas-agent",
    app_dir: str = "src/agent",
    workload_identity_provider: str = DEFAULT_WIP,
    service_account: str = DEFAULT_SA,
    force: bool = False,
) -> list[Path]:
    """Scaffold a CXAS GitOps repository layout under ``target_dir``."""
    norm_provider = provider.strip().lower()
    if norm_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported provider '{provider}'. "
            f"Expected one of: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    root = Path(target_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    slug = _slug(agent_name)
    written: list[Path] = []

    # 1. Core directories
    (root / app_dir).mkdir(parents=True, exist_ok=True)
    if _write_if_needed(root / app_dir / ".gitkeep", "", force=False):
        written.append(root / app_dir / ".gitkeep")

    (root / "evals" / "contracts").mkdir(parents=True, exist_ok=True)
    if _write_if_needed(
        root / "evals" / "contracts" / ".gitkeep", "", force=False
    ):
        written.append(root / "evals" / "contracts" / ".gitkeep")

    # 2. Profiles: dev (unprotected), qa & prod (protected: true)
    profiles_spec = {
        "dev": {
            "protected": False,
            "description": f"{agent_name} shared dev environment",
            "target": {
                "project_id": project_id,
                "location": location,
                "app_id": f"{slug}-dev",
            },
            "app_dir": app_dir,
            "env_file": "envs/env-dev.json",
        },
        "qa": {
            "protected": True,
            "description": f"{agent_name} protected QA environment",
            "target": {
                "project_id": project_id,
                "location": location,
                "app_id": f"{slug}-qa",
                "deployment_id": "live",
            },
            "app_dir": app_dir,
            "env_file": "envs/env-qa.json",
        },
        "prod": {
            "protected": True,
            "description": f"{agent_name} protected production environment",
            "target": {
                "project_id": project_id,
                "location": location,
                "app_id": f"{slug}-prod",
                "deployment_id": "live",
            },
            "app_dir": app_dir,
            "env_file": "envs/env-prod.json",
        },
    }
    for env_name, data in profiles_spec.items():
        p_path = root / ".cxas" / "profiles" / f"{env_name}.yml"
        if _write_if_needed(
            p_path, yaml.safe_dump(data, sort_keys=False), force=force
        ):
            written.append(p_path)

    # 3. Example env files
    example_env = (
        json.dumps({"app": {"variableDeclarations": []}}, indent=2) + "\n"
    )
    for env_name in ("dev", "qa", "prod"):
        e_path = root / "envs" / f"env-{env_name}.example.json"
        if _write_if_needed(e_path, example_env, force=force):
            written.append(e_path)

    # 4. CODEOWNERS & .gitignore (common to all providers)
    co_path = root / "CODEOWNERS"
    if _write_if_needed(co_path, _render_codeowners(app_dir), force=force):
        written.append(co_path)

    written.append(_ensure_gitignore(root))

    # 5. Provider-specific CI pipelines & release checklist template
    if norm_provider == "github":
        pr_tmpl = root / ".github" / "PULL_REQUEST_TEMPLATE" / "release.md"
        if _write_if_needed(
            pr_tmpl, _render_release_pr_template(), force=force
        ):
            written.append(pr_tmpl)

        workflows = {
            "cxas_ci.yml": _render_ci_workflow(
                project_id=project_id,
                location=location,
                app_dir=app_dir,
                wip=workload_identity_provider,
                sa=service_account,
            ),
            "cxas_release.yml": _render_release_workflow(
                wip=workload_identity_provider,
                sa=service_account,
            ),
            "cxas_cleanup.yml": _render_cleanup_workflow(
                project_id=project_id,
                location=location,
                wip=workload_identity_provider,
                sa=service_account,
            ),
        }
        for wf_name, content in workflows.items():
            wf_path = root / ".github" / "workflows" / wf_name
            if _write_if_needed(wf_path, content, force=force):
                written.append(wf_path)
    elif norm_provider == "gitlab":
        mr_tmpl = root / ".gitlab" / "merge_request_templates" / "release.md"
        if _write_if_needed(
            mr_tmpl, _render_release_pr_template(), force=force
        ):
            written.append(mr_tmpl)

        gl_path = root / ".gitlab-ci.yml"
        if _write_if_needed(
            gl_path,
            _render_gitlab_ci(
                project_id=project_id,
                location=location,
                app_dir=app_dir,
            ),
            force=force,
        ):
            written.append(gl_path)
    elif norm_provider == "ssm":
        checklist = root / "docs" / "release-checklist.md"
        if _write_if_needed(
            checklist, _render_release_pr_template(), force=force
        ):
            written.append(checklist)

        cb_files = {
            "cloudbuild-ci.yaml": _render_cloudbuild_ci(
                project_id=project_id,
                location=location,
                app_dir=app_dir,
            ),
            "cloudbuild-release.yaml": _render_cloudbuild_release(
                project_id=project_id,
                location=location,
            ),
            "cloudbuild-cleanup.yaml": _render_cloudbuild_cleanup(
                project_id=project_id,
                location=location,
            ),
        }
        for cb_name, content in cb_files.items():
            cb_path = root / cb_name
            if _write_if_needed(cb_path, content, force=force):
                written.append(cb_path)

    return written


def init_gitops(args: argparse.Namespace) -> None:
    """CLI handler for ``cxas init-gitops``."""
    provider = getattr(args, "provider", "github") or "github"
    written = scaffold_gitops_repo(
        target_dir=getattr(args, "target_dir", ".") or ".",
        provider=provider,
        project_id=getattr(args, "project_id", None) or "YOUR_GCP_PROJECT_ID",
        location=getattr(args, "location", None) or "us",
        agent_name=getattr(args, "agent_name", None) or "cxas-agent",
        app_dir=getattr(args, "app_dir", None) or "src/agent",
        workload_identity_provider=(
            getattr(args, "workload_identity_provider", None) or DEFAULT_WIP
        ),
        service_account=(getattr(args, "service_account", None) or DEFAULT_SA),
        force=bool(getattr(args, "force", False)),
    )
    print(
        f"Scaffolded CXAS GitOps repository [provider={provider}] "
        f"({len(written)} file(s) updated):"
    )
    for path in written:
        print(f"  - {path}")
    print("\nNext steps:")
    print(
        "  1. Review .cxas/profiles/{dev,qa,prod}.yml and envs/*.example.json"
    )
    print("  2. Run `cxas profile validate --all`")
    print(
        "  3. Spin up a personal sandbox with `cxas workspace up --profile dev`"
    )
