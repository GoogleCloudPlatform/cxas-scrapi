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
import os

import pytest

from cxas_scrapi.utils.tracing.app_config import ENV_VAR_PLACEHOLDER, AppConfig


def _write(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f)


def _make_app_dir(tmp_path, app, env=None, env_name=None):
    _write(tmp_path / "app.json", app)
    if env is not None:
        env_filename = (
            f"environment.{env_name}.json" if env_name else "environment.json"
        )
        _write(tmp_path / env_filename, env)
    return str(tmp_path)


def test_load_missing_app_json_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="app.json not found"):
        AppConfig.load(app_dir=str(tmp_path))


def test_load_with_concrete_values(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "displayName": "My App",
            "rootAgent": "main_agent",
            "modelSettings": {"model": "gemini-3.1-flash-live"},
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://my-bucket"},
                "cloudLoggingSettings": {"enableCloudLogging": True},
                "bigqueryExportSettings": {
                    "project": "p",
                    "dataset": "d",
                    "enabled": True,
                },
                "redactionConfig": {"foo": "bar"},
            },
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() == "gs://my-bucket"
    assert cfg.cloud_logging_enabled() is True
    assert cfg.bigquery_export() == {
        "project": "p",
        "dataset": "d",
        "enabled": True,
    }
    assert cfg.model_version() == "gemini-3.1-flash-live"
    assert cfg.display_name() == "My App"
    assert cfg.root_agent() == "main_agent"
    assert cfg.redaction_config() == {"foo": "bar"}


def test_app_wrapper_key_supported(tmp_path):
    """app.json may wrap content under an `app` key — both must work."""
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "app": {
                "displayName": "Wrapped",
                "loggingSettings": {
                    "audioRecordingConfig": {"gcsBucket": "gs://wrapped"},
                    "cloudLoggingSettings": {"enableCloudLogging": False},
                    "bigqueryExportSettings": {
                        "project": "wp",
                        "dataset": "wd",
                    },
                },
                "modelSettings": {"model": "gemini-2.5-flash"},
                "rootAgent": "root_a",
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() == "gs://wrapped"
    assert cfg.cloud_logging_enabled() is False
    assert cfg.bigquery_export()["project"] == "wp"
    assert cfg.model_version() == "gemini-2.5-flash"
    assert cfg.display_name() == "Wrapped"
    assert cfg.root_agent() == "root_a"


def test_env_var_substitution(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER},
                "bigqueryExportSettings": {
                    "project": ENV_VAR_PLACEHOLDER,
                    "dataset": ENV_VAR_PLACEHOLDER,
                    "enabled": True,
                },
                "cloudLoggingSettings": {"enableCloudLogging": True},
            }
        },
        env={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://resolved"},
                "bigqueryExportSettings": {
                    "project": "envproj",
                    "dataset": "envds",
                },
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() == "gs://resolved"
    assert cfg.bigquery_export() == {
        "project": "envproj",
        "dataset": "envds",
        "enabled": True,
    }


def test_env_var_unresolved_when_no_env_file(tmp_path, caplog):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() is None


def test_env_var_unresolved_when_key_missing_in_env(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
        env={"unrelated": {"key": "value"}},
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() is None


def test_env_var_when_env_value_is_also_placeholder(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
        env={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() is None


def test_explicit_env_file_path(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
    )
    custom_env = tmp_path / "envs" / "dev.json"
    os.makedirs(custom_env.parent)
    _write(
        custom_env,
        {
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://from-explicit"}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir, env_file=str(custom_env))
    assert cfg.audio_bucket() == "gs://from-explicit"


def test_named_environment(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
        env={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://staging-bucket"}
            }
        },
        env_name="staging",
    )
    cfg = AppConfig.load(app_dir=app_dir, environment="staging")
    assert cfg.audio_bucket() == "gs://staging-bucket"


def test_named_environment_file_missing_warns(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir, environment="prod")
    assert cfg.audio_bucket() is None


def test_missing_optional_fields_return_defaults(tmp_path):
    app_dir = _make_app_dir(tmp_path, app={"displayName": "minimal"})
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() is None
    assert cfg.cloud_logging_enabled() is False
    assert cfg.bigquery_export() is None
    assert cfg.model_version() is None
    assert cfg.display_name() == "minimal"
    assert cfg.root_agent() is None
    assert cfg.redaction_config() == {}


def test_redaction_config_non_dict(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={"loggingSettings": {"redactionConfig": "not-a-dict"}},
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.redaction_config() == {}


def test_env_lookup_handles_app_key_wrapping_mismatch(tmp_path):
    """app.json may store keys at root while environment.json wraps them
    under `app.*` (or vice versa). Lookup must try both shapes."""
    app_dir = _make_app_dir(
        tmp_path,
        # app.json: root-level loggingSettings
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
            }
        },
        # environment.json: wrapped under "app"
        env={
            "app": {
                "loggingSettings": {
                    "audioRecordingConfig": {"gcsBucket": "gs://wrapped"}
                }
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() == "gs://wrapped"


def test_env_lookup_app_to_root(tmp_path):
    """Reverse direction: app.json wrapped in `app`, env.json at root."""
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "app": {
                "loggingSettings": {
                    "audioRecordingConfig": {"gcsBucket": ENV_VAR_PLACEHOLDER}
                }
            }
        },
        env={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://root"}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() == "gs://root"


def test_display_name_missing_returns_none(tmp_path):
    app_dir = _make_app_dir(tmp_path, app={"rootAgent": "ra"})
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.display_name() is None


def test_no_environment_file_no_default_present(tmp_path):
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "audioRecordingConfig": {"gcsBucket": "gs://concrete"}
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    # No env file resolved at all.
    assert cfg.env_path is None
    assert cfg.audio_bucket() == "gs://concrete"
