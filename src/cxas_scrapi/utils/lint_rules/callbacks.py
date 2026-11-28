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
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import yaml

from cxas_scrapi.core.python_bundler import inspect_shared_bundle
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


def _is_type_checking_expr(expr: ast.expr) -> bool:
    return (isinstance(expr, ast.Name) and expr.id == "TYPE_CHECKING") or (
        isinstance(expr, ast.Attribute) and expr.attr == "TYPE_CHECKING"
    )


def _is_type_checking_guard(test: ast.expr) -> bool:
    """Return True if ``test`` checks ``TYPE_CHECKING``."""
    if _is_type_checking_expr(test):
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_is_type_checking_guard(v) for v in test.values)
    return False


def _is_not_type_checking_expr(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and _is_type_checking_expr(test.operand)
    )


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
            if _is_not_type_checking_expr(node.test):
                for stmt in node.body:
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


def _modules_cover_import(
    modules: Iterable[str],
    ancestor_packages: Iterable[str],
    mod: str,
    imported_name: str | None,
) -> bool:
    """Return True if ``(mod, imported_name)`` is satisfied by bundled/declared
    modules or their Python 3 namespace ancestor packages.
    """
    mod_set = modules if isinstance(modules, (set, frozenset)) else set(modules)
    anc_set = (
        ancestor_packages
        if isinstance(ancestor_packages, (set, frozenset))
        else set(ancestor_packages)
    )
    if mod in mod_set:
        return True
    if imported_name is None:
        return mod in anc_set
    if imported_name != "*":
        full = f"{mod}.{imported_name}"
        return full in mod_set or full in anc_set
    return False


