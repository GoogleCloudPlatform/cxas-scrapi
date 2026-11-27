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
    collect_env_var_declaration_names,
    collect_tool_and_toolset_env_var_placeholders,
    extract_env_variable_defaults,
    parse_env_variable_declarations,
    preserve_env_var_placeholders_on_pull,
    preserve_tool_and_toolset_env_vars_on_pull,
    resolve_app_env_variables_in_dir,
    resolve_tool_and_toolset_env_vars_in_dir,
    validate_tool_and_toolset_env_vars,
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


def test_resolve_tool_and_toolset_env_vars_all_types(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"

    # 1. ConnectorToolset (JSON, keyed by dir stem)
    ts_conn = app_dir / "toolsets" / "find_customer"
    ts_conn.mkdir(parents=True)
    (ts_conn / "find_customer.json").write_text(
        json.dumps(
            {
                "displayName": "Find Customer",
                "connectorToolset": {
                    "connection": ENV_VAR_PLACEHOLDER,
                    "connectorActions": [
                        {
                            "entityOperation": {
                                "entityId": "Customers",
                                "operation": "LIST",
                            }
                        }
                    ],
                },
            }
        )
    )

    # 2. McpToolset (YAML snake_case, keyed by displayName)
    ts_mcp = app_dir / "toolsets" / "orders_mcp"
    ts_mcp.mkdir(parents=True)
    (ts_mcp / "orders_mcp.yaml").write_text(
        yaml.safe_dump(
            {
                "display_name": "Orders MCP",
                "mcp_toolset": {
                    "server_address": ENV_VAR_PLACEHOLDER,
                    "api_authentication": {
                        "oauth_config": {
                            "oauth_grant_type": "CLIENT_CREDENTIAL",
                            "client_id": ENV_VAR_PLACEHOLDER,
                            "client_secret_version": ENV_VAR_PLACEHOLDER,
                            "token_endpoint": ENV_VAR_PLACEHOLDER,
                        }
                    },
                },
            }
        )
    )

    # 3. OpenApiTool (external schema $env_var + oauth clientId + bool skipped)
    t_oa = app_dir / "tools" / "orders_api"
    (t_oa / "open_api_tool").mkdir(parents=True)
    (t_oa / "open_api_tool" / "open_api_schema.yaml").write_text(
        "openapi: 3.0.0\nservers:\n  - url: $env_var\npaths: {}\n"
    )
    (t_oa / "orders_api.json").write_text(
        json.dumps(
            {
                "openApiTool": {
                    "name": "Orders API",
                    "openApiSchema": (
                        "tools/orders_api/open_api_tool/open_api_schema.yaml"
                    ),
                    "ignoreUnknownFields": ENV_VAR_PLACEHOLDER,
                    "apiAuthentication": {
                        "oauthConfig": {
                            "oauthGrantType": "CLIENT_CREDENTIAL",
                            "clientId": ENV_VAR_PLACEHOLDER,
                            "clientSecretVersion": ENV_VAR_PLACEHOLDER,
                            "tokenEndpoint": ENV_VAR_PLACEHOLDER,
                        }
                    },
                }
            }
        )
    )

    # 4. DataStoreTool (engine + repeated boostSpecs.dataStores)
    t_ds = app_dir / "tools" / "kb_search"
    t_ds.mkdir(parents=True)
    (t_ds / "kb_search.json").write_text(
        json.dumps(
            {
                "displayName": "KB Search",
                "dataStoreTool": {
                    "name": "kb_search",
                    "engineSource": {"engine": ENV_VAR_PLACEHOLDER},
                    "boostSpecs": [{"dataStores": [ENV_VAR_PLACEHOLDER]}],
                },
            }
        )
    )

    # 5. RemoteAgentTool (nested supportedInterfaces[].url)
    t_ra = app_dir / "tools" / "billing_remote"
    t_ra.mkdir(parents=True)
    (t_ra / "billing_remote.json").write_text(
        json.dumps(
            {
                "remoteAgentTool": {
                    "name": "billing_remote",
                    "description": "Billing A2A agent",
                    "agentCard": {
                        "name": "Billing",
                        "description": "Billing",
                        "version": "1.0",
                        "supportedInterfaces": [
                            {
                                "url": ENV_VAR_PLACEHOLDER,
                                "protocolBinding": "JSONRPC",
                                "protocolVersion": "1.0",
                            }
                        ],
                        "skills": [],
                    },
                }
            }
        )
    )

    # 6. ApplicationIntegrationTool + GoogleSearchTool + FileSearchTool
    t_ai = app_dir / "tools" / "run_workflow"
    t_ai.mkdir(parents=True)
    (t_ai / "run_workflow.json").write_text(
        json.dumps(
            {
                "applicationIntegrationTool": {
                    "name": "run_workflow",
                    "integrationVersion": ENV_VAR_PLACEHOLDER,
                    "triggerId": ENV_VAR_PLACEHOLDER,
                }
            }
        )
    )

    t_gs = app_dir / "tools" / "web_search"
    t_gs.mkdir(parents=True)
    (t_gs / "web_search.json").write_text(
        json.dumps(
            {
                "googleSearchTool": {
                    "name": "web_search",
                    "contextUrls": [ENV_VAR_PLACEHOLDER],
                    "preferredDomains": [ENV_VAR_PLACEHOLDER],
                }
            }
        )
    )

    t_fs = app_dir / "tools" / "docs_rag"
    t_fs.mkdir(parents=True)
    (t_fs / "docs_rag.json").write_text(
        json.dumps(
            {
                "fileSearchTool": {
                    "name": "docs_rag",
                    "fileCorpus": ENV_VAR_PLACEHOLDER,
                }
            }
        )
    )

    conn_val = "projects/proj-uat/locations/us-central1/connections/cymbal-uat"
    mcp_url = "https://mcp-uat.example.com/mcp/"
    secret_ver = "projects/proj-uat/secrets/oauth-client/versions/1"
    token_url = "https://auth-uat.example.com/oauth/token"
    oa_url = "https://api-uat.example.com"
    engine_val = (
        "projects/proj-uat/locations/global/collections/default_collection/"
        "engines/kb-uat"
    )
    ds_vals = [
        "projects/proj-uat/locations/global/collections/default_collection/"
        "dataStores/ds-1",
        "projects/proj-uat/locations/global/collections/default_collection/"
        "dataStores/ds-2",
    ]
    a2a_url = "https://billing-uat.example.com/a2a/v1"
    integ_ver = (
        "projects/proj-uat/locations/us-central1/integrations/orders/"
        "versions/v2"
    )
    rag_corpus = "projects/proj-uat/locations/us-central1/ragCorpora/corpus-1"

    (app_dir / "environment.json").write_text(
        json.dumps(
            {
                "toolsets": {
                    "find_customer": {
                        "connectorToolset": {"connection": conn_val}
                    },
                    "Orders MCP": {
                        "mcpToolset": {
                            "serverAddress": mcp_url,
                            "apiAuthentication": {
                                "oauthConfig": {
                                    "clientId": "mcp-uat-client",
                                    "clientSecretVersion": secret_ver,
                                    "tokenEndpoint": token_url,
                                }
                            },
                        }
                    },
                },
                "tools": {
                    "orders_api": {
                        "openApiTool": {
                            "url": oa_url,
                            "apiAuthentication": {
                                "oauthConfig": {
                                    "clientId": "oa-uat-client",
                                    "clientSecretVersion": secret_ver,
                                    "tokenEndpoint": token_url,
                                }
                            },
                        }
                    },
                    "KB Search": {
                        "dataStoreTool": {
                            "engineSource": {"engine": engine_val},
                            "boostSpecs": [{"dataStores": ds_vals}],
                        }
                    },
                    "billing_remote": {
                        "remoteAgentTool": {
                            "agentCard": {
                                "supportedInterfaces": [{"url": a2a_url}]
                            }
                        }
                    },
                    "run_workflow": {
                        "applicationIntegrationTool": {
                            "integrationVersion": integ_ver,
                            "triggerId": "api_trigger/uat_trigger",
                        }
                    },
                    "web_search": {
                        "googleSearchTool": {
                            "contextUrls": ["https://docs-uat.example.com/faq"],
                            "preferredDomains": ["docs-uat.example.com"],
                        }
                    },
                    "docs_rag": {"fileSearchTool": {"fileCorpus": rag_corpus}},
                },
            }
        )
    )

    placeholders = collect_tool_and_toolset_env_var_placeholders(app_dir)
    # Proto bool field ignoreUnknownFields must be skipped
    assert not any(
        p.field_path == ("openApiTool", "ignoreUnknownFields")
        for p in placeholders
    )

    resolved = resolve_tool_and_toolset_env_vars_in_dir(app_dir)
    assert len(resolved) == 17

    assert (
        json.loads((ts_conn / "find_customer.json").read_text())[
            "connectorToolset"
        ]["connection"]
        == conn_val
    )
    mcp_after = yaml.safe_load((ts_mcp / "orders_mcp.yaml").read_text())
    assert mcp_after["mcp_toolset"]["server_address"] == mcp_url
    assert (
        mcp_after["mcp_toolset"]["api_authentication"]["oauth_config"][
            "client_id"
        ]
        == "mcp-uat-client"
    )
    oa_schema_after = (
        t_oa / "open_api_tool" / "open_api_schema.yaml"
    ).read_text()
    assert oa_url in oa_schema_after
    assert ENV_VAR_PLACEHOLDER not in oa_schema_after
    oa_after = json.loads((t_oa / "orders_api.json").read_text())
    assert (
        oa_after["openApiTool"]["apiAuthentication"]["oauthConfig"]["clientId"]
        == "oa-uat-client"
    )
    # Boolean placeholder left for resolve_env_booleans_in_dir
    assert oa_after["openApiTool"]["ignoreUnknownFields"] == ENV_VAR_PLACEHOLDER

    ds_after = json.loads((t_ds / "kb_search.json").read_text())
    assert ds_after["dataStoreTool"]["engineSource"]["engine"] == engine_val
    assert ds_after["dataStoreTool"]["boostSpecs"][0]["dataStores"] == ds_vals

    ra_after = json.loads((t_ra / "billing_remote.json").read_text())
    assert (
        ra_after["remoteAgentTool"]["agentCard"]["supportedInterfaces"][0][
            "url"
        ]
        == a2a_url
    )

    ai_after = json.loads((t_ai / "run_workflow.json").read_text())
    assert (
        ai_after["applicationIntegrationTool"]["integrationVersion"]
        == integ_ver
    )
    assert (
        ai_after["applicationIntegrationTool"]["triggerId"]
        == "api_trigger/uat_trigger"
    )

    gs_after = json.loads((t_gs / "web_search.json").read_text())
    assert gs_after["googleSearchTool"]["contextUrls"] == [
        "https://docs-uat.example.com/faq"
    ]
    assert gs_after["googleSearchTool"]["preferredDomains"] == [
        "docs-uat.example.com"
    ]

    fs_after = json.loads((t_fs / "docs_rag.json").read_text())
    assert fs_after["fileSearchTool"]["fileCorpus"] == rag_corpus


def test_resolve_tool_and_toolset_env_vars_rejects_missing_or_invalid(
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
    mcp_dir = app_dir / "toolsets" / "orders_mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "orders_mcp.json").write_text(
        json.dumps(
            {
                "displayName": "Orders MCP",
                "mcpToolset": {"serverAddress": ENV_VAR_PLACEHOLDER},
            }
        )
    )

    with pytest.raises(ValueError, match=r"environment\.json was not found"):
        resolve_tool_and_toolset_env_vars_in_dir(app_dir)

    (app_dir / "environment.json").write_text(json.dumps({"toolsets": {}}))
    with pytest.raises(ValueError, match=r"missing from environment\.json"):
        resolve_tool_and_toolset_env_vars_in_dir(app_dir)

    (app_dir / "environment.json").write_text(
        json.dumps(
            {
                "toolsets": {
                    "find_customer": {
                        "connectorToolset": {"connection": "not-a-full-path"}
                    },
                    "orders_mcp": {
                        "mcpToolset": {"serverAddress": "not-a-url"}
                    },
                }
            }
        )
    )
    with pytest.raises(ValueError, match="has invalid value"):
        resolve_tool_and_toolset_env_vars_in_dir(app_dir)


def test_preserve_tool_and_toolset_env_vars_on_pull(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    ts_conn = app_dir / "toolsets" / "find_customer"
    ts_conn.mkdir(parents=True)
    ts_mcp = app_dir / "toolsets" / "orders_mcp"
    ts_mcp.mkdir(parents=True)
    t_ra = app_dir / "tools" / "billing_remote"
    t_ra.mkdir(parents=True)

    # Pre-pull local state with $env_var placeholders
    (ts_conn / "find_customer.json").write_text(
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
    (ts_mcp / "orders_mcp.json").write_text(
        json.dumps(
            {
                "displayName": "Orders MCP",
                "mcpToolset": {
                    "serverAddress": ENV_VAR_PLACEHOLDER,
                    "apiAuthentication": {
                        "oauthConfig": {"clientId": ENV_VAR_PLACEHOLDER}
                    },
                },
            }
        )
    )
    (t_ra / "billing_remote.json").write_text(
        json.dumps(
            {
                "displayName": "billing_remote",
                "remoteAgentTool": {
                    "name": "billing_remote",
                    "agentCard": {
                        "supportedInterfaces": [{"url": ENV_VAR_PLACEHOLDER}]
                    },
                },
            }
        )
    )
    prev_placeholders = collect_tool_and_toolset_env_var_placeholders(app_dir)
    prev_env_data = {
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

    # Simulate CES export overwriting manifests with concrete values
    exported_conn = (
        "projects/proj-dev/locations/us-central1/connections/cymbal-dev"
    )
    exported_mcp = "https://mcp-dev.example.com/mcp/"
    exported_client_id = "mcp-dev-client"
    exported_a2a = "https://billing-dev.example.com/a2a/v1"

    (ts_conn / "find_customer.json").write_text(
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
    (ts_mcp / "orders_mcp.json").write_text(
        json.dumps(
            {
                "displayName": "Orders MCP",
                "mcpToolset": {
                    "serverAddress": exported_mcp,
                    "apiAuthentication": {
                        "oauthConfig": {"clientId": exported_client_id}
                    },
                },
            }
        )
    )
    (t_ra / "billing_remote.json").write_text(
        json.dumps(
            {
                "displayName": "billing_remote",
                "remoteAgentTool": {
                    "name": "billing_remote",
                    "agentCard": {
                        "supportedInterfaces": [{"url": exported_a2a}]
                    },
                },
            }
        )
    )
    (app_dir / "environment.json").write_text(
        json.dumps({"tools": {"auth_api": {"url": "https://dev.example.com"}}})
    )

    preserved = preserve_tool_and_toolset_env_vars_on_pull(
        app_dir,
        prev_placeholders,
        previous_env_data=prev_env_data,
    )
    assert len(preserved) == 4

    assert (
        json.loads((ts_conn / "find_customer.json").read_text())[
            "connectorToolset"
        ]["connection"]
        == ENV_VAR_PLACEHOLDER
    )
    mcp_pulled = json.loads((ts_mcp / "orders_mcp.json").read_text())
    assert mcp_pulled["mcpToolset"]["serverAddress"] == ENV_VAR_PLACEHOLDER
    assert (
        mcp_pulled["mcpToolset"]["apiAuthentication"]["oauthConfig"]["clientId"]
        == ENV_VAR_PLACEHOLDER
    )
    ra_pulled = json.loads((t_ra / "billing_remote.json").read_text())
    assert (
        ra_pulled["remoteAgentTool"]["agentCard"]["supportedInterfaces"][0][
            "url"
        ]
        == ENV_VAR_PLACEHOLDER
    )

    pulled_env = json.loads((app_dir / "environment.json").read_text())
    assert pulled_env["tools"]["auth_api"]["url"] == "https://dev.example.com"
    assert (
        pulled_env["toolsets"]["find_customer"]["connectorToolset"][
            "connection"
        ]
        == exported_conn
    )
    assert (
        pulled_env["toolsets"]["Orders MCP"]["mcpToolset"]["serverAddress"]
        == exported_mcp
    )
    assert (
        pulled_env["toolsets"]["Orders MCP"]["mcpToolset"]["apiAuthentication"][
            "oauthConfig"
        ]["clientId"]
        == exported_client_id
    )
    assert (
        pulled_env["tools"]["billing_remote"]["remoteAgentTool"]["agentCard"][
            "supportedInterfaces"
        ][0]["url"]
        == exported_a2a
    )


def test_openapi_schema_path_traversal_and_symlink_rejected_without_touching_external_file(
    tmp_path: Path,
) -> None:
    from cxas_scrapi.utils.lint_rules.config import (  # noqa: PLC0415
        ToolAndToolsetEnvVars,
    )
    from cxas_scrapi.utils.linter import (  # noqa: PLC0415
        Discovery,
        LintConfig,
        build_context,
    )

    external_schema = tmp_path / "shared_schema.yaml"
    original_content = "openapi: 3.0.0\nservers:\n  - url: $env_var\n"
    external_schema.write_text(original_content, encoding="utf-8")

    env_payload = {
        "tools": {
            "api_tool": {"openApiTool": {"url": "https://prod.example.com"}}
        }
    }

    cases = [
        ("relative_parent", "../shared_schema.yaml"),
        ("nested_parent", "tools/api_tool/../../../shared_schema.yaml"),
        ("absolute", str(external_schema)),
        ("symlink_file", "tools/api_tool/schema_link.yaml"),
        ("symlink_dir", "tools/api_tool/linked_dir/shared_schema.yaml"),
    ]
    for case_name, schema_ref in cases:
        app_dir = tmp_path / f"app_{case_name}"
        tool_dir = app_dir / "tools" / "api_tool"
        tool_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text(
            json.dumps({"displayName": "Test App"}), encoding="utf-8"
        )
        (app_dir / "environment.json").write_text(
            json.dumps(env_payload), encoding="utf-8"
        )
        if case_name == "symlink_file":
            (tool_dir / "schema_link.yaml").symlink_to(external_schema)
        elif case_name == "symlink_dir":
            (tool_dir / "linked_dir").symlink_to(tmp_path)

        (tool_dir / "api_tool.json").write_text(
            json.dumps(
                {
                    "displayName": "api_tool",
                    "openApiTool": {"openApiSchema": schema_ref},
                }
            ),
            encoding="utf-8",
        )

        # Default passive collection (used by cxas pull) does not crash.
        assert collect_tool_and_toolset_env_var_placeholders(app_dir) == []

        with pytest.raises(ValueError, match=r"escapes app|Symlinks"):
            collect_tool_and_toolset_env_var_placeholders(
                app_dir, strict_schema_paths=True
            )

        with pytest.raises(ValueError, match=r"escapes app|Symlinks"):
            resolve_tool_and_toolset_env_vars_in_dir(app_dir)

        _, _, issues = validate_tool_and_toolset_env_vars(app_dir, env_payload)
        assert len(issues) == 1
        assert (
            "escapes app" in issues[0].message
            or "Symlinks" in issues[0].message
        )

        rule = ToolAndToolsetEnvVars()
        discovery = Discovery(app_dir, app_dir / "evals")
        ctx = build_context(app_dir, LintConfig(), discovery)
        lint_results = rule.check(
            app_dir / "app.json", (app_dir / "app.json").read_text("utf-8"), ctx
        )
        assert len(lint_results) == 1
        assert external_schema.read_text(encoding="utf-8") == original_content


def test_external_openapi_schema_without_env_var_does_not_break_push_pull_or_package(
    tmp_path: Path,
) -> None:
    from cxas_scrapi.core.packaging import package_app_archive  # noqa: PLC0415

    external_schema = tmp_path / "shared.yaml"
    plain_content = (
        "openapi: 3.0.0\nservers:\n  - url: https://shared.example.com\n"
    )
    external_schema.write_text(plain_content, encoding="utf-8")

    app_dir = tmp_path / "app"
    tool_dir = app_dir / "tools" / "plain_api"
    tool_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "Plain App"}), encoding="utf-8"
    )
    (tool_dir / "plain_api.json").write_text(
        json.dumps(
            {
                "displayName": "plain_api",
                "openApiTool": {"openApiSchema": "../shared.yaml"},
            }
        ),
        encoding="utf-8",
    )

    assert collect_tool_and_toolset_env_var_placeholders(app_dir) == []
    assert (
        collect_tool_and_toolset_env_var_placeholders(
            app_dir, strict_schema_paths=True
        )
        == []
    )
    assert validate_tool_and_toolset_env_vars(app_dir, {}) == ([], [], [])
    assert resolve_tool_and_toolset_env_vars_in_dir(app_dir) == []
    assert preserve_tool_and_toolset_env_vars_on_pull(app_dir, []) == []
    zip_path = package_app_archive(app_dir, tmp_path / "plain.zip")
    assert zip_path.is_file()
    assert external_schema.read_text(encoding="utf-8") == plain_content


def test_openapi_url_and_schema_both_resolved_when_both_use_env_var(
    tmp_path: Path,
) -> None:
    # 1. File-backed openApiSchema + openApiTool.url both using $env_var
    app_file = tmp_path / "app_file"
    tool_dir = app_file / "tools" / "orders_api"
    tool_dir.mkdir(parents=True)
    schema_rel = "tools/orders_api/schema.yaml"
    (app_file / schema_rel).write_text(
        "openapi: 3.0.0\nservers:\n  - url: $env_var\n", encoding="utf-8"
    )
    (tool_dir / "orders_api.json").write_text(
        json.dumps(
            {
                "displayName": "orders_api",
                "openApiTool": {
                    "url": ENV_VAR_PLACEHOLDER,
                    "openApiSchema": schema_rel,
                },
            }
        ),
        encoding="utf-8",
    )

    # Missing URL in environment.json must produce 1 deduplicated issue.
    _, _, missing_issues = validate_tool_and_toolset_env_vars(app_file, {})
    assert len(missing_issues) == 1

    (app_file / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "orders_api": {
                        "openApiTool": {
                            "url": "https://orders.prod.example.com"
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = resolve_tool_and_toolset_env_vars_in_dir(app_file)
    assert resolved == ["orders_api.openApiTool.url"]
    manifest_after = json.loads(
        (tool_dir / "orders_api.json").read_text("utf-8")
    )
    assert (
        manifest_after["openApiTool"]["url"]
        == "https://orders.prod.example.com"
    )
    schema_after = (app_file / schema_rel).read_text("utf-8")
    assert "https://orders.prod.example.com" in schema_after
    assert ENV_VAR_PLACEHOLDER not in schema_after

    # 2. Inline openApiSchema + openApiTool.url both using $env_var
    app_inline = tmp_path / "app_inline"
    inline_dir = app_inline / "tools" / "inline_api"
    inline_dir.mkdir(parents=True)
    (inline_dir / "inline_api.json").write_text(
        json.dumps(
            {
                "displayName": "inline_api",
                "openApiTool": {
                    "url": ENV_VAR_PLACEHOLDER,
                    "openApiSchema": "openapi: 3.0.0\nservers:\n  - url: $env_var\n",
                },
            }
        ),
        encoding="utf-8",
    )
    (app_inline / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "inline_api": {
                        "openApiTool": {
                            "url": "https://inline.prod.example.com"
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved_inline = resolve_tool_and_toolset_env_vars_in_dir(app_inline)
    assert resolved_inline == ["inline_api.openApiTool.url"]
    inline_after = json.loads(
        (inline_dir / "inline_api.json").read_text("utf-8")
    )
    assert (
        inline_after["openApiTool"]["url"] == "https://inline.prod.example.com"
    )
    assert (
        "https://inline.prod.example.com"
        in inline_after["openApiTool"]["openApiSchema"]
    )
    assert (
        ENV_VAR_PLACEHOLDER not in inline_after["openApiTool"]["openApiSchema"]
    )
