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

"""Environment variable resolution for app variables and ConnectorToolsets."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_VAR_PLACEHOLDER = "$env_var"
CONNECTION_NAME_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/connections/[^/\s]+$"
)


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


@dataclass
class ConnectorToolsetEnvIssue:
    """Validation issue for a ConnectorToolset ``$env_var`` connection."""

    is_env_file_issue: bool
    manifest_path: Path | None
    message: str
    fix: str


def is_valid_connection_resource_name(value: Any) -> bool:
    """Return True if ``value`` matches Integration Connectors Connection."""
    return isinstance(value, str) and bool(CONNECTION_NAME_RE.match(value))


def _sanitize_resource_key(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9-_]", "_", name)


def _iter_toolset_manifests(app_dir: Path) -> list[tuple[str, Path]]:
    """Yield ``(entry_stem, manifest_path)`` under ``<app_dir>/toolsets``."""
    toolsets_dir = app_dir / "toolsets"
    if not toolsets_dir.is_dir():
        return []
    manifests: list[tuple[str, Path]] = []
    for child in sorted(toolsets_dir.iterdir()):
        if child.is_dir():
            for ext in (".json", ".yaml", ".yml"):
                candidate = child / f"{child.name}{ext}"
                if candidate.is_file():
                    manifests.append((child.name, candidate))
                    break
        elif (
            child.is_file()
            and child.suffix in (".json", ".yaml", ".yml")
            and child.stem
        ):
            manifests.append((child.stem, child))
    return manifests


def _get_connector_toolset_dict(
    toolset_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    if "connectorToolset" in toolset_data:
        ct = toolset_data["connectorToolset"]
        return (ct if isinstance(ct, dict) else None), "connectorToolset"
    if "connector_toolset" in toolset_data:
        ct = toolset_data["connector_toolset"]
        return (ct if isinstance(ct, dict) else None), "connector_toolset"
    return None, "connectorToolset"


def _get_toolset_display_name(toolset_data: dict[str, Any]) -> str | None:
    disp = toolset_data.get("displayName")
    if not isinstance(disp, str) or not disp.strip():
        disp = toolset_data.get("display_name")
    return disp.strip() if isinstance(disp, str) and disp.strip() else None


def collect_env_var_connector_toolsets(app_dir: str | Path) -> set[str]:
    """Return toolset names whose ``connectorToolset.connection`` uses
    ``"$env_var"``.
    """
    names: set[str] = set()
    for entry, mpath in _iter_toolset_manifests(Path(app_dir)):
        try:
            data = _load_config_file(mpath)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        ct, _ = _get_connector_toolset_dict(data)
        if ct is not None and ct.get("connection") == ENV_VAR_PLACEHOLDER:
            disp = _get_toolset_display_name(data)
            names.add(disp or entry)
    return names


def extract_env_connector_toolset_connections(env_data: Any) -> dict[str, str]:
    """Return ``{toolset_key: connection}`` from ``environment.json`` data."""
    if not isinstance(env_data, dict):
        return {}
    toolsets_env = env_data.get("toolsets")
    if not isinstance(toolsets_env, dict):
        return {}
    connections: dict[str, str] = {}
    for key, spec in toolsets_env.items():
        if not isinstance(key, str) or not isinstance(spec, dict):
            continue
        ct, _ = _get_connector_toolset_dict(spec)
        if ct is None:
            continue
        conn = ct.get("connection")
        if (
            isinstance(conn, str)
            and conn.strip()
            and conn != ENV_VAR_PLACEHOLDER
        ):
            connections[key] = conn
    return connections


def _lookup_toolset_env_spec(
    toolsets_env: dict[str, Any], entry: str, disp: str | None
) -> tuple[str, Any, bool]:
    """Look up ``toolsets`` entry in ``environment.json`` by ``displayName``
    first, falling back to directory/file ``entry`` stem.
    """
    if disp and disp in toolsets_env:
        return disp, toolsets_env[disp], True
    if entry in toolsets_env:
        return entry, toolsets_env[entry], True
    return disp or entry, None, False


def validate_connector_toolset_env_vars(
    app_dir: str | Path,
    env_data: Any,
    env_label: str = "environment.json",
) -> tuple[dict[Path, str], list[str], list[ConnectorToolsetEnvIssue]]:
    """Validate ``connectorToolset.connection`` ``"$env_var"`` placeholders
    against ``env_data``.

    Returns ``(resolved_by_manifest, resolved_labels, issues)``.
    """
    root = Path(app_dir)
    placeholders: list[tuple[str, str | None, Path]] = []
    for entry, mpath in _iter_toolset_manifests(root):
        try:
            data = _load_config_file(mpath)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        ct, _ = _get_connector_toolset_dict(data)
        if ct is not None and ct.get("connection") == ENV_VAR_PLACEHOLDER:
            placeholders.append((entry, _get_toolset_display_name(data), mpath))

    if not placeholders:
        return {}, [], []

    issues: list[ConnectorToolsetEnvIssue] = []
    if not isinstance(env_data, dict):
        issues.append(
            ConnectorToolsetEnvIssue(
                is_env_file_issue=True,
                manifest_path=None,
                message=f"{env_label} root must be a JSON object",
                fix=(
                    f"Ensure {env_label} is a JSON object with a 'toolsets' "
                    "mapping"
                ),
            )
        )
        return {}, [], issues

    toolsets_env = env_data.get("toolsets")
    if toolsets_env is not None and not isinstance(toolsets_env, dict):
        issues.append(
            ConnectorToolsetEnvIssue(
                is_env_file_issue=True,
                manifest_path=None,
                message=(
                    f"Field 'toolsets' in {env_label} must be a JSON object"
                ),
                fix=(
                    f"Set 'toolsets' in {env_label} to an object keyed by "
                    "toolset displayName (or directory name)"
                ),
            )
        )
        return {}, [], issues

    toolsets_map = toolsets_env if isinstance(toolsets_env, dict) else {}
    resolved_by_path: dict[Path, str] = {}
    resolved_labels: list[str] = []

    for entry, disp, mpath in placeholders:
        label = disp or entry
        matched_key, spec, found = _lookup_toolset_env_spec(
            toolsets_map, entry, disp
        )
        expected_path = f"toolsets.{matched_key}.connectorToolset.connection"
        if not found or not isinstance(spec, dict):
            issues.append(
                ConnectorToolsetEnvIssue(
                    is_env_file_issue=False,
                    manifest_path=mpath,
                    message=(
                        f"Toolset '{label}' has connectorToolset.connection="
                        f"'{ENV_VAR_PLACEHOLDER}' in {mpath.name} but is "
                        f"missing from {env_label} under '{expected_path}'"
                    ),
                    fix=(
                        f"Add {{'{matched_key}': {{'connectorToolset': "
                        f"{{'connection': 'projects/<project>/locations/"
                        f"<location>/connections/<connection>'}}}}}} under "
                        f"'toolsets' in {env_label}"
                    ),
                )
            )
            continue

        ct_spec, _ = _get_connector_toolset_dict(spec)
        if ct_spec is None or "connection" not in ct_spec:
            issues.append(
                ConnectorToolsetEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=mpath,
                    message=(
                        f"Toolset '{label}' has connectorToolset.connection="
                        f"'{ENV_VAR_PLACEHOLDER}' in {mpath.name} but "
                        f"'{expected_path}' is missing in {env_label}"
                    ),
                    fix=(
                        f"Set '{expected_path}' in {env_label} to "
                        "'projects/<project>/locations/<location>/"
                        "connections/<connection>'"
                    ),
                )
            )
            continue

        conn_val = ct_spec["connection"]
        if conn_val == ENV_VAR_PLACEHOLDER:
            issues.append(
                ConnectorToolsetEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=mpath,
                    message=(
                        f"Toolset '{label}' in {env_label} under "
                        f"'{expected_path}' has unresolved "
                        f"'{ENV_VAR_PLACEHOLDER}'"
                    ),
                    fix=(
                        f"Replace '{ENV_VAR_PLACEHOLDER}' at '{expected_path}' "
                        f"in {env_label} with the concrete connection resource "
                        "name"
                    ),
                )
            )
            continue

        if not isinstance(conn_val, str) or not conn_val.strip():
            issues.append(
                ConnectorToolsetEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=mpath,
                    message=(
                        f"Toolset '{label}' in {env_label} under "
                        f"'{expected_path}' must be a non-empty string "
                        f"(got {_value_type_name(conn_val)})"
                    ),
                    fix=(
                        f"Set '{expected_path}' in {env_label} to "
                        "'projects/<project>/locations/<location>/"
                        "connections/<connection>'"
                    ),
                )
            )
            continue

        if not is_valid_connection_resource_name(conn_val):
            issues.append(
                ConnectorToolsetEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=mpath,
                    message=(
                        f"Toolset '{label}' in {env_label} under "
                        f"'{expected_path}' has invalid connection resource "
                        f"name '{conn_val}' (expected 'projects/{{project}}/"
                        "locations/{location}/connections/{connection}')"
                    ),
                    fix=(
                        f"Update '{expected_path}' in {env_label} to match "
                        "'projects/<project>/locations/<location>/"
                        "connections/<connection>'"
                    ),
                )
            )
            continue

        resolved_by_path[mpath] = conn_val
        resolved_labels.append(label)

    return resolved_by_path, resolved_labels, issues


def resolve_connector_toolset_env_vars_in_dir(
    app_dir: str | Path,
) -> list[str]:
    """Resolve ``"$env_var"`` ``connectorToolset.connection`` fields in staged
    ``toolsets/`` manifests from ``environment.json``.
    """
    root = Path(app_dir)
    placeholder_names = collect_env_var_connector_toolsets(root)
    if not placeholder_names:
        return []

    env_path = root / "environment.json"
    if not env_path.is_file():
        missing_list = ", ".join(sorted(placeholder_names))
        raise ValueError(
            f"Toolset manifest(s) declare '{ENV_VAR_PLACEHOLDER}' for "
            f"connectorToolset.connection [{missing_list}], but "
            "environment.json was not found (add environment.json to the app "
            "directory or pass --env-file)"
        )

    try:
        env_data = json.loads(env_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in environment.json: {e}") from e

    resolved_by_path, resolved_labels, issues = (
        validate_connector_toolset_env_vars(root, env_data)
    )
    if issues:
        raise ValueError("; ".join(i.message for i in issues))

    for mpath, conn_val in resolved_by_path.items():
        data = _load_config_file(mpath)
        ct, _ = _get_connector_toolset_dict(data)
        assert ct is not None
        ct["connection"] = conn_val
        _write_config_file(mpath, data)

    return resolved_labels


def preserve_connector_toolset_env_vars_on_pull(
    app_dir: str | Path,
    previous_env_var_toolsets: set[str],
    previous_env_connections: dict[str, str] | None = None,
) -> list[str]:
    """Restore ``"$env_var"`` on pulled ``connectorToolset.connection`` fields
    and sync concrete exported connection resource names into
    ``environment.json``.
    """
    if not previous_env_var_toolsets:
        return []

    root = Path(app_dir)
    sanitized_prev = {
        _sanitize_resource_key(n) for n in previous_env_var_toolsets
    }
    fallback_conns = previous_env_connections or {}

    env_path = root / "environment.json"
    env_data: dict[str, Any] = {}
    if env_path.is_file():
        try:
            loaded = json.loads(env_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                env_data = loaded
        except (json.JSONDecodeError, OSError):
            env_data = {}

    existing_toolsets_env = (
        env_data.get("toolsets")
        if isinstance(env_data.get("toolsets"), dict)
        else {}
    )

    preserved: list[str] = []
    exported_updates: list[tuple[str, str, str]] = []

    for entry, mpath in _iter_toolset_manifests(root):
        try:
            data = _load_config_file(mpath)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        ct, ct_key = _get_connector_toolset_dict(data)
        if ct is None:
            continue

        disp = _get_toolset_display_name(data)
        matched = (
            entry in previous_env_var_toolsets
            or (disp is not None and disp in previous_env_var_toolsets)
            or entry in sanitized_prev
        )
        if not matched:
            continue

        # Preserve existing environment.json key choice if present (e.g. stem
        # vs displayName), defaulting to displayName (CES native convention).
        if entry in existing_toolsets_env or (
            entry in fallback_conns
            and (disp is None or disp not in fallback_conns)
        ):
            target_env_key = entry
        else:
            target_env_key = disp or entry

        conn = ct.get("connection")
        if (
            isinstance(conn, str)
            and conn.strip()
            and conn != ENV_VAR_PLACEHOLDER
        ):
            exported_updates.append((target_env_key, ct_key, conn))
        else:
            fallback_val = (
                fallback_conns.get(disp) if disp else None
            ) or fallback_conns.get(entry)
            already_in_env = any(
                k in existing_toolsets_env
                and isinstance(existing_toolsets_env[k], dict)
                and _get_connector_toolset_dict(existing_toolsets_env[k])[0]
                is not None
                for k in (disp, entry)
                if k
            )
            if fallback_val and not already_in_env:
                exported_updates.append((target_env_key, ct_key, fallback_val))

        if conn != ENV_VAR_PLACEHOLDER:
            ct["connection"] = ENV_VAR_PLACEHOLDER
            _write_config_file(mpath, data)
        preserved.append(disp or entry)

    if exported_updates:
        toolsets_section = env_data.get("toolsets")
        if not isinstance(toolsets_section, dict):
            toolsets_section = {}
            env_data["toolsets"] = toolsets_section

        for target_env_key, preferred_ct_key, conn_val in exported_updates:
            ts_entry = toolsets_section.get(target_env_key)
            if not isinstance(ts_entry, dict):
                ts_entry = {}
                toolsets_section[target_env_key] = ts_entry
            existing_ct, existing_ct_key = _get_connector_toolset_dict(ts_entry)
            if existing_ct is not None:
                existing_ct["connection"] = conn_val
            else:
                use_key = (
                    existing_ct_key
                    if existing_ct_key in ts_entry
                    else preferred_ct_key
                )
                ts_entry[use_key] = {"connection": conn_val}

        env_path.write_text(
            json.dumps(env_data, indent=2) + "\n", encoding="utf-8"
        )

    return preserved
