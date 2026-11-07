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

"""Tests for callback/toolset discovery and CES v1main schema parity."""

import json
from pathlib import Path

from cxas_scrapi.utils.linter import (
    Discovery,
    LintContext,
    build_registry,
    get_toolset_tools,
)


def _get_schema_rule(rule_id: str) -> object:
    reg = build_registry()
    for r in reg.all_rules():
        if r.id == rule_id:
            return r
    raise KeyError(rule_id)


def test_discover_callbacks_supports_flat_callback_directory_layout(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text('{"displayName": "demo"}')
    flat_cb = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    flat_cb.parent.mkdir(parents=True)
    flat_cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )

    nested_cb = (
        tmp_path
        / "agents"
        / "root_agent"
        / "after_model_callbacks"
        / "after_model_callbacks_01"
        / "python_code.py"
    )
    nested_cb.parent.mkdir(parents=True)
    nested_cb.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    return None\n"
    )

    disc = Discovery(tmp_path, tmp_path / "evaluations")
    callbacks = disc.discover_callbacks()
    assert len(callbacks) == 2  # noqa: PLR2004
    assert (
        "root_agent",
        "after_model_callbacks",
        "after_model_callbacks_01",
        nested_cb,
    ) in callbacks
    assert (
        "root_agent",
        "before_model_callbacks",
        "before_model_callbacks_01",
        flat_cb,
    ) in callbacks


def test_get_toolset_tools_synthesizes_method_path_when_operation_id_omitted(
    tmp_path: Path,
) -> None:
    ts_dir = tmp_path / "toolsets" / "conversational_graph_api"
    (ts_dir / "open_api_toolset").mkdir(parents=True)
    (ts_dir / "conversational_graph_api.json").write_text(
        json.dumps(
            {
                "displayName": "conversational_graph_api",
                "openApiToolset": {
                    "openApiSchema": "open_api_toolset/open_api_schema.yaml"
                },
            }
        )
    )
    (ts_dir / "open_api_toolset" / "open_api_schema.yaml").write_text(
        "openapi: 3.0.0\n"
        "paths:\n"
        "  /:\n"
        "    post:\n"
        "      summary: GraphQL endpoint\n"
        "  /v1/orders:\n"
        "    get:\n"
        "      summary: List orders\n"
    )

    res = get_toolset_tools(
        tmp_path, "conversational_graph_api", allowed_tool_ids=["POST_"]
    )
    assert res.tools == ["conversational_graph_api_POST_"]

    res_all = get_toolset_tools(tmp_path, "conversational_graph_api")
    assert set(res_all.tools) == {
        "conversational_graph_api_POST_",
        "conversational_graph_api_GET_v1_orders",
    }


def test_v003_accepts_datastore_sources_and_snippets_config(
    tmp_path: Path,
) -> None:
    rule = _get_schema_rule("V003")
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evaluations",
        app_root=tmp_path,
    )
    tool_dir = tmp_path / "tools" / "kb_datastore"
    tool_dir.mkdir(parents=True)
    (tool_dir / "kb_datastore.json").write_text(
        json.dumps(
            {
                "name": "kb_datastore",
                "displayName": "kb_datastore",
                "dataStoreTool": {
                    "name": "kb_datastore",
                    "engineSource": {
                        "dataStoreSources": [
                            {
                                "dataStore": {
                                    "name": (
                                        "projects/p/locations/us/collections/"
                                        "default_collection/dataStores/ds1"
                                    ),
                                    "type": "UNSTRUCTURED",
                                }
                            }
                        ]
                    },
                    "modalityConfigs": [
                        {
                            "modalityType": "TEXT",
                            "snippetsConfig": {
                                "enableSnippets": True,
                                "maxSnippets": 5,
                            },
                        }
                    ],
                },
            }
        )
    )

    assert rule.check(tool_dir, "", ctx) == []


def test_v002_v003_v005_v006_accept_v1main_fields(tmp_path: Path) -> None:
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evaluations",
        app_root=tmp_path,
    )

    # V002: Agent with modelSettings.thinkingLevel
    agent_dir = tmp_path / "agents" / "handover_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "handover_agent.json").write_text(
        json.dumps(
            {
                "name": "handover_agent",
                "displayName": "handover_agent",
                "modelSettings": {
                    "model": "gemini-2.5-flash",
                    "thinkingLevel": "LOW",
                },
            }
        )
    )
    assert _get_schema_rule("V002").check(agent_dir, "", ctx) == []

    # V003: RemoteAgentTool with inputVariableMapping and outputVariableMapping
    remote_dir = tmp_path / "tools" / "call_outbound_payment"
    remote_dir.mkdir(parents=True)
    (remote_dir / "call_outbound_payment.json").write_text(
        json.dumps(
            {
                "name": "call_outbound_payment",
                "displayName": "call_outbound_payment",
                "remoteAgentTool": {
                    "name": "call_outbound_payment",
                    "description": "A2A payment sub-agent",
                    "agentCard": {
                        "name": "call_outbound_payment",
                        "description": "A2A sub-agent",
                        "version": "0.1.0",
                        "supportedInterfaces": [
                            {
                                "url": "https://ces.googleapis.com/v1/apps/a",
                                "protocolBinding": "HTTP+JSON",
                                "protocolVersion": "1.0",
                            }
                        ],
                        "skills": [
                            {
                                "id": "pay",
                                "name": "pay",
                                "description": "Pay bill",
                                "tags": ["pay"],
                            }
                        ],
                    },
                    "inputVariableMapping": {"a2a_in": "a2a_in"},
                    "outputVariableMapping": {"a2a_out": "a2a_out_payment"},
                },
            }
        )
    )
    assert _get_schema_rule("V003").check(remote_dir, "", ctx) == []

    # V005: Guardrail with action.toolCall
    gr_dir = tmp_path / "guardrails" / "Escalation_Guardrail"
    gr_dir.mkdir(parents=True)
    (gr_dir / "Escalation_Guardrail.json").write_text(
        json.dumps(
            {
                "name": "Escalation_Guardrail",
                "displayName": "Escalation Guardrail",
                "enabled": True,
                "action": {"toolCall": {"tool": "escalate_to_human"}},
                "llmPolicy": {"prompt": "Check for escalation"},
            }
        )
    )
    assert _get_schema_rule("V005").check(gr_dir, "", ctx) == []

    # V006: Scenario evaluation with userGoalBehavior/milestones and no rubrics
    eval_dir = tmp_path / "evaluations" / "Delegate_Account_Suspension"
    eval_dir.mkdir(parents=True)
    (eval_dir / "Delegate_Account_Suspension.json").write_text(
        json.dumps(
            {
                "name": "Delegate_Account_Suspension",
                "displayName": "Delegate Account Suspension",
                "scenario": {
                    "task": "User asks to suspend service.",
                    "maxTurns": 10,
                    "taskCompletionBehavior": "TASK_SATISFIED",
                    "userGoalBehavior": "USER_GOAL_SATISFIED",
                    "milestones": [{"goal": "Verify account"}],
                },
            }
        )
    )
    assert _get_schema_rule("V006").check(eval_dir, "", ctx) == []
