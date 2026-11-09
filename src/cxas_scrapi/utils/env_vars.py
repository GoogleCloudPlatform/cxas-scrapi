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

"""Environment variable resolution and validation for variableDeclarations."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_VAR_PLACEHOLDER = "$env_var"


@dataclass
class EnvVarExtraction:
    """Parsed environment variable defaults and any structural errors."""

    defaults: dict[str, Any] = field(default_factory=dict)
    declared_types: dict[str, str] = field(default_factory=dict)
    seen_names: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def _value_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def infer_schema_type(schema: dict[str, Any]) -> str | None:
    """Return normalized CES schema type string or None if unspecified."""
    if not isinstance(schema, dict):
        return None
    raw_type = schema.get("type")
    if raw_type is not None:
        norm = str(raw_type).strip().upper()
        if norm in ("", "TYPE_UNSPECIFIED", "0"):
            return None
        return norm
    if "properties" in schema:
        return "OBJECT"
    if "items" in schema:
        return "ARRAY"
    return None


def validate_value_for_schema(value: Any, schema: dict[str, Any]) -> str | None:
    """Validate a JSON/YAML default value against a CES Schema dict.

    Returns an error description if invalid, or None if valid.
    """
    schema_type = infer_schema_type(schema)
    if schema_type is None:
        return None

    if value is None:
        if schema.get("nullable") is True:
            return None
        return (
            f"expected {schema_type}, got null "
            "(set nullable: true to allow null)"
        )

    valid = False
    if schema_type == "BOOLEAN":
        valid = isinstance(value, bool)
    elif schema_type == "INTEGER":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "NUMBER":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif schema_type == "STRING":
        valid = isinstance(value, str)
    elif schema_type == "OBJECT":
        valid = isinstance(value, dict)
    elif schema_type == "ARRAY":
        valid = isinstance(value, list)
    else:
        return f"unsupported schema type '{schema.get('type')}'"

    if not valid:
        return f"expected {schema_type}, got {_value_type_name(value)}"
    return None


def parse_env_variable_declarations(env_data: Any) -> EnvVarExtraction:
    """Parse ``app.variableDeclarations`` from ``environment.json`` content."""
    result = EnvVarExtraction()
    if not isinstance(env_data, dict):
        result.errors.append("environment.json root must be a JSON object")
        return result

    if (
        "variableDeclarations" in env_data
        or "variable_declarations" in env_data
    ):
        result.errors.append(
            "Found top-level 'variableDeclarations' in environment.json; "
            "nest it under 'app.variableDeclarations'"
        )

    app_section = env_data.get("app")
    if app_section is None:
        return result
    if not isinstance(app_section, dict):
        result.errors.append(
            "Field 'app' in environment.json must be a JSON object"
        )
        return result

    has_camel = "variableDeclarations" in app_section
    has_snake = "variable_declarations" in app_section
    if not has_camel and not has_snake:
        return result

    raw_decls = (
        app_section["variableDeclarations"]
        if has_camel
        else app_section["variable_declarations"]
    )
    if not isinstance(raw_decls, list):
        result.errors.append(
            "Field 'app.variableDeclarations' in environment.json must be a "
            "list"
        )
        return result

    for idx, entry in enumerate(raw_decls):
        if not isinstance(entry, dict):
            result.errors.append(
                f"app.variableDeclarations[{idx}] in environment.json must be "
                "an object"
            )
            continue

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            result.errors.append(
                f"app.variableDeclarations[{idx}] in environment.json is "
                "missing a non-empty string 'name'"
            )
            continue

        if name in result.seen_names:
            result.errors.append(
                f"Duplicate variable '{name}' in environment.json "
                "app.variableDeclarations"
            )
            continue
        result.seen_names.add(name)

        schema = entry.get("schema")
        if not isinstance(schema, dict) or "default" not in schema:
            result.errors.append(
                f"Variable '{name}' in environment.json "
                "app.variableDeclarations must define 'schema.default'"
            )
            continue

        raw_type = schema.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            result.declared_types[name] = raw_type.strip()

        default_val = schema["default"]
        if default_val == ENV_VAR_PLACEHOLDER:
            result.errors.append(
                f"Variable '{name}' in environment.json "
                f"app.variableDeclarations has unresolved "
                f"'{ENV_VAR_PLACEHOLDER}' as schema.default"
            )
            continue

        result.defaults[name] = default_val

    return result


def extract_env_variable_defaults(
    env_data: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(defaults_by_name, errors)`` from ``environment.json`` data."""
    parsed = parse_env_variable_declarations(env_data)
    return parsed.defaults, parsed.errors


