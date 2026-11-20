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

"""Unit tests for environment variable resolution and validation."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.env_vars import (
    ENV_VAR_PLACEHOLDER,
    collect_env_var_connector_toolsets,
    collect_env_var_declaration_names,
    extract_env_connector_toolset_connections,
    extract_env_variable_defaults,
    parse_env_variable_declarations,
    preserve_connector_toolset_env_vars_on_pull,
    preserve_env_var_placeholders_on_pull,
    resolve_app_env_variables_in_dir,
    resolve_connector_toolset_env_vars_in_dir,
    validate_value_for_schema,
)


@pytest.mark.parametrize(
    ("value", "schema", "valid"),
    [
        (True, {"type": "BOOLEAN"}, True),
        (False, {"type": "BOOLEAN"}, True),
        ("true", {"type": "BOOLEAN"}, False),
        (1, {"type": "BOOLEAN"}, False),
        (42, {"type": "INTEGER"}, True),
        (True, {"type": "INTEGER"}, False),
        (3.14, {"type": "INTEGER"}, False),
        ("42", {"type": "INTEGER"}, False),
        (3.14, {"type": "NUMBER"}, True),
        (10, {"type": "NUMBER"}, True),
        (False, {"type": "NUMBER"}, False),
        ("hello", {"type": "STRING"}, True),
        (123, {"type": "STRING"}, False),
        ({"k": "v"}, {"type": "OBJECT"}, True),
        ({"k": "v"}, {"properties": {"k": {"type": "STRING"}}}, True),
        ("{}", {"type": "OBJECT"}, False),
        (["a", "b"], {"type": "ARRAY"}, True),
        (["a"], {"items": {"type": "STRING"}}, True),
        ("a,b", {"type": "ARRAY"}, False),
        (None, {"type": "STRING", "nullable": True}, True),
        (None, {"type": "STRING"}, False),
        ("anything", {"type": "TYPE_UNSPECIFIED"}, True),
    ],
)
def test_validate_value_for_schema(
    value: Any, schema: dict[str, Any], valid: bool
) -> None:
    err = validate_value_for_schema(value, schema)
    if valid:
        assert err is None
    else:
        assert err is not None


def test_parse_env_variable_declarations_valid() -> None:
    env_data = {
        "app": {
            "variableDeclarations": [
                {
                    "name": "enable_authentication",
                    "schema": {"type": "BOOLEAN", "default": True},
                },
                {
                    "name": "api_region",
                    "schema": {"default": "us-east1"},
                },
            ]
        }
    }
    defaults, errors = extract_env_variable_defaults(env_data)
    assert errors == []
    assert defaults == {
        "enable_authentication": True,
        "api_region": "us-east1",
    }


def test_parse_env_variable_declarations_catches_traps() -> None:
    parsed = parse_env_variable_declarations(
        {
            "variableDeclarations": [
                {"name": "enable_authentication", "schema": {"default": True}}
            ],
            "app": {
                "variableDeclarations": [
                    {"name": "dup", "schema": {"default": 1}},
                    {"name": "dup", "schema": {"default": 2}},
                    {"name": "missing_default", "schema": {"type": "STRING"}},
                    {
                        "name": "recursive",
                        "schema": {"default": ENV_VAR_PLACEHOLDER},
                    },
                ]
            },
        }
    )
    assert any("top-level 'variableDeclarations'" in e for e in parsed.errors)
    assert any("Duplicate variable 'dup'" in e for e in parsed.errors)
    assert any(
        "Variable 'missing_default'" in e and "schema.default" in e
        for e in parsed.errors
    )
    assert any(
        "Variable 'recursive'" in e and "unresolved" in e for e in parsed.errors
    )


def test_resolve_app_env_variables_in_dir_json_and_yaml(
    tmp_path: Path,
) -> None:
    json_dir = tmp_path / "json_app"
    json_dir.mkdir()
    (json_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Support Agent",
                "variableDeclarations": [
                    {
                        "name": "enable_authentication",
                        "schema": {
                            "type": "BOOLEAN",
                            "default": ENV_VAR_PLACEHOLDER,
                        },
                    },
                    {
                        "name": "max_retries",
                        "schema": {
                            "type": "INTEGER",
                            "default": ENV_VAR_PLACEHOLDER,
                        },
                    },
                    {
                        "name": "routing_cfg",
                        "schema": {
                            "type": "OBJECT",
                            "default": ENV_VAR_PLACEHOLDER,
                        },
                    },
                    {
                        "name": "static_flag",
                        "schema": {"type": "STRING", "default": "fixed"},
                    },
                ],
            }
        )
    )
    (json_dir / "environment.json").write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {
                            "name": "enable_authentication",
                            "schema": {"default": True},
                        },
                        {
                            "name": "max_retries",
                            "schema": {"default": 3},
                        },
                        {
                            "name": "routing_cfg",
                            "schema": {"default": {"tier": "gold"}},
                        },
                    ]
                }
            }
        )
    )

    assert collect_env_var_declaration_names(json_dir) == {
        "enable_authentication",
        "max_retries",
        "routing_cfg",
    }
    resolved = resolve_app_env_variables_in_dir(json_dir)
    assert resolved == ["enable_authentication", "max_retries", "routing_cfg"]

    updated = json.loads((json_dir / "app.json").read_text())
    by_name = {
        d["name"]: d["schema"]["default"]
        for d in updated["variableDeclarations"]
    }
    assert by_name == {
        "enable_authentication": True,
        "max_retries": 3,
        "routing_cfg": {"tier": "gold"},
        "static_flag": "fixed",
    }

    yaml_dir = tmp_path / "yaml_app"
    yaml_dir.mkdir()
    (yaml_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "Support YAML",
                "variableDeclarations": [
                    {
                        "name": "allowed_channels",
                        "schema": {
                            "type": "ARRAY",
                            "default": ENV_VAR_PLACEHOLDER,
                        },
                    }
                ],
            }
        )
    )
    (yaml_dir / "environment.json").write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {
                            "name": "allowed_channels",
                            "schema": {"default": ["voice", "chat"]},
                        }
                    ]
                }
            }
        )
    )
    assert resolve_app_env_variables_in_dir(yaml_dir) == ["allowed_channels"]
    updated_yaml = yaml.safe_load((yaml_dir / "app.yaml").read_text())
    assert updated_yaml["variableDeclarations"][0]["schema"]["default"] == [
        "voice",
        "chat",
    ]


def test_resolve_app_env_variables_fails_on_missing_or_wrong_type(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "App",
                "variableDeclarations": [
                    {
                        "name": "enable_authentication",
                        "schema": {
                            "type": "BOOLEAN",
                            "default": ENV_VAR_PLACEHOLDER,
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ValueError, match=r"environment\.json was not found"):
        resolve_app_env_variables_in_dir(app_dir)

    (app_dir / "environment.json").write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {
                            "name": "enable_authentication",
                            "schema": {"default": "true"},
                        }
                    ]
                }
            }
        )
    )
    with pytest.raises(ValueError, match="expected BOOLEAN, got string"):
        resolve_app_env_variables_in_dir(app_dir)


def test_preserve_env_var_placeholders_on_pull(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Exported App",
                "variableDeclarations": [
                    {
                        "name": "enable_authentication",
                        "schema": {"type": "BOOLEAN", "default": False},
                    },
                    {
                        "name": "static_var",
                        "schema": {"type": "STRING", "default": "keep"},
                    },
                ],
            }
        )
    )
    (app_dir / "environment.json").write_text(
        json.dumps({"tools": {"auth_tool": {"url": "https://dev.example.com"}}})
    )

    preserved = preserve_env_var_placeholders_on_pull(
        app_dir, {"enable_authentication"}
    )
    assert preserved == ["enable_authentication"]

    pulled_app = json.loads((app_dir / "app.json").read_text())
    assert (
        pulled_app["variableDeclarations"][0]["schema"]["default"]
        == ENV_VAR_PLACEHOLDER
    )
    assert pulled_app["variableDeclarations"][1]["schema"]["default"] == "keep"

    env_data = json.loads((app_dir / "environment.json").read_text())
    assert env_data["tools"]["auth_tool"]["url"] == "https://dev.example.com"
    assert env_data["app"]["variableDeclarations"] == [
        {
            "name": "enable_authentication",
            "schema": {"type": "BOOLEAN", "default": False},
        }
    ]


def test_resolve_connector_toolset_env_vars_display_name_and_stem(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    ts_dir_1 = app_dir / "toolsets" / "find_customer"
    ts_dir_1.mkdir(parents=True)
    (ts_dir_1 / "find_customer.json").write_text(
        json.dumps(
            {
                "displayName": "Find Customer",
                "connectorToolset": {
                    "connection": ENV_VAR_PLACEHOLDER,
                    "connectorActions": [{"connectionActionId": "Lookup"}],
                },
            }
        )
    )

    ts_dir_2 = app_dir / "toolsets" / "billing_lookup"
    ts_dir_2.mkdir(parents=True)
    (ts_dir_2 / "billing_lookup.yaml").write_text(
        yaml.safe_dump(
            {
                "display_name": "Billing Lookup",
                "connector_toolset": {
                    "connection": ENV_VAR_PLACEHOLDER,
                    "connector_actions": [{"connection_action_id": "GetBill"}],
                },
            }
        )
    )

    conn_1 = "projects/proj-uat/locations/us-central1/connections/cymbal-uat"
    conn_2 = "projects/proj-uat/locations/us-east1/connections/billing-uat"
    (app_dir / "environment.json").write_text(
        json.dumps(
            {
                "toolsets": {
                    # Keyed by directory stem even when displayName has spaces
                    "find_customer": {
                        "connectorToolset": {"connection": conn_1}
                    },
                    # Keyed by displayName with snake_case connector_toolset
                    "Billing Lookup": {
                        "connector_toolset": {"connection": conn_2}
                    },
                }
            }
        )
    )

    assert collect_env_var_connector_toolsets(app_dir) == {
        "Find Customer",
        "Billing Lookup",
    }
    resolved = resolve_connector_toolset_env_vars_in_dir(app_dir)
    assert set(resolved) == {"Find Customer", "Billing Lookup"}

    updated_1 = json.loads((ts_dir_1 / "find_customer.json").read_text())
    assert updated_1["connectorToolset"]["connection"] == conn_1

    updated_2 = yaml.safe_load((ts_dir_2 / "billing_lookup.yaml").read_text())
    assert updated_2["connector_toolset"]["connection"] == conn_2


def test_resolve_connector_toolset_env_vars_rejects_missing_or_invalid_path(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    ts_dir = app_dir / "toolsets" / "find_customer"
    ts_dir.mkdir(parents=True)
    (ts_dir / "find_customer.json").write_text(
        json.dumps(
            {
                "displayName": "Find Customer",
                "connectorToolset": {
                    "connection": ENV_VAR_PLACEHOLDER,
                    "connectorActions": [{"connectionActionId": "Lookup"}],
                },
            }
        )
    )

    with pytest.raises(ValueError, match=r"environment\.json was not found"):
        resolve_connector_toolset_env_vars_in_dir(app_dir)

    (app_dir / "environment.json").write_text(json.dumps({"toolsets": {}}))
    with pytest.raises(ValueError, match=r"missing from environment\.json"):
        resolve_connector_toolset_env_vars_in_dir(app_dir)

    (app_dir / "environment.json").write_text(
        json.dumps(
            {
                "toolsets": {
                    "find_customer": {
                        "connectorToolset": {"connection": "not-a-full-path"}
                    }
                }
            }
        )
    )
    with pytest.raises(ValueError, match="invalid connection resource name"):
        resolve_connector_toolset_env_vars_in_dir(app_dir)


def test_preserve_connector_toolset_env_vars_on_pull(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    ts_dir = app_dir / "toolsets" / "find_customer"
    ts_dir.mkdir(parents=True)
    exported_conn = (
        "projects/proj-dev/locations/us-central1/connections/cymbal-dev"
    )
    (ts_dir / "find_customer.json").write_text(
        json.dumps(
            {
                "displayName": "Find Customer",
                "connectorToolset": {
                    "connection": exported_conn,
                    "connectorActions": [{"connectionActionId": "Lookup"}],
                },
            }
        )
    )
    (app_dir / "environment.json").write_text(
        json.dumps({"tools": {"auth_api": {"url": "https://dev.example.com"}}})
    )

    prev_conns = extract_env_connector_toolset_connections(
        {
            "toolsets": {
                "find_customer": {
                    "connectorToolset": {
                        "connection": (
                            "projects/old/locations/us/connections/old-conn"
                        )
                    }
                }
            }
        }
    )
    preserved = preserve_connector_toolset_env_vars_on_pull(
        app_dir,
        {"Find Customer"},
        previous_env_connections=prev_conns,
    )
    assert preserved == ["Find Customer"]

    pulled_ts = json.loads((ts_dir / "find_customer.json").read_text())
    assert pulled_ts["connectorToolset"]["connection"] == ENV_VAR_PLACEHOLDER

    pulled_env = json.loads((app_dir / "environment.json").read_text())
    assert pulled_env["tools"]["auth_api"]["url"] == "https://dev.example.com"
    assert (
        pulled_env["toolsets"]["find_customer"]["connectorToolset"][
            "connection"
        ]
        == exported_conn
    )
