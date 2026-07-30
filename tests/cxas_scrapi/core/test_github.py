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

import argparse
import subprocess
import typing
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.github import (
    _auto_setup_wif,
    _get_github_details,
    _repo_relative_path,
    init_github_action,
)


def _init_github_action_args(app_dir: str) -> argparse.Namespace:
    return argparse.Namespace(
        agent_name="testagent",
        app_id="projects/my-project/locations/us/apps/test-app",
        app_name="projects/my-project/locations/us/apps/test-app",
        app_dir=app_dir,
        output=None,
        auth_method="wif",
        workload_identity_provider=(
            "projects/123/locations/global/workloadIdentityPools/pool/"
            "providers/provider"
        ),
        service_account="github-actions-sa@my-project.iam.gserviceaccount.com",
        project_id="my-project",
        location="us",
        branch="main",
        no_cleanup=False,
        install_hook=False,
        auto_create_wif=False,
        github_repo="owner/repo",
    )


def _init_git_repo(path: typing.Any) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_get_github_details_https() -> None:
    with patch("subprocess.check_output") as mock_run:
        mock_run.return_value = "https://github.com/owner/repo.git\n"
        owner, repo = _get_github_details("/tmp")
        assert owner == "owner"
        assert repo == "repo"


def test_get_github_details_ssh() -> None:
    with patch("subprocess.check_output") as mock_run:
        mock_run.return_value = "git@github.com:owner/repo.git\n"
        owner, repo = _get_github_details("/tmp")
        assert owner == "owner"
        assert repo == "repo"


def test_get_github_details_fail() -> None:
    with patch("subprocess.check_output") as mock_run:
        mock_run.side_effect = Exception("error")
        owner, repo = _get_github_details("/tmp")
        assert owner is None
        assert repo is None


def test_repo_relative_path_preserves_nested_app_dir(
    tmp_path: typing.Any,
) -> None:
    repo_root = tmp_path / "repo"
    app_dir = repo_root / "customer-service-agent/cxas_app/App"
    app_dir.mkdir(parents=True)

    assert (
        _repo_relative_path(str(app_dir), str(repo_root))
        == "customer-service-agent/cxas_app/App"
    )


def test_repo_relative_path_rejects_paths_outside_repo(
    tmp_path: typing.Any,
) -> None:
    repo_root = tmp_path / "repo"
    app_dir = tmp_path / "app"
    repo_root.mkdir()
    app_dir.mkdir()

    with pytest.raises(ValueError, match="inside the Git repository"):
        _repo_relative_path(str(app_dir), str(repo_root))


def test_auto_setup_wif_success() -> None:
    with (
        patch("subprocess.check_output") as mock_output,
        patch("subprocess.run") as mock_run,
        patch("subprocess.check_call") as mock_call,
    ):
        mock_output.return_value = "123456789\n"
        mock_run.return_value.returncode = 1  # describe fails, force creation

        wip, sa = _auto_setup_wif("my-project", "my-owner", "my-repo")

        assert (
            wip
            == "projects/123456789/locations/global/workloadIdentityPools/github-actions-pool-scrapi/providers/github-provider"  # noqa: E501
        )
        assert sa == "github-actions-sa@my-project.iam.gserviceaccount.com"

        assert mock_call.call_count >= 3


def test_init_github_action_auto_create() -> None:
    args = argparse.Namespace(
        agent_name="testagent",
        app_id="projects/p/locations/l/apps/a",
        app_name="testapp",
        app_dir=".",
        output=None,
        auth_method="wif",
        workload_identity_provider=None,
        service_account=None,
        project_id="my-project",
        location="us",
        branch="main",
        no_cleanup=False,
        install_hook=False,
        auto_create_wif=True,
        github_repo="owner/repo",
    )

    with (
        patch("cxas_scrapi.core.github._auto_setup_wif") as mock_setup,
        patch("builtins.open", MagicMock()),
        patch("os.chmod"),
    ):
        mock_setup.return_value = ("mock-wip", "mock-sa")
        init_github_action(args)

        assert args.workload_identity_provider == "mock-wip"
        assert args.service_account == "mock-sa"


def test_init_github_action_missing_wif() -> None:
    args = argparse.Namespace(
        agent_name="testagent",
        app_id="projects/p/locations/l/apps/a",
        app_name="testapp",
        app_dir=".",
        output=None,
        auth_method="wif",
        workload_identity_provider=None,
        service_account=None,  # Missing SA
        project_id="my-project",
        location="us",
        branch="main",
        no_cleanup=False,
        install_hook=False,
        auto_create_wif=False,  # Missing auto_create
        github_repo="owner/repo",
    )

    with pytest.raises(
        ValueError,
        match=(
            "Either provide --workload_identity_provider and "
            "--service_account, or use --auto-create-wif"
        ),
    ):
        init_github_action(args)


def test_init_github_action_preserves_nested_app_dir(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    repo_root = tmp_path / "repo"
    app_dir = (
        repo_root / "customer-service-agent/cxas_app/Customer_Service_Agent"
    )
    app_dir.mkdir(parents=True)
    _init_git_repo(repo_root)
    monkeypatch.chdir(repo_root)

    args = _init_github_action_args(
        "customer-service-agent/cxas_app/Customer_Service_Agent"
    )
    init_github_action(args)

    workflow_path = repo_root / ".github/workflows/ci_test_testagent.yml"
    deploy_path = repo_root / ".github/workflows/deploy_testagent.yml"
    cleanup_path = repo_root / ".github/workflows/cleanup_testagent.yml"

    ci_workflow = workflow_path.read_text()
    deploy_workflow = deploy_path.read_text()
    cleanup_workflow = cleanup_path.read_text()
    workflows = "\n".join([ci_workflow, deploy_workflow, cleanup_workflow])

    expected_app_dir = "customer-service-agent/cxas_app/Customer_Service_Agent"

    assert f"context: {expected_app_dir}" in ci_workflow
    assert f"ci-test --app-dir {expected_app_dir}" in ci_workflow
    assert f"cxas push --app-dir {expected_app_dir}" in deploy_workflow
    assert f"'{expected_app_dir}/**/*.py'" in workflows
    assert f"'{expected_app_dir}/**/*.txt'" in workflows
    assert "--project_id" not in workflows
    assert "--app_id" not in workflows
    assert "--display_name" not in workflows

    assert (app_dir / "Dockerfile").exists()
    assert (app_dir / "requirements.txt").exists()
