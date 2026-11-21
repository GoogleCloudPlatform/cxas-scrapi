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

"""Unit tests for CXAS resource dependency and blast-radius impact analyzer."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
import yaml

from cxas_scrapi.cli.app import app_impact
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.impact_analyzer import (
    analyze_app_summary,
    analyze_git_diff,
    analyze_target,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def sample_app(tmp_path: Path) -> Path:
    """Build a realistic multi-agent CXAS exported app fixture."""
    app_dir = tmp_path / "sample_app"
    app_dir.mkdir()

    # app.json with variableDeclarations, guardrails, and rootAgent
    _write_json(
        app_dir / "app.json",
        {
            "displayName": "Customer Care App",
            "rootAgent": "RootRouter",
            "guardrails": ["global_safety"],
            "variableDeclarations": [
                {
                    "name": "customer_id",
                    "description": "Primary customer identifier",
                    "schema": {"type": "STRING"},
                },
                {
                    "name": "auth_tier",
                    "description": "Authentication tier",
                    "schema": {"type": "STRING"},
                },
                {
                    "name": "order_context",
                    "description": "Order object",
                    "schema": {
                        "type": "OBJECT",
                        "properties": {
                            "order_id": {"type": "STRING"},
                            "amount": {"type": "NUMBER"},
                        },
                    },
                },
                {
                    "name": "unused_var",
                    "description": "Orphan variable",
                    "schema": {"type": "BOOLEAN"},
                },
            ],
        },
    )

    # environment.json with predefinedVariableDeclarations
    _write_json(
        app_dir / "environment.json",
        {
            "predefinedVariableDeclarations": [
                {
                    "name": "env_region",
                    "schema": {"type": "STRING"},
                }
            ]
        },
    )

    _write_text(
        app_dir / "global_instruction.txt",
        "Always operate in region {$session.params.env_region}.\n",
    )

    # Agents:
    # RootRouter -> Billing (child) & TechSupport (child)
    # Billing -> RefundSpecialist (child)
    _write_json(
        app_dir / "agents" / "RootRouter" / "RootRouter.json",
        {
            "displayName": "Root Router",
            "childAgents": ["Billing", "TechSupport"],
            "transferRules": [
                {
                    "childAgent": "Billing",
                    "condition": "session.params.auth_tier == 'VERIFIED'",
                }
            ],
        },
    )
    _write_text(
        app_dir / "agents" / "RootRouter" / "instruction.txt",
        "Route billing questions to {@AGENT: Billing} and tech issues to "
        "{@AGENT: TechSupport}.\n",
    )

    _write_json(
        app_dir / "agents" / "Billing" / "Billing.json",
        {
            "displayName": "Billing Agent",
            "tools": ["lookup_order", "Orchestrate Order"],
            "toolsets": [{"toolset": "crm_api"}],
            "guardrails": ["pci_compliance"],
            "childAgents": ["RefundSpecialist"],
        },
    )
    _write_text(
        app_dir / "agents" / "Billing" / "instruction.txt",
        "Greet customer {customer_id} and check {session.params.order_context}."
        " Call {@TOOL: lookup_order} or {@TOOLSET: crm_api}. "
        "Transfer complex refunds to {@AGENT: RefundSpecialist}.\n",
    )
    _write_text(
        app_dir
        / "agents"
        / "Billing"
        / "before_model_callbacks"
        / "gate_tools"
        / "python_code.py",
        """def before_model_callback(callback_context, llm_request):
    state = callback_context.state
    cid = state.get("customer_id")
    tier = callback_context.variables["auth_tier"]
    if not cid:
        hide_tool(llm_request, "lookup_order")
    else:
        unhide_tool(llm_request, "lookup_order")
    state.update({"auth_tier": tier})
