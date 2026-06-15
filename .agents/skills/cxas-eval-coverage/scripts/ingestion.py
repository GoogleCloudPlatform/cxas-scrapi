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

"""Data ingestion module for GECX evaluation coverage analyzer."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Set, Tuple

import yaml
from utils import find_target_agent, parse_instruction_content
from models import AgentProjectData

def _append_expectations(
    data: AgentProjectData,
    expectations: List[str],
    prefix: str,
    eval_name: str,
    file_name: str,
) -> None:
    """Appends expectation criteria to the evaluation chunks list.

    Args:
        data: The unified AgentProjectData instance being populated.
        expectations: A list of expectation text strings.
        prefix: A string prefix identifying the evaluation type.
        eval_name: The display name of the evaluation.
        file_name: The name of the file containing the evaluation.
    """
    if expectations:
        exp_lines = "\n".join(f"- {exp}" for exp in expectations)
        data.eval_chunks.append(
            {
                "text": (
                    f"{prefix}: {eval_name}\n"
                    "Expectations:\n"
                    f"{exp_lines}"
                ),
                "eval_name": eval_name,
                "file_name": file_name,
            }
        )


def find_tools_local(tools_dir: Path) -> Set[str]:
    """Finds all declared tool names in the tools directory.

    Args:
        tools_dir: The Path to the agent project's tools directory.

    Returns:
        A set of unique tool names discovered in the tools directory.
    """
    tools = set()
    if not tools_dir.exists():
        return tools

    for p in tools_dir.glob("**/*"):
        if p.suffix in (".json", ".yaml", ".yml"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = (
                        yaml.safe_load(f)
                        if p.suffix in (".yaml", ".yml")
                        else json.load(f)
                    )

                tool_name = p.stem
                if isinstance(content, dict):
                    if "displayName" in content:
                        tool_name = content["displayName"]
                    elif "name" in content:
                        name_val = content["name"]
                        if not re.match(r"^[0-9a-fA-F-]{36}$", str(name_val)):
                            tool_name = name_val
                tools.add(tool_name)
            except (json.JSONDecodeError, yaml.YAMLError, OSError):
                tools.add(p.stem)
    return tools


def ingest_agent_project(agent_dir: Path) -> AgentProjectData:
    """Ingests and parses all agent files, building AgentProjectData.

    Args:
        agent_dir: The Path to the root directory of the GECX agent project.

    Returns:
        A populated AgentProjectData container with parsed tools, evaluations,
        transfers, and instructions.
    """
    data = AgentProjectData(agent_dir=agent_dir)

    # Find declared tools
    tools_dir = agent_dir / "tools"
    data.all_tools = find_tools_local(tools_dir)

    # Find all evaluation and test files
    eval_dir = agent_dir / "evaluations"
    eval_dataset_dir = agent_dir / "evaluationDatasets"
    tool_tests_dir = agent_dir / "tool_tests"
    evals_dir = agent_dir / "evals"
    tests_dir = agent_dir / "tests"

    seen_eval_files: Set[Path] = set()
    directories = (
        eval_dir,
        eval_dataset_dir,
        tool_tests_dir,
        evals_dir,
        tests_dir,
    )
    for d in directories:
        if d.exists():
            for p in d.glob("**/*"):
                if p.is_file() and p.suffix in (".json", ".yaml", ".yml"):
                    if p not in seen_eval_files:
                        seen_eval_files.add(p)
                        data.eval_files.append(p)

    for p in agent_dir.glob("*.yaml"):
        if p.is_file() and p not in seen_eval_files:
            seen_eval_files.add(p)
            data.eval_files.append(p)
    for p in agent_dir.glob("*.yml"):
        if p.is_file() and p not in seen_eval_files:
            seen_eval_files.add(p)
            data.eval_files.append(p)

    unit_tested_tools: Set[str] = set()

    # Discover declared Agent Transfers from Agents config
    agent_files = []
    for ext in ("*.json", "*.yaml", "*.yml"):
        agent_files.extend((agent_dir / "agents").glob(f"**/{ext}"))

    agents = {}
    root_agents = set()
    for af in agent_files:
        try:
            with open(af, "r", encoding="utf-8") as f:
                if af.suffix in (".yaml", ".yml"):
                    agent_data = yaml.safe_load(f)
                else:
                    agent_data = json.load(f)
            display_name = agent_data.get("displayName")
            agents[display_name] = agent_data
            root_agents.add(display_name)
            data.agent_directories[display_name] = af.parent
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            pass

    for display_name, agent_data in agents.items():
        children = agent_data.get("childAgents", [])
        for child in children:
            root_agents.discard(child)
            data.declared_transfers.append((display_name, child))
            data.parent_child_transfers.add((display_name, child))
            for c2 in children:
                if child != c2:
                    data.declared_transfers.append((child, c2))

    default_root_agent = next(iter(root_agents)) if root_agents else None

    # Ingest all evaluations in a single, unified file-reading pass
    for ef in data.eval_files:
        file_tools = set()
        try:
            if ef.suffix == ".json":
                with open(ef, "r", encoding="utf-8") as f:
                    eval_content = json.load(f)
            elif ef.suffix in (".yaml", ".yml"):
                with open(ef, "r", encoding="utf-8") as f:
                    eval_content = yaml.safe_load(f)
            else:
                continue

            if not eval_content or not isinstance(eval_content, dict):
                continue

            eval_name = (
                eval_content.get("displayName")
                or eval_content.get("name")
                or ef.stem
            )

            tests_obj = eval_content.get("tests")
            if isinstance(tests_obj, list):
                for test_case in tests_obj:
                    if isinstance(test_case, dict):
                        t_name = test_case.get("name", "Unnamed")
                        tool_name = test_case.get("tool")
                        if tool_name and isinstance(tool_name, str):
                            file_tools.add(tool_name)
                            unit_tested_tools.add(tool_name)
                            args_str = json.dumps(test_case.get("args", {}))
                            data.eval_chunks.append(
                                {
                                    "text": (
                                        f"Tool Test: {t_name}\n"
                                        f"Tool: {tool_name}\n"
                                        f"Args: {args_str}"
                                    ),
                                    "eval_name": t_name or ef.stem,
                                    "file_name": ef.name,
                                }
                            )

            # GECX native golden evaluations
            if "golden" in eval_content:
                golden = eval_content.get("golden", {})
                turns = golden.get("turns", [])
                for turn_idx, turn in enumerate(turns):
                    steps = turn.get("steps", [])
                    turn_text = []
                    for step in steps:
                        if "userInput" in step:
                            u_input = step["userInput"].get("text", "")
                            turn_text.append(f"User: {u_input}")

                        expectation = step.get("expectation")
                        if isinstance(expectation, dict):
                            # Track tool calls
                            tool_call = expectation.get("toolCall")
                            if (
                                isinstance(tool_call, dict)
                                and "tool" in tool_call
                            ):
                                file_tools.add(tool_call["tool"])

                            # Compile expectation criteria for vector chunks
                            if "note" in expectation:
                                n_val = expectation["note"]
                                turn_text.append(f"Expectation Note: {n_val}")
                            if "agentTransfer" in expectation:
                                target_ag = expectation["agentTransfer"].get(
                                    "targetAgent", ""
                                )
                                turn_text.append(
                                    f"Expects Transfer to: {target_ag}"
                                )
                            if "toolCall" in expectation:
                                t_val = expectation["toolCall"].get(
                                    "tool", ""
                                )
                                turn_text.append(f"Expects Tool Call: {t_val}")
                            if "updatedVariables" in expectation:
                                vars_dump = json.dumps(
                                    expectation["updatedVariables"]
                                )
                                turn_text.append(
                                    f"Expects Updated Variables: {vars_dump}"
                                )

                    if turn_text:
                        turns_joined = "\n".join(turn_text)
                        data.eval_chunks.append(
                            {
                                "text": (
                                    f"Native Eval: {ef.stem} "
                                    f"(Turn {turn_idx})\n{turns_joined}"
                                ),
                                "eval_name": eval_name,
                                "file_name": ef.name,
                            }
                        )

                # Track agent transfers
                target_agents = find_target_agent(eval_content)

                current_agent = default_root_agent
                for target in target_agents:
                    if current_agent and target:
                        edge = (current_agent, target)
                        if edge not in data.covered_transfers:
                            data.covered_transfers[edge] = []
                        if eval_name not in data.covered_transfers[edge]:
                            data.covered_transfers[edge].append(eval_name)
                        current_agent = target

            # GECX native Simulation Evals
            elif "scenario" in eval_content:
                scenario = eval_content["scenario"]
                task = scenario.get("task", "")
                user_facts = scenario.get("userFacts", [])
                steps_text = [f"Task: {task}"]
                for fact in user_facts:
                    name_val = fact.get("name", "")
                    value_val = fact.get("value", "")
                    steps_text.append(f"Fact: {name_val} = {value_val}")

                steps_joined = "\n".join(steps_text)
                data.eval_chunks.append(
                    {
                        "text": (
                            f"Native Simulation Eval: {eval_name}\n"
                            f"{steps_joined}"
                        ),
                        "eval_name": eval_name,
                        "file_name": ef.name,
                    }
                )

            # SCRAPI Golden Evals
            elif "conversations" in eval_content:
                for conv in eval_content["conversations"]:
                    c_name = conv.get("conversation", "Unnamed")
                    tags = conv.get("tags", [])

                    turns_text = []
                    for turn in conv.get("turns", []):
                        user = turn.get("user", "")
                        agent = turn.get("agent", "")
                        turn_str = f"User: {user}\nAgent: {agent}"

                        for tool_call in turn.get("tool_calls", []):
                            if (
                                isinstance(tool_call, dict)
                                and "action" in tool_call
                            ):
                                file_tools.add(tool_call["action"])

                        if "tool_calls" in turn:
                            t_dump = json.dumps(turn["tool_calls"])
                            turn_str += f"\nTool Calls: {t_dump}"
                        turns_text.append(turn_str)

                    if turns_text:
                        tags_str = ", ".join(tags)
                        turns_joined = "\n".join(turns_text)
                        data.eval_chunks.append(
                            {
                                "text": (
                                    f"Conversation: {c_name}\n"
                                    f"Tags: {tags_str}\n"
                                    f"{turns_joined}"
                                ),
                                "eval_name": c_name or ef.stem,
                                "file_name": ef.name,
                            }
                        )

                    expectations = conv.get("expectations", [])
                    _append_expectations(
                        data,
                        expectations,
                        "Conversation",
                        c_name or ef.stem,
                        ef.name,
                    )

            # SCRAPI Simulation Evals
            elif "evals" in eval_content:
                for eval_item in eval_content["evals"]:
                    e_name = eval_item.get("name", "Unnamed")
                    tags = eval_item.get("tags", [])

                    steps_text = []
                    for step in eval_item.get("steps", []):
                        goal = step.get("goal", "")
                        success = step.get("success_criteria", "")
                        guide = step.get("response_guide", "")
                        steps_text.append(
                            f"Goal: {goal}\n"
                            f"Success Criteria: {success}\n"
                            f"Response Guide: {guide}"
                        )

                    if steps_text:
                        tags_str = ", ".join(tags)
                        steps_joined = "\n".join(steps_text)
                        data.eval_chunks.append(
                            {
                                "text": (
                                    f"Simulation Eval: {e_name}\n"
                                    f"Tags: {tags_str}\n"
                                    f"{steps_joined}"
                                ),
                                "eval_name": e_name or ef.stem,
                                "file_name": ef.name,
                            }
                        )

                    expectations = eval_item.get("expectations", [])
                    _append_expectations(
                        data,
                        expectations,
                        "Simulation Eval",
                        e_name or ef.stem,
                        ef.name,
                    )

                    # Extract tools from expectations & success criteria
                    text_to_scan = []
                    text_to_scan.extend(expectations)
                    for step in eval_item.get("steps", []):
                        if "success_criteria" in step:
                            text_to_scan.append(step["success_criteria"])
                        if "goal" in step:
                            text_to_scan.append(step["goal"])

                    for text_val in text_to_scan:
                        if not isinstance(text_val, str):
                            continue
                        for tool in data.all_tools:
                            escaped = re.escape(tool)
                            if re.search(
                                rf"\b{escaped}\b", text_val, re.IGNORECASE
                            ):
                                file_tools.add(tool)

        except (json.JSONDecodeError, yaml.YAMLError, OSError) as e:
            print(f"Warning: Failed to ingest evaluation file {ef}: {e}")

        # Check for phantom tools & compile coverage
        phantoms = file_tools - data.all_tools - {"end_session"}
        if phantoms:
            data.phantom_tools_by_file[ef] = phantoms

        data.called_tools.update(file_tools)

    data.covered_tools = unit_tested_tools & data.all_tools

    # Ingest all instruction files recursively
    agents_dir = agent_dir / "agents"

    def parse_instruction_file(filepath: Path, agent_name: str) -> None:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            segments = parse_instruction_content(content, agent_name)
            data.instruction_segments.extend(segments)
        except OSError as e:
            print(f"Warning: Failed to parse instructions {filepath}: {e}")

    if agents_dir.exists() and agents_dir.is_dir():
        for p in agents_dir.glob("**/instruction.*"):
            if p.is_file():
                data.instruction_files.append(p)
                parse_instruction_file(p, p.parent.name)

    glob_p = agent_dir / "global_instruction.txt"
    if glob_p.is_file():
        data.instruction_files.append(glob_p)
        parse_instruction_file(glob_p, "Global")

    # Discover callback tests
    if agents_dir.exists() and agents_dir.is_dir():
        for cb_dir in agents_dir.glob("**/*callbacks*/*"):
            if cb_dir.is_dir() and (cb_dir / "python_code.py").exists():
                try:
                    rel_path = cb_dir.relative_to(agents_dir)
                    cb_name = str(rel_path)
                except ValueError:
                    cb_name = cb_dir.name

                data.all_callbacks.add(cb_name)

                has_test = any(
                    f.name.startswith("test_") and f.name.endswith(".py")
                    for f in cb_dir.iterdir()
                    if f.is_file()
                )
                if has_test:
                    data.covered_callbacks.add(cb_name)

    return data
