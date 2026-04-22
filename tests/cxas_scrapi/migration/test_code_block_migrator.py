# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.Agent.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for code_block_migrator.py."""

from unittest.mock import MagicMock

import pytest

from cxas_scrapi.migration.code_block_migrator import (
    CodeBlockMigrator,
)


@pytest.fixture
def migrator():
    mock_tools = MagicMock()
    return CodeBlockMigrator(ps_tools_client=mock_tools, ai_augment_client=None)


def test_get_typing_imports_for_function(migrator):
    code = """def my_func(arg1: Dict[str, Any]) -> List[str]:
    return []
"""
    imports = migrator._get_typing_imports_for_function(code)
    assert "from typing import Dict" in imports
    assert "from typing import Any" in imports
    assert "from typing import List" in imports


def test_parse_code_block_with_ast(migrator):
    code = """import os
from datetime import datetime

def func1():
    pass

def func2():
    pass
"""
    imports, functions = migrator._parse_code_block_with_ast(code)
    assert "import os" in imports
    assert "from datetime import datetime" in imports
    assert len(functions) == 2
    assert functions[0][0] == "func1"
    assert functions[1][0] == "func2"


def test_extract_functions_to_ir(migrator):
    code = """def transfer_to_agent(arg1):
    pass

def use_tool():
    tools.my_tool.my_op({"param": "value"})
"""
    tool_map = {
        "tool_1": {
            "type": "TOOLSET",
            "name": "projects/p1/locations/l1/apps/a1/tools/toolset_1",
        }
    }
    tool_display_name_map = {"my_tool": "tool_1"}

    extracted_tools, action_map, referenced_toolsets = (
        migrator.extract_functions_to_ir(
            code=code,
            existing_tool_ids=set(),
            migrated_function_names=set(),
            function_name_to_tool_map={},
            tool_map=tool_map,
            tool_display_name_map=tool_display_name_map,
            target_app_resource_name="projects/p1/locations/l1/apps/a1",
        )
    )

    assert len(extracted_tools) == 2

    # Verify reserved name handling (transfer_to_agent -> usr_transfer_to_agent)
    assert action_map["transfer_to_agent"] == "usr_transfer_to_agent"
    assert extracted_tools[0]["id"] == "usr_transfer_to_agent"

    # Verify tool call transformation
    # (tools.my_tool.my_op -> tools.toolset_1_my_op)
    assert action_map["use_tool"] == "use_tool"
    assert (
        "toolset_1_my_op"
        in extracted_tools[1]["payload"]["pythonFunction"]["python_code"]
    )
    assert (
        "projects/p1/locations/l1/apps/a1/tools/toolset_1"
        in referenced_toolsets
    )
