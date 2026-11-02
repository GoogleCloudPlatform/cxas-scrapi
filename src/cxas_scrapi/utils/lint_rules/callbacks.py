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

"""Callback lint rules (C001-C017).

Validates agent callback Python files against CXAS conventions.
"""

import ast
import json
import re
import typing
import warnings
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

_STAGE_TO_JSON_KEYS: dict[str, tuple[str, str]] = {
    "before_model_callbacks": (
        "beforeModelCallbacks",
        "before_model_callbacks",
    ),
    "after_model_callbacks": (
        "afterModelCallbacks",
        "after_model_callbacks",
    ),
    "before_agent_callbacks": (
        "beforeAgentCallbacks",
        "before_agent_callbacks",
    ),
    "after_agent_callbacks": (
        "afterAgentCallbacks",
        "after_agent_callbacks",
    ),
    "before_tool_callbacks": (
        "beforeToolCallbacks",
        "before_tool_callbacks",
    ),
    "after_tool_callbacks": (
        "afterToolCallbacks",
        "after_tool_callbacks",
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


def _find_agent_dir(file_path: Path) -> Path | None:
    """Return enclosing ``agents/<agent_name>`` directory if present."""
    curr = file_path.parent
    for _ in range(5):
        if curr.parent.name == "agents":
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent
    return None


def _parse_ast(content: str) -> ast.Module | None:
    """Parse Python source into an AST module, suppressing SyntaxWarning."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(content)
    except SyntaxError:
        return None


def _walk_no_nested(root: ast.AST) -> list[ast.AST]:
    """Walk AST nodes inside ``root`` without descending into nested defs."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        for child in reversed(list(ast.iter_child_nodes(node))):
            if isinstance(
                child,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.Lambda,
                    ast.ClassDef,
                ),
            ):
                continue
            stack.append(child)
    return out


def _find_entry_function(content: str, expected_fn: str) -> re.Match | None:
    """Find the entry callback function, not helper functions."""
    entry = re.search(rf"def\s+({re.escape(expected_fn)})\s*\(", content)
    return entry


def _get_args(content: str, fn_name: str) -> list[str] | None:
    """Return argument names of fn_name using AST, or None if not found."""
    tree = _parse_ast(content)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn_name
        ):
            return [arg.arg for arg in node.args.args]
    return None


