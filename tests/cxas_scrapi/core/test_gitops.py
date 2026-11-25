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

"""Unit tests for cxas_scrapi.core.gitops and core.github app.json support."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from cxas_scrapi.core.github import init_github_action
from cxas_scrapi.core.gitops import scaffold_gitops_repo
from cxas_scrapi.utils.profiles import load_profile, validate_profile

if TYPE_CHECKING:
    from pathlib import Path

_SHA_RE = re.compile(r"@[0-9a-f]{40}$")


def test_scaffold_gitops_repo_creates_valid_profiles_and_pinned_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = scaffold_gitops_repo(
        tmp_path,
        project_id="my-gcp-proj",
        location="europe-west2",
        agent_name="Support Bot",
    )
    assert written

    # Core directories & files
    assert (tmp_path / "src" / "agent").is_dir()
    assert (tmp_path / "evals" / "contracts").is_dir()
    assert (tmp_path / "CODEOWNERS").is_file()
    assert (
        tmp_path / ".github" / "PULL_REQUEST_TEMPLATE" / "release.md"
    ).is_file()
    for env in ("dev", "qa", "prod"):
        assert (tmp_path / "envs" / f"env-{env}.example.json").is_file()
        assert (tmp_path / ".cxas" / "profiles" / f"{env}.yml").is_file()

    # Validate all 3 profiles
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    for env, expected_protected in (
        ("dev", False),
        ("qa", True),
        ("prod", True),
    ):
        prof, errors, _ = validate_profile(env)
        assert errors == []
        assert prof is not None
        assert prof.protected is expected_protected
        loaded = load_profile(env)
        assert loaded is not None
        assert loaded.target.project_id == "my-gcp-proj"
        assert loaded.target.location == "europe-west2"

    # Validate all 3 GitHub Actions workflows parse cleanly and follow #573
    for wf_name in ("cxas_ci.yml", "cxas_release.yml", "cxas_cleanup.yml"):
        wf_path = tmp_path / ".github" / "workflows" / wf_name
        assert wf_path.is_file()
        data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "permissions" in data
        assert data["permissions"]["contents"] == "read"
        jobs: dict[str, Any] = data["jobs"]
        for job in jobs.values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses:
                    assert _SHA_RE.search(uses), (
                        f"{wf_name}: action '{uses}' is not SHA-pinned"
                    )
                    if uses.startswith("actions/checkout@"):
                        assert (
                            step.get("with", {}).get("persist-credentials")
                            is False
                        )

    # .gitignore entries are present and idempotent on second run
    gi_before = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "*.local.*" in gi_before
    assert "envs/*.json" in gi_before
    scaffold_gitops_repo(
        tmp_path, project_id="my-gcp-proj", location="europe-west2"
    )
    gi_after = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gi_after == gi_before


def test_init_github_action_reads_app_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify core/github.py infers displayName and name from app.json."""
    app_dir = tmp_path / "my_agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": "projects/json-proj/locations/us/apps/json-app-id",
                "displayName": "JsonSupportBot",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        app_dir=str(app_dir),
        app_name=None,
        agent_name=None,
        project_id=None,
        location=None,
        workload_identity_provider="projects/123/locations/global/workloadIdentityPools/p/providers/g",
        service_account="sa@json-proj.iam.gserviceaccount.com",
        output=str(tmp_path / ".github" / "workflows" / "test_out.yml"),
        branch="main",
        no_cleanup=True,
        install_hook=False,
        auto_create_wif=False,
        github_repo=None,
    )
    init_github_action(args)

    wf = (tmp_path / ".github" / "workflows" / "test_out.yml").read_text(
        encoding="utf-8"
    )
    assert "json-proj" in wf
    assert "Jsonsupportbot" in wf


