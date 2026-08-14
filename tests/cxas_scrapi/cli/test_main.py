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
import typing
from unittest import mock

import pytest

from cxas_scrapi.cli import main as main_cli
from cxas_scrapi.cli.main import get_parser, run_session


def test_get_parser() -> None:
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


def test_get_parser_llm_lint() -> None:
    """Test that the parser can parse the llm-lint command."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "llm-lint",
            "--agent-dir",
            "/path/to/agent",
            "--project-id",
            "test-project",
            "--location",
            "us-central1",
            "--model",
            "gemini-2.5-flash",
            "--output",
            "/path/to/output.md",
        ]
    )
    assert args.command == "llm-lint"
    assert args.agent_dir == "/path/to/agent"
    assert args.project_id == "test-project"
    assert args.location == "us-central1"
    assert args.model == "gemini-2.5-flash"
    assert args.output == "/path/to/output.md"


def test_get_parser_evals_report() -> None:
    """Test that the parser can parse the evals report command with new model
    flags.
    """
    parser = get_parser()
    args = parser.parse_args(
        [
            "evals",
            "report",
            "--output-dir",
            "/path/to/output",
            "--sim-user-model",
            "gemini-3.1-pro-preview",
            "--eval-model",
            "gemini-3.1-flash-lite",
            "--run",
        ]
    )
    assert args.command == "evals"
    assert args.evals_command == "report"
    assert args.output_dir == "/path/to/output"
    assert args.sim_user_model == "gemini-3.1-pro-preview"
    assert args.eval_model == "gemini-3.1-flash-lite"
    assert args.run is True
    assert args.timestamped is False


def test_get_parser_evals_report_timestamped() -> None:
    """Test parser parses evals report command with --timestamped."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "evals",
            "report",
            "--output-dir",
            "/path/to/output",
            "--timestamped",
        ]
    )
    assert args.command == "evals"
    assert args.evals_command == "report"
    assert args.output_dir == "/path/to/output"
    assert args.timestamped is True


def test_cli_installed_help() -> None:
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


@mock.patch("cxas_scrapi.core.apps.Apps", autospec=True)
@mock.patch(
    "cxas_scrapi.core.conversation_history.ConversationHistory", autospec=True
)
def test_conversations_list(
    mock_ch_cls: typing.Any, mock_apps_cls: typing.Any
) -> None:
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


