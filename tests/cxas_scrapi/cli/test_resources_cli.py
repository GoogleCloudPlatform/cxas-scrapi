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

"""Tests for the GECX resources CLI subcommands."""

import argparse
from unittest import mock

from cxas_scrapi.cli import resources_cli
from cxas_scrapi.cli.main import get_parser


def test_parser_resources():
    """Test that subparsers parse GECX resources correctly."""
    parser = get_parser()

    # 1. Tools
    args = parser.parse_args(
        ["tools", "list", "--app-name", "projects/p/locations/l/apps/a"]
    )
    assert args.command == "tools"
    assert args.tools_command == "list"
    assert args.app_name == "projects/p/locations/l/apps/a"

    args = parser.parse_args(
        [
            "tools",
            "delete",
            "--app-name",
            "projects/p/locations/l/apps/a",
            "--name",
            "my-tool",
        ]
    )
    assert args.command == "tools"
    assert args.tools_command == "delete"
    assert args.name == "my-tool"

    # 2. Callbacks
    args = parser.parse_args(
        [
            "callbacks",
            "list",
            "--app-name",
            "projects/p/locations/l/apps/a",
            "--agent-name",
            "my-agent",
        ]
    )
    assert args.command == "callbacks"
    assert args.callbacks_command == "list"
    assert args.agent_name == "my-agent"

    args = parser.parse_args(
        [
            "callbacks",
            "delete",
            "--app-name",
            "projects/p/locations/l/apps/a",
            "--agent-name",
            "my-agent",
            "--callback-type",
            "before_model",
            "--index",
            "0",
        ]
    )
    assert args.command == "callbacks"
    assert args.callbacks_command == "delete"
    assert args.agent_name == "my-agent"
    assert args.callback_type == "before_model"
    assert args.index == 0

    # 3. Variables
    args = parser.parse_args(
        ["variables", "list", "--app-name", "projects/p/locations/l/apps/a"]
    )
    assert args.command == "variables"
    assert args.variables_command == "list"

    args = parser.parse_args(
        [
            "variables",
            "delete",
            "--app-name",
            "projects/p/locations/l/apps/a",
            "--name",
            "my-var",
        ]
    )
    assert args.command == "variables"
    assert args.variables_command == "delete"
    assert args.name == "my-var"


@mock.patch("cxas_scrapi.cli.resources_cli.Tools", autospec=True)
def test_tools_list(mock_tools_cls):
    """Test listing tools."""
    args = argparse.Namespace(app_name="projects/p/locations/l/apps/a")
    mock_inst = mock_tools_cls.return_value
    mock_tool = mock.MagicMock()
    mock_tool.name = "projects/p/locations/l/apps/a/tools/my-tool"
    mock_tool.display_name = "My Tool"
    mock_inst.list_tools.return_value = [mock_tool]

    resources_cli.tools_list(args)
    mock_tools_cls.assert_called_once_with(
        app_name="projects/p/locations/l/apps/a"
    )
    mock_inst.list_tools.assert_called_once()


@mock.patch("cxas_scrapi.cli.resources_cli.Tools", autospec=True)
def test_tools_delete_by_display_name(mock_tools_cls):
    """Test deleting tools by display name."""
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a", name="My Tool"
    )
    mock_inst = mock_tools_cls.return_value
    mock_inst.get_tools_map.return_value = {
        "My Tool": "projects/p/locations/l/apps/a/tools/t1"
    }

    resources_cli.tools_delete(args)
    mock_inst.get_tools_map.assert_called_once_with(reverse=True)
    mock_inst.delete_tool.assert_called_once_with(
        "projects/p/locations/l/apps/a/tools/t1"
    )


@mock.patch("cxas_scrapi.cli.resources_cli.Agents", autospec=True)
@mock.patch("cxas_scrapi.cli.resources_cli.Callbacks", autospec=True)
def test_callbacks_list(mock_cb_cls, mock_agents_cls):
    """Test listing callbacks."""
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a", agent_name=None
    )
    mock_agents_inst = mock_agents_cls.return_value
    mock_agent = mock.MagicMock()
    mock_agent.name = "projects/p/locations/l/apps/a/agents/ag1"
    mock_agent.display_name = "Agent 1"
    mock_agents_inst.list_agents.return_value = [mock_agent]

    mock_cb_inst = mock_cb_cls.return_value
    mock_cb_inst.list_callbacks.return_value = {
        "before_model_callbacks": [],
        "after_model_callbacks": [],
    }

    resources_cli.callbacks_list(args)
    mock_agents_inst.list_agents.assert_called_once()
    mock_cb_inst.list_callbacks.assert_called_once_with(mock_agent.name)


@mock.patch("cxas_scrapi.cli.resources_cli.Agents", autospec=True)
@mock.patch("cxas_scrapi.cli.resources_cli.Callbacks", autospec=True)
def test_callbacks_delete(mock_cb_cls, mock_agents_cls):
    """Test deleting a callback."""
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        agent_name="Agent 1",
        callback_type="before_model",
        index=0,
    )
    mock_agents_inst = mock_agents_cls.return_value
    mock_agents_inst.get_agents_map.return_value = {
        "Agent 1": "projects/p/locations/l/apps/a/agents/ag1"
    }

    mock_cb_inst = mock_cb_cls.return_value

    resources_cli.callbacks_delete(args)
    mock_agents_inst.get_agents_map.assert_called_once_with(reverse=True)
    mock_cb_inst.delete_callback.assert_called_once_with(
        agent_id="projects/p/locations/l/apps/a/agents/ag1",
        callback_type="before_model",
        index=0,
    )


@mock.patch("cxas_scrapi.cli.resources_cli.Variables", autospec=True)
def test_variables_list(mock_vars_cls):
    """Test listing variables."""
    args = argparse.Namespace(app_name="projects/p/locations/l/apps/a")
    mock_inst = mock_vars_cls.return_value
    mock_var = mock.MagicMock()
    mock_var.name = "v1"
    mock_var.schema = mock.MagicMock()
    mock_var.schema.type_ = mock.MagicMock()
    mock_var.schema.type_.name = "STRING"
    mock_inst.list_variables.return_value = [mock_var]
    mock_inst.variable_to_dict.return_value = "hello"

    resources_cli.variables_list(args)
    mock_inst.list_variables.assert_called_once()
    mock_inst.variable_to_dict.assert_called_once_with(mock_var)


@mock.patch("cxas_scrapi.cli.resources_cli.Variables", autospec=True)
def test_variables_delete(mock_vars_cls):
    """Test deleting a variable."""
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a", name="v1"
    )
    mock_inst = mock_vars_cls.return_value

    resources_cli.variables_delete(args)
    mock_inst.delete_variable.assert_called_once_with("v1")
