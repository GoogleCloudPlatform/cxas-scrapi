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

"""Deterministic runtime-trap lint rules from the CXAS Best Practices Guide.

Catches silent CES platform traps documented in Chapters 3-7 and Appendix A:
- T015: Helper function defined above the entry tool function in python_code.py
- T016: Python tool entry function missing a -> dict return annotation
- C011: Assigning None to session state keys in callbacks or tools
- C012: Mutating or building LlmResponse in after_model_callback without return
- C018: Callback LlmResponse / Part SDK traps (partial= kwarg,
        session_escalated, response.partial=True hazards, empty parts=[])
- C019: Sandbox unimported typing annotations, blocked HTTP/process imports, or
        ThreadPoolExecutor combined with synchronous tools.* calls
- I020: Unsupported CES Jinja subset syntax (not in, dict['key'], {# ... #})
- A014: ASYNCHRONOUS tool without audioProcessingConfig.inactivityTimeout
- A015: variableDeclarations count exceeding the 95-variable safety ceiling
- A016: More than 10 enabled llmPolicy guardrails attached to an app
- S009: RE2 quantifier spacing ({m, n}) in guardrail contentFilter regexes
"""

import ast
import json
import re
import warnings
from pathlib import Path

import yaml

from cxas_scrapi.utils.lint_rules.callbacks import _resolve_cb_type
from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

VARIABLE_COUNT_WARNING_LIMIT = 95
LLM_POLICY_GUARDRAIL_LIMIT = 10

_DICT_RETURN_NAMES = frozenset({"dict", "Dict", "Mapping", "MutableMapping"})

_TYPING_ANNOTATION_NAMES = frozenset(
    {
        "Any",
        "Callable",
        "Dict",
        "Final",
        "Iterable",
        "Iterator",
        "List",
        "Literal",
        "Mapping",
        "MutableMapping",
        "Optional",
        "Sequence",
        "Set",
        "Tuple",
        "Union",
    }
)

_BLOCKED_SANDBOX_MODULES = frozenset(
    {
        "aiohttp",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib.request",
        "urllib3",
    }
)

_PARTIAL_TTS_TAG_RE = re.compile(
    r"<\s*(?:warm|empathetic|calm|clear|positive|sympathetic|enthusiasm|"
    r"relief|interest|determination|breath|sigh|sighs|laugh|chuckle|chuckles|"
    r"exhales|gasp|whispers|whispering|short\s+pause|long\s+pause|"
    r"prosody\b[^>\n]*)\s*>",
    re.IGNORECASE,
)

_INLINE_EXAMPLE_OR_FENCE_RE = re.compile(
    r"<\s*inline_example\b[^>]*>[\s\S]*?<\s*/\s*inline_example\s*>|"
    r"```[\s\S]*?```",
    re.IGNORECASE,
)
_JINJA_NOT_IN_RE = re.compile(
    r"\{%-?\s*(?:if|elif)\b[^%]*?\bnot\s+in\b[^%]*?%\}"
)
_JINJA_DICT_SUBSCRIPT_RE = re.compile(
    r"(?:\{\{[^{}]*?\b[A-Za-z_]\w*\[\s*['\"][^'\"]+['\"]\s*\][^{}]*?\}\}|"
    r"\{%[^%]*?\b[A-Za-z_]\w*\[\s*['\"][^'\"]+['\"]\s*\][^%]*?%\})"
)
_JINJA_COMMENT_RE = re.compile(r"\{#[\s\S]*?#\}")

_RE2_SPACED_QUANTIFIER_RE = re.compile(r"(?<!\\)\{(\d+),\s+(\d*)\}")


def _parse_ast(content: str) -> ast.Module | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(content)
    except SyntaxError:
        return None


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _mask_regions(text: str) -> str:
    """Replace fenced/example blocks with newline-preserving spaces."""

    def _blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return _INLINE_EXAMPLE_OR_FENCE_RE.sub(_blank, text)


