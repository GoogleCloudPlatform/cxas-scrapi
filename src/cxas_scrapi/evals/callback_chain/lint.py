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

"""Cross-callback AST static linter (C101-C109) for CES callback chains."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.evals.callback_chain.semantics import (
    STAGE_TO_FUNC_NAME,
    normalize_image_version,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from cxas_scrapi.evals.callback_chain.traces import (
        CallbackSpec,
        ImageVersion,
        StageType,
    )

_IMMUTABLE_CALL_NAMES: frozenset[str] = frozenset(
    {
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "tuple",
        "frozenset",
        "len",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "isinstance",
        "issubclass",
        "type",
        "abs",
        "round",
        "ord",
        "chr",
        "hex",
        "oct",
        "bin",
        "hash",
        "id",
        "repr",
    }
)

_IMMUTABLE_METHOD_NAMES: frozenset[str] = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "title",
        "capitalize",
        "replace",
        "join",
        "format",
        "startswith",
        "endswith",
        "find",
        "rfind",
        "count",
        "encode",
        "decode",
        "isoformat",
        "strftime",
    }
)

_MUTABLE_CALL_NAMES: frozenset[str] = frozenset(
    {"dict", "list", "set", "defaultdict", "OrderedDict", "Counter", "deque"}
)


@dataclasses.dataclass
class CallbackChainLintFinding:
    """Represents a single static lint finding on a callback chain."""

    rule_id: str
    severity: Literal["ERROR", "WARNING", "INFO"]
    stage: StageType
    callback_name: str
    message: str
    line: int | None = None
    path: str | None = None

    @property
    def code(self) -> str:
        """Alias for `rule_id` (`C101`-`C109`)."""
        return self.rule_id


def _walk_no_nested(root: ast.AST) -> Iterator[ast.AST]:
    """Walks AST nodes inside `root` without descending into nested
    defs/lambdas/classes.
    """
    stack: list[ast.AST] = [root]
    while stack:
        node = stack.pop()
        yield node
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


def _is_state_attr(
    node: ast.AST, ctx_names: set[str], state_alias_names: set[str]
) -> bool:
    """Returns True if `node` represents `ctx.state`, `ctx.variables`, or a
    local alias.
    """
    if isinstance(node, ast.Name) and node.id in state_alias_names:
        return True
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ctx_names
        and node.attr in ("state", "variables")
    )


def _extract_state_subscript_key(
    node: ast.AST, ctx_names: set[str], state_alias_names: set[str]
) -> str | None:
    """If `node` is `ctx.state['k']`, `ctx.variables['k']`, or
    `state_alias['k']`, returns `'k'`.
    """
    if (
        isinstance(node, ast.Subscript)
        and _is_state_attr(node.value, ctx_names, state_alias_names)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return None


def _collect_state_aliases(
    fn_def: ast.FunctionDef, ctx_names: set[str]
) -> set[str]:
    """Collects local variable names assigned `ctx.state` or `ctx.variables`."""
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Assign):
                if _is_state_attr(node.value, ctx_names, aliases):
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
                and _is_state_attr(node.value, ctx_names, aliases)
                and node.target.id not in aliases
            ):
                aliases.add(node.target.id)
                changed = True
    return aliases


def _collect_target_names(target: ast.AST) -> list[str]:
    """Extracts all assigned `ast.Name` identifiers from a target (including
    tuples/lists).
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_collect_target_names(elt))
        return out
    if isinstance(target, ast.Starred):
        return _collect_target_names(target.value)
    return []


def _is_response_subexpr(expr: ast.AST, resp_names: set[str]) -> bool:
    """Returns True if `expr` is `llm_response`, its sub-attribute, or an alias
    chain.
    """
    curr: ast.AST = expr
    while isinstance(curr, (ast.Attribute, ast.Subscript)):
        curr = curr.value
    return isinstance(curr, ast.Name) and curr.id in resp_names


def _collect_response_aliases(
    fn_def: ast.FunctionDef, resp_names: set[str]
) -> set[str]:
    """Collects local variable names assigned from `llm_response` or its
    sub-attributes.
    """
    aliases: set[str] = set()
    for node in _walk_no_nested(fn_def):
        combined = resp_names | aliases
        if isinstance(node, ast.Assign):
            is_resp = _is_response_subexpr(node.value, combined)
            for target in node.targets:
                for t_name in _collect_target_names(target):
                    if is_resp:
                        aliases.add(t_name)
                    else:
                        aliases.discard(t_name)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            is_resp = _is_response_subexpr(node.value, combined)
            for t_name in _collect_target_names(node.target):
                if is_resp:
                    aliases.add(t_name)
                else:
                    aliases.discard(t_name)
        elif isinstance(node, ast.For):
            is_resp = _is_response_subexpr(node.iter, combined)
            for t_name in _collect_target_names(node.target):
                if is_resp:
                    aliases.add(t_name)
                else:
                    aliases.discard(t_name)
    return aliases


