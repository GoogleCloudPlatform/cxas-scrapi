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


"""Variable-reference lint rules (V100-V104).

Surfaces state/instruction/eval references that target undeclared keys,
mistyped nested children, or wrong parent objects after a flat→nested
``variableDeclarations`` migration. See issue #291 for the bug catalogue.
"""

import ast
import json
import re
import typing
import warnings
from pathlib import Path

import yaml

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

# ── Schema loading & caching ─────────────────────────────────────────────

_SCHEMA_CACHE: dict[str, dict] = {}

# Maps declared schema.type → tuple of acceptable Python type names.
SCHEMA_TYPE_TO_PY = {
    "STRING": ("str",),
    "BOOLEAN": ("bool",),
    "INTEGER": ("int",),
    "NUMBER": ("int", "float"),
    "OBJECT": ("dict",),
    "ARRAY": ("list",),
}

# Builtin instruction variables that bypass declaration checks.
INSTRUCTION_BUILTINS = {"current_date"}

# Builtin CES runtime state keys populated by the platform.
STATE_BUILTINS = {"_session"}

# Template ref pattern — matches single {var} and double {{var}} syntax (plus
# dotted paths like {var.child} and {{var.child}}). Excludes {@TOOL: name}
# and {@AGENT: name} directives.
TEMPLATE_RE = re.compile(r"\{{1,2}(?!@)([a-zA-Z_][\w.]*)\}{1,2}")

# Eval YAML keys that contain nested var assignments shaped like
# ``{var_name: {nested_key: value, ...}, ...}``.
EVAL_VAR_KEYS = (
    "variables",
    "session_parameters",
    "sessionParameters",
    "common_session_parameters",
    "commonSessionParameters",
    "inject_variables",
    "injectVariables",
)


def _resolve_app_root(context: LintContext) -> Path | None:
    """Locate the app root (dir containing app.json/app.yaml).

    Prefers ``context.app_root`` when set, otherwise probes
    ``context.app_dir`` directly and its immediate subdirectories
    (mirroring ``Discovery._find_app_root``).
    """
    if context.app_root is not None:
        return context.app_root
    app_dir = context.app_dir
    if app_dir is None or not app_dir.exists():
        return None
    for name in ("app.json", "app.yaml"):
        if (app_dir / name).exists():
            return app_dir
    for d in app_dir.iterdir():
        if d.is_dir() and not d.name.startswith("."):  # noqa: SIM102
            if (d / "app.json").exists() or (d / "app.yaml").exists():
                return d
    return None


def _load_app_config(app_root: Path) -> dict:
    for name in ("app.json", "app.yaml"):
        p = app_root / name
        if not p.exists():
            continue
        try:
            text = p.read_text()
            if name.endswith(".yaml"):
                return yaml.safe_load(text) or {}
            return json.loads(text)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            return {}
    return {}


def _load_var_schema(context: LintContext) -> dict:
    """Return ``{var_name: schema_dict}`` for declared variables (cached)."""
    app_root = _resolve_app_root(context)
    if app_root is None:
        return {}
    key = str(app_root)
    if key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[key]
    cfg = _load_app_config(app_root)
    decls = [
        *(
            cfg.get("variableDeclarations")
            or cfg.get("variable_declarations")
            or []
        ),
        *(
            cfg.get("predefinedVariableDeclarations")
            or cfg.get("predefined_variable_declarations")
            or []
        ),
    ]
    schema: dict[str, dict] = {}
    for d in decls:
        if isinstance(d, dict):
            name = d.get("name")
            if name:
                schema[name] = d.get("schema") or {}
    _SCHEMA_CACHE[key] = schema
    return schema


def _clear_schema_cache() -> None:
    """Test helper — drop the schema cache so a new fixture is re-read."""
    _SCHEMA_CACHE.clear()


# ── Path resolver ────────────────────────────────────────────────────────


