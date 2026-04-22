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

"""Tool lint rules (T001-T008).

Validates agent tool Python files against GECX conventions.
"""

import json
import re
from pathlib import Path
from typing import Optional

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

FUNC_DEF_RE = re.compile(r"def\s+(\w+)\s*\(([^)]*)\)")


def _load_tool_config(file_path: Path) -> tuple[Optional[dict], Optional[Path]]:
    """Load the tool JSON config from the tool directory.

    Tool layout: tools/<name>/python_function/python_code.py
    JSON config: tools/<name>/<name>.json
    """
    tool_dir = file_path.parent.parent
    json_path = tool_dir / f"{tool_dir.name}.json"
    if not json_path.exists():
        return None, json_path
    try:
        return json.loads(json_path.read_text()), json_path
    except (json.JSONDecodeError, OSError):
        return None, json_path


@rule("tools")
class MissingAgentAction(Rule):
    id = "T001"
    name = "tool-error-pattern"
    description = (
        "Tool must return agent_action on error for deterministic recovery"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if "agent_action" not in content:
            return [
                self.make_result(
                    file=rel,
                    message="Missing agent_action error return pattern",
                    fix=(
                        'Add: return {"agent_action":'
                        ' "error message for agent'
                        ' to relay"}'
                    ),
                )
            ]
        return []


@rule("tools")
class MissingDocstring(Rule):
    id = "T002"
    name = "tool-docstring"
    description = "Tool missing docstring — CES uses this as tool description"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if '"""' not in content and "'''" not in content:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Missing docstring — CES"
                        " uses tool docstrings"
                        " for invocation routing"
                    ),
                    fix=(
                        "Add a descriptive docstring"
                        " explaining when and how"
                        " the LLM should use"
                        " this tool"
                    ),
                )
            ]
        return []


@rule("tools")
class MissingTypeHints(Rule):
    id = "T003"
    name = "tool-type-hints"
    description = "Tool function arguments lack type hints"
    default_severity = Severity.INFO

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        fn_match = FUNC_DEF_RE.search(content)
        if fn_match:
            args_str = fn_match.group(2)
            if args_str.strip() and ":" not in args_str:
                return [
                    self.make_result(
                        file=rel,
                        line=1,
                        message="Function arguments lack type hints",
                        fix=(
                            "Add type hints: def"
                            " tool_name(arg: str,"
                            " count: int) -> dict:"
                        ),
                    )
                ]
        return []


@rule("tools")
class FunctionNameMismatch(Rule):
    id = "T004"
    name = "tool-fn-name"
    description = "Tool function name should match tool directory name"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        tool_dir_name = file_path.parent.parent.name

        fn_match = FUNC_DEF_RE.search(content)
        if not fn_match:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message="No function definition found in tool file",
                )
            ]

        actual_fn = fn_match.group(1)
        if actual_fn != tool_dir_name:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message=(
                        f"Function named"
                        f" '{actual_fn}', expected"
                        f" '{tool_dir_name}'"
                        " (matching directory)"
                    ),
                    fix=f"Rename to: def {tool_dir_name}(...):",
                )
            ]
        return []


@rule("tools")
class HighCardinalityArgs(Rule):
    id = "T005"
    name = "tool-high-cardinality"
    description = (
        "High-cardinality input arguments reduce deterministic tool selection"
    )
    default_severity = Severity.INFO

    HIGH_CARDINALITY_PATTERNS = [
        (
            r"timestamp",
            "timestamp — hard for voice users to express",
        ),
        (
            r"latitude|longitude|coordinates",
            "coordinates — high cardinality",
        ),
        (
            r"session_id|request_id|trace_id",
            "internal ID — not voice-expressible",
        ),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        fn_match = FUNC_DEF_RE.search(content)
        if not fn_match:
            return []

        rel = str(file_path.relative_to(context.project_root))
        args_str = fn_match.group(2)
        return [
            self.make_result(
                file=rel,
                message=f"High-cardinality argument: {label}",
                fix=(
                    "Design args that a human"
                    " can express in voice mode"
                    " (e.g., region, category,"
                    " last_n_days)"
                ),
            )
            for pattern, label in self.HIGH_CARDINALITY_PATTERNS
            if re.search(pattern, args_str, re.IGNORECASE)
        ]


@rule("tools")
class ExcessiveReturnData(Rule):
    id = "T006"
    name = "tool-return-explosion"
    description = "Tool returning excessive data bloats LLM context"
    default_severity = Severity.INFO

    RETURN_PATTERNS = [
        (
            r"return\s+response\.json\(\)",
            "Returning raw API response"
            " — may include data the"
            " LLM doesn't need",
            "Filter the response to only"
            " include fields the LLM"
            " needs for decision-making",
        ),
        (
            r"return\s+json\.loads\(",
            "Returning parsed JSON"
            " directly — consider"
            " filtering to relevant"
            " fields only",
            "Only return data that the LLM needs to see",
        ),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(file=rel, message=msg, fix=fix)
            for pattern, msg, fix in self.RETURN_PATTERNS
            if re.search(pattern, content)
        ]


@rule("tools")
class ToolNameNotSnakeCase(Rule):
    id = "T007"
    name = "tool-name-snake-case"
    description = (
        "Tool JSON name/displayName must be"
        " snake_case (no spaces or mixed case)"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, json_path = _load_tool_config(file_path)
        if not tool_config:
            return []

        rel = str(json_path.relative_to(context.project_root))
        results = []
        for field_name in ("name", "displayName"):
            value = tool_config.get(field_name, "")
            if value and (" " in value or value != value.lower()):
                snake = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Tool {field_name} '{value}' is not snake_case"
                        ),
                        fix=f'Change to: "{field_name}": "{snake}"',
                    )
                )
        return results


