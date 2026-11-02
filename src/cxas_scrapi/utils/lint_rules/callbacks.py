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

Validates agent callback Python files against CXAS conventions.
"""

import ast
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


def _find_entry_function(content: str, expected_fn: str) -> re.Match | None:
    """Find the entry callback function, not helper functions."""
    entry = re.search(rf"def\s+({re.escape(expected_fn)})\s*\(", content)
    return entry


def _get_args(content: str, fn_name: str) -> list[str] | None:
    """Return argument names of fn_name using AST, or None if not found."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn_name
        ):
            return [arg.arg for arg in node.args.args]
    return None


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

        args = _get_args(content, expected_fn)
        if args is None:
            return []
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


def _function_returns_dict(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return True if fn_node directly returns a dict literal."""
    stack = list(fn_node.body)
    while stack:
        stmt = stack.pop()
        if isinstance(
            stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
            return True
        for child in ast.iter_child_nodes(stmt):
            if not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                stack.append(child)
    return False


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

        returns_dict = False
        try:
            tree = ast.parse(content)
            expected_fn, _ = CALLBACK_SIGNATURES.get(cb_type, (None, None))
            top_fns = [
                n
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            target_fns = [n for n in top_fns if n.name == expected_fn]
            if not target_fns:
                target_fns = [n for n in top_fns if not n.name.startswith("_")]
            returns_dict = any(_function_returns_dict(fn) for fn in target_fns)
        except SyntaxError:
            returns_dict = (
                "return {" in content and "LlmResponse" not in content
            )

        if returns_dict:
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


_PYTHON_COLLECTION_METHODS: frozenset[str] = frozenset(
    {
        "add",
        "append",
        "clear",
        "copy",
        "discard",
        "extend",
        "get",
        "items",
        "keys",
        "pop",
        "remove",
        "setdefault",
        "update",
        "values",
    }
)


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
        tool_calls = re.findall(r"\btools\.(\w+)\s*\(", content)
        for tool_name in tool_calls:
            if tool_name in _PYTHON_COLLECTION_METHODS:
                continue
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
