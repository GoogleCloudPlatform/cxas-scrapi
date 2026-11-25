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
import warnings
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    is_voice_app,
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


def _resolve_cb_type(file_path: Path) -> str:
    """Resolve callback type from nested or flat callback directory layout."""
    parent_type = file_path.parent.parent.name
    if parent_type in CALLBACK_SIGNATURES:
        return parent_type
    dir_name = file_path.parent.name
    for cb_type in CALLBACK_SIGNATURES:
        if dir_name == cb_type or dir_name.startswith(f"{cb_type}_"):
            return cb_type
    return parent_type


def _find_entry_function(content: str, expected_fn: str) -> re.Match | None:
    """Find the entry callback function, not helper functions."""
    entry = re.search(rf"def\s+({re.escape(expected_fn)})\s*\(", content)
    return entry


def _get_args(content: str, fn_name: str) -> list[str] | None:
    """Return argument names of fn_name using AST, or None if not found."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
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
        cb_type = _resolve_cb_type(file_path)
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
        cb_type = _resolve_cb_type(file_path)
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
        cb_type = _resolve_cb_type(file_path)

        if cb_type not in ("before_model_callbacks", "after_model_callbacks"):
            return []

        returns_dict = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
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


def _extract_tools_calls(content: str) -> list[str]:
    """Extract tool names invoked via ``tools`` or ``async_tools``."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(content)
    except SyntaxError:
        matches = re.findall(r"\b(?:async_)?tools\.(\w+)\s*\(", content)
        return list(dict.fromkeys(matches))

    calls: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("tools", "async_tools")
        ):
            calls.append(node.func.attr)
    return list(dict.fromkeys(calls))


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
        tool_calls = _extract_tools_calls(content)
        for tool_name in tool_calls:
            if tool_name in _PYTHON_COLLECTION_METHODS:
                continue
            if any(
                tool_name.startswith(pfx)
                for pfx in context.bypass_tool_prefixes
            ):
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
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
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


@rule("callbacks")
class VoiceBeforeModelToolMutation(Rule):
    id = "C024"
    name = "voice-before-model-request-mutation"
    description = "Voice apps ignore before_model llm_request mutations"
    default_severity = Severity.WARNING

    _MUTATING_METHODS = frozenset(
        {
            "hide_tool",
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "clear",
            "update",
            "setdefault",
        }
    )

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if _resolve_cb_type(file_path) != "before_model_callbacks":
            return []

        if not is_voice_app(context):
            return []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        mutating_methods = self._MUTATING_METHODS

        class _MutationVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.mutations: list[ast.AST] = []
                self.llm_req_name = "llm_request"
                self.alias_names: set[str] = set()

            def _visit_fn(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                prev_name = self.llm_req_name
                prev_aliases = set(self.alias_names)
                all_pos = list(node.args.posonlyargs) + list(node.args.args)
                if node.name == "before_model_callback" and len(all_pos) >= 2:
                    self.llm_req_name = all_pos[1].arg
                    self.alias_names = set()
                self.generic_visit(node)
                self.llm_req_name = prev_name
                self.alias_names = prev_aliases

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_fn(node)

            def visit_AsyncFunctionDef(
                self, node: ast.AsyncFunctionDef
            ) -> None:
                self._visit_fn(node)

            def _is_request_expr(
                self, expr: ast.AST, *, include_aliases: bool = False
            ) -> bool:
                if isinstance(expr, ast.Name):
                    if expr.id == self.llm_req_name:
                        return True
                    return include_aliases and expr.id in self.alias_names
                if isinstance(expr, ast.Attribute):
                    return self._is_request_expr(
                        expr.value, include_aliases=include_aliases
                    )
                if isinstance(expr, ast.Subscript):
                    return self._is_request_expr(
                        expr.value, include_aliases=include_aliases
                    )
                return False

            def _is_mutation_target(self, target: ast.AST) -> bool:
                if isinstance(target, (ast.Tuple, ast.List)):
                    return any(
                        self._is_mutation_target(elt) for elt in target.elts
                    )
                if isinstance(target, ast.Starred):
                    return self._is_mutation_target(target.value)
                if isinstance(target, ast.Name):
                    return target.id == self.llm_req_name
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    return self._is_request_expr(
                        target.value, include_aliases=True
                    )
                return False

            def visit_Assign(self, node: ast.Assign) -> None:
                if any(self._is_mutation_target(t) for t in node.targets):
                    self.mutations.append(node)
                elif len(node.targets) == 1 and isinstance(
                    node.targets[0], ast.Name
                ):
                    alias = node.targets[0].id
                    if self._is_request_expr(node.value, include_aliases=True):
                        self.alias_names.add(alias)
                    else:
                        self.alias_names.discard(alias)
                self.generic_visit(node)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                if self._is_mutation_target(node.target):
                    self.mutations.append(node)
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    if self._is_mutation_target(node.target):
                        self.mutations.append(node)
                    elif isinstance(node.target, ast.Name):
                        alias = node.target.id
                        if self._is_request_expr(
                            node.value, include_aliases=True
                        ):
                            self.alias_names.add(alias)
                        else:
                            self.alias_names.discard(alias)
                self.generic_visit(node)

            def visit_Delete(self, node: ast.Delete) -> None:
                if any(self._is_mutation_target(t) for t in node.targets):
                    self.mutations.append(node)
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in mutating_methods
                    and self._is_request_expr(
                        node.func.value, include_aliases=True
                    )
                ) or (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"setattr", "delattr"}
                    and node.args
                    and self._is_request_expr(
                        node.args[0], include_aliases=True
                    )
                ):
                    self.mutations.append(node)
                self.generic_visit(node)

        visitor = _MutationVisitor()
        visitor.visit(tree)

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=getattr(mutation, "lineno", None),
                message=(
                    "Voice streaming ignores before_model llm_request mutations"
                ),
                fix=(
                    "Configure channel-specific tools/prompts statically or"
                    " move dynamic gating into before_tool_callback."
                ),
            )
            for mutation in visitor.mutations
        ]


