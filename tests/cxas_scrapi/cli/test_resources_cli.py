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
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

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


@mock.patch("cxas_scrapi.core.tools.Tools", autospec=True)
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


@mock.patch("cxas_scrapi.core.tools.Tools", autospec=True)
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


@mock.patch("cxas_scrapi.core.agents.Agents", autospec=True)
@mock.patch("cxas_scrapi.core.callbacks.Callbacks", autospec=True)
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


@mock.patch("cxas_scrapi.core.agents.Agents", autospec=True)
@mock.patch("cxas_scrapi.core.callbacks.Callbacks", autospec=True)
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


@mock.patch("cxas_scrapi.core.variables.Variables", autospec=True)
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


@mock.patch("cxas_scrapi.core.variables.Variables", autospec=True)
def test_variables_delete(mock_vars_cls):
    """Test deleting a variable."""
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a", name="v1"
    )
    mock_inst = mock_vars_cls.return_value

    resources_cli.variables_delete(args)
    mock_inst.delete_variable.assert_called_once_with("v1")


import pytest


def _ns(**kwargs):
    base = dict(
        app_name="projects/p/locations/l/apps/a",
        tools_command="list",
        callbacks_command="list",
        variables_command="list",
        name="item",
        agent_name="ag1",
        callback_type="before_model",
        index=0,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


class FakeProto:
    """Simulates a protobuf/pydantic message with dot access and class/instance to_dict."""

    def __init__(self, **kwargs: Any) -> None:
        self._dict = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(getattr(self, "_dict", {}))


@patch(
    "cxas_scrapi.core.common.Common._get_app_name",
    return_value="projects/p/locations/l/apps/a",
)
@patch("cxas_scrapi.core.tools.Tools")
def test_tools_delete_display_name_and_errors(
    mock_tools_cls, mock_get_app, capsys
):
    mock_tools = mock_tools_cls.return_value
    t1 = FakeProto(
        name="projects/p/locations/l/apps/a/tools/t1", display_name="my_tool"
    )
    mock_tools.list_tools.return_value = [t1]
    mock_tools.get_tools_map.return_value = {
        "my_tool": "projects/p/locations/l/apps/a/tools/t1"
    }

    # 1. Delete by display name
    resources_cli.tools_delete(_ns(tools_command="delete", name="my_tool"))
    mock_tools.delete_tool.assert_called_once_with(
        "projects/p/locations/l/apps/a/tools/t1"
    )

    # 2. Delete exception
    mock_tools.delete_tool.side_effect = RuntimeError("Tool locked")
    with pytest.raises(SystemExit) as exc:
        resources_cli.tools_delete(
            _ns(
                tools_command="delete",
                name="projects/p/locations/l/apps/a/tools/t1",
            )
        )
    assert exc.value.code == 1
    assert "Failed to delete tool" in capsys.readouterr().out


@patch(
    "cxas_scrapi.core.common.Common._get_app_name",
    return_value="projects/p/locations/l/apps/a",
)
@patch("cxas_scrapi.core.agents.Agents")
@patch("cxas_scrapi.core.callbacks.Callbacks")
def test_callbacks_list_and_delete_branches(
    mock_cb_cls, mock_agents_cls, mock_get_app, capsys
):
    """Test callbacks listing with agent filter and callback deletion by index."""
    mock_cb = mock_cb_cls.return_value
    mock_agents = mock_agents_cls.return_value
    ag_mock = FakeProto(
        name="projects/p/locations/l/apps/a/agents/ag1", display_name="ag1"
    )
    mock_agents.get_agent.return_value = ag_mock
    mock_agents.get_agents_map.return_value = {
        "ag1": "projects/p/locations/l/apps/a/agents/ag1"
    }
    mock_agents.list_agents.return_value = [ag_mock]

    cb_item = FakeProto(
        uri="https://cb.com",
        display_name="my_cb",
        python_code="def cb(): pass\n",
        description="Test cb",
    )
    mock_cb.list_callbacks.return_value = {"before_model": [cb_item]}

    # 1. list with agent and cb_type filter
    resources_cli.callbacks_list(
        _ns(
            callbacks_command="list",
            agent_name="projects/p/locations/l/apps/a/agents/ag1",
            callback_type="before_model",
        )
    )
    assert "Test cb" in capsys.readouterr().out

    # 2. delete by index
    resources_cli.callbacks_delete(
        _ns(
            callbacks_command="delete",
            agent_name="ag1",
            callback_type="before_model",
            index=0,
        )
    )
    mock_cb.delete_callback.assert_called_once()

    # 3. delete exception
    mock_cb.delete_callback.side_effect = RuntimeError("Cannot delete")
    with pytest.raises(SystemExit) as exc:
        resources_cli.callbacks_delete(
            _ns(
                callbacks_command="delete",
                agent_name="ag1",
                callback_type="before_model",
                index=0,
            )
        )
    assert exc.value.code == 1


@patch(
    "cxas_scrapi.core.common.Common._get_app_name",
    return_value="projects/p/locations/l/apps/a",
)
@patch("cxas_scrapi.core.tools.Tools")
@patch("cxas_scrapi.core.agents.Agents")
@patch("cxas_scrapi.core.callbacks.Callbacks")
@patch("cxas_scrapi.core.variables.Variables")
def test_resources_fallback_and_error_branches(
    mock_vars_cls: MagicMock,
    mock_cb_cls: MagicMock,
    mock_agents_cls: MagicMock,
    mock_tools_cls: MagicMock,
    mock_get_app: MagicMock,
    capsys: Any,
) -> None:
    """Test trailing ID lookup fallbacks and exception handling across resources_cli."""
    mock_tools = mock_tools_cls.return_value
    t1 = FakeProto(
        name="projects/p/locations/l/apps/a/tools/t1", display_name="t1"
    )
    mock_tools.get_tools_map.return_value = {}
    mock_tools.list_tools.return_value = [t1]
    resources_cli.tools_delete(_ns(tools_command="delete", name="t1"))
    mock_tools.delete_tool.assert_called_with(
        "projects/p/locations/l/apps/a/tools/t1"
    )

    mock_agents = mock_agents_cls.return_value
    ag1 = FakeProto(
        name="projects/p/locations/l/apps/a/agents/ag1", display_name="ag1"
    )
    mock_agents.get_agents_map.return_value = {}
    mock_agents.list_agents.return_value = [ag1]
    mock_cb = mock_cb_cls.return_value
    mock_cb.list_callbacks.return_value = {}
    resources_cli.callbacks_list(
        _ns(callbacks_command="list", agent_name="ag1")
    )
    assert "(No callbacks configured)" in capsys.readouterr().out

    mock_cb.list_callbacks.side_effect = RuntimeError("CB error")
    with pytest.raises(SystemExit) as exc:
        resources_cli.callbacks_list(
            _ns(callbacks_command="list", agent_name="ag1")
        )
    assert exc.value.code == 1

    mock_vars = mock_vars_cls.return_value
    mock_vars.list_variables.side_effect = RuntimeError("Var error")
    with pytest.raises(SystemExit) as exc:
        resources_cli.variables_list(_ns(variables_command="list"))
    assert exc.value.code == 1


@patch(
    "cxas_scrapi.core.common.Common._get_app_name",
    return_value="projects/p/locations/l/apps/a",
)
@patch("cxas_scrapi.core.tools.Tools")
def test_tools_error_and_not_found(mock_tools_cls, mock_get_app, capsys):
    """Test tool listing exceptions and tool deletion not found."""
    mock_tools = mock_tools_cls.return_value
    # 1. list exception
    mock_tools.list_tools.side_effect = RuntimeError("API down")
    with pytest.raises(SystemExit) as exc:
        resources_cli.tools_list(_ns(tools_command="list"))
    assert exc.value.code == 1

    # 2. delete not found
    mock_tools.list_tools.side_effect = None
    mock_tools.list_tools.return_value = []
    mock_tools.get_tools_map.return_value = {}
    with pytest.raises(SystemExit) as exc:
        resources_cli.tools_delete(
            _ns(tools_command="delete", name="non_existent")
        )
    assert exc.value.code == 1
    assert "not found in app" in capsys.readouterr().out


@patch(
    "cxas_scrapi.core.common.Common._get_app_name",
    return_value="projects/p/locations/l/apps/a",
)
@patch("cxas_scrapi.core.variables.Variables")
def test_variables_list_and_delete_branches(
    mock_vars_cls, mock_get_app, capsys
):
    """Test variables listing and deletion success and error branches."""
    mock_vars = mock_vars_cls.return_value
    mock_vars.variable_to_dict.side_effect = lambda v: {
        "name": v.name,
        "displayName": v.display_name,
        "description": "ID",
        "type": "STRING",
    }
    v1 = FakeProto(
        name="projects/p/locations/l/apps/a/variables/v1",
        display_name="user_id",
        description="ID",
        type="STRING",
        schema=FakeProto(type_=1),
    )
    mock_vars.list_variables.return_value = [v1]

    # 1. list variables
    resources_cli.variables_list(_ns(variables_command="list"))
    assert "user_id" in capsys.readouterr().out

    # 2. delete variable by display name
    resources_cli.variables_delete(
        _ns(variables_command="delete", name="user_id")
    )
    mock_vars.delete_variable.assert_called_once_with("user_id")

    # 3. delete variable exception
    mock_vars.delete_variable.side_effect = RuntimeError("Var locked")
    with pytest.raises(SystemExit) as exc:
        resources_cli.variables_delete(
            _ns(variables_command="delete", name="user_id")
        )
    assert exc.value.code == 1


@mock.patch("cxas_scrapi.core.variables.Variables")
def test_variables_list_success(mock_vars_cls: MagicMock, capsys: Any) -> None:
    """Verifies variables_list displays variable declarations table.

    Args:
        mock_vars_cls: Mocked Variables client class.
        capsys: Pytest stdout capture fixture.
    """
    mock_inst = mock_vars_cls.return_value
    var1 = MagicMock()
    var1.name = "var_one"
    var1.schema.type_ = 1
    mock_inst.list_variables.return_value = [var1]
    mock_inst.variable_to_dict.return_value = "default_1"

    args = argparse.Namespace(app_name="projects/p/locations/l/apps/a")
    resources_cli.variables_list(args)

    captured = capsys.readouterr()
    assert "var_one" in captured.out


@mock.patch("cxas_scrapi.core.variables.Variables")
def test_variables_delete_success(
    mock_vars_cls: MagicMock, capsys: Any
) -> None:
    """Verifies variables_delete invokes SDK delete_variable.

    Args:
        mock_vars_cls: Mocked Variables client class.
        capsys: Pytest stdout capture fixture.
    """
    mock_inst = mock_vars_cls.return_value
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        name="var_one",
    )
    resources_cli.variables_delete(args)
    mock_inst.delete_variable.assert_called_once()


def test_resources_invalid_app_name(capsys: Any) -> None:
    """Verifies error exit on malformed app resource name for resources handlers."""
    args = argparse.Namespace(app_name="malformed_app")
    with pytest.raises(SystemExit) as excinfo:
        resources_cli.tools_list(args)
    assert excinfo.value.code == 1

    with pytest.raises(SystemExit) as excinfo:
        resources_cli.callbacks_list(args)
    assert excinfo.value.code == 1

    with pytest.raises(SystemExit) as excinfo:
        resources_cli.variables_list(args)
    assert excinfo.value.code == 1
