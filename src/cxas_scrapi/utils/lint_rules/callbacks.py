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

"""Callback lint rules (C001-C008).

Validates agent callback Python files against GECX conventions.
"""

import re
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

CALLBACK_SIGNATURES = {
    "before_model_callbacks": (
        "before_model_callback",
        ["callback_context", "llm_request"],
    ),
    "after_model_callbacks": (
        "after_model_callback",
        ["callback_context", "llm_response"],
    ),
    "before_agent_callbacks": ("before_agent_callback", ["callback_context"]),
    "after_agent_callbacks": ("after_agent_callback", ["callback_context"]),
    "before_tool_callbacks": (
        "before_tool_callback",
        ["tool", "input", "callback_context"],
    ),
    "after_tool_callbacks": (
        "after_tool_callback",
        ["tool", "input", "callback_context", "tool_response"],
    ),
}

EXPECTED_TYPED_SIGNATURES = {
    "before_model_callbacks": {
        "fn": "before_model_callback",
        "params": {
            "callback_context": "CallbackContext",
            "llm_request": "LlmRequest",
        },
        "return": "Optional[LlmResponse]",
    },
    "after_model_callbacks": {
        "fn": "after_model_callback",
        "params": {
            "callback_context": "CallbackContext",
            "llm_response": "LlmResponse",
        },
        "return": "Optional[LlmResponse]",
    },
    "before_agent_callbacks": {
        "fn": "before_agent_callback",
        "params": {"callback_context": "CallbackContext"},
        "return": "Optional[Content]",
    },
    "after_agent_callbacks": {
        "fn": "after_agent_callback",
        "params": {"callback_context": "CallbackContext"},
        "return": "Optional[Content]",
    },
    "before_tool_callbacks": {
        "fn": "before_tool_callback",
        "params": {
            "tool": "Tool",
            "input": "dict[str, Any]",
            "callback_context": "CallbackContext",
        },
        "return": "Optional[dict[str, Any]]",
    },
    "after_tool_callbacks": {
        "fn": "after_tool_callback",
        "params": {
            "tool": "Tool",
            "input": "dict[str, Any]",
            "callback_context": "CallbackContext",
            "tool_response": "dict[str, Any]",
        },
        "return": "Optional[dict[str, Any]]",
    },
}


def _find_entry_function(content: str, expected_fn: str) -> re.Match | None:
    """Find the entry callback function, not helper functions."""
    entry = re.search(rf"def\s+({re.escape(expected_fn)})\s*\(", content)
    return entry


@rule("callbacks")
class WrongFunctionName(Rule):
    id = "C001"
    name = "callback-fn-name"
    description = "Callback function name must match callback type"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        cb_type = file_path.parent.parent.name
        expected_fn, _ = CALLBACK_SIGNATURES.get(cb_type, (None, None))
        if not expected_fn:
            return []

        all_fns = re.findall(r"def\s+(\w+)\s*\(", content)
        if not all_fns:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message="No function definition found in callback file",
                    fix=f"Define: def {expected_fn}(...):",
                )
            ]

        if expected_fn not in all_fns:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message=(
                        f"No '{expected_fn}' function"
                        f" found (found:"
                        f" {', '.join(all_fns)})"
                    ),
                    fix=f"Add entry function: def {expected_fn}(...)",
                )
            ]
        return []


@rule("callbacks")
class WrongArgCount(Rule):
    id = "C002"
    name = "callback-args"
    description = "Callback must have correct argument count for its type"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        cb_type = file_path.parent.parent.name
        expected_fn, expected_args = CALLBACK_SIGNATURES.get(
            cb_type, (None, None)
        )
        if not expected_args:
            return []

        entry = _find_entry_function(content, expected_fn)
        if not entry:
            return []

        args_match = re.search(
            r"def\s+" + re.escape(expected_fn) + r"\s*\(([^)]*)\)", content
        )
        if not args_match:
            return []

        args = [
            a.strip().split(":")[0].strip()
            for a in args_match.group(1).split(",")
            if a.strip()
        ]
        if len(args) != len(expected_args):
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message=(
                        f"Expected"
                        f" {len(expected_args)}"
                        f" args"
                        f" ({', '.join(expected_args)}),"
                        f" got {len(args)}"
                        f" ({', '.join(args)})"
                    ),
                    fix=(
                        f"Use signature: def"
                        f" {expected_fn}"
                        f"({', '.join(expected_args)}):"
                    ),
                )
            ]
        return []


