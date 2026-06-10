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


@pytest.fixture(autouse=True)
def clear_workspace_cache():
    from cxas_scrapi.core import workspace as ws

    ws._workspace_config_cache = None
    ws._project_dir = None
    ws._active_project_cache = None
    with (
        mock.patch(
            "cxas_scrapi.core.workspace.resolve_project_dir",
            side_effect=ValueError("No active project"),
        ),
        mock.patch(
            "cxas_scrapi.core.workspace.find_workspace_root",
            return_value=None,
        ),
    ):
        yield


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


def test_get_parser_llm_lint():
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


def test_cli_installed_help():
    """Test that the 'cxas' command is installed and executable (verifies

    setup.py).
    """
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
        import os
        import pathlib

        env = os.environ.copy()
        project_root = str(pathlib.Path(__file__).parents[3])
        env["PYTHONPATH"] = (
            project_root + os.pathsep + env.get("PYTHONPATH", "")
        )

        result = subprocess.run(
            [sys.executable, "-c", py_code, "--help"],
            capture_output=True,
            text=True,
            check=True,
            cwd="/tmp",
            env=env,
        )

        assert result.returncode == 0
        assert "usage: cxas" in result.stdout
    except FileNotFoundError:
        pytest.fail(
            "The 'cxas' command was not found in the environment. Is it"
            " installed?"
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


@mock.patch("cxas_scrapi.core.apps.Apps", autospec=True)
@mock.patch(
    "cxas_scrapi.core.conversation_history.ConversationHistory", autospec=True
)
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


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
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


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
def test_deployments_create(mock_deps_cls):
    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        deployment_id="test-dep",
        version_id=(
            "projects/test-project/locations/global/apps/test-app/versions/v1"
        ),
    )
    mock_deps_inst = mock_deps_cls.return_value

    main_cli.deployments_create(args)

    mock_deps_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app"
    )
    mock_deps_inst.create_deployment.assert_called_once_with(
        deployment_id="test-dep",
        display_name="test-dep",
        app_version=(
            "projects/test-project/locations/global/apps/test-app/versions/v1"
        ),
    )


@mock.patch("cxas_scrapi.core.deployments.Deployments", autospec=True)
@mock.patch("cxas_scrapi.cli.app.app_push", autospec=True)
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


def test_get_parser_run_session_use_tool_fakes():
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


def test_get_parser_workspace_set():
    """Test that the parser can parse the workspace set command."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "workspace",
            "set",
            "--project-id",
            "my-gcp-project",
            "--app-id",
            "my-app-id",
            "--location",
            "us-central1",
            "--app-dir",
            "custom_app",
            "--evals-dir",
            "custom_evals",
            "--output-dir",
            "custom_output",
            "--model",
            "gemini-pro",
            "--modality",
            "audio",
        ]
    )
    assert args.command == "workspace"
    assert args.workspace_command == "set"
    assert args.gcp_project_id == "my-gcp-project"
    assert args.deployed_app_id == "my-app-id"
    assert args.location == "us-central1"
    assert args.app_dir == "custom_app"
    assert args.evals_dir == "custom_evals"
    assert args.output_dir == "custom_output"
    assert args.model == "gemini-pro"
    assert args.modality == "audio"


def test_get_parser_workspace_create():
    """Test that the parser can parse the workspace create command."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "workspace",
            "create",
            "--target-dir",
            "my_project",
        ]
    )
    assert args.command == "workspace"
    assert args.workspace_command == "create"
    assert args.target_dir == "my_project"


@mock.patch("cxas_scrapi.cli.workspace.ws.create_default_config")
def test_workspace_create_calls_core_create(mock_create_config):
    """Verify workspace_create delegates to create_default_config."""
    from pathlib import Path

    from cxas_scrapi.cli.workspace import workspace_create

    args = argparse.Namespace(target_dir="some_dir")
    workspace_create(args)
    expected_path = str(Path("some_dir").resolve())
    mock_create_config.assert_called_once_with(expected_path)


