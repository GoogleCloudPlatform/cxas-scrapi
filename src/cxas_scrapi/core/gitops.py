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
import os
import re
import stat
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
SUPPORTED_MODELS = ("tags", "branches")

CONVENTIONAL_TITLE_REGEX = (
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([a-zA-Z0-9_./-]+\))?!?: .+"
)

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
  pr-title-lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: Lint PR title (Conventional Commits)
        env:
          PR_TITLE: ${{{{ github.event.pull_request.title }}}}
        run: |
          python3 - <<'PY'
          import os, re, sys
          pat = r"{CONVENTIONAL_TITLE_REGEX}"
          title = os.environ.get("PR_TITLE", "").strip()
          if not re.match(pat, title):
              print(f"ERROR: invalid PR title: {{title!r}}")
              print("Expected: <type>(<scope>): <description>")
              sys.exit(1)
          print(f"OK: PR title valid: {{title}}")
          PY

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


def _render_promote_workflow() -> str:
    return f"""name: CXAS Weekly Promotion (dev -> qa)

on:
  schedule:
    - cron: '0 9 * * 1'
  workflow_dispatch:
    inputs:
      source_branch:
        description: Source branch to promote from
        required: false
        default: dev
        type: string
      target_branch:
        description: Target branch to promote into
        required: false
        default: qa
        type: string

permissions:
  contents: read
  pull-requests: write

jobs:
  promote:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@{SHA_CHECKOUT} # v4.2.2
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Open promotion PR
        env:
          GH_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          SOURCE_BRANCH: ${{{{ inputs.source_branch || 'dev' }}}}
          TARGET_BRANCH: ${{{{ inputs.target_branch || 'qa' }}}}
        run: |
          git fetch origin "${{SOURCE_BRANCH}}" "${{TARGET_BRANCH}}"
          AHEAD=$(git rev-list --count \\
            "origin/${{TARGET_BRANCH}}..origin/${{SOURCE_BRANCH}}")
          if [ "${{AHEAD}}" -eq 0 ]; then
            echo "No commits to promote."
            exit 0
          fi
          EXISTING=$(gh pr list --state open \\
            --base "${{TARGET_BRANCH}}" --head "${{SOURCE_BRANCH}}" \\
            --json number --jq 'length')
          if [ "${{EXISTING}}" -gt 0 ]; then
            echo "Promotion PR already open."
            exit 0
          fi
          T="chore(release): promote ${{SOURCE_BRANCH}}->${{TARGET_BRANCH}}"
          gh pr create \\
            --base "${{TARGET_BRANCH}}" \\
            --head "${{SOURCE_BRANCH}}" \\
            --title "${{T}}" \\
            --body "Automated promotion PR (${{AHEAD}} commit(s))."
"""


def _render_gitlab_ci(
    *,
    project_id: str,
    location: str,
    app_dir: str,
    model: str = "tags",
) -> str:
    promote_job = ""
    if model == "branches":
        promote_job = """
promote-dev-to-qa:
  stage: release
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
  script:
    - bash scripts/promote.sh dev qa
"""
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

mr-title-lint:
  stage: lint
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  script:
    - |
      python3 - <<'PY'
      import os, re, sys
      pat = r"{CONVENTIONAL_TITLE_REGEX}"
      title = os.environ.get("CI_MERGE_REQUEST_TITLE", "").strip()
      if not re.match(pat, title):
          print(f"ERROR: invalid MR title: {{title!r}}")
          print("Expected: <type>(<scope>): <description>")
          sys.exit(1)
      print(f"OK: MR title valid: {{title}}")
      PY

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
{promote_job}
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
  _PR_TITLE: ""

availableSecrets:
  secretManager:
    - versionName: projects/$PROJECT_ID/secrets/ENV_DEV_JSON/versions/latest
      env: ENV_DEV_JSON

