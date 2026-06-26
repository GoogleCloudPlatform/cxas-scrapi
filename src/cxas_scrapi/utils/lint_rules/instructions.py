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

"""Instruction lint rules (I001-I016).

Validates agent instruction files against CXAS design guide best practices.
"""

import json
import re
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    ToolsetValidationBehavior,
    get_toolset_tools,
    rule,
)

TOOL_REF_PATTERN = re.compile(r"\{@TOOL:\s*([^}]+)\}")


def _load_agent_config(file_path: Path) -> dict | None:
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


def _find_line(content: str, needle: str) -> int | None:
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
        if count >= 3:
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
        referenced = _extract_tool_refs(content)

        # Filter out references that match workspace bypass prefixes
        # (e.g., MCP/Connector toolsets)
        bypass_pfx = getattr(context, "bypass_tool_prefixes", None)
        if bypass_pfx:
            referenced = {
                ref
                for ref in referenced
                if not any(ref.startswith(pfx) for pfx in bypass_pfx)
            }

        return [
            self.make_result(
                file=rel,
                message=f"{{@TOOL: {ref}}} references non-existent tool",
                fix=(
                    "Available tools:"
                    f" {', '.join(sorted(context.all_known_tools))}"
                ),
            )
            for ref in referenced
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

        app_root = file_path.parent.parent.parent

        config_tools = set(config.get("tools", []))
        bypass_prefixes = set()

        # Resolve toolsets and add their tools to config_tools
        for ts_entry in config.get("toolsets", []):
            if isinstance(ts_entry, dict):
                toolset_name = ts_entry.get("toolset")
                allowed_tool_ids = ts_entry.get("toolIds") or ts_entry.get(
                    "tool_ids"
                )
                if toolset_name:
                    res = get_toolset_tools(
                        app_root, toolset_name, allowed_tool_ids
                    )
                    if res.behavior == ToolsetValidationBehavior.BYPASS:
                        # Skip operation-level checks for MCP/Connector toolsets
                        bypass_prefixes.add(f"{toolset_name}_")
                    else:
                        config_tools.update(res.tools)

        referenced = _extract_tool_refs(content)

        # Filter out referenced tools matching bypass prefixes
        if bypass_prefixes:
            referenced = {
                ref
                for ref in referenced
                if not any(ref.startswith(pfx) for pfx in bypass_prefixes)
            }

        missing = referenced - config_tools
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    "Instruction references"
                    f" {{@TOOL: {ref}}} but agent"
                    " config does not list it"
                ),
                fix=f"Add '{ref}' to tools/toolsets, or remove the reference.",
            )
            for ref in sorted(missing)
        ]


@rule("instructions")
class MissingCurrentDate(Rule):
    id = "I014"
    name = "missing-current-date"
    description = (
        "Instruction should reference {current_date} so the"
        " agent knows today's date"
    )
    default_severity = Severity.WARNING

    VALID_PATTERNS = re.compile(r"\{current_date\}|\{\{current_date\}\}")

    _APPLICABLE_FILES = {"instruction.txt", "global_instruction.txt"}

    def _global_instruction_has_date(self, context: LintContext) -> bool:
        """Check if global_instruction.txt already references current_date."""
        global_inst = context.app_dir / "global_instruction.txt"
        if global_inst.exists():
            return bool(self.VALID_PATTERNS.search(global_inst.read_text()))
        return False

    def _all_agent_instructions_have_date(self, context: LintContext) -> bool:
        """Check if every agent instruction.txt references current_date."""
        agents_dir = context.app_dir / "agents"
        if not agents_dir.exists():
            return True
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            inst = agent_dir / "instruction.txt"
            if inst.exists() and not self.VALID_PATTERNS.search(
                inst.read_text()
            ):
                return False
        return True

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in self._APPLICABLE_FILES:
            return []
        if self.VALID_PATTERNS.search(content):
            return []
        # global_instruction.txt has current_date → all agents covered
        if self._global_instruction_has_date(context):
            return []
        # Every agent instruction.txt has current_date → also fine
        if self._all_agent_instructions_have_date(context):
            return []
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    "No current_date reference found"
                    " — without it the agent will"
                    " not know today's date"
                ),
                fix=(
                    "Add {current_date} or"
                    " {{current_date}} to the"
                    " instruction or global"
                    " instruction"
                ),
            )
        ]


