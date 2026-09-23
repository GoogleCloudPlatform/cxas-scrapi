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

"""Unit tests for cxas_scrapi.utils.agent_yaml.

Tests Guided Agent YAML serialization, extraction, tool synchronization,
linter drift verification, and Pillar 1 & 2 round-trip invertibility.
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from cxas_scrapi.utils.agent_yaml import (
    extract_tools_from_yaml,
    find_agent_json,
    find_definition_yaml,
    guided_agent_to_yaml,
    is_guided_agent,
    is_guided_agent_synced,
    yaml_to_guided_agent,
)

FIXTURE_CHARTER_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "guided_agents"
    / "charter_appointment_agent"
)


# ============================================================================
# 1. Tests for is_guided_agent
# ============================================================================


def test_is_guided_agent_dict_valid() -> None:
    valid_data = {
        "displayName": "Test Agent",
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": "agent: Test Agent\n",
            }
        },
    }
    assert is_guided_agent(valid_data) is True


def test_is_guided_agent_dict_invalid() -> None:
    # Playbook agent with instruction only
    playbook_data = {
        "displayName": "Playbook Agent",
        "instruction": "You are a helpful assistant.",
        "tools": [],
    }
    assert is_guided_agent(playbook_data) is False

    # Empty guidedAgent dict
    empty_ga = {"guidedAgent": {}}
    assert is_guided_agent(empty_ga) is False

    # None or empty configSource
    none_cs = {"guidedAgent": {"configSource": None}}
    assert is_guided_agent(none_cs) is False

    empty_cs = {"guidedAgent": {"configSource": {}}}
    assert is_guided_agent(empty_cs) is False

    # Empty dict
    assert is_guided_agent({}) is False


def test_is_guided_agent_paths_charter_fixture() -> None:
    assert FIXTURE_CHARTER_DIR.exists()
    assert is_guided_agent(FIXTURE_CHARTER_DIR) is True
    assert is_guided_agent(FIXTURE_CHARTER_DIR / "agent.json") is True
    assert (
        is_guided_agent(FIXTURE_CHARTER_DIR / "Appointment_agent.json") is True
    )
    assert is_guided_agent(FIXTURE_CHARTER_DIR / "definition.yaml") is True


def test_is_guided_agent_paths_playbook_and_nonexistent(tmp_path: Path) -> None:
    # Directory with conventional playbook agent
    playbook_dir = tmp_path / "playbook_agent"
    playbook_dir.mkdir()
    (playbook_dir / "agent.json").write_text(
        json.dumps({"displayName": "Playbook", "instruction": "Hello"}),
        encoding="utf-8",
    )
    assert is_guided_agent(playbook_dir) is False
    assert is_guided_agent(playbook_dir / "agent.json") is False

    # Non-existent path
    assert is_guided_agent(tmp_path / "nonexistent") is False

    # Non-agent json file
    other_json = tmp_path / "other.json"
    other_json.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    assert is_guided_agent(other_json) is False


def test_is_guided_agent_directory_with_only_definition_yaml(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "yaml_only_agent"
    agent_dir.mkdir()
    (agent_dir / "definition.yaml").write_text(
        "agent: Test\n", encoding="utf-8"
    )
    assert is_guided_agent(agent_dir) is True


# ============================================================================
# 2. Tests for find_agent_json and find_definition_yaml
# ============================================================================


def test_find_agent_json_charter_fixture() -> None:
    found = find_agent_json(FIXTURE_CHARTER_DIR)
    assert found is not None
    assert found.name in ("agent.json", "Appointment_agent.json")
    assert found.is_file()


def test_find_agent_json_named_json(tmp_path: Path) -> None:
    agent_dir = tmp_path / "Custom_agent"
    agent_dir.mkdir()
    named_file = agent_dir / "Custom_agent.json"
    named_file.write_text(
        json.dumps({"displayName": "Custom"}), encoding="utf-8"
    )

    found = find_agent_json(agent_dir)
    assert found == named_file


def test_find_agent_json_direct_file(tmp_path: Path) -> None:
    target_file = tmp_path / "my_agent.json"
    target_file.write_text(
        json.dumps({"displayName": "Direct"}), encoding="utf-8"
    )
    assert find_agent_json(target_file) == target_file

    non_json = tmp_path / "my_file.txt"
    non_json.write_text("hello", encoding="utf-8")
    assert find_agent_json(non_json) is None


def test_find_agent_json_none_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert find_agent_json(empty_dir) is None


def test_find_definition_yaml(tmp_path: Path) -> None:
    assert (
        find_definition_yaml(FIXTURE_CHARTER_DIR)
        == FIXTURE_CHARTER_DIR / "definition.yaml"
    )

    # .yml fallback
    yml_dir = tmp_path / "yml_agent"
    yml_dir.mkdir()
    (yml_dir / "definition.yml").write_text("agent: Yml\n", encoding="utf-8")
    assert find_definition_yaml(yml_dir) == yml_dir / "definition.yml"

    # Direct file
    direct_yaml = tmp_path / "custom.yaml"
    direct_yaml.write_text("agent: Custom\n", encoding="utf-8")
    assert find_definition_yaml(direct_yaml) == direct_yaml

    # Empty dir
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert find_definition_yaml(empty_dir) is None


# ============================================================================
# 3. Tests for extract_tools_from_yaml
# ============================================================================


def test_extract_tools_from_yaml_charter_fixture() -> None:
    definition_yaml = (FIXTURE_CHARTER_DIR / "definition.yaml").read_text(
        encoding="utf-8"
    )
    tools = extract_tools_from_yaml(definition_yaml)

    expected_tools = [
        "book_new_appointment",
        "call_wrap_up",
        "cancel_appointment",
        "emergency_and_missed_status_check",
        "escalate_to_dispatch",
        "escalation_call",
        "get_appointment_details",
        "get_available_schedule_windows",
        "get_rescheduling_eligibility",
        "modify_appointment",
        "select_appointment",
        "send_tracker_link",
    ]
    assert tools == expected_tools


def test_extract_tools_from_yaml_external_tool_mapping() -> None:
    sample_yaml = """
