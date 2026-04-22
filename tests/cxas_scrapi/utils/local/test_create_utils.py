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

"""Tests for CreateUtils."""

import json
from pathlib import Path
from unittest import mock

import pytest

from cxas_scrapi.utils.local.create_utils import CreateUtils


def test_create_agent(tmp_path):
    """Test create_agent creates directory and files correctly."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Agent"

    safe_name = "My_Test_Agent"
    mock_dict = {
        "displayName": display_name,
        "instruction": f"agents/{safe_name}/instruction.txt",
    }
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_agent(display_name, app_dir)
    target_dir = tmp_path / "agents" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, "r") as f:
        data = json.load(f)
        assert data["displayName"] == display_name
        assert data["instruction"] == f"agents/{safe_name}/instruction.txt"

    instruction_file = target_dir / "instruction.txt"
    assert instruction_file.exists()
    with open(instruction_file, "r") as f:
        content = f.read()
        assert "<role>" in content


def test_create_agent_already_exists(tmp_path):
    """Test create_agent raises FileExistsError when agent directory exists."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Agent"
    safe_name = "My_Test_Agent"

    (tmp_path / "agents" / safe_name).mkdir(parents=True)

    with pytest.raises(FileExistsError) as exc_info:
        utils.create_agent(display_name, app_dir)
    assert "already exists" in str(exc_info.value)


def test_create_tool_non_python(tmp_path):
    """Test create_tool without PYTHON type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    display_name = "My Test Tool"

    mock_dict = {"displayName": display_name}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="GOOGLE_SEARCH"
        )

    safe_name = "My_Test_Tool"
    target_dir = tmp_path / "tools" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, "r") as f:
        data = json.load(f)
        assert data["displayName"] == display_name

    assert not (target_dir / "python_function").exists()


def test_create_tool_python(tmp_path):
    """Test create_tool with PYTHON type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    display_name = "My Python Tool"
    safe_name = "My_Python_Tool"

    mock_dict = {
        "displayName": display_name,
        "pythonFunction": {"name": safe_name},
    }
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="PYTHON"
        )

    target_dir = tmp_path / "tools" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, "r") as f:
        data = json.load(f)
        assert data["displayName"] == display_name
        assert data["pythonFunction"]["name"] == safe_name

    code_file = target_dir / "python_function" / "python_code.py"
    assert code_file.exists()

    with open(code_file, "r") as f:
        content = f.read()
        assert f"def {safe_name}() -> dict:" in content


def test_create_tool_openapi(tmp_path):
    """Test create_tool with OPENAPI type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My OpenAPI Tool"
    safe_name = "My_OpenAPI_Tool"

    mock_dict = {
        "displayName": display_name,
        "openApiToolset": {
            "openApiSchema": (
                f"toolsets/{safe_name}/open_api_toolset/open_api_schema.yaml"
            )
        },
    }
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="OPENAPI"
        )

    target_dir = tmp_path / "toolsets" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, "r") as f:
        data = json.load(f)
        assert data["displayName"] == display_name

    schema_file = target_dir / "open_api_toolset" / "open_api_schema.yaml"
    assert schema_file.exists()


def test_create_tool_datastore(tmp_path):
    """Test create_tool with DATASTORE type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Datastore Tool"
    safe_name = "My_Datastore_Tool"

    mock_dict = {
        "displayName": display_name,
        "dataStoreTool": {"name": safe_name},
    }
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="DATASTORE"
        )

    target_dir = tmp_path / "tools" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, "r") as f:
        data = json.load(f)
        assert data["displayName"] == display_name


def test_create_tool_unsupported_type(tmp_path):
    """Test create_tool raises ValueError for unsupported tool type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()

    with pytest.raises(ValueError) as exc_info:
        utils.create_tool("My Tool", app_dir, tool_type="INVALID_TYPE")
    assert "Unsupported tool type" in str(exc_info.value)


def test_create_tool_openapi_add_to_agent_error(tmp_path):
    """Test create_tool raises ValueError when adding OPENAPI tool to agent."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    agent_name = "My Agent"

    # Mock _get_agent to avoid reading files and parsing protobuf
    with mock.patch.object(utils, "_get_agent", return_value=mock.Mock()):
        with pytest.raises(ValueError) as exc_info:
            utils.create_tool(
                "My Tool", app_dir, tool_type="OPENAPI", add_to_agent=agent_name
            )
    assert "Open API tool cannot be added to an agent" in str(exc_info.value)


def test_get_agent_missing_json_file(tmp_path):
    """Test _get_agent raises FileNotFoundError when json file is missing."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    agent_name = "My Agent"
    safe_name = "My_Agent"

    # Create agent directory but not the json file
    (tmp_path / "agents" / safe_name).mkdir(parents=True)

    with pytest.raises(FileNotFoundError) as exc_info:
        utils.create_tool("My Tool", app_dir, add_to_agent=agent_name)
    assert "config not found at" in str(exc_info.value)


def test_validate_app_dir_success(tmp_path):
    """Test _validate_app_dir succeeds when both agents and tools exist."""
    utils = CreateUtils()
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    utils._validate_app_dir(str(tmp_path))


def test_validate_app_dir_missing_agents(tmp_path):
    """Test _validate_app_dir fails when agents/ is missing."""
    utils = CreateUtils()
    (tmp_path / "tools").mkdir()
    with pytest.raises(FileNotFoundError):
        utils._validate_app_dir(str(tmp_path))


def test_create_tool_add_to_agent(tmp_path):
    """Test create_tool with add_to_agent adds tool to agent's tools list."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    # Setup an agent first
    agent_name = "My Agent"
    agent_safe_name = "My_Agent"
    agent_dir = agents_dir / agent_safe_name
    agent_dir.mkdir()
    agent_json_file = agent_dir / f"{agent_safe_name}.json"

    initial_agent_data = {"displayName": agent_name, "tools": []}
    with open(agent_json_file, "w") as f:
        json.dump(initial_agent_data, f)

    display_name = "My Added Tool"
    safe_name = "My_Added_Tool"
    mock_dict = {"displayName": agent_name, "tools": [display_name]}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )

    with mock.patch(
        "cxas_scrapi.utils.local.create_utils.json_format.ParseDict"
    ):
        with mock.patch(patch_path, return_value=mock_dict):
            result_path = utils.create_tool(
                display_name,
                app_dir,
                tool_type="GOOGLE_SEARCH",
                add_to_agent=agent_name,
            )

    # Verify tool created
    assert Path(result_path) == tmp_path / "tools" / safe_name

    # Verify agent updated
    with open(agent_json_file, "r") as f:
        print(agent_json_file)
        updated_agent = json.load(f)
        print(updated_agent)
        assert "tools" in updated_agent
        assert display_name in updated_agent["tools"]


def test_create_tool_add_to_agent_missing(tmp_path):
    """Test create_tool with missing add_to_agent raises FileNotFoundError."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    display_name = "My Tool"
    mock_dict = {"displayName": display_name}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )

    with mock.patch(patch_path, return_value=mock_dict):
        with pytest.raises(FileNotFoundError) as exc_info:
            utils.create_tool(
                display_name, app_dir, add_to_agent="Nonexistent Agent"
            )
    assert "config not found" in str(exc_info.value)
