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
import typing

import pytest

from cxas_scrapi.utils.tracing.app_config import (
    ENV_VAR_PLACEHOLDER,
    AppConfig,
    resolve_env_booleans_in_dir,
)


def _write(path: typing.Any, payload: typing.Any) -> None:
    with open(path, "w") as f:
        json.dump(payload, f)


def _make_app_dir(
    tmp_path: typing.Any,
    app: typing.Any,
    env: typing.Any = None,
    env_name: typing.Any = None,
) -> typing.Any:
    _write(tmp_path / "app.json", app)
    if env is not None:
        env_filename = (
            f"environment.{env_name}.json" if env_name else "environment.json"
        )
        _write(tmp_path / env_filename, env)
    return str(tmp_path)


def test_load_missing_app_json_raises(tmp_path: typing.Any) -> None:
    with pytest.raises(FileNotFoundError, match=r"app\.json not found"):
        AppConfig.load(app_dir=str(tmp_path))


def test_load_with_concrete_values(tmp_path: typing.Any) -> None:
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


def test_app_wrapper_key_supported(tmp_path: typing.Any) -> None:
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


def test_env_var_substitution(tmp_path: typing.Any) -> None:
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


def test_env_var_unresolved_when_no_env_file(
    tmp_path: typing.Any, caplog: typing.Any
) -> None:
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


def test_env_var_unresolved_when_key_missing_in_env(
    tmp_path: typing.Any,
) -> None:
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


def test_env_var_when_env_value_is_also_placeholder(
    tmp_path: typing.Any,
) -> None:
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


def test_explicit_env_file_path(tmp_path: typing.Any) -> None:
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


def test_named_environment(tmp_path: typing.Any) -> None:
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


def test_named_environment_file_missing_warns(tmp_path: typing.Any) -> None:
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


def test_missing_optional_fields_return_defaults(tmp_path: typing.Any) -> None:
    app_dir = _make_app_dir(tmp_path, app={"displayName": "minimal"})
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.audio_bucket() is None
    assert cfg.cloud_logging_enabled() is False
    assert cfg.bigquery_export() is None
    assert cfg.model_version() is None
    assert cfg.display_name() == "minimal"
    assert cfg.root_agent() is None
    assert cfg.redaction_config() == {}


