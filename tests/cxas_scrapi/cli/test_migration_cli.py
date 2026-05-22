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

"""Tests for :class:`MigrationCLI`.

Most of MigrationCLI is interactive (rich.Prompt loops), but the new
:meth:`MigrationCLI._run_post_migration_opt_ins` helper is pure async
plumbing — the right place to verify that the three opt-in flags
(consolidate / run_stage3 / persist_bundle) wire through to
:meth:`MigrationService.run_stage1` / :meth:`run_stage3` /
:meth:`persist_bundle` with the expected arguments.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cxas_scrapi.cli import migration_cli
from cxas_scrapi.cli.migration_cli import MigrationCLI
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    IRMetadata,
    MigrationConfig,
    MigrationIR,
)


def _make_config(**overrides) -> MigrationConfig:
    base = {
        "project_id": "test-project",
        "target_name": "test_target",
        "model": "gemini-2.5-flash-001",
        "optimize_for_cxas": True,
    }
    base.update(overrides)
    return MigrationConfig(**base)


def _make_source() -> DFCXAgentIR:
    return DFCXAgentIR(
        name="projects/p/locations/us/agents/src",
        display_name="Test Source",
        default_language_code="en",
    )


def _make_service_mock():
    service = MagicMock()
    service.location = "us"
    service.ir = MigrationIR(
        metadata=IRMetadata(
            app_name="test-app",
            app_id="11111111-1111-1111-1111-111111111111",
            app_resource_name="projects/p/locations/us/apps/X",
        ),
    )
    service.run_stage1 = AsyncMock(return_value=None)
    service.run_stage3 = AsyncMock(return_value=(1, 0, 0))
    service.persist_bundle = MagicMock(return_value="bundle.json")
    return service


@pytest.mark.asyncio
async def test_post_migration_opt_ins_all_off_skips_everything():
    """With all three flags off, no service methods are invoked."""
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(
        consolidate=False, run_stage3=False, persist_bundle=False
    )

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage1.assert_not_called()
    service.run_stage3.assert_not_called()
    service.persist_bundle.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_persist_only_calls_persist_bundle():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(persist_bundle=True)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.persist_bundle.assert_called_once()
    call = service.persist_bundle.call_args
    assert call.args[1] == "test_target_ir.json"
    assert call.kwargs["phase"] == "migrate"
    assert call.kwargs["status"] == "ok"
    service.run_stage1.assert_not_called()
    service.run_stage3.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_consolidate_calls_run_stage1():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(consolidate=True, persist_bundle=False)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage1.assert_awaited_once()
    kwargs = service.run_stage1.call_args.kwargs
    assert kwargs["consolidate"] is True
    assert kwargs["grouping_callback"] is None  # auto-accept (MigrationCLI)
    assert kwargs["version_label"] == "0.0.4"
    # persist_bundle is off → no persist path passed
    assert kwargs["persist_bundle_path"] is None
    service.run_stage3.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_stage3_calls_run_stage3():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(
        consolidate=True, run_stage3=True, persist_bundle=False
    )

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage1.assert_awaited_once()
    service.run_stage3.assert_awaited_once()
    stage3_kwargs = service.run_stage3.call_args.kwargs
    assert stage3_kwargs["mode"] == "hub"
    assert stage3_kwargs["persist_bundle_path"] is None


@pytest.mark.asyncio
async def test_post_migration_opt_ins_full_stack_passes_persist_paths():
    """With all three flags on, run_stage1 + run_stage3 each get the
    bundle path so they persist after their respective stages."""
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(
        consolidate=True, run_stage3=True, persist_bundle=True
    )

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    expected_path = "test_target_ir.json"
    # Initial migrate-phase persist
    service.persist_bundle.assert_called_once()
    assert service.persist_bundle.call_args.kwargs["phase"] == "migrate"

    # Stage 1 + Stage 3 both received the bundle path
    assert (
        service.run_stage1.call_args.kwargs["persist_bundle_path"]
        == expected_path
    )
    assert (
        service.run_stage3.call_args.kwargs["persist_bundle_path"]
        == expected_path
    )


@pytest.mark.asyncio
async def test_post_migration_opt_ins_consolidate_failure_no_block():
    """If consolidation raises, stage3 is still attempted (each opt-in
    step is independent — failures log but don't abort the chain)."""
    cli = MigrationCLI()
    service = _make_service_mock()
    service.run_stage1 = AsyncMock(side_effect=RuntimeError("Gemini timeout"))
    config = _make_config(consolidate=True, run_stage3=True)

    # Should NOT raise — failures are logged + surfaced via console, not raised.
    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage1.assert_awaited_once()
    service.run_stage3.assert_awaited_once()


# ===========================================================================
# `cxas migrate dfcx-cxas` subcommand handlers
# ===========================================================================


def _run_help(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_dfcx_cxas_help_lists_all_five_subcommands():
    """`cxas migrate dfcx-cxas --help` lists run/stage1/stage2/stage3/resume."""
    r = _run_help("migrate", "dfcx-cxas", "--help")
    assert r.returncode == 0, r.stderr
    for name in ("run", "stage1", "stage2", "stage3", "resume"):
        assert name in r.stdout, f"missing subcommand: {name}"


@pytest.mark.parametrize(
    "subcommand", ["run", "stage1", "stage2", "stage3", "resume"]
)
def test_each_subcommand_help_renders(subcommand: str):
    r = _run_help("migrate", "dfcx-cxas", subcommand, "--help")
    assert r.returncode == 0, r.stderr


def test_existing_dfcx_dashboard_help_still_renders():
    """The existing `cxas migrate dfcx` interactive dashboard isn't broken."""
    r = _run_help("migrate", "dfcx", "--help")
    assert r.returncode == 0, r.stderr
    assert "--default-agent-name" in r.stdout


# --- _resolve_bundle_path ------------------------------------------------


def test_resolve_bundle_path_honors_ir_bundle(tmp_path):
    bundle = tmp_path / "b.json"
    bundle.write_text("{}")
    args = argparse.Namespace(ir_bundle=str(bundle), target_name=None)
    assert migration_cli._resolve_bundle_path(args) == str(bundle)


def test_resolve_bundle_path_exits_when_missing(tmp_path):
    args = argparse.Namespace(
        ir_bundle=str(tmp_path / "nope.json"), target_name=None
    )
    with pytest.raises(SystemExit) as exc:
        migration_cli._resolve_bundle_path(args)
    assert exc.value.code == 1


def test_resolve_bundle_path_exits_when_no_args():
    args = argparse.Namespace(ir_bundle=None, target_name=None)
    with pytest.raises(SystemExit) as exc:
        migration_cli._resolve_bundle_path(args)
    assert exc.value.code == 1


# --- per-stage handlers --------------------------------------------------


def _make_stage_namespace(**kwargs) -> argparse.Namespace:
    base = dict(
        ir_bundle="/tmp/fake_bundle.json",
        target_name=None,
        project_id=None,
        location=None,
        yes=False,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_run_stage1_delegates_to_service_run_stage1():
    args = _make_stage_namespace(
        no_consolidate=False,
        grouping_json=None,
        on_integrity_fail="abort",
        version_label="0.0.1",
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage1 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/fake_bundle.json"),
    ):
        migration_cli.run_stage1(args)

    fake_service.run_stage1.assert_awaited_once()
    kwargs = fake_service.run_stage1.call_args.kwargs
    assert kwargs["consolidate"] is True
    assert kwargs["bundle"] is fake_bundle
    assert kwargs["on_integrity_fail"] == "abort"
    assert kwargs["version_label"] == "0.0.1"
    assert kwargs["persist_bundle_path"] == "/tmp/fake_bundle.json"


def test_run_stage1_no_consolidate_passes_bundle_none():
    args = _make_stage_namespace(
        no_consolidate=True,
        grouping_json=None,
        on_integrity_fail="abort",
        version_label="0.0.1",
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage1 = AsyncMock(return_value=None)

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, MagicMock(), "/tmp/b.json"),
    ):
        migration_cli.run_stage1(args)

    kwargs = fake_service.run_stage1.call_args.kwargs
    assert kwargs["consolidate"] is False
    assert kwargs["bundle"] is None


def test_run_stage2_delegates_with_default_paths():
    args = _make_stage_namespace(
        version_label="0.0.2",
        no_unit_tests=False,
        no_lint=False,
        no_report=False,
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage2 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()
    fake_bundle.config.target_name = "my_target"

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/fake_bundle.json"),
    ):
        migration_cli.run_stage2(args)

    kwargs = fake_service.run_stage2.call_args.kwargs
    assert kwargs["version_label"] == "0.0.2"
    assert kwargs["generate_unit_tests"] is True
    assert kwargs["unit_tests_path"] == "my_target_unit_tests.json"
    assert kwargs["run_lint"] is True
    assert kwargs["write_report_to"] == "my_target_optimization_report.md"
    assert kwargs["persist_bundle_path"] == "/tmp/fake_bundle.json"


def test_run_stage2_no_flags_disable_optional_outputs():
    args = _make_stage_namespace(
        version_label="0.0.2",
        no_unit_tests=True,
        no_lint=True,
        no_report=True,
        no_persist=True,
    )

    fake_service = MagicMock()
    fake_service.run_stage2 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()
    fake_bundle.config.target_name = "t"

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/b.json"),
    ):
        migration_cli.run_stage2(args)

    kwargs = fake_service.run_stage2.call_args.kwargs
    assert kwargs["generate_unit_tests"] is False
    assert kwargs["unit_tests_path"] is None
    assert kwargs["run_lint"] is False
    assert kwargs["write_report_to"] is None
    assert kwargs["persist_bundle_path"] is None


def test_run_stage3_delegates_with_mode_and_persist():
    args = _make_stage_namespace(
        mode="hub", no_set_root=False, dry_run=False, no_persist=False
    )

    fake_service = MagicMock()
    fake_service.run_stage3 = AsyncMock(return_value=(2, 0, 1))

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, MagicMock(), "/tmp/b.json"),
    ):
        migration_cli.run_stage3(args)

    kwargs = fake_service.run_stage3.call_args.kwargs
    assert kwargs["mode"] == "hub"
    assert kwargs["set_root"] is True
    assert kwargs["dry_run"] is False
    assert kwargs["persist_bundle_path"] == "/tmp/b.json"


