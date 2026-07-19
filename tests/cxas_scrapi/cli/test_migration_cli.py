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
from typing import Any
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


@pytest.fixture(autouse=True)
def mock_tee_logging(mocker: Any) -> None:
    mocker.patch("cxas_scrapi.cli.migration_cli.start_tee_logging", create=True)
    mocker.patch("cxas_scrapi.cli.migration_cli.close_tee_logging", create=True)


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
    # Post-Phase-5: consolidation is the default; explicit no_consolidate
    # is required to skip Stage 1/2/3.
    config = _make_config(
        optimize_for_cxas=False, no_consolidate=True, persist_bundle=False
    )

    await cli._run_post_migration_opt_ins(service, config, _make_source())

    service.run_stage_1.assert_not_called()
    service.run_stage_3.assert_not_called()
    service.persist_bundle.assert_not_called()


@pytest.mark.asyncio
async def test_post_migration_opt_ins_persist_only_calls_persist_bundle():
    cli = MigrationCLI()
    service = _make_service_mock()
    config = _make_config(
        optimize_for_cxas=False, no_consolidate=True, persist_bundle=True
    )

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
    config = _make_config(
        optimize_for_cxas=True, web_confirm_grouping=False, persist_bundle=False
    )

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
        patch("google.auth.default", return_value=(MagicMock(), "p")),
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


def test_parse_agent_id_formats(subtests: Any) -> None:
    """Verifies parsing of raw UUIDs, full resource paths, console URLs, and whitespace.

    Args:
        subtests: Pytest subtests fixture.
    """
    cli_inst = MigrationCLI()

    cases = [
        (
            "a4371f49-5982-4293-801b-551cf940ab65",
            "a4371f49-5982-4293-801b-551cf940ab65",
        ),
        (
            "projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65",
            "projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65",
        ),
        (
            "https://console.cloud.google.com/dialogflow/cx/projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65/playbooks",
            "projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65",
        ),
        (
            "  projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65  ",
            "projects/p/locations/l/agents/a4371f49-5982-4293-801b-551cf940ab65",
        ),
    ]

    for raw_input, expected in cases:
        with subtests.test(raw_input=raw_input):
            result = cli_inst._parse_agent_id(raw_input)
            assert result == expected


def test_sanitize_input_ansi_and_control(subtests: Any) -> None:
    """Verifies stripping of ANSI color sequences and control codes.

    Args:
        subtests: Pytest subtests fixture.
    """
    cases = [
        ("\x1b[31mRedText\x1b[0m", "RedText"),
        ("Hello\x07World", "HelloWorld"),
        ("", ""),
        ("   Normal Wording   ", "Normal Wording"),
    ]

    for raw_input, expected in cases:
        with subtests.test(raw_input=raw_input):
            result = migration_cli._sanitize_input(raw_input)
            assert result == expected


@patch("cxas_scrapi.cli.migration_cli.run_end_to_end")
def test_run_migration_dashboard_non_interactive_validation(
    mock_e2e: MagicMock, subtests: Any, capsys: Any, mocker: Any
) -> None:
    """Verifies missing flag assertions and success delegation for non-interactive --run."""
    mocker.patch.object(sys, "stdin", MagicMock(isatty=lambda: True))

    with subtests.test("Missing source agent ID and zip"):
        args = argparse.Namespace(
            run=True, source_agent_id=None, source_zip=None
        )
        with pytest.raises(SystemExit) as excinfo:
            migration_cli.run_migration_dashboard(args)
        assert excinfo.value.code == 1

    with subtests.test("Missing project ID"):
        args = argparse.Namespace(
            run=True,
            source_agent_id="projects/p/locations/l/agents/a",
            project_id=None,
        )
        with pytest.raises(SystemExit) as excinfo:
            migration_cli.run_migration_dashboard(args)
        assert excinfo.value.code == 1

    with subtests.test("Missing target name"):
        args = argparse.Namespace(
            run=True,
            source_agent_id="projects/p/locations/l/agents/a",
            project_id="my-project",
            target_name=None,
        )
        with pytest.raises(SystemExit) as excinfo:
            migration_cli.run_migration_dashboard(args)
        assert excinfo.value.code == 1

    with subtests.test(
        "Valid non-interactive --run delegates to run_end_to_end"
    ):
        args_valid = argparse.Namespace(
            run=True,
            source_agent_id="projects/p/locations/l/agents/a",
            project_id="my-project",
            target_name="my_target",
        )
        migration_cli.run_migration_dashboard(args_valid)
        mock_e2e.assert_called_once_with(args_valid)


def test_tee_stream_and_logging(tmp_path: Any, monkeypatch: Any) -> None:
    """Test Tee class object functionality."""
    import cxas_scrapi.cli.migration_cli as mcli

    log_p = tmp_path / "tee.log"
    orig_stdout = sys.stdout
    ts = mcli.Tee(str(log_p))
    ts.file.write("hello tee\n")
    ts.file.flush()
    ts.close()
    assert "hello tee" in log_p.read_text()
    assert sys.stdout == orig_stdout