def test_redaction_config_non_dict(tmp_path: typing.Any) -> None:
    app_dir = _make_app_dir(
        tmp_path,
        app={"loggingSettings": {"redactionConfig": "not-a-dict"}},
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.redaction_config() == {}


def test_env_lookup_handles_app_key_wrapping_mismatch(
    tmp_path: typing.Any,
) -> None:
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


def test_env_lookup_app_to_root(tmp_path: typing.Any) -> None:
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


def test_display_name_missing_returns_none(tmp_path: typing.Any) -> None:
    app_dir = _make_app_dir(tmp_path, app={"rootAgent": "ra"})
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.display_name() is None


def test_no_environment_file_no_default_present(tmp_path: typing.Any) -> None:
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


def test_boolean_env_var_substitution_and_coercion(
    tmp_path: typing.Any,
) -> None:
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "bigqueryExportSettings": {
                    "project": ENV_VAR_PLACEHOLDER,
                    "dataset": ENV_VAR_PLACEHOLDER,
                    "enabled": ENV_VAR_PLACEHOLDER,
                },
                "cloudLoggingSettings": {
                    "enableCloudLogging": ENV_VAR_PLACEHOLDER,
                },
            }
        },
        env={
            "loggingSettings": {
                "bigqueryExportSettings": {
                    "project": "my-proj",
                    "dataset": "my-ds",
                    "enabled": "false",
                },
                "cloudLoggingSettings": {
                    "enableCloudLogging": "true",
                },
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.cloud_logging_enabled() is True
    assert cfg.bigquery_export() == {
        "project": "my-proj",
        "dataset": "my-ds",
        "enabled": False,
    }


def test_boolean_env_override_without_placeholder(
    tmp_path: typing.Any,
) -> None:
    """When app.json has a concrete bool (`true`), environment.json can override
    it per environment (e.g. `false` in dev)."""
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "loggingSettings": {
                "bigqueryExportSettings": {
                    "project": "p",
                    "dataset": "d",
                    "enabled": True,
                },
                "cloudLoggingSettings": {
                    "enableCloudLogging": True,
                },
            }
        },
        env={
            "app": {
                "loggingSettings": {
                    "bigqueryExportSettings": {
                        "enabled": False,
                    },
                    "cloudLoggingSettings": {
                        "enableCloudLogging": False,
                    },
                }
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.cloud_logging_enabled() is False
    assert cfg.bigquery_export() == {
        "project": "p",
        "dataset": "d",
        "enabled": False,
    }


def test_resolve_env_booleans_in_dir_updates_app_and_tool_manifests(
    tmp_path: typing.Any,
) -> None:
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "displayName": "Test App",
            "loggingSettings": {
                "bigqueryExportSettings": {
                    "project": ENV_VAR_PLACEHOLDER,
                    "dataset": ENV_VAR_PLACEHOLDER,
                    "enabled": ENV_VAR_PLACEHOLDER,
                },
                "cloudLoggingSettings": {
                    "enableCloudLogging": True,
                },
            },
        },
        env={
            "app": {
                "loggingSettings": {
                    "bigqueryExportSettings": {
                        "project": "prod-proj",
                        "dataset": "prod-ds",
                        "enabled": "false",
                    },
                    "cloudLoggingSettings": {
                        "enableCloudLogging": False,
                    },
                }
            },
            "tools": {
                "lookup_order": {
                    "openApiTool": {
                        "ignoreUnknownFields": "true",
                    }
                }
            },
        },
    )
    tools_dir = tmp_path / "tools" / "lookup_order"
    tools_dir.mkdir(parents=True)
    tool_json = tools_dir / "lookup_order.json"
    _write(
        tool_json,
        {
            "displayName": "lookup_order",
            "openApiTool": {
                "url": ENV_VAR_PLACEHOLDER,
                "ignoreUnknownFields": ENV_VAR_PLACEHOLDER,
            },
        },
    )

    modified = resolve_env_booleans_in_dir(app_dir)
    assert modified is True

    with open(tmp_path / "app.json") as f:
        updated_app = json.load(f)
    bq = updated_app["loggingSettings"]["bigqueryExportSettings"]
    assert bq["enabled"] is False
    # String $env_var placeholders remain untouched for server-side resolution
    assert bq["project"] == ENV_VAR_PLACEHOLDER
    assert (
        updated_app["loggingSettings"]["cloudLoggingSettings"][
            "enableCloudLogging"
        ]
        is False
    )

    with open(tool_json) as f:
        updated_tool = json.load(f)
    assert updated_tool["openApiTool"]["ignoreUnknownFields"] is True
    assert updated_tool["openApiTool"]["url"] == ENV_VAR_PLACEHOLDER


def test_proto_bool_descriptors_full_paths_and_non_bool_untouched(
    tmp_path: typing.Any,
) -> None:
    """Proto bool fields (pinned, locked, disableConversationLogging,
    enableRedaction) resolve, while string $env_var and defaultVariables maps
    are untouched."""
    app_dir = _make_app_dir(
        tmp_path,
        app={
            "displayName": "Strict Paths App",
            "description": ENV_VAR_PLACEHOLDER,
            "pinned": ENV_VAR_PLACEHOLDER,
            "locked": ENV_VAR_PLACEHOLDER,
            "defaultVariables": {"feature": {"enabled": "yes"}},
            "loggingSettings": {
                "conversationLoggingSettings": {
                    "disableConversationLogging": ENV_VAR_PLACEHOLDER
                },
                "redactionConfig": {"enableRedaction": ENV_VAR_PLACEHOLDER},
            },
        },
        env={
            "description": "false",
            "pinned": "false",
            "locked": 1,
            "defaultVariables": {"feature": {"enabled": "yes"}},
            "loggingSettings": {
                "conversationLoggingSettings": {
                    "disableConversationLogging": "true"
                },
                "redactionConfig": {"enableRedaction": 0},
            },
        },
    )

    assert resolve_env_booleans_in_dir(app_dir) is True

    with open(tmp_path / "app.json") as f:
        updated_app = json.load(f)
    with open(tmp_path / "environment.json") as f:
        updated_env = json.load(f)

    assert updated_app["pinned"] is False
    assert updated_app["locked"] is True
    assert (
        updated_app["loggingSettings"]["conversationLoggingSettings"][
            "disableConversationLogging"
        ]
        is True
    )
    assert (
        updated_app["loggingSettings"]["redactionConfig"]["enableRedaction"]
        is False
    )
    # String field with $env_var and env "false" must NOT be coerced to bool
    assert updated_app["description"] == ENV_VAR_PLACEHOLDER
    assert updated_env["description"] == "false"
    # User variable map under defaultVariables must NOT be coerced
    assert updated_app["defaultVariables"]["feature"]["enabled"] == "yes"
    assert updated_env["defaultVariables"]["feature"]["enabled"] == "yes"


def test_missing_parent_dict_materialized_and_read_by_app_config(
    tmp_path: typing.Any,
) -> None:
    """When app.json omits cloudLoggingSettings, env override still applies."""
    app_dir = _make_app_dir(
        tmp_path,
        app={"displayName": "No Logging Parent"},
        env={
            "app": {
                "loggingSettings": {
                    "cloudLoggingSettings": {"enableCloudLogging": "true"},
                    "audioRecordingConfig": {"gcsBucket": "gs://only-env"},
                }
            }
        },
    )
    cfg = AppConfig.load(app_dir=app_dir)
    assert cfg.cloud_logging_enabled() is True

    assert resolve_env_booleans_in_dir(app_dir) is True
    with open(tmp_path / "app.json") as f:
        updated_app = json.load(f)
    assert (
        updated_app["loggingSettings"]["cloudLoggingSettings"][
            "enableCloudLogging"
        ]
        is True
    )
    # Non-boolean missing parent is not materialized
    assert "audioRecordingConfig" not in updated_app["loggingSettings"]


def test_yaml_components_display_name_lookup_and_no_nested_walk(
    tmp_path: typing.Any,
) -> None:
    """Component lookup supports YAML, keys by displayName first, skips nested
    files, and avoids unnecessary writes when nothing changed."""
    import yaml  # noqa: PLC0415

    app_dir = _make_app_dir(
        tmp_path,
        app={"displayName": "Comp App"},
        env={
            "tools": {
                "Lookup Order": {"openApiTool": {"ignoreUnknownFields": "true"}}
            },
            "guardrails": {"safety_guard": {"enabled": 0}},
        },
    )
    # JSON tool keyed by displayName ("Lookup Order" != dir "lookup_order")
    tool_dir = tmp_path / "tools" / "lookup_order"
    tool_dir.mkdir(parents=True)
    _write(
        tool_dir / "lookup_order.json",
        {
            "displayName": "Lookup Order",
            "openApiTool": {"ignoreUnknownFields": ENV_VAR_PLACEHOLDER},
        },
    )
    # YAML guardrail
    gr_dir = tmp_path / "guardrails" / "safety_guard"
    gr_dir.mkdir(parents=True)
    gr_yaml = gr_dir / "safety_guard.yaml"
    gr_yaml.write_text(
        yaml.safe_dump(
            {"displayName": "Safety Guard", "enabled": ENV_VAR_PLACEHOLDER},
            sort_keys=False,
        )
    )
    # Nested schema JSON under toolsets/ must NOT be touched
    nested_dir = tmp_path / "toolsets" / "ts" / "schema"
    nested_dir.mkdir(parents=True)
    nested_json = nested_dir / "enabled_flags.json"
    _write(nested_json, {"enabled": "yes"})
    env_data = json.loads((tmp_path / "environment.json").read_text())
    env_data["toolsets"] = {"enabled_flags": {"enabled": "true"}}
    _write(tmp_path / "environment.json", env_data)

    assert resolve_env_booleans_in_dir(app_dir) is True

    updated_tool = json.loads((tool_dir / "lookup_order.json").read_text())
    assert updated_tool["openApiTool"]["ignoreUnknownFields"] is True
    updated_gr = yaml.safe_load(gr_yaml.read_text())
    assert updated_gr["enabled"] is False
    assert json.loads(nested_json.read_text()) == {"enabled": "yes"}

    # Second run with already-normalized booleans must not rewrite files
    env_mtime_before = (tmp_path / "environment.json").stat().st_mtime_ns
    os.utime(
        tmp_path / "environment.json",
        ns=(env_mtime_before - 10_000_000, env_mtime_before - 10_000_000),
    )
    mtime_expected = (tmp_path / "environment.json").stat().st_mtime_ns
    assert resolve_env_booleans_in_dir(app_dir) is False
    assert (tmp_path / "environment.json").stat().st_mtime_ns == mtime_expected


def test_schema_lint_accepts_boolean_env_var_placeholders(
    tmp_path: typing.Any,
) -> None:
    """cxas lint V001/V003/V005 accepts boolean $env_var placeholders."""
    from cxas_scrapi.utils.linter import (  # noqa: PLC0415
        Discovery,
        LintConfig,
        build_context,
        build_registry,
    )

    _write(
        tmp_path / "app.json",
        {
            "name": "test_app",
            "displayName": "Test App",
            "pinned": ENV_VAR_PLACEHOLDER,
            "loggingSettings": {
                "bigqueryExportSettings": {
                    "project": ENV_VAR_PLACEHOLDER,
                    "dataset": ENV_VAR_PLACEHOLDER,
                    "enabled": ENV_VAR_PLACEHOLDER,
                }
            },
        },
    )
    tool_dir = tmp_path / "tools" / "lookup_order"
    tool_dir.mkdir(parents=True)
    _write(
        tool_dir / "lookup_order.json",
        {
            "displayName": "Lookup Order",
            "openApiTool": {
                "openApiSchema": (
                    "openapi: 3.0.0\ninfo:\n  title: T\n  version: '1'\n"
                    "paths: {}\n"
                ),
                "ignoreUnknownFields": ENV_VAR_PLACEHOLDER,
            },
        },
    )

    reg = build_registry()
    v001 = next(r for r in reg.all_rules() if r.id == "V001")
    v003 = next(r for r in reg.all_rules() if r.id == "V003")
    ctx = build_context(
        tmp_path, LintConfig(), Discovery(tmp_path, tmp_path / "evals")
    )

    # 1. Without environment.json (external --env-file workflow): passes
    assert v001.check(tmp_path / "app.json", "", ctx) == []
    assert v003.check(tool_dir / "lookup_order.json", "", ctx) == []

    # 2. With valid environment.json keyed by displayName: passes
    _write(
        tmp_path / "environment.json",
        {
            "app": {
                "pinned": "false",
                "loggingSettings": {
                    "bigqueryExportSettings": {"enabled": "true"}
                },
            },
            "tools": {
                "Lookup Order": {"openApiTool": {"ignoreUnknownFields": "true"}}
            },
        },
    )
    assert v001.check(tmp_path / "app.json", "", ctx) == []
    assert v003.check(tool_dir / "lookup_order.json", "", ctx) == []

    # 3. With invalid boolean in environment.json: V001 catches it!
    _write(
        tmp_path / "environment.json",
        {"app": {"pinned": "not-a-bool"}},
    )
    errs = v001.check(tmp_path / "app.json", "", ctx)
    assert len(errs) == 1
    assert "Proto schema validation failed" in errs[0].message