def _is_dict_annotation(node: ast.expr | None) -> bool:
    if node is None:
        return False
    target = node.value if isinstance(node, ast.Subscript) else node
    if isinstance(target, ast.Name):
        return target.id in _DICT_RETURN_NAMES
    if isinstance(target, ast.Attribute):
        return target.attr in _DICT_RETURN_NAMES
    return False


# ── Tool Rules (T015, T016) ──────────────────────────────────────────────


@rule("tools")
class ToolEntryMustBeFirstDef(Rule):
    id = "T015"
    name = "tool-entry-first-def"
    description = (
        "Entry tool function must be the first top-level def in python_code.py"
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

        top_fns = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if len(top_fns) < 2:  # noqa: PLR2004
            return []

        tool_dir_name = file_path.parent.parent.name
        first_fn = top_fns[0]
        if first_fn.name != tool_dir_name and any(
            fn.name == tool_dir_name for fn in top_fns[1:]
        ):
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    line=first_fn.lineno,
                    message=(
                        f"Helper function '{first_fn.name}' is defined above "
                        f"'{tool_dir_name}' — CES binds the first top-level "
                        "def in python_code.py as the tool entry point"
                    ),
                    fix=(
                        f"Move 'def {first_fn.name}(...)' below "
                        f"'def {tool_dir_name}(...)'."
                    ),
                )
            ]
        return []


@rule("tools")
class ToolReturnDictAnnotation(Rule):
    id = "T016"
    name = "tool-return-dict-annotation"
    description = (
        "Python tool entry function should declare a -> dict return annotation"
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

        top_fns = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not top_fns:
            return []

        tool_dir_name = file_path.parent.parent.name
        entry_fn = next(
            (fn for fn in top_fns if fn.name == tool_dir_name),
            next(
                (fn for fn in top_fns if not fn.name.startswith("_")),
                top_fns[0],
            ),
        )
        if _is_dict_annotation(entry_fn.returns):
            return []

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                line=entry_fn.lineno,
                message=(
                    f"Tool '{entry_fn.name}' is missing a '-> dict' return "
                    "annotation — required for structured schema inference and "
                    "Part.from_function_call dispatch"
                ),
                fix=(
                    "Add return type annotation: "
                    f"def {entry_fn.name}(...) -> dict:"
                ),
            )
        ]


# ── State None Assignment (C011 across callbacks & tools) ────────────────


def _is_state_expr(node: ast.AST, state_aliases: set[str]) -> bool:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "state"
        and not (
            isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls", "runner", "engine")
        )
    ):
        return True
    return isinstance(node, ast.Name) and node.id in state_aliases