""",
    )

    _write_json(
        app_dir / "agents" / "RefundSpecialist" / "RefundSpecialist.json",
        {
            "displayName": "Refund Specialist",
            "tools": ["issue_refund"],
        },
    )
    _write_text(
        app_dir / "agents" / "RefundSpecialist" / "instruction.txt",
        "Issue refunds using {@TOOL: issue_refund}.\n",
    )

    _write_json(
        app_dir / "agents" / "TechSupport" / "TechSupport.json",
        {
            "displayName": "Tech Support",
            "tools": ["run_diagnostics"],
        },
    )
    _write_text(
        app_dir / "agents" / "TechSupport" / "instruction.txt",
        "Run diagnostics via {@TOOL: run_diagnostics}.\n",
    )

    # Orphan agent (not root, not a child of any agent)
    _write_json(
        app_dir / "agents" / "OrphanAgent" / "OrphanAgent.json",
        {
            "displayName": "Orphan Agent",
        },
    )

    # Tools:
    # 1. lookup_order (reads customer_id)
    _write_json(
        app_dir / "tools" / "lookup_order" / "lookup_order.json",
        {
            "displayName": "Lookup Order",
            "pythonFunction": {"name": "lookup_order"},
        },
    )
    _write_text(
        app_dir
        / "tools"
        / "lookup_order"
        / "python_function"
        / "python_code.py",
        """def lookup_order(order_id: str, context=None) -> dict:
    \"\"\"Look up order details.\"\"\"
    cid = context.state["customer_id"]
    return {"order_id": order_id, "customer_id": cid}
""",
    )

    # 2. orchestrate_order (DAG tool calling lookup_order)
    _write_json(
        app_dir / "tools" / "orchestrate_order" / "orchestrate_order.json",
        {
            "displayName": "Orchestrate Order",
            "pythonFunction": {"name": "orchestrate_order"},
        },
    )
    _write_text(
        app_dir
        / "tools"
        / "orchestrate_order"
        / "python_function"
        / "python_code.py",
        """def orchestrate_order(order_id: str) -> dict:
    \"\"\"Call lookup_order as a DAG helper.\"\"\"
    return tools.lookup_order({"order_id": order_id})
""",
    )

    # 3. issue_refund
    _write_json(
        app_dir / "tools" / "issue_refund" / "issue_refund.json",
        {
            "displayName": "Issue Refund",
            "pythonFunction": {"name": "issue_refund"},
        },
    )
    _write_text(
        app_dir
        / "tools"
        / "issue_refund"
        / "python_function"
        / "python_code.py",
        """def issue_refund(order_id: str) -> dict:
    \"\"\"Process a refund.\"\"\"
    return {"status": "refunded"}
""",
    )

    # 4. run_diagnostics
    _write_json(
        app_dir / "tools" / "run_diagnostics" / "run_diagnostics.json",
        {
            "displayName": "Run Diagnostics",
            "pythonFunction": {"name": "run_diagnostics"},
        },
    )
    _write_text(
        app_dir
        / "tools"
        / "run_diagnostics"
        / "python_function"
        / "python_code.py",
        """def run_diagnostics() -> dict:
    \"\"\"Run line diagnostics.\"\"\"
    return {"status": "ok"}