tools:
  internal_lookup:
    external_tool: real_lookup_api
  direct_tool:
    params: { id: string }
  simple_tool: null
"""
    tools = extract_tools_from_yaml(sample_yaml)
    assert tools == ["direct_tool", "real_lookup_api", "simple_tool"]


def test_extract_tools_from_yaml_dict_input() -> None:
    parsed_dict: dict[str, Any] = {
        "tools": {
            "tool_b": {"external_tool": "b_tool"},
            "tool_a": {"external_tool": "a_tool"},
        }
    }
    tools = extract_tools_from_yaml(parsed_dict)
    assert tools == ["a_tool", "b_tool"]


def test_extract_tools_from_yaml_deduplication() -> None:
    sample_yaml = """
tools:
  alias_one:
    external_tool: shared_tool
  alias_two:
    external_tool: shared_tool
  shared_tool:
    external_tool: shared_tool
"""
    tools = extract_tools_from_yaml(sample_yaml)
    assert tools == ["shared_tool"]


def test_extract_tools_from_yaml_edge_cases() -> None:
    assert extract_tools_from_yaml("") == []
    assert extract_tools_from_yaml("agent: No tools\n") == []
    assert extract_tools_from_yaml("tools: {}\n") == []
    assert extract_tools_from_yaml("tools: null\n") == []
    assert extract_tools_from_yaml("::: invalid yaml") == []
    assert extract_tools_from_yaml({"tools": []}) == []


# ============================================================================
# 4. Tests for guided_agent_to_yaml
# ============================================================================


def test_guided_agent_to_yaml_charter_fixture(tmp_path: Path) -> None:
    # Copy fixture agent.json to tmp_path
    agent_dir = tmp_path / "charter_agent"
    agent_dir.mkdir()
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "agent.json", agent_dir / "agent.json"
    )

    extracted_yaml = guided_agent_to_yaml(agent_dir)
    written_yaml_path = agent_dir / "definition.yaml"

    assert written_yaml_path.exists()
    assert written_yaml_path.read_text(encoding="utf-8") == extracted_yaml

    # Parity check with golden definition.yaml
    golden_yaml = (FIXTURE_CHARTER_DIR / "definition.yaml").read_text(
        encoding="utf-8"
    )
    assert extracted_yaml == golden_yaml
    assert extracted_yaml.endswith("\n")


def test_guided_agent_to_yaml_custom_output_path(tmp_path: Path) -> None:
    custom_output = tmp_path / "custom" / "my_definition.yaml"
    extracted_yaml = guided_agent_to_yaml(
        FIXTURE_CHARTER_DIR / "agent.json", output_path=custom_output
    )

    assert custom_output.exists()
    assert custom_output.read_text(encoding="utf-8") == extracted_yaml


def test_guided_agent_to_yaml_error_not_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="Agent JSON file not found"):
        guided_agent_to_yaml(empty_dir)


def test_guided_agent_to_yaml_error_not_guided(tmp_path: Path) -> None:
    playbook_dir = tmp_path / "playbook"
    playbook_dir.mkdir()
    (playbook_dir / "agent.json").write_text(
        json.dumps({"displayName": "Playbook", "instruction": "You are a bot"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a Guided Agent"):
        guided_agent_to_yaml(playbook_dir)


# ============================================================================
# 5. Tests for yaml_to_guided_agent
# ============================================================================


def test_yaml_to_guided_agent_existing_agent(tmp_path: Path) -> None:
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()

    initial_json: dict[str, Any] = {
        "name": "12345-abcde",
        "displayName": "Test Agent",
        "description": "Initial description",
        "instruction": "This should be purged!",
        "tools": ["old_tool"],
        "guidedAgent": {},
    }
    (agent_dir / "agent.json").write_text(
        json.dumps(initial_json, indent=2), encoding="utf-8"
    )

    yaml_content = """agent: "Test Agent"
