# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.Agent.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    IRAgent,
    IRMetadata,
    MigrationConfig,
    MigrationIR,
)
from cxas_scrapi.migration.ir_bundle import IRBundle
from cxas_scrapi.migration.service import MigrationService

# ---------------------------------------------------------------------------
# Shared fixtures for stage-method tests
# ---------------------------------------------------------------------------


def _make_ir(with_app: bool = True) -> MigrationIR:
    """Build a minimal MigrationIR with one agent."""
    return MigrationIR(
        metadata=IRMetadata(
            app_name="test-app",
            app_id="11111111-1111-1111-1111-111111111111",
            app_resource_name=(
                "projects/p/locations/us/apps/X" if with_app else None
            ),
        ),
        agents={
            "RootAgent": IRAgent(
                type="PLAYBOOK",
                display_name="Root Agent",
                instruction="<root/>",
                resource_name=(
                    "projects/p/locations/us/apps/X/agents/A"
                    if with_app
                    else None
                ),
            )
        },
    )


def _make_source_data() -> DFCXAgentIR:
    return DFCXAgentIR(
        name="projects/p/locations/us/agents/src",
        display_name="Test Source",
        default_language_code="en",
        playbooks=[
            {
                "name": "projects/p/locations/us/agents/src/playbooks/p1",
                "displayName": "Root Agent",
                "playbookType": "ROUTINE",
            }
        ],
        flows=[],
    )


def _make_bundle(with_app: bool = True) -> IRBundle:
    return IRBundle(
        config=MigrationConfig(
            project_id="test-project",
            target_name="test_target",
            model="gemini-2.5-flash-001",
        ),
        source_agent_data=_make_source_data(),
        ir=_make_ir(with_app=with_app),
        app_url=(
            "https://ces.cloud.google.com/projects/p/locations/us/apps/X"
            if with_app
            else None
        ),
    )


def _make_service(ir: MigrationIR | None = None) -> MigrationService:
    """Build a MigrationService with heavy dependencies mocked out."""
    service = MigrationService(
        project_id="test-project",
        ps_apps_client=MagicMock(),
        ps_agents_client=MagicMock(),
        ps_tools_client=MagicMock(),
        ps_toolsets_client=MagicMock(),
        secret_manager_client=MagicMock(),
        cx_api_client=MagicMock(),
    )
    service.ir = ir if ir is not None else _make_ir()
    service.source_agent_data = _make_source_data()
    service._deploy_base_resources = AsyncMock()
    service._deploy_pending_agents = AsyncMock()
    service.topology_linker = MagicMock()
    return service


# ---------------------------------------------------------------------------
# run_migration end-to-end (pre-existing test, kept as-is)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_migration_success():
    # Mock external clients
    mock_ps_apps = MagicMock()
    mock_ps_agents = MagicMock()
    mock_ps_tools = MagicMock()
    mock_ps_toolsets = MagicMock()
    mock_secret_manager = MagicMock()
    mock_cx_api = MagicMock()

    service = MigrationService(
        project_id="test-project",
        ps_apps_client=mock_ps_apps,
        ps_agents_client=mock_ps_agents,
        ps_tools_client=mock_ps_tools,
        ps_toolsets_client=mock_ps_toolsets,
        secret_manager_client=mock_secret_manager,
        cx_api_client=mock_cx_api,
    )

    # Mock internal components
    service.exporter = MagicMock()
    service.exporter.fetch_full_agent_details.return_value = DFCXAgentIR(
        name="projects/p/locations/l/agents/a",
        display_name="Test Agent",
        default_language_code="en",
        playbooks=[],
        flows=[],
    )

    service.ai_augment = MagicMock()
    service.ai_augment.generate_agent_description = AsyncMock(
        return_value="Desc"
    )

    # Mock deploy methods
    service._deploy_base_resources = AsyncMock()
    service._deploy_pending_agents = AsyncMock()

    # Mock flow processing
    service._process_single_flow = AsyncMock()

    # Mock topology linker
    service.topology_linker = MagicMock()

    # Mock reporter to avoid creating report file during test
    service.reporter = MagicMock()

    with patch(
        "cxas_scrapi.migration.service.DFCXParameterExtractor.migrate_parameters"
    ) as mock_migrate:
        mock_migrate.return_value = ([], {})
        config = MigrationConfig(
            project_id="dummy-project",
            target_name="cxas-app",
            model="gemini-2.5-flash-001",
        )
        await service.run_migration(
            source_cx_agent_id="dfcx-123", config=config
        )

    # Verify sequence
    service.exporter.fetch_full_agent_details.assert_called_once_with(
        "dfcx-123", use_export=True
    )
    mock_migrate.assert_called_once()
    service._deploy_base_resources.assert_called_once()
    service._deploy_pending_agents.assert_called_once()
    service.topology_linker.link_and_finalize_topology.assert_called_once()