@mock.patch("cxas_scrapi.cli.workspace.ws.resolve_project_dir")
@mock.patch("cxas_scrapi.cli.workspace.ws.find_workspace_root")
@mock.patch("cxas_scrapi.cli.workspace.ws.update_workspace_config")
def test_workspace_set_calls_core_update(
    mock_update_config, mock_find_ws_root, mock_resolve_dir, tmp_path
):
    """Verify workspace_set delegates to update_workspace_config."""
    from cxas_scrapi.cli.workspace import workspace_set

    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    (project_dir / "gecx-config.json").touch()

    args = argparse.Namespace(
        gcp_project_id="proj",
        deployed_app_id="app",
        location="loc",
        app_dir="dir",
        evals_dir="evals",
        output_dir="out",
        model="model",
        modality="mod",
    )
    mock_resolve_dir.return_value = str(project_dir)
    mock_find_ws_root.return_value = str(tmp_path)
    mock_update_config.return_value = (
        True,
        str(project_dir / "gecx-config.toml"),
    )

    workspace_set(args)

    expected_updates = {
        "gcp_project_id": "proj",
        "deployed_app_id": "app",
        "location": "loc",
        "app_dir": "dir",
        "evals_dir": "evals",
        "output_dir": "out",
        "model": "model",
        "modality": "mod",
    }
    mock_update_config.assert_called_once_with(expected_updates)


@mock.patch("cxas_scrapi.cli.workspace.ws.resolve_project_dir")
@mock.patch("cxas_scrapi.cli.workspace.ws.find_workspace_root")
@mock.patch("cxas_scrapi.cli.workspace.ws.update_workspace_config")
def test_workspace_set_handles_file_not_found(
    mock_update_config, mock_find_ws_root, mock_resolve_dir, tmp_path, capsys
):
    """Verify workspace_set handles missing config file correctly."""
    from cxas_scrapi.cli.workspace import workspace_set

    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    (project_dir / "gecx-config.json").touch()

    args = argparse.Namespace(gcp_project_id="proj")
    mock_resolve_dir.return_value = str(project_dir)
    mock_find_ws_root.return_value = str(tmp_path)
    mock_update_config.side_effect = FileNotFoundError("Config not found")

    with pytest.raises(SystemExit) as excinfo:
        workspace_set(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Config not found" in captured.out


@mock.patch("cxas_scrapi.core.workspace.resolve_project_dir")
def test_workspace_show_prints_config(mock_resolve_dir, tmp_path, capsys):
    """Verify that workspace_show prints the current configuration."""
    import json

    from cxas_scrapi.cli.workspace import workspace_show

    mock_resolve_dir.return_value = str(tmp_path)

    config_file = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-proj",
        "deployed_app_id": "test-app",
        "location": "us",
        "app_dir": "app",
        "evals_dir": "evals",
        "output_dir": ".scrapi-out",
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    args = argparse.Namespace()
    workspace_show(args)

    captured = capsys.readouterr()
    normalized_out = captured.out.replace("\n", "").replace(" ", "")
    assert "ProjectPath:" in normalized_out
    assert str(tmp_path).replace(" ", "") in normalized_out
    assert "ConfigurationFile:" in normalized_out
    assert "gecx-config" in normalized_out
    assert '"gcp_project_id":"test-proj"' in normalized_out
    assert '"output_dir":".scrapi-out"' in normalized_out


def test_cli_no_args_prints_help():
    """Test that running the cli with no arguments prints the main help page."""
    py_code = (
        "import sys; "
        "sys.argv[0]='cxas'; "
        "from cxas_scrapi.cli.main import main; "
        "main()"
    )
    import os
    import pathlib

    env = os.environ.copy()
    project_root = str(pathlib.Path(__file__).parents[3])
    env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", py_code],
        capture_output=True,
        text=True,
        cwd="/tmp",
        env=env,
    )
    assert result.returncode == 0

    assert "usage: cxas" in result.stdout
    assert "options:" in result.stdout


def test_cli_workspace_no_subcommand_prints_workspace_help():
    """Verify 'cxas workspace' with no subcommand prints help."""
    py_code = (
        "import sys; "
        "sys.argv[0]='cxas'; "
        "from cxas_scrapi.cli.main import main; "
        "main()"
    )
    import os
    import pathlib

    env = os.environ.copy()
    project_root = str(pathlib.Path(__file__).parents[3])
    env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", py_code, "workspace"],
        capture_output=True,
        text=True,
        cwd="/tmp",
        env=env,
    )
    assert result.returncode == 0

    assert "usage: cxas workspace" in result.stdout
    assert "Workspace Commands:" in result.stdout