def _check_state_none_assignments(
    rule_obj: Rule, rel: str, content: str
) -> list[LintResult]:
    tree = _parse_ast(content)
    if tree is None:
        return []

    results: list[LintResult] = []
    fn_scopes: list[ast.AST] = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    scopes: list[ast.AST] = fn_scopes or [tree]

    for scope in scopes:
        aliases: set[str] = {"state"}
        body = getattr(scope, "body", [])
        for stmt in ast.walk(scope):
            if stmt is not scope and isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if isinstance(stmt, ast.Assign):
                is_state_rhs = _is_state_expr(stmt.value, aliases)
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        if is_state_rhs:
                            aliases.add(target.id)
                        elif target.id in aliases:
                            aliases.discard(target.id)
                    elif (
                        isinstance(target, ast.Subscript)
                        and _is_state_expr(target.value, aliases)
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is None
                    ):
                        results.append(
                            rule_obj.make_result(
                                file=rel,
                                line=stmt.lineno,
                                message=(
                                    "Assigning None to session state corrupts "
                                    "or deletes the key in the CES runtime"
                                ),
                                fix=(
                                    "Assign a typed empty value matching "
                                    "variableDeclarations "
                                    "('', False, 0, {}, [])."
                                ),
                            )
                        )
            elif (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Subscript)
                and _is_state_expr(stmt.target.value, aliases)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is None
            ):
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=stmt.lineno,
                        message=(
                            "Assigning None to session state corrupts or "
                            "deletes the key in the CES runtime"
                        ),
                        fix=(
                            "Assign a typed empty value matching "
                            "variableDeclarations ('', False, 0, {}, [])."
                        ),
                    )
                )
        for node in body:
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "update"
                    and _is_state_expr(call.func.value, aliases)
                ):
                    for arg in call.args:
                        if isinstance(arg, ast.Dict):
                            for val in arg.values:
                                if (
                                    isinstance(val, ast.Constant)
                                    and val.value is None
                                ):
                                    results.append(
                                        rule_obj.make_result(
                                            file=rel,
                                            line=call.lineno,
                                            message=(
                                                "state.update({...: None}) "
                                                "corrupts or deletes session "
                                                "state keys in CES"
                                            ),
                                            fix=(
                                                "Use typed empty values "
                                                "('', False, 0, {}, [])."
                                            ),
                                        )
                                    )
    # Deduplicate by line number
    seen_lines: set[int | None] = set()
    deduped: list[LintResult] = []
    for r in results:
        if r.line not in seen_lines:
            seen_lines.add(r.line)
            deduped.append(r)
    return deduped


@rule("callbacks")
class CallbackStateNoneAssignment(Rule):
    id = "C011"
    name = "state-none-assignment"
    description = "Session state keys should not be assigned None"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        return _check_state_none_assignments(self, rel, content)


@rule("tools")
class ToolStateNoneAssignment(Rule):
    id = "C011"
    name = "state-none-assignment"
    description = "Session state keys should not be assigned None"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        return _check_state_none_assignments(self, rel, content)


# ── after_model_callback Unreturned Response (C012) ──────────────────────


def _root_name(node: ast.AST) -> str | None:
    cur = node
    while isinstance(cur, (ast.Attribute, ast.Subscript)):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def _is_llm_response_ctor(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "LlmResponse":
        return True
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "LlmResponse"
    )


@rule("callbacks")
class AfterModelUnreturnedResponse(Rule):
    id = "C012"
    name = "after-model-unreturned-response"
    description = (
        "after_model_callback must explicitly return LlmResponse when "
        "mutating llm_response or constructing LlmResponse.from_parts"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if (
            file_path.suffix != ".py"
            or _resolve_cb_type(file_path) != "after_model_callbacks"
        ):
            return []
        tree = _parse_ast(content)
        if tree is None:
            return []

        entry_fn = next(
            (
                n
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "after_model_callback"
            ),
            None,
        )
        if entry_fn is None:
            return []

        resp_param = (
            entry_fn.args.args[1].arg
            if len(entry_fn.args.args) >= 2  # noqa: PLR2004
            else "llm_response"
        )
        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []

        mutated_lines: list[int] = []
        has_non_none_return = False

        for node in ast.walk(entry_fn):
            if node is not entry_fn and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if isinstance(node, ast.Expr) and _is_llm_response_ctor(node.value):
                results.append(
                    self.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            "LlmResponse constructed as a standalone "
                            "expression without 'return' — modifications are "
                            "discarded"
                        ),
                        fix="Prepend 'return ' to the LlmResponse(...) call.",
                    )
                )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, (ast.Attribute, ast.Subscript))
                        and _root_name(target) == resp_param
                    ):
                        mutated_lines.append(node.lineno)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in ("append", "extend", "insert", "pop", "clear", "remove")
                and _root_name(node.func.value) == resp_param
            ):
                mutated_lines.append(node.lineno)
            elif (
                isinstance(node, ast.Return)
                and node.value is not None
                and not (
                    isinstance(node.value, ast.Constant)
                    and node.value.value is None
                )
            ):
                has_non_none_return = True

        if mutated_lines and not has_non_none_return:
            results.append(
                self.make_result(
                    file=rel,
                    line=mutated_lines[0],
                    message=(
                        f"'{resp_param}' is mutated in-place in "
                        "after_model_callback, but the function never returns "
                        "an LlmResponse — changes are silently discarded"
                    ),
                    fix=(
                        f"Add 'return {resp_param}' (or return "
                        "LlmResponse.from_parts(...))."
                    ),
                )
            )
        return results


