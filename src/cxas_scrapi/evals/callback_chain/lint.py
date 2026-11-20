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
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.evals.callback_chain.semantics import (
    STAGE_TO_AGENT_JSON_KEYS,
    STAGE_TO_FUNC_NAME,
    STAGE_TO_GLOBAL_JSON_KEYS,
    infer_callback_name_from_path,
    normalize_image_version,
    normalize_stage,
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


def _expr_can_be_none(expr: ast.AST | None) -> bool:
    """Returns True if `expr` is `None` or a ternary `IfExp` yielding `None`."""
    if expr is None:
        return True
    if isinstance(expr, ast.Constant) and expr.value is None:
        return True
    if isinstance(expr, ast.IfExp):
        return _expr_can_be_none(expr.body) or _expr_can_be_none(expr.orelse)
    return False


def _is_none_return(s: ast.Return) -> bool:
    """Returns True if `s` can directly evaluate to `None`."""
    return _expr_can_be_none(s.value)


def _stmt_can_return_none(stmt: ast.stmt) -> bool:
    """Returns True if `stmt` contains any reachable `return` or `return None`
    outside nested function/class definitions.
    """
    return any(
        isinstance(sub, ast.Return) and _is_none_return(sub)
        for sub in _walk_no_nested(stmt)
    )


def _stmts_guarantee_non_none_return(stmts: list[ast.stmt]) -> bool:
    """Returns True if `stmts` unconditionally return a non-None value."""
    for s in stmts:
        if isinstance(s, ast.Return):
            return not _is_none_return(s)
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
            and not any(_stmt_can_return_none(fs) for fs in s.finalbody)
            and _stmts_guarantee_non_none_return(s.body)
            and s.handlers
            and all(
                _stmts_guarantee_non_none_return(h.body) for h in s.handlers
            )
        ):
            return True
        if _stmt_can_return_none(s):
            return False
    return False


def _find_c106_unreturned_mutations(
    fn_def: ast.FunctionDef, resp_names: set[str]
) -> list[int]:
    """Finds in-place `llm_response` mutations that can reach a `return None`
    (or fall-through).
    """
    violation_lines: list[int] = []

    def _check_block(
        stmts: list[ast.stmt],
        parent_rem_guarantees_return: bool,
        active_aliases: set[str],
    ) -> set[str]:
        aliases = set(active_aliases)
        for idx, stmt in enumerate(stmts):
            if isinstance(stmt, ast.Assign):
                is_resp = _is_response_subexpr(stmt.value, aliases)
                for target in stmt.targets:
                    for t_name in _collect_target_names(target):
                        if is_resp:
                            aliases.add(t_name)
                        else:
                            aliases.discard(t_name)
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                is_resp = _is_response_subexpr(stmt.value, aliases)
                for t_name in _collect_target_names(stmt.target):
                    if is_resp:
                        aliases.add(t_name)
                    else:
                        aliases.discard(t_name)
            elif isinstance(stmt, ast.For):
                is_resp = _is_response_subexpr(stmt.iter, aliases)
                for t_name in _collect_target_names(stmt.target):
                    if is_resp:
                        aliases.add(t_name)
                    else:
                        aliases.discard(t_name)

            rem_guarantees_return = (
                _stmts_guarantee_non_none_return(stmts[idx + 1 :])
                or parent_rem_guarantees_return
            )
            mut_line = _stmt_mutates_response(stmt, aliases)
            if (
                mut_line is not None
                and not rem_guarantees_return
                and mut_line not in violation_lines
            ):
                violation_lines.append(mut_line)
            if isinstance(stmt, ast.If):
                body_aliases = _check_block(
                    stmt.body, rem_guarantees_return, aliases.copy()
                )
                else_aliases = _check_block(
                    stmt.orelse, rem_guarantees_return, aliases.copy()
                )
                aliases = body_aliases | else_aliases
            elif isinstance(
                stmt, (ast.For, ast.While, ast.With, ast.AsyncWith)
            ):
                aliases |= _check_block(
                    stmt.body, rem_guarantees_return, aliases.copy()
                )
            elif isinstance(stmt, ast.Try):
                try_aliases = _check_block(
                    stmt.body, rem_guarantees_return, aliases.copy()
                )
                for h in stmt.handlers:
                    try_aliases |= _check_block(
                        h.body, rem_guarantees_return, aliases.copy()
                    )
                try_aliases |= _check_block(
                    stmt.orelse, rem_guarantees_return, try_aliases.copy()
                )
                aliases = _check_block(
                    stmt.finalbody, rem_guarantees_return, try_aliases.copy()
                )
        return aliases

    _check_block(fn_def.body, False, set(resp_names))
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
    for child in _walk_no_nested(node):
        if (
            isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
            and non_str_key_vars
            and child.id in non_str_key_vars
        ):
            return (
                True,
                getattr(child, "lineno", None) or non_str_key_vars[child.id],
            )
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
                    for st_key, _ in _extract_state_subscript_targets(
                        target, ctx_names, state_aliases
                    ):
                        written.add(st_key)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                st_key = _extract_state_subscript_key(
                    node.target, ctx_names, state_aliases
                )
                if st_key is not None:
                    written.add(st_key)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_state_attr(node.func.value, ctx_names, state_aliases)
                and node.func.attr == "update"
            ):
                if node.args and isinstance(node.args[0], ast.Dict):
                    for k_node in node.args[0].keys:
                        if isinstance(k_node, ast.Constant) and isinstance(
                            k_node.value, str
                        ):
                            written.add(k_node.value)
                for kw in node.keywords:
                    if kw.arg is not None:
                        written.add(kw.arg)
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
            elif isinstance(node, ast.AugAssign):
                k = _extract_state_subscript_key(
                    node.target, ctx_names, state_aliases
                )
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