def _collect_request_config_aliases(
    fn_def: ast.FunctionDef, req_names: set[str]
) -> set[str]:
    """Collects local variable names assigned `llm_request.config`."""
    aliases: set[str] = set()
    for node in _walk_no_nested(fn_def):
        if isinstance(node, ast.Assign):
            is_cfg = (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id in req_names
                and node.value.attr == "config"
            ) or (isinstance(node.value, ast.Name) and node.value.id in aliases)
            for target in node.targets:
                for t_name in _collect_target_names(target):
                    if is_cfg:
                        aliases.add(t_name)
                    else:
                        aliases.discard(t_name)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            is_cfg = (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id in req_names
                and node.value.attr == "config"
            ) or (isinstance(node.value, ast.Name) and node.value.id in aliases)
            for t_name in _collect_target_names(node.target):
                if is_cfg:
                    aliases.add(t_name)
                else:
                    aliases.discard(t_name)
    return aliases


def _is_definitely_non_none_return_expr(
    expr: ast.AST | None, param_names: set[str]
) -> bool:
    """Returns True only if `expr` is guaranteed at the AST level to be a
    non-None override.

    Matches:
    - Constructor / factory calls: `LlmResponse(...)`,
      `LlmResponse.from_parts(...)`,
      `Content(...)`, `Content.from_parts(...)`, or non-empty `dict(...)`
    - Non-empty dict literal (`{...}`)
    - Direct callback parameter name (e.g. `return llm_response`)
    Excludes arbitrary function calls (`return helper(...)`) and arbitrary local
    variables.
    """
    if expr is None:
        return False
    if isinstance(expr, ast.Constant) and expr.value is None:
        return False
    if isinstance(expr, ast.Dict):
        return len(expr.keys) > 0
    if isinstance(expr, ast.Name):
        return expr.id in param_names
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name):
            if expr.func.id in ("LlmResponse", "Content"):
                return True
            if expr.func.id == "dict" and (expr.args or expr.keywords):
                return True
        elif (
            isinstance(expr.func, ast.Attribute)
            and isinstance(expr.func.value, ast.Name)
            and expr.func.value.id in ("LlmResponse", "Content")
        ):
            return True
    return False


