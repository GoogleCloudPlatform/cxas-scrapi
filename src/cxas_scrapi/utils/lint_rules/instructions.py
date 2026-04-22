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

"""Instruction lint rules (I001-I013).

Validates agent instruction files against GECX design guide best practices.
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

TOOL_REF_PATTERN = re.compile(r"\{@TOOL:\s*([^}]+)\}")


def _load_agent_config(file_path: Path) -> Optional[dict]:
    """Load the agent JSON config adjacent to an instruction file."""
    agent_dir = file_path.parent
    agent_json = agent_dir / f"{agent_dir.name}.json"
    if not agent_json.exists():
        return None
    try:
        return json.loads(agent_json.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _extract_tool_refs(content: str) -> set[str]:
    """Extract all {@TOOL: name} references from instruction content."""
    return {m.group(1).strip() for m in TOOL_REF_PATTERN.finditer(content)}


def _find_line(content: str, needle: str) -> Optional[int]:
    """Return 1-based line number of first occurrence, or None."""
    for i, line in enumerate(content.splitlines(), 1):
        if needle in line:
            return i
    return None


@rule("instructions")
class RequiredXmlStructure(Rule):
    id = "I001"
    name = "required-xml-structure"
    description = (
        "Instruction must contain <role>, <persona>, and <taskflow> tags"
    )
    default_severity = Severity.ERROR

    REQUIRED_TAGS = ["<role>", "<persona>", "<taskflow>"]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=f"Missing required XML tag: {tag}",
                fix=(
                    f"Add {tag}..."
                    f"{tag.replace('<', '</')}"
                    " section to instruction"
                ),
            )
            for tag in self.REQUIRED_TAGS
            if tag not in content
        ]


@rule("instructions")
class TaskflowChildren(Rule):
    id = "I002"
    name = "taskflow-children"
    description = "Taskflow must contain <subtask> or <step> children"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if "<taskflow>" not in content:
            return []
        match = re.search(r"<taskflow>(.*?)</taskflow>", content, re.DOTALL)
        if not match:
            return []
        taskflow = match.group(1)
        if "<subtask" not in taskflow and "<step" not in taskflow:
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message="<taskflow> has no <subtask> or <step> children",
                    fix=(
                        'Add <subtask name="...">'
                        "<step>...</step>"
                        "</subtask> inside"
                        " <taskflow>"
                    ),
                )
            ]
        return []


@rule("instructions")
class ExcessiveIfElse(Rule):
    id = "I003"
    name = "excessive-if-else"
    description = (
        "Excessive IF/ELSE logic in instructions (should be in callbacks)"
    )
    default_severity = Severity.WARNING

    IF_ELSE_RE = re.compile(r"\bIF\b.*\bELSE\b", re.IGNORECASE)

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        count = sum(
            1 for line in content.split("\n") if self.IF_ELSE_RE.search(line)
        )
        if count >= 3:  # noqa: PLR2004
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Found {count} IF/ELSE"
                        " blocks — excessive"
                        " programmatic logic"
                        " degrades LLM reliability"
                    ),
                    fix="Move deterministic branching to callbacks.",
                )
            ]
        return []


@rule("instructions")
class NegativeTriggers(Rule):
    id = "I004"
    name = "negative-triggers"
    description = "Negative conditions in triggers confuse the LLM"
    default_severity = Severity.WARNING

    NEGATIVE_PATTERNS = [
        (r"<trigger>.*\bNOT\b.*</trigger>", "NOT in trigger"),
        (r"<trigger>.*\bis NOT\b.*</trigger>", "is NOT in trigger"),
        (
            r"<trigger>.*\bnot\s+(?:a|an|the)\b.*</trigger>",
            "negation in trigger",
        ),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        for pattern, label in self.NEGATIVE_PATTERNS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                line_num = content[: m.start()].count("\n") + 1
                results.append(
                    self.make_result(
                        file=rel,
                        line=line_num,
                        message=f"Negative condition in trigger: {label}",
                        fix=(
                            "Use positive triggers"
                            " only. Put the excluded"
                            " case as a separate,"
                            " earlier step."
                        ),
                    )
                )
        return results


@rule("instructions")
class ConditionalLogicBlock(Rule):
    id = "I005"
    name = "conditional-logic-block"
    description = (
        "conditional_logic blocks for intent classification confuse the LLM"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=content[: m.start()].count("\n") + 1,
                message=(
                    "<conditional_logic> block"
                    " — LLM gets confused by"
                    " priority-ordered"
                    " conditionals"
                ),
                fix=(
                    "Use separate <step>"
                    " elements with distinct"
                    " triggers instead"
                ),
            )
            for m in re.finditer(r"<conditional_logic>", content)
        ]


@rule("instructions")
class HardcodedData(Rule):
    id = "I006"
    name = "hardcoded-data"
    description = (
        "Hardcoded data (phone numbers, prices) should come from tools"
    )
    default_severity = Severity.WARNING

    DEFAULT_PATTERNS = [
        (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "phone number"),
        (r"\$\d+(?:\.\d{2})?", "price/dollar amount"),
    ]

    def _should_skip(self, line: str) -> bool:
        if "{" in line and "}" in line:
            return True
        if "<inline_example" in line or "</inline_example" in line:
            return True
        return False

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        options = context.options.get("I006", {})
        custom = options.get("patterns", None)
        patterns = (
            [(p, "data") for p in custom] if custom else self.DEFAULT_PATTERNS
        )

        results = []
        for i, line in enumerate(content.split("\n"), 1):
            if self._should_skip(line):
                continue
            for pattern, label in patterns:
                for m in re.finditer(pattern, line):
                    results.append(
                        self.make_result(
                            file=rel,
                            line=i,
                            message=(
                                f"Possible hardcoded {label}: '{m.group()}'"
                            ),
                            fix=(
                                "Data should come from"
                                " tool responses, not"
                                " hardcoded in"
                                " instructions"
                            ),
                        )
                    )
        return results


@rule("instructions")
class InstructionTooLong(Rule):
    id = "I007"
    name = "instruction-too-long"
    description = (
        "Instruction exceeds word count"
        " threshold — consider splitting"
        " into sub-agents"
    )
    default_severity = Severity.INFO

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        max_words = context.options.get("I007", {}).get("max_words", 3000)
        word_count = len(content.split())
        if word_count > max_words:
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Instruction is"
                        f" {word_count} words"
                        f" (threshold: {max_words})"
                    ),
                    fix=(
                        "Consider splitting into"
                        " sub-agents to reduce"
                        " context size"
                    ),
                )
            ]
        return []


@rule("instructions")
class InvalidAgentRef(Rule):
    id = "I008"
    name = "invalid-agent-ref"
    description = "Agent reference points to non-existent agent"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        valid = context.all_agent_names | context.all_agent_display_names
        refs = {
            ref.strip() for ref in re.findall(r"\{@AGENT:\s*([^}]+)\}", content)
        }
        return [
            self.make_result(
                file=rel,
                message=f"{{@AGENT: {ref}}} references non-existent agent",
                fix=f"Available agents: {', '.join(sorted(valid))}",
            )
            for ref in refs
            if ref not in valid
        ]


@rule("instructions")
class InvalidToolRef(Rule):
    id = "I009"
    name = "invalid-tool-ref"
    description = "Tool reference points to tool not in agent's config"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=f"{{@TOOL: {ref}}} references non-existent tool",
                fix=(
                    "Available tools:"
                    f" {', '.join(sorted(context.all_known_tools))}"
                ),
            )
            for ref in _extract_tool_refs(content)
            if ref not in context.all_known_tools
        ]


def _check_wrong_syntax(
    rule_obj: Rule,
    file_path: Path,
    content: str,
    context: LintContext,
    patterns: list[tuple[str, str]],
    fix: str,
) -> list[LintResult]:
    """Shared logic for I010 and I011 — detect wrong reference syntax."""
    rel = str(file_path.relative_to(context.project_root))
    results = []
    for i, line in enumerate(content.split("\n"), 1):
        for pattern, label in patterns:
            for m in re.finditer(pattern, line):
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=i,
                        message=(
                            "Wrong reference syntax:"
                            f" {label} found:"
                            f" {m.group(0)}"
                        ),
                        fix=fix,
                    )
                )
    return results


@rule("instructions")
class WrongAgentSyntax(Rule):
    id = "I010"
    name = "wrong-agent-syntax"
    description = "Wrong agent reference syntax (must use {@AGENT: Name})"
    default_severity = Severity.ERROR

    WRONG_PATTERNS = [
        (r"\$\{AGENT:([^}]+)\}", "${AGENT:...}"),
        (r"(?<!\{)\{AGENT:([^}]+)\}", "{AGENT:...}"),
        (r"\$\{@AGENT:([^}]+)\}", "${@AGENT:...}"),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_wrong_syntax(
            self,
            file_path,
            content,
            context,
            self.WRONG_PATTERNS,
            fix="Use {@AGENT: Display Name} (with @ sign, spaces in name)",
        )


@rule("instructions")
class WrongToolSyntax(Rule):
    id = "I011"
    name = "wrong-tool-syntax"
    description = "Wrong tool reference syntax (must use {@TOOL: Name})"
    default_severity = Severity.ERROR

    WRONG_PATTERNS = [
        (r"\$\{TOOL:([^}]+)\}", "${TOOL:...}"),
        (r"(?<!\{)\{TOOL:([^}]+)\}", "{TOOL:...}"),
        (r"\$\{@TOOL:([^}]+)\}", "${@TOOL:...}"),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_wrong_syntax(
            self,
            file_path,
            content,
            context,
            self.WRONG_PATTERNS,
            fix="Use {@TOOL: Tool Name}",
        )


@rule("instructions")
class UnusedToolInConfig(Rule):
    id = "I012"
    name = "unused-tool-in-config"
    description = "Tool in agent JSON but not referenced in instruction"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        config = _load_agent_config(file_path)
        if not config:
            return []

        config_tools = set(config.get("tools", []))
        instruction_refs = _extract_tool_refs(content)
        unused = config_tools - instruction_refs - {"end_session"}

        agent_json_rel = str(
            (file_path.parent / f"{file_path.parent.name}.json").relative_to(
                context.project_root
            )
        )
        return [
            self.make_result(
                file=agent_json_rel,
                message=(
                    f"Agent config lists tool"
                    f" '{tool}' but instruction"
                    " never references it"
                ),
                fix=(
                    f"Add {{@TOOL: {tool}}} in"
                    " instruction, or remove"
                    " from agent config if"
                    " not needed"
                ),
            )
            for tool in sorted(unused)
        ]


@rule("instructions")
class ToolNotInConfig(Rule):
    id = "I013"
    name = "tool-not-in-config"
    description = "Tool referenced in instruction but not in agent JSON"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        config = _load_agent_config(file_path)
        if not config:
            return []

        config_tools = set(config.get("tools", []))
        missing = _extract_tool_refs(content) - config_tools
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    "Instruction references"
                    f" {{@TOOL: {ref}}} but agent"
                    " config does not list it"
                ),
                fix=f"Add '{ref}' to tools array, or remove the reference.",
            )
            for ref in sorted(missing)
        ]