# ── Callback LlmResponse & Part SDK Traps (C018) ─────────────────────────


def _call_is_part_method(node: ast.Call, method_names: tuple[str, ...]) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Part"
        and node.func.attr in method_names
    )


def _expr_has_function_call_part(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Call)
        and _call_is_part_method(sub, ("from_function_call",))
        for sub in ast.walk(node)
    )


def _expr_has_tts_tag(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _call_is_part_method(
            sub, ("from_text", "from_customized_response")
        ):
            for s in ast.walk(sub):
                if (
                    isinstance(s, ast.Constant)
                    and isinstance(s.value, str)
                    and _PARTIAL_TTS_TAG_RE.search(s.value)
                ):
                    return True
    return False


@rule("callbacks")
class CallbackLlmResponseTraps(Rule):
    id = "C018"
    name = "callback-llm-response-traps"
    description = (
        "Catches Part.from_text(partial=...), Part.from_end_session("
        "session_escalated=...), partial=True hazards, and empty parts=[]"
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
        has_inactivity_guard = bool(
            re.search(r"no\s+user\s+activity|inactivity", content, re.I)
        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_is_part_method(node, ("from_text",)):
                for kw in node.keywords:
                    if kw.arg == "partial":
                        results.append(
                            self.make_result(
                                file=rel,
                                line=node.lineno,
                                message=(
                                    "Part.from_text() does not accept a "
                                    "'partial' keyword argument (raises "
                                    "TypeError at runtime)"
                                ),
                                fix=(
                                    "Remove partial=... from Part.from_text(); "
                                    "set response.partial = True on "
                                    "LlmResponse only for plain-text bridges."
                                ),
                            )
                        )
            elif _call_is_part_method(node, ("from_end_session",)):
                for kw in node.keywords:
                    if kw.arg == "session_escalated":
                        results.append(
                            self.make_result(
                                file=rel,
                                line=node.lineno,
                                message=(
                                    "Part.from_end_session() parameter is "
                                    "'escalated=', not 'session_escalated=' "
                                    "(raises TypeError at runtime)"
                                ),
                                fix=(
                                    "Use Part.from_end_session(escalated=True)."
                                ),
                            )
                        )
            elif _is_llm_response_ctor(node) and not has_inactivity_guard:
                parts_expr: ast.expr | None = (
                    node.args[0] if node.args else None
                )
                for kw in node.keywords:
                    if kw.arg == "parts":
                        parts_expr = kw.value
                if isinstance(parts_expr, ast.List) and not parts_expr.elts:
                    results.append(
                        self.make_result(
                            file=rel,
                            line=node.lineno,
                            message=(
                                "LlmResponse.from_parts(parts=[]) hangs or "
                                "drops normal caller turns — empty parts=[] "
                                "is valid only on '<no user activity>' turns"
                            ),
                            fix=(
                                "Use LlmResponse.from_parts("
                                "parts=[Part.from_text(text='')]) on caller "
                                "turns."
                            ),
                        )
                    )

        # Check per-function response.partial = True hazards
        fn_nodes = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for fn in fn_nodes:
            fn_call_vars: set[str] = set()
            tts_tag_vars: set[str] = set()
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    tgt = stmt.targets[0]
                    if isinstance(tgt, ast.Name):
                        if _expr_has_function_call_part(stmt.value):
                            fn_call_vars.add(tgt.id)
                        if _expr_has_tts_tag(stmt.value):
                            tts_tag_vars.add(tgt.id)
            for stmt in ast.walk(fn):
                if not isinstance(stmt, ast.Assign):
                    continue
                if not (
                    isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is True
                ):
                    continue
                for tgt in stmt.targets:
                    if (
                        isinstance(tgt, ast.Attribute)
                        and tgt.attr == "partial"
                        and isinstance(tgt.value, ast.Name)
                    ):
                        vname = tgt.value.id
                        if vname in fn_call_vars:
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=stmt.lineno,
                                    message=(
                                        f"Setting '{vname}.partial = True' on "
                                        "an LlmResponse containing "
                                        "Part.from_function_call silently "
                                        "drops the tool call"
                                    ),
                                    fix=(
                                        f"Remove '{vname}.partial = True' when "
                                        "returning Part.from_function_call."
                                    ),
                                )
                            )
                        if vname in tts_tag_vars:
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=stmt.lineno,
                                    message=(
                                        f"Setting '{vname}.partial = True' "
                                        "causes inline TTS audio tags to be "
                                        "spoken aloud verbatim"
                                    ),
                                    fix=(
                                        f"Remove '{vname}.partial = True' or "
                                        "strip inline TTS tags from partial "
                                        "bridge text."
                                    ),
                                )
                            )
        return results


