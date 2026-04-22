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

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DFCXPlaybookConverter:
    """Converts Dialogflow CX playbooks to CXAS agents."""

    def __init__(self, reporter: Any):
        self.reporter = reporter

    @staticmethod
    def sanitize_display_name(display_name: str, max_len: int = 85) -> str:
        """Sanitizes a display name for CXAS resources."""
        sanitized = re.sub(r"[^a-zA-Z0-9_ -]", "", display_name)
        sanitized = re.sub(r"[ _-]+", " ", sanitized).strip()
        return sanitized[:max_len]

    @staticmethod
    def recursively_extract_instructions(
        steps: List[Dict[str, Any]], level: int = 0
    ) -> List[str]:
        instruction_lines = []
        indent = "    " * level
        for step in steps:
            if "text" in step:
                instruction_lines.append(f"{indent}- {step['text']}")
            if "steps" in step and step["steps"]:
                instruction_lines.extend(
                    DFCXPlaybookConverter.recursively_extract_instructions(
                        step["steps"], level + 1
                    )
                )
        return instruction_lines

    @staticmethod
    def var_replacer(match, parameter_name_map, reporter):
        original_match = match.group(0)
        var_name = next(g for g in match.groups() if g is not None)

        sanitized_name = parameter_name_map.get(var_name)
        if not sanitized_name:
            sanitized_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_name)

        new_ref = f"{{{sanitized_name}}}"

        reporter.log_transformation(
            "Variable Syntax",
            original_match,
            new_ref,
            "Updated DFCX $ variable to CXAS {} format",
        )
        return new_ref

    @staticmethod
    def replace_tool_reference(
        match: re.Match, cx_tool_display_name_to_id_map, tool_map, reporter
    ) -> str:
        dfcx_display_name = match.group(1).strip()
        dfcx_tool_id = cx_tool_display_name_to_id_map.get(dfcx_display_name)

        if not dfcx_tool_id:
            return match.group(0)

        resource_info = tool_map.get(dfcx_tool_id)
        if not resource_info:
            return match.group(0)

        if resource_info.type == "TOOLSET":
            ops = resource_info.operation_ids
            if ops:
                toolset_name = resource_info.name.split("/")[-1]
                new_ref = f"{{@TOOL: {toolset_name}_{ops[0]}}}"
                reporter.log_transformation(
                    "Instruction Rewrite",
                    match.group(0),
                    new_ref,
                    "Mapped to Toolset Operation",
                )
                return new_ref
            else:
                new_ref = f"{{@TOOL: {resource_info.name.split('/')[-1]}}}"
                reporter.log_transformation(
                    "Instruction Rewrite",
                    match.group(0),
                    new_ref,
                    "Mapped to Toolset Name (Fallback)",
                )
                return new_ref
        else:
            ps_tool_name = resource_info.name.split("/")[-1]
            new_ref = f"{{@TOOL: {ps_tool_name}}}"
            reporter.log_transformation(
                "Instruction Rewrite",
                match.group(0),
                new_ref,
                "Mapped to Standard Tool",
            )
            return new_ref

    @staticmethod
    def replace_routing_ref(match: re.Match, reporter) -> str:
        original = match.group(0)
        target_name = match.group(2).strip()
        new_ref = f"{{@AGENT: {target_name}}}"

        reporter.log_transformation(
            "Instruction Rewrite",
            original,
            new_ref,
            f"Updated {match.group(1).upper()} Reference syntax",
        )
        return new_ref

    def convert_cx_playbook_to_ps_agent(
        self,
        cx_playbook: Dict[str, Any],
        tool_map: Dict[str, Any],  # Map of DFCX ID -> IRTool
        generated_description: Optional[str],
        parameter_name_map: Dict[str, str],
        cx_tool_display_name_to_id_map: Dict[str, str],
        master_inline_action_map: Dict[str, str],
        default_model: str,
    ) -> Dict[str, Any]:
        """Converts a pre-processed DFCX Playbook to a CXAS Agent payload."""

        instruction_text = ""
        if (
            "instruction" in cx_playbook
            and "steps" in cx_playbook["instruction"]
        ):
            lines = DFCXPlaybookConverter.recursively_extract_instructions(
                cx_playbook["instruction"]["steps"]
            )
            instruction_text = "\n".join(lines)

        var_pattern = re.compile(
            r"`\$(?:(?:session|page)\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)`|"
            r"\$\`(?:(?:session|page)\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)\`|"
            r"\$\{(?:(?:session|page)\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)\}|"
            r"\$(?:(?:session|page)\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)"
        )

        if instruction_text:
            instruction_text = var_pattern.sub(
                lambda m: DFCXPlaybookConverter.var_replacer(
                    m, parameter_name_map, self.reporter
                ),
                instruction_text,
            )

        instruction_text = re.sub(
            r"\${TOOL:([^}]+)}",
            lambda m: DFCXPlaybookConverter.replace_tool_reference(
                m, cx_tool_display_name_to_id_map, tool_map, self.reporter
            ),
            instruction_text,
        )

        routing_pattern = re.compile(
            r"\$\{\s*(agent|flow|playbook)\s*:\s*([^}]+)\}", flags=re.IGNORECASE
        )
        instruction_text = routing_pattern.sub(
            lambda m: DFCXPlaybookConverter.replace_routing_ref(
                m, self.reporter
            ),
            instruction_text,
        )

        if master_inline_action_map:
            for func_name, ps_resource_name in master_inline_action_map.items():
                pattern = r"(?:`?)\b" + re.escape(func_name) + r"\b(?:`?)"
                tool_id = ps_resource_name.split("/")[-1]

                reserved_ids = {
                    "transfer_to_agent",
                    "tranferToAgent",
                    "end_session",
                    "customize_response",
                }
                clean_func_name = func_name.lstrip("_-")

                if clean_func_name in reserved_ids and tool_id.startswith(
                    "usr_"
                ):
                    replacement_name = tool_id
                else:
                    replacement_name = func_name

                replacement = f"{{@TOOL: {replacement_name}}}"
                if re.search(pattern, instruction_text):
                    instruction_text = re.sub(
                        pattern, replacement, instruction_text
                    )
                    self.reporter.log_transformation(
                        "Instruction Rewrite",
                        func_name,
                        replacement,
                        "Mapped Python Function to Tool",
                    )

        referenced_tools = []
        referenced_toolsets = []

        if "referencedTools" in cx_playbook:
            for cx_tool_id in cx_playbook["referencedTools"]:
                if cx_tool_id in tool_map:
                    tool = tool_map[cx_tool_id]
                    if tool.type == "TOOLSET":
                        referenced_toolsets.append(
                            {
                                "toolset": tool.name,
                                "toolIds": tool.operation_ids,
                            }
                        )
                    else:
                        referenced_tools.append(tool.name)

        display_name = DFCXPlaybookConverter.sanitize_display_name(
            cx_playbook.get("displayName", "Unnamed Agent")
        )
        description = generated_description or cx_playbook.get(
            "goal", "No description provided."
        )
        goal = cx_playbook.get("goal", "No description provided.")

        concatenated_instruction = (
            f"# Agent Goal\n{goal}\n\n# Agent Instruction\n{instruction_text}"
        )

        target_model = cx_playbook.get("_target_model", default_model)

        agent_payload = {
            "display_name": display_name,
            "description": description,
            "instruction": concatenated_instruction,
            "tools": list(set(referenced_tools)),
            "toolsets": referenced_toolsets,
            "modelSettings": {"model": target_model},
        }
        return agent_payload