@rule("instructions")
class BannedLegacyXmlTags(Rule):
    id = "I015"
    name = "banned-legacy-xml-tags"
    description = (
        "Instruction contains legacy CamelCase / state-machine XML tags"
        " that diverge from the canonical taskflow schema"
    )
    default_severity = Severity.ERROR

    BANNED_TAGS = (
        "<Agent>",
        "<Conversation_Schema>",
        "<Persona>",
        "<Role>",
        "<General_Instruction>",
        "<Context>",
        "<state",
        "<transitions>",
        "<transition ",
    )

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=_find_line(content, tag),
                message=(
                    f"Banned legacy XML tag '{tag}' — use the canonical"
                    " lowercase taskflow schema instead"
                ),
                fix=(
                    "Rewrite into <role>/<persona>/<primary_goal>/"
                    "<constraints>/<guidelines>/<taskflow>/<subtask>/"
                    "<step>/<trigger>/<action>"
                ),
            )
            for tag in self.BANNED_TAGS
            if tag in content
        ]


# --- I016: prose state machine -------------------------------------------
#
# Each signal below is one deterministic surface form of "I built a state
# machine in the prompt". They are grouped into four categories so the rule
# can fire on *co-occurrence* (several mild tells together) rather than on
# any single weak phrase. A handful are flagged high-confidence (HC): they
# are near-definitional FSM markers (reading/writing a retry counter or a
# named state, an orchestrator agent_action interpreter loop, a state machine
# named in a step) and fire on their own.
#
# Surface forms are kept disjoint from I003 (bare ``IF ... ELSE``) and I005
# (``<conditional_logic>``) so no line is reported by two rules: ``if_code``
# only matches a quoted ALL_CAPS enum or a ``result.<field>`` subject, never a
# bare IF/ELSE, and nothing here keys on the ``<conditional_logic>`` tag.