""",
    )

    # 5. orphan_tool
    _write_json(
        app_dir / "tools" / "orphan_tool" / "orphan_tool.json",
        {
            "displayName": "Orphan Tool",
            "pythonFunction": {"name": "orphan_tool"},
        },
    )

    # Toolsets:
    _write_json(
        app_dir / "toolsets" / "crm_api" / "crm_api.json",
        {"displayName": "CRM API"},
    )
    _write_yaml(
        app_dir
        / "toolsets"
        / "crm_api"
        / "open_api_toolset"
        / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "paths": {
                "/customers": {
                    "get": {"operationId": "get_profile"},
                }
            },
        },
    )
    _write_json(
        app_dir / "toolsets" / "orphan_toolset" / "orphan_toolset.json",
        {"displayName": "Orphan Toolset"},
    )

    # Guardrails:
    _write_json(
        app_dir / "guardrails" / "global_safety" / "global_safety.json",
        {"displayName": "Global Safety", "enabled": True},
    )
    _write_json(
        app_dir / "guardrails" / "pci_compliance" / "pci_compliance.json",
        {"displayName": "PCI Compliance", "enabled": True},
    )
    _write_json(
        app_dir / "guardrails" / "orphan_guardrail" / "orphan_guardrail.json",
        {"displayName": "Orphan Guardrail", "enabled": True},
    )

    # Evaluations:
    # 1. Exported CES evaluation JSON for Billing
    _write_json(
        app_dir
        / "evaluations"
        / "billing_order_eval"
        / "billing_order_eval.json",
        {
            "displayName": "Billing Order Eval",
            "golden": {
                "variableOverrides": {"customer_id": "C-123"},
                "turns": [
                    {
                        "user": "Where is my order?",
                        "agentTransfer": {"targetAgent": "Billing"},
                        "toolCall": {"name": "lookup_order"},
                        "agentResponse": {
                            "agent": "Billing",
                            "text": "Found it.",
                        },
                    }
                ],
            },
        },
    )

    # 2. Local YAML golden for RefundSpecialist (child of Billing)
    _write_yaml(
        app_dir / "evals" / "goldens" / "refund_flow.yaml",
        {
            "conversations": [
                {
                    "conversation": "Refund request",
                    "turns": [
                        {
                            "user": "Refund my order please",
                            "tool_calls": [{"action": "issue_refund"}],
                            "agent": "Your refund is processed.",
                        }
                    ],
                }
            ]
        },
    )

    # 3. Local YAML simulation for TechSupport (sibling of Billing)
    _write_yaml(
        app_dir / "evals" / "simulations" / "tech_support_sim.yaml",
        {
            "target_agent": "TechSupport",
            "inject_variables": {"device_model": "Router-X"},
            "toolExpectation": {"tool_called": "run_diagnostics"},
        },
    )

    return app_dir


def test_variable_impact_across_all_surfaces(sample_app: Path) -> None:
    """Variable blast radius includes instructions, callbacks, tools, and evals."""
    res = analyze_target(sample_app, "variable:customer_id")

    assert res.target == "variable:customer_id"
    assert res.resource_type == "variable"
    assert res.resource_name == "customer_id"
    assert "app.json" in res.defined_in

    # Instructions referencing {customer_id}
    assert "agents/Billing/instruction.txt" in res.affected_instructions

    # Callbacks reading state.get("customer_id")
    assert (
        "agents/Billing/before_model_callbacks/gate_tools/python_code.py"
        in res.affected_callbacks
    )

    # Tools reading context.state["customer_id"] + transitive DAG caller
    assert "lookup_order" in res.affected_tools
    assert "orchestrate_order" in res.affected_tools

    # Affected agents: Billing + parent RootRouter + child RefundSpecialist
    assert "Billing" in res.affected_agents
    assert "RootRouter" in res.affected_agents
    assert "RefundSpecialist" in res.affected_agents
    assert "TechSupport" not in res.affected_agents

    # Affected evaluations: billing_order_eval + child refund_flow, NOT tech_support
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in res.affected_evaluations
    )
    assert "evals/goldens/refund_flow.yaml" in res.affected_evaluations
    assert (
        "evals/simulations/tech_support_sim.yaml"
        not in res.affected_evaluations
    )


def test_bare_target_and_transfer_rule_variable(sample_app: Path) -> None:
    """Bare variable name auto-resolves and detects transferRules conditions."""
    res = analyze_target(sample_app, "auth_tier")
    assert res.target == "variable:auth_tier"
    assert "RootRouter" in res.affected_agents
    assert "Billing" in res.affected_agents
    assert (
        "agents/Billing/before_model_callbacks/gate_tools/python_code.py"
        in res.affected_callbacks
    )


def test_environment_variable_in_global_instruction(sample_app: Path) -> None:
    """Variable in global_instruction.txt impacts all agents and evaluations."""
    res = analyze_target(sample_app, "variable:env_region")
    assert "environment.json" in res.defined_in
    assert "global_instruction.txt" in res.affected_instructions
    assert {
        "RootRouter",
        "Billing",
        "RefundSpecialist",
        "TechSupport",
    }.issubset(set(res.affected_agents))
    assert len(res.affected_evaluations) == 3


def test_tool_impact_and_dag_propagation(sample_app: Path) -> None:
    """Tool impact includes DAG callers, callbacks, instructions, and evals."""
    # Resolve via display name "Lookup Order"
    res = analyze_target(sample_app, "tool:Lookup Order")
    assert res.target == "tool:lookup_order"
    assert "lookup_order" in res.affected_tools
    assert "orchestrate_order" in res.affected_tools
    assert (
        "agents/Billing/before_model_callbacks/gate_tools/python_code.py"
        in res.affected_callbacks
    )
    assert "agents/Billing/instruction.txt" in res.affected_instructions
    assert "Billing" in res.affected_agents
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in res.affected_evaluations
    )
    assert (
        "evals/simulations/tech_support_sim.yaml"
        not in res.affected_evaluations
    )


def test_toolset_and_guardrail_impact(sample_app: Path) -> None:
    """Toolset and guardrail blast-radius resolution."""
    ts_res = analyze_target(sample_app, "toolset:crm_api")
    assert ts_res.target == "toolset:crm_api"
    assert "crm_api_get_profile" in ts_res.affected_tools
    assert "agents/Billing/instruction.txt" in ts_res.affected_instructions
    assert "Billing" in ts_res.affected_agents

    # Agent-level guardrail affects Billing and its child RefundSpecialist
    pci_res = analyze_target(sample_app, "guardrail:pci_compliance")
    assert "Billing" in pci_res.affected_agents
    assert "RefundSpecialist" in pci_res.affected_agents
    assert "TechSupport" not in pci_res.affected_agents
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in pci_res.affected_evaluations
    )
    assert "evals/goldens/refund_flow.yaml" in pci_res.affected_evaluations
    assert (
        "evals/simulations/tech_support_sim.yaml"
        not in pci_res.affected_evaluations
    )

    # App-level guardrail affects all agents and all evaluations
    global_res = analyze_target(sample_app, "guardrail:global_safety")
    assert len(global_res.affected_evaluations) == 3


def test_subagent_parent_child_transitive_impact(sample_app: Path) -> None:
    """Parent<->child agent impact propagates to evals without sibling bleed."""
    # Parent Billing -> affects child RefundSpecialist's evals
    billing_res = analyze_target(sample_app, "agent:Billing")
    assert "Billing" in billing_res.affected_agents
    assert "RefundSpecialist" in billing_res.affected_agents
    assert "RootRouter" in billing_res.affected_agents
    assert "TechSupport" not in billing_res.affected_agents
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in billing_res.affected_evaluations
    )
    assert "evals/goldens/refund_flow.yaml" in billing_res.affected_evaluations
    assert (
        "evals/simulations/tech_support_sim.yaml"
        not in billing_res.affected_evaluations
    )

    # Child RefundSpecialist -> affects parent Billing's evals, not TechSupport
    refund_res = analyze_target(sample_app, "agent:RefundSpecialist")
    assert "RefundSpecialist" in refund_res.affected_agents
    assert "Billing" in refund_res.affected_agents
    assert "TechSupport" not in refund_res.affected_agents
    assert "evals/goldens/refund_flow.yaml" in refund_res.affected_evaluations
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in refund_res.affected_evaluations
    )
    assert (
        "evals/simulations/tech_support_sim.yaml"
        not in refund_res.affected_evaluations
    )


def test_analyze_app_summary_and_orphans(sample_app: Path) -> None:
    """Summary mode returns resource counts, high-impact ranking, and orphans."""
    summary = analyze_app_summary(sample_app)
    assert summary.resource_counts["agents"] == 5
    assert summary.resource_counts["tools"] == 5
    assert summary.resource_counts["toolsets"] == 2
    assert summary.resource_counts["guardrails"] == 3
    assert summary.resource_counts["evaluations"] == 3

    assert "variable:unused_var" in summary.orphan_resources
    assert "tool:orphan_tool" in summary.orphan_resources
    assert "toolset:orphan_toolset" in summary.orphan_resources
    assert "guardrail:orphan_guardrail" in summary.orphan_resources
    assert "agent:OrphanAgent" in summary.orphan_resources
    # RootRouter is the rootAgent so it must not be flagged as an orphan
    assert "agent:RootRouter" not in summary.orphan_resources

    top_targets = [item["target"] for item in summary.high_impact_resources]
    assert "variable:customer_id" in top_targets


def test_analyze_git_diff_mode(sample_app: Path) -> None:
    """Git diff mode detects modified variable declarations and tool changes."""
    subprocess.run(["git", "init"], cwd=str(sample_app), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(sample_app),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(sample_app),
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(sample_app), check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(sample_app),
        check=True,
    )

    # Modify customer_id description in app.json and run_diagnostics tool code
    app_json_path = sample_app / "app.json"
    app_data = json.loads(app_json_path.read_text(encoding="utf-8"))
    app_data["variableDeclarations"][0]["description"] = "Updated description"
    _write_json(app_json_path, app_data)

    diag_py = (
        sample_app
        / "tools"
        / "run_diagnostics"
        / "python_function"
        / "python_code.py"
    )
    _write_text(
        diag_py,
        """def run_diagnostics() -> dict:
    \"\"\"Updated diagnostics.\"\"\"
    return {"status": "healthy"}
