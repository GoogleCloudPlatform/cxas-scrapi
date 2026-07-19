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

"""Unit tests for :mod:`cxas_scrapi.cli.main`."""

import argparse
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from cxas_scrapi.cli import main as evals_cli_module


def test_combined_evals_report_cmd_success(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Verifies successful execution of combined evaluation report generation.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
        tmp_path: Pytest temporary directory fixture.
    """
    mock_gen = mocker.patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    )
    mock_gen.return_value = "/tmp/report.html"

    args = argparse.Namespace(
        output_dir=str(tmp_path / "output"),
        output="/tmp/report.html",
        gcs_path=None,
        timestamped=False,
        include=None,
        filter_files=None,
        filter_tags=None,
        filter_names=None,
        input_dir=None,
        tool_test_file="evals/tool_tests/",
        goldens_dir="evals/goldens/",
        simulation_dir="evals/simulations/",
        golden_run="STABLE",
        app_name="projects/p/locations/l/apps/a",
        run=False,
        app_dir=str(tmp_path / "app"),
        modality="text",
        sim_user_model="gemini-2.5-flash-001",
        eval_model="gemini-2.5-flash-001",
        runs=1,
        sim_parallel=5,
        golden_timeout=600,
        json_progress=False,
        bg_noise_file=None,
    )
    evals_cli_module.combined_evals_report_cmd(args)

    mock_gen.assert_called_once()
    captured = capsys.readouterr()
    assert "Combined report generated at /tmp/report.html" in captured.out


def test_export_eval_basic(mocker: Any, capsys: Any) -> None:
    """Verifies export_eval dispatching.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id="eval_1",
        output="/tmp/eval.yaml",
        format="yaml",
    )
    evals_cli_module.export_eval(args)

    mock_client.export_evaluation.assert_called_once()
    captured = capsys.readouterr()
    assert "Exporting evaluation: eval_1" in captured.out


def test_push_eval_basic(mocker: Any, capsys: Any) -> None:
    """Verifies push_eval dispatching.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_eval_utils_cls = mocker.patch("cxas_scrapi.utils.eval_utils.EvalUtils")
    mock_eval_utils = mock_eval_utils_cls.return_value
    mock_eval_utils.load_golden_evals_from_yaml.return_value = [
        {"displayName": "Golden 1"}
    ]

    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        file="/tmp/eval.yaml",
    )
    evals_cli_module.push_eval(args)

    mock_client.update_evaluation.assert_called_once()
    captured = capsys.readouterr()
    assert "Pushing evaluation(s) from /tmp/eval.yaml" in captured.out


def test_run_eval_missing_args_triggers_exit(capsys: Any) -> None:
    """Verifies error handling in run_eval when no evaluation filters are supplied.

    Args:
        capsys: Pytest stdout capture fixture.
    """
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id=None,
        display_name_prefix=None,
        tags=None,
    )

    with pytest.raises(SystemExit) as excinfo:
        evals_cli_module.run_eval(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "You must provide either --evaluation-id" in captured.out


def test_run_eval_by_id_success(mocker: Any, capsys: Any) -> None:
    """Verifies run_eval execution when --evaluation-id is provided.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value
    mock_op = MagicMock()
    mock_op.result.return_value = MagicMock()
    mock_client.run_evaluation.return_value = mock_op

    mock_utils_cls = mocker.patch("cxas_scrapi.utils.eval_utils.EvalUtils")
    mock_utils = mock_utils_cls.return_value
    mock_utils.evals_to_dataframe.return_value = {"summary": pd.DataFrame()}

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id="eval_123",
        display_name_prefix=None,
        tags=None,
        modality="text",
        golden_run_method="STABLE",
        wait=False,
    )
    evals_cli_module.run_eval(args)

    assert mock_client.run_evaluation.call_count == 1
    call_kwargs = mock_client.run_evaluation.call_args.kwargs
    assert call_kwargs["evaluations"] == ["eval_123"]
    assert call_kwargs["app_name"] == "projects/p/locations/l/apps/a"
    captured = capsys.readouterr()
    assert "Triggering evaluation for App:" in captured.out


