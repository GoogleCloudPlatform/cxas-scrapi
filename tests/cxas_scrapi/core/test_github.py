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
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.github import (
    _auto_setup_wif,
    _get_github_details,
    init_github_action,
)


def test_get_github_details_https():
    with patch("subprocess.check_output") as mock_run:
        mock_run.return_value = "https://github.com/owner/repo.git\n"
        owner, repo = _get_github_details("/tmp")
        assert owner == "owner"
        assert repo == "repo"


def test_get_github_details_ssh():
    with patch("subprocess.check_output") as mock_run:
        mock_run.return_value = "git@github.com:owner/repo.git\n"
        owner, repo = _get_github_details("/tmp")
        assert owner == "owner"
        assert repo == "repo"


def test_get_github_details_fail():
    with patch("subprocess.check_output") as mock_run:
        mock_run.side_effect = Exception("error")
        owner, repo = _get_github_details("/tmp")
        assert owner is None
        assert repo is None


def test_auto_setup_wif_success():
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


def test_init_github_action_auto_create():
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


def test_init_github_action_missing_wif():
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