""",
    )

    diff_res = analyze_git_diff(sample_app, git_ref="HEAD")
    assert "app.json" in diff_res.changed_files
    assert (
        "tools/run_diagnostics/python_function/python_code.py"
        in diff_res.changed_files
    )
    # Only customer_id was changed in app.json, not auth_tier or unused_var
    assert "variable:customer_id" in diff_res.changed_resources
    assert "variable:auth_tier" not in diff_res.changed_resources
    assert "tool:run_diagnostics" in diff_res.changed_resources

    assert "Billing" in diff_res.affected_agents
    assert "TechSupport" in diff_res.affected_agents
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in diff_res.affected_evaluations
    )
    assert (
        "evals/simulations/tech_support_sim.yaml"
        in diff_res.affected_evaluations
    )


def test_cli_json_and_exit_codes(
    sample_app: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI parser and handler support --target, --diff, --json, and exit codes."""
    parser = get_parser()

    # 1. --target with --json
    args = parser.parse_args(
        [
            "impact",
            "--app-dir",
            str(sample_app),
            "--target",
            "variable:customer_id",
            "--json",
        ]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args)
    assert exc_info.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["target"] == "variable:customer_id"
    assert (
        "evaluations/billing_order_eval/billing_order_eval.json"
        in out["affected_evaluations"]
    )

    # 2. Summary mode (human-readable)
    args_summary = parser.parse_args(["impact", "--app-dir", str(sample_app)])
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_summary)
    assert exc_info.value.code == 0
    text_out = capsys.readouterr().out
    assert "CXAS Dependency & Impact Summary" in text_out
    assert "Orphan / Unreferenced Resources" in text_out

    # 3. Unknown target exits with code 1
    args_bad = parser.parse_args(
        [
            "impact",
            "--app-dir",
            str(sample_app),
            "--target",
            "variable:does_not_exist",
            "--json",
        ]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_bad)
    assert exc_info.value.code == 1
    err_json = json.loads(capsys.readouterr().out)
    assert "error" in err_json

    # 4. Conflicting --target and --diff exits with code 1
    args_conflict = parser.parse_args(
        [
            "impact",
            "--app-dir",
            str(sample_app),
            "--target",
            "variable:customer_id",
            "--diff",
            "HEAD",
        ]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_conflict)
    assert exc_info.value.code == 1