def test_scaffold_gitops_repo_gitlab_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify --provider gitlab generates valid .gitlab-ci.yml and MR template."""
    scaffold_gitops_repo(
        tmp_path,
        provider="gitlab",
        project_id="gl-proj",
        location="europe-west2",
        agent_name="Billing Bot",
    )

    assert (tmp_path / ".gitlab-ci.yml").is_file()
    assert (
        tmp_path / ".gitlab" / "merge_request_templates" / "release.md"
    ).is_file()
    assert (tmp_path / "CODEOWNERS").is_file()
    assert not (tmp_path / ".github" / "workflows").exists()

    gl_data = yaml.safe_load(
        (tmp_path / ".gitlab-ci.yml").read_text(encoding="utf-8")
    )
    assert gl_data["stages"] == ["lint", "test", "release", "cleanup"]
    assert "lint_and_validate" in gl_data
    assert "pr_snapshot_evals" in gl_data
    assert "release_qa" in gl_data
    assert "release_prod" in gl_data
    assert "nightly_workspace_cleanup" in gl_data
    raw_gl = (tmp_path / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "ENV_DEV_JSON" in raw_gl
    assert "ENV_QA_JSON" in raw_gl
    assert "ENV_PROD_JSON" in raw_gl
    assert "--on-drift=fail" in raw_gl
    assert "--fail-if-version-exists" in raw_gl

    # Profiles validate cleanly
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    for env in ("dev", "qa", "prod"):
        _, errors, _ = validate_profile(env)
        assert errors == []

    # No-overwrite behavior when force=False
    gl_path = tmp_path / ".gitlab-ci.yml"
    gl_path.write_text("# custom\n", encoding="utf-8")
    scaffold_gitops_repo(tmp_path, provider="gitlab", force=False)
    assert gl_path.read_text(encoding="utf-8") == "# custom\n"


def test_scaffold_gitops_repo_ssm_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify --provider ssm generates valid Cloud Build YAML + release checklist."""
    scaffold_gitops_repo(
        tmp_path,
        provider="ssm",
        project_id="ssm-proj",
        location="us",
        agent_name="Support Bot",
    )

    assert (tmp_path / "docs" / "release-checklist.md").is_file()
    assert (tmp_path / "CODEOWNERS").is_file()
    assert not (tmp_path / ".github" / "workflows").exists()
    assert not (tmp_path / ".gitlab-ci.yml").exists()

    for fname in (
        "cloudbuild-ci.yaml",
        "cloudbuild-release.yaml",
        "cloudbuild-cleanup.yaml",
    ):
        p = tmp_path / fname
        assert p.is_file()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "steps" in data
        assert "substitutions" in data

    ci_raw = (tmp_path / "cloudbuild-ci.yaml").read_text(encoding="utf-8")
    ci_data = yaml.safe_load(ci_raw)
    assert "availableSecrets" in ci_data
    assert (
        ci_data["availableSecrets"]["secretManager"][0]["env"] == "ENV_DEV_JSON"
    )
    assert "$$ENV_DEV_JSON" in ci_raw
    assert "$${ENV_DEV_JSON:-}" in ci_raw

    rel_raw = (tmp_path / "cloudbuild-release.yaml").read_text(encoding="utf-8")
    rel_data = yaml.safe_load(rel_raw)
    assert (
        rel_data["availableSecrets"]["secretManager"][0]["env"]
        == "ENV_PROD_JSON"
    )
    assert "$$ENV_PROD_JSON" in rel_raw
    assert "$${ENV_PROD_JSON:-}" in rel_raw
    assert "--with-lock" in rel_raw
    assert "--on-drift=fail" in rel_raw
    assert "--fail-if-version-exists" in rel_raw

    # Profiles validate cleanly
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    for env in ("dev", "qa", "prod"):
        _, errors, _ = validate_profile(env)
        assert errors == []


def test_scaffold_gitops_repo_rejects_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        scaffold_gitops_repo(tmp_path, provider="bitbucket")


def test_scaffold_gitops_repo_rejects_unknown_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported model"):
        scaffold_gitops_repo(tmp_path, model="gitflow")


def test_scaffold_gitops_repo_day1_extras_default_tags_model(
    tmp_path: Path,
) -> None:
    """Verify PR title lint, bump-version.py, contracts docs, and cxaslint.yaml."""
    scaffold_gitops_repo(
        tmp_path,
        provider="github",
        model="tags",
        project_id="my-proj",
        location="us",
    )

    # Contracts starter & commented cxaslint.yaml
    assert (tmp_path / "docs" / "contracts" / "README.md").is_file()
    assert (tmp_path / "docs" / "contracts" / "_template.md").is_file()
    lint_cfg_path = tmp_path / "cxaslint.yaml"
    assert lint_cfg_path.is_file()
    lint_data = yaml.safe_load(lint_cfg_path.read_text(encoding="utf-8"))
    assert lint_data["app_dir"] == "src/agent"
    assert not lint_data.get("require_agent_contracts")
    assert not lint_data.get("allowed_variable_prefixes")

    # bump-version.py is executable; promote.sh / cxas_promote.yml absent in tags
    bump = tmp_path / "scripts" / "bump-version.py"
    assert bump.is_file()
    assert os.access(bump, os.X_OK)
    assert not (tmp_path / "scripts" / "promote.sh").exists()
    assert not (
        tmp_path / ".github" / "workflows" / "cxas_promote.yml"
    ).exists()

    # PR title lint job present in cxas_ci.yml
    ci_data = yaml.safe_load(
        (tmp_path / ".github" / "workflows" / "cxas_ci.yml").read_text(
            encoding="utf-8"
        )
    )
    assert "pr-title-lint" in ci_data["jobs"]
    assert ci_data["jobs"]["pr-title-lint"]["permissions"] == {
        "contents": "read"
    }


