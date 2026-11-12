"""Loader for a pulled CXAS app's local app.json + environment.json files.

Reads the same files `cxas pull` writes to disk and the same files `cxas push`
sends back, so a `cxas trace` user does not need to re-declare audio buckets,
Cloud Logging settings, or BigQuery export settings — they are picked up from
the agent's own configuration.

`app.json` may reference per-environment values via the literal string
`"$env_var"`. The matching key is looked up in `environment.json` (mirroring
`app.json` under `"app"` or at the root). Missing keys are left as `None` and a
warning is logged.
"""

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

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from google.cloud.ces_v1beta import types as ces_types
from google.protobuf.descriptor import Descriptor, FieldDescriptor

logger = logging.getLogger(__name__)

ENV_VAR_PLACEHOLDER = "$env_var"

_RESOURCE_DESCRIPTORS: dict[str, Descriptor] = {
    "app": ces_types.App.pb().DESCRIPTOR,
    "agents": ces_types.Agent.pb().DESCRIPTOR,
    "tools": ces_types.Tool.pb().DESCRIPTOR,
    "toolsets": ces_types.Toolset.pb().DESCRIPTOR,
    "guardrails": ces_types.Guardrail.pb().DESCRIPTOR,
    "evaluations": ces_types.Evaluation.pb().DESCRIPTOR,
    "evaluationExpectations": ces_types.EvaluationExpectation.pb().DESCRIPTOR,
}

_ENV_CATEGORY_KEYS: frozenset[str] = frozenset(
    {
        "app",
        "agents",
        "tools",
        "toolsets",
        "guardrails",
        "examples",
        "evaluations",
        "evaluationExpectations",
    }
)


def _resolve_proto_field(
    desc: Descriptor | None, key: str
) -> FieldDescriptor | None:
    """Finds a field on `desc` matching `key` (snake_case or camelCase)."""
    if desc is None or desc.full_name.startswith("google.protobuf."):
        return None
    for field in desc.fields:
        if key in (field.name, field.camelcase_name):
            return field
    return None


def _is_proto_bool_path(desc: Descriptor | None, path: tuple[str, ...]) -> bool:
    """Returns True when `path` resolves to a scalar `bool` field on `desc`."""
    if not path or desc is None:
        return False
    cur: Descriptor | None = desc
    idx = 0
    while idx < len(path) - 1:
        field = _resolve_proto_field(cur, path[idx])
        if (
            field is None
            or field.type != FieldDescriptor.TYPE_MESSAGE
            or field.message_type is None
            or field.message_type.full_name.startswith("google.protobuf.")
        ):
            return False
        if field.message_type.GetOptions().map_entry:
            idx += 1
            val_field = field.message_type.fields_by_name["value"]
            if idx == len(path) - 1:
                return val_field.type == FieldDescriptor.TYPE_BOOL
            if (
                val_field.type != FieldDescriptor.TYPE_MESSAGE
                or val_field.message_type is None
                or val_field.message_type.full_name.startswith(
                    "google.protobuf."
                )
            ):
                return False
            cur = val_field.message_type
        else:
            if field.is_repeated:
                return False
            cur = field.message_type
        idx += 1

    leaf_field = _resolve_proto_field(cur, path[-1])
    return (
        leaf_field is not None
        and not leaf_field.is_repeated
        and leaf_field.type == FieldDescriptor.TYPE_BOOL
    )