def test_cyclic_agent_transfers_and_cyclic_dag_tools(tmp_path: Path) -> None:
    """Mutual agent transfers (A <-> B) and tool cycles terminate cleanly."""
    app_dir = tmp_path / "cyclic_app"
    app_dir.mkdir()
    _write_json(
        app_dir / "app.json",
        {"displayName": "Cyclic App", "rootAgent": "AgentA"},
    )
    for agent_name, peer in (("AgentA", "AgentB"), ("AgentB", "AgentA")):
        _write_json(
            app_dir / "agents" / agent_name / f"{agent_name}.json",
            {
                "displayName": agent_name,
                "childAgents": [peer],
                "transferRules": [
                    {"childAgent": peer, "condition": "session.params.loop_ok"}
                ],
            },
        )
        _write_text(
            app_dir / "agents" / agent_name / "instruction.txt",
            f"Bounce to {{@AGENT: {peer}}}.\n",
        )
        _write_text(
            app_dir
            / "agents"
            / agent_name
            / "before_agent_callbacks_01"
            / "python_code.py",
            f'def before_agent(ctx):\n    transfer_to_agent("{peer}")\n',
        )

    for tname, peer_tool in (("tool_a", "tool_b"), ("tool_b", "tool_a")):
        _write_json(
            app_dir / "tools" / tname / f"{tname}.json",
            {"displayName": tname},
        )
        _write_text(
            app_dir / "tools" / tname / "python_function" / "python_code.py",
            f"def {tname}():\n    return tools.{peer_tool}({{}})\n",
        )

    res_a = analyze_target(app_dir, "agent:AgentA")
    assert set(res_a.affected_agents) == {"AgentA", "AgentB"}

    res_tool = analyze_target(app_dir, "tool:tool_a")
    assert set(res_tool.affected_tools) == {"tool_a", "tool_b"}

    summary = analyze_app_summary(app_dir)
    assert summary.resource_counts["agents"] == 2
    assert summary.resource_counts["tools"] == 2