# ---------------------------------------------------------------------------
# persist_bundle
# ---------------------------------------------------------------------------


def test_persist_bundle_writes_file_and_appends_history():
    service = _make_service()
    bundle = _make_bundle()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "bundle.json")
        returned = service.persist_bundle(
            bundle, path, phase="stage1", status="ok", notes="dedup done"
        )

    assert returned == path
    assert bundle.ir is service.ir
    assert len(bundle.stage_history) == 1
    entry = bundle.stage_history[0]
    assert entry.phase == "stage1"
    assert entry.status == "ok"
    assert entry.notes == "dedup done"
    assert isinstance(entry.started_at, datetime)


def test_persist_bundle_without_phase_skips_history():
    service = _make_service()
    bundle = _make_bundle()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "bundle.json")
        service.persist_bundle(bundle, path)
    assert bundle.stage_history == []


# ---------------------------------------------------------------------------
# run_stage1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stage1_consolidate_requires_bundle():
    service = _make_service()
    with pytest.raises(ValueError, match="requires bundle"):
        await service.run_stage1(consolidate=True)


@pytest.mark.asyncio
async def test_run_stage1_dedup_only_no_version_no_consolidation():
    """Variable dedup runs; no Version created when version_label=None;
    no consolidation when consolidate=False (the default)."""
    service = _make_service()

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ) as mock_run,
        patch("cxas_scrapi.migration.service.Versions") as mock_versions_cls,
    ):
        result = await service.run_stage1(version_label=None)

    assert result is None
    mock_run.assert_awaited_once()
    mock_versions_cls.assert_not_called()


@pytest.mark.asyncio
async def test_run_stage1_creates_version_when_label_set():
    service = _make_service()
    fake_versions_client = MagicMock()

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.Versions",
            return_value=fake_versions_client,
        ),
    ):
        await service.run_stage1(version_label="0.0.2")

    fake_versions_client.create_version.assert_called_once()
    call_kwargs = fake_versions_client.create_version.call_args.kwargs
    assert call_kwargs["display_name"] == "0.0.2"
    assert "variable dedup" in call_kwargs["description"]
    assert "consolidation" not in call_kwargs["description"]


@pytest.mark.asyncio
async def test_run_stage1_consolidate_runs_consolidator_and_persists_grouping():
    """End-to-end consolidation path with mocked consolidator + grouping
    callback returning the proposed groupings unchanged."""
    service = _make_service()
    bundle = _make_bundle()
    fake_groupings = {
        "RootGroup": {
            "agents": ["Root Agent"],
            "journey": "main",
            "is_root": True,
        }
    }

    consolidated_ir = _make_ir()

    fake_consolidator = MagicMock()
    fake_consolidator.propose_groupings = AsyncMock(return_value=fake_groupings)
    fake_consolidator.consolidate = MagicMock(return_value=consolidated_ir)
    fake_consolidator.synthesize_instructions = AsyncMock(
        return_value={"RootGroup": "ok"}
    )

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.StructuralConsolidator",
            return_value=fake_consolidator,
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "detect_root_key",
            return_value="RootAgent",
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "validate_groupings"
        ) as mock_validate,
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "persist_grouping"
        ) as mock_persist_grouping,
        patch(
            "cxas_scrapi.migration.service.integrity_checks."
            "check_consolidation_integrity",
            return_value=([], []),
        ),
        patch(
            "cxas_scrapi.migration.service.topology_wirer.set_app_root_agent",
            return_value=(True, "set"),
        ),
        patch(
            "cxas_scrapi.migration.service.topology_wirer.delete_orphan_agents",
            return_value=(0, 0),
        ),
        patch("cxas_scrapi.migration.service.Versions"),
    ):
        returned = await service.run_stage1(
            consolidate=True,
            bundle=bundle,
            version_label=None,
        )

    # persist_grouping was patched, so no file was written to disk.
    mock_persist_grouping.assert_called_once()

    assert returned == fake_groupings
    fake_consolidator.propose_groupings.assert_awaited_once()
    mock_validate.assert_called_once()
    fake_consolidator.consolidate.assert_called_once_with(fake_groupings)
    fake_consolidator.synthesize_instructions.assert_awaited_once()
    # Bundle mutated.
    assert bundle.grouping == fake_groupings
    assert bundle.pre_consolidation_ir is not None
    # Service IR replaced with consolidated output.
    assert service.ir is consolidated_ir
    # Update-pass deploys were called.
    service._deploy_base_resources.assert_awaited_once_with(is_update_pass=True)
    service._deploy_pending_agents.assert_awaited_once_with(is_update_pass=True)


