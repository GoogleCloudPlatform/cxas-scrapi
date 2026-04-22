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

import re
from unittest.mock import MagicMock

from cxas_scrapi.migration.data_models import IRTool
from cxas_scrapi.migration.dfcx_playbook_converter import (
    DFCXPlaybookConverter,
)


def test_sanitize_display_name():
    assert (
        DFCXPlaybookConverter.sanitize_display_name("Valid Name")
        == "Valid Name"
    )
    assert (
        DFCXPlaybookConverter.sanitize_display_name("Invalid@Name")
        == "InvalidName"
    )
    assert DFCXPlaybookConverter.sanitize_display_name("a" * 100) == "a" * 85


def test_recursively_extract_instructions():
    steps = [
        {"text": "Step 1"},
        {"text": "Step 2", "steps": [{"text": "Substep 2.1"}]},
    ]
    lines = DFCXPlaybookConverter.recursively_extract_instructions(steps)
    assert len(lines) == 3
    assert lines[0] == "- Step 1"
    assert lines[1] == "- Step 2"
    assert lines[2] == "    - Substep 2.1"


def test_var_replacer():
    mock_reporter = MagicMock()
    parameter_name_map = {"var1": "sanitized_var1"}
    pattern = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_-]*)")
    match = pattern.search("$var1")

    res = DFCXPlaybookConverter.var_replacer(
        match, parameter_name_map, mock_reporter
    )
    assert res == "{sanitized_var1}"
    assert mock_reporter.log_transformation.call_count == 1


def test_replace_tool_reference():
    mock_reporter = MagicMock()
    cx_tool_display_name_to_id_map = {"Test Tool": "tool-123"}
    tool_map = {
        "tool-123": IRTool(
            id="tool-123",
            name="projects/123/tools/Test_Tool",
            type="TOOL",
            payload={},
        )
    }
    pattern = re.compile(r"\${TOOL:([^}]+)}")
    match = pattern.search("${TOOL:Test Tool}")

    res = DFCXPlaybookConverter.replace_tool_reference(
        match, cx_tool_display_name_to_id_map, tool_map, mock_reporter
    )
    assert res == "{@TOOL: Test_Tool}"
    assert mock_reporter.log_transformation.call_count == 1


def test_replace_routing_ref():
    mock_reporter = MagicMock()
    pattern = re.compile(
        r"\$\{\s*(agent|flow|playbook)\s*:\s*([^}]+)\}", flags=re.IGNORECASE
    )
    match = pattern.search("${playbook:Target Agent}")

    res = DFCXPlaybookConverter.replace_routing_ref(match, mock_reporter)
    assert res == "{@AGENT: Target Agent}"
    assert mock_reporter.log_transformation.call_count == 1


def test_convert_cx_playbook_to_ps_agent():
    mock_reporter = MagicMock()
    converter = DFCXPlaybookConverter(mock_reporter)

    cx_playbook = {
        "displayName": "Test Playbook",
        "goal": "Test Goal",
        "instruction": {"steps": [{"text": "Step 1"}]},
    }
    tool_map = {}
    parameter_name_map = {}
    cx_tool_display_name_to_id_map = {}
    master_inline_action_map = {}

    res = converter.convert_cx_playbook_to_ps_agent(
        cx_playbook,
        tool_map,
        "Generated Description",
        parameter_name_map,
        cx_tool_display_name_to_id_map,
        master_inline_action_map,
        "model-123",
    )

    assert res["display_name"] == "Test Playbook"
    assert res["description"] == "Generated Description"
    assert "# Agent Goal\nTest Goal" in res["instruction"]