def test_ambiguous_bare_target_names_and_explicit_prefixes(
    tmp_path: Path,
) -> None:
    """Explicit type prefixes disambiguate resources sharing a bare name."""
    app_dir = tmp_path / "ambig_app"
    app_dir.mkdir()
    _write_json(
        app_dir / "app.json",
        {"displayName": "Ambig App", "rootAgent": "billing"},
    )
    _write_json(
        app_dir / "agents" / "billing" / "billing.json",
        {"displayName": "billing", "tools": ["billing"]},
    )
    _write_json(
        app_dir / "tools" / "billing" / "billing.json",
        {"displayName": "billing"},
    )

    res_tool = analyze_target(app_dir, "tool:billing")
    assert res_tool.target == "tool:billing"
    assert res_tool.resource_type == "tool"

    res_agent = analyze_target(app_dir, "agent:billing")
    assert res_agent.target == "agent:billing"
    assert res_agent.resource_type == "agent"

    # Bare name resolves deterministically according to RESOURCE_TYPES priority
    res_bare = analyze_target(app_dir, "billing")
    assert res_bare.target == "tool:billing"

    with pytest.raises(ValueError, match="Unknown resource type"):
        analyze_target(app_dir, "invalid_type:billing")


def test_flat_callback_layout_and_multihop_dag_tools(tmp_path: Path) -> None:
    """Flat callback directories and multi-hop DAG tool chains are tracked."""
    app_dir = tmp_path / "flat_cb_app"
    app_dir.mkdir()
    _write_json(
        app_dir / "app.json",
        {
            "displayName": "Flat Callback App",
            "rootAgent": "MainAgent",
            "variableDeclarations": [
                {"name": "session_token", "schema": {"type": "STRING"}}
            ],
        },
    )
    _write_json(
        app_dir / "agents" / "MainAgent" / "MainAgent.json",
        {"displayName": "Main Agent", "tools": ["top_dag_tool"]},
    )
    _write_text(
        app_dir
        / "agents"
        / "MainAgent"
        / "before_model_callbacks_01"
        / "python_code.py",
        """def before_model_callback(callback_context, llm_request):
    tok = callback_context.state.get("session_token")
    if not tok:
        hide_tool(llm_request, "leaf_tool")
""",
    )

    _write_json(
        app_dir / "tools" / "leaf_tool" / "leaf_tool.json",
        {"displayName": "leaf_tool"},
    )
    _write_text(
        app_dir / "tools" / "leaf_tool" / "python_function" / "python_code.py",
        """def leaf_tool(context=None):
    return context.state["session_token"]
""",
    )
    _write_json(
        app_dir / "tools" / "mid_dag_tool" / "mid_dag_tool.json",
        {"displayName": "mid_dag_tool"},
    )
    _write_text(
        app_dir
        / "tools"
        / "mid_dag_tool"
        / "python_function"
        / "python_code.py",
        """def mid_dag_tool():
    return tools.leaf_tool({"id": 1})
""",
    )
    _write_json(
        app_dir / "tools" / "top_dag_tool" / "top_dag_tool.json",
        {"displayName": "top_dag_tool"},
    )
    _write_text(
        app_dir
        / "tools"
        / "top_dag_tool"
        / "python_function"
        / "python_code.py",
        """def top_dag_tool():
    return tools.mid_dag_tool({})
""",
    )

    res = analyze_target(app_dir, "variable:session_token")
    assert (
        "agents/MainAgent/before_model_callbacks_01/python_code.py"
        in res.affected_callbacks
    )
    assert set(res.affected_tools) == {
        "leaf_tool",
        "mid_dag_tool",
        "top_dag_tool",
    }
    assert "MainAgent" in res.affected_agents


