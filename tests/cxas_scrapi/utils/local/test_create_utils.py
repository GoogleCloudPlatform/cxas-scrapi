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
import typing
from pathlib import Path
from unittest import mock

import pytest

from cxas_scrapi.utils.local.create_utils import CreateUtils


def test_create_agent(tmp_path: typing.Any) -> None:
    """Test create_agent creates directory and files correctly."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Agent"

    safe_name = "my_test_agent"
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

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["displayName"] == display_name
        assert data["instruction"] == f"agents/{safe_name}/instruction.txt"

    instruction_file = target_dir / "instruction.txt"
    assert instruction_file.exists()
    with open(instruction_file, encoding="utf-8") as f:
        content = f.read()
        assert "<role>" in content
        assert "${current_date}" in content
        assert "${price}" in content


def test_create_agent_already_exists(tmp_path: typing.Any) -> None:
    """Test create_agent raises FileExistsError when agent directory exists."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Agent"
    safe_name = "my_test_agent"

    (tmp_path / "agents" / safe_name).mkdir(parents=True)

    with pytest.raises(FileExistsError) as exc_info:
        utils.create_agent(display_name, app_dir)
    assert "already exists" in str(exc_info.value)


def test_create_tool_non_python(tmp_path: typing.Any) -> None:
    """Test create_tool without PYTHON type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    display_name = "My Test Tool"
    safe_name = "my_test_tool"

    mock_dict = {"displayName": safe_name}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )
    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="GOOGLE_SEARCH"
        )

    target_dir = tmp_path / "tools" / safe_name

    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["displayName"] == safe_name

    assert not (target_dir / "python_function").exists()


def test_create_tool_python(tmp_path: typing.Any) -> None:
    """Test create_tool with PYTHON type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    display_name = "My Python Tool"
    safe_name = "my_python_tool"

    mock_dict = {
        "displayName": safe_name,
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

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["displayName"] == safe_name
        assert data["pythonFunction"]["name"] == safe_name

    code_file = target_dir / "python_function" / "python_code.py"
    assert code_file.exists()

    with open(code_file, encoding="utf-8") as f:
        content = f.read()
        assert f"def {safe_name}() -> dict:" in content
        assert '"""Docstring explaining how to use' in content
        assert "try:" in content
        assert "return {}" in content
        assert '"agent_action"' in content


def test_create_tool_openapi(tmp_path: typing.Any) -> None:
    """Test create_tool with OPENAPI type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My OpenAPI Tool"
    safe_name = "my_openapi_tool"

    mock_dict = {
        "displayName": safe_name,
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

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["displayName"] == safe_name

    schema_file = target_dir / "open_api_toolset" / "open_api_schema.yaml"
    assert schema_file.exists()


def test_create_tool_datastore(tmp_path: typing.Any) -> None:
    """Test create_tool with DATASTORE type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Datastore Tool"
    safe_name = "my_datastore_tool"

    mock_dict = {
        "displayName": safe_name,
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

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["displayName"] == safe_name


def test_create_tool_unsupported_type(tmp_path: typing.Any) -> None:
    """Test create_tool raises ValueError for unsupported tool type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()

    with pytest.raises(ValueError) as exc_info:  # noqa: PT011
        utils.create_tool("My Tool", app_dir, tool_type="INVALID_TYPE")
    assert "Unsupported tool type" in str(exc_info.value)


def test_create_tool_openapi_add_to_agent_error(tmp_path: typing.Any) -> None:
    """Test create_tool raises ValueError when adding OPENAPI tool to agent."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    agent_name = "My Agent"

    # Mock _get_agent to avoid reading files and parsing protobuf
    with mock.patch.object(utils, "_get_agent", return_value=mock.Mock()):  # noqa: SIM117
        with pytest.raises(ValueError) as exc_info:  # noqa: PT011
            utils.create_tool(
                "My Tool", app_dir, tool_type="OPENAPI", add_to_agent=agent_name
            )
    assert "Open API tool cannot be added to an agent" in str(exc_info.value)


def test_get_agent_missing_json_file(tmp_path: typing.Any) -> None:
    """Test _get_agent raises FileNotFoundError when json file is missing."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    agent_name = "My Agent"
    safe_name = "my_agent"

    # Create agent directory but not the json file
    (tmp_path / "agents" / safe_name).mkdir(parents=True)

    with pytest.raises(FileNotFoundError) as exc_info:
        utils.create_tool("My Tool", app_dir, add_to_agent=agent_name)
    assert "config not found at" in str(exc_info.value)


def test_validate_app_dir_success(tmp_path: typing.Any) -> None:
    """Test _validate_app_dir succeeds when both agents and tools exist."""
    utils = CreateUtils()
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    utils._validate_app_dir(str(tmp_path))


def test_validate_app_dir_missing_agents(tmp_path: typing.Any) -> None:
    """Test _validate_app_dir fails when agents/ is missing."""
    utils = CreateUtils()
    (tmp_path / "tools").mkdir()
    with pytest.raises(FileNotFoundError):
        utils._validate_app_dir(str(tmp_path))


def test_create_tool_add_to_agent(tmp_path: typing.Any) -> None:
    """Test create_tool with add_to_agent adds tool to agent's tools list."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    # Setup an agent first
    agent_name = "My Agent"
    agent_safe_name = "my_agent"
    agent_dir = agents_dir / agent_safe_name
    agent_dir.mkdir()
    agent_json_file = agent_dir / f"{agent_safe_name}.json"

    initial_agent_data = {"displayName": agent_name, "tools": []}
    with open(agent_json_file, "w", encoding="utf-8") as f:
        json.dump(initial_agent_data, f)

    agent_instruction_file = agent_dir / "instruction.txt"
    with open(agent_instruction_file, "w", encoding="utf-8") as f:
        f.write("<role>Test Agent Role</role>")

    display_name = "My Added Tool"
    safe_name = "my_added_tool"

    result_path = utils.create_tool(
        display_name,
        app_dir,
        tool_type="GOOGLE_SEARCH",
        add_to_agent=agent_name,
    )

    # Verify tool created
    assert Path(result_path) == tmp_path / "tools" / safe_name

    # Verify agent updated
    with open(agent_json_file, encoding="utf-8") as f:
        updated_agent = json.load(f)
        assert "tools" in updated_agent
        assert safe_name in updated_agent["tools"]

    # Verify instruction updated with tool reference
    with open(agent_instruction_file, encoding="utf-8") as f:
        updated_instruction = f.read()
        assert (
            f"<!-- Tool ref: {{@TOOL: {safe_name}}} -->" in updated_instruction
        )


def test_create_guardrail_llm_policy(tmp_path: typing.Any) -> None:
    """Test create_guardrail creates directory and JSON correctly."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Guardrail"
    safe_name = "my_test_guardrail"

    result_path = utils.create_guardrail(display_name, app_dir)

    target_dir = tmp_path / "guardrails" / safe_name
    assert Path(result_path) == target_dir
    assert target_dir.exists()

    json_file = target_dir / f"{safe_name}.json"
    assert json_file.exists()

    with open(json_file) as f:
        data = json.load(f)
        assert data["displayName"] == display_name
        assert data["enabled"] is True
        assert "llmPolicy" in data
        assert data["llmPolicy"]["policyScope"] == "AGENT_RESPONSE"
        assert "CRITICAL RULE" in data["llmPolicy"]["prompt"]
        assert "TRIGGER CRITERIA" in data["llmPolicy"]["prompt"]
        assert "DO NOT FLAG" in data["llmPolicy"]["prompt"]


def test_create_guardrail_adds_to_app_json(tmp_path: typing.Any) -> None:
    """Test create_guardrail adds display name to app.json guardrails list."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()

    app_json = tmp_path / "app.json"
    with open(app_json, "w") as f:
        json.dump({"displayName": "My App", "guardrails": ["Existing"]}, f)

    utils.create_guardrail("New Guardrail", app_dir)

    with open(app_json) as f:
        app_data = json.load(f)
    assert "New Guardrail" in app_data["guardrails"]
    assert "Existing" in app_data["guardrails"]


def test_create_guardrail_creates_guardrails_key_in_app_json(
    tmp_path: typing.Any,
) -> None:
    """Test create_guardrail creates guardrails key if missing from app.json."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()

    app_json = tmp_path / "app.json"
    with open(app_json, "w") as f:
        json.dump({"displayName": "My App"}, f)

    utils.create_guardrail("New Guardrail", app_dir)

    with open(app_json) as f:
        app_data = json.load(f)
    assert app_data["guardrails"] == ["New Guardrail"]


def test_create_guardrail_already_exists(tmp_path: typing.Any) -> None:
    """Test create_guardrail raises FileExistsError when directory exists."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()
    display_name = "My Test Guardrail"
    safe_name = "my_test_guardrail"

    (tmp_path / "guardrails" / safe_name).mkdir(parents=True)

    with pytest.raises(FileExistsError) as exc_info:
        utils.create_guardrail(display_name, app_dir)
    assert "already exists" in str(exc_info.value)


def test_create_guardrail_unsupported_type(tmp_path: typing.Any) -> None:
    """Test create_guardrail raises ValueError for unsupported type."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir()

    with pytest.raises(ValueError) as exc_info:  # noqa: PT011
        utils.create_guardrail(
            "My Guardrail", app_dir, guardrail_type="INVALID"
        )
    assert "Unsupported guardrail type" in str(exc_info.value)


def test_create_tool_add_to_agent_missing(tmp_path: typing.Any) -> None:
    """Test create_tool with missing add_to_agent raises FileNotFoundError."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    display_name = "My Tool"
    safe_name = "my_tool"
    mock_dict = {"displayName": safe_name}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )

    with mock.patch(patch_path, return_value=mock_dict):  # noqa: SIM117
        with pytest.raises(FileNotFoundError) as exc_info:
            utils.create_tool(
                display_name, app_dir, add_to_agent="Nonexistent Agent"
            )
    assert "config not found" in str(exc_info.value)


def test_get_safe_display_name_empty_raises_value_error() -> None:
    """Test _get_safe_display_name raises ValueError on empty sanitized name."""
    utils = CreateUtils()
    with pytest.raises(ValueError) as exc_info:  # noqa: PT011
        utils._get_safe_display_name("!!!")
    assert "must contain at least one alphanumeric character" in str(
        exc_info.value
    )


def test_get_safe_display_name_digit_prepends_underscore() -> None:
    """Test _get_safe_display_name prepends '_' if name starts with digit."""
    utils = CreateUtils()
    assert utils._get_safe_display_name("2nd Tool") == "_2nd_tool"


def test_get_safe_display_name_keyword_raises_value_error() -> None:
    """Test _get_safe_display_name raises ValueError on reserved keywords."""
    utils = CreateUtils()
    with pytest.raises(ValueError) as exc_info:  # noqa: PT011
        utils._get_safe_display_name("def")
    assert "reserved keyword" in str(exc_info.value)


def test_create_tool_already_exists_overwrites(tmp_path: typing.Any) -> None:
    """Test create_tool overwrites templates but keeps extra files intact."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    # Seed existing directory with an old file
    tool_dir = tmp_path / "tools" / "my_tool"
    tool_dir.mkdir(parents=True)
    old_file = tool_dir / "old_file.txt"
    with open(old_file, "w", encoding="utf-8") as f:
        f.write("obsolete content")

    display_name = "My Tool"
    safe_name = "my_tool"
    mock_dict = {"displayName": safe_name}
    patch_path = (
        "cxas_scrapi.utils.local.create_utils.json_format.MessageToDict"
    )

    with mock.patch(patch_path, return_value=mock_dict):
        result_path = utils.create_tool(
            display_name, app_dir, tool_type="GOOGLE_SEARCH"
        )

    assert Path(result_path) == tool_dir
    assert tool_dir.exists()

    # Verify old file remains completely intact during overwrite
    assert old_file.exists()

    # Verify new config file was generated
    assert (tool_dir / f"{safe_name}.json").exists()


def test_create_tool_add_to_agent_idempotency(tmp_path: typing.Any) -> None:
    """Test that create_tool does not add duplicate tools to agent config."""
    utils = CreateUtils()
    app_dir = str(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)

    agent_name = "My Agent"
    agent_safe_name = "my_agent"
    agent_dir = agents_dir / agent_safe_name
    agent_dir.mkdir()
    agent_json_file = agent_dir / f"{agent_safe_name}.json"

    # Seed agent with the tool already present
    initial_agent_data = {"displayName": agent_name, "tools": ["my_added_tool"]}
    with open(agent_json_file, "w", encoding="utf-8") as f:
        json.dump(initial_agent_data, f)

    display_name = "My Added Tool"
    safe_name = "my_added_tool"

    result_path = utils.create_tool(
        display_name,
        app_dir,
        tool_type="GOOGLE_SEARCH",
        add_to_agent=agent_name,
    )

    # Verify tool created
    assert Path(result_path) == tmp_path / "tools" / safe_name

    # Verify agent has exactly ONE reference (no duplicates appended)
    with open(agent_json_file, encoding="utf-8") as f:
        updated_agent = json.load(f)
        assert updated_agent["tools"] == ["my_added_tool"]
