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

"""Strict structural validator for canonical CXAS instruction XML.

Used by the consolidation pipeline to gate Gemini-synthesized instruction
output. Returns a list of diagnostic strings; an empty list means the
instruction passes. Diagnostics are formatted to be appended verbatim to
a re-prompt when Gemini drifts from the canonical schema.

The canonical schema is defined in docs/design-guide/instruction-design.md
and demonstrated by examples/bella_notte/agents/*/instruction.txt.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

REQUIRED_TOP_LEVEL_TAGS: tuple[str, ...] = ("role", "persona")

# Tags that, if present anywhere, force <taskflow> to also be present at
# the top level. Specialist agents that are purely <role>+<persona>+
# <guidelines> remain valid; structured behavior steps must live inside
# <taskflow>.
TASKFLOW_INDICATOR_TAGS: frozenset[str] = frozenset({"subtask", "step"})

# Tags from the legacy non-canonical schema. Their presence is a strong
# signal Gemini fell back to the old <Agent><Conversation_Schema> shape.
BANNED_TAGS: frozenset[str] = frozenset(
    {
        "Agent",
        "Name",
        "Role",
        "Persona",
        "Context",
        "General_Instruction",
        "Conversation_Schema",
        "state",
        "transitions",
        "transition",
        "conditional_logic",
        "handling_user_negative_sentiment",
        "communication_style",
        "prohibited_topics",
    }
)

_WRONG_TOOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\{TOOL:[^}]+\}"),
    re.compile(r"(?<!\{)\{TOOL:[^}]+\}"),
    re.compile(r"\$\{@TOOL:[^}]+\}"),
)
_WRONG_AGENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\{AGENT:[^}]+\}"),
    re.compile(r"(?<!\{)\{AGENT:[^}]+\}"),
    re.compile(r"\$\{@AGENT:[^}]+\}"),
)


def validate_canonical_instruction(text: str) -> list[str]:
    """Validate instruction XML against the canonical schema.

    Returns a list of human-readable diagnostic strings. Empty list means
    the instruction passes all checks.
    """
    if not text or not text.strip():
        return [
            "Instruction is empty.",
            "Missing required top-level tag <role>.",
            "Missing required top-level tag <persona>.",
        ]

    try:
        root = ET.fromstring(f"<__doc__>{text}</__doc__>")
    except ET.ParseError as e:
        return [f"XML syntax error: {e}"]

    diagnostics: list[str] = []
    diagnostics.extend(_check_banned_tags(root))
    diagnostics.extend(_check_required_top_level(root))
    diagnostics.extend(_check_taskflow_structure(root))
    diagnostics.extend(_check_reference_syntax(text))
    return diagnostics


def _check_banned_tags(root: ET.Element) -> list[str]:
    """Flag any tag from the legacy non-canonical schema."""
    seen: set[str] = set()
    diagnostics: list[str] = []
    for elem in root.iter():
        if elem.tag in BANNED_TAGS and elem.tag not in seen:
            seen.add(elem.tag)
            diagnostics.append(
                f"Found banned tag <{elem.tag}>; remove it and use the"
                " canonical lowercase taskflow schema (see"
                " docs/design-guide/instruction-design.md)."
            )
    return diagnostics


def _check_required_top_level(root: ET.Element) -> list[str]:
    """Require <role> and <persona> always; <taskflow> when steps exist."""
    top_level = {child.tag for child in root}
    diagnostics = [
        f"Missing required top-level tag <{tag}>."
        for tag in REQUIRED_TOP_LEVEL_TAGS
        if tag not in top_level
    ]
    if "taskflow" in top_level:
        return diagnostics
    has_indicator = any(
        elem.tag in TASKFLOW_INDICATOR_TAGS for elem in root.iter()
    )
    if has_indicator:
        diagnostics.append(
            "Missing required top-level tag <taskflow>; <subtask> and"
            " <step> elements must be wrapped in a <taskflow>."
        )
    return diagnostics


def _check_taskflow_structure(root: ET.Element) -> list[str]:
    """Enforce subtask/step/trigger/action nesting inside every taskflow."""
    diagnostics: list[str] = []
    for taskflow in root.iter("taskflow"):
        subtasks = list(taskflow.findall("subtask"))
        if not subtasks:
            diagnostics.append(
                "<taskflow> has 0 <subtask> children; must have at least 1."
            )
            continue
        for subtask in subtasks:
            diagnostics.extend(_check_subtask(subtask))
    return diagnostics


def _check_subtask(subtask: ET.Element) -> list[str]:
    diagnostics: list[str] = []
    name = subtask.get("name")
    label = name or "?"
    if not name:
        diagnostics.append(
            "<subtask> is missing the required 'name' attribute."
        )
    steps = list(subtask.findall("step"))
    if not steps:
        diagnostics.append(
            f'<subtask name="{label}"> has 0 <step> children; must have'
            " at least 1."
        )
        return diagnostics
    for step in steps:
        diagnostics.extend(_check_step(step, subtask_label=label))
    return diagnostics


def _check_step(step: ET.Element, *, subtask_label: str) -> list[str]:
    diagnostics: list[str] = []
    step_name = step.get("name")
    step_label = step_name or "?"
    if not step_name:
        diagnostics.append(
            f'<step> in subtask "{subtask_label}" is missing the required'
            " 'name' attribute."
        )
    triggers = step.findall("trigger")
    actions = step.findall("action")
    if len(triggers) != 1:
        diagnostics.append(
            f'<step name="{step_label}"> must contain exactly 1 <trigger>'
            f" (found {len(triggers)})."
        )
    if len(actions) != 1:
        diagnostics.append(
            f'<step name="{step_label}"> must contain exactly 1 <action>'
            f" (found {len(actions)})."
        )
    return diagnostics


def _check_reference_syntax(text: str) -> list[str]:
    """Flag wrong-form {TOOL:...} / {AGENT:...} reference syntax."""
    diagnostics: list[str] = []
    for pattern in _WRONG_TOOL_PATTERNS:
        for match in pattern.finditer(text):
            diagnostics.append(
                f"Wrong tool reference syntax: '{match.group(0)}';"
                " use {@TOOL: name}."
            )
    for pattern in _WRONG_AGENT_PATTERNS:
        for match in pattern.finditer(text):
            diagnostics.append(
                f"Wrong agent reference syntax: '{match.group(0)}';"
                " use {@AGENT: name}."
            )
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: validate one or more instruction files."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m cxas_scrapi.migration.xml_validator"
            " FILE [FILE ...]",
            file=sys.stderr,
        )
        return 2

    exit_code = 0
    for arg in args:
        path = Path(arg)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{arg}: ERROR could not read: {exc}")
            exit_code = 2
            continue
        diagnostics = validate_canonical_instruction(text)
        if diagnostics:
            print(f"{arg}: FAIL ({len(diagnostics)} issue(s))")
            for diag in diagnostics:
                print(f"  - {diag}")
            exit_code = max(exit_code, 1)
        else:
            print(f"{arg}: OK")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