def test_conversations_list_invalid_app_name(capsys: typing.Any) -> None:
    args = argparse.Namespace(app_name="malformed-app-name")
    with pytest.raises(SystemExit) as excinfo:
        main_cli.conversations_list(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Invalid App Name format" in captured.out


@mock.patch("cxas_scrapi.core.apps.Apps", autospec=True)
@mock.patch(
    "cxas_scrapi.core.conversation_history.ConversationHistory", autospec=True
)
def test_conversations_get(
    mock_ch_cls: typing.Any, mock_apps_cls: typing.Any
) -> None:
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


def test_conversations_get_invalid_conversation_name(
    capsys: typing.Any,
) -> None:
    args = argparse.Namespace(conversation_resource_name="malformed-conv-name")
    with pytest.raises(SystemExit) as excinfo:
        main_cli.conversations_get(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Invalid Conversation Resource Name format" in captured.out


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
def test_deployments_list(mock_deps_cls: typing.Any) -> None:
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


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
def test_deployments_create(mock_deps_cls: typing.Any) -> None:
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
        channel_type="API",
        traffic_split=None,
    )


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
@mock.patch("cxas_scrapi.cli.app.app_push", autospec=True)
def test_deployments_promote(
    mock_app_push: typing.Any, mock_deps_cls: typing.Any
) -> None:
    args = argparse.Namespace(
        app_resource_name="projects/test-project/locations/global/apps/test-app",
        app_dir="/dummy/path",
        live_deployment_resource_name="projects/test-project/locations/global/apps/test-app/deployments/live-dep",
    )

    def push_side_effect(push_args: typing.Any) -> str:
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


def test_get_parser_run_session_use_tool_fakes() -> None:
    """Test that the parser parses run-session with --use-tool-fakes."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "run-session",
            "text",
            "projects/test-project/locations/global/apps/test-app",
            "--use-tool-fakes",
        ]
    )
    assert args.command == "run-session"
    assert args.modality == "text"
    expected_app = "projects/test-project/locations/global/apps/test-app"
    assert args.app_name == expected_app
    assert args.use_tool_fakes is True


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
def test_deployments_create_with_split(mock_deps_cls: typing.Any) -> None:
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        deployment_id="test-dep",
        version="v1",
        version_id=None,
        traffic_split="v1:90,v2:10",
    )
    mock_deps_inst = mock_deps_cls.return_value

    main_cli.deployments_create(args)

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.create_deployment.assert_called_once_with(
        deployment_id="test-dep",
        display_name="test-dep",
        app_version="v1",
        channel_type="API",
        traffic_split={"v1": 90, "v2": 10},
    )


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
def test_deployments_promote_with_split(mock_deps_cls: typing.Any) -> None:
    args = argparse.Namespace(
        app_resource_name=None,
        app_dir=None,
        live_deployment_resource_name=None,
        app_name="projects/test-project/locations/global/apps/test-app",
        deployment_id="live-dep",
        version="v2",
        traffic_split="v1:50,v2:50",
    )

    mock_deps_inst = mock_deps_cls.return_value
    mock_deps_inst.get_deployment.return_value = mock.MagicMock()

    main_cli.deployments_promote(args)

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.update_deployment.assert_called_once_with(
        deployment_id="live-dep",
        app_version="v2",
        traffic_split={"v1": 50, "v2": 50},
    )


@mock.patch("cxas_scrapi.core.evaluations.Evaluations", autospec=True)
@mock.patch("cxas_scrapi.utils.eval_utils.EvalUtils", autospec=True)
def test_run_eval_modality(
    mock_eval_utils_cls: typing.Any, mock_eval_cls: typing.Any
) -> None:
    """Test that run_eval forwards the modality argument to run_evaluation."""
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        evaluation_id="eval-123",
        display_name_prefix=None,
        tags=None,
        modality="audio",
        wait=False,
        golden_run_method="STABLE",
    )
    mock_eval_inst = mock_eval_cls.return_value
    mock_eval_utils_inst = mock_eval_utils_cls.return_value
    mock_eval_utils_inst.evals_to_dataframe.return_value = {}

    main_cli.run_eval(args)

    mock_eval_cls.assert_called_once_with(app_name=args.app_name)
    mock_eval_inst.run_evaluation.assert_called_once_with(
        evaluations=["eval-123"],
        app_name=args.app_name,
        modality="audio",
        golden_run_method="STABLE",
    )


def test_run_session_headless_failure(
    monkeypatch: typing.Any, capsys: typing.Any
) -> None:
    # Mock isatty to return False (headless environment)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    args = argparse.Namespace(app_name="dummy_app", modality="TEXT")

    with pytest.raises(SystemExit) as excinfo:
        run_session(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    expected_msg = "ERROR: 'run-session' requires an interactive terminal."
    assert expected_msg in captured.err


def test_parser_push_version_name() -> None:
    """Test that the push parser correctly handles --version-name."""
    test_args = [
        "cxas",
        "push",
        "--to",
        "my-app",
        "--create-version",
        "--version-name",
        "v1.2.0",
        "--version-description",
        "Release 1.2.0",
    ]
    with (
        mock.patch.object(sys, "argv", test_args),
        mock.patch("cxas_scrapi.cli.main.app_push") as mock_app_push,
    ):
        main_cli.main()
        mock_app_push.assert_called_once()
        parsed_args = mock_app_push.call_args[0][0]
        assert parsed_args.to == "my-app"
        assert parsed_args.create_version is True
        assert parsed_args.version_name == "v1.2.0"
        assert parsed_args.version_description == "Release 1.2.0"


def test_parser_pull_version_id() -> None:
    """Test that the pull parser correctly handles --version-id."""
    test_args = [
        "cxas",
        "pull",
        "my-app",
        "--target-dir",
        "/tmp/target",
        "--version-id",
        "0.0.3",
        "--overwrite",
    ]
    with (
        mock.patch.object(sys, "argv", test_args),
        mock.patch("cxas_scrapi.cli.main.app_pull") as mock_app_pull,
    ):
        main_cli.main()
        mock_app_pull.assert_called_once()
        parsed_args = mock_app_pull.call_args[0][0]
        assert parsed_args.app == "my-app"
        assert parsed_args.target_dir == "/tmp/target"
        assert parsed_args.version_id == "0.0.3"
        assert parsed_args.overwrite is True
