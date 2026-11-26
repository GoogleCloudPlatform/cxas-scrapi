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

"""Hermetic unit tests for the CXAS In-Flight Prompt Hot-Patching Engine."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from google.cloud.ces_v1beta import types

from cxas_scrapi.core.hot_patch import (
    EtagConflictError,
    HotPatchApplyError,
    HotPatchEngine,
    ScopedLintError,
    StateManifest,
)

if TYPE_CHECKING:
    from pathlib import Path

APP_NAME = "projects/demo-proj/locations/us-central1/apps/support-app"
BILLING_AGENT_RES = f"{APP_NAME}/agents/billing-001"
TRIAGE_AGENT_RES = f"{APP_NAME}/agents/triage-002"
LOOKUP_TOOL_RES = f"{APP_NAME}/tools/lookup-001"


class FakeCesBackend:
    """High-fidelity in-memory CES control plane with FieldMask & etag checks."""

    def __init__(self) -> None:
        self._etag_seq = 10
        self.app = types.App(
            name=APP_NAME,
            display_name="Support App",
            global_instruction="Always be polite and concise.",
            root_agent=TRIAGE_AGENT_RES,
            etag="etag-app-10",
        )
        self.agents: dict[str, types.Agent] = {
            TRIAGE_AGENT_RES: types.Agent(
                name=TRIAGE_AGENT_RES,
                display_name="Triage Agent",
                instruction="Route callers to Billing.",
                etag="etag-triage-10",
                before_agent_callbacks=[
                    types.Callback(
                        python_code=(
                            "def before_agent_callback(callback_context):\n"
                            "    return None\n"
                        ),
                        description="Init auth state",
                        disabled=False,
                    )
                ],
            ),
            BILLING_AGENT_RES: types.Agent(
                name=BILLING_AGENT_RES,
                display_name="Billing Agent",
                instruction="Assist callers with invoices.",
                etag="etag-billing-10",
            ),
        }
        self.tools: dict[str, types.Tool] = {
            LOOKUP_TOOL_RES: types.Tool(
                name=LOOKUP_TOOL_RES,
                display_name="lookup_invoice",
                etag="etag-tool-10",
                python_function=types.PythonFunction(
                    name="lookup_invoice",
                    python_code="def lookup_invoice(inv_id: str):\n    return {'id': inv_id}\n",
                ),
            )
        }
        self.guardrails: dict[str, types.Guardrail] = {}
        self.fail_on_agent_update: str | None = None
        self.update_app_calls: list[types.UpdateAppRequest] = []
        self.update_agent_calls: list[types.UpdateAgentRequest] = []
        self.create_agent_calls: list[types.CreateAgentRequest] = []
        self.delete_agent_calls: list[types.DeleteAgentRequest] = []
        self.update_tool_calls: list[types.UpdateToolRequest] = []
        self.create_tool_calls: list[types.CreateToolRequest] = []
        self.delete_tool_calls: list[types.DeleteToolRequest] = []
        self.create_guardrail_calls: list[types.CreateGuardrailRequest] = []
        self.update_guardrail_calls: list[types.UpdateGuardrailRequest] = []
        self.delete_guardrail_calls: list[types.DeleteGuardrailRequest] = []

    def _next_etag(self, label: str) -> str:
        self._etag_seq += 1
        return f"etag-{label}-{self._etag_seq}"

    def _verify_agent_references(self, agent: types.Agent) -> None:
        for t_res in agent.tools:
            if t_res not in self.tools:
                raise ValueError(
                    f"400 INVALID_ARGUMENT: Referenced tool '{t_res}' does not exist yet."
                )
        for g_res in agent.guardrails:
            if g_res not in self.guardrails:
                raise ValueError(
                    f"400 INVALID_ARGUMENT: Referenced guardrail '{g_res}' does not exist yet."
                )
        for c_res in agent.child_agents:
            if c_res not in self.agents:
                raise ValueError(
                    f"400 INVALID_ARGUMENT: Referenced child_agent '{c_res}' does not exist yet."
                )

    def update_app(self, *, request: types.UpdateAppRequest) -> types.App:
        if request.app.etag and request.app.etag != self.app.etag:
            raise RuntimeError(
                f"409 ABORTED: App etag mismatch ({request.app.etag} != {self.app.etag})"
            )
        for path in request.update_mask.paths:
            if path == "guardrails":
                for g_res in request.app.guardrails:
                    if g_res not in self.guardrails:
                        raise ValueError(
                            f"400 INVALID_ARGUMENT: App guardrail '{g_res}' does not exist yet."
                        )
        self.update_app_calls.append(request)
        for path in request.update_mask.paths:
            if path == "global_instruction":
                self.app.global_instruction = request.app.global_instruction
            elif path == "guardrails":
                self.app.guardrails.clear()
                self.app.guardrails.extend(request.app.guardrails)
            elif path == "root_agent":
                self.app.root_agent = request.app.root_agent
            elif path == "description":
                self.app.description = request.app.description
        self.app.etag = self._next_etag("app")
        return types.App.deserialize(types.App.serialize(self.app))

    def create_agent(self, *, request: types.CreateAgentRequest) -> types.Agent:
        self._verify_agent_references(request.agent)
        self.create_agent_calls.append(request)
        res_name = f"{request.parent}/agents/{request.agent_id}"
        stored = types.Agent.deserialize(types.Agent.serialize(request.agent))
        stored.name = res_name
        stored.etag = self._next_etag("agent")
        self.agents[res_name] = stored
        return types.Agent.deserialize(types.Agent.serialize(stored))

    def update_agent(self, *, request: types.UpdateAgentRequest) -> types.Agent:
        target = self.agents[request.agent.name]
        if request.agent.etag and request.agent.etag != target.etag:
            raise RuntimeError(
                f"409 ABORTED: Agent etag mismatch ({request.agent.etag} != {target.etag})"
            )
        if self.fail_on_agent_update == request.agent.name:
            raise ValueError(
                "400 INVALID_ARGUMENT: Before agent callback takes one "
                "positional argument but received 3."
            )
        self._verify_agent_references(request.agent)
        self.update_agent_calls.append(request)
        for path in request.update_mask.paths:
            if path in ("instruction", "description", "display_name"):
                setattr(target, path, getattr(request.agent, path))
            else:
                getattr(target, path).clear()
                getattr(target, path).extend(getattr(request.agent, path))
        target.etag = self._next_etag("agent")
        return types.Agent.deserialize(types.Agent.serialize(target))

    def delete_agent(self, *, request: types.DeleteAgentRequest) -> None:
        for ag in self.agents.values():
            if ag.name != request.name and request.name in ag.child_agents:
                raise RuntimeError(
                    f"400 FAILED_PRECONDITION: Cannot delete agent '{request.name}' "
                    f"while parent '{ag.name}' still references it in child_agents."
                )
        self.delete_agent_calls.append(request)
        self.agents.pop(request.name, None)

    def create_tool(self, *, request: types.CreateToolRequest) -> types.Tool:
        self.create_tool_calls.append(request)
        res_name = f"{request.parent}/tools/{request.tool_id}"
        stored = types.Tool.deserialize(types.Tool.serialize(request.tool))
        stored.name = res_name
        stored.etag = self._next_etag("tool")
        self.tools[res_name] = stored
        return types.Tool.deserialize(types.Tool.serialize(stored))

    def update_tool(self, *, request: types.UpdateToolRequest) -> types.Tool:
        target = self.tools[request.tool.name]
        if request.tool.etag and request.tool.etag != target.etag:
            raise RuntimeError("409 ABORTED: Tool etag mismatch")
        self.update_tool_calls.append(request)
        for path in request.update_mask.paths:
            if path == "python_function.python_code":
                target.python_function.python_code = (
                    request.tool.python_function.python_code
                )
        target.etag = self._next_etag("tool")
        return types.Tool.deserialize(types.Tool.serialize(target))

    def delete_tool(self, *, request: types.DeleteToolRequest) -> None:
        for ag in self.agents.values():
            if request.name in ag.tools:
                raise RuntimeError(
                    f"400 FAILED_PRECONDITION: Cannot delete tool '{request.name}' "
                    f"while agent '{ag.name}' still references it."
                )
        self.delete_tool_calls.append(request)
        self.tools.pop(request.name, None)

    def create_guardrail(
        self, *, request: types.CreateGuardrailRequest
    ) -> types.Guardrail:
        self.create_guardrail_calls.append(request)
        res_name = f"{request.parent}/guardrails/{request.guardrail_id}"
        stored = types.Guardrail.deserialize(
            types.Guardrail.serialize(request.guardrail)
        )
        stored.name = res_name
        stored.etag = self._next_etag("guardrail")
        self.guardrails[res_name] = stored
        return types.Guardrail.deserialize(types.Guardrail.serialize(stored))

    def update_guardrail(
        self, *, request: types.UpdateGuardrailRequest
    ) -> types.Guardrail:
        target = self.guardrails[request.guardrail.name]
        if request.guardrail.etag and request.guardrail.etag != target.etag:
            raise RuntimeError("409 ABORTED: Guardrail etag mismatch")
        self.update_guardrail_calls.append(request)
        stored = types.Guardrail.deserialize(
            types.Guardrail.serialize(request.guardrail)
        )
        stored.etag = self._next_etag("guardrail")
        self.guardrails[stored.name] = stored
        return types.Guardrail.deserialize(types.Guardrail.serialize(stored))

    def delete_guardrail(
        self, *, request: types.DeleteGuardrailRequest
    ) -> None:
        for ag in self.agents.values():
            if request.name in ag.guardrails:
                raise RuntimeError(
                    f"400 FAILED_PRECONDITION: Cannot delete guardrail '{request.name}' "
                    f"while agent '{ag.name}' still references it."
                )
        self.delete_guardrail_calls.append(request)
        self.guardrails.pop(request.name, None)

    def list_guardrails(
        self, *, request: types.ListGuardrailsRequest
    ) -> list[types.Guardrail]:
        return [
            types.Guardrail.deserialize(types.Guardrail.serialize(g))
            for g in self.guardrails.values()
        ]

    def get_guardrail(
        self, *, request: types.GetGuardrailRequest
    ) -> types.Guardrail:
        return types.Guardrail.deserialize(
            types.Guardrail.serialize(self.guardrails[request.name])
        )


class FakeAppsClient:
    def __init__(self, backend: FakeCesBackend) -> None:
        self.backend = backend
        self.creds = object()
        self.client = backend

    def get_app(self, app_name: str) -> types.App:
        assert app_name == self.backend.app.name
        return types.App.deserialize(types.App.serialize(self.backend.app))


class FakeAgentsClient:
    def __init__(self, backend: FakeCesBackend) -> None:
        self.backend = backend
        self.client = backend

    def list_agents(self) -> list[types.Agent]:
        return [
            types.Agent.deserialize(types.Agent.serialize(a))
            for a in self.backend.agents.values()
        ]

    def get_agent(self, agent_name: str) -> types.Agent:
        return types.Agent.deserialize(
            types.Agent.serialize(self.backend.agents[agent_name])
        )


class FakeToolsClient:
    def __init__(self, backend: FakeCesBackend) -> None:
        self.backend = backend
        self.client = backend

    def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool.deserialize(types.Tool.serialize(t))
            for t in self.backend.tools.values()
        ]

    def get_tool(self, tool_name: str) -> types.Tool:
        return types.Tool.deserialize(
            types.Tool.serialize(self.backend.tools[tool_name])
        )


def create_workspace(root: Path) -> Path:
    """Creates a realistic local CXAS workspace matching FakeCesBackend."""
    (root / "app.json").write_text(
        json.dumps({"displayName": "Support App", "rootAgent": "triage_agent"}),
        encoding="utf-8",
    )
    (root / "global_instruction.txt").write_text(
        "Always be polite and concise.",
        encoding="utf-8",
    )

    triage_dir = root / "agents" / "triage_agent"
    triage_dir.mkdir(parents=True)
    (triage_dir / "triage_agent.json").write_text(
        json.dumps({"displayName": "Triage Agent"}),
        encoding="utf-8",
    )
    (triage_dir / "instruction.txt").write_text(
        "Route callers to Billing.",
        encoding="utf-8",
    )
    cb_dir = triage_dir / "before_agent_callbacks" / "cb_01"
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n    return None\n",
        encoding="utf-8",
    )

    billing_dir = root / "agents" / "billing_agent"
    billing_dir.mkdir(parents=True)
    (billing_dir / "billing_agent.json").write_text(
        json.dumps({"displayName": "Billing Agent"}),
        encoding="utf-8",
    )
    (billing_dir / "instruction.txt").write_text(
        "Assist callers with invoices.",
        encoding="utf-8",
    )

    tool_dir = root / "tools" / "lookup_invoice" / "python_function"
    tool_dir.mkdir(parents=True)
    (root / "tools" / "lookup_invoice" / "lookup_invoice.json").write_text(
        json.dumps({"displayName": "lookup_invoice"}),
        encoding="utf-8",
    )
    (tool_dir / "python_code.py").write_text(
        "def lookup_invoice(inv_id: str):\n    return {'id': inv_id}\n",
        encoding="utf-8",
    )
    return root


def make_engine(workspace: Path, backend: FakeCesBackend) -> HotPatchEngine:
    return HotPatchEngine(
        app_name=APP_NAME,
        app_dir=workspace,
        apps_client=FakeAppsClient(backend),  # type: ignore[arg-type]
        agents_client=FakeAgentsClient(backend),  # type: ignore[arg-type]
        tools_client=FakeToolsClient(backend),  # type: ignore[arg-type]
    )


def test_bootstrap_and_surgical_single_agent_patch(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)

    (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Assist {{ customer_name }} with invoices using {@TOOL: lookup_invoice}.",
        encoding="utf-8",
    )

    report = engine.apply_patch()

    assert report.status == "patched"
    assert report.bootstrapped_from_remote is True
    assert len(report.deltas) == 1
    assert report.deltas[0].rel_path == "agents/billing_agent/instruction.txt"
    assert len(backend.update_agent_calls) == 1
    assert list(backend.update_agent_calls[0].update_mask.paths) == [
        "instruction"
    ]
    assert len(backend.update_app_calls) == 0
    assert "lookup_invoice" in backend.agents[BILLING_AGENT_RES].instruction

    noop_report = engine.apply_patch()
    assert noop_report.status == "noop"
    assert noop_report.bootstrapped_from_remote is False
    assert len(backend.update_agent_calls) == 1


def test_combined_instruction_and_callback_merges_into_single_rpc(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    assert engine.apply_patch().status == "noop"

    triage_dir = workspace / "agents" / "triage_agent"
    (triage_dir / "instruction.txt").write_text(
        "Greet caller warmly and route to {@AGENT: billing_agent}.",
        encoding="utf-8",
    )
    (
        triage_dir / "before_agent_callbacks" / "cb_01" / "python_code.py"
    ).write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['patched'] = 'true'\n"
        "    return None\n",
        encoding="utf-8",
    )

    report = engine.apply_patch()

    assert report.status == "patched"
    assert len(report.applied_patches) == 1
    patch_res = report.applied_patches[0]
    assert patch_res.field_mask_paths == [
        "instruction",
        "before_agent_callbacks",
    ]
    assert len(backend.update_agent_calls) == 1
    updated_cb = backend.agents[TRIAGE_AGENT_RES].before_agent_callbacks[0]
    assert "patched" in updated_cb.python_code
    assert updated_cb.description == "Init auth state"


def test_scoped_preflight_linter_catches_jinja_and_tool_errors(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    engine.apply_patch()

    (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Hello {{ unclosed_var and call {@TOOL: nonexistent_tool}",
        encoding="utf-8",
    )

    with pytest.raises(ScopedLintError) as exc_info:
        engine.apply_patch()

    rule_ids = {f.rule_id for f in exc_info.value.findings}
    assert "HP-I003" in rule_ids
    assert "HP-I004" in rule_ids
    assert len(backend.update_agent_calls) == 0


def test_scoped_preflight_linter_catches_callback_arity_and_sandbox_imports(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    engine.apply_patch()

    cb_file = (
        workspace
        / "agents"
        / "triage_agent"
        / "before_agent_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb_file.write_text(
        "from google.adk import CallbackContext\n\n"
        "def before_agent_callback(ctx, extra_arg):\n"
        "    return None\n",
        encoding="utf-8",
    )

    with pytest.raises(ScopedLintError) as exc_info:
        engine.apply_patch()

    rule_ids = {f.rule_id for f in exc_info.value.findings}
    assert "HP-C002" in rule_ids
    assert "HP-C004" in rule_ids
    assert len(backend.update_agent_calls) == 0


def test_etag_optimistic_concurrency_blocks_overwrite_unless_forced(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    engine.apply_patch()

    backend.agents[BILLING_AGENT_RES].instruction = "Console edit v2"
    backend.agents[BILLING_AGENT_RES].etag = "etag-billing-99"

    (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Local conflicting edit",
        encoding="utf-8",
    )

    with pytest.raises(EtagConflictError) as exc_info:
        engine.apply_patch(force_etag=False)

    assert exc_info.value.expected_etag == "etag-billing-10"
    assert exc_info.value.remote_etag == "etag-billing-99"
    assert backend.agents[BILLING_AGENT_RES].instruction == "Console edit v2"

    forced = engine.apply_patch(force_etag=True)
    assert forced.status == "patched"
    assert (
        backend.agents[BILLING_AGENT_RES].instruction
        == "Local conflicting edit"
    )


def test_atomic_rollback_restores_all_resources_on_partial_failure(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    engine.apply_patch()

    orig_global = backend.app.global_instruction
    orig_billing = backend.agents[BILLING_AGENT_RES].instruction
    orig_triage = backend.agents[TRIAGE_AGENT_RES].instruction

    (workspace / "global_instruction.txt").write_text(
        "Transient global instruction",
        encoding="utf-8",
    )
    (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Transient billing instruction",
        encoding="utf-8",
    )
    (workspace / "agents" / "triage_agent" / "instruction.txt").write_text(
        "Transient triage instruction",
        encoding="utf-8",
    )

    backend.fail_on_agent_update = TRIAGE_AGENT_RES

    with pytest.raises(HotPatchApplyError) as exc_info:
        engine.apply_patch()

    failed_report = exc_info.value.report
    assert failed_report.status == "rolled_back"
    assert len(failed_report.applied_patches) == 2
    assert len(failed_report.rollback_actions) == 2
    assert all(rb.restored for rb in failed_report.rollback_actions)

    assert backend.app.global_instruction == orig_global
    assert backend.agents[BILLING_AGENT_RES].instruction == orig_billing
    assert backend.agents[TRIAGE_AGENT_RES].instruction == orig_triage

    saved_manifest = StateManifest.load(workspace, APP_NAME)
    assert saved_manifest is not None
    assert (
        saved_manifest.files["global_instruction.txt"]["sha256"]
        != failed_report.deltas[0].new_sha256
    )


def test_topological_dag_creates_tool_guardrail_subagent_and_binds_parent_without_importapp(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    assert engine.apply_patch().status == "noop"

    # 1. Create a brand-new Python Function Tool (Level 1 leaf)
    new_tool_dir = (
        workspace / "tools" / "z_refund_calculator" / "python_function"
    )
    new_tool_dir.mkdir(parents=True)
    (
        workspace / "tools" / "z_refund_calculator" / "z_refund_calculator.json"
    ).write_text(
        json.dumps(
            {
                "displayName": "z_refund_calculator",
                "description": "Calculates instant customer refund eligibility",
            }
        ),
        encoding="utf-8",
    )
    (new_tool_dir / "python_code.py").write_text(
        "def z_refund_calculator(order_id: str) -> dict:\n"
        "    return {'order_id': order_id, 'refundable': True, 'amount': 125.0}\n",
        encoding="utf-8",
    )

    # 2. Create a brand-new Guardrail (Level 1 leaf)
    gr_dir = workspace / "guardrails" / "pii_shield"
    gr_dir.mkdir(parents=True)
    (gr_dir / "pii_shield.json").write_text(
        json.dumps(
            {
                "displayName": "PII Shield Guardrail",
                "description": "Blocks raw SSN or card PINs",
                "enabled": True,
                "contentFilter": {
                    "bannedContents": ["ssn", "card pin"],
                    "matchType": "SIMPLE_STRING_MATCH",
                },
                "action": {
                    "respondImmediately": {
                        "responses": [
                            {"text": "Please do not share sensitive PINs."}
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. Create a brand-new Sub-Agent named 'z_refund_specialist' (sorts alphabetically
    # AFTER 'triage_agent', proving topological sort executes it at Level 2 BEFORE parent!)
    sub_dir = workspace / "agents" / "z_refund_specialist"
    sub_dir.mkdir(parents=True)
    (sub_dir / "z_refund_specialist.json").write_text(
        json.dumps(
            {
                "displayName": "Refund Specialist",
                "description": "Handles refund calculations",
                "tools": ["z_refund_calculator"],
                "guardrails": ["pii_shield"],
            }
        ),
        encoding="utf-8",
    )
    (sub_dir / "instruction.txt").write_text(
        "You are the Refund Specialist. Use {@TOOL: z_refund_calculator} to calculate refunds.",
        encoding="utf-8",
    )

    # 4. Bind the new sub-agent and guardrail into parent 'triage_agent' (Level 3)
    triage_dir = workspace / "agents" / "triage_agent"
    (triage_dir / "triage_agent.json").write_text(
        json.dumps(
            {
                "displayName": "Triage Agent",
                "childAgents": ["billing_agent", "z_refund_specialist"],
                "guardrails": ["pii_shield"],
            }
        ),
        encoding="utf-8",
    )
    (triage_dir / "instruction.txt").write_text(
        "Route billing to {@AGENT: billing_agent} and refunds to {@AGENT: z_refund_specialist}.",
        encoding="utf-8",
    )

    # 5. Bind the new guardrail into app.json (Level 4)
    (workspace / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Support App",
                "rootAgent": "triage_agent",
                "guardrails": ["pii_shield"],
            }
        ),
        encoding="utf-8",
    )

    report = engine.apply_patch()

    assert report.status == "patched"
    assert len(report.applied_patches) == 5

    # Verify strict topological execution order:
    # Step 0: L1 CREATE tool (z_refund_calculator)
    # Step 1: L1 CREATE guardrail (pii_shield)
    # Step 2: L2 CREATE agent (z_refund_specialist)
    # Step 3: L3 UPDATE agent (triage_agent)
    # Step 4: L4 UPDATE app (Support App)
    steps = [
        (p.dag_level, p.operation, p.resource_type, p.resource_key)
        for p in report.applied_patches
    ]
    assert steps == [
        (1, "CREATE", "tool", "z_refund_calculator"),
        (1, "CREATE", "guardrail", "pii_shield"),
        (2, "CREATE", "agent", "z_refund_specialist"),
        (3, "UPDATE", "agent", "triage_agent"),
        (4, "UPDATE", "app", "app"),
    ]

    created_tool_res = f"{APP_NAME}/tools/z_refund_calculator"
    created_gr_res = f"{APP_NAME}/guardrails/pii_shield"
    created_sub_res = f"{APP_NAME}/agents/z_refund_specialist"

    assert created_tool_res in backend.tools
    assert created_gr_res in backend.guardrails
    assert created_sub_res in backend.agents
    assert list(backend.agents[created_sub_res].tools) == [created_tool_res]
    assert list(backend.agents[created_sub_res].guardrails) == [created_gr_res]
    assert created_sub_res in backend.agents[TRIAGE_AGENT_RES].child_agents
    assert created_gr_res in backend.agents[TRIAGE_AGENT_RES].guardrails
    assert list(backend.app.guardrails) == [created_gr_res]

    # Verify subsequent run is a 0-RPC noop
    noop = engine.apply_patch()
    assert noop.status == "noop"


def test_reverse_topological_rollback_deletes_created_dag_resources_and_reverts_parents(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    assert engine.apply_patch().status == "noop"

    # Add a new tool, guardrail, and sub-agent, plus parent binding
    tool_dir = workspace / "tools" / "fraud_scanner" / "python_function"
    tool_dir.mkdir(parents=True)
    (workspace / "tools" / "fraud_scanner" / "fraud_scanner.json").write_text(
        json.dumps({"displayName": "fraud_scanner"}), encoding="utf-8"
    )
    (tool_dir / "python_code.py").write_text(
        "def fraud_scanner(tx_id: str) -> dict:\n    return {'risk': 'low'}\n",
        encoding="utf-8",
    )

    gr_dir = workspace / "guardrails" / "fraud_guard"
    gr_dir.mkdir(parents=True)
    (gr_dir / "fraud_guard.json").write_text(
        json.dumps(
            {
                "displayName": "Fraud Guard",
                "enabled": True,
                "contentFilter": {
                    "bannedContents": ["bypass_fraud"],
                    "matchType": "SIMPLE_STRING_MATCH",
                },
            }
        ),
        encoding="utf-8",
    )

    sub_dir = workspace / "agents" / "z_fraud_specialist"
    sub_dir.mkdir(parents=True)
    (sub_dir / "z_fraud_specialist.json").write_text(
        json.dumps(
            {
                "displayName": "Fraud Specialist",
                "tools": ["fraud_scanner"],
                "guardrails": ["fraud_guard"],
            }
        ),
        encoding="utf-8",
    )
    (sub_dir / "instruction.txt").write_text(
        "Investigate suspicious transactions with {@TOOL: fraud_scanner}.",
        encoding="utf-8",
    )

    triage_dir = workspace / "agents" / "triage_agent"
    (triage_dir / "triage_agent.json").write_text(
        json.dumps(
            {
                "displayName": "Triage Agent",
                "childAgents": ["z_fraud_specialist"],
            }
        ),
        encoding="utf-8",
    )

    def _fail_post_validation(_report: object) -> None:
        raise RuntimeError("Simulated post-patch smoke check failure")

    with pytest.raises(HotPatchApplyError) as exc_info:
        engine.apply_patch(post_patch_validator=_fail_post_validation)

    failed_report = exc_info.value.report
    assert failed_report.status == "rolled_back"
    assert len(failed_report.applied_patches) == 4
    assert len(failed_report.rollback_actions) == 4
    assert all(rb.restored for rb in failed_report.rollback_actions)

    # Verify reverse-topological rollback order:
    # 1. REVERT parent triage_agent (unlinks child_agents reference first!)
    # 2. DELETE child z_fraud_specialist (unlinks tool & guardrail references!)
    # 3. DELETE guardrail fraud_guard
    # 4. DELETE tool fraud_scanner
    rb_ops = [
        (rb.operation, rb.resource_type, rb.display_name)
        for rb in failed_report.rollback_actions
    ]
    assert rb_ops == [
        ("REVERT", "agent", "Triage Agent"),
        ("DELETE", "agent", "Fraud Specialist"),
        ("DELETE", "guardrail", "Fraud Guard"),
        ("DELETE", "tool", "fraud_scanner"),
    ]

    assert f"{APP_NAME}/agents/z_fraud_specialist" not in backend.agents
    assert f"{APP_NAME}/guardrails/fraud_guard" not in backend.guardrails
    assert f"{APP_NAME}/tools/fraud_scanner" not in backend.tools
    assert list(backend.agents[TRIAGE_AGENT_RES].child_agents) == []


def test_scoped_linter_blocks_unknown_guardrail_or_child_agent_in_config(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    (workspace / "guardrails").mkdir(exist_ok=True)
    backend = FakeCesBackend()
    engine = make_engine(workspace, backend)
    engine.apply_patch()

    triage_dir = workspace / "agents" / "triage_agent"
    (triage_dir / "triage_agent.json").write_text(
        json.dumps(
            {
                "displayName": "Triage Agent",
                "childAgents": ["nonexistent_subagent"],
                "guardrails": ["nonexistent_guardrail"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScopedLintError) as exc_info:
        engine.apply_patch()

    rule_ids = {f.rule_id for f in exc_info.value.findings}
    assert "HP-A003" in rule_ids
    assert "HP-A004" in rule_ids
    assert len(backend.update_agent_calls) == 0