steps:
  - id: pr-title-lint
    name: python:3.11-slim
    entrypoint: python3
    env:
      - PR_TITLE=${{_PR_TITLE}}
    args:
      - -c
      - |
        import os, re, sys
        title = os.environ.get("PR_TITLE", "").strip()
        if not title:
            print("SKIP: _PR_TITLE is empty; skipping title lint.")
            sys.exit(0)
        pat = r"{CONVENTIONAL_TITLE_REGEX}"
        if not re.match(pat, title):
            print(f"ERROR: invalid PR title: {{title!r}}")
            print("Expected: <type>(<scope>): <description>")
            sys.exit(1)
        print(f"OK: PR title valid: {{title}}")

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
        if [ -n "$${{ENV_DEV_JSON:-}}" ]; then
          mkdir -p envs
          printf '%s' "$$ENV_DEV_JSON" > envs/env-dev.json
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
        if [ -n "$${{ENV_PROD_JSON:-}}" ]; then
          mkdir -p envs
          printf '%s' "$$ENV_PROD_JSON" > "envs/env-${{_TARGET_ENV}}.json"
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


def _render_cloudbuild_promote() -> str:
    return """# Cloud Build Weekly Branch Promotion (GCP SSM)
# Attach to a Cloud Scheduler or manual trigger for dev -> qa promotions.
substitutions:
  _SOURCE_BRANCH: "dev"
  _TARGET_BRANCH: "qa"

steps:
  - id: promote-branches
    name: gcr.io/cloud-builders/git
    entrypoint: bash
    env:
      - CI=true
    args:
      - -euo
      - pipefail
      - -c
      - |
        SRC="${_SOURCE_BRANCH}"
        DST="${_TARGET_BRANCH}"
        echo "Running branch promotion: $${SRC} -> $${DST}"
        bash scripts/promote.sh "$${SRC}" "$${DST}"
"""