@pytest.mark.asyncio
async def test_run_stage1_consolidate_aborts_on_integrity_blocking():
    """When integrity_checks returns blocking errors and
    on_integrity_fail='abort', run_stage1 raises RuntimeError."""
    service = _make_service()
    bundle = _make_bundle()
    fake_groupings = {"RootGroup": {"agents": ["Root Agent"], "is_root": True}}

    fake_consolidator = MagicMock()
    fake_consolidator.propose_groupings = AsyncMock(return_value=fake_groupings)
    fake_consolidator.consolidate = MagicMock(return_value=_make_ir())
    fake_consolidator.synthesize_instructions = AsyncMock(return_value={})

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.StructuralConsolidator",
            return_value=fake_consolidator,
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "detect_root_key",
            return_value="RootAgent",
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "validate_groupings"
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "persist_grouping"
        ),
        patch(
            "cxas_scrapi.migration.service.integrity_checks."
            "check_consolidation_integrity",
            return_value=(["BLOCKING: unknown tool foo"], []),
        ),
    ):
        with pytest.raises(RuntimeError, match="blocking"):
            await service.run_stage1(
                consolidate=True,
                bundle=bundle,
                on_integrity_fail="abort",
                version_label=None,
            )

    # Deploy should NOT have run when we abort.
    service._deploy_base_resources.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_stage1_callback_returning_none_skips_consolidation():
    """When the grouping_callback returns None, the consolidation block
    is skipped but Stage 1 variable dedup still applies."""
    service = _make_service()
    bundle = _make_bundle()

    fake_consolidator = MagicMock()
    fake_consolidator.propose_groupings = AsyncMock(
        return_value={"G": {"agents": ["Root Agent"], "is_root": True}}
    )

    # Callback receives kwargs (ir, groupings, consolidator, root_key,
    # dep_summary) — accept **_ so the test doesn't have to mirror the
    # full contract.
    async def reject_callback(**_):
        return None

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.StructuralConsolidator",
            return_value=fake_consolidator,
        ),
        patch(
            "cxas_scrapi.migration.service.structural_consolidator."
            "detect_root_key",
            return_value="RootAgent",
        ),
    ):
        result = await service.run_stage1(
            consolidate=True,
            bundle=bundle,
            grouping_callback=reject_callback,
            version_label=None,
        )

    assert result is None
    fake_consolidator.consolidate.assert_not_called()
    service._deploy_base_resources.assert_not_awaited()
    assert bundle.grouping is None
    assert bundle.pre_consolidation_ir is None


# ---------------------------------------------------------------------------
# run_stage2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stage2_creates_version_when_label_set():
    service = _make_service()
    fake_versions_client = MagicMock()

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.Versions",
            return_value=fake_versions_client,
        ),
    ):
        await service.run_stage2(version_label="0.0.3")

    fake_versions_client.create_version.assert_called_once()
    assert (
        fake_versions_client.create_version.call_args.kwargs["display_name"]
        == "0.0.3"
    )


@pytest.mark.asyncio
async def test_run_stage2_generate_unit_tests_writes_json(tmp_path):
    service = _make_service()
    out_path = str(tmp_path / "unit_tests.json")

    fake_test_case = MagicMock()
    fake_test_case.model_dump = MagicMock(return_value={"name": "tc1"})
    fake_gen = MagicMock()
    fake_gen.generate_tests_for_agent = MagicMock(return_value=[fake_test_case])

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.service.DeterministicEvalGenerator",
            return_value=fake_gen,
        ),
        patch("cxas_scrapi.migration.service.Versions"),
    ):
        await service.run_stage2(
            version_label=None,
            generate_unit_tests=True,
            unit_tests_path=out_path,
        )

    assert os.path.exists(out_path)
    with open(out_path) as f:
        data = json.load(f)
    assert "RootAgent" in data
    assert data["RootAgent"][0]["name"] == "tc1"


@pytest.mark.asyncio
async def test_run_stage2_run_lint_invokes_post_deploy_lint():
    service = _make_service()
    fake_lint = AsyncMock(return_value=(True, "lint passed"))

    with (
        patch(
            "cxas_scrapi.migration.stage_runner.run_stage_with_redeploy",
            new=AsyncMock(return_value=MagicMock(optimization_logs=[])),
        ),
        patch(
            "cxas_scrapi.migration.post_deploy_lint.run_post_deploy_lint",
            new=fake_lint,
        ),
        patch("cxas_scrapi.migration.service.Versions"),
    ):
        await service.run_stage2(version_label=None, run_lint=True)

    fake_lint.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_stage3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stage3_requires_grouping_on_bundle():
    service = _make_service()
    bundle = _make_bundle()  # bundle.grouping is None
    with pytest.raises(RuntimeError, match="bundle.grouping"):
        await service.run_stage3(bundle=bundle)