tools:
  new_tool_z:
    external_tool: tool_z
  new_tool_a:
    external_tool: tool_a
"""
    (agent_dir / "definition.yaml").write_text(yaml_content, encoding="utf-8")

    updated_data = yaml_to_guided_agent(agent_dir)

    # 1. 'instruction' field must be completely removed
    assert "instruction" not in updated_data

    # 2. Top-level metadata preserved
    assert updated_data["name"] == "12345-abcde"
    assert updated_data["displayName"] == "Test Agent"
    assert updated_data["description"] == "Initial description"

    # 3. inlineText updated with YAML content
    config_source = updated_data["guidedAgent"]["configSource"]
    assert config_source["format"] == "YAML"
    assert config_source["inlineText"] == yaml_content

    # 4. tools list synchronized in sorted order
    assert updated_data["tools"] == ["tool_a", "tool_z"]

    # 5. agent.json on disk has 2-space indentation and trailing newline
    written_text = (agent_dir / "agent.json").read_text(encoding="utf-8")
    assert written_text.endswith("\n")
    assert json.loads(written_text) == updated_data


def test_yaml_to_guided_agent_initializes_minimal(tmp_path: Path) -> None:
    agent_dir = tmp_path / "New_Guided_Agent"
    agent_dir.mkdir()
    yaml_content = (
        "agent: New Agent\ntools:\n  tool_1:\n    external_tool: tool_1\n"
    )
    (agent_dir / "definition.yaml").write_text(yaml_content, encoding="utf-8")

    data = yaml_to_guided_agent(agent_dir)
    assert data["displayName"] == "New_Guided_Agent"
    assert "instruction" not in data
    assert data["tools"] == ["tool_1"]
    assert data["guidedAgent"]["configSource"]["inlineText"] == yaml_content


def test_yaml_to_guided_agent_custom_yaml_path(tmp_path: Path) -> None:
    agent_dir = tmp_path / "custom_agent"
    agent_dir.mkdir()
    custom_yaml = tmp_path / "external_definition.yaml"
    custom_yaml.write_text("agent: External\n", encoding="utf-8")

    data = yaml_to_guided_agent(agent_dir, yaml_path=custom_yaml)
    assert (
        data["guidedAgent"]["configSource"]["inlineText"] == "agent: External\n"
    )


# ============================================================================
# 6. Pillar 1 & 2 Round-Trip Invertibility & Parity Tests
# ============================================================================


def test_pillar_1_and_2_round_trip_invertibility(tmp_path: Path) -> None:
    """Verifies lossless round-trip parity on Charter fixture.

    Pillar 1: Invertibility (Lossless Translation)
      yaml_to_guided_agent(guided_agent_to_yaml(agent.json)) == agent.json
      guided_agent_to_yaml(yaml_to_guided_agent(def.yaml)) == def.yaml

    Pillar 2: Semantic Fidelity (Golden Diffing)
      0-diff parity between original and round-trip representations.
    """
    work_dir = tmp_path / "charter_roundtrip"
    work_dir.mkdir()

    orig_agent_json_path = FIXTURE_CHARTER_DIR / "agent.json"
    orig_definition_yaml_path = FIXTURE_CHARTER_DIR / "definition.yaml"

    shutil.copyfile(orig_agent_json_path, work_dir / "agent.json")
    shutil.copyfile(orig_definition_yaml_path, work_dir / "definition.yaml")

    with open(orig_agent_json_path, encoding="utf-8") as f:
        orig_agent_data = json.load(f)
    orig_yaml_text = orig_definition_yaml_path.read_text(encoding="utf-8")

    # Forward Trip 1: JSON -> YAML
    extracted_yaml = guided_agent_to_yaml(work_dir)
    assert extracted_yaml == orig_yaml_text, (
        "Pillar 2 Failure: Extracted YAML differs from golden"
    )

    # Backward Trip 1: YAML -> JSON
    compiled_agent_data = yaml_to_guided_agent(work_dir)
    assert compiled_agent_data == orig_agent_data, (
        "Pillar 1 Failure: Compiled agent.json differs from golden"
    )

    # Forward Trip 2: Compiled JSON -> YAML
    re_extracted_yaml = guided_agent_to_yaml(work_dir)
    assert re_extracted_yaml == orig_yaml_text, (
        "Pillar 1 Failure: Re-extracted YAML differs"
    )

    # Verify 0-line diff on disk files
    written_json_text = (work_dir / "agent.json").read_text(encoding="utf-8")
    orig_json_text = orig_agent_json_path.read_text(encoding="utf-8")
    json_diff = list(
        difflib.unified_diff(
            orig_json_text.splitlines(keepends=True),
            written_json_text.splitlines(keepends=True),
        )
    )
    assert not json_diff, (
        f"Unexpected diff in agent.json:\n{''.join(json_diff)}"
    )

    written_yaml_text = (work_dir / "definition.yaml").read_text(
        encoding="utf-8"
    )
    yaml_diff = list(
        difflib.unified_diff(
            orig_yaml_text.splitlines(keepends=True),
            written_yaml_text.splitlines(keepends=True),
        )
    )
    assert not yaml_diff, (
        f"Unexpected diff in definition.yaml:\n{''.join(yaml_diff)}"
    )


# ============================================================================
# 7. Tests for is_guided_agent_synced
# ============================================================================


def test_is_guided_agent_synced_fixture_in_sync() -> None:
    is_synced, diff = is_guided_agent_synced(FIXTURE_CHARTER_DIR)
    assert is_synced is True
    assert diff == ""


def test_is_guided_agent_synced_yaml_modified(tmp_path: Path) -> None:
    work_dir = tmp_path / "modified_yaml_agent"
    work_dir.mkdir()
    shutil.copyfile(FIXTURE_CHARTER_DIR / "agent.json", work_dir / "agent.json")
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "definition.yaml", work_dir / "definition.yaml"
    )

    # Modify definition.yaml
    yaml_path = work_dir / "definition.yaml"
    modified_content = yaml_path.read_text(encoding="utf-8").replace(
        "Thanks for calling Spectrum.", "Welcome to Spectrum Field Service."
    )
    yaml_path.write_text(modified_content, encoding="utf-8")

    is_synced, diff = is_guided_agent_synced(work_dir)
    assert is_synced is False
    assert "YAML definition diverges from agent.json inlineText" in diff
    assert '+  greeting: "Welcome to Spectrum Field Service.' in diff


def test_is_guided_agent_synced_tools_mismatch(tmp_path: Path) -> None:
    work_dir = tmp_path / "tools_mismatch_agent"
    work_dir.mkdir()
    shutil.copyfile(FIXTURE_CHARTER_DIR / "agent.json", work_dir / "agent.json")
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "definition.yaml", work_dir / "definition.yaml"
    )

    # Modify agent.json tools to be missing a tool
    with open(work_dir / "agent.json", encoding="utf-8") as f:
        data = json.load(f)
    data["tools"] = data["tools"][:-1]  # remove send_tracker_link
    with open(work_dir / "agent.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    is_synced, diff = is_guided_agent_synced(work_dir)
    assert is_synced is False
    assert "Tools mismatch" in diff


def test_is_guided_agent_synced_forbidden_instruction(tmp_path: Path) -> None:
    work_dir = tmp_path / "instruction_present_agent"
    work_dir.mkdir()
    shutil.copyfile(FIXTURE_CHARTER_DIR / "agent.json", work_dir / "agent.json")
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "definition.yaml", work_dir / "definition.yaml"
    )

    # Inject forbidden instruction field
    with open(work_dir / "agent.json", encoding="utf-8") as f:
        data = json.load(f)
    data["instruction"] = "Disallowed instruction field"
    with open(work_dir / "agent.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    is_synced, diff = is_guided_agent_synced(work_dir)
    assert is_synced is False
    assert "forbidden 'instruction' field" in diff


def test_is_guided_agent_synced_missing_files(tmp_path: Path) -> None:
    # Missing definition.yaml
    no_yaml_dir = tmp_path / "no_yaml"
    no_yaml_dir.mkdir()
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "agent.json", no_yaml_dir / "agent.json"
    )
    is_synced, diff = is_guided_agent_synced(no_yaml_dir)
    assert is_synced is False
    assert "Missing definition.yaml" in diff

    # Missing agent.json
    no_json_dir = tmp_path / "no_json"
    no_json_dir.mkdir()
    shutil.copyfile(
        FIXTURE_CHARTER_DIR / "definition.yaml", no_json_dir / "definition.yaml"
    )
    is_synced, diff = is_guided_agent_synced(no_json_dir)
    assert is_synced is False
    assert "Missing agent.json" in diff

    # Non-existent dir
    is_synced, diff = is_guided_agent_synced(tmp_path / "does_not_exist")
    assert is_synced is False
    assert "Directory or file does not exist" in diff

    # Malformed JSON
    bad_json_dir = tmp_path / "bad_json"
    bad_json_dir.mkdir()
    (bad_json_dir / "definition.yaml").write_text(
        "agent: Bad\n", encoding="utf-8"
    )
    (bad_json_dir / "agent.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    is_synced, diff = is_guided_agent_synced(bad_json_dir)
    assert is_synced is False
    assert "Failed to parse" in diff

    # Not a guided agent (conventional agent in agent.json)
    conventional_dir = tmp_path / "conventional"
    conventional_dir.mkdir()
    (conventional_dir / "definition.yaml").write_text(
        "agent: Conv\n", encoding="utf-8"
    )
    (conventional_dir / "agent.json").write_text(
        json.dumps({"displayName": "Conv", "instruction": "playbook"}),
        encoding="utf-8",
    )
    is_synced, diff = is_guided_agent_synced(conventional_dir)
    assert is_synced is False
    assert "not a Guided Agent" in diff


# ============================================================================
# 8. Tests for Edge Cases & Full Coverage
# ============================================================================


def test_find_agent_json_edge_cases(tmp_path: Path) -> None:
    # Non-existent path
    assert find_agent_json(tmp_path / "does_not_exist") is None

    # Directory with package.json, hidden file, and corrupt json
    noisy_dir = tmp_path / "noisy"
    noisy_dir.mkdir()
    (noisy_dir / "package.json").write_text("{}", encoding="utf-8")
    (noisy_dir / ".hidden.json").write_text("{}", encoding="utf-8")
    (noisy_dir / "corrupt.json").write_text("{bad json", encoding="utf-8")

    # Ignore package.json and hidden, skip corrupt, return None if no valid
    assert find_agent_json(noisy_dir) == noisy_dir / "corrupt.json"

    # Add a valid agent json
    (noisy_dir / "valid_agent.json").write_text(
        json.dumps({"displayName": "Valid"}), encoding="utf-8"
    )
    assert find_agent_json(noisy_dir) == noisy_dir / "valid_agent.json"


def test_find_definition_yaml_edge_cases(tmp_path: Path) -> None:
    # Non-existent path
    assert find_definition_yaml(tmp_path / "does_not_exist") is None

    # Direct non-yaml file
    txt_file = tmp_path / "info.txt"
    txt_file.write_text("info", encoding="utf-8")
    assert find_definition_yaml(txt_file) is None


def test_extract_tools_from_yaml_list_of_dicts_and_syntax_error() -> None:
    # List of dicts in tools
    tools_list = {
        "tools": [
            {"external_tool": "tool_x"},
            {"name": "tool_y"},
            "plain_tool",
            {"unnamed": "skip"},
        ]
    }
    extracted = extract_tools_from_yaml(tools_list)
    assert extracted == ["plain_tool", "tool_x", "tool_y"]

    # Syntax error in YAML string
    assert extract_tools_from_yaml("tools:\n  bad: [unclosed") == []


def test_guided_agent_to_yaml_output_dir_and_no_newline(tmp_path: Path) -> None:
    agent_dir = tmp_path / "no_newline_agent"
    agent_dir.mkdir()
    (agent_dir / "agent.json").write_text(
        json.dumps(
            {
                "displayName": "No Newline",
                "guidedAgent": {
                    "configSource": {
                        # Notice no trailing \n
                        "inlineText": "agent: No Newline",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Output to an existing directory instead of a file
    output_dir = tmp_path / "target_dir"
    output_dir.mkdir()

    content = guided_agent_to_yaml(agent_dir, output_path=output_dir)
    assert content == "agent: No Newline\n"
    assert (output_dir / "definition.yaml").read_text(
        encoding="utf-8"
    ) == "agent: No Newline\n"


def test_yaml_to_guided_agent_direct_file_path_and_missing(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "direct_file_agent"
    agent_dir.mkdir()
    yaml_file = agent_dir / "definition.yaml"
    yaml_file.write_text("agent: Direct\n", encoding="utf-8")
    agent_json_file = agent_dir / "agent.json"
    agent_json_file.write_text(
        json.dumps({"displayName": "Direct"}), encoding="utf-8"
    )

    # Pass agent.json path directly instead of directory
    result = yaml_to_guided_agent(agent_json_file)
    assert result["displayName"] == "Direct"
    assert (
        result["guidedAgent"]["configSource"]["inlineText"] == "agent: Direct\n"
    )

    # Missing definition.yaml
    empty_dir = tmp_path / "missing_yaml"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="No definition YAML found"):
        yaml_to_guided_agent(empty_dir)


def test_guided_agent_special_characters_and_formatting(tmp_path: Path) -> None:
    """Verifies unicode, emojis, and multiline blocks preserve fidelity."""
    complex_yaml = """agent: "Complex Specialist 🤖"