@rule("callbacks")
class CamelCaseFunction(Rule):
    id = "C003"
    name = "callback-camelcase"
    description = "CES requires snake_case function names, not camelCase"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        camel_fns = re.findall(r"def\s+((?:[a-z]+[A-Z])\w*)\s*\(", content)
        for fn in camel_fns:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"camelCase function '{fn}' — CES requires snake_case"
                    ),
                    fix="Rename to snake_case",
                )
            )
        return results


@rule("callbacks")
class ReturnsDictNotLlmResponse(Rule):
    id = "C004"
    name = "callback-return-type"
    description = "Model callbacks should return LlmResponse, not dict"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        cb_type = file_path.parent.parent.name

        if cb_type not in ("before_model_callbacks", "after_model_callbacks"):
            return []

        if "return {" in content and "LlmResponse" not in content:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Callback returns a dict"
                        " — should return"
                        " LlmResponse or None"
                    ),
                    fix=(
                        "Use: return"
                        " LlmResponse.from_parts("
                        "parts=[Part.from_text("
                        "text='...')])"
                    ),
                )
            ]
        return []


@rule("callbacks")
class HardcodedPhraseList(Rule):
    id = "C005"
    name = "callback-hardcoded-phrases"
    description = (
        "Hardcoded phrase lists for intent"
        " detection — keep detection in"
        " instructions"
    )
    default_severity = Severity.WARNING

    PATTERNS = [
        r'\[.*"[^"]+",\s*"[^"]+",\s*"[^"]+".*\]',
        r"if\s+.*\bin\s+\[",
        r"any\(\s*\w+\s+in\s+",
    ]
    DETECTION_KEYWORDS = [
        "detect",
        "intent",
        "phrase",
        "keyword",
        "profan",
        "escalat",
        "frustrat",
    ]

    def _is_detection_line(self, line: str) -> bool:
        lower = line.lower()
        return any(re.search(p, line) for p in self.PATTERNS) and any(
            kw in lower for kw in self.DETECTION_KEYWORDS
        )

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=i,
                message=(
                    "Hardcoded phrase list for"
                    " intent detection"
                    " — misses natural variations"
                ),
                fix=(
                    "Keep detection in instructions"
                    " (LLM understands intent)."
                    " Use callbacks for"
                    " execution only."
                ),
            )
            for i, line in enumerate(content.split("\n"), 1)
            if self._is_detection_line(line)
        ]


@rule("callbacks")
class BareExcept(Rule):
    id = "C006"
    name = "callback-bare-except"
    description = "Bare except without logging swallows errors silently"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "except:" or re.match(r"except\s*:", stripped):
                results.append(
                    self.make_result(
                        file=rel,
                        line=i,
                        message=(
                            "Bare 'except:' — catches"
                            " all errors silently."
                            " Platform tool errors"
                            " bypass try/except."
                        ),
                        fix=(
                            "Use 'except Exception"
                            " as e:' with logging,"
                            " or catch specific"
                            " exceptions"
                        ),
                    )
                )
        return results


@rule("callbacks")
class ToolNamingConvention(Rule):
    id = "C007"
    name = "callback-tool-naming"
    description = "Verify tools.* call uses correct naming convention"
    default_severity = Severity.INFO

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        tool_calls = re.findall(r"tools\.(\w+)\s*\(", content)
        for tool_name in tool_calls:
            if tool_name not in context.all_known_tools:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"tools.{tool_name}()"
                            " — verify naming:"
                            " Python tools use"
                            " function name, API"
                            " connectors use"
                            " DisplayName_OperationId"
                        ),
                        fix=(
                            "Check the exact tool"
                            " name from the platform."
                            " Platform errors from"
                            " wrong names bypass"
                            " try/except."
                        ),
                    )
                )
        return results