# ── Sandbox Typing & Blocked Imports (C019 across callbacks & tools) ─────


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
    )


def _check_sandbox_typing_and_imports(
    rule_obj: Rule, rel: str, content: str
) -> list[LintResult]:
    tree = _parse_ast(content)
    if tree is None:
        return []

    results: list[LintResult] = []
    has_future_annotations = False
    defined_names: set[str] = set()
    uses_thread_pool = False

    def _walk_runtime(nodes: list[ast.stmt]) -> None:
        nonlocal has_future_annotations, uses_thread_pool
        for stmt in nodes:
            if isinstance(stmt, ast.If) and _is_type_checking_guard(stmt.test):
                continue
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    bound = (
                        alias.asname
                        if alias.asname
                        else alias.name.split(".")[0]
                    )
                    defined_names.add(bound)
                    if (
                        alias.name in _BLOCKED_SANDBOX_MODULES
                        or alias.name.split(".")[0] in _BLOCKED_SANDBOX_MODULES
                    ):
                        results.append(
                            rule_obj.make_result(
                                file=rel,
                                line=stmt.lineno,
                                message=(
                                    f"Importing '{alias.name}' is blocked or "
                                    "unsupported in the CES Python sandbox"
                                ),
                                fix=(
                                    "Use 'from ces_requests import "
                                    "ces_requests' for outbound HTTP calls."
                                ),
                            )
                        )
            elif isinstance(stmt, ast.ImportFrom):
                mod = stmt.module or ""
                if mod == "__future__" and any(
                    a.name == "annotations" for a in stmt.names
                ):
                    has_future_annotations = True
                for alias in stmt.names:
                    if alias.name != "*":
                        defined_names.add(alias.asname or alias.name)
                    if alias.name == "ThreadPoolExecutor":
                        uses_thread_pool = True
                    full = f"{mod}.{alias.name}" if mod else alias.name
                    if (
                        mod in _BLOCKED_SANDBOX_MODULES
                        or mod.split(".")[0] in _BLOCKED_SANDBOX_MODULES
                        or full in _BLOCKED_SANDBOX_MODULES
                    ):
                        results.append(
                            rule_obj.make_result(
                                file=rel,
                                line=stmt.lineno,
                                message=(
                                    f"Importing from '{mod}' is blocked or "
                                    "unsupported in the CES Python sandbox"
                                ),
                                fix=(
                                    "Use 'from ces_requests import "
                                    "ces_requests' for outbound HTTP calls."
                                ),
                            )
                        )
            elif isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                defined_names.add(stmt.name)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        defined_names.add(t.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(
                stmt.target, ast.Name
            ):
                defined_names.add(stmt.target.id)

            for child_attr in ("body", "orelse", "finalbody"):
                child_list = getattr(stmt, child_attr, None)
                if isinstance(child_list, list) and not isinstance(
                    stmt, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    _walk_runtime(child_list)

    _walk_runtime(tree.body)

    if not has_future_annotations:
        missing_typing: dict[str, int] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ann_nodes: list[ast.expr] = [
                a.annotation
                for a in (
                    node.args.posonlyargs
                    + node.args.args
                    + node.args.kwonlyargs
                )
                if a.annotation is not None
            ]
            if node.args.vararg and node.args.vararg.annotation:
                ann_nodes.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                ann_nodes.append(node.args.kwarg.annotation)
            if node.returns is not None:
                ann_nodes.append(node.returns)
            for ann in ann_nodes:
                for sub in ast.walk(ann):
                    if (
                        isinstance(sub, ast.Name)
                        and sub.id in _TYPING_ANNOTATION_NAMES
                        and sub.id not in defined_names
                    ):
                        missing_typing.setdefault(sub.id, sub.lineno)
        for tname, lineno in sorted(
            missing_typing.items(), key=lambda kv: kv[1]
        ):
            results.append(
                rule_obj.make_result(
                    file=rel,
                    line=lineno,
                    message=(
                        f"Annotation '{tname}' is not imported — raises "
                        "NameError when CES loads the Python module"
                    ),
                    fix=(
                        f"Add 'from typing import {tname}' or use Python 3.10+ "
                        "built-in generics (dict, list, str | None)."
                    ),
                )
            )

    if not uses_thread_pool:
        uses_thread_pool = any(
            isinstance(n, ast.Attribute) and n.attr == "ThreadPoolExecutor"
            for n in ast.walk(tree)
        )
    if uses_thread_pool:
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "tools"
                and n.attr not in ("items", "keys", "values", "get", "update")
            ):
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=n.lineno,
                        message=(
                            "Using synchronous 'tools.*' with "
                            "ThreadPoolExecutor fails in the CES sandbox"
                        ),
                        fix=(
                            "Use 'await asyncio.gather(async_tools."
                            f"{n.attr}(...), ...)' instead."
                        ),
                    )
                )
                break

    return results


