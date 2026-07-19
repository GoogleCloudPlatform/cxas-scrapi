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

"""Unit tests for create_local CLI commands."""

import argparse
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from cxas_scrapi.cli.create_local import handle_local_create


def _ns(**kwargs: Any) -> argparse.Namespace:
    base = dict(
        name="test-item",
        app_dir=".",
        tool_type=None,
        add_to_agent=None,
        guardrail_type="llm_policy",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


@patch("cxas_scrapi.cli.create_local.CreateUtils", autospec=True)
def test_handle_local_create_agent(mock_utils_cls: MagicMock, capsys: Any) -> None:
    """Test creating a local agent template."""
    mock_inst = mock_utils_cls.return_value
    mock_inst.create_agent.return_value = "agents/test-item"

    args = _ns(create_local_command="agent")
    handle_local_create(args)

    mock_inst.create_agent.assert_called_once_with(display_name="test-item", app_dir=".")
    captured = capsys.readouterr()
    assert "Creating local agent template: test-item" in captured.out
    assert "Successfully created local template at: agents/test-item" in captured.out


@patch("cxas_scrapi.cli.create_local.CreateUtils", autospec=True)
def test_handle_local_create_tool(mock_utils_cls: MagicMock, capsys: Any) -> None:
    """Test creating a local tool template."""
    mock_inst = mock_utils_cls.return_value
    mock_inst.create_tool.return_value = "tools/test-item"

    args = _ns(create_local_command="tool", tool_type="http", add_to_agent="agent-a")
    handle_local_create(args)

    mock_inst.create_tool.assert_called_once_with(
        display_name="test-item",
        app_dir=".",
        tool_type="http",
        add_to_agent="agent-a",
    )
    captured = capsys.readouterr()
    assert "Successfully created local template at: tools/test-item" in captured.out


@patch("cxas_scrapi.cli.create_local.CreateUtils", autospec=True)
def test_handle_local_create_guardrail(mock_utils_cls: MagicMock, capsys: Any) -> None:
    """Test creating a local guardrail template."""
    mock_inst = mock_utils_cls.return_value
    mock_inst.create_guardrail.return_value = "guardrails/test-item"

    args = _ns(create_local_command="guardrail", guardrail_type="pii")
    handle_local_create(args)

    mock_inst.create_guardrail.assert_called_once_with(
        display_name="test-item",
        app_dir=".",
        guardrail_type="pii",
    )
    captured = capsys.readouterr()
    assert "Successfully created local template at: guardrails/test-item" in captured.out


@patch("cxas_scrapi.cli.create_local.CreateUtils", autospec=True)
def test_handle_local_create_exception(mock_utils_cls: MagicMock, capsys: Any) -> None:
    """Test exception handling in handle_local_create."""
    mock_inst = mock_utils_cls.return_value
    mock_inst.create_agent.side_effect = RuntimeError("Disk full")

    args = _ns(create_local_command="agent")
    with pytest.raises(SystemExit) as exc:
        handle_local_create(args)
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Failed to create local template: Disk full" in captured.out