def test_git_diff_in_monorepo_subdirectory_with_deletions(
    tmp_path: Path,
) -> None:
    """Git diff resolves relative paths inside a monorepo and tracks deletions."""
    repo_root = tmp_path / "monorepo"
    app_dir = repo_root / "apps" / "support_agent"
    app_dir.mkdir(parents=True)
    _write_text(repo_root / "README.md", "Monorepo root\n")

    _write_json(
        app_dir / "app.json",
        {
            "displayName": "Support Agent",
            "rootAgent": "Root",
            "variableDeclarations": [
                {"name": "keep_var", "schema": {"type": "STRING"}},
                {"name": "deleted_var", "schema": {"type": "STRING"}},
            ],
        },
    )
    _write_json(
        app_dir / "agents" / "Root" / "Root.json",
        {"displayName": "Root", "tools": ["deleted_tool"]},
    )
    _write_text(
        app_dir / "agents" / "Root" / "instruction.txt",
        "Use {deleted_var} and {keep_var}.\n",
    )
    _write_json(
        app_dir / "tools" / "deleted_tool" / "deleted_tool.json",
        {"displayName": "deleted_tool"},
    )
    _write_text(
        app_dir
        / "tools"
        / "deleted_tool"
        / "python_function"
        / "python_code.py",
        "def deleted_tool():\n    return {}\n",
    )

    subprocess.run(["git", "init"], cwd=str(repo_root), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo_root),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo_root),
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo_root), check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo_root),
        check=True,
    )

    # Modify file outside app_dir, delete deleted_var from app.json, and remove
    # deleted_tool directory from the working tree.
    _write_text(repo_root / "README.md", "Updated monorepo root\n")
    _write_json(
        app_dir / "app.json",
        {
            "displayName": "Support Agent",
            "rootAgent": "Root",
            "variableDeclarations": [
                {"name": "keep_var", "schema": {"type": "STRING"}},
            ],
        },
    )
    for child in sorted(
        (app_dir / "tools" / "deleted_tool").rglob("*"), reverse=True
    ):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    (app_dir / "tools" / "deleted_tool").rmdir()

    diff_res = analyze_git_diff(app_dir, git_ref="HEAD")
    assert "README.md" not in diff_res.changed_files
    assert "app.json" in diff_res.changed_files
    assert "variable:deleted_var" in diff_res.changed_resources
    assert "variable:keep_var" not in diff_res.changed_resources
    assert "tool:deleted_tool" in diff_res.changed_resources
    assert "Root" in diff_res.affected_agents
    assert "agents/Root/instruction.txt" in diff_res.affected_instructions