def _block_cannot_fall_through(
    stmts: list[ast.stmt], param_names: set[str]
) -> tuple[bool, int | None]:
    """Returns `(True, lineno)` if control flow in `stmts` cannot reach the
    block end.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Return):
            if _is_definitely_non_none_return_expr(stmt.value, param_names):
                return True, stmt.lineno
            return False, None
        if isinstance(stmt, ast.Raise):
            return True, stmt.lineno
        if isinstance(stmt, ast.If):
            b_true, line_t = _block_cannot_fall_through(stmt.body, param_names)
            if stmt.orelse:
                b_false, line_f = _block_cannot_fall_through(
                    stmt.orelse, param_names
                )
                if b_true and b_false:
                    return True, line_t or line_f
            elif b_true:
                # If the `if` branch always exits (returns non-None or raises),
                # continue checking subsequent statements in `stmts`.
                continue
        if isinstance(stmt, ast.Try):
            b_try, line_try = _block_cannot_fall_through(stmt.body, param_names)
            if (
                b_try
                and stmt.handlers
                and all(
                    _block_cannot_fall_through(h.body, param_names)[0]
                    for h in stmt.handlers
                )
            ):
                return True, line_try
    return False, None


def _function_unconditionally_short_circuits(
    fn_def: ast.FunctionDef, param_names: set[str]
) -> tuple[bool, int | None]:
    """Checks whether `fn_def` unconditionally short-circuits (for rule C101):

    (a) Every `Return` in `fn_def` (excluding nested defs) returns a non-None
    expression;
    (b) Control flow cannot fall off the end of `fn_def` (implicit `return
    None`);
    (c) Return expressions are non-None constructors, non-empty dicts, or
    parameter names.
    """
    returns = [
        node for node in _walk_no_nested(fn_def) if isinstance(node, ast.Return)
    ]
    if not returns:
        return False, None
    for ret_node in returns:
        if not _is_definitely_non_none_return_expr(ret_node.value, param_names):
            return False, None
    return _block_cannot_fall_through(fn_def.body, param_names)


def _classify_rhs_mutability(
    expr: ast.AST,
) -> Literal["mutable", "immutable", "unknown"]:
    """Classifies whether an assigned RHS expression creates/refers to a mutable
    container.
    """
    if isinstance(expr, ast.Constant):
        return "immutable"
    if isinstance(expr, ast.JoinedStr):
        return "immutable"
    if isinstance(expr, ast.Tuple):
        if all(
            _classify_rhs_mutability(elt) == "immutable" for elt in expr.elts
        ):
            return "immutable"
        return "unknown"
    if isinstance(expr, (ast.UnaryOp, ast.Compare)):
        return "immutable"
    if isinstance(expr, ast.BoolOp) and all(
        _classify_rhs_mutability(v) == "immutable" for v in expr.values
    ):
        return "immutable"
    if (
        isinstance(expr, ast.BinOp)
        and _classify_rhs_mutability(expr.left) == "immutable"
        and _classify_rhs_mutability(expr.right) == "immutable"
    ):
        return "immutable"
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
        return "mutable"
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name):
            if expr.func.id in _IMMUTABLE_CALL_NAMES:
                return "immutable"
            if expr.func.id in _MUTABLE_CALL_NAMES:
                return "mutable"
        elif isinstance(expr.func, ast.Attribute):
            if expr.func.attr in _IMMUTABLE_METHOD_NAMES:
                return "immutable"
            if expr.func.attr in ("copy", "deepcopy"):
                return "mutable"
    return "unknown"


def _stmt_mutates_response(stmt: ast.stmt, resp_names: set[str]) -> int | None:
    """If `stmt` directly mutates an `llm_response` attribute/subscript/list,
    returns lineno.
    """
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
        ):
            root = call.func.value
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in resp_names:
                return stmt.lineno
    return None


def _stmts_guarantee_non_none_return(stmts: list[ast.stmt]) -> bool:
    """Returns True if `stmts` unconditionally return a non-None value."""
    for s in stmts:
        if isinstance(s, ast.Return):
            return not (
                s.value is None
                or (isinstance(s.value, ast.Constant) and s.value.value is None)
            )
        if isinstance(s, ast.Raise):
            return True
        if (
            isinstance(s, ast.If)
            and s.orelse
            and _stmts_guarantee_non_none_return(s.body)
            and _stmts_guarantee_non_none_return(s.orelse)
        ):
            return True
        if (
            isinstance(s, ast.Try)
            and _stmts_guarantee_non_none_return(s.body)
            and s.handlers
            and all(
                _stmts_guarantee_non_none_return(h.body) for h in s.handlers
            )
        ):
            return True
    return False


def _find_c106_unreturned_mutations(
    fn_def: ast.FunctionDef, resp_names: set[str]
) -> list[int]:
    """Finds in-place `llm_response` mutations that can reach a `return None`
    (or fall-through).
    """
    violation_lines: list[int] = []
    active_aliases: set[str] = set(resp_names)

    def _check_block(
        stmts: list[ast.stmt], parent_rem_guarantees_return: bool
    ) -> None:
        for idx, stmt in enumerate(stmts):
            if isinstance(stmt, ast.Assign):
                is_resp = _is_response_subexpr(stmt.value, active_aliases)
                for target in stmt.targets:
                    for t_name in _collect_target_names(target):
                        if is_resp:
                            active_aliases.add(t_name)
                        else:
                            active_aliases.discard(t_name)
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                is_resp = _is_response_subexpr(stmt.value, active_aliases)
                for t_name in _collect_target_names(stmt.target):
                    if is_resp:
                        active_aliases.add(t_name)
                    else:
                        active_aliases.discard(t_name)
            elif isinstance(stmt, ast.For):
                is_resp = _is_response_subexpr(stmt.iter, active_aliases)
                for t_name in _collect_target_names(stmt.target):
                    if is_resp:
                        active_aliases.add(t_name)
                    else:
                        active_aliases.discard(t_name)

            rem_guarantees_return = (
                _stmts_guarantee_non_none_return(stmts[idx + 1 :])
                or parent_rem_guarantees_return
            )
            mut_line = _stmt_mutates_response(stmt, active_aliases)
            if (
                mut_line is not None
                and not rem_guarantees_return
                and mut_line not in violation_lines
            ):
                violation_lines.append(mut_line)
            if isinstance(stmt, ast.If):
                _check_block(stmt.body, rem_guarantees_return)
                _check_block(stmt.orelse, rem_guarantees_return)
            elif isinstance(
                stmt, (ast.For, ast.While, ast.With, ast.AsyncWith)
            ):
                _check_block(stmt.body, rem_guarantees_return)
            elif isinstance(stmt, ast.Try):
                _check_block(stmt.body, rem_guarantees_return)
                for h in stmt.handlers:
                    _check_block(h.body, rem_guarantees_return)
                _check_block(stmt.orelse, rem_guarantees_return)
                _check_block(stmt.finalbody, rem_guarantees_return)

    _check_block(fn_def.body, False)
    return violation_lines


def _is_non_str_key_node(k: ast.AST | None) -> bool:
    """Returns True if `k` is a non-str Constant or UnaryOp on a Constant."""
    if k is None:
        return False
    if isinstance(k, ast.Constant):
        return not isinstance(k.value, str)
    return isinstance(k, ast.UnaryOp) and isinstance(k.operand, ast.Constant)


def _has_non_str_dict_key(
    node: ast.AST,
    non_str_key_vars: dict[str, int] | None = None,
) -> tuple[bool, int | None]:
    """Checks if an AST expression contains a `dict` literal with a non-str
    key (or references a local variable bound to such a dict).
    """
    if (
        isinstance(node, ast.Name)
        and non_str_key_vars
        and node.id in non_str_key_vars
    ):
        return True, getattr(node, "lineno", None) or non_str_key_vars[node.id]
    for child in _walk_no_nested(node):
        if isinstance(child, ast.Dict):
            for k in child.keys:
                if _is_non_str_key_node(k):
                    return True, child.lineno
    return False, None


def _collect_written_keys_in_module(
    tree: ast.Module, stage: StageType, func_name: str
) -> set[str]:
    """Collects state keys written in `func_name` or in module-level helper
    functions.
    """
    written: set[str] = set()
    for top_node in tree.body:
        if not isinstance(top_node, ast.FunctionDef):
            continue
        arg_names = [a.arg for a in top_node.args.args]
        ctx_names: set[str] = {"callback_context", "tool_context", "ctx"}
        state_aliases: set[str] = set()
        if top_node.name == func_name:
            if (stage in ("before_agent", "after_agent") and arg_names) or (
                stage in ("before_model", "after_model") and len(arg_names) >= 2
            ):
                ctx_names.add(arg_names[0])
            elif stage in ("before_tool", "after_tool") and len(arg_names) >= 3:
                ctx_names.add(arg_names[2])
        else:
            for a_name in arg_names:
                if a_name in ("state", "variables"):
                    state_aliases.add(a_name)
                elif a_name in ("callback_context", "tool_context", "ctx"):
                    ctx_names.add(a_name)
        state_aliases.update(_collect_state_aliases(top_node, ctx_names))
        for node in _walk_no_nested(top_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    st_key = _extract_state_subscript_key(
                        target, ctx_names, state_aliases
                    )
                    if st_key is not None:
                        written.add(st_key)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id in ctx_names
                        and node.func.attr == "set_variable"
                        and len(node.args) >= 2
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    )
                    or (
                        _is_state_attr(
                            node.func.value, ctx_names, state_aliases
                        )
                        and node.func.attr == "setdefault"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    )
                )
            ):
                written.add(node.args[0].value)
    return written


def collect_written_state_keys_in_specs(
    stage: StageType, specs: Sequence[CallbackSpec]
) -> set[str]:
    """Pre-scans `specs` to collect all state keys written or initialized in
    `stage`.
    """
    written: set[str] = set()
    func_name = STAGE_TO_FUNC_NAME[stage]
    for spec in specs:
        if spec.disabled:
            continue
        try:
            tree = ast.parse(spec.python_code, filename=spec.path or spec.name)
        except SyntaxError:
            continue
        written.update(_collect_written_keys_in_module(tree, stage, func_name))
    return written


def _collect_subscript_or_in_keys_in_module(
    tree: ast.Module, stage: StageType, func_name: str
) -> set[str]:
    """Collects state keys read via `state['k']` or checked via `'k' in
    state`.
    """
    keys: set[str] = set()
    for top_node in tree.body:
        if not isinstance(top_node, ast.FunctionDef):
            continue
        arg_names = [a.arg for a in top_node.args.args]
        ctx_names: set[str] = {"callback_context", "tool_context", "ctx"}
        state_aliases: set[str] = set()
        if top_node.name == func_name:
            if (stage in ("before_agent", "after_agent") and arg_names) or (
                stage in ("before_model", "after_model") and len(arg_names) >= 2
            ):
                ctx_names.add(arg_names[0])
            elif stage in ("before_tool", "after_tool") and len(arg_names) >= 3:
                ctx_names.add(arg_names[2])
        else:
            for a_name in arg_names:
                if a_name in ("state", "variables"):
                    state_aliases.add(a_name)
                elif a_name in ("callback_context", "tool_context", "ctx"):
                    ctx_names.add(a_name)
        state_aliases.update(_collect_state_aliases(top_node, ctx_names))
        for node in _walk_no_nested(top_node):
            if isinstance(node, ast.Subscript) and isinstance(
                node.ctx, ast.Load
            ):
                k = _extract_state_subscript_key(node, ctx_names, state_aliases)
                if k is not None:
                    keys.add(k)
            elif isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators, strict=False):
                    if (
                        isinstance(op, (ast.In, ast.NotIn))
                        and _is_state_attr(comp, ctx_names, state_aliases)
                        and isinstance(node.left, ast.Constant)
                        and isinstance(node.left.value, str)
                    ):
                        keys.add(node.left.value)
    return keys


def collect_subscript_or_in_keys_in_specs(
    stage: StageType, specs: Sequence[CallbackSpec]
) -> set[str]:
    """Pre-scans `specs` to collect state keys read via subscript or `in` check
    in `stage`.
    """
    keys: set[str] = set()
    func_name = STAGE_TO_FUNC_NAME[stage]
    for spec in specs:
        if spec.disabled:
            continue
        try:
            tree = ast.parse(spec.python_code, filename=spec.path or spec.name)
        except SyntaxError:
            continue
        keys.update(
            _collect_subscript_or_in_keys_in_module(tree, stage, func_name)
        )
    return keys


def lint_stage_chain(
    stage: StageType,
    specs: Sequence[CallbackSpec],
    *,
    app_config: dict[str, Any] | None = None,
    image_version: ImageVersion = "v1",
    extra_known_keys: set[str] | None = None,
    subscript_or_in_keys: set[str] | None = None,
    include_info: bool = False,
) -> list[CallbackChainLintFinding]:
    """Runs cross-callback static analysis (C101-C109) on a stage's callback
    chain.
    """
    findings: list[CallbackChainLintFinding] = []
    enabled_specs = [s for s in specs if not s.disabled]
    if not enabled_specs:
        return findings

    func_name = STAGE_TO_FUNC_NAME[stage]

    # Collect keys read via subscript or `in` across this stage + any
    # cross-stage keys
    eff_sub_or_in_keys: set[str] = collect_subscript_or_in_keys_in_specs(
        stage, enabled_specs
    )
    if subscript_or_in_keys:
        eff_sub_or_in_keys.update(subscript_or_in_keys)

    # Collect known state keys (predefined + app.json variables + cross-stage
    # written keys)
    known_keys: set[str] = {"current_date", "_session", "session_metadata"}
    if extra_known_keys:
        known_keys.update(extra_known_keys)
    if app_config:
        for list_key in (
            "variableDeclarations",
            "variable_declarations",
            "predefinedVariableDeclarations",
            "predefined_variable_declarations",
        ):
            var_decls = app_config.get(list_key) or []
            for decl in var_decls:
                if isinstance(decl, dict):
                    v_name = decl.get("name")
                    if isinstance(v_name, str) and v_name:
                        known_keys.add(v_name)

    for idx, spec in enumerate(enabled_specs):
        try:
            tree = ast.parse(spec.python_code, filename=spec.path or spec.name)
        except SyntaxError as exc:
            findings.append(
                CallbackChainLintFinding(
                    rule_id="C109",
                    severity="ERROR",
                    stage=stage,
                    callback_name=spec.name,
                    line=exc.lineno or 1,
                    path=spec.path,
                    message=(
                        f"SyntaxError in callback '{spec.name}': {exc.msg}"
                    ),
                )
            )
            continue

        fn_def: ast.FunctionDef | None = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                fn_def = node
                break
        if fn_def is None:
            continue

        arg_names = [a.arg for a in fn_def.args.args]
        override_param_names: set[str] = set()
        ctx_names: set[str] = {"callback_context", "tool_context", "ctx"}
        req_names: set[str] = {"llm_request"}
        resp_names: set[str] = {"llm_response"}
        if stage in ("before_agent", "after_agent") and arg_names:
            ctx_names.add(arg_names[0])
        elif stage == "before_model" and len(arg_names) >= 2:
            ctx_names.add(arg_names[0])
            req_names.add(arg_names[1])
        elif stage == "after_model" and len(arg_names) >= 2:
            ctx_names.add(arg_names[0])
            resp_names.add(arg_names[1])
            override_param_names.add(arg_names[1])
        elif stage in ("before_tool", "after_tool") and len(arg_names) >= 3:
            ctx_names.add(arg_names[2])
            override_param_names.add(arg_names[1])
            if stage == "after_tool" and len(arg_names) >= 4:
                override_param_names.add(arg_names[3])

        # Collect local variable aliases to `ctx.state` / `ctx.variables`
        state_aliases = _collect_state_aliases(fn_def, ctx_names)
        req_cfg_aliases = (
            _collect_request_config_aliases(fn_def, req_names)
            if stage == "before_model"
            else set()
        )

        # C101: Unconditional short-circuit when later callbacks exist
        if idx < len(enabled_specs) - 1:
            always_non_none, ret_line = (
                _function_unconditionally_short_circuits(
                    fn_def, override_param_names
                )
            )
            if always_non_none:
                skipped = [s.name for s in enabled_specs[idx + 1 :]]
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C101",
                        severity="WARNING",
                        stage=stage,
                        callback_name=spec.name,
                        line=ret_line,
                        path=spec.path,
                        message=(
                            f"Callback '{spec.name}' unconditionally returns "
                            "a non-None value, "
                            f"making subsequent callbacks {skipped} "
                            "unreachable."
                        ),
                    )
                )

        # Track state keys checked via `'k' in state` / `state.get('k')` or
        # written in this callback (only from called helpers or top-level body)
        called_helper_names: set[str] = {
            n.func.id
            for n in _walk_no_nested(fn_def)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        checked_or_written_in_cb: set[str] = set()
        for top_node in tree.body:
            if (
                isinstance(top_node, ast.FunctionDef)
                and top_node.name != func_name
                and top_node.name in called_helper_names
            ):
                helper_written = _collect_written_keys_in_module(
                    ast.Module(body=[top_node], type_ignores=[]),
                    stage,
                    func_name,
                )
                checked_or_written_in_cb.update(helper_written)
                known_keys.update(helper_written)
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators, strict=False):
                    if (
                        isinstance(op, (ast.In, ast.NotIn))
                        and _is_state_attr(comp, ctx_names, state_aliases)
                        and isinstance(node.left, ast.Constant)
                        and isinstance(node.left.value, str)
                    ):
                        checked_or_written_in_cb.add(node.left.value)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_state_attr(node.func.value, ctx_names, state_aliases)
                and node.func.attr in ("get", "setdefault", "pop")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                if node.func.attr == "pop" and len(node.args) < 2:
                    continue
                checked_or_written_in_cb.add(node.args[0].value)
                if node.func.attr == "setdefault":
                    known_keys.add(node.args[0].value)

        # C102: Track local variable bindings (versioned per reassignment) and
        # mutability
        binding_counter = 0
        current_binding: dict[
            str, tuple[int, Literal["mutable", "immutable", "unknown"]]
        ] = {}
        binding_to_state_keys: dict[
            tuple[str, int],
            list[tuple[str, int, Literal["mutable", "immutable", "unknown"]]],
        ] = {}
        non_str_key_vars: dict[str, int] = {}

        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.For):
                for t_name in _collect_target_names(node.target):
                    binding_counter += 1
                    current_binding[t_name] = (binding_counter, "unknown")
                    non_str_key_vars.pop(t_name, None)

            if isinstance(node, ast.Assign):
                for target in node.targets:
                    st_key = _extract_state_subscript_key(
                        target, ctx_names, state_aliases
                    )
                    if st_key is not None:
                        checked_or_written_in_cb.add(st_key)
                        known_keys.add(st_key)

                        # C104: state[k] = None (WARNING if k has subscript/in
                        # check, else INFO)
                        if (
                            isinstance(node.value, ast.Constant)
                            and node.value.value is None
                        ):
                            c104_sev: Literal["WARNING", "INFO"] = (
                                "WARNING"
                                if st_key in eff_sub_or_in_keys
                                else "INFO"
                            )
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C104",
                                    severity=c104_sev,
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=node.lineno,
                                    path=spec.path,
                                    message=(
                                        "Assigning `None` to "
                                        f"`state[{st_key!r}]` deletes the "
                                        "key in the CES runtime; subsequent "
                                        "`'k' in state` "
                                        "checks will be False."
                                    ),
                                )
                            )

                        # C102: local variable assigned to multiple state keys
                        # without copy
                        if isinstance(node.value, ast.Name):
                            v_id = node.value.id
                            b_id, mut = current_binding.get(
                                v_id, (0, "unknown")
                            )
                            if mut != "immutable":
                                binding_to_state_keys.setdefault(
                                    (v_id, b_id), []
                                ).append((st_key, node.lineno, mut))

                        # C108: non-str dict key in state (v2 only)
                        if image_version == "v2":
                            has_bad_key, bad_line = _has_non_str_dict_key(
                                node.value, non_str_key_vars
                            )
                            if has_bad_key:
                                findings.append(
                                    CallbackChainLintFinding(
                                        rule_id="C108",
                                        severity="ERROR",
                                        stage=stage,
                                        callback_name=spec.name,
                                        line=bad_line or node.lineno,
                                        path=spec.path,
                                        message=(
                                            "State assignment to "
                                            f"`state[{st_key!r}]` contains "
                                            "a non-string dict key, which "
                                            "raises `TypeError: "
                                            "Dict key must be str` under V2 "
                                            "image (`orjson`)."
                                        ),
                                    )
                                )
                    else:
                        t_names = _collect_target_names(target)
                        if t_names:
                            rhs_mut = (
                                _classify_rhs_mutability(node.value)
                                if isinstance(target, ast.Name)
                                else "unknown"
                            )
                            has_bad, bad_ln = _has_non_str_dict_key(
                                node.value, non_str_key_vars
                            )
                            for t_name in t_names:
                                binding_counter += 1
                                current_binding[t_name] = (
                                    binding_counter,
                                    rhs_mut,
                                )
                                if has_bad and isinstance(target, ast.Name):
                                    non_str_key_vars[t_name] = (
                                        bad_ln or node.lineno
                                    )
                                else:
                                    non_str_key_vars.pop(t_name, None)

                    # C107: unsupported llm_request field mutation in
                    # before_model
                    if stage == "before_model" and isinstance(
                        target, ast.Attribute
                    ):
                        if (
                            isinstance(target.value, ast.Name)
                            and target.value.id in req_names
                            and target.attr not in ("contents", "config")
                        ):
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C107",
                                    severity="WARNING",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=node.lineno,
                                    path=spec.path,
                                    message=(
                                        f"Mutating `llm_request.{target.attr}` "
                                        "has no effect in "
                                        "CES runtime; only "
                                        "`config.system_instruction`, "
                                        "`config.excluded_tools`, and "
                                        "`contents` are extracted."
                                    ),
                                )
                            )
                        elif (
                            isinstance(target.value, ast.Name)
                            and target.value.id in req_names
                            and target.attr == "config"
                            and isinstance(node.value, ast.Call)
                            and any(
                                kw.arg
                                and kw.arg
                                not in ("system_instruction", "excluded_tools")
                                for kw in node.value.keywords
                            )
                        ):
                            bad_kw = next(
                                kw.arg
                                for kw in node.value.keywords
                                if kw.arg
                                and kw.arg
                                not in ("system_instruction", "excluded_tools")
                            )
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C107",
                                    severity="WARNING",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=node.lineno,
                                    path=spec.path,
                                    message=(
                                        f"Mutating "
                                        f"`llm_request.config.{bad_kw}` "
                                        "has no "
                                        "effect in CES runtime; only "
                                        "`config.system_instruction` "
                                        "and `config.excluded_tools` are "
                                        "extracted."
                                    ),
                                )
                            )
                        elif (
                            (
                                isinstance(target.value, ast.Attribute)
                                and isinstance(target.value.value, ast.Name)
                                and target.value.value.id in req_names
                                and target.value.attr == "config"
                            )
                            or (
                                isinstance(target.value, ast.Name)
                                and target.value.id in req_cfg_aliases
                            )
                        ) and target.attr not in (
                            "system_instruction",
                            "excluded_tools",
                        ):
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C107",
                                    severity="WARNING",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=node.lineno,
                                    path=spec.path,
                                    message=(
                                        f"Mutating "
                                        f"`llm_request.config.{target.attr}` "
                                        "has no "
                                        "effect in CES runtime; only "
                                        "`config.system_instruction` "
                                        "and `config.excluded_tools` are "
                                        "extracted."
                                    ),
                                )
                            )

            elif (
                isinstance(node, ast.AugAssign)
                and stage == "before_model"
                and isinstance(node.target, ast.Attribute)
            ):
                target = node.target
                if (
                    isinstance(target.value, ast.Name)
                    and target.value.id in req_names
                    and target.attr not in ("contents", "config")
                ) or (
                    (
                        (
                            isinstance(target.value, ast.Attribute)
                            and isinstance(target.value.value, ast.Name)
                            and target.value.value.id in req_names
                            and target.value.attr == "config"
                        )
                        or (
                            isinstance(target.value, ast.Name)
                            and target.value.id in req_cfg_aliases
                        )
                    )
                    and target.attr
                    not in ("system_instruction", "excluded_tools")
                ):
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C107",
                            severity="WARNING",
                            stage=stage,
                            callback_name=spec.name,
                            line=node.lineno,
                            path=spec.path,
                            message=(
                                f"Mutating `llm_request.config.{target.attr}` "
                                "has no effect in CES runtime; only "
                                "`config.system_instruction` and "
                                "`config.excluded_tools` are extracted."
                            ),
                        )
                    )

            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                binding_counter += 1
                current_binding[node.target.id] = (
                    binding_counter,
                    _classify_rhs_mutability(node.value),
                )
                has_bad, bad_ln = _has_non_str_dict_key(
                    node.value, non_str_key_vars
                )
                if has_bad:
                    non_str_key_vars[node.target.id] = bad_ln or node.lineno
                else:
                    non_str_key_vars.pop(node.target.id, None)

            # Check state.update({...}) for C104 & C108
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_state_attr(node.func.value, ctx_names, state_aliases)
                and node.func.attr == "update"
                and node.args
                and isinstance(node.args[0], ast.Dict)
            ):
                upd_dict = node.args[0]
                for k_node, v_node in zip(
                    upd_dict.keys, upd_dict.values, strict=False
                ):
                    if isinstance(k_node, ast.Constant) and isinstance(
                        k_node.value, str
                    ):
                        u_key = k_node.value
                        checked_or_written_in_cb.add(u_key)
                        known_keys.add(u_key)
                        if (
                            isinstance(v_node, ast.Constant)
                            and v_node.value is None
                        ):
                            c104_upd_sev: Literal["WARNING", "INFO"] = (
                                "WARNING"
                                if u_key in eff_sub_or_in_keys
                                else "INFO"
                            )
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C104",
                                    severity=c104_upd_sev,
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=node.lineno,
                                    path=spec.path,
                                    message=(
                                        "Assigning `None` to "
                                        f"`state[{u_key!r}]` via `update()` "
                                        "deletes the key in the CES runtime."
                                    ),
                                )
                            )
                    elif image_version == "v2" and _is_non_str_key_node(k_node):
                        findings.append(
                            CallbackChainLintFinding(
                                rule_id="C108",
                                severity="ERROR",
                                stage=stage,
                                callback_name=spec.name,
                                line=node.lineno,
                                path=spec.path,
                                message=(
                                    "State `update()` contains a non-string "
                                    "dict key, which raises `TypeError: Dict "
                                    "key must be str` under V2 (`orjson`)."
                                ),
                            )
                        )

            # Check set_variable calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ctx_names
                and node.func.attr == "set_variable"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                st_key = node.args[0].value
                val_node = node.args[1]
                checked_or_written_in_cb.add(st_key)
                known_keys.add(st_key)
                if (
                    isinstance(val_node, ast.Constant)
                    and val_node.value is None
                ):
                    c104_set_sev: Literal["WARNING", "INFO"] = (
                        "WARNING" if st_key in eff_sub_or_in_keys else "INFO"
                    )
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C104",
                            severity=c104_set_sev,
                            stage=stage,
                            callback_name=spec.name,
                            line=node.lineno,
                            path=spec.path,
                            message=(
                                f"Setting variable `{st_key!r}` to `None` "
                                "deletes the key in "
                                "the CES runtime."
                            ),
                        )
                    )
                if isinstance(val_node, ast.Name):
                    v_id = val_node.id
                    b_id, mut = current_binding.get(v_id, (0, "unknown"))
                    if mut != "immutable":
                        binding_to_state_keys.setdefault(
                            (v_id, b_id), []
                        ).append((st_key, node.lineno, mut))
                if image_version == "v2":
                    has_bad_key, bad_line = _has_non_str_dict_key(
                        val_node, non_str_key_vars
                    )
                    if has_bad_key:
                        findings.append(
                            CallbackChainLintFinding(
                                rule_id="C108",
                                severity="ERROR",
                                stage=stage,
                                callback_name=spec.name,
                                line=bad_line or node.lineno,
                                path=spec.path,
                                message=(
                                    f"Setting variable `{st_key!r}` contains "
                                    "a non-string dict key, which raises "
                                    "`TypeError: Dict key must be str` under "
                                    "V2 image (`orjson`)."
                                ),
                            )
                        )

            # Check Return statements (for C105, only in top-level callback via
            # _walk_no_nested)
            if (
                isinstance(node, ast.Return)
                and stage in ("before_tool", "after_tool")
                and node.value is not None
                and not (
                    isinstance(node.value, ast.Constant)
                    and node.value.value is None
                )
            ):
                is_empty_dict_call = (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "dict"
                    and not node.value.args
                    and not node.value.keywords
                )
                if (
                    isinstance(node.value, ast.Dict) and not node.value.keys
                ) or is_empty_dict_call:
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C105",
                            severity="WARNING",
                            stage=stage,
                            callback_name=spec.name,
                            line=node.lineno,
                            path=spec.path,
                            message=(
                                f"Tool callback '{spec.name}' returns empty "
                                f"dict `{{}}`, "
                                "which breaks the Python loop in 'combined' "
                                "mode "
                                "(`is not None`) but is treated as `None` "
                                "(continue) in "
                                "'per_callback' mode (where the runtime "
                                "treats an empty "
                                "dict as no override)."
                            ),
                        )
                    )
                elif isinstance(
                    node.value,
                    (
                        ast.Constant,
                        ast.JoinedStr,
                        ast.List,
                        ast.Tuple,
                        ast.Set,
                    ),
                ):
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C105",
                            severity="ERROR"
                            if image_version == "v1"
                            else "WARNING",
                            stage=stage,
                            callback_name=spec.name,
                            line=node.lineno,
                            path=spec.path,
                            message=(
                                f"Tool callback '{spec.name}' returns a "
                                "non-dict literal, "
                                "which raises `ValidationError` in V1 image "
                                "and diverges "
                                "between modes in V2."
                            ),
                        )
                    )

        # Emit C102 findings for mutable local binding assigned to multiple
        # state keys
        emitted_c102_groups: set[tuple[str, tuple[str, ...]]] = set()
        for (var_name, _b_id), key_entries in binding_to_state_keys.items():
            unique_keys = tuple(sorted({k for k, _, _ in key_entries}))
            if len(unique_keys) >= 2:
                group_sig = (var_name, unique_keys)
                if group_sig in emitted_c102_groups:
                    continue
                emitted_c102_groups.add(group_sig)
                mut_kind = key_entries[-1][2]
                sev: Literal["WARNING", "INFO"] = (
                    "WARNING" if mut_kind == "mutable" else "INFO"
                )
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C102",
                        severity=sev,
                        stage=stage,
                        callback_name=spec.name,
                        line=key_entries[-1][1],
                        path=spec.path,
                        message=(
                            f"Local variable '{var_name}' is assigned to "
                            "multiple state keys "
                            f"{list(unique_keys)} without `copy`/`deepcopy`, "
                            "creating a shared "
                            "reference aliasing hazard in 'combined' mode."
                        ),
                    )
                )

        # C106: Emit finding only if in-place llm_response mutation reaches
        # `return None`
        if stage == "after_model":
            c106_lines = _find_c106_unreturned_mutations(fn_def, resp_names)
            for mut_line in c106_lines:
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C106",
                        severity="WARNING",
                        stage=stage,
                        callback_name=spec.name,
                        line=mut_line,
                        path=spec.path,
                        message=(
                            f"Callback '{spec.name}' mutates `llm_response` "
                            "in-place on a path "
                            "that returns `None`; in-place mutations are "
                            "discarded in "
                            "'per_callback' mode (and at the end of "
                            "'combined' mode)."
                        ),
                    )
                )

        # Second pass for C103: direct subscript reads `state['k']` on
        # undeclared keys
        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Subscript) and isinstance(
                node.ctx, ast.Load
            ):
                read_key = _extract_state_subscript_key(
                    node, ctx_names, state_aliases
                )
                if (
                    read_key is not None
                    and read_key not in known_keys
                    and read_key not in checked_or_written_in_cb
                ):
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C103",
                            severity="WARNING",
                            stage=stage,
                            callback_name=spec.name,
                            line=node.lineno,
                            path=spec.path,
                            message=(
                                f"Direct subscript read `state[{read_key!r}]` "
                                "on a key that is "
                                "not declared in `app.json` and was not "
                                "written by any earlier "
                                "or same-agent callback."
                            ),
                        )
                    )

    if not include_info:
        findings = [f for f in findings if f.severity != "INFO"]
    return findings


def lint_app_dir(
    app_dir: str | Path,
    *,
    agent_name: str = "*",
    stage: str | None = None,
    image_version: ImageVersion = "v1",
    include_info: bool = False,
) -> list[CallbackChainLintFinding]:
    """Runs C101-C109 static analysis across all matching agents in `app_dir`.

    Returns a list of `CallbackChainLintFinding` objects (tagged with
    `_agent_name` attribute for convenience when callers group by agent).
    """
    import fnmatch  # noqa: PLC0415

    from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
        CallbackChainRunner,
    )

    eff_img = normalize_image_version(image_version)
    app_root = Path(app_dir)
    agents_dir = app_root / "agents"
    if not agents_dir.is_dir():
        return []

    pattern = agent_name or "*"
    agent_dirs = sorted(
        d
        for d in agents_dir.iterdir()
        if d.is_dir() and fnmatch.fnmatch(d.name, pattern)
    )
    all_findings: list[CallbackChainLintFinding] = []
    for ag_dir in agent_dirs:
        try:
            runner = CallbackChainRunner.from_agent_dir(
                ag_dir,
                app_dir=app_root,
                image_version=eff_img,
            )
            ag_findings = runner.lint(
                stage=stage,
                image_version=eff_img,
                include_info=include_info,
            )
            for f in ag_findings:
                object.__setattr__(f, "_agent_name", ag_dir.name)
                all_findings.append(f)
        except Exception as exc:
            err_finding = CallbackChainLintFinding(
                rule_id="C109",
                severity="ERROR",
                stage="chain",  # type: ignore[arg-type]
                callback_name=ag_dir.name,
                message=(
                    f"Failed to load callback chain for agent "
                    f"'{ag_dir.name}': {type(exc).__name__}: {exc}"
                ),
            )
            object.__setattr__(err_finding, "_agent_name", ag_dir.name)
            all_findings.append(err_finding)
    return all_findings
