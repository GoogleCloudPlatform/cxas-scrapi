# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for ``cxas_scrapi.migration.analysis_reporter``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cxas_scrapi.migration.analysis_reporter import (
    MigrationAnalysisBuilder,
    MigrationAnalysisSnapshot,
)
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    DFCXFlowModel,
    DFCXPageModel,
    IRAgent,
    IRBundle,
    IRMetadata,
    IRTool,
    MigrationConfig,
    MigrationIR,
)

# --- Helpers --------------------------------------------------------------


def _make_service(*, with_grouping: bool = False) -> SimpleNamespace:
    """Build a stand-in service object with enough attrs for the reporter."""
    source = DFCXAgentIR(
        name="projects/p/locations/us/agents/abc",
        display_name="DemoAgent",
        default_language_code="en",
        flows=[
            DFCXFlowModel(
                flow_id="projects/p/locations/us/agents/abc/flows/f1",
                flow_data={
                    "displayName": "Greetings",
                    "description": "Initial greet flow",
                    "transitionRoutes": [{"intent": "i1"}],
                    "eventHandlers": [{}],
                    "nluSettings": {"modelType": "default"},
                },
                pages=[
                    DFCXPageModel(
                        page_id="p1", page_data={"displayName": "Welcome"}
                    ),
                    DFCXPageModel(
                        page_id="p2", page_data={"displayName": "Menu"}
                    ),
                ],
            ),
        ],
        intents=[{"name": "i1"}],
        webhooks=[{"name": "w1"}],
        playbooks=[{"displayName": "RootAgent"}],
    )

    ir = MigrationIR(
        metadata=IRMetadata(
            app_name="DemoApp",
            app_id="demo-uuid",
            app_resource_name="projects/p/locations/us/apps/demo-uuid",
            default_model="gemini-3-flash-preview",
        ),
        parameters={
            "mock_mode": {
                "name": "mock_mode",
                "description": "Mock toggle.",
                "schema": {"type": "BOOLEAN", "default": False},
            },
            "session_token": {
                "name": "session_token",
                "description": "Auth token.",
                "schema": {"type": "STRING"},
            },
        },
        tools={
            "billing_lookup": IRTool(
                id="billing_lookup",
                name="projects/p/locations/us/apps/demo-uuid/tools/billing_lookup",
                type="PYTHON",
                payload={
                    "displayName": "billing_lookup",
                    "pythonFunction": {
                        "code": (
                            "def billing_lookup(account_id: str) -> dict:\n"
                            "    '''Look up an account balance.'''\n"
                            "    mock_mode = get_variable('mock_mode')\n"
                            "    if mock_mode:\n"
                            "        return {'balance': 100}\n"
                            "    return real_call(account_id)\n"
                        )
                    },
                },
            ),
            "billing_api": IRTool(
                id="billing_api",
                name=(
                    "projects/p/locations/us/apps/demo-uuid/toolsets/"
                    "billing_api"
                ),
                type="TOOLSET",
                payload={
                    "displayName": "billing_api",
                    "open_api_toolset": {
                        "text_schema": (
                            "servers:\n  - url: $BILLING_URL\npaths: {}\n"
                        ),
                    },
                },
                operation_ids=["get_balance", "list_invoices"],
            ),
        },
        agents={
            "RootAgent": IRAgent(
                type="PLAYBOOK",
                display_name="RootAgent",
                description="Top-level router agent.",
                instruction=(
                    "<Agent>greets caller using {mock_mode} and routes "
                    "via {session_token}.</Agent>"
                ),
                tools=[
                    "projects/p/locations/us/apps/demo-uuid/tools/billing_lookup",
                ],
                toolsets=[
                    {
                        "toolset": (
                            "projects/p/locations/us/apps/demo-uuid/toolsets/"
                            "billing_api"
                        )
                    }
                ],
            ),
        },
        routing_edges=[],
    )

    config = MigrationConfig(
        project_id="p",
        target_name="demo",
        model="gemini-3-flash-preview",
    )

    bundle = IRBundle(config=config, source_agent_data=source, ir=ir)
    if with_grouping:
        bundle.grouping = {
            "RootAgent": {
                "agents": ["Greetings"],
                "rationale": "All greet logic merged into one agent.",
                "journey": "Caller is greeted and routed.",
                "is_root": True,
            }
        }

    service = SimpleNamespace(
        ir=ir, source_agent_data=source, _analysis_bundle=bundle
    )
    return service