@pytest.mark.asyncio
async def test_run_stage3_dry_run_skips_apply():
    service = _make_service()
    bundle = _make_bundle()
    bundle.grouping = {"RootGroup": {"agents": ["Root Agent"], "is_root": True}}

    with (
        patch(
            "cxas_scrapi.migration.service.topology_wirer."
            "compute_group_children",
            return_value={"RootGroup": set()},
        ),
        patch(
            "cxas_scrapi.migration.service.topology_wirer.apply_topology",
            return_value=(0, 0, 0),
        ) as mock_apply,
        patch(
            "cxas_scrapi.migration.service.topology_wirer.set_app_root_agent",
            return_value=(True, "ok"),
        ) as mock_set_root,
    ):
        result = await service.run_stage3(bundle=bundle, dry_run=True)

    assert result == (0, 0, 0)
    mock_apply.assert_called_once()
    assert mock_apply.call_args.kwargs.get("dry_run") is True
    # set_root and bundle persistence are skipped in dry-run mode.
    mock_set_root.assert_not_called()


@pytest.mark.asyncio
async def test_run_stage3_persists_bundle_on_success(tmp_path):
    service = _make_service()
    bundle = _make_bundle()
    bundle.grouping = {"RootGroup": {"agents": ["Root Agent"], "is_root": True}}
    bundle_path = str(tmp_path / "bundle.json")

    with (
        patch(
            "cxas_scrapi.migration.service.topology_wirer."
            "compute_group_children",
            return_value={"RootGroup": set()},
        ),
        patch(
            "cxas_scrapi.migration.service.topology_wirer.apply_topology",
            return_value=(1, 0, 0),
        ),
        patch(
            "cxas_scrapi.migration.service.topology_wirer.set_app_root_agent",
            return_value=(True, "ok"),
        ),
    ):
        updated, skipped, failed = await service.run_stage3(
            bundle=bundle, persist_bundle_path=bundle_path
        )

    assert (updated, skipped, failed) == (1, 0, 0)
    assert os.path.exists(bundle_path)
    assert bundle.stage_history[-1].phase == "stage3"
    assert bundle.stage_history[-1].status == "ok"


# ---------------------------------------------------------------------------
# run_migration back-compat — refactored optimize_for_cxas branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_migration_optimize_for_cxas_calls_new_stage_methods():
    """run_migration with optimize_for_cxas=True should delegate to
    run_stage1(version_label='0.0.2') + run_stage2(version_label='0.0.3'),
    preserving the existing 0.0.1 / 0.0.2 / 0.0.3 Version naming."""
    service = _make_service()

    # Replace the new stage methods so we can assert their invocations
    # without exercising the full optimizer pipeline.
    service.run_stage1 = AsyncMock(return_value=None)
    service.run_stage2 = AsyncMock(return_value=None)

    # Stub the source loader + reporter so run_migration can reach the
    # optimize_for_cxas branch without hitting external systems.
    service.exporter = MagicMock()
    service.exporter.fetch_full_agent_details.return_value = _make_source_data()
    service.ai_augment = MagicMock()
    service.ai_augment.generate_agent_description = AsyncMock(return_value="D")
    service._process_single_flow = AsyncMock()
    service.reporter = MagicMock()

    fake_versions_client = MagicMock()

    with (
        patch(
            "cxas_scrapi.migration.service.DFCXParameterExtractor."
            "migrate_parameters"
        ) as mock_migrate,
        patch(
            "cxas_scrapi.migration.service.Versions",
            return_value=fake_versions_client,
        ),
    ):
        mock_migrate.return_value = ([], {})
        config = MigrationConfig(
            project_id="test-project",
            target_name="cxas-app",
            model="gemini-2.5-flash-001",
            optimize_for_cxas=True,
        )
        await service.run_migration(source_cx_agent_id="dfcx-1", config=config)

    # Pre-opt Version 0.0.1 was created inline by run_migration.
    fake_versions_client.create_version.assert_called_once()
    assert (
        fake_versions_client.create_version.call_args.kwargs["display_name"]
        == "0.0.1"
    )

    # run_stage1 was called with version_label="0.0.2".
    service.run_stage1.assert_awaited_once_with(version_label="0.0.2")
    # run_stage2 was called with version_label="0.0.3".
    service.run_stage2.assert_awaited_once_with(version_label="0.0.3")
