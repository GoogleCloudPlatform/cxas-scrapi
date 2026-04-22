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

"""App and agent config lint rules (A001-A005).

Validates app.json and agent JSON configuration files.
"""

import json
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


@rule("config")
class InvalidJson(Rule):
    id = "A001"
    name = "config-json-parse"
    description = "Config file must be valid JSON"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"Invalid JSON: {e}",
                )
            ]
        return []


@rule("config")
class MissingRequiredFields(Rule):
    id = "A002"
    name = "config-required-fields"
    description = "Config must have required fields (name, displayName)"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        if file_path.name == "app.json":
            for field_name in ["name", "displayName"]:
                if field_name not in data:
                    results.append(
                        self.make_result(
                            file=rel,
                            message=f"Missing required field: '{field_name}'",
                        )
                    )
        return results


@rule("config")
class AgentToolNotExists(Rule):
    id = "A003"
    name = "config-tool-exists"
    description = "Agent config references non-existent tool"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name == "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        for tool in data.get("tools", []):
            if tool not in context.all_known_tools:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Agent config lists tool"
                            f" '{tool}' but it does"
                            " not exist"
                        ),
                        fix=(
                            "Available tools:"
                            f" {', '.join(sorted(context.all_known_tools))}"
                        ),
                    )
                )
        return results


@rule("config")
class AgentMissingInstruction(Rule):
    id = "A004"
    name = "config-missing-instruction"
    description = "Agent directory must have an instruction.txt file"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name == "app.json":
            return []

        agent_dir = file_path.parent
        instruction = agent_dir / "instruction.txt"
        if not instruction.exists():
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Agent '{agent_dir.name}'"
                        " has config but no"
                        " instruction.txt"
                    ),
                    fix=(
                        "Create instruction.txt"
                        " with <role>, <persona>,"
                        " and <taskflow> sections"
                    ),
                )
            ]
        return []


@rule("config")
class RootAgentMissingEndSession(Rule):
    id = "A005"
    name = "config-root-missing-end-session"
    description = "Root agent must have end_session tool associated"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        root_agent_name = data.get("rootAgent")
        if not root_agent_name:
            return []

        agent_dir = file_path.parent / "agents" / root_agent_name
        agent_json = agent_dir / f"{root_agent_name}.json"
        if not agent_json.exists():
            return []

        try:
            agent_data = json.loads(agent_json.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        tools = agent_data.get("tools", [])
        if "end_session" not in tools:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Root agent"
                        f" '{root_agent_name}' is"
                        " missing 'end_session'"
                        " tool — the agent cannot"
                        " terminate conversations"
                    ),
                    fix=(
                        "Associate end_session with"
                        " the root agent via:"
                        " agents_client"
                        ".update_agent("
                        "agent_name=...,"
                        " tools=[...,"
                        " 'end_session'])"
                    ),
                )
            ]
        return []


@rule("config")
class AppMissingTools(Rule):
    id = "A006"
    name = "config-app-missing-tools"
    description = "app.json must list all local tools"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        app_tools = set(data.get("tools", []))
        missing_tools = context.all_tool_names - app_tools

        for tool in sorted(missing_tools):
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Local tool '{tool}' is not listed in the"
                        " app.json tools array"
                    ),
                    fix=f"Add '{tool}' to the tools array in app.json",
                )
            )
        return results