@rule("callbacks")
class CallbackSandboxTypingAndImports(Rule):
    id = "C019"
    name = "sandbox-typing-and-imports"
    description = (
        "Checks CES sandbox unimported typing annotations, blocked HTTP/"
        "process modules, and ThreadPoolExecutor + tools.* usage"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        return _check_sandbox_typing_and_imports(self, rel, content)


@rule("tools")
class ToolSandboxTypingAndImports(Rule):
    id = "C019"
    name = "sandbox-typing-and-imports"
    description = (
        "Checks CES sandbox unimported typing annotations, blocked HTTP/"
        "process modules, and ThreadPoolExecutor + tools.* usage"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        return _check_sandbox_typing_and_imports(self, rel, content)


# ── Instruction CES Jinja Subset Syntax (I020) ───────────────────────────


@rule("instructions")
class CesJinjaSubsetSyntax(Rule):
    id = "I020"
    name = "ces-jinja-subset-syntax"
    description = (
        "Instructions must avoid unsupported CES Jinja constructs ('not in', "
        "dict['key'] subscripts, and {# ... #} comments)"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        masked = _mask_regions(content)
        results: list[LintResult] = []

        for m in _JINJA_NOT_IN_RE.finditer(masked):
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(masked, m.start()),
                    message=(
                        "CES Jinja parser does not support the 'not in' "
                        f"operator in '{m.group(0).strip()}'"
                    ),
                    fix="Rewrite as '{% if not (item in collection) %}'.",
                )
            )

        for m in _JINJA_DICT_SUBSCRIPT_RE.finditer(masked):
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(masked, m.start()),
                    message=(
                        "CES Jinja templates do not support bracket string-key "
                        f"lookup in '{m.group(0).strip()}'"
                    ),
                    fix="Use dot notation instead (e.g., {{ obj.field }}).",
                )
            )

        for m in _JINJA_COMMENT_RE.finditer(masked):
            snippet = m.group(0).splitlines()[0][:40]
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(masked, m.start()),
                    message=(
                        f"Jinja comment '{snippet}' is unsupported in CES "
                        "instructions and may break compilation or leak aloud"
                    ),
                    fix=(
                        "Remove {# ... #} comments from deployed instruction "
                        "files."
                    ),
                )
            )

        return results


