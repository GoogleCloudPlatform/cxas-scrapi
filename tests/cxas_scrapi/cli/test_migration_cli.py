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
plumbing — the right place to verify that the profile configuration settings
(optimization, Spoke-Hub architecture style, and bundle persistence)
wire through correctly to :meth:`MigrationService.run_stage_1` /
:meth:`run_stage_3` / :meth:`persist_bundle` with the expected arguments.
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


@pytest.fixture(autouse=True, scope="module")
def mock_tee_logging():
    with (
        patch("cxas_scrapi.cli.migration_cli.start_tee_logging") as m_start,
        patch("cxas_scrapi.cli.migration_cli.close_tee_logging") as m_close,
    ):
        yield m_start, m_close


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
    service.run_stage_1 = AsyncMock(return_value=None)
    service.run_stage_2 = AsyncMock(return_value=None)
    service.run_stage_3 = AsyncMock(return_value=(1, 0, 0))
    service.persist_bundle = MagicMock(return_value="bundle.json")
    return service


@pytest.mark.asyncio
async def test_post_migration_opt_ins_all_off_skips_everything():
    """With all optimization off, no stage methods are invoked."""
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(optimize_for_cxas=False, persist_bundle=False)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage_1.assert_not_called()
    service.run_stage_3.assert_not_called()
    service.persist_bundle.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_persist_only_calls_persist_bundle():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(optimize_for_cxas=False, persist_bundle=True)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.persist_bundle.assert_called_once()
    call = service.persist_bundle.call_args
    assert call.args[1] == "test_target_ir.json"
    assert call.kwargs["phase"] == "migrate"
    assert call.kwargs["status"] == "ok"
    service.run_stage_1.assert_not_called()
    service.run_stage_3.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_optimized_path_calls_stage1_and_stage3():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(optimize_for_cxas=True, persist_bundle=False)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage_1.assert_awaited_once()
    kwargs = service.run_stage_1.call_args.kwargs
    assert (
        kwargs["grouping_callback"] is not None
    )  # interactive TUI review callback
    assert kwargs["version_label"] == "0.0.3"
    assert kwargs["dedup_version_label"] == "0.0.2"
    assert kwargs["persist_bundle_path"] is None

    service.run_stage_2.assert_awaited_once()
    stage2_kwargs = service.run_stage_2.call_args.kwargs
    assert stage2_kwargs["version_label"] == "0.0.4"
    assert stage2_kwargs["persist_bundle_path"] is None

    service.run_stage_3.assert_awaited_once()
    stage3_kwargs = service.run_stage_3.call_args.kwargs
    assert stage3_kwargs["mode"] == "hub"
    assert stage3_kwargs["version_label"] == "0.0.5"
    assert stage3_kwargs["persist_bundle_path"] is None


@pytest.mark.asyncio
async def test_post_migration_opt_ins_full_stack_passes_persist_paths():
    """With all optimization and persist on, run_stage_1 + run_stage_3 each
    get the bundle path so they persist after their respective stages.
    """
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(optimize_for_cxas=True, persist_bundle=True)

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    expected_path = "test_target_ir.json"
    # Initial migrate-phase persist
    service.persist_bundle.assert_called_once()
    assert service.persist_bundle.call_args.kwargs["phase"] == "migrate"

    # Stage 1 + Stage 2 + Stage 3 all received the bundle path
    assert (
        service.run_stage_1.call_args.kwargs["persist_bundle_path"]
        == expected_path
    )
    assert (
        service.run_stage_2.call_args.kwargs["persist_bundle_path"]
        == expected_path
    )
    assert (
        service.run_stage_3.call_args.kwargs["persist_bundle_path"]
        == expected_path
    )


@pytest.mark.asyncio
async def test_post_migration_opt_ins_consolidate_failure_aborts_loop():
    """If Stage 1 raises, subsequent stages (Stage 2 & Stage 3) are aborted
    cleanly to prevent operating on a failed/stale state.
    """
    cli = MigrationCLI()
    service = _make_service_mock()
    service.run_stage_1 = AsyncMock(side_effect=RuntimeError("Gemini timeout"))
    config = _make_config(optimize_for_cxas=True)

    # Should NOT raise — failures are logged + surfaced via console, not raised.
    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage_1.assert_awaited_once()
    service.run_stage_2.assert_not_called()
    service.run_stage_3.assert_not_called()


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


def test_dfcx_help_lists_run_and_optimize():
    """`cxas migrate dfcx --help` lists --run, --optimize,
    and --profile arguments."""
    r = _run_help("migrate", "dfcx", "--help")
    assert r.returncode == 0, r.stderr
    assert "--run" in r.stdout
    assert "--optimize" in r.stdout
    assert "--profile" in r.stdout
    assert "--default-agent-name" in r.stdout


@pytest.mark.parametrize("mode_arg", [["--run"], ["--optimize"]])
def test_each_mode_help_renders(mode_arg: list[str]):
    r = _run_help("migrate", "dfcx", *mode_arg, "--help")
    assert r.returncode == 0, r.stderr


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


