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
class AppRootAgentValidation(Rule):
    id = "A006"
    name = "config-root-agent"
    description = (
        "App config must have a valid rootAgent pointing to an "
        "existing agent directory"
    )
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

        # 1. Check case sensitivity / incorrect snake_case
        if "root_agent" in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Found 'root_agent' in app.json, but CXAS strictly "
                        "requires camelCase 'rootAgent'"
                    ),
                    fix="Rename 'root_agent' to 'rootAgent'",
                )
            )
            return results

        # 2. Check if rootAgent is missing
        root_agent_name = data.get("rootAgent")
        if not root_agent_name:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Missing required field 'rootAgent' in app.json. "
                        "An app must have a rootAgent to handle incoming "
                        "sessions"
                    ),
                    fix="Add 'rootAgent': '<agent_directory_name>' to app.json",
                )
            )
            return results

        # 3. Check if rootAgent is not a string
        if not isinstance(root_agent_name, str):
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Field 'rootAgent' in app.json must be a string, "
                        f"got {type(root_agent_name).__name__}"
                    ),
                )
            )
            return results

        # 4. Check if rootAgent exists under agents/
        agent_dir = file_path.parent / "agents" / root_agent_name
        if not agent_dir.exists() or not agent_dir.is_dir():
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"rootAgent '{root_agent_name}' specified in app.json "
                        "does not exist under the agents/ directory"
                    ),
                    fix=(
                        f"Create the directory 'agents/{root_agent_name}' "
                        "or fix the 'rootAgent' reference in app.json"
                    ),
                )
            )
            return results

        # 5. Check if <rootAgent>.json exists
        agent_json = agent_dir / f"{root_agent_name}.json"
        if not agent_json.exists():
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Root agent '{root_agent_name}' exists but is "
                        f"missing required '{root_agent_name}.json' file"
                    ),
                    fix=(
                        f"Create file 'agents/"
                        f"{root_agent_name}/{root_agent_name}.json'"
                    ),
                )
            )

        return results