def test_test_tools_command(mocker: Any, capsys: Any) -> None:
    """Verifies test_tools CLI handler.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_tool_evals_cls = mocker.patch("cxas_scrapi.evals.tool_evals.ToolEvals")
    mock_tool_evals = mock_tool_evals_cls.return_value
    mock_tool_evals.load_tool_test_cases_from_file.return_value = [{"case": 1}]
    mock_tool_evals.run_tool_tests.return_value = pd.DataFrame(
        [{"status": "PASSED"}]
    )

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        test_file="tools/test.json",
        debug=False,
    )
    with pytest.raises(SystemExit) as excinfo:
        evals_cli_module.test_tools(args)
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Running tool tests for App:" in captured.out


def test_test_callbacks_command(mocker: Any, capsys: Any) -> None:
    """Verifies test_callbacks CLI handler.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_cb_evals_cls = mocker.patch(
        "cxas_scrapi.evals.callback_evals.CallbackEvals"
    )
    mock_cb_evals = mock_cb_evals_cls.return_value
    mock_cb_evals.test_all_callbacks_in_app_dir.return_value = pd.DataFrame(
        [{"status": "PASSED"}]
    )

    args = argparse.Namespace(
        app_dir="/tmp/app",
        agent_name=None,
        callback_type=None,
        callback_name=None,
        log_file=None,
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as excinfo:
        evals_cli_module.test_callbacks(args)
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Running callback tests in App directory:" in captured.out


def test_wait_for_evaluation_completion_success(
    mocker: Any, capsys: Any
) -> None:
    """Verifies wait_for_evaluation_completion polls and returns new dataframe results.

    Args:
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_eval_utils = MagicMock()
    mock_df = pd.DataFrame(
        [
            {
                "eval_result_id": "res_1",
                "execution_state": "COMPLETED",
                "evaluation_status": "PASSED",
            }
        ]
    )
    mock_eval_utils.evals_to_dataframe.return_value = {"summary": mock_df}
    mock_eval_utils.eval_client.get_evaluation_result.return_value = {
        "result": "ok"
    }

    res = evals_cli_module.wait_for_evaluation_completion(
        eval_utils=mock_eval_utils,
        old_result_ids=set(),
        app_name="projects/p/locations/l/apps/a",
        expected_count=1,
        timeout_seconds=5,
    )
    assert res is not None
    captured = capsys.readouterr()
    assert "All 1 evaluations completed." in captured.out


def test_filter_metrics_and_assess(subtests: Any) -> None:
    """Verifies filter_metrics_and_assess for pass/fail/error states.

    Args:
        subtests: Pytest subtests fixture.
    """
    with subtests.test("Passed assessment"):
        df_summary = pd.DataFrame(
            [{"evaluation_status": "PASSED", "execution_state": "COMPLETED"}]
        )
        res = evals_cli_module.filter_metrics_and_assess(
            {"summary": df_summary}, filter_auto_metrics=False
        )
        assert res is True

    with subtests.test("Failed assessment"):
        df_summary = pd.DataFrame(
            [{"evaluation_status": "FAILED", "execution_state": "COMPLETED"}]
        )
        res = evals_cli_module.filter_metrics_and_assess(
            {"summary": df_summary}, filter_auto_metrics=False
        )
        assert res is False


def test_run_eval_prefix_and_tags(mocker: Any, capsys: Any) -> None:
    """Verifies run_eval filtering by display_name_prefix and tags with waiting."""
    mocker.patch("time.sleep")
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value

    mock_op = MagicMock()

    mock_op.result.return_value = MagicMock()
    mock_client.run_evaluation.return_value = mock_op

    e1 = MagicMock()
    e1.name = "eval_1"
    e1.display_name = "test_a"
    e1.tags = ["regression"]

    e2 = MagicMock()
    e2.name = "eval_2"
    e2.display_name = "test_b"
    e2.tags = ["smoke"]

    mock_client.list_evaluations.return_value = [e1, e2]

    mock_utils_cls = mocker.patch("cxas_scrapi.utils.eval_utils.EvalUtils")
    mock_utils = mock_utils_cls.return_value
    mock_utils.evals_to_dataframe.side_effect = [
        {"summary": pd.DataFrame([{"eval_result_id": "r0"}])},
        {
            "summary": pd.DataFrame(
                [
                    {"eval_result_id": "r0"},
                    {"eval_result_id": "r1", "execution_state": "COMPLETED"},
                ]
            )
        },
        {
            "summary": pd.DataFrame(
                [{"eval_result_id": "r1", "execution_state": "COMPLETED"}]
            )
        },
    ]

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id=None,
        display_name_prefix="test_",
        tags=["regression"],
        modality="text",
        golden_run_method="STABLE",
        wait=True,
        filter_auto_metrics=False,
    )
    mocker.patch.object(
        evals_cli_module,
        "wait_for_evaluation_completion",
        return_value={
            "summary": pd.DataFrame(
                [
                    {
                        "evaluation_status": "PASSED",
                        "execution_state": "COMPLETED",
                    }
                ]
            )
        },
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.run_eval(args)
    assert exc.value.code == 0
    assert mock_client.run_evaluation.call_count == 1
    call_kwargs = mock_client.run_evaluation.call_args.kwargs
    assert call_kwargs["evaluations"] == ["eval_1", "eval_2"]


def test_ci_test_success(mocker: Any, capsys: Any) -> None:
    """Verifies ci_test command handler execution."""
    mock_apps_cls = mocker.patch("cxas_scrapi.core.apps.Apps")
    mock_apps = mock_apps_cls.return_value
    mock_apps.get_app_by_display_name.return_value = None

    mocker.patch(
        "cxas_scrapi.cli.app.app_push",
        return_value="projects/p/locations/l/apps/ci-app",
    )
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_evals_cls.return_value.get_evaluations_map.return_value = {
        "goldens": {"g1": "eval_1"}
    }

    mock_run = mocker.patch("cxas_scrapi.cli.evals.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0)

    args = argparse.Namespace(
        project_id="p",
        location="l",
        app_dir=".",
        display_name="CI App",
        env_file=None,
    )
    evals_cli_module.ci_test(args)
    assert mock_run.call_count >= 1
    captured = capsys.readouterr()
    assert "CI Test Lifecycle Completed Successfully" in captured.out


def test_local_test_success(mocker: Any, capsys: Any) -> None:
    """Verifies local_test command handler execution."""
    mock_call = mocker.patch(
        "cxas_scrapi.cli.evals.subprocess.call", return_value=0
    )
    args = argparse.Namespace(
        project_id="p",
        location="l",
        app_dir=".",
        env_file=None,
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.local_test(args)
    assert exc.value.code == 0
    assert mock_call.call_count >= 1


def test_test_single_callback_success(mocker: Any, capsys: Any) -> None:
    """Verifies test_single_callback command handler execution."""
    mock_cb_evals_cls = mocker.patch(
        "cxas_scrapi.evals.callback_evals.CallbackEvals"
    )
    mock_cb_evals = mock_cb_evals_cls.return_value
    mock_cb_evals.test_single_callback_for_agent.return_value = pd.DataFrame(
        [{"status": "PASSED"}]
    )

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        agent_name="agent_a",
        callback_type="before_model",
        test_file_path="callbacks/test_cb.py",
        log_file=None,
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.test_single_callback(args)
    assert exc.value.code == 0


def test_export_eval_json_and_output(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Verifies export_eval writing JSON to file."""
    from pathlib import Path

    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value
    mock_client.export_evaluation.side_effect = lambda *a, **kw: (
        (kw.get("output_path") or (a[2] if len(a) > 2 else None))
        and Path(kw.get("output_path") or a[2]).write_text(
            '{"displayName": "Eval"}'
        )
    )

    out_file = tmp_path / "eval.json"
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id="eval_1",
        output=str(out_file),
        format="json",
    )
    evals_cli_module.export_eval(args)
    mock_client.export_evaluation.assert_called_once()
    assert out_file.exists()
    assert "displayName" in out_file.read_text()