def test_scaffold_gitops_repo_branches_model_all_providers(
    tmp_path: Path,
) -> None:
    """Verify --model branches scaffolds promote.sh + weekly workflow per provider."""
    # 1. GitHub
    gh_dir = tmp_path / "gh"
    scaffold_gitops_repo(gh_dir, provider="github", model="branches")
    promote_sh = gh_dir / "scripts" / "promote.sh"
    assert promote_sh.is_file()
    assert os.access(promote_sh, os.X_OK)
    syntax = subprocess.run(
        ["bash", "-n", str(promote_sh)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    wf_path = gh_dir / ".github" / "workflows" / "cxas_promote.yml"
    assert wf_path.is_file()
    wf_data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    assert wf_data["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
    }
    on_block = wf_data.get("on", wf_data.get(True))
    assert on_block["schedule"] == [{"cron": "0 9 * * 1"}]
    for step in wf_data["jobs"]["promote"]["steps"]:
        uses = step.get("uses")
        if uses:
            assert _SHA_RE.search(uses), f"Action not SHA-pinned: {uses}"
            if uses.startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False
    assert "gh pr create" in wf_path.read_text(encoding="utf-8")

    # 2. GitLab
    gl_dir = tmp_path / "gl"
    scaffold_gitops_repo(gl_dir, provider="gitlab", model="branches")
    assert (gl_dir / "scripts" / "promote.sh").is_file()
    gl_data = yaml.safe_load(
        (gl_dir / ".gitlab-ci.yml").read_text(encoding="utf-8")
    )
    assert "mr-title-lint" in gl_data
    assert "promote-dev-to-qa" in gl_data
    assert gl_data["promote-dev-to-qa"]["stage"] == "release"

    # 3. SSM / Cloud Build
    ssm_dir = tmp_path / "ssm"
    scaffold_gitops_repo(ssm_dir, provider="ssm", model="branches")
    assert (ssm_dir / "scripts" / "promote.sh").is_file()
    cb_ci = yaml.safe_load(
        (ssm_dir / "cloudbuild-ci.yaml").read_text(encoding="utf-8")
    )
    assert "_PR_TITLE" in cb_ci["substitutions"]
    assert any(s.get("id") == "pr-title-lint" for s in cb_ci["steps"])

    cb_promote_path = ssm_dir / "cloudbuild-promote.yaml"
    assert cb_promote_path.is_file()
    cb_promote_raw = cb_promote_path.read_text(encoding="utf-8")
    cb_promote = yaml.safe_load(cb_promote_raw)
    assert cb_promote["substitutions"]["_SOURCE_BRANCH"] == "dev"
    assert cb_promote["substitutions"]["_TARGET_BRANCH"] == "qa"
    assert "$${SRC}" in cb_promote_raw
    assert "$${DST}" in cb_promote_raw


def test_bump_version_script_patch_minor_major_and_apply(
    tmp_path: Path,
) -> None:
    """Run generated scripts/bump-version.py against a real temp git repo."""
    scaffold_gitops_repo(tmp_path, provider="github")
    script = tmp_path / "scripts" / "bump-version.py"

    def _git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout.strip()

    _git("init")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test User")
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    _git("add", "README.md")
    _git("commit", "-m", "fix: initial setup")

    # Patch bump from 0.0.0 -> v0.0.1 and apply tag
    out = subprocess.run(
        [sys.executable, str(script), "--apply"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "v0.0.1"
    assert _git("tag", "-l", "v0.0.1") == "v0.0.1"

    # Minor bump on feat:
    (tmp_path / "README.md").write_text("feat\n", encoding="utf-8")
    _git("add", "README.md")
    _git("commit", "-m", "feat(billing): add refund flow")
    out = subprocess.run(
        [sys.executable, str(script), "--apply"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "v0.1.0"
    assert _git("tag", "-l", "v0.1.0") == "v0.1.0"

    # Major bump on feat!:
    (tmp_path / "README.md").write_text("breaking\n", encoding="utf-8")
    _git("add", "README.md")
    _git("commit", "-m", "feat!: remove legacy v1 handoff")
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "v1.0.0"