@rule("tools")
class ToolDisplayNameUnreferenced(Rule):
    id = "T008"
    name = "tool-displayname-unreferenced"
    description = "Tool displayName not referenced by any agent's tools array"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, json_path = _load_tool_config(file_path)
        if not tool_config:
            return []

        display_name = tool_config.get("displayName", "")
        if not display_name:
            return []

        # tools/<name>/python_function/python_code.py → 4 levels to app root
        app_root = file_path.parent.parent.parent.parent
        agents_dir = app_root / "agents"
        if not agents_dir.exists():
            return []

        referenced = any(
            display_name in json.loads(agent_json.read_text()).get("tools", [])
            for agent_dir in agents_dir.iterdir()
            if agent_dir.is_dir()
            for agent_json in [agent_dir / f"{agent_dir.name}.json"]
            if agent_json.exists()
        )

        if not referenced:
            rel = str(json_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Tool displayName"
                        f" '{display_name}' not"
                        " found in any agent's"
                        " tools array"
                    ),
                    fix=(
                        "Add this tool to the"
                        " relevant agent's JSON"
                        " config, or remove the"
                        " tool if unused"
                    ),
                )
            ]
        return []


@rule("tools")
class KwargsInSignature(Rule):
    id = "T009"
    name = "tool-kwargs-signature"
    description = (
        "Tool function uses **kwargs"
        " — platform requires explicit"
        " named parameters"
    )
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        fn_match = FUNC_DEF_RE.search(content)
        if fn_match and "**" in fn_match.group(2):
            line = content[: fn_match.start()].count("\n") + 1
            return [
                self.make_result(
                    file=rel,
                    line=line,
                    message=(
                        "Tool function uses"
                        " **kwargs — platform"
                        " silently drops tools"
                        " with **kwargs during"
                        " import"
                    ),
                    fix=(
                        "Replace **kwargs with"
                        " explicit parameters:"
                        " def my_tool(param1:"
                        " str = '', param2:"
                        " str = '') -> dict:"
                    ),
                )
            ]
        return []


@rule("tools")
class ToolInvalidPythonSyntax(Rule):
    id = "T010"
    name = "tool-python-syntax"
    description = "Tool Python file must have valid syntax"
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            compile(content, rel, "exec")
        except SyntaxError as e:
            return [
                self.make_result(
                    file=rel,
                    line=e.lineno,
                    message=(f"Invalid Python syntax: {e.msg}"),
                    fix=(
                        "Fix the syntax error"
                        " — invalid Python causes"
                        " tools to be silently"
                        " dropped during import"
                    ),
                )
            ]
        return []


@rule("tools")
class NoneDefaultValue(Rule):
    id = "T011"
    name = "tool-none-default"
    description = (
        "Tool parameter uses None as default"
        " — platform silently drops"
        " these tools"
    )
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        fn_match = FUNC_DEF_RE.search(content)
        if not fn_match:
            return []

        rel = str(file_path.relative_to(context.project_root))
        args_str = fn_match.group(2)
        line = content[: fn_match.start()].count("\n") + 1

        def _param_name(p):
            return p.split(":")[0].strip()

        return [
            self.make_result(
                file=rel,
                line=line,
                message=(
                    f"Parameter"
                    f" '{_param_name(p)}'"
                    " uses None as default"
                    " — platform silently"
                    " drops tools with None"
                    " defaults during import"
                ),
                fix=(
                    "Use type-matching defaults: str = '', int = 0, list = []"
                ),
            )
            for p in args_str.split(",")
            if "=" in p and re.search(r"=\s*None\s*$", p.strip())
        ]


@rule("tools")
class MissingToolDescriptionInJSON(Rule):
    id = "T012"
    name = "tool-json-missing-description"
    description = (
        "Tool JSON configuration must include pythonFunction.description."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, json_path = _load_tool_config(file_path)
        if not tool_config:
            return []

        python_function = tool_config.get("pythonFunction", {})
        description = python_function.get("description")

        if not description or not str(description).strip():
            rel = str(json_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Tool JSON configuration is missing "
                        "pythonFunction.description field. "
                        "The LLM relies on this description to know when to "
                        "call the tool."
                    ),
                    fix=(
                        "Add a 'description' key to the 'pythonFunction' "
                        "object in the tool's JSON file."
                    ),
                )
            ]
        return []