def test_cli_invalid_app_dir_and_invalid_git_ref_errors(
    sample_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI handles nonexistent dir, file path, and invalid git ref cleanly."""
    parser = get_parser()

    # 1. Nonexistent --app-dir
    args_nonexistent = parser.parse_args(
        ["impact", "--app-dir", str(tmp_path / "does_not_exist"), "--json"]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_nonexistent)
    assert exc_info.value.code == 1
    err_nonexistent = json.loads(capsys.readouterr().out)
    assert "does not exist" in err_nonexistent["error"]

    # 2. File passed to --app-dir instead of directory
    file_path = tmp_path / "file.txt"
    _write_text(file_path, "not an app dir\n")
    args_file = parser.parse_args(
        ["impact", "--app-dir", str(file_path), "--json"]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_file)
    assert exc_info.value.code == 1
    err_file = json.loads(capsys.readouterr().out)
    assert "error" in err_file

    # 3. Invalid --diff git ref
    subprocess.run(["git", "init"], cwd=str(sample_app), check=True)
    args_bad_ref = parser.parse_args(
        [
            "impact",
            "--app-dir",
            str(sample_app),
            "--diff",
            "invalid_git_ref_xyz",
            "--json",
        ]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_impact(args_bad_ref)
    assert exc_info.value.code == 1
    err_ref = json.loads(capsys.readouterr().out)
    assert "invalid_git_ref_xyz" in err_ref["error"]


def test_git_diff_deleted_resource_display_name_and_py_fn_aliases(
    tmp_path: Path,
) -> None:
    """Deleted resources in git diff resolve displayName and pythonFunction."""
    file_path = tmp_path / "not_a_dir.json"
    _write_text(file_path, "{}\n")
    with pytest.raises(ValueError, match="not a directory"):
        analyze_target(file_path, "tool:any")

    app_dir = tmp_path / "alias_diff_app"
    app_dir.mkdir()
    _write_json(
        app_dir / "app.json",
        {"displayName": "Alias Diff App", "rootAgent": "Root"},
    )
    _write_json(
        app_dir / "agents" / "Root" / "Root.json",
        {"displayName": "Root"},
    )
    _write_text(
        app_dir / "agents" / "Root" / "instruction.txt",
        "Call {@TOOL: Custom Display Tool} to look up records.\n",
    )
    _write_text(
        app_dir
        / "agents"
        / "Root"
        / "before_model_callbacks_01"
        / "python_code.py",
        'def cb(ctx, req):\n    hide_tool(req, "custom_py_fn_name")\n',
    )
    _write_json(
        app_dir / "tools" / "del_tool_id" / "del_tool_id.json",
        {
            "displayName": "Custom Display Tool",
            "pythonFunction": {"name": "custom_py_fn_name"},
        },
    )
    _write_json(
        app_dir / "evaluations" / "alias_eval.json",
        {
            "displayName": "alias_eval",
            "turns": [{"toolCall": {"displayName": "Custom Display Tool"}}],
        },
    )

    subprocess.run(["git", "init"], cwd=str(app_dir), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(app_dir),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(app_dir),
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(app_dir), check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(app_dir),
        check=True,
    )

    # Delete the tool JSON file in the working tree
    (app_dir / "tools" / "del_tool_id" / "del_tool_id.json").unlink()
    (app_dir / "tools" / "del_tool_id").rmdir()

    diff_res = analyze_git_diff(app_dir, git_ref="HEAD")
    assert "tool:del_tool_id" in diff_res.changed_resources
    assert "Root" in diff_res.affected_agents
    assert "agents/Root/instruction.txt" in diff_res.affected_instructions
    assert (
        "agents/Root/before_model_callbacks_01/python_code.py"
        in diff_res.affected_callbacks
    )
    assert "evaluations/alias_eval.json" in diff_res.affected_evaluations