def _render_bump_version_script() -> str:
    return '''#!/usr/bin/env python3
"""Compute the next SemVer tag from Conventional Commits since latest v* tag.

Usage:
    python3 scripts/bump-version.py          # print next vX.Y.Z tag
    python3 scripts/bump-version.py --apply  # create annotated git tag
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SEMVER_RE = re.compile(r"^v?(\\d+)\\.(\\d+)\\.(\\d+)$")
BREAKING_SUBJECT_RE = re.compile(r"^[a-z]+(\\([^)]+\\))?!:")
FEAT_SUBJECT_RE = re.compile(r"^feat(\\([^)]+\\))?:")


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _latest_tag(cwd: str) -> str | None:
    res = _git(["describe", "--tags", "--abbrev=0", "--match", "v*"], cwd)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _commits_since(tag: str | None, cwd: str) -> list[tuple[str, str]]:
    rev = f"{tag}..HEAD" if tag else "HEAD"
    sep_rec = "\\x1e"
    sep_fld = "\\x1f"
    fmt = f"%s{sep_fld}%b{sep_rec}"
    res = _git(["log", rev, f"--format={fmt}"], cwd)
    if res.returncode != 0:
        return []
    commits: list[tuple[str, str]] = []
    for raw in res.stdout.split(sep_rec):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(sep_fld, 1)
        subject = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        if subject:
            commits.append((subject, body))
    return commits


def compute_next_version(cwd: str = ".") -> str:
    """Return the next SemVer tag (``vX.Y.Z``) from git commit history."""
    tag = _latest_tag(cwd)
    if tag:
        m = SEMVER_RE.match(tag)
        if not m:
            raise ValueError(f"Latest tag {tag!r} is not valid SemVer (vX.Y.Z)")
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        major, minor, patch = 0, 0, 0

    commits = _commits_since(tag, cwd)
    bump = "patch"
    for subject, body in commits:
        if (
            BREAKING_SUBJECT_RE.match(subject)
            or "BREAKING CHANGE" in body
            or "BREAKING-CHANGE" in body
        ):
            bump = "major"
            break
        if FEAT_SUBJECT_RE.match(subject):
            bump = "minor"

    if bump == "major":
        return f"v{major + 1}.0.0"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute (and optionally apply) the next SemVer git tag."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create an annotated git tag for the computed version.",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to the git repository (default: current directory).",
    )
    args = parser.parse_args()

    next_tag = compute_next_version(args.repo)
    print(next_tag)

    if args.apply:
        res = _git(
            ["tag", "-a", next_tag, "-m", f"Release {next_tag}"],
            args.repo,
        )
        if res.returncode != 0:
            print(res.stderr.strip(), file=sys.stderr)
            return res.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _render_promote_script() -> str:
    return """#!/usr/bin/env bash
# Helper for branch-based promotions (dev -> qa or qa -> main).
# Usage:
#   bash scripts/promote.sh              # promotes dev -> qa
#   bash scripts/promote.sh dev qa       # promotes dev -> qa
#   bash scripts/promote.sh qa main      # promotes qa -> main
set -euo pipefail

SOURCE_BRANCH="${1:-dev}"
TARGET_BRANCH="${2:-qa}"

if [ "${CI:-false}" != "true" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
    exit 1
  fi
fi

echo "Fetching origin/${SOURCE_BRANCH} and origin/${TARGET_BRANCH}..."
git fetch origin "${SOURCE_BRANCH}" "${TARGET_BRANCH}"

if ! git rev-parse --verify "origin/${SOURCE_BRANCH}" >/dev/null 2>&1; then
  echo "ERROR: Remote branch origin/${SOURCE_BRANCH} not found." >&2
  exit 1
fi
if ! git rev-parse --verify "origin/${TARGET_BRANCH}" >/dev/null 2>&1; then
  echo "ERROR: Remote branch origin/${TARGET_BRANCH} not found." >&2
  exit 1
fi

RANGE="origin/${TARGET_BRANCH}..origin/${SOURCE_BRANCH}"
AHEAD="$(git rev-list --count "${RANGE}")"
if [ "${AHEAD}" -eq 0 ]; then
  echo "Nothing to promote: 0 commits ahead (${RANGE})."
  exit 0
fi

if command -v cxas >/dev/null 2>&1; then
  cxas profile validate --all
fi

TITLE="chore(release): promote ${SOURCE_BRANCH} to ${TARGET_BRANCH}"
BODY="Promotion (${AHEAD} commit(s): ${SOURCE_BRANCH} -> ${TARGET_BRANCH})."

if command -v gh >/dev/null 2>&1; then
  gh pr create \\
    --base "${TARGET_BRANCH}" \\
    --head "${SOURCE_BRANCH}" \\
    --title "${TITLE}" \\
    --body "${BODY}"
elif command -v glab >/dev/null 2>&1; then
  glab mr create \\
    --source-branch "${SOURCE_BRANCH}" \\
    --target-branch "${TARGET_BRANCH}" \\
    --title "${TITLE}" \\
    --description "${BODY}"
else
  echo "Ready: ${AHEAD} commit(s) (${SOURCE_BRANCH}->${TARGET_BRANCH})."
  echo "Open a PR/MR in your Git host with title: ${TITLE}"
fi
"""


def _render_contracts_readme() -> str:
    return """# Sub-Agent Interface Contracts

Document each non-root sub-agent's public contract in a Markdown file named
`<agent_name>.md` in this directory (for example, `billing_agent.md` for
`agents/billing_agent/`).

## Creating a New Contract

1. Copy `_template.md` to `<agent_name>.md`.
2. Fill in entry conditions, session variables read/written, tools used,
   handoff/exit criteria, and linked evaluation scenarios.
3. Keep the contract in sync with PR changes to the sub-agent.

## Optional CI Enforcement (`cxas lint`)

To enforce that every non-root sub-agent has a matching contract doc, uncomment
`require_agent_contracts: true` in `cxaslint.yaml`:

```yaml
require_agent_contracts: true
contracts_dir: docs/contracts
```
"""


def _render_contracts_template() -> str:
    return """# Sub-Agent Contract: `<agent_name>`

- **Owner:** `@org/team-name`
- **Agent Directory:** `src/agent/agents/<agent_name>/`
- **Last Updated:** `YYYY-MM-DD`

## Purpose

Briefly describe the single responsibility of this sub-agent.

## Entry Conditions

- When the root agent or another sub-agent transfers here.
- Required preconditions (e.g., authenticated user, populated session vars).

## Session Variables

| Variable | Access (`read` / `write`) | Type | Description |
|----------|---------------------------|------|-------------|
| `user_id` | `read` | `STRING` | Authenticated user identifier |

## Tools & External Dependencies

- `tool_name` — brief description of side effects / failure handling.

## Handoff & Exit Criteria

- Conditions under which control returns to the root agent or transfers to
  another sub-agent.

## Evaluation Coverage

- `evals/contracts/<agent_name>_happy_path.yaml`
"""


def _render_cxaslint_yaml(app_dir: str) -> str:
    return f"""# CXAS Linter Configuration (cxas lint)
# See docs/guides/linting/configuration.md for all options.
app_dir: {app_dir}
evals_dir: evals/

# --- Opt-in GitOps governance rules (disabled by default) ---
# Require docs/contracts/<agent_name>.md for every non-root sub-agent (S014):
# require_agent_contracts: true
# contracts_dir: docs/contracts

# Enforce allowed prefixes on app.json variableDeclarations (V105):
# allowed_variable_prefixes:
#   - user_
#   - session_
#   - flow_
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


def _write_if_needed(
    path: Path, content: str, *, force: bool, executable: bool = False
) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        mode = path.stat().st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
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
    model: str = "tags",
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

    norm_model = model.strip().lower()
    if norm_model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported model '{model}'. "
            f"Expected one of: {', '.join(SUPPORTED_MODELS)}."
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

    # 4. CODEOWNERS, .gitignore, contracts docs, cxaslint.yaml & helper scripts
    co_path = root / "CODEOWNERS"
    if _write_if_needed(co_path, _render_codeowners(app_dir), force=force):
        written.append(co_path)

    written.append(_ensure_gitignore(root))

    contracts_readme = root / "docs" / "contracts" / "README.md"
    if _write_if_needed(
        contracts_readme, _render_contracts_readme(), force=force
    ):
        written.append(contracts_readme)

    contracts_tmpl = root / "docs" / "contracts" / "_template.md"
    if _write_if_needed(
        contracts_tmpl, _render_contracts_template(), force=force
    ):
        written.append(contracts_tmpl)

    lint_cfg = root / "cxaslint.yaml"
    if _write_if_needed(lint_cfg, _render_cxaslint_yaml(app_dir), force=force):
        written.append(lint_cfg)

    bump_script = root / "scripts" / "bump-version.py"
    if _write_if_needed(
        bump_script,
        _render_bump_version_script(),
        force=force,
        executable=True,
    ):
        written.append(bump_script)

    if norm_model == "branches":
        promote_script = root / "scripts" / "promote.sh"
        if _write_if_needed(
            promote_script,
            _render_promote_script(),
            force=force,
            executable=True,
        ):
            written.append(promote_script)

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
        if norm_model == "branches":
            workflows["cxas_promote.yml"] = _render_promote_workflow()
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
                model=norm_model,
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
        if norm_model == "branches":
            cb_files["cloudbuild-promote.yaml"] = _render_cloudbuild_promote()
        for cb_name, content in cb_files.items():
            cb_path = root / cb_name
            if _write_if_needed(cb_path, content, force=force):
                written.append(cb_path)

    return written


def init_gitops(args: argparse.Namespace) -> None:
    """CLI handler for ``cxas init-gitops``."""
    provider = getattr(args, "provider", "github") or "github"
    model = getattr(args, "model", "tags") or "tags"
    written = scaffold_gitops_repo(
        target_dir=getattr(args, "target_dir", ".") or ".",
        provider=provider,
        model=model,
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
        f"Scaffolded CXAS GitOps repository "
        f"[provider={provider}, model={model}] "
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