@rule("callbacks")
class MissingTypingImport(Rule):
    id = "C008"
    name = "callback-missing-typing-import"
    description = (
        "Callback uses typing types"
        " (Optional, Iterator, etc.)"
        " without importing them"
    )
    default_severity = Severity.ERROR

    TYPING_TYPES = {
        "Optional",
        "Iterator",
        "List",
        "Dict",
        "Tuple",
        "Set",
        "Union",
        "Any",
    }

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not str(file_path).endswith(".py"):
            return []

        results = []
        rel = str(file_path.relative_to(context.project_root))

        has_typing_import = (
            "from typing import" in content or "import typing" in content
        )
        if has_typing_import:
            return []

        used_types = []
        for type_name in self.TYPING_TYPES:
            patterns = [
                rf"-> {type_name}\[",
                rf"-> {type_name}\b",
                rf": {type_name}\[",
                rf": {type_name}\b",
            ]
            for pattern in patterns:
                if re.search(pattern, content):
                    used_types.append(type_name)
                    break

        if used_types:
            types_str = ", ".join(sorted(set(used_types)))
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Uses {types_str} without"
                        " importing from typing"
                        " — will fail with 'name"
                        " not defined' at push"
                        " time"
                    ),
                    fix=f"Add: from typing import {types_str}",
                )
            )

        return results


@rule("callbacks")
class WrongCallbackSignature(Rule):
    id = "C009"
    name = "callback-signature"
    description = "Callback function must have correct type annotations"
    default_severity = Severity.ERROR

    def check(  # noqa: C901
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        cb_type = file_path.parent.parent.name
        expected = EXPECTED_TYPED_SIGNATURES.get(cb_type)
        if not expected:
            return []

        fn_name = expected["fn"]
        pattern = (
            rf"def\s+{re.escape(fn_name)}"
            r"\s*\(([^)]*)\)(\s*->\s*[^:]+)?:"
        )
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            return []

        args_str = match.group(1)
        return_str = match.group(2)
        params_str = ", ".join(
            f"{k}: {v}" for k, v in expected["params"].items()
        )
        expected_sig = f"def {fn_name}({params_str}) -> {expected['return']}:"

        results = []
        for param in (p.strip() for p in args_str.split(",") if p.strip()):
            parts = param.split(":")
            param_name = parts[0].strip()
            expected_type = expected["params"].get(param_name)
            if not expected_type:
                continue
            if len(parts) < 2:  # noqa: PLR2004
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Parameter"
                            f" '{param_name}'"
                            " missing type"
                            " annotation,"
                            f" expected"
                            f" '{param_name}:"
                            f" {expected_type}'"
                        ),
                        fix=expected_sig,
                    )
                )
            elif parts[1].strip() != expected_type:
                actual_t = parts[1].strip()
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Parameter"
                            f" '{param_name}'"
                            f" has type"
                            f" '{actual_t}',"
                            f" expected"
                            f" '{expected_type}'"
                        ),
                        fix=expected_sig,
                    )
                )

        if not return_str:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Missing return type"
                        " annotation, expected"
                        f" '-> {expected['return']}'"
                    ),
                    fix=expected_sig,
                )
            )
        else:
            actual = return_str.strip().lstrip("->").strip()
            if actual != expected["return"]:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Return type is"
                            f" '{actual}', expected"
                            f" '{expected['return']}'"
                        ),
                        fix=expected_sig,
                    )
                )

        return results


@rule("callbacks")
class InvalidPythonSyntax(Rule):
    id = "C010"
    name = "callback-python-syntax"
    description = "Callback Python file must have valid syntax"
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        if not str(file_path).endswith(".py"):
            return []
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
                        " callbacks to silently"
                        " fail on the platform"
                    ),
                )
            ]
        return []