@patch("cxas_scrapi.cli.migration_cli.Confirm.ask", return_value=True)
@patch("cxas_scrapi.cli.migration_cli.Prompt.ask")
@patch("subprocess.run")
def test_migration_cli_methods(
    mock_run: MagicMock,
    mock_ask: MagicMock,
    mock_confirm: MagicMock,
    tmp_path: Any,
) -> None:
    """Test MigrationCLI check_auth, compose_config, and dependency analysis."""
    from cxas_scrapi.cli.migration_cli import MigrationCLI
    from cxas_scrapi.migration.data_models import (
        DFCXAgentIR,
        IRMetadata,
        MigrationIR,
    )

    cli_obj = MigrationCLI()
    mock_run.return_value = MagicMock(returncode=0)
    assert cli_obj.check_auth() is True

    mock_ask.side_effect = [
        "test_proj",
        "Default Agent",
        "P",
        "1",
        "1",
        "hub-and-spoke",
        "Custom API Runner",
    ]
    cfg = cli_obj.compose_config("Default Agent")
    assert cfg.project_id == "test_proj"
    assert cfg.target_name == "Default Agent"

    ir = DFCXAgentIR(
        source_agent_id="ag_id",
        display_name="DFCX Agent",
        name="projects/p/locations/l/agents/ag_id",
        default_language_code="en",
    )
    cli_obj.run_dependency_analysis(ir, ir)
    mig_ir = MigrationIR(
        metadata=IRMetadata(app_name="Test App", app_id="app_id")
    )
    cli_obj.display_status(mig_ir)


@patch("cxas_scrapi.cli.migration_cli.run_stage_1")
@patch("cxas_scrapi.cli.migration_cli.run_stage_2")
@patch("cxas_scrapi.cli.migration_cli.run_stage_3")
@patch("cxas_scrapi.cli.migration_cli.run_resume")
def test_run_migration_stage_router(
    mock_res: MagicMock,
    mock_s3: MagicMock,
    mock_s2: MagicMock,
    mock_s1: MagicMock,
) -> None:
    """Test run_migration_dashboard routing across explicit stages 1, 2, 3, and resume."""
    from cxas_scrapi.cli.migration_cli import run_migration_dashboard

    run_migration_dashboard(
        argparse.Namespace(
            optimize=True, stage="1", version_label=None, run=False
        )
    )
    mock_s1.assert_called_once()

    run_migration_dashboard(
        argparse.Namespace(
            optimize=True, stage="2", version_label=None, run=False
        )
    )
    mock_s2.assert_called_once()

    run_migration_dashboard(
        argparse.Namespace(
            optimize=True, stage="3", version_label=None, run=False
        )
    )
    mock_s3.assert_called_once()

    run_migration_dashboard(
        argparse.Namespace(
            optimize=True,
            stage="resume",
            version_label=None,
            yes=True,
            run=False,
        )
    )
    mock_res.assert_called_once()


@patch("cxas_scrapi.cli.migration_cli.run_end_to_end")
def test_run_migration_dashboard_run_and_default(
    mock_e2e: MagicMock, mocker: Any
) -> None:
    """Test run_migration_dashboard run=True flow and default dashboard launch."""
    from cxas_scrapi.cli.migration_cli import run_migration_dashboard

    # 1. run=True validation errors
    with pytest.raises(SystemExit) as exc:
        run_migration_dashboard(
            argparse.Namespace(run=True, source_agent_id=None, source_zip=None)
        )
    assert exc.value.code == 1

    args_run = argparse.Namespace(
        run=True, source_agent_id="ag_id", project_id="p", target_name="Target"
    )
    mock_e2e.return_value = None
    run_migration_dashboard(args_run)
    mock_e2e.assert_called_once_with(args_run)

    # 2. Default dashboard mode
    mock_tui = mocker.patch("cxas_scrapi.cli.migration_cli.MigrationCLI.run")
    mocker.patch("cxas_scrapi.cli.migration_cli.ConversationalAgentsAPI")
    run_migration_dashboard(
        argparse.Namespace(run=False, optimize=False, default_agent_name="ag")
    )
    mock_tui.assert_called_once()