def test_wait_for_evaluation_completion_timeout(
    mocker: Any, capsys: Any
) -> None:
    """Verifies wait_for_evaluation_completion raising exit on timeout."""
    mock_eval_utils = MagicMock()
    mock_eval_utils.evals_to_dataframe.return_value = {
        "summary": pd.DataFrame()
    }
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.wait_for_evaluation_completion(
            eval_utils=mock_eval_utils,
            old_result_ids=set(),
            app_name="projects/p/locations/l/apps/a",
            expected_count=1,
            timeout_seconds=0,
        )
    assert exc.value.code == 1
    assert (
        "Timeout waiting for evaluation to complete." in capsys.readouterr().out
    )


def test_evals_error_branches(mocker: Any, capsys: Any) -> None:
    """Test exception and error branches across evals commands."""
    # 1. export_eval exception
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_evals_cls.return_value.export_evaluation.side_effect = RuntimeError(
        "API down"
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.export_eval(
            argparse.Namespace(
                app_name="projects/p/locations/l/apps/a",
                evaluation_id="id",
                output=None,
                format="yaml",
            )
        )
    assert exc.value.code == 1

    # 2. push_eval no valid goldens
    mocker.patch(
        "cxas_scrapi.utils.eval_utils.EvalUtils.load_golden_evals_from_yaml",
        return_value=[],
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.push_eval(
            argparse.Namespace(
                app_name="projects/p/locations/l/apps/a", file="empty.yaml"
            )
        )
    assert exc.value.code == 1

    # 3. ci_test app_push failure
    mocker.patch(
        "cxas_scrapi.core.apps.Apps.get_app_by_display_name", return_value=None
    )
    mocker.patch("cxas_scrapi.cli.app.app_push", return_value=None)
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.ci_test(
            argparse.Namespace(
                project_id="p", location="l", display_name="CI", app_dir="."
            )
        )
    assert exc.value.code == 1

    # 4. local_test docker build failure
    mocker.patch("cxas_scrapi.cli.evals.subprocess.call", return_value=1)
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.local_test(
            argparse.Namespace(project_id="p", location="l", app_dir=".")
        )
    assert exc.value.code == 1


def test_combined_evals_report_cmd_multi_run_and_audio(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Verifies combined_evals_report_cmd with audio modality and multi-run."""
    mock_gen = mocker.patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    )
    mock_gen.return_value = "/tmp/report.html"

    args = argparse.Namespace(
        output_dir=str(tmp_path / "output"),
        output=None,
        gcs_path=None,
        timestamped=True,
        include="smoke",
        filter_files=None,
        filter_tags=None,
        filter_names=None,
        input_dir="some_input/",
        tool_test_file=None,
        goldens_dir=None,
        simulation_dir=None,
        golden_run="projects/p/locations/l/apps/a/evalRuns/123",
        app_name="projects/p/locations/l/apps/a",
        run=False,
        app_dir=".",
        modality="audio",
        sim_user_model="custom_sim",
        eval_model="custom_eval",
        runs=2,
        sim_parallel=5,
        golden_timeout=600,
        json_progress=False,
        bg_noise_file="noise.wav",
    )
    evals_cli_module.combined_evals_report_cmd(args)
    mock_gen.assert_called_once()


def test_filter_metrics_and_assess_auto_metrics(capsys: Any) -> None:
    """Test filter_metrics_and_assess with filter_auto_metrics=True and custom expectations."""
    # 1. Expectations not met
    df_fail = pd.DataFrame(
        [
            {
                "record_type": "summary_expectation",
                "expectation": "exp1",
                "met_count": 0,
                "not_met_count": 1,
            }
        ]
    )
    res = evals_cli_module.filter_metrics_and_assess(
        {"expectations": df_fail}, filter_auto_metrics=True
    )
    assert res is False
    assert "1 custom expectations not met" in capsys.readouterr().out

    # 2. Expectations met
    df_pass = pd.DataFrame(
        [
            {
                "record_type": "summary_expectation",
                "expectation": "exp1",
                "met_count": 1,
                "not_met_count": 0,
            }
        ]
    )
    res = evals_cli_module.filter_metrics_and_assess(
        {"expectations": df_pass}, filter_auto_metrics=True
    )
    assert res is True
    assert "All 1 custom expectations met" in capsys.readouterr().out


def test_run_eval_with_wait_and_failures(mocker: Any, capsys: Any) -> None:
    """Test run_eval with wait=True formatting failure details and exiting with code 1."""
    mocker.patch("time.sleep")
    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_client = mock_evals_cls.return_value
    mock_op = MagicMock()
    mock_op.result.return_value = MagicMock()
    mock_client.run_evaluation.return_value = mock_op
    mock_client.list_evaluations.return_value = [
        MagicMock(name="eval_1", display_name="test_a")
    ]

    mock_utils_cls = mocker.patch("cxas_scrapi.utils.eval_utils.EvalUtils")
    mock_utils = mock_utils_cls.return_value
    mock_utils.evals_to_dataframe.return_value = {
        "summary": pd.DataFrame([{"evaluation_status": "PASSED"}])
    }

    df_failures = pd.DataFrame(
        [
            {
                "display_name": "Eval A",
                "failure_type": "System Engine Error",
                "actual": "500 Internal Error",
            },
            {
                "display_name": "Eval B",
                "failure_type": "Mismatch",
                "expected": "Yes",
                "actual": "No",
                "score": 0.2,
                "turn_index": 1,
            },
        ]
    )
    mocker.patch.object(
        evals_cli_module,
        "wait_for_evaluation_completion",
        return_value={
            "summary": pd.DataFrame(
                [
                    {
                        "evaluation_status": "FAILED",
                        "execution_state": "COMPLETED",
                    }
                ]
            ),
            "failures": df_failures,
        },
    )

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        evaluation_id="eval_1",
        display_name_prefix=None,
        tags=None,
        modality="text",
        golden_run_method="STABLE",
        wait=True,
        filter_auto_metrics=False,
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.run_eval(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Eval A Errored" in out or "Eval A Failed" in out
    assert "500 Internal Error" in out
    assert "Expected: Yes" in out
    assert "FINAL RESULT: FAIL" in out


def test_tool_and_callback_tests_failures_and_log_files(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Test failure status returns and log file creation in test_tools, test_callbacks, test_single_callback."""
    from pathlib import Path

    # 1. test_tools with FAIL status and debug=True
    mock_tool_evals_cls = mocker.patch("cxas_scrapi.evals.tool_evals.ToolEvals")
    mock_tool_evals = mock_tool_evals_cls.return_value
    mock_tool_evals.load_tool_test_cases_from_file.return_value = [{"case": 1}]
    mock_tool_evals.run_tool_tests.return_value = pd.DataFrame(
        [
            {
                "status": "FAIL",
                "test_case_id": "tc1",
                "tool_name": "t1",
                "error": "err",
            }
        ]
    )

    args_tool = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        test_file="tools.json",
        debug=True,
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.test_tools(args_tool)
    assert exc.value.code == 1

    # 2. test_callbacks with specific agent/cb_type and log_file
    mock_cb_evals_cls = mocker.patch(
        "cxas_scrapi.evals.callback_evals.CallbackEvals"
    )
    mock_cb_evals = mock_cb_evals_cls.return_value
    mock_cb_evals.test_all_callbacks_in_app_dir.side_effect = lambda *a, **kw: (
        (
            Path(kw["log_file"]).write_text("cb log")
            if kw.get("log_file")
            else None
        )
        or pd.DataFrame([{"status": "FAILED"}])
    )

    log_file = tmp_path / "cb.log"
    args_cb = argparse.Namespace(
        app_dir="/tmp/app",
        agent_name="agent_a",
        callback_type="before_model",
        callback_name="cb1",
        log_file=str(log_file),
        pytest_args=["-v"],
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.test_callbacks(args_cb)
    assert exc.value.code == 1
    assert log_file.exists()

    # 3. test_single_callback with log_file
    mock_cb_evals.test_single_callback_for_agent.side_effect = lambda *a, **kw: (
        (
            Path(kw["log_file"]).write_text("single log")
            if kw.get("log_file")
            else None
        )
        or pd.DataFrame([{"status": "FAILED"}])
    )
    log_single = tmp_path / "single.log"
    args_single = argparse.Namespace(
        app_name="a",
        agent_name="ag",
        callback_type="before_model",
        test_file_path="cb.py",
        log_file=str(log_single),
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.test_single_callback(args_single)
    assert exc.value.code == 1
    assert log_single.exists()


def test_ci_test_and_local_test_full_paths_and_env(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Test ci_test when tool_tests.yaml exists and local_test when env_file/oauth_token are present."""
    # 1. ci_test with tool_tests.yaml
    mock_apps_cls = mocker.patch("cxas_scrapi.core.apps.Apps")
    mock_apps_cls.return_value.get_app_by_display_name.return_value = None
    mocker.patch(
        "cxas_scrapi.cli.app.app_push",
        return_value="projects/p/locations/l/apps/ci-app",
    )
    mock_run = mocker.patch(
        "cxas_scrapi.cli.evals.subprocess.run",
        return_value=MagicMock(returncode=0),
    )

    app_dir = tmp_path / "app"
    (app_dir / "tests").mkdir(parents=True, exist_ok=True)
    (app_dir / "tests" / "tool_tests.yaml").write_text("tests: []")

    args_ci = argparse.Namespace(
        project_id="p",
        location="l",
        app_dir=str(app_dir),
        display_name="CI",
        env_file=None,
    )
    evals_cli_module.ci_test(args_ci)
    assert mock_run.call_count >= 1


def test_wait_for_evaluation_completion_full_polling_loop(
    mocker: Any, capsys: Any
) -> None:
    """Test wait_for_evaluation_completion handling empty summary initially, then error states."""
    mock_eval_utils = MagicMock()
    df_empty = pd.DataFrame()
    df_err = pd.DataFrame(
        [{"eval_result_id": "run_x", "execution_state": "ERROR"}]
    )
    mock_eval_utils.evals_to_dataframe.side_effect = [
        {"summary": df_empty},
        {"summary": df_err},
    ]
    mock_eval_utils.eval_client.get_evaluation_result.return_value = {
        "result": "error"
    }

    mocker.patch("time.sleep")
    # Advance simulated time by 10 seconds per loop check to exit almost instantly
    mocker.patch("time.time", side_effect=[0, 0, 10, 20, 30, 100, 200, 700])
    with pytest.raises(SystemExit):
        evals_cli_module.wait_for_evaluation_completion(
            eval_utils=mock_eval_utils,
            old_result_ids=set(),
            app_name="projects/p/locations/l/apps/a",
            expected_count=1,
            timeout_seconds=5,
        )


def test_ci_test_with_running_tools_and_evals(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Test ci_test executing test-tools subprocess and run_eval commands."""
    mock_apps_cls = mocker.patch("cxas_scrapi.core.apps.Apps")
    mock_apps_cls.return_value.get_app_by_display_name.return_value = None
    mocker.patch(
        "cxas_scrapi.cli.app.app_push",
        return_value="projects/p/locations/l/apps/ci-app",
    )
    mock_run = mocker.patch(
        "cxas_scrapi.cli.evals.subprocess.run",
        return_value=MagicMock(returncode=0),
    )

    mock_evals_cls = mocker.patch("cxas_scrapi.core.evaluations.Evaluations")
    mock_evals_cls.return_value.get_evaluations_map.return_value = {
        "goldens": {"g1": "eval_1"},
        "scenarios": {"s1": "sim_1"},
    }

    app_dir = tmp_path / "app"
    (app_dir / "tests").mkdir(parents=True, exist_ok=True)
    (app_dir / "tests" / "tool_tests.yaml").write_text("tests: []")

    args = argparse.Namespace(
        project_id="p",
        location="l",
        app_dir=str(app_dir),
        display_name="CI",
        env_file="env.json",
    )
    evals_cli_module.ci_test(args)
    assert (
        mock_run.call_count >= 3
    )  # 1 for test-tools, 2 for run (golden + scenario)


def test_local_test_full_paths_and_env(
    mocker: Any, capsys: Any, tmp_path: Any
) -> None:
    """Test local_test when env_file and oauth_token are present."""
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch("cxas_scrapi.cli.evals.subprocess.call", return_value=0)
    mocker.patch.dict("os.environ", {"CXAS_OAUTH_TOKEN": "secret_token"})
    args_local = argparse.Namespace(
        project_id="p", location="l", app_dir=str(app_dir), env_file="env.json"
    )
    with pytest.raises(SystemExit) as exc:
        evals_cli_module.local_test(args_local)
    assert exc.value.code == 0
    assert "Using provided CXAS_OAUTH_TOKEN" in capsys.readouterr().out
