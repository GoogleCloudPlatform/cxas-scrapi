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

"""Tests for the main CLI entry point."""

import argparse
import subprocess
import sys
from unittest import mock

import pytest

from cxas_scrapi.cli import main as main_cli
from cxas_scrapi.cli.main import get_parser


def test_get_parser():
    """Test that the parser can be initialized and parses help correctly."""
    parser = get_parser()
    assert parser is not None

    # Test parsing a simple command to verify the parser structure
    args = parser.parse_args(
        ["apps", "list", "--project-id", "test-project", "--location", "us"]
    )
    assert args.command == "apps"
    assert args.project_id == "test-project"
    assert args.location == "us"


def test_cli_installed_help():
    """Test that the 'cxas' command is installed and executable (verifies
    setup.py)."""
    # This tests the installation of the wheel we just built and installed.
    # When running tests via 'conda run -n cxas-scrapi pytest', 'cxas'
    # should be in the PATH.
    try:
        py_code = (
            "import sys; "
            "sys.argv[0]='cxas'; "
            "from cxas_scrapi.cli.main import main; "
            "main()"
        )
        result = subprocess.run(
            [sys.executable, "-c", py_code, "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.returncode == 0
        assert "usage: cxas" in result.stdout
    except FileNotFoundError:
        pytest.fail(
            "The 'cxas' command was not found in the environment. "
            "Is it installed?"
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"'cxas --help' failed with return code {e.returncode}. "
            f"Output: {e.output}"
        )


@mock.patch("cxas_scrapi.cli.main.Apps", autospec=True)
@mock.patch("cxas_scrapi.cli.main.ConversationHistory", autospec=True)
def test_conversations_list(mock_ch_cls, mock_apps_cls):
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_apps_inst = mock_apps_cls.return_value
    mock_apps_inst.creds = mock.MagicMock()

    mock_ch_inst = mock_ch_cls.return_value
    mock_ch_inst.list_conversations.return_value = []

    main_cli.conversations_list(args)

    mock_apps_cls.assert_called_once_with(
        project_id="test-project", location="global"
    )
    mock_ch_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app",
        creds=mock_apps_inst.creds,
    )
    mock_ch_inst.list_conversations.assert_called_once()


def test_conversations_list_invalid_app_name(capsys):
    args = argparse.Namespace(app_name="malformed-app-name")
    with pytest.raises(SystemExit) as excinfo:
        main_cli.conversations_list(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Invalid App Name format" in captured.out


@mock.patch("cxas_scrapi.cli.main.Apps", autospec=True)
@mock.patch("cxas_scrapi.cli.main.ConversationHistory", autospec=True)
def test_conversations_get(mock_ch_cls, mock_apps_cls):
    args = argparse.Namespace(
        conversation_resource_name="projects/test-project/locations/global/apps/test-app/conversations/test-conv"
    )
    mock_apps_inst = mock_apps_cls.return_value
    mock_apps_inst.creds = mock.MagicMock()

    mock_ch_inst = mock_ch_cls.return_value
    mock_ch_inst.get_conversation.return_value = mock.MagicMock()

    main_cli.conversations_get(args)

    mock_apps_cls.assert_called_once_with(
        project_id="test-project", location="global"
    )
    mock_ch_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app",
        creds=mock_apps_inst.creds,
    )
    mock_ch_inst.get_conversation.assert_called_once_with(
        conversation_id=(
            "projects/test-project/locations/global/apps/test-app/"
            "conversations/test-conv"
        )
    )


def test_conversations_get_invalid_conversation_name(capsys):
    args = argparse.Namespace(conversation_resource_name="malformed-conv-name")
    with pytest.raises(SystemExit) as excinfo:
        main_cli.conversations_get(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Invalid Conversation Resource Name format" in captured.out


@mock.patch("cxas_scrapi.cli.main.Deployments", autospec=True)
def test_deployments_list(mock_deps_cls):
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst = mock_deps_cls.return_value
    mock_deps_inst.list_deployments.return_value = []

    main_cli.deployments_list(args)

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.list_deployments.assert_called_once()


@mock.patch("cxas_scrapi.cli.main.Deployments", autospec=True)
def test_deployments_create(mock_deps_cls):
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        deployment_id="test-dep",
        version_id="projects/test-project/locations/global/apps/test-app/versions/v1",
    )
    mock_deps_inst = mock_deps_cls.return_value

    main_cli.deployments_create(args)

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.create_deployment.assert_called_once_with(
        deployment_id="test-dep",
        display_name="test-dep",
        app_version="projects/test-project/locations/global/apps/test-app/versions/v1",
    )


@mock.patch("cxas_scrapi.cli.main.Deployments", autospec=True)
@mock.patch("cxas_scrapi.cli.main.app_push", autospec=True)
def test_deployments_promote(mock_app_push, mock_deps_cls):
    args = argparse.Namespace(
        app_resource_name="projects/test-project/locations/global/apps/test-app",
        app_dir="/dummy/path",
        live_deployment_resource_name="projects/test-project/locations/global/apps/test-app/deployments/live-dep",
    )

    def push_side_effect(push_args):
        push_args.created_version_name = (
            "projects/test-project/locations/global/apps/test-app/versions/v1"
        )
        return "projects/test-project/locations/global/apps/test-app"

    mock_app_push.side_effect = push_side_effect

    mock_deps_inst = mock_deps_cls.return_value
    mock_deps_inst.get_deployment.return_value = mock.MagicMock()

    main_cli.deployments_promote(args)

    mock_app_push.assert_called_once()
    called_args = mock_app_push.call_args[0][0]
    expected_app = "projects/test-project/locations/global/apps/test-app"
    assert called_args.to == expected_app
    assert called_args.app_dir == "/dummy/path"
    assert called_args.create_version is True

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.get_deployment.assert_called_once_with(
        deployment_id="live-dep"
    )
    mock_deps_inst.update_deployment.assert_called_once_with(
        deployment_id="live-dep",
        app_version=(
            "projects/test-project/locations/global/apps/test-app/versions/v1"
        ),
    )
