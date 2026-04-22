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


from cxas_scrapi.migration.data_models import IRTool
from cxas_scrapi.migration.dfcx_example_converter import DFCXExampleConverter


def test_convert_cx_example_to_ps_example():
    cx_example = {
        "displayName": "Test Example",
        "description": "Test Description",
        "actions": [
            {"userUtterance": {"text": "Hello"}},
            {"agentUtterance": {"text": "Hi there"}},
            {
                "toolUse": {
                    "tool": "Test Tool",
                    "action": "get_data",
                    "inputActionParameters": {"id": "123"},
                    "outputActionParameters": {"result": "data"},
                }
            },
        ],
    }
    ps_agent_id = "agent-123"
    ps_agent_display_name = "Test Agent"
    tool_map = {
        "tool-123": IRTool(
            id="tool-123",
            name="projects/123/tools/Test_Tool",
            type="TOOL",
            payload={},
        )
    }
    agent_id_map = {}
    cx_tool_display_name_to_id_map = {"Test Tool": "tool-123"}
    cx_playbook_display_name_to_id_map = {}
    inline_action_map = {}

    res = DFCXExampleConverter.convert_cx_example_to_ps_example(
        cx_example,
        ps_agent_id,
        ps_agent_display_name,
        tool_map,
        agent_id_map,
        cx_tool_display_name_to_id_map,
        cx_playbook_display_name_to_id_map,
        inline_action_map,
    )

    assert res["display_name"] == "Test Agent Test Example"
    assert res["description"] == "Test Description"
    assert res["entry_agent"] == "agent-123"
    assert len(res["messages"]) == 3
    assert res["messages"][0]["role"] == "user"
    assert res["messages"][1]["role"] == "agent"
    assert res["messages"][2]["role"] == "agent"
    assert "tool_call" in res["messages"][2]["chunks"][0]


def test_convert_cx_example_to_ps_example_inline_action():
    cx_example = {
        "displayName": "Test Example",
        "actions": [
            {
                "toolUse": {
                    "tool": "inline-action",
                    "action": "custom_func",
                    "inputActionParameters": {"param": "value"},
                }
            }
        ],
    }
    ps_agent_id = "agent-123"
    ps_agent_display_name = "Test Agent"
    tool_map = {}
    agent_id_map = {}
    cx_tool_display_name_to_id_map = {}
    cx_playbook_display_name_to_id_map = {}
    inline_action_map = {"custom_func": "projects/123/tools/custom_func_tool"}

    res = DFCXExampleConverter.convert_cx_example_to_ps_example(
        cx_example,
        ps_agent_id,
        ps_agent_display_name,
        tool_map,
        agent_id_map,
        cx_tool_display_name_to_id_map,
        cx_playbook_display_name_to_id_map,
        inline_action_map,
    )

    assert len(res["messages"]) == 1
    assert res["messages"][0]["role"] == "agent"
    assert (
        res["messages"][0]["chunks"][0]["tool_call"]["tool"]
        == "projects/123/tools/custom_func_tool"
    )
