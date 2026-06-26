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

# Template ref pattern — matches {var} and {var.child}. Excludes @TOOL/@AGENT
# directives (those have a colon: {@TOOL: name}) and double-brace forms.
TEMPLATE_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][\w.]*)\}(?!\})")

# Eval YAML keys that contain nested var assignments shaped like
# ``{var_name: {nested_key: value, ...}, ...}``.
EVAL_VAR_KEYS = ("variables", "session_parameters", "common_session_parameters")


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
        if d.is_dir() and not d.name.startswith("."):
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
    decls = cfg.get("variableDeclarations") or []
    schema: dict[str, dict] = {}
    for d in decls:
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
        props = current.get("properties") or {}
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

    def __init__(self):
        self.accesses: list[tuple[str, str, int, str | None]] = []
        self._skip_subscripts: set[int] = set()

    @staticmethod
    def _is_state_target(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id == "state":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "state":
            return True
        return False

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

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if self._is_state_target(node.func.value):
                if method == "get" and node.args:
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
                elif method == "update" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Dict):
                        for k_node, v_node in zip(
                            arg.keys, arg.values, strict=False
                        ):
                            key = self._const_str(k_node)
                            if key is not None:
                                rhs_type = self._literal_type_name(v_node)
                                self.accesses.append(
                                    ("write", key, node.lineno, rhs_type)
                                )
        self.generic_visit(node)


def _collect_state_accesses(
    content: str,
) -> list[tuple[str, str, int, str | None]]:
    try:
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


def _walk_eval_for_var_keys(node, content: str, out: list) -> None:
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
    for m in TEMPLATE_RE.finditer(stripped):
        ref = m.group(1)
        if ref in INSTRUCTION_BUILTINS:
            continue
        line = stripped[: m.start()].count("\n") + 1
        refs.append((ref, line))
    return refs


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
        for path, line in _collect_eval_var_refs(content):
            verdict = resolve_path(path, schema)
            if verdict[0] == "undeclared":
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
        if (key, line) in seen:
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
        if (key, line) in seen:
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