# (name, compiled regex, category, high_confidence)
_SM_SIGNALS: list[tuple[str, re.Pattern, str, bool]] = [
    # --- control_flow: hand-driven GOTO edges between named steps --------
    # Plain forward navigation ("proceed to subtask conclusion") is weak: it
    # is also the design-guide-endorsed way to move between subtasks, so it
    # only contributes to the total and never fires on its own.
    (
        "forward_jump",
        re.compile(
            r"(?i)\b(?:proceed|transition|route|go|continue|jump)\s+to\s+"
            r"(?:the\s+)?(?:sub-?task|step)\b"
        ),
        "control_flow",
        False,
    ),
    # Back-edges create a cycle — the hallmark of a hand-rolled FSM.
    (
        "loop_edge",
        re.compile(
            r"(?i)\b(?:loop\s+back|go\s+back|come\s+back|return|back)\s+to\s+"
            r"(?:the\s+)?(?:sub-?task|step)\b"
        ),
        "control_flow",
        False,
    ),
    # A jump gated by a condition is a dispatch-table edge ("Else if user
    # says 'edit', go to step X") — not benign sequential navigation. Only
    # if/else/otherwise count: a plain "when done, proceed to step" is
    # sequencing, not a branch.
    (
        "conditional_jump",
        re.compile(
            r"(?i)\b(?:if|else|otherwise)\b.*\b"
            r"(?:go|proceed|route|transition|jump|loop|continue|return)\s+"
            r"(?:back\s+)?to\s+(?:the\s+)?(?:sub-?task|step)\b"
        ),
        "control_flow",
        False,
    ),
    (
        "arrow",
        re.compile(
            r"->\s*(?:Proceed|Call|Go|Loop|Ask|If|STOP|Transition|Route)"
        ),
        "control_flow",
        False,
    ),
    (
        "stop",
        re.compile(r"(?<![A-Za-z])STOP\.(?:\s|$)"),
        "control_flow",
        False,
    ),
    # --- retry_state: counters the LLM is asked to read / advance --------
    (
        "counter_cmp",
        re.compile(
            r"(?i)\{?\b\w*(?:_counter|_count|noCount|retry|_retry)\b\}?\s+"
            r"(?:is|==|!=)\s+(?:\"?(?:0|1|2|3)\"?|null|not\s+set)"
        ),
        "retry_state",
        True,
    ),
    (
        "update_counter",
        re.compile(
            r"(?i)update_params\w*[^\n]*\b\w*"
            r"(?:counter|_count|_retry|noCount)\b\s*[:=]\s*['\"]?[0-3]['\"]?"
        ),
        "retry_state",
        True,
    ),
    (
        "ordinal",
        re.compile(
            r"(?i)\b(?:1st|2nd|3rd|first|second|third)\s+"
            r"(?:consecutive\s+)?(?:attempt|occurrence|repeat|failure|try)\b"
        ),
        "retry_state",
        False,
    ),
    # --- state_inspect: reading / writing an explicit state variable -----
    (
        "state_var_eq",
        re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}\s*(?:==|!=)\s*\"?\w+"),
        "state_inspect",
        False,
    ),
    # Persisting an UPPER_SNAKE state value to a *_state / *_status field,
    # e.g. update_params(value='{"flow_status": "BAG_VERIFICATION"}').
    (
        "state_write",
        re.compile(
            r"(?i)\b(?:flow_status|[a-z][a-z0-9_]*_state"
            r"|[a-z][a-z0-9_]*_status)\b"
            r"[\"']?\s*[:=,]\s*[\"'{]*\s*[\"']?[A-Z][A-Z0-9_]{2,}"
        ),
        "state_inspect",
        True,
    ),
    # Dispatching on that state in a trigger ("When user is in BAG_VERIFICATION
    # flow.").
    (
        "state_trigger",
        re.compile(
            r"(?i)\bwhen\b[^<>\n]*\b(?:is\s+in|in\s+the|status\s+is|state\s+is)"
            r"\b[^<>\n]*\b[A-Z][A-Z0-9_]{2,}\b"
        ),
        "state_inspect",
        False,
    ),
    # --- orchestrator_fsm: an externalized FSM driven from prose ---------
    (
        "agent_action_verbatim",
        re.compile(
            r"(?i)(?:follow|execute)\s+(?:the\s+)?returned\s+"
            r"[`'\"]?agent_action[`'\"]?\s+verbatim"
        ),
        "orchestrator_fsm",
        True,
    ),
    (
        "lookup_flag",
        re.compile(r"(?i)lookup_flag\s*[:=]\s*['\"][A-Z][A-Z_]{2,}['\"]"),
        "orchestrator_fsm",
        True,
    ),
    # "state machine" named in a step/subtask (e.g. <step name="Run Qty
    # Capture State Machine">). Scoped to names so that merely *describing*
    # FSM logic as living in code does not trip the rule.
    (
        "state_machine_step",
        re.compile(
            r"(?i)(?:name\s*=\s*[\"'][^\"']*|<(?:step|subtask)\b[^>]*)"
            r"state[ -]?machine"
        ),
        "orchestrator_fsm",
        True,
    ),
    # IF on a tool result code / quoted enum — disjoint from I003's IF/ELSE.
    (
        "if_code",
        re.compile(
            r"^\s*[-*]?\s*(?:IF|If)\s+"
            r"(?:\"[A-Z_]{3,}\"|result\.[a-z_]+\s*(?:==|is|differs))"
        ),
        "orchestrator_fsm",
        False,
    ),
]

_SM_STRONG_EDGES = {"loop_edge", "conditional_jump"}