def _extract_state_subscript_targets(
    target: ast.AST, ctx_names: set[str], state_aliases: set[str]
) -> list[tuple[str, ast.Subscript]]:
    """Extracts `(key, subscript_node)` pairs from direct or tuple-unpacked
    assignments (`state['a'], state['b'] = 1, 2`).
    """
    direct = _extract_state_subscript_key(target, ctx_names, state_aliases)
    if direct is not None and isinstance(target, ast.Subscript):
        return [(direct, target)]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[tuple[str, ast.Subscript]] = []
        for elt in target.elts:
            inner = elt.value if isinstance(elt, ast.Starred) else elt
            out.extend(
                _extract_state_subscript_targets(
                    inner, ctx_names, state_aliases
                )
            )
        return out
    return []


def _spec_unique_id(stage: StageType, idx: int, spec: CallbackSpec) -> str:
    """Returns a collision-free identifier for `spec` within `stage`."""
    scope = "global" if spec.is_global else "agent"
    return f"{stage}:{scope}:{idx}:{spec.path or spec.name}"


def collect_subscript_or_in_keys_by_callback(
    stage: StageType, specs: Sequence[CallbackSpec]
) -> dict[str, set[str]]:
    """Maps each state key read via subscript or `in` check to the set of
    unique callback identifiers that access it.
    """
    readers: dict[str, set[str]] = {}
    func_name = STAGE_TO_FUNC_NAME[stage]
    enabled_specs = [s for s in specs if not s.disabled]
    for idx, spec in enumerate(enabled_specs):
        try:
            tree = ast.parse(spec.python_code, filename=spec.path or spec.name)
        except SyntaxError:
            continue
        cb_id = _spec_unique_id(stage, idx, spec)
        for k in _collect_subscript_or_in_keys_in_module(
            tree, stage, func_name
        ):
            readers.setdefault(k, set()).add(cb_id)
    return readers


def _stmts_unconditionally_exit(stmts: list[ast.stmt]) -> bool:
    """Returns True if `stmts` unconditionally terminate current block flow
    (`return`, `raise`, `break`, `continue`).
    """
    for stmt in stmts:
        if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return True
        if (
            isinstance(stmt, ast.If)
            and stmt.orelse
            and _stmts_unconditionally_exit(stmt.body)
            and _stmts_unconditionally_exit(stmt.orelse)
        ):
            return True
    return False


