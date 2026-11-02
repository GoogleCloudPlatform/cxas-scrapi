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

"""Typed Python stub (.pyi / .py) generator for CXAS applications.

Inspects an exported CXAS application directory (`app.json` / `app.yaml`,
`environment.json`, `tools/`, and `toolsets/`) and emits typed Python
definitions:
- `AppState` (`TypedDict` for session state / variables)
- Per-tool argument `TypedDict`s (`<PascalToolName>Args`)
- `TypedTools` (`Protocol` enforcing the CES Xbox v2 single positional dict
  contract)
- `TypedCallbackContext` and `TypedToolContext`
"""

from __future__ import annotations

import ast
import json
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Built-in session state keys always available at runtime.
BUILTIN_STATE_VARS: dict[str, str] = {
    "current_date": "str",
}

# Context parameter names skipped when extracting tool input arguments.
_CONTEXT_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "self",
        "cls",
        "context",
        "tool_context",
        "callback_context",
    }
)

# Reserved top-level class names in generated stub files.
_RESERVED_STUB_NAMES: frozenset[str] = frozenset(
    {
        "Any",
        "NotRequired",
        "Protocol",
        "Required",
        "TypedDict",
        "AppState",
        "TypedTools",
        "TypedCallbackContext",
        "TypedToolContext",
    }
)


@dataclass
class StubGenerationResult:
    """Result returned by `generate_stubs`."""

    content: str
    output_path: Path
    variables_count: int
    tools_count: int
    up_to_date: bool


@dataclass
class _TypedDictSpec:
    """Internal representation of a generated TypedDict definition."""

    name: str
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    docstring: str = ""
    use_not_required: bool = False


@dataclass
class _ToolSpec:
    """Internal representation of a discovered CXAS tool."""

    name: str
    method_name: str
    args_type_name: str
    params: list[tuple[str, str, bool]] = field(default_factory=list)
    description: str = ""


def _is_valid_identifier(name: str) -> bool:
    """Return True if `name` is a valid non-keyword Python identifier."""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


def _sanitize_identifier(name: str, fallback: str = "field") -> str:
    """Convert arbitrary string `name` into a valid Python identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def _sanitize_docstring(text: str, max_len: int = 68) -> str:
    """Escape a docstring so it is single-line, syntax-safe, and concise."""
    single_line = re.sub(r"\s+", " ", text).strip()
    escaped = single_line.replace("\\", "\\\\").replace('"', '\\"')
    if len(escaped) > max_len:
        escaped = escaped[: max_len - 3].rstrip("\\") + "..."
    return escaped


def _to_pascal_case(name: str, fallback: str = "Item") -> str:
    """Convert `name` (snake_case, kebab-case, or camelCase) to PascalCase."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name) if p]
    if not parts:
        return fallback
    camel_chunks: list[str] = []
    for part in parts:
        if part.islower() or part.isupper():
            camel_chunks.append(part.capitalize())
        else:
            camel_chunks.append(part[0].upper() + part[1:])
    result = "".join(camel_chunks)
    if result[0].isdigit():
        result = f"{fallback}{result}"
    if keyword.iskeyword(result):
        result = f"{result}{fallback}"
    return result