description: "Handles international characters: ñ, ü, é, 中文, 日本語, العربية"

metadata:
  persona: >-
    Here's a complex multi-line persona with "quotes", 'single quotes',
    colons: inside text, and special symbols: # @ $ % ^ & * () _ +
  block_scalar: |
    Line 1: Special chars \t \\ / " '
    Line 2: 🚀 💳 🔒
tools:
  international_tool:
    external_tool: "special_tool_äöü"
"""
    agent_dir = tmp_path / "complex_agent"
    agent_dir.mkdir()
    (agent_dir / "definition.yaml").write_text(complex_yaml, encoding="utf-8")

    # Compile to JSON
    agent_data = yaml_to_guided_agent(agent_dir)
    assert agent_data["tools"] == ["special_tool_äöü"]
    assert (
        agent_data["guidedAgent"]["configSource"]["inlineText"] == complex_yaml
    )

    # Extract back to YAML
    extracted_yaml = guided_agent_to_yaml(agent_dir)
    assert extracted_yaml == complex_yaml

    # Verify sync
    synced, diff = is_guided_agent_synced(agent_dir)
    assert synced is True
    assert diff == ""


def test_is_guided_agent_corrupt_json_and_yaml_direct(tmp_path: Path) -> None:
    # Corrupt json file
    corrupt_json = tmp_path / "corrupt.json"
    corrupt_json.write_text("{invalid json", encoding="utf-8")
    assert is_guided_agent(corrupt_json) is False

    # Direct yaml file passed as agent_dir to yaml_to_guided_agent
    agent_dir = tmp_path / "yaml_dir"
    agent_dir.mkdir()
    yaml_file = agent_dir / "definition.yaml"
    yaml_file.write_text("agent: Direct YAML\n", encoding="utf-8")

    result = yaml_to_guided_agent(yaml_file)
    assert (
        result["guidedAgent"]["configSource"]["inlineText"]
        == "agent: Direct YAML\n"
    )
    assert (agent_dir / "agent.json").exists()