def _extract_state_test_guards(
    test: ast.AST, ctx_names: set[str], state_aliases: set[str]
) -> tuple[set[str], set[str]]:
    """Extracts `(true_guarded_keys, false_guarded_keys)` proven present in
    `state` when `test` evaluates to True vs False.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        t_g, f_g = _extract_state_test_guards(
            test.operand, ctx_names, state_aliases
        )
        return f_g, t_g
    if isinstance(test, ast.BoolOp):
        sub_pairs = [
            _extract_state_test_guards(v, ctx_names, state_aliases)
            for v in test.values
        ]
        if not sub_pairs:
            return set(), set()
        if isinstance(test.op, ast.And):
            t_union: set[str] = set()
            f_inter = set(sub_pairs[0][1])
            for t_sub, f_sub in sub_pairs:
                t_union |= t_sub
                f_inter &= f_sub
            return t_union, f_inter
        if isinstance(test.op, ast.Or):
            t_inter = set(sub_pairs[0][0])
            f_union: set[str] = set()
            for t_sub, f_sub in sub_pairs:
                t_inter &= t_sub
                f_union |= f_sub
            return t_inter, f_union
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        comp = test.comparators[0]
        if (
            isinstance(op, (ast.In, ast.NotIn))
            and _is_state_attr(comp, ctx_names, state_aliases)
            and isinstance(test.left, ast.Constant)
            and isinstance(test.left.value, str)
        ):
            k = test.left.value
            return ({k}, set()) if isinstance(op, ast.In) else (set(), {k})
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Attribute)
        and _is_state_attr(test.func.value, ctx_names, state_aliases)
        and test.func.attr == "get"
        and test.args
        and isinstance(test.args[0], ast.Constant)
        and isinstance(test.args[0].value, str)
        and (
            len(test.args) == 1
            or (
                isinstance(test.args[1], ast.Constant)
                and not bool(test.args[1].value)
            )
        )
    ):
        return {test.args[0].value}, set()
    return set(), set()


def _collect_c103_findings_in_fn(
    fn_def: ast.FunctionDef,
    *,
    stage: StageType,
    spec: CallbackSpec,
    ctx_names: set[str],
    state_aliases: set[str],
    base_avail_keys: set[str],
    declared_no_default_keys: set[str] | None = None,
) -> list[CallbackChainLintFinding]:
    """Walks `fn_def` in statement/control-flow order to find undeclared or
    uninitialized `state['k']` subscript reads (C103) and `state['k'] += ...`.
    """
    c103_out: list[CallbackChainLintFinding] = []
    no_default_set = declared_no_default_keys or set()

    def _emit_c103(read_key: str, lineno: int) -> None:
        is_decl_no_def = read_key in no_default_set
        if is_decl_no_def:
            msg = (
                f"Direct subscript read `state[{read_key!r}]` on a variable "
                "declared in `app.json` without a default value and not "
                "written or guarded prior to this read."
            )
        else:
            msg = (
                f"Direct subscript read `state[{read_key!r}]` "
                "on a key that is not declared in `app.json` and was "
                "not written prior to this read."
            )
        finding = CallbackChainLintFinding(
            rule_id="C103",
            severity="WARNING",
            stage=stage,
            callback_name=spec.name,
            line=lineno,
            path=spec.path,
            message=msg,
        )
        object.__setattr__(finding, "_is_declared_no_default", is_decl_no_def)
        c103_out.append(finding)

    def _check_expr(expr: ast.AST | None, avail_keys: set[str]) -> set[str]:
        newly_written: set[str] = set()
        if expr is None:
            return newly_written
        if isinstance(
            expr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            return newly_written
        if isinstance(expr, ast.BoolOp):
            running = set(avail_keys)
            for val in expr.values:
                w = _check_expr(val, running)
                newly_written |= w
                running |= w
                t_g, f_g = _extract_state_test_guards(
                    val, ctx_names, state_aliases
                )
                if isinstance(expr.op, ast.And):
                    running |= t_g
                elif isinstance(expr.op, ast.Or):
                    running |= f_g
            return newly_written
        if isinstance(expr, ast.IfExp):
            w_test = _check_expr(expr.test, avail_keys)
            newly_written |= w_test
            t_g, f_g = _extract_state_test_guards(
                expr.test, ctx_names, state_aliases
            )
            w_b = _check_expr(expr.body, avail_keys | w_test | t_g)
            w_e = _check_expr(expr.orelse, avail_keys | w_test | f_g)
            newly_written |= w_b & w_e
            return newly_written

        if isinstance(expr, ast.Subscript) and isinstance(expr.ctx, ast.Load):
            rk = _extract_state_subscript_key(expr, ctx_names, state_aliases)
            if rk is not None and rk not in (avail_keys | newly_written):
                _emit_c103(rk, expr.lineno)
        elif (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Attribute)
            and _is_state_attr(expr.func.value, ctx_names, state_aliases)
        ):
            if (
                expr.func.attr == "setdefault"
                and expr.args
                and isinstance(expr.args[0], ast.Constant)
                and isinstance(expr.args[0].value, str)
            ):
                newly_written.add(expr.args[0].value)
            elif expr.func.attr == "update":
                if expr.args and isinstance(expr.args[0], ast.Dict):
                    for kn in expr.args[0].keys:
                        if isinstance(kn, ast.Constant) and isinstance(
                            kn.value, str
                        ):
                            newly_written.add(kn.value)
                for kw in expr.keywords:
                    if kw.arg is not None:
                        newly_written.add(kw.arg)
        elif (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Attribute)
            and isinstance(expr.func.value, ast.Name)
            and expr.func.value.id in ctx_names
            and expr.func.attr == "set_variable"
            and len(expr.args) >= 2
            and isinstance(expr.args[0], ast.Constant)
            and isinstance(expr.args[0].value, str)
        ):
            newly_written.add(expr.args[0].value)

        for child in ast.iter_child_nodes(expr):
            newly_written |= _check_expr(child, avail_keys | newly_written)
        return newly_written

    def _check_stmts(
        stmts: list[ast.stmt], written_so_far: set[str]
    ) -> set[str]:
        curr_written = set(written_so_far)
        for stmt in stmts:
            if isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            avail = base_avail_keys | curr_written
            if isinstance(stmt, ast.Assign):
                curr_written |= _check_expr(stmt.value, avail)
                for target in stmt.targets:
                    st_targets = _extract_state_subscript_targets(
                        target, ctx_names, state_aliases
                    )
                    if st_targets:
                        for st_k, sub_node in st_targets:
                            curr_written |= _check_expr(
                                sub_node.slice, base_avail_keys | curr_written
                            )
                            curr_written.add(st_k)
                    else:
                        curr_written |= _check_expr(
                            target, base_avail_keys | curr_written
                        )
            elif isinstance(stmt, ast.AnnAssign):
                curr_written |= _check_expr(stmt.value, avail)
                st_k = _extract_state_subscript_key(
                    stmt.target, ctx_names, state_aliases
                )
                if st_k is not None:
                    curr_written.add(st_k)
                else:
                    curr_written |= _check_expr(stmt.target, avail)
            elif isinstance(stmt, ast.AugAssign):
                aug_k = _extract_state_subscript_key(
                    stmt.target, ctx_names, state_aliases
                )
                if aug_k is not None and aug_k not in avail:
                    _emit_c103(aug_k, stmt.lineno)
                else:
                    curr_written |= _check_expr(stmt.target, avail)
                curr_written |= _check_expr(stmt.value, avail)
                if aug_k is not None:
                    curr_written.add(aug_k)
            elif isinstance(stmt, ast.If):
                curr_written |= _check_expr(stmt.test, avail)
                t_g, f_g = _extract_state_test_guards(
                    stmt.test, ctx_names, state_aliases
                )
                b_w = _check_stmts(stmt.body, curr_written | t_g)
                e_w = _check_stmts(stmt.orelse, curr_written | f_g)
                body_exits = _stmts_unconditionally_exit(stmt.body)
                else_exits = bool(
                    stmt.orelse and _stmts_unconditionally_exit(stmt.orelse)
                )
                if body_exits and not else_exits:
                    curr_written = e_w | f_g
                elif else_exits and not body_exits:
                    curr_written = b_w | t_g
                else:
                    curr_written |= (b_w - t_g) | (e_w - f_g)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                curr_written |= _check_expr(stmt.iter, avail)
                for st_k, _ in _extract_state_subscript_targets(
                    stmt.target, ctx_names, state_aliases
                ):
                    curr_written.add(st_k)
                curr_written |= _check_stmts(stmt.body, curr_written)
                curr_written |= _check_stmts(stmt.orelse, curr_written)
            elif isinstance(stmt, ast.While):
                curr_written |= _check_expr(stmt.test, avail)
                t_g, _ = _extract_state_test_guards(
                    stmt.test, ctx_names, state_aliases
                )
                curr_written |= (
                    _check_stmts(stmt.body, curr_written | t_g) - t_g
                )
                curr_written |= _check_stmts(stmt.orelse, curr_written)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    curr_written |= _check_expr(
                        item.context_expr, base_avail_keys | curr_written
                    )
                curr_written |= _check_stmts(stmt.body, curr_written)
            elif isinstance(stmt, ast.Try):
                t_w = _check_stmts(stmt.body, curr_written)
                for h in stmt.handlers:
                    t_w |= _check_stmts(h.body, curr_written)
                t_w |= _check_stmts(stmt.orelse, t_w)
                curr_written = _check_stmts(stmt.finalbody, t_w)
            else:
                for child in ast.iter_child_nodes(stmt):
                    curr_written |= _check_expr(
                        child, base_avail_keys | curr_written
                    )
        return curr_written

    _check_stmts(fn_def.body, set())
    return c103_out


def lint_stage_chain(
    stage: StageType,
    specs: Sequence[CallbackSpec],
    *,
    app_config: dict[str, Any] | None = None,
    image_version: ImageVersion = "v1",
    extra_known_keys: set[str] | None = None,
    subscript_or_in_keys: set[str] | None = None,
    key_readers_by_callback: dict[str, set[str]] | None = None,
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
    eff_key_readers: dict[str, set[str]] = (
        collect_subscript_or_in_keys_by_callback(stage, enabled_specs)
    )
    if key_readers_by_callback:
        for k, cb_ids in key_readers_by_callback.items():
            eff_key_readers.setdefault(k, set()).update(cb_ids)

    eff_sub_or_in_keys: set[str] = set(eff_key_readers.keys())
    if subscript_or_in_keys:
        eff_sub_or_in_keys.update(subscript_or_in_keys)

    # Collect known state keys (predefined + app.json variables with defaults +
    # cross-stage written keys) and declared variables lacking defaults
    known_keys: set[str] = {"current_date", "_session", "session_metadata"}
    declared_no_default_keys: set[str] = set()
    if extra_known_keys:
        known_keys.update(extra_known_keys)
    if app_config:
        for pred_key in (
            "predefinedVariableDeclarations",
            "predefined_variable_declarations",
        ):
            for decl in app_config.get(pred_key) or []:
                if isinstance(decl, dict):
                    v_name = decl.get("name")
                    if isinstance(v_name, str) and v_name:
                        known_keys.add(v_name)
        for list_key in (
            "variableDeclarations",
            "variable_declarations",
        ):
            var_decls = app_config.get(list_key) or []
            for decl in var_decls:
                if isinstance(decl, dict):
                    v_name = decl.get("name")
                    if isinstance(v_name, str) and v_name:
                        schema = decl.get("schema")
                        has_default = (
                            isinstance(schema, dict)
                            and "default" in schema
                            and schema["default"] is not None
                        )
                        if has_default:
                            known_keys.add(v_name)
                        elif v_name not in known_keys:
                            declared_no_default_keys.add(v_name)

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

        # Track state keys written in called module-level helpers, while
        # tracking writes & guards inside `fn_def` strictly in statement order.
        called_helper_names: set[str] = {
            n.func.id
            for n in _walk_no_nested(fn_def)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
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
                known_keys.update(helper_written)
        pre_cb_known_keys: set[str] = set(known_keys)
        curr_cb_id = _spec_unique_id(stage, idx, spec)

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
                            c104_f = CallbackChainLintFinding(
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
                            other_readers = sorted(
                                r
                                for r in eff_key_readers.get(st_key, set())
                                if r != curr_cb_id
                            )
                            object.__setattr__(
                                c104_f, "_other_callback_readers", other_readers
                            )
                            findings.append(c104_f)

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
                        for unpacked_key, _ in _extract_state_subscript_targets(
                            target, ctx_names, state_aliases
                        ):
                            known_keys.add(unpacked_key)
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

            elif isinstance(node, ast.AugAssign):
                aug_st_key = _extract_state_subscript_key(
                    node.target, ctx_names, state_aliases
                )
                if aug_st_key is not None:
                    known_keys.add(aug_st_key)
                if stage == "before_model" and isinstance(
                    node.target, ast.Attribute
                ):
                    target = node.target
                    is_direct_req = (
                        isinstance(target.value, ast.Name)
                        and target.value.id in req_names
                        and target.attr not in ("contents", "config")
                    )
                    is_cfg_req = (
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
                    )
                    if is_direct_req or is_cfg_req:
                        mut_expr = (
                            f"llm_request.{target.attr}"
                            if is_direct_req
                            else f"llm_request.config.{target.attr}"
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
                                    f"Mutating `{mut_expr}` has no effect in "
                                    "CES runtime; only "
                                    "`config.system_instruction`, "
                                    "`config.excluded_tools`, and `contents` "
                                    "are extracted."
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

            # Check state.update(...) for C104 & C108
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_state_attr(node.func.value, ctx_names, state_aliases)
                and node.func.attr == "update"
            ):
                upd_pairs: list[tuple[str | None, ast.AST | None, bool]] = []
                if node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Dict):
                        for k_node, v_node in zip(
                            first_arg.keys, first_arg.values, strict=False
                        ):
                            if isinstance(k_node, ast.Constant) and isinstance(
                                k_node.value, str
                            ):
                                upd_pairs.append((k_node.value, v_node, False))
                            else:
                                upd_pairs.append(
                                    (
                                        None,
                                        v_node,
                                        _is_non_str_key_node(k_node),
                                    )
                                )
                    elif image_version == "v2":
                        has_bad_arg, bad_arg_ln = _has_non_str_dict_key(
                            first_arg, non_str_key_vars
                        )
                        if has_bad_arg:
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C108",
                                    severity="ERROR",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=bad_arg_ln or node.lineno,
                                    path=spec.path,
                                    message=(
                                        "State `update()` contains a "
                                        "non-string dict key, which raises "
                                        "`TypeError: Dict key must be str` "
                                        "under V2 (`orjson`)."
                                    ),
                                )
                            )
                for kw in node.keywords:
                    if kw.arg is not None:
                        upd_pairs.append((kw.arg, kw.value, False))
                    elif image_version == "v2":
                        has_bad_kw, bad_kw_ln = _has_non_str_dict_key(
                            kw.value, non_str_key_vars
                        )
                        if has_bad_kw:
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C108",
                                    severity="ERROR",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=bad_kw_ln or node.lineno,
                                    path=spec.path,
                                    message=(
                                        "State `update()` contains a "
                                        "non-string dict key, which raises "
                                        "`TypeError: Dict key must be str` "
                                        "under V2 (`orjson`)."
                                    ),
                                )
                            )

                for u_key, v_node, is_bad_top_key in upd_pairs:
                    if u_key is not None:
                        known_keys.add(u_key)
                        if (
                            v_node is not None
                            and isinstance(v_node, ast.Constant)
                            and v_node.value is None
                        ):
                            c104_upd_sev: Literal["WARNING", "INFO"] = (
                                "WARNING"
                                if u_key in eff_sub_or_in_keys
                                else "INFO"
                            )
                            c104_uf = CallbackChainLintFinding(
                                rule_id="C104",
                                severity=c104_upd_sev,
                                stage=stage,
                                callback_name=spec.name,
                                line=getattr(v_node, "lineno", None)
                                or node.lineno,
                                path=spec.path,
                                message=(
                                    "Assigning `None` to "
                                    f"`state[{u_key!r}]` via `update()` "
                                    "deletes the key in the CES runtime."
                                ),
                            )
                            other_readers = sorted(
                                r
                                for r in eff_key_readers.get(u_key, set())
                                if r != curr_cb_id
                            )
                            object.__setattr__(
                                c104_uf,
                                "_other_callback_readers",
                                other_readers,
                            )
                            findings.append(c104_uf)
                    if image_version == "v2":
                        has_bad_val, bad_val_ln = (
                            _has_non_str_dict_key(v_node, non_str_key_vars)
                            if v_node is not None
                            else (False, None)
                        )
                        if is_bad_top_key or has_bad_val:
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C108",
                                    severity="ERROR",
                                    stage=stage,
                                    callback_name=spec.name,
                                    line=bad_val_ln or node.lineno,
                                    path=spec.path,
                                    message=(
                                        "State `update()` contains a "
                                        "non-string dict key, which raises "
                                        "`TypeError: Dict key must be str` "
                                        "under V2 (`orjson`)."
                                    ),
                                )
                            )

            # Check state.setdefault(key, default) calls for C104 & C108
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_state_attr(node.func.value, ctx_names, state_aliases)
                and node.func.attr == "setdefault"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                sd_key = node.args[0].value
                known_keys.add(sd_key)
                sd_val = node.args[1] if len(node.args) >= 2 else None
                if sd_val is None or (
                    isinstance(sd_val, ast.Constant) and sd_val.value is None
                ):
                    c104_sd_sev: Literal["WARNING", "INFO"] = (
                        "WARNING" if sd_key in eff_sub_or_in_keys else "INFO"
                    )
                    c104_sdf = CallbackChainLintFinding(
                        rule_id="C104",
                        severity=c104_sd_sev,
                        stage=stage,
                        callback_name=spec.name,
                        line=node.lineno,
                        path=spec.path,
                        message=(
                            f"`state.setdefault({sd_key!r}, None)` assigns "
                            "`None` when missing, deleting the key in the "
                            "CES runtime."
                        ),
                    )
                    other_readers = sorted(
                        r
                        for r in eff_key_readers.get(sd_key, set())
                        if r != curr_cb_id
                    )
                    object.__setattr__(
                        c104_sdf, "_other_callback_readers", other_readers
                    )
                    findings.append(c104_sdf)
                elif image_version == "v2":
                    has_bad_sd, bad_sd_ln = _has_non_str_dict_key(
                        sd_val, non_str_key_vars
                    )
                    if has_bad_sd:
                        findings.append(
                            CallbackChainLintFinding(
                                rule_id="C108",
                                severity="ERROR",
                                stage=stage,
                                callback_name=spec.name,
                                line=bad_sd_ln or node.lineno,
                                path=spec.path,
                                message=(
                                    f"`state.setdefault({sd_key!r}, ...)` "
                                    "contains a non-string dict key, which "
                                    "raises `TypeError: Dict key must be str` "
                                    "under V2 image (`orjson`)."
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
                known_keys.add(st_key)
                if (
                    isinstance(val_node, ast.Constant)
                    and val_node.value is None
                ):
                    c104_set_sev: Literal["WARNING", "INFO"] = (
                        "WARNING" if st_key in eff_sub_or_in_keys else "INFO"
                    )
                    c104_sf = CallbackChainLintFinding(
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
                    other_readers = sorted(
                        r
                        for r in eff_key_readers.get(st_key, set())
                        if r != curr_cb_id
                    )
                    object.__setattr__(
                        c104_sf, "_other_callback_readers", other_readers
                    )
                    findings.append(c104_sf)
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

        # Second pass for C103: statement-order subscript reads `state['k']`
        # (including `state['k'] += ...`) on undeclared and uninitialized keys
        findings.extend(
            _collect_c103_findings_in_fn(
                fn_def,
                stage=stage,
                spec=spec,
                ctx_names=ctx_names,
                state_aliases=state_aliases,
                base_avail_keys=pre_cb_known_keys,
                declared_no_default_keys=declared_no_default_keys,
            )
        )

    if not include_info:
        findings = [f for f in findings if f.severity != "INFO"]
    return findings


def _find_json_line(text: str, needle: str) -> int:
    """Finds 1-based line number of `needle` (preferring JSON-quoted match)."""
    if not needle:
        return 1
    quoted = json.dumps(needle)
    idx = text.find(quoted)
    if idx < 0:
        idx = text.find(needle)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _parse_entries_resilient(
    entries: list[Any],
    *,
    stage: StageType,
    is_global: bool,
    agent_dir: Path,
    app_root: Path | None,
    config_file_path: Path,
    config_raw_text: str,
    c109_findings: list[CallbackChainLintFinding],
) -> list[CallbackSpec]:
    """Parses callback config entries individually, recording C109 on missing
    or unreadable files while preserving all valid callbacks for C101-C108.
    """
    from cxas_scrapi.evals.callback_chain.traces import (  # noqa: PLC0415
        CallbackSpec as _CallbackSpec,
    )

    specs: list[CallbackSpec] = []
    resolved_cfg_str = str(config_file_path.resolve())
    for idx, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        rel_code_path = entry.get("pythonCode") or entry.get("python_code", "")
        inline_code = entry.get("inlineCode") or entry.get("inline_code")
        disabled = bool(entry.get("disabled", False))
        description = entry.get("description", "")
        cb_name = entry.get("name")
        if not cb_name:
            if rel_code_path:
                cb_name = infer_callback_name_from_path(rel_code_path)
            else:
                prefix = f"global_{stage}" if is_global else stage
                cb_name = f"{prefix}_callbacks_{idx:02d}"

        code_str = ""
        resolved_file: Path | None = None
        if inline_code is not None:
            code_str = inline_code
        elif rel_code_path:
            file_candidates: list[Path] = []
            if app_root is not None:
                file_candidates.append(app_root / rel_code_path)
            file_candidates.append(agent_dir / rel_code_path)
            file_candidates.append(Path(rel_code_path))

            for cand in file_candidates:
                try:
                    if cand.is_file():
                        resolved_file = cand.resolve()
                        break
                except OSError:
                    continue

            if resolved_file is not None:
                try:
                    code_str = resolved_file.read_text(encoding="utf-8")
                except Exception as exc:
                    if not disabled:
                        c109_f = CallbackChainLintFinding(
                            rule_id="C109",
                            severity="ERROR",
                            stage=stage,
                            callback_name=cb_name,
                            message=(
                                f"Failed to read '{rel_code_path}' for "
                                f"agent '{agent_dir.name}': "
                                f"{type(exc).__name__}: {exc}"
                            ),
                            line=_find_json_line(
                                config_raw_text, rel_code_path
                            ),
                            path=resolved_cfg_str,
                        )
                        object.__setattr__(c109_f, "_is_config_level", False)
                        c109_findings.append(c109_f)
                    continue
            elif not disabled:
                c109_f = CallbackChainLintFinding(
                    rule_id="C109",
                    severity="ERROR",
                    stage=stage,
                    callback_name=cb_name,
                    message=(
                        "Failed to load callback chain for agent "
                        f"'{agent_dir.name}': FileNotFoundError: "
                        f"Callback pythonCode file not found: "
                        f"'{rel_code_path}'"
                    ),
                    line=_find_json_line(config_raw_text, rel_code_path),
                    path=resolved_cfg_str,
                )
                object.__setattr__(c109_f, "_is_config_level", False)
                c109_findings.append(c109_f)
                continue

        specs.append(
            _CallbackSpec(
                name=cb_name,
                stage=stage,
                python_code=code_str,
                path=str(resolved_file) if resolved_file else rel_code_path,
                description=description,
                disabled=disabled,
                is_global=is_global,
            )
        )
    return specs


def load_agent_lint_specs_resilient(
    agent_dir: Path,
    app_root: Path | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[StageType, list[CallbackSpec]],
    list[CallbackChainLintFinding],
]:
    """Loads sanitized app/agent configs, per-stage `CallbackSpec` lists, and
    any `C109` config/file load findings without aborting remaining callbacks.
    """
    from cxas_scrapi.evals.callback_chain.traces import (  # noqa: PLC0415
        CallbackSpec as _CallbackSpec,
    )

    resolved_agent_path = agent_dir.resolve()
    eff_app_root = Path(app_root).resolve() if app_root is not None else None
    if eff_app_root is None and resolved_agent_path.parent.name == "agents":
        eff_app_root = resolved_agent_path.parent.parent

    c109_findings: list[CallbackChainLintFinding] = []
    app_cfg: dict[str, Any] = {}
    app_json_path: Path | None = None
    app_raw_text = ""
    if eff_app_root is not None and (eff_app_root / "app.json").is_file():
        app_json_path = (eff_app_root / "app.json").resolve()
        try:
            app_raw_text = app_json_path.read_text(encoding="utf-8")
            parsed_app = json.loads(app_raw_text)
            if isinstance(parsed_app, dict):
                app_cfg = parsed_app
        except Exception as exc:
            err_f = CallbackChainLintFinding(
                rule_id="C109",
                severity="ERROR",
                stage="before_model",
                callback_name="app",
                message=(
                    f"Failed to parse '{app_json_path.name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                line=getattr(exc, "lineno", None) or 1,
                path=str(app_json_path),
            )
            object.__setattr__(err_f, "_is_config_level", True)
            c109_findings.append(err_f)

    agent_cfg: dict[str, Any] = {}
    agent_json_path: Path | None = (
        resolved_agent_path / f"{resolved_agent_path.name}.json"
    )
    agent_raw_text = ""
    if agent_json_path.is_file():
        try:
            agent_raw_text = agent_json_path.read_text(encoding="utf-8")
            parsed_ag = json.loads(agent_raw_text)
            if isinstance(parsed_ag, dict):
                agent_cfg = parsed_ag
        except Exception as exc:
            err_f = CallbackChainLintFinding(
                rule_id="C109",
                severity="ERROR",
                stage="before_agent",
                callback_name=agent_dir.name,
                message=(
                    f"Failed to parse '{agent_json_path.name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                line=getattr(exc, "lineno", None) or 1,
                path=str(agent_json_path),
            )
            object.__setattr__(err_f, "_is_config_level", True)
            c109_findings.append(err_f)
    else:
        other_jsons = sorted(resolved_agent_path.glob("*.json"))
        if other_jsons:
            bad_json = other_jsons[0].resolve()
            err_f = CallbackChainLintFinding(
                rule_id="C109",
                severity="ERROR",
                stage="before_agent",
                callback_name=agent_dir.name,
                message=(
                    f"Expected agent config '{agent_json_path.name}' "
                    f"in '{agent_dir.name}', found "
                    f"{[p.name for p in other_jsons]}"
                ),
                line=1,
                path=str(bad_json),
            )
            object.__setattr__(err_f, "_is_config_level", True)
            c109_findings.append(err_f)
        agent_json_path = None

    stage_specs_map: dict[StageType, list[CallbackSpec]] = {}
    for st in STAGE_TO_FUNC_NAME:
        specs: list[CallbackSpec] = []
        if app_json_path is not None:
            for g_key in STAGE_TO_GLOBAL_JSON_KEYS.get(st, ()):
                g_entries = app_cfg.get(g_key)
                if isinstance(g_entries, list) and g_entries:
                    specs.extend(
                        _parse_entries_resilient(
                            g_entries,
                            stage=st,
                            is_global=True,
                            agent_dir=resolved_agent_path,
                            app_root=eff_app_root,
                            config_file_path=app_json_path,
                            config_raw_text=app_raw_text,
                            c109_findings=c109_findings,
                        )
                    )
                    break

        had_agent_json_stage_key = False
        if agent_json_path is not None:
            for a_key in STAGE_TO_AGENT_JSON_KEYS[st]:
                a_entries = agent_cfg.get(a_key)
                if isinstance(a_entries, list):
                    had_agent_json_stage_key = True
                    if a_entries:
                        specs.extend(
                            _parse_entries_resilient(
                                a_entries,
                                stage=st,
                                is_global=False,
                                agent_dir=resolved_agent_path,
                                app_root=eff_app_root,
                                config_file_path=agent_json_path,
                                config_raw_text=agent_raw_text,
                                c109_findings=c109_findings,
                            )
                        )
                    break

        if (
            agent_json_path is None
            and not had_agent_json_stage_key
            and not [s for s in specs if not s.is_global]
        ):
            stage_dir = resolved_agent_path / f"{st}_callbacks"
            if stage_dir.is_dir():
                for cb_code_file in sorted(stage_dir.glob("*/python_code.py")):
                    try:
                        code_txt = cb_code_file.read_text(encoding="utf-8")
                    except Exception as exc:
                        c109_f = CallbackChainLintFinding(
                            rule_id="C109",
                            severity="ERROR",
                            stage=st,
                            callback_name=cb_code_file.parent.name,
                            message=(
                                f"Failed to read '{cb_code_file}' for agent "
                                f"'{resolved_agent_path.name}': "
                                f"{type(exc).__name__}: {exc}"
                            ),
                            line=1,
                            path=str(cb_code_file.resolve()),
                        )
                        object.__setattr__(c109_f, "_is_config_level", False)
                        c109_findings.append(c109_f)
                        continue
                    specs.append(
                        _CallbackSpec(
                            name=cb_code_file.parent.name,
                            stage=st,
                            python_code=code_txt,
                            path=str(cb_code_file.resolve()),
                        )
                    )
        stage_specs_map[st] = specs

    sanitized_app_cfg = {
        k: v
        for k, v in app_cfg.items()
        if k
        not in {gk for gks in STAGE_TO_GLOBAL_JSON_KEYS.values() for gk in gks}
    }
    sanitized_agent_cfg = {
        k: v
        for k, v in agent_cfg.items()
        if k
        not in {ak for aks in STAGE_TO_AGENT_JSON_KEYS.values() for ak in aks}
    }
    return (
        sanitized_app_cfg,
        sanitized_agent_cfg,
        stage_specs_map,
        c109_findings,
    )


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
    norm_stage: StageType | None = (
        normalize_stage(stage) if stage is not None else None
    )
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
        filtered_c109: list[CallbackChainLintFinding] = []
        try:
            (
                sanitized_app_cfg,
                sanitized_agent_cfg,
                stage_specs_map,
                c109_findings,
            ) = load_agent_lint_specs_resilient(ag_dir, app_root)
            filtered_c109 = (
                [
                    f
                    for f in c109_findings
                    if f.stage == norm_stage
                    or getattr(f, "_is_config_level", False)
                ]
                if norm_stage is not None
                else c109_findings
            )
            runner = CallbackChainRunner(
                agent_dir=None,
                app_dir=app_root.resolve(),
                app_config=sanitized_app_cfg,
                agent_config=sanitized_agent_cfg,
                callbacks=stage_specs_map,
                image_version=eff_img,
            )
            ag_findings = filtered_c109 + runner.lint(
                stage=norm_stage,
                image_version=eff_img,
                include_info=include_info,
            )
            for f in ag_findings:
                object.__setattr__(f, "_agent_name", ag_dir.name)
                all_findings.append(f)
        except Exception as exc:
            for f in filtered_c109:
                object.__setattr__(f, "_agent_name", ag_dir.name)
                all_findings.append(f)
            ag_json = ag_dir / f"{ag_dir.name}.json"
            err_finding = CallbackChainLintFinding(
                rule_id="C109",
                severity="ERROR",
                stage=norm_stage or "before_agent",
                callback_name=ag_dir.name,
                line=1,
                path=str(ag_json.resolve())
                if ag_json.is_file()
                else str(ag_dir.resolve()),
                message=(
                    f"Failed to load callback chain for agent "
                    f"'{ag_dir.name}': {type(exc).__name__}: {exc}"
                ),
            )
            object.__setattr__(err_finding, "_agent_name", ag_dir.name)
            all_findings.append(err_finding)
    return all_findings
