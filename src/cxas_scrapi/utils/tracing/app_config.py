"""Loader for a pulled CXAS app's local app.json + environment.json files.

Reads the same files `cxas pull` writes to disk and the same files `cxas push`
sends back, so a `cxas trace` user does not need to re-declare audio buckets,
Cloud Logging settings, or BigQuery export settings — they are picked up from
the agent's own configuration.

`app.json` may reference per-environment values via the literal string
`"$env_var"`. The matching key is looked up in `environment.json` (typed as
a flat key→value map). Missing keys are left as `None` and a warning is logged.
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

import json
import logging
import os
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ENV_VAR_PLACEHOLDER = "$env_var"

BOOLEAN_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "enabled",
        "enableCloudLogging",
        "enable_cloud_logging",
        "enableAudioRecording",
        "enable_audio_recording",
        "redactConversationData",
        "redact_conversation_data",
        "requiredRecordingConsent",
        "required_recording_consent",
        "ignoreUnknownFields",
        "ignore_unknown_fields",
        "includeAllFields",
        "include_all_fields",
        "enableConversationLogging",
        "enable_conversation_logging",
    }
)


def _coerce_bool(value: Any) -> bool | None:
    """Coerces a boolean or stringified boolean ('true'/'false') to a bool."""
    if isinstance(value, bool):
        return value
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
        node: Any = self._app
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        if not isinstance(node, dict):
            return None
        leaf_key = parts[-1]
        if leaf_key not in node:
            if leaf_key in BOOLEAN_CONFIG_FIELDS and self._env:
                env_val = self._lookup_env(dotted_key)
                if env_val is not None:
                    return _coerce_bool(env_val)
            return None
        resolved = self._resolve(node[leaf_key], key_hint=dotted_key)
        if leaf_key in BOOLEAN_CONFIG_FIELDS:
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


def _merge_env_booleans_into_dict(
    target: dict[str, Any], env_node: dict[str, Any]
) -> bool:
    """Recursively resolves boolean `$env_var` placeholders and boolean
    overrides from `env_node` into `target`.

    Returns True if `target` was modified.
    """
    modified = False
    for key, env_val in list(env_node.items()):
        if isinstance(env_val, dict):
            target_child = target.get(key)
            if isinstance(target_child, dict) and _merge_env_booleans_into_dict(
                target_child, env_val
            ):
                modified = True
            continue

        coerced_bool: bool | None = None
        if isinstance(env_val, bool):
            coerced_bool = env_val
        elif key in BOOLEAN_CONFIG_FIELDS or (
            target.get(key) == ENV_VAR_PLACEHOLDER
        ):
            coerced_bool = _coerce_bool(env_val)

        if coerced_bool is not None:
            env_node[key] = coerced_bool
            if (
                target.get(key) == ENV_VAR_PLACEHOLDER
                or key in BOOLEAN_CONFIG_FIELDS
                or isinstance(target.get(key), bool)
            ) and target.get(key) is not coerced_bool:
                target[key] = coerced_bool
                modified = True
    return modified


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
    app_env_sources: list[dict[str, Any]] = []
    if isinstance(env_data.get("app"), dict):
        app_env_sources.append(env_data["app"])
    app_env_sources.append(
        {
            k: v
            for k, v in env_data.items()
            if k
            not in (
                "app",
                "agents",
                "tools",
                "toolsets",
                "guardrails",
                "examples",
                "evaluations",
            )
            and isinstance(v, dict)
        }
    )

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
                for src in app_env_sources:
                    if src and _merge_env_booleans_into_dict(target_app, src):
                        app_mod = True
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
                for src in app_env_sources:
                    if src and _merge_env_booleans_into_dict(target_app, src):
                        yaml_mod = True
                if yaml_mod:
                    with open(app_yaml_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(app_yaml_data, f, sort_keys=False)
                    any_modified = True
        except Exception as e:
            logger.warning(
                f"Failed to resolve booleans in {app_yaml_path}: {e}"
            )

    # 2. Resolve into component manifests (tools, toolsets, agents, guardrails)
    for category in ("tools", "toolsets", "agents", "guardrails"):
        cat_env = env_data.get(category)
        cat_dir = os.path.join(agent_dir, category)
        if not isinstance(cat_env, dict) or not os.path.isdir(cat_dir):
            continue
        for root, _, files in os.walk(cat_dir):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                stem = os.path.splitext(fname)[0]
                parent_name = os.path.basename(root)
                spec = cat_env.get(stem) or cat_env.get(parent_name)
                if not isinstance(spec, dict):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        comp_data = json.load(f)
                    if isinstance(
                        comp_data, dict
                    ) and _merge_env_booleans_into_dict(comp_data, spec):
                        with open(fpath, "w", encoding="utf-8") as f:
                            json.dump(comp_data, f, indent=2)
                        any_modified = True
                except Exception as e:
                    logger.warning(
                        f"Failed to resolve booleans in {fpath}: {e}"
                    )

    # Write back environment.json if stringified booleans were normalized
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            json.dump(env_data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not update staged {env_path}: {e}")

    return any_modified