@rule("instructions")
class ProseStateMachine(Rule):
    id = "I016"
    name = "prose-state-machine"
    description = (
        "Instruction encodes a state machine in prose (named-step GOTOs,"
        " retry counters, STOP tokens, state writes, orchestrator"
        " agent_action traversal) instead of in deterministic code"
    )
    default_severity = Severity.WARNING

    # Configurable firing levers (options.I016 in cxaslint.yaml).
    DEFAULT_MIN_DISTINCT_CATEGORIES = 2
    DEFAULT_MIN_TOTAL = 4
    DEFAULT_MIN_STRONG_EDGES = 3

    @staticmethod
    def _mask_examples(content: str) -> list[str]:
        """Blank out whole <inline_example>...</inline_example> regions.

        Control-flow tokens that appear only in a sample transcript should not
        count toward the score. Region-scoped (not just the tag lines) so a
        multi-line example body is fully excluded.
        """
        masked = []
        inside = False
        for line in content.split("\n"):
            opens = "<inline_example" in line
            closes = "</inline_example" in line
            if inside or opens:
                masked.append("")
            else:
                masked.append(line)
            if opens and not closes:
                inside = True
            elif closes:
                inside = False
        return masked

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        opts = context.options.get("I016", {})
        min_distinct = opts.get(
            "min_distinct_categories", self.DEFAULT_MIN_DISTINCT_CATEGORIES
        )
        min_total = opts.get("min_total", self.DEFAULT_MIN_TOTAL)
        min_strong = opts.get("min_strong_edges", self.DEFAULT_MIN_STRONG_EDGES)

        lines = self._mask_examples(content)

        counts: dict[str, int] = {}
        first_line: dict[str, int] = {}
        for name, pattern, _category, _hc in _SM_SIGNALS:
            for i, line in enumerate(lines, 1):
                hits = len(pattern.findall(line))
                if hits:
                    counts[name] = counts.get(name, 0) + hits
                    first_line.setdefault(name, i)

        if not counts:
            return []

        categories = {
            cat for name, _pat, cat, _hc in _SM_SIGNALS if counts.get(name)
        }
        hc_hits = [
            name
            for name, _pat, _cat, hc in _SM_SIGNALS
            if hc and counts.get(name)
        ]
        total = sum(counts.values())
        distinct_categories = len(categories)
        strong_edges = sum(counts.get(n, 0) for n in _SM_STRONG_EDGES)

        fires = (
            bool(hc_hits)
            or (distinct_categories >= min_distinct and total >= min_total)
            or strong_edges >= min_strong
        )
        if not fires:
            return []

        # Anchor the single result at the strongest evidence available.
        anchor_order = (
            hc_hits
            + ["conditional_jump", "loop_edge"]
            + [name for name, *_ in _SM_SIGNALS]
        )
        anchor_line = next(
            (first_line[n] for n in anchor_order if n in first_line), None
        )

        summary = ", ".join(
            f"{name}×{counts[name]}"
            for name, *_ in _SM_SIGNALS
            if counts.get(name)
        )
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=anchor_line,
                message=(
                    "Instruction encodes a state machine in prose"
                    f" ({distinct_categories} categories, {total} signals:"
                    f" {summary}). Control flow should be deterministic code,"
                    " not LLM-interpreted text."
                ),
                fix=(
                    "Move control flow out of the prompt. Put retry/attempt"
                    " counters and failure limits in a before_model/"
                    "after_model callback (slot-filling keeps retry state in"
                    " Python — never ask the LLM to read/increment a {counter}"
                    " variable). Replace 'Proceed to subtask X'/'Loop back to"
                    " step Y'/STOP GOTOs, state-variable writes, and"
                    " IF-on-agent_action dispatch with declarative"
                    " slot-filling DAG transitions so the framework owns"
                    " routing. Write a goal-oriented prompt that states intent,"
                    " not a transition table."
                ),
            )
        ]