def _find_app_config_file(app_dir: Path) -> Path | None:
    for name in ("app.json", "app.yaml"):
        candidate = app_dir / name
        if candidate.is_file():
            return candidate
    return None


def _load_config_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _write_config_file(path: Path, data: dict[str, Any]) -> None:
    if path.suffix in (".yaml", ".yml"):
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _get_variable_declarations_list(
    app_data: dict[str, Any],
) -> list[Any] | None:
    if "variableDeclarations" in app_data:
        decls = app_data["variableDeclarations"]
    elif "variable_declarations" in app_data:
        decls = app_data["variable_declarations"]
    else:
        return None
    return decls if isinstance(decls, list) else None


def collect_env_var_declaration_names(app_dir: str | Path) -> set[str]:
    """Return variable names whose ``schema.default`` is ``"$env_var"``."""
    app_cfg = _find_app_config_file(Path(app_dir))
    if app_cfg is None:
        return set()
    try:
        app_data = _load_config_file(app_cfg)
    except (json.JSONDecodeError, yaml.YAMLError, OSError):
        return set()
    if not isinstance(app_data, dict):
        return set()
    decls = _get_variable_declarations_list(app_data)
    if not decls:
        return set()

    names: set[str] = set()
    for decl in decls:
        if not isinstance(decl, dict):
            continue
        name = decl.get("name")
        schema = decl.get("schema")
        if (
            isinstance(name, str)
            and name
            and isinstance(schema, dict)
            and schema.get("default") == ENV_VAR_PLACEHOLDER
        ):
            names.add(name)
    return names


