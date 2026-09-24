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

"""Bidirectional YAML synchronization engine for Guided Agents."""

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def find_agent_json(agent_dir: Path | str) -> Path | None:
    """Finds agent.json or <agent_name>.json in agent_dir."""
    path = Path(agent_dir)
    if path.is_file():
        if path.suffix == ".json":
            return path
        return None
    if not path.is_dir():
        return None

    # Check for direct agent.json
    agent_json = path / "agent.json"
    if agent_json.is_file():
        return agent_json

    # Check for <dir_name>.json
    named_json = path / f"{path.name}.json"
    if named_json.is_file():
        return named_json

    # Check for any *.json that resembles an agent definition
    for candidate in sorted(path.glob("*.json")):
        if candidate.name.startswith(".") or candidate.name == "package.json":
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and (
                    "guidedAgent" in data
                    or "displayName" in data
                    or "name" in data
                ):
                    return candidate
        except Exception:
            continue

    # Fallback to any non-hidden json
    for candidate in sorted(path.glob("*.json")):
        if (
            not candidate.name.startswith(".")
            and candidate.name != "package.json"
        ):
            return candidate

    return None


def find_definition_yaml(agent_dir: Path | str) -> Path | None:
    """Finds definition.yaml in agent_dir."""
    path = Path(agent_dir)
    if path.is_file():
        if path.name in (
            "definition.yaml",
            "definition.yml",
        ) or path.suffix in (".yaml", ".yml"):
            return path
        path = path.parent
    if not path.is_dir():
        return None

    direct_yaml = path / "definition.yaml"
    if direct_yaml.is_file():
        return direct_yaml

    direct_yml = path / "definition.yml"
    if direct_yml.is_file():
        return direct_yml

    return None


def is_guided_agent(agent_data: dict[str, Any] | Path | str) -> bool:
    """Returns True if agent_data (or agent path) represents a Guided Agent.

    A Guided Agent defines 'guidedAgent' containing 'configSource' in
    its manifest, or has a definition.yaml present in the agent directory.
    """
    if isinstance(agent_data, dict):
        ga = agent_data.get("guidedAgent")
        return bool(
            isinstance(ga, dict) and "configSource" in ga and ga["configSource"]
        )

    path = Path(agent_data)
    if path.is_dir():
        if find_definition_yaml(path) is not None:
            return True
        agent_json = find_agent_json(path)
        if agent_json:
            return is_guided_agent(agent_json)
        return False

    if path.is_file():
        if path.name in (
            "definition.yaml",
            "definition.yml",
        ) or path.suffix in (".yaml", ".yml"):
            return True
        if path.suffix == ".json":
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    return is_guided_agent(data)
            except Exception:
                return False
        return False

    return False


def extract_tools_from_yaml(yaml_content: str | dict[str, Any]) -> list[str]:
    """Parses YAML content and extracts external tools in tools mapping.

    For each tool entry, uses tool_dict.get('external_tool', tool_key).
    Returns a sorted, deduplicated list of tool names.
    """
    if isinstance(yaml_content, str):
        try:
            parsed = yaml.safe_load(yaml_content)
        except Exception as e:
            logger.warning("Failed to parse YAML content: %s", e)
            return []
    elif isinstance(yaml_content, dict):
        parsed = yaml_content
    else:
        return []

    if not isinstance(parsed, dict):
        return []

    tools_section = parsed.get("tools")
    tool_names: set[str] = set()

    if isinstance(tools_section, dict):
        for tool_key, tool_val in tools_section.items():
            if isinstance(tool_val, dict):
                tool_name = tool_val.get("external_tool") or tool_key
            else:
                tool_name = tool_val or tool_key
            if tool_name:
                tool_names.add(str(tool_name).strip())
    elif isinstance(tools_section, list):
        for item in tools_section:
            if isinstance(item, str):
                tool_names.add(item.strip())
            elif isinstance(item, dict):
                tool_name = item.get("external_tool") or item.get("name")
                if tool_name:
                    tool_names.add(str(tool_name).strip())

    return sorted(tool_names)


def guided_agent_to_yaml(
    agent_dir: Path | str,
    output_path: Path | str | None = None,
) -> str:
    """Reads agent.json, verifies Guided Agent, and extracts definition.yaml.

    Writes to definition.yaml in agent_dir (or output_path) with UTF-8
    encoding and trailing newline.
    Returns the YAML string content.
    """
    path = Path(agent_dir)
    if path.is_file():
        agent_json_path = path
        target_dir = path.parent
    else:
        target_dir = path
        agent_json_path = find_agent_json(path)

    if not agent_json_path or not agent_json_path.is_file():
        raise FileNotFoundError(f"Agent JSON file not found in {agent_dir}")

    with open(agent_json_path, encoding="utf-8") as f:
        agent_data = json.load(f)

    if not is_guided_agent(agent_data):
        raise ValueError(
            f"Agent at {agent_json_path} is not a Guided Agent "
            "(missing guidedAgent.configSource)"
        )

    config_source = agent_data.get("guidedAgent", {}).get("configSource", {})
    yaml_content = config_source.get("inlineText", "")
    if yaml_content and not yaml_content.endswith("\n"):
        yaml_content += "\n"

    if output_path:
        out = Path(output_path)
        if out.is_dir():
            out = out / "definition.yaml"
    else:
        out = target_dir / "definition.yaml"

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(yaml_content)

    return yaml_content


