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

"""Structure validation rules (S001-S004).

Validates app structure and cross-references using the existing
``cxas_scrapi.utils.validator.Validator`` for structural checks, plus
custom rules for cross-referencing (e.g., instruction references a tool
not in the agent's tool list).
"""

import json
import re
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


@rule("structure")
class AgentToolReferences(Rule):
    """Instruction references tools not in the agent's tool list.

    When an instruction tells the LLM to call a tool that isn't in the
    agent's tool list, the LLM can't call it — it silently improvises.
    """

    id = "S002"
    name = "agent-tool-references"
    description = (
        "Instruction references tools that exist in the agent's tool list"
    )
    default_severity = Severity.ERROR
    target = "instruction"

    TOOL_REF_PATTERN = re.compile(r"\{@TOOL[:\s]+([^}]+)\}", re.IGNORECASE)

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_dir = file_path.parent
        agent_json = agent_dir / f"{agent_dir.name}.json"
        if not agent_json.exists():
            return []

        try:
            agent_config = json.loads(agent_json.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        known_tools = (
            set(agent_config.get("tools", [])) | context.platform_tools
        )
        known_tools_lower = {t.lower() for t in known_tools}

        referenced = {
            match.group(1).strip()
            for match in self.TOOL_REF_PATTERN.finditer(content)
        }
        missing = {t for t in referenced if t.lower() not in known_tools_lower}
        if not missing:
            return []

        # Build line index once for all missing tools
        lines = content.splitlines()
        results = []
        for tool_name in missing:
            line_num = next(
                (i for i, line in enumerate(lines, 1) if tool_name in line),
                None,
            )
            results.append(
                self.make_result(
                    str(file_path),
                    (
                        "Instruction references"
                        f" tool '{tool_name}'"
                        " but it's not in the"
                        " agent's tool list."
                    ),
                    line=line_num,
                    fix=(
                        f"Add '{tool_name}' to"
                        " the tools list in"
                        f" {agent_json.name},"
                        " or remove the"
                        " reference from the"
                        " instruction."
                    ),
                )
            )
        return results


def _resolve_path(app_dir: Path, relative_path: str) -> Path:
    """Resolve a relative path against app_dir, with subdirectory fallback."""
    candidate = app_dir / relative_path
    if candidate.exists():
        return candidate
    for child in app_dir.iterdir():
        if child.is_dir() and (child / relative_path).exists():
            return child / relative_path
    return candidate


_CALLBACK_TYPES = [
    "beforeAgentCallbacks",
    "afterAgentCallbacks",
    "beforeModelCallbacks",
    "afterModelCallbacks",
    "beforeToolCallbacks",
    "afterToolCallbacks",
]


@rule("structure")
class CallbackFileReferences(Rule):
    """Agent JSON references callback files that don't exist."""

    id = "S003"
    name = "callback-file-references"
    description = "Callback code files referenced in agent JSON exist on disk"
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        try:
            agent_config = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        code_paths = [
            cb.get("pythonCode", "")
            for cb_type in _CALLBACK_TYPES
            for cb in agent_config.get(cb_type, [])
        ]
        for code_path in filter(None, code_paths):
            resolved = _resolve_path(context.app_dir, code_path)
            if not resolved.exists():
                results.append(
                    self.make_result(
                        str(file_path),
                        f"Callback references '{code_path}' but file not found",
                        fix=(
                            "Create the callback"
                            " file or fix the path"
                            " in the agent JSON"
                        ),
                    )
                )
        return results


@rule("structure")
class ChildAgentReferences(Rule):
    """Agent JSON references child agents that don't exist."""

    id = "S004"
    name = "child-agent-references"
    description = (
        "Child agent references in agent JSON point to existing agents"
    )
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        try:
            agent_config = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        child_agents = agent_config.get("childAgents", [])
        valid_agents = (
            context.all_agent_names | context.all_agent_display_names
        )
        for child_name in child_agents:
            if child_name not in valid_agents:
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            "References child agent"
                            f" '{child_name}' but no"
                            " agent directory found."
                            " Available agents:"
                            f" {sorted(valid_agents)}"
                        ),
                        fix="Create the agent directory or fix the reference",
                    )
                )
        return results