def resolve_app_env_variables_in_dir(app_dir: str | Path) -> list[str]:
    """Resolve ``"$env_var"`` defaults in staged ``app.json`` / ``app.yaml``.

    Reads ``environment.json`` in ``app_dir``, validates structure and schema
    types, substitutes concrete defaults into ``app.json`` / ``app.yaml`` in
    place, and returns the resolved variable names. Raises ``ValueError`` if
    any placeholder cannot be resolved or fails type validation.
    """
    root = Path(app_dir)
    app_cfg = _find_app_config_file(root)
    env_path = root / "environment.json"

    placeholder_decls: list[dict[str, Any]] = []
    app_data: Any = None
    if app_cfg is not None:
        try:
            app_data = _load_config_file(app_cfg)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            raise ValueError(f"Invalid {app_cfg.name}: {e}") from e
        if isinstance(app_data, dict):
            decls = _get_variable_declarations_list(app_data)
            if decls:
                for decl in decls:
                    if not isinstance(decl, dict):
                        continue
                    name = decl.get("name")
                    schema = decl.get("schema")
                    if (
                        isinstance(name, str)
                        and name
                        and isinstance(schema, dict)
                        and schema.get("default") == ENV_VAR_PLACEHOLDER
                    ):
                        placeholder_decls.append(decl)

    if not placeholder_decls:
        if env_path.is_file():
            try:
                raw_env = json.loads(env_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
            if isinstance(raw_env, dict) and (
                "variableDeclarations" in raw_env
                or "variable_declarations" in raw_env
            ):
                raise ValueError(
                    "Found top-level 'variableDeclarations' in "
                    "environment.json; nest it under "
                    "'app.variableDeclarations' and set 'schema.default' to "
                    f"'{ENV_VAR_PLACEHOLDER}' in app config"
                )
        return []

    assert app_cfg is not None
    if not env_path.is_file():
        missing_list = ", ".join(d["name"] for d in placeholder_decls)
        raise ValueError(
            f"{app_cfg.name} declares '{ENV_VAR_PLACEHOLDER}' for variable(s) "
            f"[{missing_list}], but environment.json was not found "
            "(add environment.json to the app directory or pass --env-file)"
        )

    try:
        env_data = json.loads(env_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in environment.json: {e}") from e

    parsed = parse_env_variable_declarations(env_data)
    errors = list(parsed.errors)
    resolved_names: list[str] = []

    for decl in placeholder_decls:
        var_name = decl["name"]
        schema = decl["schema"]
        if var_name not in parsed.defaults:
            if var_name not in parsed.seen_names:
                errors.append(
                    f"Variable '{var_name}' has schema.default="
                    f"'{ENV_VAR_PLACEHOLDER}' in {app_cfg.name} but is missing "
                    "from environment.json under 'app.variableDeclarations'"
                )
            continue

        resolved_val = parsed.defaults[var_name]
        type_err = validate_value_for_schema(resolved_val, schema)
        if type_err:
            errors.append(
                f"Variable '{var_name}' in environment.json has invalid "
                f"schema.default ({type_err})"
            )
            continue

        schema["default"] = resolved_val
        resolved_names.append(var_name)

    if errors:
        raise ValueError("; ".join(errors))

    _write_config_file(app_cfg, app_data)
    return resolved_names


def preserve_env_var_placeholders_on_pull(
    app_dir: str | Path,
    previous_env_var_names: set[str],
    previous_env_defaults: dict[str, Any] | None = None,
) -> list[str]:
    """Restore ``"$env_var"`` in pulled app config and sync environment.json.

    For each variable in ``previous_env_var_names`` that still exists in the
    exported ``app.json`` / ``app.yaml``, writes its exported default into
    ``environment.json`` under ``app.variableDeclarations`` and restores
    ``schema.default = "$env_var"`` in the local app config.
    """
    if not previous_env_var_names:
        return []

    root = Path(app_dir)
    app_cfg = _find_app_config_file(root)
    if app_cfg is None:
        return []

    try:
        app_data = _load_config_file(app_cfg)
    except (json.JSONDecodeError, yaml.YAMLError, OSError):
        return []
    if not isinstance(app_data, dict):
        return []

    decls = _get_variable_declarations_list(app_data)
    if not decls:
        return []

    preserved: list[str] = []
    exported_updates: list[tuple[str, Any, str | None]] = []
    fallback_defaults = previous_env_defaults or {}

    for decl in decls:
        if not isinstance(decl, dict):
            continue
        name = decl.get("name")
        schema = decl.get("schema")
        if (
            not isinstance(name, str)
            or name not in previous_env_var_names
            or not isinstance(schema, dict)
        ):
            continue

        var_type = (
            schema.get("type") if isinstance(schema.get("type"), str) else None
        )
        if "default" in schema and schema["default"] != ENV_VAR_PLACEHOLDER:
            exported_updates.append((name, schema["default"], var_type))
        elif name in fallback_defaults:
            exported_updates.append((name, fallback_defaults[name], var_type))

        schema["default"] = ENV_VAR_PLACEHOLDER
        preserved.append(name)

    if not preserved:
        return []

    _write_config_file(app_cfg, app_data)

    if exported_updates:
        env_path = root / "environment.json"
        env_data: dict[str, Any] = {}
        if env_path.is_file():
            try:
                loaded = json.loads(env_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    env_data = loaded
            except (json.JSONDecodeError, OSError):
                env_data = {}

        app_section = env_data.get("app")
        if not isinstance(app_section, dict):
            app_section = {}
            env_data["app"] = app_section

        decl_key = (
            "variable_declarations"
            if "variable_declarations" in app_section
            and "variableDeclarations" not in app_section
            else "variableDeclarations"
        )
        env_decls = app_section.get(decl_key)
        if not isinstance(env_decls, list):
            env_decls = []
            app_section[decl_key] = env_decls

        by_name: dict[str, dict[str, Any]] = {}
        for item in env_decls:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                by_name[item["name"]] = item

        for var_name, val, var_type in exported_updates:
            if var_name in by_name:
                existing_entry = by_name[var_name]
                entry_schema = existing_entry.get("schema")
                if not isinstance(entry_schema, dict):
                    entry_schema = {}
                    existing_entry["schema"] = entry_schema
                if var_type and "type" not in entry_schema:
                    entry_schema["type"] = var_type
                entry_schema["default"] = val
            else:
                new_schema: dict[str, Any] = {}
                if var_type:
                    new_schema["type"] = var_type
                new_schema["default"] = val
                entry = {"name": var_name, "schema": new_schema}
                env_decls.append(entry)
                by_name[var_name] = entry

        env_path.write_text(
            json.dumps(env_data, indent=2) + "\n", encoding="utf-8"
        )

    return preserved