# ── App Config Rules (A014, A015, A016) ──────────────────────────────────


def _load_structured(file_path: Path, content: str) -> dict | None:
    try:
        data = (
            json.loads(content)
            if file_path.suffix == ".json"
            else (yaml.safe_load(content) or {})
        )
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@rule("config")
class AsyncToolMissingInactivityTimeout(Rule):
    id = "A014"
    name = "async-tool-missing-inactivity-timeout"
    description = (
        "Apps with ASYNCHRONOUS tools must configure "
        "audioProcessingConfig.inactivityTimeout"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in ("app.json", "app.yaml"):
            return []
        data = _load_structured(file_path, content)
        if data is None:
            return []

        tools_root = file_path.parent / "tools"
        if not tools_root.is_dir():
            return []

        async_tools: list[str] = []
        for tool_dir in sorted(tools_root.iterdir()):
            if not tool_dir.is_dir():
                continue
            for suffix in (".json", ".yaml"):
                cfg_path = tool_dir / f"{tool_dir.name}{suffix}"
                if not cfg_path.is_file():
                    continue
                cfg = _load_structured(cfg_path, cfg_path.read_text())
                if not cfg:
                    continue
                exec_type = str(
                    cfg.get("executionType") or cfg.get("execution_type") or ""
                ).upper()
                if exec_type == "ASYNCHRONOUS":
                    async_tools.append(tool_dir.name)
                    break

        if not async_tools:
            return []

        audio_cfg = (
            data.get("audioProcessingConfig")
            or data.get("audio_processing_config")
            or {}
        )
        timeout = (
            audio_cfg.get("inactivityTimeout")
            or audio_cfg.get("inactivity_timeout")
            if isinstance(audio_cfg, dict)
            else None
        )
        if timeout:
            return []

        rel = str(file_path.relative_to(context.project_root))
        joined = ", ".join(async_tools)
        return [
            self.make_result(
                file=rel,
                message=(
                    f"App defines ASYNCHRONOUS tool(s) ({joined}) without "
                    "'audioProcessingConfig.inactivityTimeout' — sessions "
                    "stall waiting for async tool completion"
                ),
                fix=(
                    'Set "audioProcessingConfig": {"inactivityTimeout": "20s"} '
                    "in app.json."
                ),
            )
        ]


@rule("config")
class VariableCountLimit(Rule):
    id = "A015"
    name = "variable-count-limit"
    description = (
        "App variableDeclarations should stay at or below 95 top-level "
        "variables (platform limit ~100)"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in ("app.json", "app.yaml"):
            return []
        data = _load_structured(file_path, content)
        if data is None:
            return []

        decls = (
            data.get("variableDeclarations")
            or data.get("variable_declarations")
            or []
        )
        a015_opts = context.options.get("A015")
        custom_limit = (
            a015_opts.get("max_variables_per_app")
            if isinstance(a015_opts, dict)
            else None
        )
        if custom_limit is None:
            custom_limit = context.options.get("max_variables_per_app")

        if isinstance(custom_limit, (int, float)) and not isinstance(
            custom_limit, bool
        ):
            platform_limit = int(custom_limit)
            warning_limit = int(custom_limit * 0.95)
        else:
            platform_limit = 100
            warning_limit = VARIABLE_COUNT_WARNING_LIMIT

        if not isinstance(decls, list) or len(decls) <= warning_limit:
            return []

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    f"App declares {len(decls)} top-level variables "
                    f"(recommended ceiling: {warning_limit}; "
                    f"platform limit: ~{platform_limit})"
                ),
                fix=(
                    "Group related scalar variables into typed OBJECT "
                    "declarations in variableDeclarations."
                ),
            )
        ]