# --- Builder smoke ---------------------------------------------------------


def test_snapshot_dataclass_defaults():
    snap = MigrationAnalysisSnapshot(app_name="X", target_name="x")
    d = snap.to_dict()
    assert d["app_name"] == "X"
    assert d["agents"] == {}
    assert d["evals"] is None


def test_builder_paths_default_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    b = MigrationAnalysisBuilder(target_name="demo", app_name="Demo")
    assert b.html_path == tmp_path / "demo_migration_analysis.html"
    assert b.json_path == tmp_path / "demo_migration_analysis.json"


def test_builder_paths_use_output_dir(tmp_path):
    b = MigrationAnalysisBuilder(
        target_name="demo", app_name="Demo", output_dir=tmp_path
    )
    assert b.html_path.parent == tmp_path
    assert b.json_path.parent == tmp_path


def test_record_phase_appends(tmp_path):
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.record_phase("a", "did a")
    b.record_phase("b", "did b", duration_s=1.5)
    b.flush()
    data = json.loads(b.json_path.read_text())
    assert [p["name"] for p in data["pipeline"]] == ["a", "b"]
    assert data["pipeline"][1]["duration_s"] == 1.5


# --- Snapshot derivation --------------------------------------------------


def test_derives_kpis_from_service(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    k = b.snapshot.kpis
    assert k["dfcx_flows"] == 1
    assert k["dfcx_pages_total"] == 2
    assert k["dfcx_intents"] == 1
    assert k["dfcx_webhooks"] == 1
    assert k["cxas_agents"] == 1
    assert k["cxas_tools"] == 1  # PYTHON
    assert k["cxas_toolsets"] == 1  # TOOLSET
    assert k["cxas_variables"] == 2


def test_derives_tools_and_mock_detection(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    tools = b.snapshot.tools
    toolsets = b.snapshot.toolsets
    assert "billing_lookup" in tools
    assert tools["billing_lookup"]["mocked_fn"] is True
    assert "billing_lookup" in tools["billing_lookup"]["signature"]
    assert "billing_api" in toolsets
    assert toolsets["billing_api"]["mocked_server"] is True
    assert toolsets["billing_api"]["operation_count"] == 2


def test_used_by_reverse_index(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    assert b.snapshot.tools["billing_lookup"]["callers"] == ["RootAgent"]
    assert b.snapshot.toolsets["billing_api"]["callers"] == ["RootAgent"]


def test_variables_referenced_by_instruction(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    by_name = {v["name"]: v for v in b.snapshot.variables}
    assert by_name["mock_mode"]["referenced_by"] == ["RootAgent"]
    assert by_name["session_token"]["referenced_by"] == ["RootAgent"]


def test_grouping_marks_absorbed_flows_and_root(tmp_path):
    svc = _make_service(with_grouping=True)
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    agent = b.snapshot.agents["RootAgent"]
    assert agent["is_root"] is True
    assert agent["absorbed_flows"] == ["Greetings"]
    assert "merged" in agent["rationale"]
    flow = b.snapshot.flows[0]
    assert flow["absorbed_by_group"] == "RootAgent"


# --- Flush / rendering -----------------------------------------------------


def test_flush_writes_html_and_json_atomically(tmp_path):
    svc = _make_service(with_grouping=True)
    b = MigrationAnalysisBuilder("demo", "Demo App", output_dir=tmp_path)
    b.record_phase("source_loaded", "loaded source")
    b.update_from_service(svc)
    b.flush()
    assert b.html_path.exists()
    assert b.json_path.exists()
    html = b.html_path.read_text()
    assert "Demo App" in html
    assert "report-data" in html
    # data round-trips out of the embedded blob
    data = json.loads(b.json_path.read_text())
    assert data["agents"]["RootAgent"]["is_root"] is True


def test_flush_is_idempotent(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    b.flush()
    first = b.html_path.read_text()
    b.flush()
    second = b.html_path.read_text()
    # generated_at field re-renders each flush, but everything else stable
    assert len(first) == len(second) or abs(len(first) - len(second)) < 100


def test_flush_never_raises_when_service_state_bad(tmp_path, caplog):
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    # service object missing every expected attr
    b.update_from_service(SimpleNamespace())
    b.flush()  # should not raise
    assert b.html_path.exists()


# --- Service hook coverage ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "source_loaded",
        "parameters_extracted",
        "tools_converted",
        "agents_compiled",
        "fast_deploy_complete",
        "flows_processed",
        "topology_linked",
        "stage_1_dedup",
        "stage_1_consolidation",
        "stage_2_optimization",
        "stage_2_eval_regen",
        "stage_2_lint",
        "stage_3_topology",
    ],
)
def test_service_declares_all_planned_checkpoints(name):
    """Sanity check that every documented checkpoint name actually fires
    in ``service.py``. Catches accidental rename/removal."""
    src = Path("src/cxas_scrapi/migration/service.py").read_text(
        encoding="utf-8"
    )
    assert f'"{name}"' in src, f"checkpoint {name!r} not wired in service.py"


# --- Grouping Review (Phase 3) --------------------------------------------


def test_snapshot_pending_grouping_defaults_none():
    snap = MigrationAnalysisSnapshot(app_name="X", target_name="x")
    assert snap.pending_grouping is None
    assert snap.to_dict()["pending_grouping"] is None


def test_snapshot_pending_grouping_serializes_through(tmp_path):
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.snapshot.pending_grouping = {
        "groupings": {
            "RootAgent": {
                "agents": ["Flow A", "Flow B"],
                "rationale": "main entrypoint",
                "journey": "greet + route",
                "is_root": True,
            },
        },
        "all_flow_names": ["Flow A", "Flow B"],
        "root_key": "RootAgent",
        "status": "awaiting_confirmation",
        "session_id": "abc-123",
    }
    b.flush()
    data = json.loads(b.json_path.read_text())
    pg = data["pending_grouping"]
    assert pg["status"] == "awaiting_confirmation"
    assert pg["groupings"]["RootAgent"]["is_root"] is True
    assert pg["groupings"]["RootAgent"]["agents"] == ["Flow A", "Flow B"]


def test_html_renders_grouping_tab_hidden_when_pending_is_none(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    b.flush()
    html = b.html_path.read_text(encoding="utf-8")
    # Tab markup is always present in template but starts hidden.
    assert 'id="tab-grouping"' in html
    assert 'style="display:none;"' in html


def test_html_renders_grouping_tab_with_pending_data(tmp_path):
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    b.snapshot.pending_grouping = {
        "groupings": {
            "RootAgent": {
                "agents": ["Flow A"],
                "is_root": True,
                "rationale": "core",
                "journey": "",
            },
            "Helpers": {
                "agents": ["Flow B"],
                "is_root": False,
                "rationale": "",
                "journey": "",
            },
        },
        "all_flow_names": ["Flow A", "Flow B"],
        "root_key": "RootAgent",
        "status": "awaiting_confirmation",
        "session_id": "s-1",
    }
    b.flush()
    html = b.html_path.read_text(encoding="utf-8")
    # The renderer runs client-side; we just check the data made it into
    # the embedded report-data blob and the template scaffolding shipped.
    assert '"pending_grouping"' in html
    assert "RootAgent" in html
    assert "Helpers" in html
    assert "renderGroupingReview" in html
    assert 'id="panel-grouping"' in html


def test_html_confirm_button_disabled_without_live_endpoint(tmp_path):
    """In file:// read-only mode, the Confirm/Abort buttons are disabled."""
    svc = _make_service()
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    b.update_from_service(svc)
    b.flush()
    html = b.html_path.read_text(encoding="utf-8")
    # Static template ships with buttons disabled by default; the live
    # server (Phase 4) injects __REVIEW_ENDPOINT__ to enable them.
    assert 'id="gr-confirm"' in html
    assert 'id="gr-abort"' in html
    # Either explicit `disabled` attribute or the no-endpoint title.
    assert "Live server not running" in html