def test_run_stage3_dry_run_skips_persist():
    args = _make_stage_namespace(
        mode="hierarchy",
        no_set_root=True,
        dry_run=True,
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage3 = AsyncMock(return_value=(0, 0, 0))

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, MagicMock(), "/tmp/b.json"),
    ):
        migration_cli.run_stage3(args)

    kwargs = fake_service.run_stage3.call_args.kwargs
    assert kwargs["mode"] == "hierarchy"
    assert kwargs["set_root"] is False
    assert kwargs["dry_run"] is True
    assert kwargs["persist_bundle_path"] is None


# --- run (end-to-end) ----------------------------------------------------


def test_run_end_to_end_exits_when_no_source():
    args = argparse.Namespace(
        source_agent_id=None,
        source_zip=None,
        project_id="p",
        location="us",
        target_name="t",
        env="PROD",
        model="m",
        no_optimize=False,
        consolidate=False,
        stage3=False,
        persist_bundle=False,
        yes=False,
    )
    with pytest.raises(SystemExit) as exc:
        migration_cli.run_end_to_end(args)
    assert exc.value.code == 1


def test_run_end_to_end_builds_config_and_calls_service():
    args = argparse.Namespace(
        source_agent_id="projects/p/locations/us/agents/uuid",
        source_zip=None,
        project_id="p",
        location="us",
        target_name="my_target",
        env="PROD",
        model="gemini-2.5-flash-001",
        no_optimize=False,
        consolidate=True,
        stage3=True,
        persist_bundle=True,
        yes=True,
    )

    # MigrationConfig's source_agent_data_override is a typed Pydantic
    # field — use a real DFCXAgentIR instance, not MagicMock.
    fake_agent_data = _make_source()
    fake_cx_api = MagicMock()
    fake_cx_api.fetch_full_agent_details.return_value = fake_agent_data

    fake_service = MagicMock()
    fake_service.ir = MagicMock()
    fake_service.run_migration = AsyncMock(return_value=None)

    with (
        patch.object(
            migration_cli, "ConversationalAgentsAPI", return_value=fake_cx_api
        ),
        patch.object(
            migration_cli, "MigrationService", return_value=fake_service
        ),
        patch.object(migration_cli, "MigrationCLI") as mock_cli_cls,
    ):
        mock_dashboard = mock_cli_cls.return_value
        mock_dashboard._run_post_migration_opt_ins = AsyncMock(
            return_value=None
        )
        migration_cli.run_end_to_end(args)

    fake_cx_api.fetch_full_agent_details.assert_called_once_with(
        "projects/p/locations/us/agents/uuid", use_export=True
    )
    fake_service.run_migration.assert_awaited_once()
    config_arg = fake_service.run_migration.call_args.kwargs["config"]
    assert config_arg.target_name == "my_target"
    assert config_arg.optimize_for_cxas is True
    assert config_arg.consolidate is True
    assert config_arg.run_stage3 is True
    assert config_arg.persist_bundle is True
    mock_dashboard._run_post_migration_opt_ins.assert_awaited_once()