def resolve_path(path: str, schema: dict) -> tuple:
    """Resolve a dotted variable path against the declaration schema.

    Returns a tagged tuple — caller dispatches on the first element:
      ("ok", schema_type)             - leaf resolves cleanly
      ("ok_object",)                  - resolves to a declared OBJECT
      ("undeclared",)                 - top-level name is not declared
      ("stale_flat", parent_var)      - top-level name lives nested under parent
      ("not_object", parent_path)     - dotted access but parent isn't OBJECT
      ("no_property", parent_path, child) - parent is OBJECT, child missing
    """
    parts = path.split(".")
    head = parts[0]
    if head not in schema:
        for vname, vschema in schema.items():
            if vschema.get("type") == "OBJECT":
                props = vschema.get("properties") or {}
                if head in props:
                    return ("stale_flat", vname)
        return ("undeclared",)

    current = schema[head]
    for i, child in enumerate(parts[1:], start=1):
        if current.get("type") != "OBJECT":
            return ("not_object", ".".join(parts[:i]))
        props = current.get("properties")
        if not props:
            # Free-form OBJECT with no declared properties schema: any nested
            # key is allowed and its leaf type is unconstrained.
            return ("ok", "UNKNOWN")
        if child not in props:
            return ("no_property", ".".join(parts[:i]), child)
        current = props[child]

    if current.get("type") == "OBJECT":
        return ("ok_object",)
    return ("ok", current.get("type", "UNKNOWN"))


def _available_top_level(schema: dict) -> str:
    return ", ".join(sorted(schema.keys())) or "(none declared)"


def _available_properties(parent_schema: dict) -> str:
    props = parent_schema.get("properties") or {}
    return ", ".join(sorted(props.keys())) or "(none)"


# ── State-access AST visitor (callbacks + tools) ────────────────────────