def yaml_to_guided_agent(
    agent_dir: Path | str,
    yaml_path: Path | str | None = None,
) -> dict[str, Any]:
    """Reads definition.yaml (or yaml_path) and updates agent.json.

    - Reads existing agent.json (or initializes minimal guided agent structure).
    - Removes 'instruction' field if present.
    - Updates guidedAgent.configSource.inlineText with YAML content.
    - Synchronizes tools list from YAML in sorted order.
    - Writes updated agent.json with 2-space indentation and trailing newline.
    - Returns updated agent_data dict.
    """
    if yaml_path:
        yaml_file = Path(yaml_path)
    else:
        path = Path(agent_dir)
        if path.is_file() and path.suffix in (".yaml", ".yml"):
            yaml_file = path
        else:
            yaml_file = find_definition_yaml(path)

    if not yaml_file or not yaml_file.is_file():
        raise FileNotFoundError(f"No definition YAML found for {agent_dir}")

    with open(yaml_file, encoding="utf-8") as f:
        yaml_content = f.read()

    # Determine agent.json location
    path = Path(agent_dir)
    if path.is_file():
        if path.suffix == ".json":
            agent_json_path = path
            target_dir = path.parent
        else:
            target_dir = path.parent
            agent_json_path = find_agent_json(target_dir) or (
                target_dir / "agent.json"
            )
    else:
        target_dir = path
        agent_json_path = find_agent_json(target_dir) or (
            target_dir / "agent.json"
        )

    if agent_json_path.is_file():
        with open(agent_json_path, encoding="utf-8") as f:
            agent_data = json.load(f)
    else:
        agent_data = {
            "displayName": target_dir.name,
            "guidedAgent": {},
        }

    # Ensure instruction field is removed (critical platform rule)
    agent_data.pop("instruction", None)

    # Update guidedAgent.configSource
    if "guidedAgent" not in agent_data or not isinstance(
        agent_data["guidedAgent"], dict
    ):
        agent_data["guidedAgent"] = {}
    agent_data["guidedAgent"]["configSource"] = {
        "format": "YAML",
        "inlineText": yaml_content,
    }

    # Extract declared external tools and update tools list in sorted order
    sorted_tools = extract_tools_from_yaml(yaml_content)
    agent_data["tools"] = sorted_tools

    # Write updated agent.json
    agent_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(agent_json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(agent_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return agent_data


def is_guided_agent_synced(agent_dir: Path | str) -> tuple[bool, str]:
    """Compares definition.yaml against agent.json inlineText and tools list.

    Returns (True, "") if synchronized, or (False, diff) if out of sync.
    """
    path = Path(agent_dir)
    if not path.exists():
        return False, f"Directory or file does not exist: {agent_dir}"

    yaml_path = find_definition_yaml(path)
    if not yaml_path or not yaml_path.is_file():
        return False, f"Missing definition.yaml in {agent_dir}"

    agent_json_path = find_agent_json(path)
    if not agent_json_path or not agent_json_path.is_file():
        return False, f"Missing agent.json in {agent_dir}"

    try:
        with open(agent_json_path, encoding="utf-8") as f:
            agent_data = json.load(f)
    except Exception as e:
        return False, f"Failed to parse {agent_json_path}: {e}"

    if not is_guided_agent(agent_data):
        return (
            False,
            f"Agent at {agent_json_path} is not a Guided Agent "
            "(missing guidedAgent.configSource)",
        )

    with open(yaml_path, encoding="utf-8") as f:
        yaml_content = f.read()

    inline_text = (
        agent_data.get("guidedAgent", {})
        .get("configSource", {})
        .get("inlineText", "")
    )

    # Compare inlineText vs definition.yaml
    if yaml_content != inline_text:
        diff = list(
            difflib.unified_diff(
                inline_text.splitlines(keepends=True),
                yaml_content.splitlines(keepends=True),
                fromfile=f"{agent_json_path.name}:inlineText",
                tofile=yaml_path.name,
            )
        )
        return (
            False,
            "YAML definition diverges from agent.json inlineText:\n"
            + "".join(diff),
        )

    # Compare tools list
    yaml_tools = extract_tools_from_yaml(yaml_content)
    json_tools = agent_data.get("tools", [])
    if yaml_tools != json_tools:
        return (
            False,
            f"Tools mismatch: definition.yaml declares {yaml_tools} "
            f"but agent.json has {json_tools}",
        )

    # Verify no invalid instruction field exists
    if "instruction" in agent_data:
        return (
            False,
            "agent.json contains forbidden 'instruction' field for Guided"
            " Agent",
        )

    return True, ""