_BUNDLE_BEGIN_PREFIX = "# === BEGIN CXAS_SHARED_BUNDLE"
_BUNDLE_END_PREFIX = "# === END CXAS_SHARED_BUNDLE"


def _iter_safe_py_files(root: Path) -> list[Path]:
    """Return sorted ``.py`` files under ``root``, skipping symlinks/hidden."""
    if root.is_symlink() or not root.is_dir():
        return []
    found: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for entry in entries:
            if (
                entry.is_symlink()
                or entry.name.startswith(".")
                or entry.name == "__pycache__"
            ):
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file() and entry.suffix == ".py":
                found.append(entry)
    found.sort()
    return found


def _is_type_checking_guard(test: ast.expr) -> bool:
    """Return True if ``test`` checks ``TYPE_CHECKING``."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_is_type_checking_guard(v) for v in test.values)
    return False


def _collect_python_lib_imports(
    tree: ast.AST,
) -> list[tuple[str, str | None, int]]:
    """Return ``(module, imported_name_or_none, lineno)`` for ``python_lib``."""
    imports: list[tuple[str, str | None, int]] = []

    class _ImportVisitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking_guard(node.test):
                for stmt in node.orelse:
                    self.visit(stmt)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "python_lib" or alias.name.startswith(
                    "python_lib."
                ):
                    imports.append((alias.name, None, node.lineno))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if (
                node.level == 0
                and node.module
                and (
                    node.module == "python_lib"
                    or node.module.startswith("python_lib.")
                )
            ):
                for alias in node.names:
                    imports.append((node.module, alias.name, node.lineno))

    _ImportVisitor().visit(tree)
    return imports


def _check_bundle_markers(content: str) -> tuple[bool, bool]:
    """Return ``(has_valid_bundle, is_malformed)`` for bundle markers."""
    depth = 0
    paired_count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(_BUNDLE_BEGIN_PREFIX):
            if depth != 0:
                return False, True
            depth += 1
        elif stripped.startswith(_BUNDLE_END_PREFIX):
            if depth != 1:
                return False, True
            depth -= 1
            paired_count += 1
    if depth != 0:
        return False, True
    return paired_count > 0, False


def _safe_child_exists(
    base: Path, parts: list[str], *, expect_dir: bool
) -> bool:
    cur = base
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return False
    return cur.is_dir() if expect_dir else cur.is_file()


def _native_env_has_module(
    content_root: Path, mod: str, imported_name: str | None
) -> bool:
    parts = mod.split(".")
    if _safe_child_exists(
        content_root, [*parts[:-1], f"{parts[-1]}.py"], expect_dir=False
    ):
        return True
    if _safe_child_exists(
        content_root, [*parts, "__init__.py"], expect_dir=False
    ):
        return True
    if imported_name and imported_name != "*":
        if _safe_child_exists(
            content_root, [*parts, f"{imported_name}.py"], expect_dir=False
        ):
            return True
        if _safe_child_exists(
            content_root,
            [*parts, imported_name, "__init__.py"],
            expect_dir=False,
        ):
            return True
    if imported_name is None and _safe_child_exists(
        content_root, parts, expect_dir=True
    ):
        return bool(_iter_safe_py_files(content_root.joinpath(*parts)))
    return False


@rule("callbacks")
class SharedPythonBundleIntegrity(Rule):
    """Validates ``python_lib/`` syntax and shared-Python bundle integrity."""

    id = "C025"
    name = "shared-python-bundle-integrity"
    description = (
        "Validates python_lib/ syntax and CXAS_SHARED_BUNDLE / "
        "pythonEnvFiles bundling"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = (
            file_path.parent
            if file_path.name in ("app.json", "app.yaml", "app.yml")
            else (context.app_root or context.app_dir)
        )
        results: list[LintResult] = []

        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(context.project_root))
            except ValueError:
                return str(p)

        # Part A: Syntax check python_lib/ and pythonEnvFiles/content/
        for py_root in (
            app_root / "python_lib",
            app_root / "pythonEnvFiles" / "content",
        ):
            for py_path in _iter_safe_py_files(py_root):
                rel = _rel(py_path)
                try:
                    src = py_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as e:
                    results.append(
                        self.make_result(
                            file=rel,
                            severity=Severity.ERROR,
                            message=(
                                "Failed to read shared Python file as "
                                f"UTF-8: {e}"
                            ),
                            fix="Re-save the Python file using UTF-8 encoding.",
                        )
                    )
                    continue
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", SyntaxWarning)
                        compile(src, rel, "exec")
                except SyntaxError as e:
                    results.append(
                        self.make_result(
                            file=rel,
                            line=e.lineno,
                            severity=Severity.ERROR,
                            message=(
                                f"Syntax error in shared Python file: {e.msg}"
                            ),
                            fix="Fix the Python syntax error.",
                        )
                    )

        # Part B: Bundle integrity across callback & tool entrypoints
        env_yaml = app_root / "pythonEnvFiles" / "pythonEnvFiles.yaml"
        content_root = app_root / "pythonEnvFiles" / "content"
        content_py_lib = content_root / "python_lib"
        has_native_env = (
            env_yaml.is_file()
            and not env_yaml.is_symlink()
            and not (app_root / "pythonEnvFiles").is_symlink()
            and not content_root.is_symlink()
            and content_py_lib.is_dir()
            and not content_py_lib.is_symlink()
        )
        local_py_lib = app_root / "python_lib"
        has_local_py_lib = (
            local_py_lib.is_dir() and not local_py_lib.is_symlink()
        )

        entry_files = _iter_safe_py_files(
            app_root / "agents"
        ) + _iter_safe_py_files(app_root / "tools")

        for py_path in entry_files:
            rel = _rel(py_path)
            try:
                src = py_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            has_valid_bundle, malformed_bundle = _check_bundle_markers(src)
            if malformed_bundle:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            "Malformed or truncated CXAS_SHARED_BUNDLE marker "
                            "pair."
                        ),
                        fix=(
                            "Re-bundle with cxas build/package or restore the "
                            "matching BEGIN/END CXAS_SHARED_BUNDLE markers."
                        ),
                    )
                )
                continue

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(src)
            except SyntaxError:
                continue

            py_lib_imports = _collect_python_lib_imports(tree)
            if not py_lib_imports or has_valid_bundle:
                continue

            if has_native_env:
                missing_reported: set[str] = set()
                for mod, imp_name, lineno in py_lib_imports:
                    if not _native_env_has_module(content_root, mod, imp_name):
                        label = (
                            f"{mod}.{imp_name}"
                            if imp_name and imp_name != "*"
                            else mod
                        )
                        if label in missing_reported:
                            continue
                        missing_reported.add(label)
                        results.append(
                            self.make_result(
                                file=rel,
                                line=lineno,
                                severity=Severity.ERROR,
                                message=(
                                    f"Imports '{label}' which is missing from "
                                    "pythonEnvFiles/content/."
                                ),
                                fix=(
                                    "Re-run cxas build/package with "
                                    "--native-python-env or add the missing "
                                    "module under python_lib/."
                                ),
                            )
                        )
            elif has_local_py_lib:
                first_line = py_lib_imports[0][2]
                results.append(
                    self.make_result(
                        file=rel,
                        line=first_line,
                        severity=Severity.WARNING,
                        message=(
                            "Imports python_lib.* without inline "
                            "CXAS_SHARED_BUNDLE or pythonEnvFiles; run "
                            "cxas build/package before deploying to CES."
                        ),
                        fix=(
                            "Run cxas build or cxas package to bundle "
                            "python_lib/ before pushing to CES."
                        ),
                    )
                )
            else:
                first_line = py_lib_imports[0][2]
                results.append(
                    self.make_result(
                        file=rel,
                        line=first_line,
                        severity=Severity.ERROR,
                        message=(
                            "Imports python_lib.* with neither "
                            "CXAS_SHARED_BUNDLE nor pythonEnvFiles — "
                            "guaranteed ModuleNotFoundError in CES runtime."
                        ),
                        fix=(
                            "Provide python_lib/ and bundle via cxas build/"
                            "package, or remove the python_lib import."
                        ),
                    )
                )

        return results