def _resolve_app_root(app_dir: Path) -> Path:
    """Locate the app root directory containing app configs or resources."""
    if not app_dir.exists() or not app_dir.is_dir():
        raise FileNotFoundError(
            f"Application directory not found or not a directory: {app_dir}"
        )
    markers = ("app.json", "app.yaml", "environment.json", "tools", "toolsets")
    if any((app_dir / m).exists() for m in markers):
        return app_dir
    for child in sorted(app_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("."):  # noqa: SIM102
            if (child / "app.json").exists() or (child / "app.yaml").exists():
                return child
    return app_dir


def _load_structured_file(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML file as a dictionary, returning `{}` on failure."""
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            loaded = yaml.safe_load(text)
        else:
            loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, yaml.YAMLError, OSError):
        return {}


def _extract_var_declarations_from_mapping(
    data: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Extract `(var_name, schema_dict)` pairs from an app or env config."""
    results: list[tuple[str, dict[str, Any]]] = []
    containers: list[dict[str, Any]] = [data]
    app_sub = data.get("app")
    if isinstance(app_sub, dict):
        containers.append(app_sub)

    decl_keys = (
        "variableDeclarations",
        "variable_declarations",
        "predefinedVariableDeclarations",
        "predefined_variable_declarations",
    )
    for container in containers:
        for key in decl_keys:
            raw_decls = container.get(key)
            if isinstance(raw_decls, list):
                for item in raw_decls:
                    if not isinstance(item, dict):
                        continue
                    var_name = item.get("name")
                    if not isinstance(var_name, str) or not var_name.strip():
                        continue
                    schema = item.get("schema")
                    if not isinstance(schema, dict):
                        schema = {
                            k: v
                            for k, v in item.items()
                            if k in ("type", "properties", "items", "required")
                        }
                    results.append((var_name.strip(), schema))
            elif isinstance(raw_decls, dict):
                for var_name, val in raw_decls.items():
                    if not isinstance(var_name, str) or not var_name.strip():
                        continue
                    if isinstance(val, dict):
                        schema = (
                            val["schema"]
                            if isinstance(val.get("schema"), dict)
                            else val
                        )
                    else:
                        schema = {}
                    results.append((var_name.strip(), schema))
    return results


def _collect_variable_schemas(app_root: Path) -> dict[str, dict[str, Any]]:
    """Collect declared variable schemas from app and environment configs."""
    var_schemas: dict[str, dict[str, Any]] = {}

    for cfg_name in (
        "app.json",
        "app.yaml",
        "app.yml",
        "environment.json",
        "environment.yaml",
        "environment.yml",
    ):
        cfg_path = app_root / cfg_name
        if not cfg_path.is_file():
            continue
        cfg_data = _load_structured_file(cfg_path)
        for var_name, schema in _extract_var_declarations_from_mapping(
            cfg_data
        ):
            if var_name not in var_schemas or (
                not var_schemas[var_name] and schema
            ):
                var_schemas[var_name] = schema

    return var_schemas


def _allocate_unique_type_name(
    desired: str,
    used_names: set[str],
) -> str:
    """Ensure a generated TypedDict class name does not collide."""
    candidate = desired
    idx = 2
    while candidate in used_names or candidate in _RESERVED_STUB_NAMES:
        candidate = f"{desired}{idx}"
        idx += 1
    used_names.add(candidate)
    return candidate


def _allocate_unique_method_name(
    desired: str,
    used_method_names: set[str],
) -> str:
    """Ensure a generated Protocol method name on `TypedTools` is unique."""
    candidate = desired
    idx = 2
    while candidate in used_method_names:
        candidate = f"{desired}_{idx}"
        idx += 1
    used_method_names.add(candidate)
    return candidate


def _schema_to_py_type(
    schema: dict[str, Any] | None,
    type_prefix: str,
    nested_defs: list[_TypedDictSpec],
    used_names: set[str],
    suffix: str = "State",
) -> str:
    """Map a CXAS / OpenAPI schema dictionary to a Python type annotation."""
    if not isinstance(schema, dict) or not schema:
        return "Any"

    raw_type = str(schema.get("type") or "").strip().upper()
    props = schema.get("properties")
    if not raw_type and isinstance(props, dict) and props:
        raw_type = "OBJECT"

    if raw_type in ("STRING", "STR"):
        return "str"
    if raw_type in ("INTEGER", "INT", "INT32", "INT64"):
        return "int"
    if raw_type in ("NUMBER", "FLOAT", "DOUBLE"):
        return "float"
    if raw_type in ("BOOLEAN", "BOOL"):
        return "bool"
    if raw_type in ("ARRAY", "LIST"):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict) and items_schema:
            item_type = _schema_to_py_type(
                items_schema,
                f"{type_prefix}Item",
                nested_defs,
                used_names,
                suffix=suffix,
            )
            return f"list[{item_type}]"
        return "list[Any]"
    if raw_type in ("OBJECT", "DICT", "MAP"):
        if isinstance(props, dict) and props:
            td_name = _allocate_unique_type_name(
                f"{_to_pascal_case(type_prefix)}{suffix}",
                used_names,
            )
            req_set = {
                str(r)
                for r in (schema.get("required") or [])
                if isinstance(r, str)
            }
            td_fields: list[tuple[str, str, bool]] = []
            for prop_name, prop_schema in props.items():
                prop_prefix = f"{type_prefix}_{prop_name}"
                prop_py_type = _schema_to_py_type(
                    prop_schema if isinstance(prop_schema, dict) else {},
                    prop_prefix,
                    nested_defs,
                    used_names,
                    suffix=suffix,
                )
                td_fields.append(
                    (prop_name, prop_py_type, prop_name in req_set)
                )
            nested_defs.append(
                _TypedDictSpec(
                    name=td_name,
                    fields=td_fields,
                    docstring=f"Nested object type for `{type_prefix}`.",
                    use_not_required=False,
                )
            )
            return td_name
        return "dict[str, Any]"
    return "Any"


def _normalize_ast_annotation(node: ast.expr | None) -> str:
    """Convert an AST type annotation expression into a stub-safe type."""
    if node is None:
        return "Any"
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "None"
        if isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
                return _normalize_ast_annotation(parsed.body)
            except SyntaxError:
                return "Any"
        return "Any"
    if isinstance(node, ast.Name):
        mapping = {
            "str": "str",
            "int": "int",
            "float": "float",
            "bool": "bool",
            "bytes": "bytes",
            "list": "list[Any]",
            "List": "list[Any]",
            "dict": "dict[str, Any]",
            "Dict": "dict[str, Any]",
            "set": "set[Any]",
            "Set": "set[Any]",
            "tuple": "tuple[Any, ...]",
            "Tuple": "tuple[Any, ...]",
            "Any": "Any",
            "None": "None",
            "object": "Any",
            "Sequence": "list[Any]",
            "Mapping": "dict[str, Any]",
            "Iterable": "list[Any]",
        }
        return mapping.get(node.id, "Any")
    if isinstance(node, ast.Attribute):
        return _normalize_ast_annotation(ast.Name(id=node.attr))
    if isinstance(node, ast.Subscript):
        base_name = ""
        if isinstance(node.value, ast.Name):
            base_name = node.value.id
        elif isinstance(node.value, ast.Attribute):
            base_name = node.value.attr

        if base_name in ("Optional",):
            inner = _normalize_ast_annotation(node.slice)
            return f"{inner} | None"
        if base_name in ("Union",):
            if isinstance(node.slice, ast.Tuple):
                parts = [_normalize_ast_annotation(e) for e in node.slice.elts]
                return " | ".join(parts) if parts else "Any"
            return _normalize_ast_annotation(node.slice)
        if base_name in ("list", "List", "Sequence", "Iterable"):
            inner = _normalize_ast_annotation(node.slice)
            return f"list[{inner}]"
        if base_name in ("set", "Set"):
            inner = _normalize_ast_annotation(node.slice)
            return f"set[{inner}]"
        if base_name in ("dict", "Dict", "Mapping"):
            if (
                isinstance(node.slice, ast.Tuple) and len(node.slice.elts) == 2  # noqa: PLR2004
            ):
                k_type = _normalize_ast_annotation(node.slice.elts[0])
                v_type = _normalize_ast_annotation(node.slice.elts[1])
                return f"dict[{k_type}, {v_type}]"
            return "dict[str, Any]"
        if base_name in ("tuple", "Tuple"):
            if isinstance(node.slice, ast.Tuple):
                parts = [_normalize_ast_annotation(e) for e in node.slice.elts]
                return f"tuple[{', '.join(parts)}]" if parts else "tuple[()]"
            inner = _normalize_ast_annotation(node.slice)
            return f"tuple[{inner}, ...]"
        return "Any"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _normalize_ast_annotation(node.left)
        right = _normalize_ast_annotation(node.right)
        return f"{left} | {right}"
    return "Any"


def _infer_type_from_default(default_node: ast.expr | None) -> str:
    """Infer a basic Python type from an AST default value when unannotated."""
    if default_node is None:
        return "Any"
    if isinstance(default_node, ast.UnaryOp) and isinstance(
        default_node.op, (ast.USub, ast.UAdd)
    ):
        operand = default_node.operand
        if isinstance(operand, ast.Constant) and not isinstance(
            operand.value, bool
        ):
            if isinstance(operand.value, int):
                return "int"
            if isinstance(operand.value, float):
                return "float"
        return "Any"
    if isinstance(default_node, ast.Constant):
        val = default_node.value
        if isinstance(val, bool):
            return "bool"
        if isinstance(val, int):
            return "int"
        if isinstance(val, float):
            return "float"
        if isinstance(val, str):
            return "str"
    if isinstance(default_node, ast.List):
        return "list[Any]"
    if isinstance(default_node, ast.Dict):
        return "dict[str, Any]"
    return "Any"


def _find_tool_ast_function(
    content: str, expected_name: str | None = None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the entry tool AST function node, skipping private helpers."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    fns = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not fns:
        return None
    if expected_name:
        for fn in fns:
            if fn.name == expected_name:
                return fn
    for fn in fns:
        if not fn.name.startswith("_"):
            return fn
    return fns[0]


def _extract_params_from_python_code(
    code_path: Path, expected_fn_name: str | None = None
) -> tuple[str | None, list[tuple[str, str, bool]], str]:
    """Extract `(fn_name, [(param, py_type, required)], docstring)` via AST."""
    if not code_path.is_file():
        return None, [], ""
    try:
        content = code_path.read_text(encoding="utf-8")
    except OSError:
        return None, [], ""

    fn_node = _find_tool_ast_function(content, expected_fn_name)
    if fn_node is None:
        return None, [], ""

    docstring = ast.get_docstring(fn_node) or ""
    pos_args = list(fn_node.args.posonlyargs) + list(fn_node.args.args)
    defaults = list(fn_node.args.defaults)
    num_no_default = len(pos_args) - len(defaults)

    params: list[tuple[str, str, bool]] = []
    for idx, arg in enumerate(pos_args):
        if arg.arg in _CONTEXT_PARAM_NAMES:
            continue
        has_default = idx >= num_no_default
        default_node = defaults[idx - num_no_default] if has_default else None
        if arg.annotation is not None:
            py_type = _normalize_ast_annotation(arg.annotation)
        else:
            py_type = _infer_type_from_default(default_node)
        params.append((arg.arg, py_type, not has_default))

    for arg, kw_default in zip(
        fn_node.args.kwonlyargs, fn_node.args.kw_defaults, strict=False
    ):
        if arg.arg in _CONTEXT_PARAM_NAMES:
            continue
        has_default = kw_default is not None
        if arg.annotation is not None:
            py_type = _normalize_ast_annotation(arg.annotation)
        else:
            py_type = _infer_type_from_default(kw_default)
        params.append((arg.arg, py_type, not has_default))

    return fn_node.name, params, docstring.splitlines()[0] if docstring else ""


def _extract_schema_dict_from_tool_config(
    tool_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Locate the input parameter schema dictionary inside a tool config."""
    schema_keys = ("parameters", "inputSchema", "input_schema", "schema")
    for key in schema_keys:
        candidate = tool_cfg.get(key)
        if isinstance(candidate, dict) and (
            "properties" in candidate or "type" in candidate
        ):
            return candidate

    nested_containers = (
        "pythonFunction",
        "python_function",
        "clientFunction",
        "client_function",
        "functionDeclaration",
        "function_declaration",
        "openApiTool",
        "open_api_tool",
        "googleSearchTool",
        "dataStoreTool",
        "remoteAgentTool",
    )
    for container_key in nested_containers:
        sub = tool_cfg.get(container_key)
        if isinstance(sub, dict):
            for key in schema_keys:
                candidate = sub.get(key)
                if isinstance(candidate, dict) and (
                    "properties" in candidate or "type" in candidate
                ):
                    return candidate
    return {}


def _extract_params_from_schema_dict(
    schema_dict: dict[str, Any],
    tool_name: str,
    nested_defs: list[_TypedDictSpec],
    used_names: set[str],
) -> list[tuple[str, str, bool]]:
    """Convert a JSON/OpenAPI object schema into parameter tuples."""
    if not isinstance(schema_dict, dict):
        return []
    props = schema_dict.get("properties")
    if not isinstance(props, dict) or not props:
        return []

    req_list = schema_dict.get("required")
    req_set = (
        {str(r) for r in req_list if isinstance(r, str)}
        if isinstance(req_list, list)
        else set()
    )
    params: list[tuple[str, str, bool]] = []
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_name, str) or not prop_name:
            continue
        py_type = _schema_to_py_type(
            prop_schema if isinstance(prop_schema, dict) else {},
            f"{tool_name}_{prop_name}",
            nested_defs,
            used_names,
            suffix="Param",
        )
        params.append((prop_name, py_type, prop_name in req_set))
    return params


def _merge_tool_params(
    ast_params: list[tuple[str, str, bool]],
    schema_params: list[tuple[str, str, bool]],
) -> list[tuple[str, str, bool]]:
    """Combine AST parameters with JSON-schema parameters."""
    if not ast_params:
        return schema_params
    if not schema_params:
        return ast_params

    schema_map = {name: (ptype, req) for name, ptype, req in schema_params}
    merged: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for name, ast_type, ast_req in ast_params:
        seen.add(name)
        if name in schema_map:
            sch_type, sch_req = schema_map[name]
            chosen_type = (
                sch_type
                if ast_type in ("Any", "dict[str, Any]")
                and sch_type not in ("Any", "dict[str, Any]")
                else ast_type
            )
            merged.append((name, chosen_type, ast_req and sch_req))
        else:
            merged.append((name, ast_type, ast_req))

    for name, sch_type, sch_req in schema_params:
        if name not in seen:
            merged.append((name, sch_type, sch_req))
    return merged


def _discover_tools_in_dir(
    app_root: Path,
    nested_defs: list[_TypedDictSpec],
    used_names: set[str],
    used_method_names: set[str],
) -> list[_ToolSpec]:
    """Discover all tools under `tools/` and `toolsets/`."""
    discovered: list[_ToolSpec] = []
    seen_tool_names: set[str] = set()

    tools_dir = app_root / "tools"
    if tools_dir.is_dir():
        for tool_dir in sorted(tools_dir.iterdir()):
            if not tool_dir.is_dir() or tool_dir.name.startswith("."):
                continue

            cfg_path = tool_dir / f"{tool_dir.name}.json"
            if not cfg_path.is_file():
                cfg_path = tool_dir / f"{tool_dir.name}.yaml"
            if not cfg_path.is_file():
                candidates = sorted(tool_dir.glob("*.json")) + sorted(
                    tool_dir.glob("*.yaml")
                )
                if candidates:
                    cfg_path = candidates[0]

            tool_cfg = (
                _load_structured_file(cfg_path) if cfg_path.is_file() else {}
            )
            py_fn_cfg = (
                tool_cfg.get("pythonFunction")
                or tool_cfg.get("python_function")
                or {}
            )
            client_fn_cfg = (
                tool_cfg.get("clientFunction")
                or tool_cfg.get("client_function")
                or {}
            )

            code_path = tool_dir / "python_function" / "python_code.py"
            if not code_path.is_file() and isinstance(py_fn_cfg, dict):
                rel_code = py_fn_cfg.get("pythonCode") or py_fn_cfg.get(
                    "python_code"
                )
                if isinstance(rel_code, str) and rel_code:
                    alt_path = app_root / rel_code
                    if alt_path.is_file():
                        code_path = alt_path

            ast_fn_name, ast_params, ast_doc = _extract_params_from_python_code(
                code_path, expected_fn_name=tool_dir.name
            )

            raw_name = (
                (py_fn_cfg.get("name") if isinstance(py_fn_cfg, dict) else None)
                or (
                    client_fn_cfg.get("name")
                    if isinstance(client_fn_cfg, dict)
                    else None
                )
                or tool_cfg.get("displayName")
                or tool_cfg.get("display_name")
                or ast_fn_name
                or tool_dir.name
            )
            tool_name = str(raw_name).strip() or tool_dir.name
            if tool_name in seen_tool_names:
                continue
            seen_tool_names.add(tool_name)

            schema_dict = _extract_schema_dict_from_tool_config(tool_cfg)
            schema_params = _extract_params_from_schema_dict(
                schema_dict, tool_name, nested_defs, used_names
            )
            params = _merge_tool_params(ast_params, schema_params)

            desc = (
                tool_cfg.get("description")
                or (
                    py_fn_cfg.get("description")
                    if isinstance(py_fn_cfg, dict)
                    else None
                )
                or (
                    client_fn_cfg.get("description")
                    if isinstance(client_fn_cfg, dict)
                    else None
                )
                or ast_doc
                or ""
            )
            args_type_name = _allocate_unique_type_name(
                f"{_to_pascal_case(tool_name, fallback='Tool')}Args",
                used_names,
            )
            method_name = _allocate_unique_method_name(
                _sanitize_identifier(tool_name, fallback="call_tool"),
                used_method_names,
            )
            discovered.append(
                _ToolSpec(
                    name=tool_name,
                    method_name=method_name,
                    args_type_name=args_type_name,
                    params=params,
                    description=str(desc).splitlines()[0] if desc else "",
                )
            )

    toolsets_dir = app_root / "toolsets"
    if toolsets_dir.is_dir():
        for ts_dir in sorted(toolsets_dir.iterdir()):
            if not ts_dir.is_dir() or ts_dir.name.startswith("."):
                continue
            discovered.extend(
                _discover_toolset_specs(
                    app_root,
                    ts_dir,
                    seen_tool_names,
                    nested_defs,
                    used_names,
                    used_method_names,
                )
            )

    return discovered


def _discover_toolset_specs(
    app_root: Path,
    ts_dir: Path,
    seen_tool_names: set[str],
    nested_defs: list[_TypedDictSpec],
    used_names: set[str],
    used_method_names: set[str],
) -> list[_ToolSpec]:
    """Extract tool specifications from a `toolsets/<ts_dir>/` directory."""
    specs: list[_ToolSpec] = []
    ts_name = ts_dir.name
    cfg_path = ts_dir / f"{ts_name}.json"
    if not cfg_path.is_file():
        cfg_path = ts_dir / f"{ts_name}.yaml"
    if not cfg_path.is_file():
        candidates = sorted(ts_dir.glob("*.json")) + sorted(
            ts_dir.glob("*.yaml")
        )
        if candidates:
            cfg_path = candidates[0]

    ts_cfg = _load_structured_file(cfg_path) if cfg_path.is_file() else {}

    # 1. OpenAPI toolsets
    oa_cfg = ts_cfg.get("openApiToolset") or ts_cfg.get("open_api_toolset")
    if isinstance(oa_cfg, dict):
        schema_data = _load_openapi_schema(app_root, ts_dir, oa_cfg)
        specs.extend(
            _extract_openapi_tool_specs(
                ts_name,
                schema_data,
                seen_tool_names,
                nested_defs,
                used_names,
                used_method_names,
            )
        )

    # 2. Explicit tool declarations in toolset config
    raw_tools = (
        ts_cfg.get("tools")
        or ts_cfg.get("toolDeclarations")
        or ts_cfg.get("functionDeclarations")
        or []
    )
    if isinstance(raw_tools, list):
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            item_name = str(
                item.get("name") or item.get("displayName") or ""
            ).strip()
            if not item_name:
                continue
            full_name = (
                item_name
                if item_name.startswith(f"{ts_name}_")
                else f"{ts_name}_{item_name}"
            )
            if full_name in seen_tool_names:
                continue
            seen_tool_names.add(full_name)
            schema_dict = _extract_schema_dict_from_tool_config(item)
            params = _extract_params_from_schema_dict(
                schema_dict, full_name, nested_defs, used_names
            )
            args_type_name = _allocate_unique_type_name(
                f"{_to_pascal_case(full_name, fallback='Tool')}Args",
                used_names,
            )
            raw_desc = str(item.get("description") or "")
            specs.append(
                _ToolSpec(
                    name=full_name,
                    method_name=_allocate_unique_method_name(
                        _sanitize_identifier(full_name, fallback="call_tool"),
                        used_method_names,
                    ),
                    args_type_name=args_type_name,
                    params=params,
                    description=raw_desc.splitlines()[0] if raw_desc else "",
                )
            )

    return specs


def _load_openapi_schema(
    app_root: Path, ts_dir: Path, oa_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Load an OpenAPI schema dict from a file reference or inline string."""
    default_yaml = ts_dir / "open_api_toolset" / "open_api_schema.yaml"
    default_json = ts_dir / "open_api_toolset" / "open_api_schema.json"
    raw_ref = oa_cfg.get("openApiSchema") or oa_cfg.get("open_api_schema")

    if isinstance(raw_ref, str) and raw_ref.strip():
        ref_str = raw_ref.strip()
        for candidate in (ts_dir / ref_str, app_root / ref_str):
            if candidate.is_file():
                return _load_structured_file(candidate)
        if "\n" in ref_str or ref_str.startswith("{"):
            try:
                loaded = yaml.safe_load(ref_str)
                if isinstance(loaded, dict):
                    return loaded
            except yaml.YAMLError:
                pass

    if default_yaml.is_file():
        return _load_structured_file(default_yaml)
    if default_json.is_file():
        return _load_structured_file(default_json)
    return {}


def _extract_openapi_tool_specs(
    ts_name: str,
    schema_data: dict[str, Any],
    seen_tool_names: set[str],
    nested_defs: list[_TypedDictSpec],
    used_names: set[str],
    used_method_names: set[str],
) -> list[_ToolSpec]:
    """Extract `_ToolSpec` items from an OpenAPI `paths` dictionary."""
    specs: list[_ToolSpec] = []
    paths = schema_data.get("paths")
    if not isinstance(paths, dict):
        return specs

    http_methods = {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "options",
        "head",
        "trace",
    }
    for path_str, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        path_params = (
            methods.get("parameters")
            if isinstance(methods.get("parameters"), list)
            else []
        )
        for method_name, details in methods.items():
            if str(method_name).lower() not in http_methods or not isinstance(
                details, dict
            ):
                continue
            op_id = details.get("operationId")
            if not op_id:
                sanitized = re.sub(
                    r"[^A-Za-z0-9_]", "_", str(path_str).strip("/")
                )
                op_id = f"{str(method_name).upper()}_{sanitized}"
            full_name = f"{ts_name}_{op_id}"
            if full_name in seen_tool_names:
                continue
            seen_tool_names.add(full_name)

            params: list[tuple[str, str, bool]] = []
            seen_params: set[str] = set()
            op_params = (
                details.get("parameters")
                if isinstance(details.get("parameters"), list)
                else []
            )
            for p_item in [*path_params, *op_params]:
                if not isinstance(p_item, dict):
                    continue
                p_name = p_item.get("name")
                if not isinstance(p_name, str) or p_name in seen_params:
                    continue
                seen_params.add(p_name)
                p_schema = (
                    p_item.get("schema")
                    if isinstance(p_item.get("schema"), dict)
                    else p_item
                )
                p_type = _schema_to_py_type(
                    p_schema,
                    f"{full_name}_{p_name}",
                    nested_defs,
                    used_names,
                    suffix="Param",
                )
                p_req = bool(p_item.get("required", False))
                params.append((p_name, p_type, p_req))

            req_body = details.get("requestBody")
            if isinstance(req_body, dict):
                content_map = req_body.get("content")
                if isinstance(content_map, dict):
                    json_media = content_map.get("application/json")
                    if isinstance(json_media, dict) and isinstance(
                        json_media.get("schema"), dict
                    ):
                        body_params = _extract_params_from_schema_dict(
                            json_media["schema"],
                            full_name,
                            nested_defs,
                            used_names,
                        )
                        for b_name, b_type, b_req in body_params:
                            if b_name not in seen_params:
                                seen_params.add(b_name)
                                params.append((b_name, b_type, b_req))

            args_type_name = _allocate_unique_type_name(
                f"{_to_pascal_case(full_name, fallback='Tool')}Args",
                used_names,
            )
            desc = str(
                details.get("summary") or details.get("description") or ""
            )
            specs.append(
                _ToolSpec(
                    name=full_name,
                    method_name=_allocate_unique_method_name(
                        _sanitize_identifier(full_name, fallback="call_tool"),
                        used_method_names,
                    ),
                    args_type_name=args_type_name,
                    params=params,
                    description=desc.splitlines()[0] if desc else "",
                )
            )
    return specs


def _render_typed_dict(spec: _TypedDictSpec) -> list[str]:
    """Render a `_TypedDictSpec` as either class-based or functional syntax."""
    all_valid_identifiers = all(
        _is_valid_identifier(fname) for fname, _, _ in spec.fields
    )

    lines: list[str] = []
    if all_valid_identifiers:
        lines.append(f"class {spec.name}(TypedDict, total=False):")
        if spec.docstring:
            safe_doc = _sanitize_docstring(spec.docstring)
            lines.append(f'    """{safe_doc}"""')
            if spec.fields:
                lines.append("")
        if not spec.fields:
            lines.append("    pass")
        else:
            for fname, ftype, req in spec.fields:
                rendered_type = (
                    f"NotRequired[{ftype}]"
                    if spec.use_not_required and not req
                    else ftype
                )
                lines.append(f"    {fname}: {rendered_type}")
    else:
        lines.append(f"{spec.name} = TypedDict(")
        lines.append(f'    "{spec.name}",')
        lines.append("    {")
        for fname, ftype, req in spec.fields:
            rendered_type = (
                f"NotRequired[{ftype}]"
                if spec.use_not_required and not req
                else ftype
            )
            escaped_key = json.dumps(fname)
            lines.append(f"        {escaped_key}: {rendered_type},")
        lines.append("    },")
        lines.append("    total=False,")
        lines.append(")")
    return lines


def _render_stub_content(
    state_spec: _TypedDictSpec,
    nested_defs: list[_TypedDictSpec],
    tool_specs: list[_ToolSpec],
) -> str:
    """Assemble the complete `.pyi` / `.py` stub file content."""
    blocks: list[list[str]] = []

    for nested in nested_defs:
        blocks.append(_render_typed_dict(nested))

    blocks.append(_render_typed_dict(state_spec))

    for tool in tool_specs:
        tool_td = _TypedDictSpec(
            name=tool.args_type_name,
            fields=tool.params,
            docstring=(
                f"Arguments for tool `{tool.name}`."
                if not tool.description
                else f"Arguments for `{tool.name}`: {tool.description}"
            ),
            use_not_required=True,
        )
        blocks.append(_render_typed_dict(tool_td))

    tools_block: list[str] = [
        "class TypedTools(Protocol):",
        '    """CES Xbox v2 tool dispatcher (single positional dict arg)."""',
    ]
    if not tool_specs:
        tools_block.append("    pass")
    else:
        tools_block.append("")
        for idx, tool in enumerate(tool_specs):
            if idx > 0:
                tools_block.append("")
            sig_single = (
                f"    def {tool.method_name}("
                f"self, args: {tool.args_type_name}"
                " | dict[str, Any] | None = None, /"
                ") -> dict[str, Any]: ..."
            )
            if len(sig_single) <= 80:  # noqa: PLR2004
                tools_block.append(sig_single)
            else:
                tools_block.extend(
                    [
                        f"    def {tool.method_name}(",
                        "        self,",
                        (
                            f"        args: {tool.args_type_name}"
                            " | dict[str, Any] | None = None,"
                        ),
                        "        /,",
                        "    ) -> dict[str, Any]: ...",
                    ]
                )
    blocks.append(tools_block)

    blocks.append(
        [
            "class TypedCallbackContext:",
            '    """Typed callback context with AppState and TypedTools."""',
            "",
            "    state: AppState",
            "    variables: AppState",
            "    tools: TypedTools",
            "    invocation_id: str",
            "    agent_name: str",
            "    session_id: str",
            "    language_code: str",
            "    project_id: str",
            "    project_number: int",
            "    location: str",
            "    app_id: str",
            "    turn_index: int",
            "",
            "    def get_variable("
            "self, key: str, default: Any = None) -> Any: ...",
            "    def set_variable(self, key: str, value: Any) -> None: ...",
            "    def remove_variable(self, key: str) -> None: ...",
        ]
    )

    blocks.append(
        [
            "class TypedToolContext(TypedCallbackContext):",
            '    """Typed tool context exposing AppState and TypedTools."""',
            "",
            "    state: AppState",
            "    variables: AppState",
            "    tools: TypedTools",
            "    function_call_id: str | None",
        ]
    )

    body_text = "\n\n".join("\n".join(b) for b in blocks)
    typing_imports = ["Any"]
    if "NotRequired[" in body_text:
        typing_imports.append("NotRequired")
    typing_imports.extend(["Protocol", "TypedDict"])

    header = "\n".join(
        [
            "# Auto-generated by `cxas generate-stubs`. Do not edit manually.",
            "",
            "from __future__ import annotations",
            "",
            f"from typing import {', '.join(typing_imports)}",
        ]
    )
    return f"{header}\n\n{body_text}\n"


def generate_stubs(
    app_dir: str | Path,
    output_path: str | Path | None = None,
    check: bool = False,
    *,
    write: bool = True,
) -> StubGenerationResult:
    """Generate typed Python stubs (`cxas_types.pyi`) for a CXAS app directory.

    Args:
        app_dir: Path to the exported CXAS application directory.
        output_path: Target stub file path. Defaults to
            `<app_dir>/cxas_types.pyi`.
        check: When True, do not write files; verify whether `output_path`
            already matches the generated content.
        write: When True (and `check` is False), write the generated stub to
            `output_path`.

    Returns:
        A `StubGenerationResult` with the generated source, target path, counts,
        and sync status.
    """
    app_dir_path = Path(app_dir)
    app_root = _resolve_app_root(app_dir_path)
    target_path = (
        Path(output_path)
        if output_path is not None
        else app_dir_path / "cxas_types.pyi"
    )

    used_names: set[str] = set(_RESERVED_STUB_NAMES)
    used_method_names: set[str] = set()
    nested_defs: list[_TypedDictSpec] = []

    var_schemas = _collect_variable_schemas(app_root)
    declared_vars_count = len(var_schemas)

    state_fields: list[tuple[str, str, bool]] = []
    for var_name, schema in var_schemas.items():
        py_type = _schema_to_py_type(
            schema,
            var_name,
            nested_defs,
            used_names,
            suffix="State",
        )
        state_fields.append((var_name, py_type, False))

    for builtin_name, builtin_type in BUILTIN_STATE_VARS.items():
        if builtin_name not in var_schemas:
            state_fields.append((builtin_name, builtin_type, False))

    state_spec = _TypedDictSpec(
        name="AppState",
        fields=state_fields,
        docstring="Typed session state dictionary for the CXAS application.",
        use_not_required=False,
    )

    tool_specs = _discover_tools_in_dir(
        app_root, nested_defs, used_names, used_method_names
    )
    content = _render_stub_content(state_spec, nested_defs, tool_specs)

    if check:
        up_to_date = (
            target_path.is_file()
            and target_path.read_text(encoding="utf-8") == content
        )
    else:
        if write:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
        up_to_date = True

    return StubGenerationResult(
        content=content,
        output_path=target_path,
        variables_count=declared_vars_count,
        tools_count=len(tool_specs),
        up_to_date=up_to_date,
    )
