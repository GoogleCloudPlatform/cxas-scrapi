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

"""Environment variable resolution for app variables, tools, and toolsets."""

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
HTTP_URL_RE = re.compile(r"^https?://[^\s]+$")
SERVICE_DIRECTORY_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/namespaces/[^/\s]+/services/[^/\s]+$"
)
SECRET_VERSION_RE = re.compile(
    r"^projects/[^/\s]+/secrets/[^/\s]+/versions/[^/\s]+$"
)
ENGINE_NAME_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/collections/[^/\s]+/engines/[^/\s]+$"
)
DATA_STORE_NAME_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/collections/[^/\s]+/"
    r"dataStores/[^/\s]+$"
)
RAG_CORPUS_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/ragCorpora/[^/\s]+$"
)
INTEGRATION_VERSION_RE = re.compile(
    r"^projects/[^/\s]+/locations/[^/\s]+/integrations/[^/\s]+/"
    r"versions/[^/\s]+$"
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


_TOOL_AND_TOOLSET_CATEGORIES: tuple[str, ...] = ("tools", "toolsets")

_TOOL_NAME_CONTAINERS: tuple[str, ...] = (
    "openApiTool",
    "open_api_tool",
    "connectorTool",
    "connector_tool",
    "mcpTool",
    "mcp_tool",
    "dataStoreTool",
    "data_store_tool",
    "fileSearchTool",
    "file_search_tool",
    "remoteAgentTool",
    "remote_agent_tool",
    "applicationIntegrationTool",
    "application_integration_tool",
    "enterpriseAgentTool",
    "enterprise_agent_tool",
    "googleSearchTool",
    "google_search_tool",
    "pythonFunction",
    "python_function",
    "clientFunction",
    "client_function",
    "widgetTool",
    "widget_tool",
    "agentTool",
    "agent_tool",
    "systemTool",
    "system_tool",
)

_EXACT_PATH_VALIDATORS: dict[tuple[str, ...], tuple[re.Pattern[str], str]] = {
    ("connector_toolset", "connection"): (
        CONNECTION_NAME_RE,
        "projects/{project}/locations/{location}/connections/{connection}",
    ),
    ("connector_tool", "connection"): (
        CONNECTION_NAME_RE,
        "projects/{project}/locations/{location}/connections/{connection}",
    ),
    ("mcp_toolset", "server_address"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
    ("mcp_tool", "server_address"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
    ("open_api_tool", "url"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
    ("open_api_toolset", "url"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
    ("file_search_tool", "file_corpus"): (
        RAG_CORPUS_RE,
        "projects/{project}/locations/{location}/ragCorpora/{rag_corpus}",
    ),
    ("data_store_tool", "engine_source", "engine"): (
        ENGINE_NAME_RE,
        "projects/{project}/locations/{location}/collections/{collection}/"
        "engines/{engine}",
    ),
    ("data_store_tool", "boost_specs", "data_stores"): (
        DATA_STORE_NAME_RE,
        "projects/{project}/locations/{location}/collections/{collection}/"
        "dataStores/{dataStore}",
    ),
    ("application_integration_tool", "integration_version"): (
        INTEGRATION_VERSION_RE,
        "projects/{project}/locations/{location}/integrations/{integration}/"
        "versions/{version}",
    ),
    ("google_search_tool", "context_urls"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
}

_SUFFIX_PATH_VALIDATORS: dict[tuple[str, ...], tuple[re.Pattern[str], str]] = {
    ("service_directory_config", "service"): (
        SERVICE_DIRECTORY_RE,
        "projects/{project}/locations/{location}/namespaces/{namespace}/"
        "services/{service}",
    ),
    ("api_key_config", "api_key_secret_version"): (
        SECRET_VERSION_RE,
        "projects/{project}/secrets/{secret}/versions/{version}",
    ),
    ("oauth_config", "client_secret_version"): (
        SECRET_VERSION_RE,
        "projects/{project}/secrets/{secret}/versions/{version}",
    ),
    ("oauth_config", "token_endpoint"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
    ("data_store", "name"): (
        DATA_STORE_NAME_RE,
        "projects/{project}/locations/{location}/collections/{collection}/"
        "dataStores/{dataStore}",
    ),
    ("supported_interfaces", "url"): (
        HTTP_URL_RE,
        "https://<host>/<path>",
    ),
}


@dataclass
class ResourceEnvIssue:
    """Validation issue for a tool/toolset ``$env_var`` placeholder."""

    is_env_file_issue: bool
    manifest_path: Path | None
    message: str
    fix: str


@dataclass(frozen=True)
class ResourceEnvPlaceholder:
    """A ``$env_var`` placeholder discovered in a tool or toolset manifest."""

    category: str
    entry: str
    label: str
    candidate_keys: tuple[str, ...]
    manifest_path: Path
    field_path: tuple[str | int, ...]
    is_list_placeholder: bool = False
    openapi_schema_path: Path | None = None
    openapi_schema_inline: bool = False


def is_valid_connection_resource_name(value: Any) -> bool:
    """Return True if ``value`` matches Integration Connectors Connection."""
    return isinstance(value, str) and bool(CONNECTION_NAME_RE.match(value))


def _sanitize_resource_key(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9-_]", "_", name)


def _to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _to_camel(name: str) -> str:
    parts = name.split("_")
    if len(parts) <= 1:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)


def _find_dict_key(mapping: dict[str, Any], key: str) -> str | None:
    if key in mapping:
        return key
    camel = _to_camel(key)
    if camel in mapping:
        return camel
    snake = _to_snake(key)
    if snake in mapping:
        return snake
    return None


def _format_field_path(field_path: tuple[str | int, ...]) -> str:
    out = ""
    for part in field_path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else part
    return out


def _get_field_validator(
    field_path: tuple[str | int, ...],
) -> tuple[re.Pattern[str], str] | None:
    str_parts = tuple(_to_snake(p) for p in field_path if isinstance(p, str))
    if str_parts in _EXACT_PATH_VALIDATORS:
        return _EXACT_PATH_VALIDATORS[str_parts]
    for suffix_len in (2, 1):
        if len(str_parts) >= suffix_len:
            suffix = str_parts[-suffix_len:]
            if suffix in _SUFFIX_PATH_VALIDATORS:
                return _SUFFIX_PATH_VALIDATORS[suffix]
    return None


def _iter_category_manifests(
    app_dir: Path, category: str
) -> list[tuple[str, Path]]:
    """Yield ``(entry_stem, manifest_path)`` under ``<app_dir>/<category>``."""
    cat_dir = app_dir / category
    if not cat_dir.is_dir():
        return []
    manifests: list[tuple[str, Path]] = []
    for child in sorted(cat_dir.iterdir()):
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


def _get_resource_candidate_keys(
    category: str, entry: str, data: dict[str, Any]
) -> tuple[str, tuple[str, ...]]:
    """Return ``(label, ordered_candidate_env_keys)`` for a tool or toolset."""
    keys: list[str] = []
    for key_name in ("displayName", "display_name"):
        val = data.get(key_name)
        if isinstance(val, str) and val.strip() and val.strip() not in keys:
            keys.append(val.strip())

    if category == "tools":
        for container_key in _TOOL_NAME_CONTAINERS:
            sub = data.get(container_key)
            if isinstance(sub, dict):
                sub_name = sub.get("name")
                if (
                    isinstance(sub_name, str)
                    and sub_name.strip()
                    and sub_name.strip() not in keys
                ):
                    keys.append(sub_name.strip())

    if entry not in keys:
        keys.append(entry)

    return keys[0], tuple(keys)


def _get_proto_descriptors() -> dict[str, Any]:
    from cxas_scrapi.utils.tracing.app_config import (  # noqa: PLC0415
        _RESOURCE_DESCRIPTORS,
    )

    return _RESOURCE_DESCRIPTORS


def _resolve_proto_field(desc: Any, key: str) -> Any:
    if desc is None or desc.full_name.startswith("google.protobuf."):
        return None
    for proto_field in desc.fields:
        if key in (proto_field.name, proto_field.camelcase_name):
            return proto_field
    return None


def _collect_placeholders_from_dict(
    node: dict[str, Any],
    desc: Any,
    prefix: tuple[str | int, ...],
    out: list[tuple[tuple[str | int, ...], bool]],
) -> None:
    from google.protobuf.descriptor import FieldDescriptor  # noqa: PLC0415

    for key, val in node.items():
        proto_field = _resolve_proto_field(desc, key)
        if (
            proto_field is not None
            and proto_field.type == FieldDescriptor.TYPE_BOOL
            and not proto_field.is_repeated
        ):
            continue

        cur_path = (*prefix, key)
        if val == ENV_VAR_PLACEHOLDER:
            out.append((cur_path, False))
            continue

        child_desc = None
        is_bool_map = False
        if (
            proto_field is not None
            and proto_field.type == FieldDescriptor.TYPE_MESSAGE
            and proto_field.message_type is not None
            and not proto_field.message_type.full_name.startswith(
                "google.protobuf."
            )
        ):
            if proto_field.message_type.GetOptions().map_entry:
                val_field = proto_field.message_type.fields_by_name["value"]
                if val_field.type == FieldDescriptor.TYPE_BOOL:
                    is_bool_map = True
                elif val_field.type == FieldDescriptor.TYPE_MESSAGE:
                    child_desc = val_field.message_type
            else:
                child_desc = proto_field.message_type

        if is_bool_map:
            continue

        if isinstance(val, dict):
            if (
                proto_field is not None
                and proto_field.type == FieldDescriptor.TYPE_MESSAGE
                and proto_field.message_type is not None
                and proto_field.message_type.GetOptions().map_entry
            ):
                for map_k, map_v in val.items():
                    map_path = (*cur_path, map_k)
                    if map_v == ENV_VAR_PLACEHOLDER:
                        out.append((map_path, False))
                    elif isinstance(map_v, dict):
                        _collect_placeholders_from_dict(
                            map_v, child_desc, map_path, out
                        )
            else:
                _collect_placeholders_from_dict(val, child_desc, cur_path, out)
        elif isinstance(val, list) and val:
            if all(item == ENV_VAR_PLACEHOLDER for item in val):
                out.append((cur_path, True))
            else:
                for idx, item in enumerate(val):
                    item_path = (*cur_path, idx)
                    if item == ENV_VAR_PLACEHOLDER:
                        out.append((item_path, False))
                    elif isinstance(item, dict):
                        _collect_placeholders_from_dict(
                            item, child_desc, item_path, out
                        )


def _detect_default_openapi_schema_placeholder(
    root: Path,
    category: str,
    entry: str,
) -> tuple[tuple[str, str], Path | None, bool] | None:
    """Check default CES export OpenAPI schema files for ``$env_var``."""
    default_folder = (
        "open_api_toolset" if category == "toolsets" else "open_api_tool"
    )
    default_oa_key = (
        "openApiToolset" if category == "toolsets" else "openApiTool"
    )
    for ext in (".yaml", ".yml"):
        candidate_path = (
            root / category / entry / default_folder / f"open_api_schema{ext}"
        )
        if candidate_path.is_symlink() or not candidate_path.is_file():
            continue
        try:
            raw = candidate_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ENV_VAR_PLACEHOLDER in raw:
            return (default_oa_key, "url"), candidate_path, False
    return None


def _read_schema_file_if_placeholder(
    root: Path, schema_val: str
) -> Path | None:
    if "\n" in schema_val:
        return None
    candidate_path = root / schema_val
    if candidate_path.is_symlink() or not candidate_path.is_file():
        return None
    try:
        raw = candidate_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return candidate_path if ENV_VAR_PLACEHOLDER in raw else None


def _detect_openapi_schema_placeholder(
    root: Path,
    category: str,
    entry: str,
    data: dict[str, Any],
) -> tuple[tuple[str, str], Path | None, bool] | None:
    """Return ``((oa_key, 'url'), schema_path, is_inline)`` if OpenAPI schema
    contains ``$env_var``.
    """
    oa_candidates = (
        ("openApiToolset", "open_api_toolset")
        if category == "toolsets"
        else ("openApiTool", "open_api_tool")
    )
    has_explicit_schema = False
    for oa_key in oa_candidates:
        oa = data.get(oa_key)
        if not isinstance(oa, dict):
            continue
        schema_key = _find_dict_key(oa, "openApiSchema")
        schema_val = oa.get(schema_key) if schema_key is not None else None
        if not isinstance(schema_val, str) or not schema_val.strip():
            continue
        has_explicit_schema = True
        if schema_val == ENV_VAR_PLACEHOLDER:
            return None
        if ENV_VAR_PLACEHOLDER in schema_val:
            return (oa_key, "url"), None, True
        hit_path = _read_schema_file_if_placeholder(root, schema_val)
        if hit_path is not None:
            return (oa_key, "url"), hit_path, False
    if has_explicit_schema:
        return None
    return _detect_default_openapi_schema_placeholder(root, category, entry)


def collect_tool_and_toolset_env_var_placeholders(
    app_dir: str | Path,
) -> list[ResourceEnvPlaceholder]:
    """Return all non-boolean ``"$env_var"`` placeholders across ``tools/``
    and ``toolsets/`` manifests (including OpenAPI schemas with ``$env_var``).
    """
    root = Path(app_dir)
    descriptors = _get_proto_descriptors()
    found: list[ResourceEnvPlaceholder] = []

    for category in _TOOL_AND_TOOLSET_CATEGORIES:
        cat_desc = descriptors.get(category)
        for entry, mpath in _iter_category_manifests(root, category):
            try:
                data = _load_config_file(mpath)
            except (ValueError, yaml.YAMLError, OSError):
                continue
            if not isinstance(data, dict):
                continue

            label, cand_keys = _get_resource_candidate_keys(
                category, entry, data
            )
            raw_paths: list[tuple[tuple[str | int, ...], bool]] = []
            _collect_placeholders_from_dict(data, cat_desc, (), raw_paths)
            seen_targets: set[
                tuple[tuple[str | int, ...], Path | None, bool]
            ] = set()
            for field_path, is_list in raw_paths:
                target_key = (field_path, None, False)
                if target_key in seen_targets:
                    continue
                seen_targets.add(target_key)
                found.append(
                    ResourceEnvPlaceholder(
                        category=category,
                        entry=entry,
                        label=label,
                        candidate_keys=cand_keys,
                        manifest_path=mpath,
                        field_path=field_path,
                        is_list_placeholder=is_list,
                    )
                )

            oa_hit = _detect_openapi_schema_placeholder(
                root, category, entry, data
            )
            if oa_hit is not None:
                oa_url_path, schema_path, is_inline = oa_hit
                target_key = (oa_url_path, schema_path, is_inline)
                if target_key not in seen_targets:
                    seen_targets.add(target_key)
                    found.append(
                        ResourceEnvPlaceholder(
                            category=category,
                            entry=entry,
                            label=label,
                            candidate_keys=cand_keys,
                            manifest_path=mpath,
                            field_path=oa_url_path,
                            is_list_placeholder=False,
                            openapi_schema_path=schema_path,
                            openapi_schema_inline=is_inline,
                        )
                    )

    return found


def _lookup_env_resource_spec(
    cat_env: dict[str, Any], candidate_keys: tuple[str, ...]
) -> tuple[str, Any, bool]:
    for key in candidate_keys:
        if key in cat_env:
            return key, cat_env[key], True
    return candidate_keys[0], None, False


def _get_nested_env_value(
    spec: Any, field_path: tuple[str | int, ...]
) -> tuple[bool, Any]:
    cur = spec
    for part in field_path:
        if isinstance(part, int):
            if not isinstance(cur, list) or part >= len(cur):
                return False, None
            cur = cur[part]
        else:
            if not isinstance(cur, dict):
                return False, None
            matched_key = _find_dict_key(cur, part)
            if matched_key is None:
                return False, None
            cur = cur[matched_key]
    return True, cur


def _set_nested_value(
    container: Any, field_path: tuple[str | int, ...], value: Any
) -> None:
    cur = container
    for idx, part in enumerate(field_path[:-1]):
        nxt = field_path[idx + 1]
        if isinstance(part, int):
            while len(cur) <= part:
                cur.append({} if isinstance(nxt, str) else [])
            if isinstance(nxt, str) and not isinstance(cur[part], dict):
                cur[part] = {}
            elif isinstance(nxt, int) and not isinstance(cur[part], list):
                cur[part] = []
            cur = cur[part]
        else:
            matched_key = _find_dict_key(cur, part) or part
            existing = cur.get(matched_key)
            if isinstance(nxt, str) and not isinstance(existing, dict):
                cur[matched_key] = {}
            elif isinstance(nxt, int) and not isinstance(existing, list):
                cur[matched_key] = []
            cur = cur[matched_key]

    last = field_path[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append(None)
        cur[last] = value
    else:
        matched_key = _find_dict_key(cur, last) or last
        cur[matched_key] = value


def _validate_resolved_string(
    val: Any,
    placeholder: ResourceEnvPlaceholder,
    expected_path: str,
    env_label: str,
) -> ResourceEnvIssue | None:
    kind = "Toolset" if placeholder.category == "toolsets" else "Tool"
    validator = _get_field_validator(placeholder.field_path)
    expected_hint = validator[1] if validator is not None else "non-empty value"

    if val == ENV_VAR_PLACEHOLDER:
        return ResourceEnvIssue(
            is_env_file_issue=True,
            manifest_path=placeholder.manifest_path,
            message=(
                f"{kind} '{placeholder.label}' in {env_label} under "
                f"'{expected_path}' has unresolved '{ENV_VAR_PLACEHOLDER}'"
            ),
            fix=(
                f"Replace '{ENV_VAR_PLACEHOLDER}' at '{expected_path}' in "
                f"{env_label} with the concrete value ({expected_hint})"
            ),
        )

    if not isinstance(val, str) or not val.strip():
        return ResourceEnvIssue(
            is_env_file_issue=True,
            manifest_path=placeholder.manifest_path,
            message=(
                f"{kind} '{placeholder.label}' in {env_label} under "
                f"'{expected_path}' must be a non-empty string "
                f"(got {_value_type_name(val)})"
            ),
            fix=f"Set '{expected_path}' in {env_label} to '{expected_hint}'",
        )

    if validator is not None:
        pattern, pattern_desc = validator
        if not pattern.match(val):
            return ResourceEnvIssue(
                is_env_file_issue=True,
                manifest_path=placeholder.manifest_path,
                message=(
                    f"{kind} '{placeholder.label}' in {env_label} under "
                    f"'{expected_path}' has invalid value '{val}' "
                    f"(expected '{pattern_desc}')"
                ),
                fix=(
                    f"Update '{expected_path}' in {env_label} to match "
                    f"'{pattern_desc}'"
                ),
            )
    return None


def _dedupe_env_issues(
    issues: list[ResourceEnvIssue],
) -> list[ResourceEnvIssue]:
    seen: set[tuple[bool, Path | None, str]] = set()
    unique: list[ResourceEnvIssue] = []
    for issue in issues:
        key = (issue.is_env_file_issue, issue.manifest_path, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def validate_tool_and_toolset_env_vars(
    app_dir: str | Path,
    env_data: Any,
    env_label: str = "environment.json",
) -> tuple[
    list[tuple[ResourceEnvPlaceholder, Any]],
    list[str],
    list[ResourceEnvIssue],
]:
    """Validate all ``tools/`` and ``toolsets/`` ``"$env_var"`` placeholders
    against ``env_data``.

    Returns ``(resolutions, resolved_labels, issues)``.
    """
    placeholders = collect_tool_and_toolset_env_var_placeholders(app_dir)
    if not placeholders:
        return [], [], []

    issues: list[ResourceEnvIssue] = []
    if not isinstance(env_data, dict):
        issues.append(
            ResourceEnvIssue(
                is_env_file_issue=True,
                manifest_path=None,
                message=f"{env_label} root must be a JSON object",
                fix=(
                    f"Ensure {env_label} is a JSON object with 'tools' / "
                    "'toolsets' mappings"
                ),
            )
        )
        return [], [], issues

    for category in sorted({p.category for p in placeholders}):
        cat_val = env_data.get(category)
        if cat_val is not None and not isinstance(cat_val, dict):
            issues.append(
                ResourceEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=None,
                    message=(
                        f"Field '{category}' in {env_label} must be a JSON "
                        "object"
                    ),
                    fix=(
                        f"Set '{category}' in {env_label} to an object keyed "
                        "by displayName (or directory name)"
                    ),
                )
            )
    if issues:
        return [], [], issues

    resolutions: list[tuple[ResourceEnvPlaceholder, Any]] = []
    resolved_labels: list[str] = []

    for ph in placeholders:
        cat_map = env_data.get(ph.category)
        cat_dict = cat_map if isinstance(cat_map, dict) else {}
        matched_key, spec, found = _lookup_env_resource_spec(
            cat_dict, ph.candidate_keys
        )
        field_dotted = _format_field_path(ph.field_path)
        expected_path = f"{ph.category}.{matched_key}.{field_dotted}"
        kind = "Toolset" if ph.category == "toolsets" else "Tool"

        if not found or not isinstance(spec, dict):
            issues.append(
                ResourceEnvIssue(
                    is_env_file_issue=False,
                    manifest_path=ph.manifest_path,
                    message=(
                        f"{kind} '{ph.label}' uses '{ENV_VAR_PLACEHOLDER}' for "
                        f"'{field_dotted}' in {ph.manifest_path.name} but is "
                        f"missing from {env_label} under '{expected_path}'"
                    ),
                    fix=f"Add '{expected_path}' in {env_label}",
                )
            )
            continue

        val_found, raw_val = _get_nested_env_value(spec, ph.field_path)
        if not val_found:
            issues.append(
                ResourceEnvIssue(
                    is_env_file_issue=True,
                    manifest_path=ph.manifest_path,
                    message=(
                        f"{kind} '{ph.label}' uses '{ENV_VAR_PLACEHOLDER}' for "
                        f"'{field_dotted}' in {ph.manifest_path.name} but "
                        f"'{expected_path}' is missing in {env_label}"
                    ),
                    fix=f"Set '{expected_path}' in {env_label}",
                )
            )
            continue

        if ph.is_list_placeholder:
            if not isinstance(raw_val, list) or not raw_val:
                issues.append(
                    ResourceEnvIssue(
                        is_env_file_issue=True,
                        manifest_path=ph.manifest_path,
                        message=(
                            f"{kind} '{ph.label}' in {env_label} under "
                            f"'{expected_path}' must be a non-empty list of "
                            f"strings (got {_value_type_name(raw_val)})"
                        ),
                        fix=(
                            f"Set '{expected_path}' in {env_label} to a "
                            "non-empty list of strings"
                        ),
                    )
                )
                continue
            item_failed = False
            for idx, item in enumerate(raw_val):
                item_issue = _validate_resolved_string(
                    item, ph, f"{expected_path}[{idx}]", env_label
                )
                if item_issue is not None:
                    issues.append(item_issue)
                    item_failed = True
            if item_failed:
                continue
        else:
            issue = _validate_resolved_string(
                raw_val, ph, expected_path, env_label
            )
            if issue is not None:
                issues.append(issue)
                continue

        resolutions.append((ph, raw_val))
        resolved_labels.append(f"{ph.label}.{field_dotted}")

    return (
        resolutions,
        list(dict.fromkeys(resolved_labels)),
        _dedupe_env_issues(issues),
    )


def resolve_tool_and_toolset_env_vars_in_dir(
    app_dir: str | Path,
) -> list[str]:
    """Resolve ``"$env_var"`` placeholders across staged ``tools/`` and
    ``toolsets/`` manifests (and OpenAPI schemas) from ``environment.json``.
    """
    root = Path(app_dir)
    placeholders = collect_tool_and_toolset_env_var_placeholders(root)
    if not placeholders:
        return []

    env_path = root / "environment.json"
    if not env_path.is_file():
        missing_list = ", ".join(
            sorted(
                {
                    f"{p.label}.{_format_field_path(p.field_path)}"
                    for p in placeholders
                }
            )
        )
        raise ValueError(
            f"Tool/toolset manifest(s) declare '{ENV_VAR_PLACEHOLDER}' "
            f"[{missing_list}], but environment.json was not found (add "
            "environment.json to the app directory or pass --env-file)"
        )

    try:
        env_data = json.loads(env_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in environment.json: {e}") from e

    resolutions, resolved_labels, issues = validate_tool_and_toolset_env_vars(
        root, env_data
    )
    if issues:
        raise ValueError("; ".join(i.message for i in issues))

    by_manifest: dict[Path, list[tuple[ResourceEnvPlaceholder, Any]]] = {}
    for ph, val in resolutions:
        if ph.openapi_schema_path is not None:
            raw = ph.openapi_schema_path.read_text(encoding="utf-8")
            ph.openapi_schema_path.write_text(
                raw.replace(ENV_VAR_PLACEHOLDER, str(val)), encoding="utf-8"
            )
        else:
            by_manifest.setdefault(ph.manifest_path, []).append((ph, val))

    for mpath, items in by_manifest.items():
        data = _load_config_file(mpath)
        for ph, val in items:
            if ph.openapi_schema_inline:
                oa_dict = data.get(ph.field_path[0])
                if isinstance(oa_dict, dict):
                    schema_key = _find_dict_key(oa_dict, "openApiSchema")
                    if schema_key and isinstance(oa_dict.get(schema_key), str):
                        oa_dict[schema_key] = oa_dict[schema_key].replace(
                            ENV_VAR_PLACEHOLDER, str(val)
                        )
            else:
                _set_nested_value(data, ph.field_path, val)
        _write_config_file(mpath, data)

    return resolved_labels


def preserve_tool_and_toolset_env_vars_on_pull(
    app_dir: str | Path,
    previous_placeholders: list[ResourceEnvPlaceholder],
    previous_env_data: dict[str, Any] | None = None,
) -> list[str]:
    """Restore ``"$env_var"`` across pulled ``tools/`` and ``toolsets/``
    manifests and sync concrete exported values into ``environment.json``.
    """
    if not previous_placeholders:
        return []

    root = Path(app_dir)
    prev_env = previous_env_data if isinstance(previous_env_data, dict) else {}

    env_path = root / "environment.json"
    env_data: dict[str, Any] = {}
    if env_path.is_file():
        try:
            loaded = json.loads(env_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                env_data = loaded
        except (json.JSONDecodeError, OSError):
            env_data = {}

    # Group prior placeholders by category -> set of normalized keys
    by_category: dict[str, list[ResourceEnvPlaceholder]] = {}
    for ph in previous_placeholders:
        if ph.openapi_schema_path is not None or ph.openapi_schema_inline:
            continue
        by_category.setdefault(ph.category, []).append(ph)

    preserved: list[str] = []
    env_modified = False

    for category, cat_placeholders in by_category.items():
        existing_cat_env = (
            env_data.get(category)
            if isinstance(env_data.get(category), dict)
            else {}
        )
        prev_cat_env = (
            prev_env.get(category)
            if isinstance(prev_env.get(category), dict)
            else {}
        )

        for entry, mpath in _iter_category_manifests(root, category):
            try:
                data = _load_config_file(mpath)
            except (json.JSONDecodeError, yaml.YAMLError, OSError):
                continue
            if not isinstance(data, dict):
                continue

            label, cand_keys = _get_resource_candidate_keys(
                category, entry, data
            )
            cand_set = set(cand_keys) | {
                _sanitize_resource_key(k) for k in cand_keys
            }

            matched_phs: list[ResourceEnvPlaceholder] = []
            seen_field_paths: set[tuple[str | int, ...]] = set()
            for ph in cat_placeholders:
                ph_keys = set(ph.candidate_keys) | {
                    _sanitize_resource_key(k) for k in ph.candidate_keys
                }
                if cand_set & ph_keys and ph.field_path not in seen_field_paths:
                    matched_phs.append(ph)
                    seen_field_paths.add(ph.field_path)

            if not matched_phs:
                continue

            # Preserve existing environment.json key choice if present,
            # defaulting to displayName / tool name / entry.
            target_env_key = label
            for key in cand_keys:
                if key in existing_cat_env:
                    target_env_key = key
                    break
            else:
                for key in cand_keys:
                    if key in prev_cat_env:
                        target_env_key = key
                        break

            manifest_changed = False
            for ph in matched_phs:
                val_found, cur_val = _get_nested_env_value(data, ph.field_path)
                if not val_found:
                    continue

                is_already_placeholder = cur_val == ENV_VAR_PLACEHOLDER or (
                    ph.is_list_placeholder
                    and isinstance(cur_val, list)
                    and bool(cur_val)
                    and all(x == ENV_VAR_PLACEHOLDER for x in cur_val)
                )

                env_value_to_write: Any = None
                has_env_value_to_write = False
                if not is_already_placeholder:
                    if ph.is_list_placeholder:
                        if isinstance(cur_val, list) and cur_val:
                            env_value_to_write = cur_val
                            has_env_value_to_write = True
                    elif isinstance(cur_val, str) and cur_val.strip():
                        env_value_to_write = cur_val
                        has_env_value_to_write = True

                    restored_val: Any = (
                        [ENV_VAR_PLACEHOLDER]
                        if ph.is_list_placeholder
                        else ENV_VAR_PLACEHOLDER
                    )
                    _set_nested_value(data, ph.field_path, restored_val)
                    manifest_changed = True
                else:
                    # Native CES ENV_VAR field already exported "$env_var":
                    # preserve CES's exported environment.json value if present,
                    # otherwise backfill from pre-pull environment.json.
                    already_in_env = any(
                        _get_nested_env_value(
                            existing_cat_env.get(k), ph.field_path
                        )[0]
                        for k in cand_keys
                        if k in existing_cat_env
                    )
                    if not already_in_env:
                        for k in cand_keys:
                            if k in prev_cat_env:
                                ok, prev_v = _get_nested_env_value(
                                    prev_cat_env[k], ph.field_path
                                )
                                if ok and prev_v != ENV_VAR_PLACEHOLDER:
                                    env_value_to_write = prev_v
                                    has_env_value_to_write = True
                                    break

                if has_env_value_to_write:
                    cat_section = env_data.get(category)
                    if not isinstance(cat_section, dict):
                        cat_section = {}
                        env_data[category] = cat_section
                    res_entry = cat_section.get(target_env_key)
                    if not isinstance(res_entry, dict):
                        res_entry = {}
                        cat_section[target_env_key] = res_entry
                    _set_nested_value(
                        res_entry, ph.field_path, env_value_to_write
                    )
                    env_modified = True

                preserved.append(f"{label}.{_format_field_path(ph.field_path)}")

            if manifest_changed:
                _write_config_file(mpath, data)

    if env_modified:
        env_path.write_text(
            json.dumps(env_data, indent=2) + "\n", encoding="utf-8"
        )

    return preserved