def _find_callback_ast_fns(
    tree: ast.Module, cb_type: str
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return entry callback AST functions for ``cb_type``."""
    expected_fn, _ = CALLBACK_SIGNATURES.get(cb_type, (None, None))
    top_fns = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if expected_fn:
        matched = [n for n in top_fns if n.name == expected_fn]
        if matched:
            return matched
    non_private = [n for n in top_fns if not n.name.startswith("_")]
    return non_private or top_fns


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

        tree = _parse_ast(content)
        if tree is not None:
            target_fns = _find_callback_ast_fns(tree, cb_type)
            returns_dict = any(_function_returns_dict(fn) for fn in target_fns)
        else:
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


# ── C011: Unconditional Callback Short-Circuit ───────────────────────────


def _discover_stage_callbacks_in_order(
    agent_dir: Path, cb_type: str, context: LintContext
) -> list[Path]:
    """Return ordered callback files for ``cb_type`` in ``agent_dir``."""
    ordered: list[Path] = []
    seen: set[Path] = set()
    app_root = context.app_root or agent_dir.parent.parent

    agent_json = agent_dir / f"{agent_dir.name}.json"
    json_keys = _STAGE_TO_JSON_KEYS.get(cb_type, ())
    if agent_json.exists() and json_keys:
        try:
            cfg = json.loads(agent_json.read_text())
            if isinstance(cfg, dict):
                entries: list[typing.Any] = []
                for k in json_keys:
                    if isinstance(cfg.get(k), list):
                        entries = cfg[k]
                        break
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("disabled"):
                        continue
                    code_rel = entry.get("pythonCode") or entry.get(
                        "python_code"
                    )
                    if not isinstance(code_rel, str) or not code_rel:
                        continue
                    candidate = (app_root / code_rel).resolve()
                    if not candidate.exists():
                        alt = (context.app_dir / code_rel).resolve()
                        if alt.exists():
                            candidate = alt
                    if candidate.exists() and candidate not in seen:
                        seen.add(candidate)
                        ordered.append(candidate)
        except (OSError, json.JSONDecodeError):
            pass

    if ordered:
        return ordered

    nested_dir = agent_dir / cb_type
    if nested_dir.is_dir():
        for cb_sub in sorted(nested_dir.iterdir()):
            if not cb_sub.is_dir():
                continue
            code = (cb_sub / "python_code.py").resolve()
            if code.exists() and code not in seen:
                seen.add(code)
                ordered.append(code)

    for d in sorted(agent_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith(f"{cb_type}_"):
            code = (d / "python_code.py").resolve()
            if code.exists() and code not in seen:
                seen.add(code)
                ordered.append(code)

    return ordered


def _is_non_none_return_expr(
    expr: ast.AST | None, param_names: set[str]
) -> bool:
    """Return True if ``expr`` is guaranteed at the AST level to be non-None."""
    if expr is None:
        return False
    if isinstance(expr, ast.Constant):
        return expr.value is not None
    if isinstance(
        expr,
        (
            ast.Dict,
            ast.List,
            ast.Tuple,
            ast.Set,
            ast.DictComp,
            ast.ListComp,
            ast.SetComp,
            ast.JoinedStr,
        ),
    ):
        return True
    if isinstance(expr, ast.Name):
        return expr.id in param_names
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name):
            return expr.func.id in (
                "LlmResponse",
                "Content",
                "dict",
                "list",
                "str",
                "int",
                "float",
                "bool",
            )
        if (
            isinstance(expr.func, ast.Attribute)
            and isinstance(expr.func.value, ast.Name)
            and expr.func.value.id in ("LlmResponse", "Content", "Part")
        ):
            return True
    return False


def _collect_non_none_local_vars(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef, param_names: set[str]
) -> set[str]:
    """Return local variable names assigned exclusively non-None expressions."""
    non_none_locals: set[str] = set()
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        known = param_names | non_none_locals
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Assign):
                is_non_none = _is_non_none_return_expr(node.value, known)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if is_non_none and target.id not in tainted:
                            if target.id not in non_none_locals:
                                non_none_locals.add(target.id)
                                changed = True
                        else:
                            tainted.add(target.id)
                            if target.id in non_none_locals:
                                non_none_locals.discard(target.id)
                                changed = True
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                var_name = node.target.id
                if (
                    node.value is not None
                    and _is_non_none_return_expr(node.value, known)
                    and var_name not in tainted
                ):
                    if var_name not in non_none_locals:
                        non_none_locals.add(var_name)
                        changed = True
                else:
                    tainted.add(var_name)
                    if var_name in non_none_locals:
                        non_none_locals.discard(var_name)
                        changed = True
    return non_none_locals


def _block_always_returns_non_none(
    stmts: list[ast.stmt], non_none_names: set[str]
) -> tuple[bool, int | None]:
    """Return ``(True, lineno)`` if every path in ``stmts`` returns non-None."""
    for stmt in stmts:
        if isinstance(stmt, ast.Return):
            if _is_non_none_return_expr(stmt.value, non_none_names):
                return True, stmt.lineno
            return False, None
        if isinstance(stmt, ast.If) and stmt.orelse:
            b_true, line_t = _block_always_returns_non_none(
                stmt.body, non_none_names
            )
            b_false, _ = _block_always_returns_non_none(
                stmt.orelse, non_none_names
            )
            if b_true and b_false:
                return True, line_t
        if isinstance(stmt, ast.Try):
            if stmt.finalbody:
                b_fin, line_fin = _block_always_returns_non_none(
                    stmt.finalbody, non_none_names
                )
                if b_fin:
                    return True, line_fin
            b_try, line_try = _block_always_returns_non_none(
                stmt.body, non_none_names
            )
            b_else, line_else = _block_always_returns_non_none(
                stmt.orelse, non_none_names
            )
            if (
                (b_try or b_else)
                and stmt.handlers
                and all(
                    _block_always_returns_non_none(h.body, non_none_names)[0]
                    for h in stmt.handlers
                )
            ):
                return True, line_try or line_else
    return False, None


def _function_unconditionally_short_circuits(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[bool, int | None]:
    """Check whether ``fn_def`` unconditionally returns a non-None value."""
    param_names = {a.arg for a in fn_def.args.args}
    non_none_names = param_names | _collect_non_none_local_vars(
        fn_def, param_names
    )
    returns = [
        node for node in _walk_no_nested(fn_def) if isinstance(node, ast.Return)
    ]
    if not returns:
        return False, None
    for ret_node in returns:
        if not _is_non_none_return_expr(ret_node.value, non_none_names):
            return False, None
    return _block_always_returns_non_none(fn_def.body, non_none_names)


@rule("callbacks")
class UnconditionalCallbackShortCircuit(Rule):
    id = "C011"
    name = "unconditional-callback-short-circuit"
    description = (
        "Earlier callback in a multi-callback stage unconditionally returns a "
        "non-None value, making subsequent callbacks unreachable"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        cb_type = _resolve_cb_type(file_path)
        if cb_type not in CALLBACK_SIGNATURES:
            return []
        agent_dir = _find_agent_dir(file_path)
        if agent_dir is None or not agent_dir.is_dir():
            return []

        stage_files = _discover_stage_callbacks_in_order(
            agent_dir, cb_type, context
        )
        if len(stage_files) < 2:  # noqa: PLR2004
            return []

        resolved_self = file_path.resolve()
        try:
            idx = stage_files.index(resolved_self)
        except ValueError:
            return []

        # Only flag if there is at least one subsequent callback in the stage
        if idx >= len(stage_files) - 1:
            return []

        tree = _parse_ast(content)
        if tree is None:
            return []

        target_fns = _find_callback_ast_fns(tree, cb_type)
        if not target_fns:
            return []

        always_non_none, ret_line = _function_unconditionally_short_circuits(
            target_fns[0]
        )
        if not always_non_none:
            return []

        rel = str(file_path.relative_to(context.project_root))
        skipped = [p.parent.name for p in stage_files[idx + 1 :]]
        return [
            self.make_result(
                file=rel,
                line=ret_line,
                message=(
                    f"Callback '{file_path.parent.name}' unconditionally "
                    "returns a non-None value, making subsequent callbacks "
                    f"{skipped} in '{cb_type}' unreachable"
                ),
                fix=(
                    "Return None on pass-through paths so subsequent "
                    "callbacks in the stage can execute, or merge/reorder "
                    "the callbacks."
                ),
            )
        ]


# ── Shared State Helpers (C012, C013, C016) ──────────────────────────────

_NON_CTX_HOLDERS = frozenset({"self", "cls", "runner", "engine"})


def _is_state_container_expr(
    node: ast.AST, state_aliases: set[str], ctx_names: set[str]
) -> bool:
    """Return True if ``node`` refers to session state / variables dict."""
    if isinstance(node, ast.Name):
        return node.id in state_aliases
    if isinstance(node, ast.Attribute) and node.attr in ("state", "variables"):
        if isinstance(node.value, ast.Name):
            return node.value.id not in _NON_CTX_HOLDERS and (
                node.value.id in ctx_names
                or node.value.id
                in ("callback_context", "context", "tool_context", "ctx", "cb")
            )
        return True
    return False


def _extract_state_key_target(
    target: ast.AST, state_aliases: set[str], ctx_names: set[str]
) -> str | None:
    """If ``target`` is ``state['k']`` or ``ctx.state['k']``, return ``'k'``."""
    if (
        isinstance(target, ast.Subscript)
        and _is_state_container_expr(target.value, state_aliases, ctx_names)
        and isinstance(target.slice, ast.Constant)
        and isinstance(target.slice.value, str)
    ):
        return target.slice.value
    return None


def _collect_fn_state_aliases(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    """Return ``(state_aliases, ctx_names)`` for a function definition."""
    arg_names = [a.arg for a in fn_def.args.args]
    ctx_names = {
        "callback_context",
        "context",
        "tool_context",
        "ctx",
        "cb",
        *arg_names[:1],
    }
    if len(arg_names) >= 3:  # noqa: PLR2004
        ctx_names.add(arg_names[2])

    state_aliases: set[str] = {"state"}
    for a in arg_names:
        if a in ("state", "variables"):
            state_aliases.add(a)

    changed = True
    while changed:
        changed = False
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Assign):
                if _is_state_container_expr(
                    node.value, state_aliases, ctx_names
                ):
                    for t in node.targets:
                        if (
                            isinstance(t, ast.Name)
                            and t.id not in state_aliases
                        ):
                            state_aliases.add(t.id)
                            changed = True
                else:
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == "state":
                            state_aliases.discard("state")
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
            ):
                if _is_state_container_expr(
                    node.value, state_aliases, ctx_names
                ):
                    if node.target.id not in state_aliases:
                        state_aliases.add(node.target.id)
                        changed = True
                elif node.target.id == "state":
                    state_aliases.discard("state")
    return state_aliases, ctx_names


def _is_mutable_container_rhs(expr: ast.AST) -> bool:
    """Return True if ``expr`` creates a mutable container (dict/list/set)."""
    if isinstance(
        expr,
        (
            ast.Dict,
            ast.List,
            ast.Set,
            ast.DictComp,
            ast.ListComp,
            ast.SetComp,
        ),
    ):
        return True
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id in ("dict", "list", "set")
    )


# ── C012: Shared State Reference Aliasing ────────────────────────────────


@rule("callbacks")
class SharedStateReferenceAliasing(Rule):
    id = "C012"
    name = "shared-state-reference-aliasing"
    description = (
        "Mutable local container assigned to multiple session state keys "
        "without .copy() or deepcopy()"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        tree = _parse_ast(content)
        if tree is None:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []

        for fn_node in tree.body:
            if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            state_aliases, ctx_names = _collect_fn_state_aliases(fn_node)
            binding_id = 0
            mutable_bindings: dict[str, tuple[str, int]] = {}
            binding_writes: dict[tuple[str, int], list[tuple[str, int]]] = {}

            for node in _walk_no_nested(fn_node):
                if isinstance(node, ast.Assign):
                    if _is_mutable_container_rhs(node.value):
                        binding_id += 1
                        name_targets = [
                            t.id
                            for t in node.targets
                            if isinstance(t, ast.Name)
                        ]
                        st_keys = [
                            st_key
                            for t in node.targets
                            if (
                                st_key := _extract_state_key_target(
                                    t, state_aliases, ctx_names
                                )
                            )
                            is not None
                        ]
                        var_label = (
                            name_targets[0] if name_targets else "<literal>"
                        )
                        for name in name_targets:
                            mutable_bindings[name] = (var_label, binding_id)
                        for st_key in st_keys:
                            binding_writes.setdefault(
                                (var_label, binding_id), []
                            ).append((st_key, node.lineno))
                    elif (
                        isinstance(node.value, ast.Name)
                        and node.value.id in mutable_bindings
                    ):
                        var_label, b_id = mutable_bindings[node.value.id]
                        for target in node.targets:
                            st_key = _extract_state_key_target(
                                target, state_aliases, ctx_names
                            )
                            if st_key is not None:
                                binding_writes.setdefault(
                                    (var_label, b_id), []
                                ).append((st_key, node.lineno))
                            elif isinstance(target, ast.Name):
                                mutable_bindings[target.id] = (var_label, b_id)
                    else:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                mutable_bindings.pop(target.id, None)
                elif (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.value is not None
                ):
                    binding_id += 1
                    if _is_mutable_container_rhs(node.value):
                        mutable_bindings[node.target.id] = (
                            node.target.id,
                            binding_id,
                        )
                    else:
                        mutable_bindings.pop(node.target.id, None)

            for (var_name, _b_id), writes in binding_writes.items():
                unique_keys = list(dict.fromkeys(k for k, _ in writes))
                if len(unique_keys) >= 2:  # noqa: PLR2004
                    last_line = writes[-1][1]
                    results.append(
                        self.make_result(
                            file=rel,
                            line=last_line,
                            message=(
                                f"Mutable container '{var_name}' is "
                                "assigned to multiple state keys "
                                f"{unique_keys} without .copy() or deepcopy(), "
                                "creating unintended reference aliasing"
                            ),
                            fix=(
                                "Assign a copy ('.copy()' or "
                                "'copy.deepcopy(...)') to each state "
                                "key so mutations to one key do not mutate "
                                "the other."
                            ),
                        )
                    )
        return results


# ── C013: State None Deletes Key ─────────────────────────────────────────


@rule("callbacks")
class StateNoneDeletesKey(Rule):
    id = "C013"
    name = "state-none-deletes-key"
    description = (
        "Assigning None to state['k'] deletes the key in the CES runtime "
        "rather than storing None"
    )
    default_severity = Severity.WARNING

    def _make_c013_result(self, rel: str, line: int, st_key: str) -> LintResult:
        return self.make_result(
            file=rel,
            line=line,
            message=(
                f"Assigning None to state['{st_key}'] deletes the key from "
                "session state in the CES runtime, which can cause KeyError "
                "on downstream subscript reads"
            ),
            fix=(
                f"Use state.pop('{st_key}', None) if key deletion is "
                "intended, or assign an explicit empty sentinel (e.g. '' or "
                "False)."
            ),
        )

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        tree = _parse_ast(content)
        if tree is None:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []

        for fn_node in tree.body:
            if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            state_aliases, ctx_names = _collect_fn_state_aliases(fn_node)
            for node in _walk_no_nested(fn_node):
                if isinstance(node, ast.Assign):
                    if not (
                        isinstance(node.value, ast.Constant)
                        and node.value.value is None
                    ):
                        continue
                    for target in node.targets:
                        st_key = _extract_state_key_target(
                            target, state_aliases, ctx_names
                        )
                        if st_key is not None:
                            results.append(
                                self._make_c013_result(rel, node.lineno, st_key)
                            )
                elif (
                    isinstance(node, ast.AnnAssign)
                    and node.value is not None
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is None
                ):
                    st_key = _extract_state_key_target(
                        node.target, state_aliases, ctx_names
                    )
                    if st_key is not None:
                        results.append(
                            self._make_c013_result(rel, node.lineno, st_key)
                        )
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update"
                    and _is_state_container_expr(
                        node.func.value, state_aliases, ctx_names
                    )
                ):
                    for arg in node.args:
                        if isinstance(arg, ast.Dict):
                            for k, v in zip(arg.keys, arg.values, strict=False):
                                if (
                                    isinstance(k, ast.Constant)
                                    and isinstance(k.value, str)
                                    and isinstance(v, ast.Constant)
                                    and v.value is None
                                ):
                                    results.append(
                                        self._make_c013_result(
                                            rel, v.lineno, k.value
                                        )
                                    )
                    for kw in node.keywords:
                        if (
                            kw.arg is not None
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is None
                        ):
                            results.append(
                                self._make_c013_result(
                                    rel, kw.value.lineno, kw.arg
                                )
                            )
        return results


# ── C014: Unreturned LlmResponse Mutation ────────────────────────────────


def _is_response_subexpr(expr: ast.AST, resp_names: set[str]) -> bool:
    """Return True if ``expr`` is rooted at one of ``resp_names``."""
    curr: ast.AST = expr
    while isinstance(curr, (ast.Attribute, ast.Subscript)):
        curr = curr.value
    return isinstance(curr, ast.Name) and curr.id in resp_names


def _collect_response_aliases(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef, resp_names: set[str]
) -> set[str]:
    """Collect local variables assigned from ``llm_response`` or sub-attrs."""
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        combined = resp_names | aliases
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Assign):
                if _is_response_subexpr(node.value, combined):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id not in aliases
                        ):
                            aliases.add(target.id)
                            changed = True
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and _is_response_subexpr(node.value, combined)
                and node.target.id not in aliases
            ):
                aliases.add(node.target.id)
                changed = True
    return aliases


def _stmt_mutates_response(stmt: ast.stmt, resp_names: set[str]) -> int | None:
    """Return line number if ``stmt`` directly mutates ``llm_response``."""
    if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = (
            stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        )
        for target in targets:
            if isinstance(target, (ast.Attribute, ast.Subscript)):
                root: ast.AST = target
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in resp_names:
                    return stmt.lineno
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        if isinstance(call.func, ast.Attribute) and call.func.attr in (
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "clear",
            "update",
        ):
            root = call.func.value
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in resp_names:
                return stmt.lineno
    return None


def _is_explicit_none_return(ret_stmt: ast.Return) -> bool:
    """Return True if ``ret_stmt`` is ``return`` or ``return None``."""
    return ret_stmt.value is None or (
        isinstance(ret_stmt.value, ast.Constant)
        and ret_stmt.value.value is None
    )


def _continuation_always_returns_non_none(
    stmts: list[ast.stmt], fallthrough_ok: bool
) -> bool:
    """Return True if every path through ``stmts`` returns non-None."""
    for idx, stmt in enumerate(stmts):
        rest = stmts[idx + 1 :]
        if isinstance(stmt, ast.Return):
            return not _is_explicit_none_return(stmt)
        if isinstance(stmt, ast.Raise):
            return True
        if isinstance(stmt, ast.If):
            after_if = _continuation_always_returns_non_none(
                rest, fallthrough_ok
            )
            return _continuation_always_returns_non_none(
                stmt.body, after_if
            ) and _continuation_always_returns_non_none(stmt.orelse, after_if)
        if isinstance(stmt, (ast.For, ast.While)):
            if any(
                isinstance(n, ast.Return) and _is_explicit_none_return(n)
                for n in _walk_no_nested(stmt)
            ):
                return False
        elif isinstance(stmt, ast.Try):
            if stmt.finalbody and _continuation_always_returns_non_none(
                stmt.finalbody, False
            ):
                return True
            if any(
                isinstance(n, ast.Return) and _is_explicit_none_return(n)
                for n in _walk_no_nested(stmt)
            ):
                return False
            after_try = _continuation_always_returns_non_none(
                rest, fallthrough_ok
            )
            after_body = _continuation_always_returns_non_none(
                stmt.orelse, after_try
            )
            try_ok = _continuation_always_returns_non_none(
                stmt.body, after_body
            )
            handlers_ok = all(
                _continuation_always_returns_non_none(h.body, after_try)
                for h in stmt.handlers
            )
            return try_ok and handlers_ok
    return fallthrough_ok


def _find_unreturned_response_mutations(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef, resp_names: set[str]
) -> list[int]:
    """Find in-place ``llm_response`` mutations that can reach return None."""
    violation_lines: list[int] = []

    def _check_block(
        stmts: list[ast.stmt], parent_rem_returns_non_none: bool
    ) -> None:
        for idx, stmt in enumerate(stmts):
            rem_returns_non_none = _continuation_always_returns_non_none(
                stmts[idx + 1 :], parent_rem_returns_non_none
            )
            mut_line = _stmt_mutates_response(stmt, resp_names)
            if (
                mut_line is not None
                and not rem_returns_non_none
                and mut_line not in violation_lines
            ):
                violation_lines.append(mut_line)
            if isinstance(stmt, ast.If):
                _check_block(stmt.body, rem_returns_non_none)
                _check_block(stmt.orelse, rem_returns_non_none)
            elif isinstance(stmt, (ast.For, ast.While)):
                _check_block(stmt.body, rem_returns_non_none)
            elif isinstance(stmt, ast.Try):
                after_body = _continuation_always_returns_non_none(
                    stmt.orelse, rem_returns_non_none
                )
                _check_block(stmt.body, after_body)
                for h in stmt.handlers:
                    _check_block(h.body, rem_returns_non_none)
                _check_block(stmt.orelse, rem_returns_non_none)
                _check_block(stmt.finalbody, rem_returns_non_none)

    _check_block(fn_def.body, False)
    return violation_lines


@rule("callbacks")
class UnreturnedLlmResponseMutation(Rule):
    id = "C014"
    name = "unreturned-llm-response-mutation"
    description = (
        "after_model callback mutates llm_response in-place on a path that "
        "returns None or falls through"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        cb_type = _resolve_cb_type(file_path)
        if cb_type != "after_model_callbacks":
            return []

        tree = _parse_ast(content)
        if tree is None:
            return []

        target_fns = _find_callback_ast_fns(tree, cb_type)
        if not target_fns:
            return []

        fn_def = target_fns[0]
        arg_names = [a.arg for a in fn_def.args.args]
        resp_names = {"llm_response"}
        if len(arg_names) >= 2:  # noqa: PLR2004
            resp_names.add(arg_names[1])
        eff_resp_names = resp_names | _collect_response_aliases(
            fn_def, resp_names
        )

        lines = _find_unreturned_response_mutations(fn_def, eff_resp_names)
        if not lines:
            return []

        rel = str(file_path.relative_to(context.project_root))
        resp_param = arg_names[1] if len(arg_names) >= 2 else "llm_response"  # noqa: PLR2004
        return [
            self.make_result(
                file=rel,
                line=line,
                message=(
                    f"In-place mutation to '{resp_param}' on a path that "
                    "returns None or falls through — CES discards in-place "
                    "mutations unless the LlmResponse is explicitly returned"
                ),
                fix=f"Add 'return {resp_param}' after mutating the response.",
            )
            for line in lines
        ]


# ── C015: Unsupported LlmRequest Mutation ────────────────────────────────

_ALLOWED_LLM_REQUEST_ATTRS = frozenset({"contents", "config"})
_ALLOWED_LLM_REQUEST_CONFIG_ATTRS = frozenset(
    {"system_instruction", "excluded_tools"}
)


def _check_llm_request_target(
    target: ast.AST, req_names: set[str]
) -> str | None:
    """Return offending attr path if ``target`` mutates unsupported field."""
    # Direct attribute or subscript/method on llm_request.<attr>
    curr = target
    chain: list[str] = []
    while isinstance(curr, (ast.Attribute, ast.Subscript)):
        if isinstance(curr, ast.Attribute):
            chain.append(curr.attr)
        curr = curr.value
    if not (isinstance(curr, ast.Name) and curr.id in req_names and chain):
        return None
    chain.reverse()
    top_attr = chain[0]
    if top_attr not in _ALLOWED_LLM_REQUEST_ATTRS:
        return f"llm_request.{top_attr}"
    if top_attr == "config" and len(chain) >= 2:  # noqa: PLR2004
        cfg_attr = chain[1]
        if cfg_attr not in _ALLOWED_LLM_REQUEST_CONFIG_ATTRS:
            return f"llm_request.config.{cfg_attr}"
    return None


@rule("callbacks")
class UnsupportedLlmRequestMutation(Rule):
    id = "C015"
    name = "unsupported-llm-request-mutation"
    description = (
        "before_model callback mutates llm_request attributes that the CES "
        "runtime discards"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        cb_type = _resolve_cb_type(file_path)
        if cb_type != "before_model_callbacks":
            return []

        tree = _parse_ast(content)
        if tree is None:
            return []

        target_fns = _find_callback_ast_fns(tree, cb_type)
        if not target_fns:
            return []

        fn_def = target_fns[0]
        arg_names = [a.arg for a in fn_def.args.args]
        req_names = {"llm_request"}
        if len(arg_names) >= 2:  # noqa: PLR2004
            req_names.add(arg_names[1])

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []
        seen_lines: set[tuple[str, int]] = set()

        for node in _walk_no_nested(fn_def):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                for target in targets:
                    offending = _check_llm_request_target(target, req_names)
                    if (
                        offending is not None
                        and (offending, node.lineno) not in seen_lines
                    ):
                        seen_lines.add((offending, node.lineno))
                        results.append(
                            self.make_result(
                                file=rel,
                                line=node.lineno,
                                message=(
                                    f"Mutating '{offending}' has no effect in "
                                    "the CES runtime — only "
                                    "'llm_request.contents', "
                                    "'llm_request.config.system_instruction', "
                                    "and 'llm_request.config.excluded_tools' "
                                    "are honored"
                                ),
                                fix=(
                                    "Use 'llm_request.config.excluded_tools' "
                                    "to hide tools, or modify "
                                    "'llm_request.contents' / "
                                    "'llm_request.config.system_instruction'."
                                ),
                            )
                        )
            elif isinstance(node, ast.Expr) and isinstance(
                node.value, ast.Call
            ):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr in (
                    "append",
                    "extend",
                    "insert",
                    "pop",
                    "remove",
                    "clear",
                    "update",
                ):
                    offending = _check_llm_request_target(
                        call.func.value, req_names
                    )
                    if (
                        offending is not None
                        and (offending, node.lineno) not in seen_lines
                    ):
                        seen_lines.add((offending, node.lineno))
                        results.append(
                            self.make_result(
                                file=rel,
                                line=node.lineno,
                                message=(
                                    f"Mutating '{offending}' has no effect in "
                                    "the CES runtime — only "
                                    "'llm_request.contents', "
                                    "'llm_request.config.system_instruction', "
                                    "and 'llm_request.config.excluded_tools' "
                                    "are honored"
                                ),
                                fix=(
                                    "Use 'llm_request.config.excluded_tools' "
                                    "to hide tools, or modify "
                                    "'llm_request.contents' / "
                                    "'llm_request.config.system_instruction'."
                                ),
                            )
                        )
        return results


# ── C016: Non-String State Dict Key ──────────────────────────────────────


def _extract_non_string_state_subscript_key(
    target: ast.AST, state_aliases: set[str], ctx_names: set[str]
) -> tuple[str, int] | None:
    """Return ``(repr_of_key, lineno)`` if ``target`` is ``state[non_str]``."""
    if isinstance(target, ast.Subscript) and _is_state_container_expr(
        target.value, state_aliases, ctx_names
    ):
        sl = target.slice
        if isinstance(sl, ast.Constant) and not isinstance(sl.value, str):
            return repr(sl.value), sl.lineno
        if isinstance(sl, ast.Tuple):
            return "tuple(...)", sl.lineno
    return None


def _find_non_string_dict_key(
    node: ast.AST,
    bad_local_dicts: dict[str, tuple[str, int]] | None = None,
) -> tuple[str, int] | None:
    """Return ``(repr_of_key, lineno)`` if ``node`` has a non-str dict key."""
    if (
        bad_local_dicts
        and isinstance(node, ast.Name)
        and node.id in bad_local_dicts
    ):
        return bad_local_dicts[node.id]
    for child in _walk_no_nested(node):
        if isinstance(child, ast.Dict):
            for k in child.keys:
                if k is None:
                    continue
                if isinstance(k, ast.Constant) and not isinstance(k.value, str):
                    return repr(k.value), k.lineno
                if isinstance(k, ast.Tuple):
                    return "tuple(...)", k.lineno
            if bad_local_dicts:
                for v in child.values:
                    if isinstance(v, ast.Name) and v.id in bad_local_dicts:
                        return bad_local_dicts[v.id]
    return None


@rule("callbacks")
class NonStringStateDictKey(Rule):
    id = "C016"
    name = "non-string-state-dict-key"
    description = (
        "Dictionary assigned into session state contains non-str constant "
        "keys, which raise TypeError under CES V2 (orjson)"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        tree = _parse_ast(content)
        if tree is None:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []

        for fn_node in tree.body:
            if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            state_aliases, ctx_names = _collect_fn_state_aliases(fn_node)
            bad_local_dicts: dict[str, tuple[str, int]] = {}

            for node in _walk_no_nested(fn_node):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        bad_sub = _extract_non_string_state_subscript_key(
                            target, state_aliases, ctx_names
                        )
                        if bad_sub is not None:
                            bad_repr, bad_line = bad_sub
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=bad_line or node.lineno,
                                    message=(
                                        f"Direct state subscript assignment "
                                        f"state[{bad_repr}] uses a non-string "
                                        "key, which raises 'TypeError: Dict "
                                        "key must be str' in the CES V2 "
                                        "runtime (orjson)"
                                    ),
                                    fix=(
                                        "Convert all session state keys to "
                                        "strings (e.g. str(key))."
                                    ),
                                )
                            )
                            continue
                        st_key = _extract_state_key_target(
                            target, state_aliases, ctx_names
                        )
                        if st_key is not None:
                            bad = _find_non_string_dict_key(
                                node.value, bad_local_dicts
                            )
                            if bad is not None:
                                bad_repr, bad_line = bad
                                results.append(
                                    self.make_result(
                                        file=rel,
                                        line=bad_line or node.lineno,
                                        message=(
                                            f"State assignment to "
                                            f"state['{st_key}'] contains "
                                            f"non-string dict key {bad_repr}, "
                                            "which raises 'TypeError: Dict "
                                            "key must be str' in the CES V2 "
                                            "runtime (orjson)"
                                        ),
                                        fix=(
                                            "Convert all dictionary keys in "
                                            "session state to strings "
                                            "(e.g. str(key))."
                                        ),
                                    )
                                )
                        elif isinstance(target, ast.Name):
                            bad = _find_non_string_dict_key(
                                node.value, bad_local_dicts
                            )
                            if bad is not None:
                                bad_local_dicts[target.id] = bad
                            else:
                                bad_local_dicts.pop(target.id, None)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("update", "setdefault")
                    and _is_state_container_expr(
                        node.func.value, state_aliases, ctx_names
                    )
                ):
                    for arg in node.args:
                        bad = _find_non_string_dict_key(arg, bad_local_dicts)
                        if bad is not None:
                            bad_repr, bad_line = bad
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=bad_line or node.lineno,
                                    message=(
                                        f"state.{node.func.attr}() contains "
                                        f"non-string dict key {bad_repr}, "
                                        "which raises 'TypeError: Dict key "
                                        "must be str' in the CES V2 runtime "
                                        "(orjson)"
                                    ),
                                    fix=(
                                        "Convert all dictionary keys in "
                                        "session state to strings "
                                        "(e.g. str(key))."
                                    ),
                                )
                            )
        return results


# ── C017: Invalid Xbox v2 Tool Invocation ────────────────────────────────


def _collect_shadowed_tools_names_in_scope(scope: ast.AST) -> set[str]:
    """Return variable names if ``tools`` is rebound as a local variable."""
    shadowed: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in (
            scope.args.posonlyargs + scope.args.args + scope.args.kwonlyargs
        ):
            if arg.arg == "tools":
                shadowed.add("tools")
                return shadowed
    for node in _walk_no_nested(scope):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "tools":
                    shadowed.add("tools")
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "tools"
        ):
            shadowed.add("tools")
    return shadowed


def _is_tools_namespace_expr(node: ast.AST, shadowed: set[str]) -> str | None:
    """Return namespace label if ``node`` is ``tools`` or ``ctx.tools``."""
    if (
        isinstance(node, ast.Name)
        and node.id == "tools"
        and "tools" not in shadowed
    ):
        return "tools"
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "tools"
        and isinstance(node.value, ast.Name)
        and node.value.id not in _NON_CTX_HOLDERS
    ):
        return f"{node.value.id}.tools"
    return None


def _check_c017_xbox_tool_hazards(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    """Check Python AST for Xbox v2 ``tools.*`` invocation and probe hazards."""
    if file_path.suffix != ".py":
        return []
    tree = _parse_ast(content)
    if tree is None:
        return []

    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []

    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(node)

    for scope in scopes:
        shadowed = _collect_shadowed_tools_names_in_scope(scope)
        for node in _walk_no_nested(scope):
            if not isinstance(node, ast.Call):
                continue

            # 1. tools.<tool_name>(key=val) or <ctx>.tools.<tool_name>(key=val)
            if (
                isinstance(node.func, ast.Attribute)
                and (
                    ns_label := _is_tools_namespace_expr(
                        node.func.value, shadowed
                    )
                )
                is not None
                and node.func.attr not in _PYTHON_COLLECTION_METHODS
                and node.keywords
            ):
                tool_name = node.func.attr
                kw_names = [kw.arg or "**kwargs" for kw in node.keywords]
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"{ns_label}.{tool_name}() called with keyword "
                            f"arguments ({', '.join(kw_names)}) — CES Xbox v2 "
                            "tool wrappers only accept a single positional "
                            "dict and raise TypeError on keyword arguments"
                        ),
                        fix=(
                            f"Pass a single positional dict: "
                            f"{ns_label}.{tool_name}({{...}})"
                        ),
                    )
                )

            # 2. getattr(tools, ...) or hasattr(tools, ...)
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr")
                and node.args
                and (
                    ns_label := _is_tools_namespace_expr(node.args[0], shadowed)
                )
                is not None
            ):
                fn_name = node.func.id
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"{fn_name}({ns_label}, ...) probe is always "
                            "truthy in CES Xbox v2 because Tools.__getattr__ "
                            "unconditionally returns a callable wrapper and "
                            "never raises AttributeError"
                        ),
                        fix=(
                            "Invoke known attached tools directly via "
                            f"{ns_label}.<tool_name>({{...}}) without probing "
                            f"with {fn_name}({ns_label}, ...)."
                        ),
                    )
                )
    return results


@rule("callbacks")
class CallbackInvalidXboxToolInvocation(Rule):
    id = "C017"
    name = "invalid-xbox-tool-invocation"
    description = (
        "CES Xbox v2 tools.<name>() requires a single positional dict and "
        "getattr/hasattr(tools, ...) never raises AttributeError"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_c017_xbox_tool_hazards(self, file_path, content, context)


@rule("tools")
class ToolInvalidXboxToolInvocation(Rule):
    id = "C017"
    name = "invalid-xbox-tool-invocation"
    description = (
        "CES Xbox v2 tools.<name>() requires a single positional dict and "
        "getattr/hasattr(tools, ...) never raises AttributeError"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_c017_xbox_tool_hazards(self, file_path, content, context)