@rule("config")
class LlmPolicyGuardrailLimit(Rule):
    id = "A016"
    name = "llm-policy-guardrail-limit"
    description = "At most 10 enabled llmPolicy guardrails should be attached"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in ("app.json", "app.yaml"):
            return []
        data = _load_structured(file_path, content)
        if data is None:
            return []

        guardrails_dir = file_path.parent / "guardrails"
        if not guardrails_dir.is_dir():
            return []

        attached_refs = data.get("guardrails")
        attached_set = (
            {str(g).split("/")[-1] for g in attached_refs}
            if isinstance(attached_refs, list)
            else None
        )

        llm_policy_names: list[str] = []
        for gdir in sorted(guardrails_dir.iterdir()):
            if not gdir.is_dir():
                continue
            for suffix in (".json", ".yaml"):
                gfile = gdir / f"{gdir.name}{suffix}"
                if not gfile.is_file():
                    continue
                gcfg = _load_structured(gfile, gfile.read_text())
                if not gcfg or gcfg.get("enabled") is False:
                    continue
                disp = str(gcfg.get("displayName") or gdir.name)
                if attached_set is not None and not (
                    gdir.name in attached_set or disp in attached_set
                ):
                    continue
                if "llmPolicy" in gcfg or "llm_policy" in gcfg:
                    llm_policy_names.append(gdir.name)
                break

        if len(llm_policy_names) <= LLM_POLICY_GUARDRAIL_LIMIT:
            return []

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    f"App has {len(llm_policy_names)} enabled llmPolicy "
                    f"guardrails (platform ceiling: "
                    f"{LLM_POLICY_GUARDRAIL_LIMIT}; each adds ~300 ms latency)"
                ),
                fix=(
                    "Consolidate related llmPolicy prompts or move "
                    "deterministic pattern checks to contentFilter guardrails."
                ),
            )
        ]


# ── Guardrail Structure Rule (S009) ──────────────────────────────────────


def _collect_regex_values(obj: object, parent_key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(_collect_regex_values(v, str(k)))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_regex_values(item, parent_key))
    elif isinstance(obj, str) and "regex" in parent_key.lower():
        found.append(obj)
    return found


@rule("structure")
class GuardrailRe2QuantifierSpacing(Rule):
    id = "S009"
    name = "guardrail-re2-quantifier-spacing"
    description = (
        "Guardrail contentFilter regexes must not include spaces inside "
        "RE2 repetition quantifiers ({m,n})"
    )
    default_severity = Severity.ERROR
    target = "guardrail_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        guardrail_dir = file_path if file_path.is_dir() else file_path.parent
        target_file: Path | None = None
        gcfg: dict | None = None
        for suffix in (".json", ".yaml"):
            candidate = guardrail_dir / f"{guardrail_dir.name}{suffix}"
            if candidate.is_file():
                target_file = candidate
                gcfg = _load_structured(candidate, candidate.read_text())
                break
        if gcfg is None or target_file is None:
            return []

        cf = gcfg.get("contentFilter") or gcfg.get("content_filter")
        if not isinstance(cf, dict):
            return []

        rel = str(target_file.relative_to(context.project_root))
        results: list[LintResult] = []
        for pattern in _collect_regex_values(cf):
            m = _RE2_SPACED_QUANTIFIER_RE.search(pattern)
            if m:
                fixed = f"{{{m.group(1)},{m.group(2)}}}"
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Guardrail regex '{pattern}' contains spaced "
                            f"quantifier '{m.group(0)}' — RE2 treats it as "
                            "literal characters and silently fails to match"
                        ),
                        fix=f"Remove space after comma: '{fixed}'.",
                    )
                )
        return results
