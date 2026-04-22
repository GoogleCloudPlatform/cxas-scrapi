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

"""Eval lint rules (E001-E011).

Validates golden, scenario, and simulation YAML files.
"""

import re
from pathlib import Path
from typing import Optional

import yaml

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


def _is_golden(file_path: Path) -> bool:
    return "goldens" in file_path.parts


def _is_simulation(file_path: Path) -> bool:
    return "simulations" in file_path.parts


def _is_tool_test(file_path: Path) -> bool:
    return "tool_tests" in file_path.stem or "tool_tests" in file_path.parts


def _parse_yaml(content: str) -> Optional[dict]:
    """Parse YAML, returning None on error or empty content."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return None
    return data if data else None


def _iter_golden_turns(data: dict):
    """Yield (conv_name, turn_index, turn_dict) for golden eval turns."""
    for conv in data.get("conversations", []):
        conv_name = conv.get("conversation", "")
        for i, turn in enumerate(conv.get("turns", [])):
            if isinstance(turn, dict):
                yield conv_name, i + 1, turn


@rule("evals")
class InvalidYaml(Rule):
    id = "E001"
    name = "eval-yaml-parse"
    description = "Eval file must be valid YAML"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            return [self.make_result(file=rel, message=f"Invalid YAML: {e}")]
        return []


@rule("evals")
class MissingConversations(Rule):
    id = "E002"
    name = "eval-structure"
    description = "Golden eval must have 'conversations' key"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        rel = str(file_path.relative_to(context.project_root))
        data = _parse_yaml(content)
        if data is None:
            return [
                self.make_result(
                    file=rel, message="Eval file is empty or invalid"
                )
            ]
        if "conversations" not in data:
            return [
                self.make_result(
                    file=rel,
                    message="Missing 'conversations' key in golden eval YAML",
                )
            ]
        return []


@rule("evals")
class InvalidToolCall(Rule):
    id = "E003"
    name = "eval-tool-exists"
    description = "Tool calls in evals must reference existing tools"
    default_severity = Severity.WARNING

    SPECIAL_ACTIONS = {"transfer_to_agent", "end_session"}

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if _is_golden(file_path) or str(file_path).startswith(
            str(context.evals_dir)
        ):
            return []

        data = _parse_yaml(content)
        if not data:
            return []

        conversations = (
            data if isinstance(data, list) else data.get("conversations", [])
        )
        valid_tools = context.all_known_tools | self.SPECIAL_ACTIONS
        rel = str(file_path.relative_to(context.project_root))

        results = []
        for conv_name, turn_num, turn in _iter_golden_turns(
            {"conversations": conversations}
        ):
            for tc in turn.get("tool_calls", []):
                action = tc.get("action", "")
                if action and action not in valid_tools:
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Conv '{conv_name}'"
                                f" turn {turn_num}:"
                                f" tool_call '{action}'"
                                " not found in local"
                                " app tools"
                            ),
                            fix=(
                                "Available local tools:"
                                f" {', '.join(sorted(context.all_known_tools))}"
                            ),
                        )
                    )
        return results


@rule("evals")
class UndeclaredSessionParam(Rule):
    id = "E004"
    name = "eval-session-param"
    description = "Session parameters should reference known variables"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return []


@rule("evals")
class DuplicateYamlKeys(Rule):
    id = "E005"
    name = "eval-duplicate-keys"
    description = (
        "Duplicate YAML keys in same mapping (second overwrites first)"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        prev_key = ""
        prev_indent = -1
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            key_match = re.match(r"^(\w+):", stripped)
            if key_match:
                key = key_match.group(1)
                if (
                    key == prev_key
                    and indent == prev_indent
                    and key == "tool_calls"
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            line=i,
                            message=(
                                f"Duplicate '{key}:'"
                                " key at same level"
                                " — second overwrites"
                                " first"
                            ),
                            fix="Combine into a single tool_calls: list",
                        )
                    )
                prev_key = key
                prev_indent = indent
            elif (
                stripped
                and not stripped.startswith("-")
                and not stripped.startswith("#")
            ):
                prev_key = ""
        return results


@rule("evals")
class GoldenWithoutMocks(Rule):
    id = "E006"
    name = "eval-no-mocks"
    description = "Golden eval with tool_calls but no session_parameters"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        has_common_params = bool(data.get("common_session_parameters"))
        has_tool_calls = any(
            turn.get("tool_calls")
            for _conv, _i, turn in _iter_golden_turns(data)
        )

        if has_tool_calls and not has_common_params:
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Golden eval has tool_calls"
                        " but no"
                        " common_session_parameters"
                    ),
                    fix="Add session parameters for reliable tool responses",
                )
            ]
        return []


@rule("evals")
class GoldenAgentFieldNotString(Rule):
    id = "E007"
    name = "eval-agent-not-string"
    description = (
        "Golden agent response must be a string or list of strings, not a dict"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        for conv, i, turn in _iter_golden_turns(data):
            agent = turn.get("agent")
            if agent is None:
                continue
            if isinstance(agent, str):
                continue
            if isinstance(agent, list) and all(
                isinstance(item, str) for item in agent
            ):
                continue
            results.append(
                self.make_result(
                    file=rel,
                    message=f"Conv '{conv}' turn {i}: 'agent' field is a "
                    f"{type(agent).__name__}, must be a plain string"
                    " or a list of strings",
                    fix=(
                        "Replace with a plain string or a list"
                        " of strings containing the expected"
                        " agent response text"
                    ),
                )
            )
        return results


@rule("evals")
class GoldenMissingAgentField(Rule):
    id = "E008"
    name = "eval-missing-agent"
    description = (
        "Golden turn missing 'agent' field"
        " — causes automatic FAIL from"
        " unexpected response"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    f"Conv '{conv}' turn {i}:"
                    " has 'user' but no"
                    " 'agent' field — any"
                    " agent response will be"
                    " flagged as UNEXPECTED"
                    " RESPONSE"
                ),
                fix=("Add an 'agent' field with the expected response text"),
            )
            for conv, i, turn in _iter_golden_turns(data)
            if "user" in turn and "agent" not in turn
        ]


@rule("evals")
class SimMissingTags(Rule):
    id = "E009"
    name = "eval-sim-missing-tags"
    description = (
        "Simulation eval missing 'tags' field — won't match --priority filters"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_simulation(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        if isinstance(data, dict):
            evals_list = data.get("evals", [])
        elif isinstance(data, list):
            evals_list = data
        else:
            return []

        return [
            self.make_result(
                file=rel,
                message=f"Sim '{ev.get('name', '')}' has no 'tags' field — "
                f"won't be found by --priority P0/P1/P2 filters",
                fix='Add: tags: ["P0", "category"]',
            )
            for ev in evals_list
            if isinstance(ev, dict) and "tags" not in ev
        ]


@rule("evals")
class ToolTestWrongKey(Rule):
    id = "E010"
    name = "eval-tool-test-wrong-key"
    description = (
        "Tool test YAML uses 'test_cases'"
        " instead of 'tests' — SCRAPI"
        " silently returns 0 tests"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_tool_test(file_path):
            return []
        data = _parse_yaml(content)
        if not data or not isinstance(data, dict):
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        if "test_cases" in data and "tests" not in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Uses 'test_cases' key but"
                        " SCRAPI expects 'tests'"
                        " — all tests will be"
                        " silently skipped"
                    ),
                    fix="Rename 'test_cases:' to 'tests:'",
                )
            )
        if "tool_name" in data and "tests" not in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Uses top-level 'tool_name'"
                        " (old format) — SCRAPI"
                        " expects 'tool' on each"
                        " test case inside 'tests'"
                    ),
                    fix=(
                        "Restructure: move"
                        " tool_name into each test"
                        " case as 'tool:', rename"
                        " 'test_cases:' to 'tests:'"
                    ),
                )
            )
        return results


@rule("evals")
class InvalidMatchType(Rule):
    id = "E011"
    name = "eval-invalid-match-type"
    description = "Invalid $matchType value in golden tool_calls args"
    default_severity = Severity.ERROR

    VALID_MATCH_TYPES = {"ignore", "semantic", "contains", "regexp"}
    COMMON_TYPOS = {
        "regex": "regexp",
        "exact": None,
        "fuzzy": "semantic",
    }

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        for conv_name, turn_num, turn in _iter_golden_turns(data):
            for tc in turn.get("tool_calls", []):
                args = tc.get("args", {})
                if not isinstance(args, dict):
                    continue
                for arg_name, arg_val in args.items():
                    if not isinstance(arg_val, dict):
                        continue
                    match_type = arg_val.get("$matchType")
                    if (
                        match_type is None
                        or match_type in self.VALID_MATCH_TYPES
                    ):
                        continue
                    suggestion = self.COMMON_TYPOS.get(match_type)
                    valid_vals = ", ".join(sorted(self.VALID_MATCH_TYPES))
                    fix = (
                        f'Did you mean "{suggestion}"?'
                        if suggestion
                        else f"Valid values: {valid_vals}"
                    )
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Conv"
                                f" '{conv_name}'"
                                f" turn"
                                f" {turn_num}:"
                                f" arg"
                                f" '{arg_name}'"
                                " has invalid"
                                " $matchType"
                                f" '{match_type}'"
                            ),
                            fix=fix,
                        )
                    )
        return results