def test_parse_agent_id_extracts_from_formats():
    cli = MigrationCLI()
    expected = (
        "projects/my-project-123/locations/global/agents/"
        "a4371f49-5982-4293-801b-551cf940ab65"
    )

    # 1. Raw exact path format
    assert cli._parse_agent_id(expected) == expected

    # 2. Browser console URL format
    url = "https://dialogflow.cloud.google.com/cx/projects/my-project-123/locations/global/agents/a4371f49-5982-4293-801b-551cf940ab65/playbooks"
    assert cli._parse_agent_id(url) == expected

    # 3. Path with extra spaces
    assert cli._parse_agent_id(f"  {expected}  ") == expected

    # 4. Fallback for standard UUID or single short string
    short = "a4371f49-5982-4293-801b-551cf940ab65"
    assert cli._parse_agent_id(short) == short


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


def test_run_stage_1_delegates_to_service_run_stage_1():
    args = _make_stage_namespace(
        grouping_json=None,
        version_label="0.0.3",
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage_1 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/fake_bundle.json"),
    ):
        migration_cli.run_stage_1(args)

    fake_service.run_stage_1.assert_awaited_once()
    kwargs = fake_service.run_stage_1.call_args.kwargs
    assert kwargs["bundle"] is fake_bundle
    assert kwargs["version_label"] == "0.0.3"
    assert kwargs["dedup_version_label"] == "0.0.2"
    assert kwargs["persist_bundle_path"] == "/tmp/fake_bundle.json"


def test_run_stage_2_delegates_with_default_paths():
    args = _make_stage_namespace(
        version_label="0.0.4",
        no_unit_tests=False,
        no_lint=False,
        no_report=False,
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage_2 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()
    fake_bundle.config.target_name = "my_target"

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/fake_bundle.json"),
    ):
        migration_cli.run_stage_2(args)

    kwargs = fake_service.run_stage_2.call_args.kwargs
    assert kwargs["version_label"] == "0.0.4"
    assert kwargs["generate_unit_tests"] is True
    assert kwargs["unit_tests_path"] == "my_target_unit_tests.json"
    assert kwargs["run_lint"] is True
    assert kwargs["write_report_to"] == "my_target_optimization_report.md"
    assert kwargs["persist_bundle_path"] == "/tmp/fake_bundle.json"


def test_run_stage_2_no_flags_disable_optional_outputs():
    args = _make_stage_namespace(
        version_label="0.0.4",
        no_unit_tests=True,
        no_lint=True,
        no_report=True,
        no_persist=True,
    )

    fake_service = MagicMock()
    fake_service.run_stage_2 = AsyncMock(return_value=None)
    fake_bundle = MagicMock()
    fake_bundle.config.target_name = "t"

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, fake_bundle, "/tmp/b.json"),
    ):
        migration_cli.run_stage_2(args)

    kwargs = fake_service.run_stage_2.call_args.kwargs
    assert kwargs["generate_unit_tests"] is False
    assert kwargs["unit_tests_path"] is None
    assert kwargs["run_lint"] is False
    assert kwargs["write_report_to"] is None
    assert kwargs["persist_bundle_path"] is None


def test_run_stage_3_delegates_with_architecture_and_persist():
    args = _make_stage_namespace(
        architecture="hub-and-spoke",
        version_label="0.0.5",
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage_3 = AsyncMock(return_value=(2, 0, 1))

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, MagicMock(), "/tmp/b.json"),
    ):
        migration_cli.run_stage_3(args)

    kwargs = fake_service.run_stage_3.call_args.kwargs
    assert kwargs["mode"] == "hub"
    assert kwargs["version_label"] == "0.0.5"
    assert kwargs["persist_bundle_path"] == "/tmp/b.json"


def test_run_stage_3_original_hierarchy_maps_correctly():
    args = _make_stage_namespace(
        architecture="original-hierarchy",
        version_label="0.0.5",
        no_persist=False,
    )

    fake_service = MagicMock()
    fake_service.run_stage_3 = AsyncMock(return_value=(2, 0, 1))

    with patch.object(
        migration_cli,
        "_restore_service_and_bundle",
        return_value=(fake_service, MagicMock(), "/tmp/b.json"),
    ):
        migration_cli.run_stage_3(args)

    kwargs = fake_service.run_stage_3.call_args.kwargs
    assert kwargs["mode"] == "hierarchy"
    assert kwargs["version_label"] == "0.0.5"
    assert kwargs["persist_bundle_path"] == "/tmp/b.json"


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
        profile="standard",
        architecture="hub-and-spoke",
        no_optimize=False,
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
        profile="standard",
        architecture="hub-and-spoke",
        no_optimize=False,
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
    ):
        migration_cli.run_end_to_end(args)

    fake_cx_api.fetch_full_agent_details.assert_called_once_with(
        "projects/p/locations/us/agents/uuid", use_export=True
    )
    fake_service.run_migration.assert_awaited_once()
    config_arg = fake_service.run_migration.call_args.kwargs["config"]
    assert config_arg.target_name == "my_target"
    assert config_arg.optimize_for_cxas is True
    assert config_arg.profile == "standard"
    assert config_arg.architecture == "hub-and-spoke"
    assert config_arg.interactive is False
    # Verify logical properties bridge
    assert config_arg.consolidate is True
    assert config_arg.run_stage_3 is True
    assert config_arg.persist_bundle is True