@patch("cxas_scrapi.cli.migration_cli.MigrationService")
def test_run_post_migration_opt_ins_method(mock_svc_cls: MagicMock) -> None:
    """Test MigrationCLI._run_post_migration_opt_ins async execution."""
    import asyncio

    from cxas_scrapi.cli.migration_cli import MigrationCLI
    from cxas_scrapi.migration.data_models import (
        DFCXAgentIR,
        IRMetadata,
        MigrationConfig,
        MigrationIR,
    )

    cli_obj = MigrationCLI()
    mock_svc = mock_svc_cls.return_value
    mock_svc.ir = MigrationIR(
        metadata=IRMetadata(app_name="Test App", app_id="app_id")
    )
    mock_svc.location = "us"
    mock_svc.run_stage_1.return_value = MagicMock(agents={"a": MagicMock()})
    mock_svc.run_stage_2.return_value = MagicMock(agents={"a": MagicMock()})
    mock_svc.run_stage_3.return_value = (1, 0, 0)

    cfg = MigrationConfig(
        project_id="p",
        target_name="Target",
        persist_bundle=True,
        optimize_for_cxas=True,
        gen_unit_tests=True,
        gen_report=True,
        model="gemini-3-flash",
    )
    ir = DFCXAgentIR(
        source_agent_id="src",
        display_name="src",
        name="projects/p/locations/l/agents/src",
        default_language_code="en",
    )
    asyncio.run(cli_obj._run_post_migration_opt_ins(mock_svc, cfg, ir))
    mock_svc.persist_bundle.assert_called_once()


@patch("cxas_scrapi.cli.migration_cli.asyncio.run")
@patch("cxas_scrapi.cli.migration_cli._restore_service_and_bundle")
def test_run_stage_execution_handlers(
    mock_restore: MagicMock, mock_run: MagicMock
) -> None:
    """Test full execution of run_stage_1, run_stage_2, and run_stage_3."""
    from cxas_scrapi.cli.migration_cli import (
        run_stage_1,
        run_stage_2,
        run_stage_3,
    )

    mock_svc = MagicMock()
    mock_bundle = MagicMock()
    mock_bundle.config.target_name = "TestAgent"
    mock_restore.return_value = (mock_svc, mock_bundle, "bundle.json")
    mock_run.return_value = (1, 0, 0)  # for stage 3 return tuple

    # Stage 1
    args_s1 = argparse.Namespace(
        grouping_json=None,
        version_label="v1",
        no_persist=False,
        no_web_confirm=True,
        auto_confirm_grouping=True,
    )
    run_stage_1(args_s1)
    assert mock_run.call_count >= 1

    # Stage 2
    args_s2 = argparse.Namespace(
        version_label="v2",
        no_persist=True,
        no_unit_tests=True,
        no_lint=True,
        no_report=True,
    )
    run_stage_2(args_s2)
    assert mock_run.call_count >= 2

    # Stage 3
    args_s3 = argparse.Namespace(
        version_label="v3", no_persist=False, architecture="original-hierarchy"
    )
    run_stage_3(args_s3)
    assert mock_run.call_count >= 3


@patch("cxas_scrapi.cli.migration_cli.run_stage_1")
@patch("cxas_scrapi.cli.migration_cli.Prompt.ask")
@patch("cxas_scrapi.cli.migration_cli.glob.glob", return_value=["test_ir.json"])
def test_run_resume_execution(
    mock_glob: MagicMock,
    mock_ask: MagicMock,
    mock_s1: MagicMock,
    monkeypatch: Any,
) -> None:
    """Test run_resume bundle picking and stage routing."""
    from cxas_scrapi.cli.migration_cli import run_resume

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    mock_ask.side_effect = ["1", "stage_1"]
    args = argparse.Namespace(
        target_name=None,
        ir_bundle=None,
        project_id="p",
        location="l",
        yes=False,
        no_input=False,
    )
    run_resume(args)
    mock_s1.assert_called_once()


@patch("google.auth.default", return_value=(MagicMock(), "p"))
@patch("cxas_scrapi.cli.migration_cli.asyncio.run")
@patch("cxas_scrapi.cli.migration_cli.ConversationalAgentsAPI")
def test_run_end_to_end_execution(
    mock_api_cls: MagicMock, mock_run: MagicMock, mock_auth: MagicMock
) -> None:
    """Test run_end_to_end profile parsing and async execution flow."""
    from cxas_scrapi.cli.migration_cli import run_end_to_end
    from cxas_scrapi.migration.data_models import DFCXAgentIR

    mock_api = mock_api_cls.return_value
    mock_api.fetch_full_agent_details.return_value = DFCXAgentIR(
        source_agent_id="ag_id",
        display_name="DFCX Agent",
        name="projects/p/locations/l/agents/ag_id",
        default_language_code="en",
    )

    # 1. Standard profile
    args_std = argparse.Namespace(
        source_agent_id="ag_id",
        source_zip=None,
        profile="standard",
        project_id="p",
        location="l",
        target_name="Target",
        env="PROD",
        model="gemini-3-flash",
        persist_bundle=True,
        no_optimize=False,
        no_consolidate=False,
        yes=True,
        no_web_confirm=True,
        auto_confirm_grouping=True,
    )
    run_end_to_end(args_std)
    assert mock_run.call_count >= 1
