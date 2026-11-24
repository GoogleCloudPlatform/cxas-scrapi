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
import re
from typing import TYPE_CHECKING, Any

import yaml

from cxas_scrapi.core.github import init_github_action
from cxas_scrapi.core.gitops import scaffold_gitops_repo
from cxas_scrapi.utils.profiles import load_profile, validate_profile

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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