def _coerce_bool(value: Any) -> bool | None:
    """Coerces a boolean, 0/1 integer, or stringified boolean to a `bool`."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in ("true", "1", "yes"):
            return True
        if norm in ("false", "0", "no"):
            return False
    return None


class AppConfig:
    """Reads `app.json` (+ `environment.json`) from a pulled-app directory."""

    def __init__(
        self,
        app_data: dict[str, Any],
        env_data: dict[str, Any] | None,
        app_dir: str,
        env_path: str | None,
    ) -> None:
        self._app = app_data or {}
        self._env = env_data or {}
        self.app_dir = app_dir
        self.env_path = env_path

    @classmethod
    def load(
        cls,
        app_dir: str = ".",
        env_file: str | None = None,
        environment: str | None = None,
    ) -> "AppConfig":
        """Loads `app.json` and the resolved `environment.json` from disk.

        Resolution order for environment.json:
          1. `env_file` (explicit path)
          2. `environment=<name>` -> `<app_dir>/environment.<name>.json`
          3. `<app_dir>/environment.json`
          4. None (env_var placeholders left unresolved with a warning)
        """
        app_path = os.path.join(app_dir, "app.json")
        if not os.path.isfile(app_path):
            raise FileNotFoundError(
                f"app.json not found in {os.path.abspath(app_dir)}. "
                f"Run `cxas pull` first or pass --app-dir."
            )
        with open(app_path) as f:
            app_data = json.load(f)

        env_path = cls._resolve_env_path(app_dir, env_file, environment)
        env_data: dict[str, Any] | None = None
        if env_path and os.path.isfile(env_path):
            with open(env_path) as f:
                env_data = json.load(f)
        elif env_path:
            logger.warning(
                f"Environment file not found: {env_path}. "
                f"$env_var placeholders will be left unresolved."
            )

        return cls(
            app_data=app_data,
            env_data=env_data,
            app_dir=app_dir,
            env_path=env_path,
        )

    @staticmethod
    def _resolve_env_path(
        app_dir: str,
        env_file: str | None,
        environment: str | None,
    ) -> str | None:
        if env_file:
            return env_file
        if environment:
            return os.path.join(app_dir, f"environment.{environment}.json")
        default = os.path.join(app_dir, "environment.json")
        return default if os.path.exists(default) else None

    def _resolve(self, value: Any, key_hint: str | None = None) -> Any:
        """Substitutes `$env_var` placeholders using environment.json.

        The CXAS convention stores the literal string "$env_var" in app.json
        and looks up the matching field name in environment.json. The matching
        is done by the JSON-path key (e.g. `loggingSettings.audioRecordingConfig
        .gcsBucket`) — environment.json mirrors the structure of app.json,
        with concrete values where app.json had placeholders.
        """
        if value != ENV_VAR_PLACEHOLDER:
            return value
        if not self._env or not key_hint:
            logger.warning(
                f"Cannot resolve $env_var for {key_hint}: no environment "
                f"file loaded."
            )
            return None
        resolved = self._lookup_env(key_hint)
        if resolved is None:
            logger.warning(
                f"$env_var for {key_hint} not present in {self.env_path}."
            )
        return resolved

    def _lookup_env(self, dotted_key: str) -> Any:
        # `app.json` may store keys at the root (`loggingSettings.*`) while
        # `environment.json` wraps the same tree under `app.*`, or vice versa.
        # Try the literal key first, then with/without an `app.` prefix.
        candidates = [dotted_key]
        if dotted_key.startswith("app."):
            candidates.append(dotted_key[len("app.") :])
        else:
            candidates.append(f"app.{dotted_key}")
        for key in candidates:
            node: Any = self._env
            ok = True
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    ok = False
                    break
                node = node[part]
            if ok and node != ENV_VAR_PLACEHOLDER:
                return node
        return None

    def _get(self, dotted_key: str) -> Any:
        parts = dotted_key.split(".")
        if parts[0] == "app":
            if not isinstance(self._app.get("app"), dict):
                return None
            node: Any = self._app["app"]
            rel_parts = tuple(parts[1:])
        else:
            if (
                isinstance(self._app.get("app"), dict)
                and parts[0] not in self._app
            ):
                return None
            node = self._app
            rel_parts = tuple(parts)

        if not rel_parts:
            return None

        is_bool_field = _is_proto_bool_path(
            _RESOURCE_DESCRIPTORS["app"], rel_parts
        )

        missing = False
        for part in rel_parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                missing = True
                break
            node = node[part]

        leaf_key = rel_parts[-1]
        if missing or not isinstance(node, dict) or leaf_key not in node:
            if is_bool_field and self._env:
                env_val = self._lookup_env(dotted_key)
                if env_val is not None:
                    return _coerce_bool(env_val)
            return None

        resolved = self._resolve(node[leaf_key], key_hint=dotted_key)
        if is_bool_field:
            if self._env:
                env_override = self._lookup_env(dotted_key)
                if env_override is not None:
                    coerced = _coerce_bool(env_override)
                    if coerced is not None:
                        return coerced
            coerced_res = _coerce_bool(resolved)
            if coerced_res is not None:
                return coerced_res
        return resolved

    def audio_bucket(self) -> str | None:
        """Returns the GCS bucket configured for audio recording."""
        # Apps store the bucket at
        # app.loggingSettings.audioRecordingConfig.gcsBucket. Some apps wrap
        # the whole app config under an "app" key; support both.
        for prefix in ("app.", ""):
            v = self._get(
                f"{prefix}loggingSettings.audioRecordingConfig.gcsBucket"
            )
            if v:
                return v
        return None

    def cloud_logging_enabled(self) -> bool:
        for prefix in ("app.", ""):
            v = self._get(
                f"{prefix}loggingSettings.cloudLoggingSettings."
                f"enableCloudLogging"
            )
            if v is not None:
                coerced = _coerce_bool(v)
                return coerced if coerced is not None else bool(v)
        return False

    def bigquery_export(self) -> dict[str, Any] | None:
        for prefix in ("app.", ""):
            project = self._get(
                f"{prefix}loggingSettings.bigqueryExportSettings.project"
            )
            dataset = self._get(
                f"{prefix}loggingSettings.bigqueryExportSettings.dataset"
            )
            enabled = self._get(
                f"{prefix}loggingSettings.bigqueryExportSettings.enabled"
            )
            if project or dataset or enabled is not None:
                coerced_enabled = (
                    _coerce_bool(enabled) if enabled is not None else None
                )
                return {
                    "project": project,
                    "dataset": dataset,
                    "enabled": (
                        coerced_enabled
                        if coerced_enabled is not None
                        else (bool(enabled) if enabled is not None else None)
                    ),
                }
        return None

    def model_version(self) -> str | None:
        for prefix in ("app.", ""):
            v = self._get(f"{prefix}modelSettings.model")
            if v:
                return v
        return None

    def display_name(self) -> str | None:
        for prefix in ("app.", ""):
            v = self._get(f"{prefix}displayName")
            if v:
                return v
        return None

    def root_agent(self) -> str | None:
        for prefix in ("app.", ""):
            v = self._get(f"{prefix}rootAgent")
            if v:
                return v
        return None

    def redaction_config(self) -> dict[str, Any]:
        for prefix in ("app.", ""):
            v = self._get(f"{prefix}loggingSettings.redactionConfig")
            if v is not None:
                return v if isinstance(v, dict) else {}
        return {}


_APP_VAR_DECL_KEYS: frozenset[str] = frozenset(
    {"variableDeclarations", "variable_declarations"}
)

_COMPONENT_CATEGORIES: tuple[str, ...] = (
    "tools",
    "toolsets",
    "agents",
    "guardrails",
    "evaluations",
    "evaluationExpectations",
)


def _merge_env_booleans_into_dict(
    target: dict[str, Any],
    env_node: dict[str, Any],
    desc: Descriptor,
    skip_keys: frozenset[str] = frozenset(),
) -> tuple[bool, bool]:
    """Recursively resolves boolean `$env_var` placeholders and boolean
    overrides from `env_node` into `target` guided by protobuf `desc`.

    Returns `(target_modified, env_modified)`.
    """
    target_modified = False
    env_modified = False

    for key, env_val in list(env_node.items()):
        if key in skip_keys:
            continue
        field = _resolve_proto_field(desc, key)
        if field is None:
            continue

        target_key = next(
            (k for k in (key, field.camelcase_name, field.name) if k in target),
            key,
        )

        if field.type == FieldDescriptor.TYPE_BOOL and not field.is_repeated:
            coerced_bool = _coerce_bool(env_val)
            if coerced_bool is not None:
                if env_node[key] is not coerced_bool:
                    env_node[key] = coerced_bool
                    env_modified = True
                if target.get(target_key) is not coerced_bool:
                    target[target_key] = coerced_bool
                    target_modified = True
            continue

        if (
            field.type != FieldDescriptor.TYPE_MESSAGE
            or field.message_type is None
            or field.message_type.full_name.startswith("google.protobuf.")
        ):
            continue

        if field.message_type.GetOptions().map_entry and isinstance(
            env_val, dict
        ):
            val_field = field.message_type.fields_by_name["value"]
            target_map = target.get(target_key)
            if val_field.type == FieldDescriptor.TYPE_BOOL and isinstance(
                target_map, dict
            ):
                for map_key, map_env_val in list(env_val.items()):
                    coerced_bool = _coerce_bool(map_env_val)
                    if coerced_bool is not None:
                        if env_val[map_key] is not coerced_bool:
                            env_val[map_key] = coerced_bool
                            env_modified = True
                        if target_map.get(map_key) is not coerced_bool:
                            target_map[map_key] = coerced_bool
                            target_modified = True
            elif (
                val_field.type == FieldDescriptor.TYPE_MESSAGE
                and val_field.message_type is not None
                and not val_field.message_type.full_name.startswith(
                    "google.protobuf."
                )
            ):
                for map_key, map_env_val in env_val.items():
                    if not isinstance(map_env_val, dict):
                        continue
                    if isinstance(target_map, dict) and isinstance(
                        target_map.get(map_key), dict
                    ):
                        t_mod, e_mod = _merge_env_booleans_into_dict(
                            target_map[map_key],
                            map_env_val,
                            val_field.message_type,
                        )
                        target_modified |= t_mod
                        env_modified |= e_mod
            continue

        if field.is_repeated:
            target_list = target.get(target_key)
            if isinstance(env_val, list) and isinstance(target_list, list):
                named_targets = {
                    t_item["name"]: t_item
                    for t_item in target_list
                    if isinstance(t_item, dict)
                    and isinstance(t_item.get("name"), str)
                    and t_item["name"]
                }
                if named_targets and any(
                    isinstance(e_item, dict)
                    and isinstance(e_item.get("name"), str)
                    for e_item in env_val
                ):
                    pairs = [
                        (named_targets[e_item["name"]], e_item)
                        for e_item in env_val
                        if isinstance(e_item, dict)
                        and isinstance(e_item.get("name"), str)
                        and e_item["name"] in named_targets
                    ]
                else:
                    pairs = [
                        (t_item, e_item)
                        for t_item, e_item in zip(
                            target_list, env_val, strict=False
                        )
                        if isinstance(t_item, dict) and isinstance(e_item, dict)
                    ]
                for t_item, e_item in pairs:
                    t_mod, e_mod = _merge_env_booleans_into_dict(
                        t_item, e_item, field.message_type
                    )
                    target_modified |= t_mod
                    env_modified |= e_mod
            continue

        if isinstance(env_val, dict):
            existing_child = target.get(target_key)
            if isinstance(existing_child, dict):
                t_mod, e_mod = _merge_env_booleans_into_dict(
                    existing_child, env_val, field.message_type
                )
                target_modified |= t_mod
                env_modified |= e_mod
            elif target_key not in target or existing_child is None:
                sub_target: dict[str, Any] = {}
                t_mod, e_mod = _merge_env_booleans_into_dict(
                    sub_target, env_val, field.message_type
                )
                env_modified |= e_mod
                if t_mod and sub_target:
                    target[target_key] = sub_target
                    target_modified = True

    return target_modified, env_modified


def _dict_has_bool_placeholders(
    target: dict[str, Any],
    desc: Descriptor,
    skip_keys: frozenset[str] = frozenset(),
) -> bool:
    """Returns True if `target` has any `"$env_var"` on a proto `bool` field."""
    for key, val in target.items():
        if key in skip_keys:
            continue
        field = _resolve_proto_field(desc, key)
        if field is None:
            continue
        if (
            field.type == FieldDescriptor.TYPE_BOOL
            and not field.is_repeated
            and val == ENV_VAR_PLACEHOLDER
        ):
            return True
        if (
            field.type == FieldDescriptor.TYPE_MESSAGE
            and field.message_type is not None
            and not field.message_type.full_name.startswith("google.protobuf.")
        ):
            if field.message_type.GetOptions().map_entry and isinstance(
                val, dict
            ):
                val_field = field.message_type.fields_by_name["value"]
                if val_field.type == FieldDescriptor.TYPE_BOOL:
                    if any(v == ENV_VAR_PLACEHOLDER for v in val.values()):
                        return True
                elif (
                    val_field.type == FieldDescriptor.TYPE_MESSAGE
                    and val_field.message_type is not None
                ):
                    for map_val in val.values():
                        if isinstance(
                            map_val, dict
                        ) and _dict_has_bool_placeholders(
                            map_val, val_field.message_type
                        ):
                            return True
            elif field.is_repeated and isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and _dict_has_bool_placeholders(
                        item, field.message_type
                    ):
                        return True
            elif not field.is_repeated and isinstance(val, dict):
                if _dict_has_bool_placeholders(val, field.message_type):
                    return True
    return False


def _iter_component_manifests(cat_dir: str) -> list[tuple[str, str, bool]]:
    """Yields `(entry_name, file_path, is_yaml)` under a category directory."""
    manifests: list[tuple[str, str, bool]] = []
    if not os.path.isdir(cat_dir):
        return manifests
    for entry in sorted(os.listdir(cat_dir)):
        entry_path = os.path.join(cat_dir, entry)
        if os.path.isdir(entry_path):
            for ext in (".json", ".yaml", ".yml"):
                fpath = os.path.join(entry_path, f"{entry}{ext}")
                if os.path.isfile(fpath):
                    manifests.append((entry, fpath, ext in (".yaml", ".yml")))
        elif os.path.isfile(entry_path):
            stem, ext = os.path.splitext(entry)
            if ext in (".json", ".yaml", ".yml") and stem:
                manifests.append((stem, entry_path, ext in (".yaml", ".yml")))
    return manifests


def has_bool_env_var_placeholders(agent_dir: str) -> bool:
    """Returns True if any staged manifest in `agent_dir` has a boolean
    `"$env_var"` placeholder.
    """
    app_desc = _RESOURCE_DESCRIPTORS["app"]
    for fname, is_yaml in (("app.json", False), ("app.yaml", True)):
        fpath = os.path.join(agent_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                data = yaml.safe_load(f) if is_yaml else json.load(f)
            if isinstance(data, dict):
                target = (
                    data["app"] if isinstance(data.get("app"), dict) else data
                )
                if _dict_has_bool_placeholders(
                    target, app_desc, skip_keys=_APP_VAR_DECL_KEYS
                ):
                    return True
        except Exception:
            continue

    for category in _COMPONENT_CATEGORIES:
        cat_dir = os.path.join(agent_dir, category)
        cat_desc = _RESOURCE_DESCRIPTORS[category]
        for _, fpath, is_yaml in _iter_component_manifests(cat_dir):
            try:
                with open(fpath, encoding="utf-8") as f:
                    comp_data = yaml.safe_load(f) if is_yaml else json.load(f)
                if isinstance(comp_data, dict) and _dict_has_bool_placeholders(
                    comp_data, cat_desc
                ):
                    return True
            except Exception:
                continue
    return False


def _replace_bool_placeholders_with_default(
    target: dict[str, Any], desc: Descriptor
) -> None:
    """Replaces literal `"$env_var"` on proto `bool` fields with `False`."""
    for key, val in list(target.items()):
        field = _resolve_proto_field(desc, key)
        if field is None:
            continue
        if (
            field.type == FieldDescriptor.TYPE_BOOL
            and not field.is_repeated
            and val == ENV_VAR_PLACEHOLDER
        ):
            target[key] = False
        elif (
            field.type == FieldDescriptor.TYPE_MESSAGE
            and field.message_type is not None
            and not field.message_type.full_name.startswith("google.protobuf.")
        ):
            if field.message_type.GetOptions().map_entry and isinstance(
                val, dict
            ):
                val_field = field.message_type.fields_by_name["value"]
                if val_field.type == FieldDescriptor.TYPE_BOOL:
                    for map_key, map_val in list(val.items()):
                        if map_val == ENV_VAR_PLACEHOLDER:
                            val[map_key] = False
                elif (
                    val_field.type == FieldDescriptor.TYPE_MESSAGE
                    and val_field.message_type is not None
                ):
                    for map_val in val.values():
                        if isinstance(map_val, dict):
                            _replace_bool_placeholders_with_default(
                                map_val, val_field.message_type
                            )
            elif field.is_repeated and isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        _replace_bool_placeholders_with_default(
                            item, field.message_type
                        )
            elif not field.is_repeated and isinstance(val, dict):
                _replace_bool_placeholders_with_default(val, field.message_type)


def resolve_env_booleans_for_lint(
    data: dict[str, Any],
    category: str,
    resource_name: str,
    app_root: Path,
    env_file: Path | None = None,
) -> None:
    """Resolves boolean `$env_var` placeholders in-memory for `cxas lint`.

    When `env_file` or `environment.json` is present, resolves boolean values
    from it so invalid or missing values are still caught. When no
    `environment.json` exists on disk (e.g. supplied via `--env-file` at push
    time), substitutes `False` for literal `"$env_var"` on proto `bool` fields.
    """
    desc = _RESOURCE_DESCRIPTORS.get(category)
    if desc is None or not isinstance(data, dict):
        return

    env_path = (
        env_file
        if env_file is not None and env_file.is_file()
        else app_root / "environment.json"
    )
    if not env_path.is_file():
        _replace_bool_placeholders_with_default(data, desc)
        return

    try:
        with open(env_path, encoding="utf-8") as f:
            env_data = json.load(f)
    except Exception:
        return

    if not isinstance(env_data, dict):
        return

    env_copy = copy.deepcopy(env_data)
    if category == "app":
        target_app = data["app"] if isinstance(data.get("app"), dict) else data
        if isinstance(env_copy.get("app"), dict):
            _merge_env_booleans_into_dict(
                target_app,
                env_copy["app"],
                desc,
                skip_keys=_APP_VAR_DECL_KEYS,
            )
        _merge_env_booleans_into_dict(
            target_app,
            env_copy,
            desc,
            skip_keys=_ENV_CATEGORY_KEYS | _APP_VAR_DECL_KEYS,
        )
    else:
        cat_env = env_copy.get(category)
        if not isinstance(cat_env, dict):
            return
        disp = data.get("displayName") or data.get("display_name")
        spec = (
            cat_env.get(disp) if isinstance(disp, str) and disp else None
        ) or cat_env.get(resource_name)
        if isinstance(spec, dict):
            _merge_env_booleans_into_dict(data, spec, desc)


def resolve_env_booleans_in_dir(agent_dir: str) -> bool:
    """Resolves boolean `$env_var` placeholders and boolean overrides from
    `environment.json` into staged resource manifests inside `agent_dir`.

    CES `ImportApp` parses `app.json` (and component manifests) into protobuf
    messages before resolving `$env_var` string fields, so `"$env_var"` on a
    protobuf `bool` field fails proto parsing unless resolved to a native JSON
    boolean prior to archive creation.
    """
    env_path = os.path.join(agent_dir, "environment.json")
    if not os.path.isfile(env_path):
        return False

    try:
        with open(env_path, encoding="utf-8") as f:
            env_data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to parse {env_path}: {e}")
        return False

    if not isinstance(env_data, dict):
        return False

    any_modified = False
    env_modified = False
    app_desc = _RESOURCE_DESCRIPTORS["app"]

    # 1. Resolve into app.json / app.yaml
    app_json_path = os.path.join(agent_dir, "app.json")
    if os.path.isfile(app_json_path):
        try:
            with open(app_json_path, encoding="utf-8") as f:
                app_data = json.load(f)
            if isinstance(app_data, dict):
                target_app = (
                    app_data["app"]
                    if isinstance(app_data.get("app"), dict)
                    else app_data
                )
                app_mod = False
                if isinstance(env_data.get("app"), dict):
                    t_mod, e_mod = _merge_env_booleans_into_dict(
                        target_app,
                        env_data["app"],
                        app_desc,
                        skip_keys=_APP_VAR_DECL_KEYS,
                    )
                    app_mod |= t_mod
                    env_modified |= e_mod
                t_mod, e_mod = _merge_env_booleans_into_dict(
                    target_app,
                    env_data,
                    app_desc,
                    skip_keys=_ENV_CATEGORY_KEYS | _APP_VAR_DECL_KEYS,
                )
                app_mod |= t_mod
                env_modified |= e_mod
                if app_mod:
                    with open(app_json_path, "w", encoding="utf-8") as f:
                        json.dump(app_data, f, indent=2)
                    any_modified = True
        except Exception as e:
            logger.warning(
                f"Failed to resolve booleans in {app_json_path}: {e}"
            )

    app_yaml_path = os.path.join(agent_dir, "app.yaml")
    if os.path.isfile(app_yaml_path):
        try:
            with open(app_yaml_path, encoding="utf-8") as f:
                app_yaml_data = yaml.safe_load(f)
            if isinstance(app_yaml_data, dict):
                target_app = (
                    app_yaml_data["app"]
                    if isinstance(app_yaml_data.get("app"), dict)
                    else app_yaml_data
                )
                yaml_mod = False
                if isinstance(env_data.get("app"), dict):
                    t_mod, e_mod = _merge_env_booleans_into_dict(
                        target_app,
                        env_data["app"],
                        app_desc,
                        skip_keys=_APP_VAR_DECL_KEYS,
                    )
                    yaml_mod |= t_mod
                    env_modified |= e_mod
                t_mod, e_mod = _merge_env_booleans_into_dict(
                    target_app,
                    env_data,
                    app_desc,
                    skip_keys=_ENV_CATEGORY_KEYS | _APP_VAR_DECL_KEYS,
                )
                yaml_mod |= t_mod
                env_modified |= e_mod
                if yaml_mod:
                    with open(app_yaml_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(app_yaml_data, f, sort_keys=False)
                    any_modified = True
        except Exception as e:
            logger.warning(
                f"Failed to resolve booleans in {app_yaml_path}: {e}"
            )

    # 2. Resolve into component manifests (nested or flat)
    for category in _COMPONENT_CATEGORIES:
        cat_env = env_data.get(category)
        cat_dir = os.path.join(agent_dir, category)
        cat_desc = _RESOURCE_DESCRIPTORS[category]
        if not isinstance(cat_env, dict):
            continue
        for entry, fpath, is_yaml in _iter_component_manifests(cat_dir):
            try:
                with open(fpath, encoding="utf-8") as f:
                    comp_data = yaml.safe_load(f) if is_yaml else json.load(f)
                if not isinstance(comp_data, dict):
                    continue
                disp = comp_data.get("displayName") or comp_data.get(
                    "display_name"
                )
                spec = (
                    cat_env.get(disp)
                    if isinstance(disp, str) and disp
                    else None
                ) or cat_env.get(entry)
                if not isinstance(spec, dict):
                    continue
                t_mod, e_mod = _merge_env_booleans_into_dict(
                    comp_data, spec, cat_desc
                )
                env_modified |= e_mod
                if t_mod:
                    with open(fpath, "w", encoding="utf-8") as f:
                        if is_yaml:
                            yaml.safe_dump(comp_data, f, sort_keys=False)
                        else:
                            json.dump(comp_data, f, indent=2)
                    any_modified = True
            except Exception as e:
                logger.warning(f"Failed to resolve booleans in {fpath}: {e}")

    # Write back environment.json only if stringified/int booleans normalized
    if env_modified:
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                json.dump(env_data, f, indent=2)
            any_modified = True
        except Exception as e:
            logger.debug(f"Could not update staged {env_path}: {e}")

    return any_modified
