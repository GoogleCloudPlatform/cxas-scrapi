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

logger = logging.getLogger(__name__)

ENV_VAR_PLACEHOLDER = "$env_var"


class AppConfig:
    """Reads `app.json` (+ `environment.json`) from a pulled-app directory."""

    def __init__(
        self,
        app_data: dict[str, Any],
        env_data: dict[str, Any] | None,
        app_dir: str,
        env_path: str | None,
    ):
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
        node: Any = self._app
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return self._resolve(node, key_hint=dotted_key)

    def audio_bucket(self) -> str | None:
        """Returns the GCS bucket configured for audio recording."""
        # The Humana CFD app at humana_cfd_mvp1/.../app.json shows the path:
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
                return bool(v)
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
            if project or dataset:
                return {
                    "project": project,
                    "dataset": dataset,
                    "enabled": bool(enabled) if enabled is not None else None,
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
