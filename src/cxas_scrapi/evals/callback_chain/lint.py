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

"""Cross-callback AST static linter (C011-C019) for CES callback chains."""

from __future__ import annotations

import ast
import dataclasses
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.evals.callback_chain.semantics import STAGE_TO_FUNC_NAME

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
        """Alias for `rule_id` (`C011`-`C019`)."""
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
        if isinstance(stmt, ast.If) and stmt.orelse:
            b_true, line_t = _block_cannot_fall_through(stmt.body, param_names)
            b_false, _ = _block_cannot_fall_through(stmt.orelse, param_names)
            if b_true and b_false:
                return True, line_t
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
    """Checks whether `fn_def` unconditionally short-circuits (for rule C011):

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


def _stmts_contain_non_none_return(stmts: list[ast.stmt]) -> bool:
    """Returns True if any statement in `stmts` (excluding nested defs) is a
    non-None `Return`.
    """
    for s in stmts:
        for child in _walk_no_nested(s):
            if isinstance(child, ast.Return) and not (
                child.value is None
                or (
                    isinstance(child.value, ast.Constant)
                    and child.value.value is None
                )
            ):
                return True
    return False


def _find_c016_unreturned_mutations(
    fn_def: ast.FunctionDef, resp_names: set[str]
) -> list[int]:
    """Finds in-place `llm_response` mutations that are not followed by a
    non-None return.
    """
    violation_lines: list[int] = []

    def _check_block(
        stmts: list[ast.stmt], parent_rem_has_return: bool
    ) -> None:
        for idx, stmt in enumerate(stmts):
            rem_has_return = (
                _stmts_contain_non_none_return(stmts[idx + 1 :])
                or parent_rem_has_return
            )
            mut_line = _stmt_mutates_response(stmt, resp_names)
            if (
                mut_line is not None
                and not rem_has_return
                and mut_line not in violation_lines
            ):
                violation_lines.append(mut_line)
            if isinstance(stmt, ast.If):
                _check_block(stmt.body, rem_has_return)
                _check_block(stmt.orelse, rem_has_return)
            elif isinstance(stmt, (ast.For, ast.While)):
                _check_block(stmt.body, rem_has_return)
            elif isinstance(stmt, ast.Try):
                _check_block(stmt.body, rem_has_return)
                for h in stmt.handlers:
                    _check_block(h.body, rem_has_return)
                _check_block(stmt.orelse, rem_has_return)
                _check_block(stmt.finalbody, rem_has_return)

    _check_block(fn_def.body, False)
    return violation_lines


def _has_non_str_dict_key(node: ast.AST) -> tuple[bool, int | None]:
    """Checks if an AST expression contains a `dict` literal with a non-str
    Constant key.
    """
    for child in _walk_no_nested(node):
        if isinstance(child, ast.Dict):
            for k in child.keys:
                if isinstance(k, ast.Constant) and not isinstance(k.value, str):
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
    """Runs cross-callback static analysis (C011-C019) on a stage's callback
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
        except SyntaxError:
            continue

        fn_def: ast.FunctionDef | None = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                fn_def = node
                break
        if fn_def is None:
            continue

        arg_names = [a.arg for a in fn_def.args.args]
        param_names_set = set(arg_names)
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
        elif stage in ("before_tool", "after_tool") and len(arg_names) >= 3:
            ctx_names.add(arg_names[2])

        # Collect local variable aliases to `ctx.state` / `ctx.variables`
        state_aliases = _collect_state_aliases(fn_def, ctx_names)

        # C011: Unconditional short-circuit when later callbacks exist
        if idx < len(enabled_specs) - 1:
            always_non_none, ret_line = (
                _function_unconditionally_short_circuits(
                    fn_def, param_names_set
                )
            )
            if always_non_none:
                skipped = [s.name for s in enabled_specs[idx + 1 :]]
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C011",
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
        # written in this callback
        checked_or_written_in_cb: set[str] = set()
        for top_node in tree.body:
            if (
                isinstance(top_node, ast.FunctionDef)
                and top_node.name != func_name
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
                checked_or_written_in_cb.add(node.args[0].value)
                if node.func.attr == "setdefault":
                    known_keys.add(node.args[0].value)

        # C012: Track local variable bindings (versioned per reassignment) and
        # mutability
        binding_counter = 0
        current_binding: dict[
            str, tuple[int, Literal["mutable", "immutable", "unknown"]]
        ] = {}
        binding_to_state_keys: dict[
            tuple[str, int],
            list[tuple[str, int, Literal["mutable", "immutable", "unknown"]]],
        ] = {}

        for node in _walk_no_nested(fn_def):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    st_key = _extract_state_subscript_key(
                        target, ctx_names, state_aliases
                    )
                    if st_key is not None:
                        checked_or_written_in_cb.add(st_key)
                        known_keys.add(st_key)

                        # C014: state[k] = None (WARNING if k has subscript/in
                        # check, else INFO)
                        if (
                            isinstance(node.value, ast.Constant)
                            and node.value.value is None
                        ):
                            c014_sev: Literal["WARNING", "INFO"] = (
                                "WARNING"
                                if st_key in eff_sub_or_in_keys
                                else "INFO"
                            )
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C014",
                                    severity=c014_sev,
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

                        # C012: local variable assigned to multiple state keys
                        # without copy
                        if isinstance(node.value, ast.Name):
                            v_id = node.value.id
                            b_id, mut = current_binding.get(
                                v_id, (0, "unknown")
                            )
                            if mut == "mutable":
                                binding_to_state_keys.setdefault(
                                    (v_id, b_id), []
                                ).append((st_key, node.lineno, mut))

                        # C018: non-str dict key in state (v2 only)
                        if image_version == "v2":
                            has_bad_key, bad_line = _has_non_str_dict_key(
                                node.value
                            )
                            if has_bad_key:
                                findings.append(
                                    CallbackChainLintFinding(
                                        rule_id="C018",
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
                    elif isinstance(target, ast.Name):
                        binding_counter += 1
                        current_binding[target.id] = (
                            binding_counter,
                            _classify_rhs_mutability(node.value),
                        )

                    # C017: unsupported llm_request field mutation in
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
                                    rule_id="C017",
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
                            isinstance(target.value, ast.Attribute)
                            and isinstance(target.value.value, ast.Name)
                            and target.value.value.id in req_names
                            and target.value.attr == "config"
                            and target.attr
                            not in ("system_instruction", "excluded_tools")
                        ):
                            findings.append(
                                CallbackChainLintFinding(
                                    rule_id="C017",
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
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                binding_counter += 1
                current_binding[node.target.id] = (
                    binding_counter,
                    _classify_rhs_mutability(node.value),
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
                    c014_set_sev: Literal["WARNING", "INFO"] = (
                        "WARNING" if st_key in eff_sub_or_in_keys else "INFO"
                    )
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C014",
                            severity=c014_set_sev,
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

            # Check Return statements (for C015, only in top-level callback via
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
                if isinstance(node.value, ast.Dict) and not node.value.keys:
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C015",
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
                    (ast.Constant, ast.List, ast.Tuple, ast.Set),
                ):
                    findings.append(
                        CallbackChainLintFinding(
                            rule_id="C015",
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

        # Emit C012 findings for mutable local binding assigned to multiple
        # state keys
        emitted_c012_groups: set[tuple[str, tuple[str, ...]]] = set()
        for (var_name, _b_id), key_entries in binding_to_state_keys.items():
            unique_keys = tuple(sorted({k for k, _, _ in key_entries}))
            if len(unique_keys) >= 2:
                group_sig = (var_name, unique_keys)
                if group_sig in emitted_c012_groups:
                    continue
                emitted_c012_groups.add(group_sig)
                mut_kind = key_entries[-1][2]
                sev: Literal["WARNING", "INFO"] = (
                    "WARNING" if mut_kind == "mutable" else "INFO"
                )
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C012",
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

        # C016: Emit finding only if in-place llm_response mutation reaches
        # `return None`
        if stage == "after_model":
            eff_resp_names = resp_names | _collect_response_aliases(
                fn_def, resp_names
            )
            c016_lines = _find_c016_unreturned_mutations(fn_def, eff_resp_names)
            for mut_line in c016_lines:
                findings.append(
                    CallbackChainLintFinding(
                        rule_id="C016",
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

        # Second pass for C013: direct subscript reads `state['k']` on
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
                            rule_id="C013",
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