def _resolve_safe_rel_path(base: Path, rel_path: str) -> Path | None:
    """Resolve a relative POSIX path safely inside ``base`` without symlinks."""
    if not rel_path or "\\" in rel_path or "\x00" in rel_path:
        return None
    pure = PurePosixPath(rel_path)
    if pure.is_absolute() or not pure.parts:
        return None
    if any(part in ("", ".", "..") or ":" in part for part in pure.parts):
        return None
    cur = base
    for part in pure.parts:
        cur = cur / part
        if cur.is_symlink():
            return None
    try:
        resolved_base = base.resolve()
        resolved_cur = cur.resolve()
        if (
            resolved_cur != resolved_base
            and resolved_base not in resolved_cur.parents
        ):
            return None
    except OSError:
        return None
    return cur


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

        env_dir = app_root / "pythonEnvFiles"
        content_root = env_dir / "content"

        # Part A: Syntax check python_lib/ and pythonEnvFiles/content/
        py_roots = [app_root / "python_lib"]
        if not env_dir.is_symlink():
            py_roots.append(content_root)

        for py_root in py_roots:
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

        # Part B: Validate pythonEnvFiles/pythonEnvFiles.yaml manifest & entries
        env_yaml = env_dir / "pythonEnvFiles.yaml"
        has_env_manifest = False
        declared_modules: set[str] = set()
        declared_ancestors: set[str] = set()
        if (
            not env_dir.is_symlink()
            and env_yaml.is_file()
            and not env_yaml.is_symlink()
        ):
            env_yaml_rel = _rel(env_yaml)
            try:
                parsed_env = yaml.safe_load(
                    env_yaml.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
                results.append(
                    self.make_result(
                        file=env_yaml_rel,
                        severity=Severity.ERROR,
                        message=(
                            "Failed to parse pythonEnvFiles/"
                            f"pythonEnvFiles.yaml: {e}"
                        ),
                        fix=(
                            "Fix or re-generate pythonEnvFiles/"
                            "pythonEnvFiles.yaml with cxas build/package "
                            "--native-python-env."
                        ),
                    )
                )
            else:
                entries = (
                    parsed_env.get("pythonEnvFiles")
                    if isinstance(parsed_env, dict)
                    else None
                )
                if not isinstance(entries, list):
                    results.append(
                        self.make_result(
                            file=env_yaml_rel,
                            severity=Severity.ERROR,
                            message=(
                                "Invalid pythonEnvFiles/pythonEnvFiles.yaml: "
                                "expected a YAML mapping with a "
                                "'pythonEnvFiles' list."
                            ),
                            fix=(
                                "Re-generate pythonEnvFiles/"
                                "pythonEnvFiles.yaml with cxas build/package "
                                "--native-python-env."
                            ),
                        )
                    )
                else:
                    has_env_manifest = True
                    content_safe = (
                        not content_root.is_symlink() and content_root.is_dir()
                    )
                    for idx, entry in enumerate(entries, 1):
                        if not isinstance(entry, dict):
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Entry #{idx} in "
                                        "pythonEnvFiles/pythonEnvFiles.yaml "
                                        "must be a mapping with 'name' and "
                                        "'filePath'."
                                    ),
                                    fix=(
                                        "Re-generate pythonEnvFiles/"
                                        "pythonEnvFiles.yaml with cxas "
                                        "build/package --native-python-env."
                                    ),
                                )
                            )
                            continue
                        entry_name = entry.get("name")
                        if not isinstance(entry_name, str) or not (
                            entry_name.strip()
                        ):
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Entry #{idx} in "
                                        "pythonEnvFiles/pythonEnvFiles.yaml "
                                        "is missing a non-empty 'name'."
                                    ),
                                    fix=(
                                        "Provide a non-empty string 'name' "
                                        "or re-generate pythonEnvFiles.yaml."
                                    ),
                                )
                            )
                        fp = entry.get("filePath")
                        if not isinstance(fp, str) or not fp.strip():
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Entry #{idx} in "
                                        "pythonEnvFiles/pythonEnvFiles.yaml "
                                        "is missing a non-empty 'filePath'."
                                    ),
                                    fix=(
                                        "Provide a relative 'filePath' under "
                                        "content/ or re-generate "
                                        "pythonEnvFiles.yaml."
                                    ),
                                )
                            )
                            continue
                        target = _resolve_safe_rel_path(env_dir, fp)
                        if target is None:
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Invalid or unsafe filePath '{fp}' "
                                        "in pythonEnvFiles/pythonEnvFiles.yaml."
                                    ),
                                    fix=(
                                        "Use a safe relative path under "
                                        "pythonEnvFiles/content/ with no "
                                        "symlinks or '..' segments."
                                    ),
                                )
                            )
                            continue
                        if not target.is_file():
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Declared filePath '{fp}' in "
                                        "pythonEnvFiles/pythonEnvFiles.yaml "
                                        "does not exist on disk."
                                    ),
                                    fix=(
                                        "Re-run cxas build/package "
                                        "--native-python-env to sync "
                                        "pythonEnvFiles.yaml with content/."
                                    ),
                                )
                            )
                            continue
                        if not content_safe:
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Declared filePath '{fp}' requires a "
                                        "non-symlink pythonEnvFiles/content/ "
                                        "directory."
                                    ),
                                    fix=(
                                        "Re-run cxas build/package "
                                        "--native-python-env."
                                    ),
                                )
                            )
                            continue
                        try:
                            rel_content = target.relative_to(
                                content_root
                            ).as_posix()
                        except ValueError:
                            results.append(
                                self.make_result(
                                    file=env_yaml_rel,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"Declared filePath '{fp}' in "
                                        "pythonEnvFiles/pythonEnvFiles.yaml "
                                        "must reside under 'content/'."
                                    ),
                                    fix=(
                                        "Place shared modules under "
                                        "pythonEnvFiles/content/ and "
                                        "re-generate pythonEnvFiles.yaml."
                                    ),
                                )
                            )
                            continue
                        if rel_content.endswith(".py"):
                            mod_name = rel_content[:-3].replace("/", ".")
                            if mod_name.endswith(".__init__"):
                                mod_name = mod_name[:-9]
                            declared_modules.add(mod_name)
                            parts = mod_name.split(".")
                            for i in range(1, len(parts)):
                                declared_ancestors.add(".".join(parts[:i]))

        local_py_lib = app_root / "python_lib"
        has_local_py_lib = (
            local_py_lib.is_dir() and not local_py_lib.is_symlink()
        )

        # Part C: Bundle integrity across callback & tool entrypoints
        entry_files = _iter_safe_py_files(
            app_root / "agents"
        ) + _iter_safe_py_files(app_root / "tools")

        for py_path in entry_files:
            rel = _rel(py_path)
            try:
                src = py_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            inspection = inspect_shared_bundle(src, py_path)
            if inspection.error is not None:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=inspection.error,
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
            if not py_lib_imports:
                continue

            if inspection.has_bundle:
                missing_reported: set[str] = set()
                for mod, imp_name, lineno in py_lib_imports:
                    if not _modules_cover_import(
                        inspection.bundled_modules,
                        inspection.ancestor_packages,
                        mod,
                        imp_name,
                    ):
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
                                    f"Imports '{label}' which is not included "
                                    "in the inline CXAS_SHARED_BUNDLE block."
                                ),
                                fix=(
                                    "Re-run cxas build/package to refresh the "
                                    "inline CXAS_SHARED_BUNDLE block."
                                ),
                            )
                        )
            elif has_env_manifest:
                missing_reported = set()
                for mod, imp_name, lineno in py_lib_imports:
                    if not _modules_cover_import(
                        declared_modules,
                        declared_ancestors,
                        mod,
                        imp_name,
                    ):
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
                                    "pythonEnvFiles/content/ or "
                                    "pythonEnvFiles/pythonEnvFiles.yaml."
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