class _StateAccessVisitor(ast.NodeVisitor):
    """Extract reads/writes against ``state``-like attributes.

    Matches both bare ``state`` (the common local-rebind convention,
    e.g. ``state = callback_context.state``) and any attribute chain
    ending in ``.state`` (``callback_context.state``, ``context.state``).

    Emits records of ``(kind, key, lineno, rhs_type)``:
      kind     - "read" | "write"
      key      - the literal string key (or None if non-literal)
      lineno   - 1-based line number
      rhs_type - Python type name of the RHS literal for writes
                 ("str"/"bool"/"int"/"float"/"dict"/"list"), or None
                 if the RHS is a call/name/etc. (uninferable)
    """

    # Calls whose return type we can infer without semantic analysis.
    KNOWN_CALL_RETURN_TYPES = {
        "len": "int",
        "str": "str",
        "repr": "str",
        "int": "int",
        "float": "float",
        "bool": "bool",
        "list": "list",
        "dict": "dict",
        "tuple": "tuple",
        "set": "set",
        "frozenset": "set",
    }

    _NON_CTX_STATE_HOLDERS = frozenset({"self", "cls", "runner", "engine"})

    def __init__(self) -> None:
        self.accesses: list[tuple[str, str, int, str | None]] = []
        self._skip_subscripts: set[int] = set()
        self._scope_stack: list[tuple[set[str], set[str]]] = []
        self._module_shadows_state = False

    @classmethod
    def _is_state_attr(cls, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "state"
            and not (
                isinstance(node.value, ast.Name)
                and node.value.id in cls._NON_CTX_STATE_HOLDERS
            )
        )

    def _is_state_target(self, node: ast.AST) -> bool:
        if self._is_state_attr(node):
            return True
        if isinstance(node, ast.Name):
            name = node.id
            for aliases, shadowed in reversed(self._scope_stack):
                if name in shadowed:
                    return False
                if name in aliases:
                    return True
            if name == "state" and not self._module_shadows_state:
                return True
        return False

    def visit_Module(self, node: ast.Module) -> None:
        stack: list[ast.AST] = [
            stmt
            for stmt in node.body
            if not isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        ]
        while stack:
            sub = stack.pop()
            if isinstance(sub, ast.Assign) and not self._is_state_attr(
                sub.value
            ):
                for target in sub.targets:
                    if (
                        isinstance(target, ast.Name) and target.id == "state"
                    ) or (
                        isinstance(target, ast.Tuple)
                        and any(
                            isinstance(elt, ast.Name) and elt.id == "state"
                            for elt in target.elts
                        )
                    ):
                        self._module_shadows_state = True
            for child in ast.iter_child_nodes(sub):
                if not isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    stack.append(child)
        self.generic_visit(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope_stack.append((set(), set()))
        try:
            self.generic_visit(node)
        finally:
            self._scope_stack.pop()

    def _record_target_binding(self, target: ast.AST, is_ctx: bool) -> None:
        if not self._scope_stack:
            return
        aliases, shadowed = self._scope_stack[-1]
        if isinstance(target, ast.Name):
            if is_ctx:
                aliases.add(target.id)
                shadowed.discard(target.id)
            else:
                shadowed.add(target.id)
                aliases.discard(target.id)
        elif isinstance(target, ast.Tuple):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    shadowed.add(elt.id)
                    aliases.discard(elt.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    @staticmethod
    def _const_str(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @classmethod
    def _literal_type_name(cls, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, str):
                return "str"
            if isinstance(v, int):
                return "int"
            if isinstance(v, float):
                return "float"
            return None
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, ast.List):
            return "list"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return cls._literal_type_name(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return cls.KNOWN_CALL_RETURN_TYPES.get(node.func.id)
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript) and self._is_state_target(
                target.value
            ):
                key = self._const_str(target.slice)
                if key is not None:
                    rhs_type = self._literal_type_name(node.value)
                    self.accesses.append(
                        ("write", key, target.lineno, rhs_type)
                    )
                self._skip_subscripts.add(id(target))
        self.generic_visit(node)
        is_ctx = self._is_state_attr(node.value)
        for target in node.targets:
            self._record_target_binding(target, is_ctx)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = node.target
        if isinstance(target, ast.Subscript) and self._is_state_target(
            target.value
        ):
            key = self._const_str(target.slice)
            if key is not None and node.value is not None:
                rhs_type = self._literal_type_name(node.value)
                self.accesses.append(("write", key, target.lineno, rhs_type))
            self._skip_subscripts.add(id(target))
        self.generic_visit(node)
        if node.value is not None:
            self._record_target_binding(target, self._is_state_attr(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        target = node.target
        if isinstance(target, ast.Subscript) and self._is_state_target(
            target.value
        ):
            key = self._const_str(target.slice)
            if key is not None:
                # AugAssign is both read+write; record as write for V100/V102.
                self.accesses.append(("write", key, target.lineno, None))
            self._skip_subscripts.add(id(target))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if id(node) not in self._skip_subscripts and self._is_state_target(
            node.value
        ):
            key = self._const_str(node.slice)
            if key is not None:
                self.accesses.append(("read", key, node.lineno, None))
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for left_op, op, right_op in zip(
            operands, node.ops, node.comparators, strict=False
        ):
            if isinstance(op, (ast.In, ast.NotIn)) and self._is_state_target(
                right_op
            ):
                key = self._const_str(left_op)
                if key is not None:
                    self.accesses.append(("read", key, node.lineno, None))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if self._is_state_target(node.func.value):
                if method in ("get", "pop") and node.args:
                    key = self._const_str(node.args[0])
                    if key is not None:
                        self.accesses.append(("read", key, node.lineno, None))
                elif method == "setdefault" and node.args:
                    key = self._const_str(node.args[0])
                    if key is not None:
                        rhs_type = (
                            self._literal_type_name(node.args[1])
                            if len(node.args) >= 2
                            else None
                        )
                        self.accesses.append(
                            ("write", key, node.lineno, rhs_type)
                        )
                elif method == "update":
                    if node.args and isinstance(node.args[0], ast.Dict):
                        arg = node.args[0]
                        for k_node, v_node in zip(
                            arg.keys, arg.values, strict=False
                        ):
                            key = self._const_str(k_node)
                            if key is not None:
                                rhs_type = self._literal_type_name(v_node)
                                self.accesses.append(
                                    ("write", key, node.lineno, rhs_type)
                                )
                    for kw in node.keywords:
                        if kw.arg is not None:
                            rhs_type = self._literal_type_name(kw.value)
                            self.accesses.append(
                                ("write", kw.arg, node.lineno, rhs_type)
                            )
        self.generic_visit(node)


def _collect_state_accesses(
    content: str,
) -> list[tuple[str, str, int, str | None]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(content)
    except SyntaxError:
        return []
    visitor = _StateAccessVisitor()
    visitor.visit(tree)
    return visitor.accesses


# ── Eval YAML traversal ─────────────────────────────────────────────────


def _collect_eval_var_refs(content: str) -> list[tuple[str, int]]:
    """Return ``[(dotted_path, line), ...]`` for keys under known eval var maps.

    Walks any top-level ``variables`` / ``session_parameters`` /
    ``common_session_parameters`` block plus their per-test/per-turn
    siblings (recursive). YAML loaders don't preserve line numbers
    without a custom Loader, so line is best-effort (defaults to 1).
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if not data:
        return []
    refs: list[tuple[str, int]] = []
    _walk_eval_for_var_keys(data, content, refs)
    return refs


def _walk_eval_for_var_keys(node: typing.Any, content: str, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in EVAL_VAR_KEYS and isinstance(v, dict):
                _emit_var_paths(v, content, out, prefix="")
            else:
                _walk_eval_for_var_keys(v, content, out)
    elif isinstance(node, list):
        for item in node:
            _walk_eval_for_var_keys(item, content, out)


def _emit_var_paths(node: dict, content: str, out: list, prefix: str) -> None:
    """Emit (path, line) for each leaf-bearing key in a nested var map."""
    for k, v in node.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        line = _find_key_line(content, k)
        if isinstance(v, dict):
            # Emit the parent path so resolver can confirm it's a declared
            # OBJECT, then recurse for nested children.
            out.append((path, line))
            _emit_var_paths(v, content, out, prefix=path)
        else:
            out.append((path, line))


def _find_key_line(content: str, key: str) -> int:
    pattern = re.compile(rf"^\s*{re.escape(str(key))}\s*:", re.MULTILINE)
    m = pattern.search(content)
    return content[: m.start()].count("\n") + 1 if m else 1


# ── Instruction template extraction ──────────────────────────────────────


def _strip_inline_examples(content: str) -> str:
    """Blank out ``<inline_example>...</inline_example>`` blocks.

    Inline examples may contain literal ``{var}`` snippets that should
    not be lint-checked. Replace them with same-length whitespace so
    line numbers are preserved.
    """
    pattern = re.compile(
        r"<inline_example\b[^>]*>.*?</inline_example>",
        re.DOTALL | re.IGNORECASE,
    )
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), content)


def _collect_template_refs(content: str) -> list[tuple[str, int]]:
    stripped = _strip_inline_examples(content)
    refs = []
    seen: set[tuple[str, int]] = set()
    for m in TEMPLATE_RE.finditer(stripped):
        ref = m.group(1)
        if ref in INSTRUCTION_BUILTINS:
            continue
        line = stripped[: m.start()].count("\n") + 1
        if (ref, line) in seen:
            continue
        seen.add((ref, line))
        refs.append((ref, line))
    return refs


def _extract_non_fstring_constants(tree: ast.AST) -> set[str]:
    """Return string constants from AST, excluding f-string parts."""
    fstring_part_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant):
                    fstring_part_ids.add(id(child))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in fstring_part_ids
    }


def _agent_callback_substitutes_ref(file_path: Path, ref: str) -> bool:
    """Return True if a callback in the same agent dir substitutes ``{ref}``."""
    agent_dir = file_path.parent
    if agent_dir.parent.name != "agents" or not agent_dir.is_dir():
        return False
    placeholder = f"{{{ref}}}"
    for py_file in agent_dir.rglob("*.py"):
        try:
            text = py_file.read_text(errors="ignore")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        if placeholder in _extract_non_fstring_constants(tree):
            return True
    return False


# ── Rules ────────────────────────────────────────────────────────────────


@rule("callbacks")
class CallbackVariableDeclared(Rule):
    id = "V100"
    name = "variable-declared"
    description = (
        "State reads/writes must reference a declared "
        "variableDeclarations entry"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v100_python(self, file_path, content, context)


@rule("tools")
class ToolVariableDeclared(Rule):
    id = "V100"
    name = "variable-declared"
    description = (
        "State reads/writes must reference a declared "
        "variableDeclarations entry"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v100_python(self, file_path, content, context)


@rule("evals")
class EvalVariableDeclared(Rule):
    id = "V100"
    name = "variable-declared"
    description = (
        "Eval session_parameters/variables must reference declared variables"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix not in (".yaml", ".yml"):
            return []
        schema = _load_var_schema(context)
        if not schema:
            return []
        rel = str(file_path.relative_to(context.project_root))
        results = []
        seen: set[tuple[str, int]] = set()
        failed_prefixes: set[str] = set()
        for path, line in _collect_eval_var_refs(content):
            if (path, line) in seen or any(
                path.startswith(f"{p}.") for p in failed_prefixes
            ):
                continue
            verdict = resolve_path(path, schema)
            tag = verdict[0]
            if tag in ("ok", "ok_object"):
                continue
            seen.add((path, line))
            failed_prefixes.add(path)
            if tag == "undeclared":
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Eval variable '{path}' is not declared in "
                            "app.json variableDeclarations"
                        ),
                        fix=(
                            "Declare it under variableDeclarations, or "
                            f"reference a known top-level var: "
                            f"{_available_top_level(schema)}"
                        ),
                    )
                )
            elif tag == "stale_flat":
                results.append(
                    _stale_flat_result(self, rel, line, path, verdict[1])
                )
            elif tag == "no_property":
                parent_path = verdict[1]
                child = verdict[2]
                parent_schema = _walk_to_schema(parent_path, schema)
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Eval variable '{path}' — '{parent_path}' has no "
                            f"property '{child}' (declared properties: "
                            f"{_available_properties(parent_schema)})"
                        ),
                        fix=(
                            f"Use one of the declared properties on "
                            f"'{parent_path}', or add '{child}' to its schema"
                        ),
                    )
                )
            elif tag == "not_object":
                parent_path = verdict[1]
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Eval variable '{path}' — '{parent_path}' is not "
                            f"an OBJECT; cannot access nested key '{path}'"
                        ),
                        fix=(
                            f"Change '{parent_path}' schema type to OBJECT, "
                            "or use a flat reference"
                        ),
                    )
                )
        return results


def _check_v100_python(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    if file_path.suffix != ".py":
        return []
    schema = _load_var_schema(context)
    if not schema:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results = []
    seen: set[tuple[str, int]] = set()
    for _kind, key, line, _rhs in _collect_state_accesses(content):
        if (key, line) in seen or key.split(".")[0] in STATE_BUILTINS:
            continue
        verdict = resolve_path(key, schema)
        if verdict[0] != "undeclared":
            continue
        seen.add((key, line))
        results.append(
            rule_obj.make_result(
                file=rel,
                line=line,
                message=(
                    f"State key '{key}' is not declared in app.json "
                    "variableDeclarations"
                ),
                fix=(
                    "Declare it under variableDeclarations, or use a known "
                    f"top-level var: {_available_top_level(schema)}"
                ),
            )
        )
    return results


@rule("callbacks")
class CallbackVariableTypeMatch(Rule):
    id = "V101"
    name = "variable-type-match"
    description = (
        "State write must match the declared schema type for the variable"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v101_python(self, file_path, content, context)


@rule("tools")
class ToolVariableTypeMatch(Rule):
    id = "V101"
    name = "variable-type-match"
    description = (
        "State write must match the declared schema type for the variable"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v101_python(self, file_path, content, context)


def _check_v101_python(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    if file_path.suffix != ".py":
        return []
    schema = _load_var_schema(context)
    if not schema:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results = []
    for kind, key, line, rhs_type in _collect_state_accesses(content):
        if kind != "write" or rhs_type is None:
            continue
        verdict = resolve_path(key, schema)
        if verdict[0] != "ok":
            continue
        declared_type = verdict[1]
        allowed = SCHEMA_TYPE_TO_PY.get(declared_type, ())
        if not allowed or rhs_type in allowed:
            continue
        results.append(
            rule_obj.make_result(
                file=rel,
                line=line,
                message=(
                    f"State key '{key}' is declared as {declared_type}, "
                    f"but write assigns {rhs_type}"
                ),
                fix=(
                    f"Assign a {' or '.join(allowed)} value, or change the "
                    f"schema type of '{key}'"
                ),
            )
        )
    return results


@rule("callbacks")
class CallbackNestedPropertyExists(Rule):
    id = "V102"
    name = "nested-property-exists"
    description = "Dotted state keys must resolve to declared OBJECT properties"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v102_python(self, file_path, content, context)


@rule("tools")
class ToolNestedPropertyExists(Rule):
    id = "V102"
    name = "nested-property-exists"
    description = "Dotted state keys must resolve to declared OBJECT properties"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v102_python(self, file_path, content, context)


def _check_v102_python(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    if file_path.suffix != ".py":
        return []
    schema = _load_var_schema(context)
    if not schema:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results = []
    seen: set[tuple[str, int]] = set()
    for _kind, key, line, _rhs in _collect_state_accesses(content):
        if "." not in key or (key, line) in seen:
            continue
        verdict = resolve_path(key, schema)
        seen.add((key, line))
        if verdict[0] == "no_property":
            parent_path = verdict[1]
            child = verdict[2]
            parent_schema = _walk_to_schema(parent_path, schema)
            results.append(
                rule_obj.make_result(
                    file=rel,
                    line=line,
                    message=(
                        f"'{parent_path}' has no property '{child}' "
                        f"(declared properties: "
                        f"{_available_properties(parent_schema)})"
                    ),
                    fix=(
                        f"Use one of the declared properties on "
                        f"'{parent_path}', or add '{child}' to its schema"
                    ),
                )
            )
        elif verdict[0] == "not_object":
            parent_path = verdict[1]
            results.append(
                rule_obj.make_result(
                    file=rel,
                    line=line,
                    message=(
                        f"'{parent_path}' is not an OBJECT — cannot access "
                        f"nested key '{key}'"
                    ),
                    fix=(
                        f"Change '{parent_path}' schema type to OBJECT, or "
                        "use a flat reference"
                    ),
                )
            )
    return results


def _walk_to_schema(path: str, schema: dict) -> dict:
    parts = path.split(".")
    current = schema.get(parts[0]) or {}
    for child in parts[1:]:
        props = current.get("properties") or {}
        current = props.get(child) or {}
    return current


def _stale_flat_result(
    rule_obj: Rule, rel: str, line: int, key: str, parent_var: str
) -> LintResult:
    suggested = f"{parent_var}.{key}"
    return rule_obj.make_result(
        file=rel,
        line=line,
        message=(
            f"'{key}' is no longer a top-level variable — it lives nested "
            f"under '{parent_var}' as '{suggested}'"
        ),
        fix=f"Replace '{key}' with '{suggested}'",
    )


@rule("callbacks")
class CallbackStaleFlatVar(Rule):
    id = "V103"
    name = "no-stale-flat-var-regression"
    description = (
        "Top-level state key matches a nested property — likely a stale "
        "flat-variable reference"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v103_python(self, file_path, content, context)


@rule("tools")
class ToolStaleFlatVar(Rule):
    id = "V103"
    name = "no-stale-flat-var-regression"
    description = (
        "Top-level state key matches a nested property — likely a stale "
        "flat-variable reference"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_v103_python(self, file_path, content, context)


def _check_v103_python(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    if file_path.suffix != ".py":
        return []
    schema = _load_var_schema(context)
    if not schema:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results = []
    seen: set[tuple[str, int]] = set()
    for _kind, key, line, _rhs in _collect_state_accesses(content):
        if (key, line) in seen or key.split(".")[0] in STATE_BUILTINS:
            continue
        verdict = resolve_path(key, schema)
        if verdict[0] != "stale_flat":
            continue
        seen.add((key, line))
        results.append(_stale_flat_result(rule_obj, rel, line, key, verdict[1]))
    return results


@rule("instructions")
class InstructionStaleFlatVar(Rule):
    id = "V103"
    name = "no-stale-flat-var-regression"
    description = (
        "Instruction template reference matches a nested property — likely "
        "a stale flat-variable reference"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        schema = _load_var_schema(context)
        if not schema:
            return []
        rel = str(file_path.relative_to(context.project_root))
        results = []
        for ref, line in _collect_template_refs(content):
            if "." in ref:
                continue
            verdict = resolve_path(ref, schema)
            if verdict[0] != "stale_flat":
                continue
            results.append(_stale_flat_result(self, rel, line, ref, verdict[1]))
        return results


@rule("instructions")
class InstructionVariableRef(Rule):
    id = "V104"
    name = "instruction-variable-ref"
    description = (
        "Instruction {var} references must resolve to a declared variable"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        schema = _load_var_schema(context)
        if not schema:
            return []
        rel = str(file_path.relative_to(context.project_root))
        results = []
        for ref, line in _collect_template_refs(content):
            verdict = resolve_path(ref, schema)
            tag = verdict[0]
            if tag in ("ok", "ok_object"):
                continue
            if tag == "undeclared" and _agent_callback_substitutes_ref(
                file_path, ref
            ):
                continue
            if tag in ("undeclared", "stale_flat"):
                # Both surface as "not a declared top-level var".
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Template ref '{{{ref}}}' is not a declared "
                            "variable — it will silently resolve to empty "
                            "string at runtime"
                        ),
                        fix=(
                            "Use a declared top-level var: "
                            f"{_available_top_level(schema)}"
                        ),
                    )
                )
            elif tag == "no_property":
                parent_path = verdict[1]
                child = verdict[2]
                parent_schema = _walk_to_schema(parent_path, schema)
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Template ref '{{{ref}}}' — '{parent_path}' "
                            f"has no property '{child}' (declared: "
                            f"{_available_properties(parent_schema)})"
                        ),
                        fix=(
                            "Use a declared property, or add it to the "
                            f"'{parent_path}' schema"
                        ),
                    )
                )
            elif tag == "not_object":
                parent_path = verdict[1]
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Template ref '{{{ref}}}' — '{parent_path}' "
                            "is not an OBJECT; cannot access nested keys"
                        ),
                        fix=(
                            f"Change '{parent_path}' schema type to OBJECT, "
                            "or use a flat reference"
                        ),
                    )
                )
        return results
