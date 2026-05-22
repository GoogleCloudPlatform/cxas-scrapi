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

import json
import os
from unittest.mock import mock_open, patch

import pandas as pd

from cxas_scrapi.utils.reporting import (
    _escape,
    _fmt_duration,
    _format_trace_line,
    _load_sim_test_cases,
    _resolve_tool_name,
    _upload_to_gcs,
    generate_combined_html_report,
    generate_combined_report_from_dir,
    generate_html_report,
    run_all_evals,
)


@patch("cxas_scrapi.utils.reporting.GCSUtils")
def test_upload_to_gcs_success(mock_gcs_cls):
    mock_gcs = mock_gcs_cls.return_value
    mock_gcs.upload_string.return_value = (
        "https://storage.mtls.cloud.google.com/bucket/report.html"
    )

    res = _upload_to_gcs("gs://bucket/report.html", "<html></html>")
    assert res == "https://storage.mtls.cloud.google.com/bucket/report.html"


@patch("cxas_scrapi.utils.reporting.GCSUtils")
def test_upload_to_gcs_failure(mock_gcs_cls):
    mock_gcs = mock_gcs_cls.return_value
    mock_gcs.upload_string.side_effect = Exception("error")

    res = _upload_to_gcs("gs://bucket/report.html", "<html></html>")
    assert res is None


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_success(mock_file, mock_upload):
    mock_upload.return_value = "https://url"
    results = [{"name": "test", "passed": True, "run": 1}]

    generate_html_report(results, "gs://bucket/report.html", "text", "model")

    mock_upload.assert_called_once()
    mock_file.assert_not_called()


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_fallback_with_extension(
    mock_file, mock_upload
):
    mock_upload.return_value = None
    results = [{"name": "test", "passed": True, "run": 1}]

    generate_html_report(
        results, "gs://bucket/fail_report.html", "text", "model"
    )

    mock_upload.assert_called_once()
    mock_file.assert_called_once_with("fail_report.html", "w")


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_fallback_no_extension(mock_file, mock_upload):
    mock_upload.return_value = None
    results = [{"name": "test", "passed": True, "run": 1}]

    # Path with no extension
    generate_html_report(results, "gs://bucket/no_ext", "text", "model")

    mock_upload.assert_called_once()
    mock_file.assert_called_once_with("report_fallback.html", "w")


@patch("cxas_scrapi.utils.reporting.Tools")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_tools_failure(mock_file, mock_tools_cls):
    # Simulate Tools(app_name).get_tools_map() failing
    mock_tools_cls.return_value.get_tools_map.side_effect = Exception(
        "Tools failed"
    )

    results = [{"name": "test", "passed": True, "run": 1}]
    generate_html_report(
        results, "local.html", "text", "model", app_name="projects/p"
    )

    mock_file.assert_called_once_with("local.html", "w")


@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_local(mock_file):
    results = [
        {
            "name": "test_eval",
            "passed": False,
            "error": "Timeout",
            "run": 1,
            "session_id": "sess123",
            "turns": 5,
            "detailed_trace": ["User: hello", "Agent Text: hi"],
            "step_details": [
                {
                    "goal": "g",
                    "status": "Completed",
                    "success_criteria": "c",
                    "justification": "j",
                }
            ],
            "expectation_details": [
                {"expectation": "e", "status": "Met", "justification": "j2"}
            ],
        }
    ]

    generate_html_report(
        results=results,
        output_path="local.html",
        modality="audio",
        model="gemini-3.1-pro-preview",
        app_name="projects/p1/locations/l1/apps/a1",
        wall_clock_s=120.5,
    )

    mock_file.assert_called_once_with("local.html", "w")
    content = mock_file().write.call_args[0][0]
    assert "Simulation Eval Report" in content
    assert "0.0%" in content
    assert "audio" in content
    assert "gemini-3.1-pro-preview" in content
    assert "2.0m" in content
    assert "test_eval" in content
    assert "Timeout" in content
    assert "sess123" in content


def test_fmt_duration():
    assert _fmt_duration(None) == ""
    assert _fmt_duration(30) == "30.0s"
    assert _fmt_duration(90) == "1.5m"


def test_escape():
    assert _escape('<script>&"') == "&lt;script&gt;&amp;&quot;"


def test_resolve_tool_name():
    tools_map = {"projects/p/tools/t1": "MyTool"}
    assert _resolve_tool_name("projects/p/tools/t1", tools_map) == "MyTool"
    assert _resolve_tool_name("projects/p/tools/t2", tools_map) == "t2"
    assert _resolve_tool_name(None, tools_map) is None


def test_format_trace_line():
    tools_map = {"path/to/tool": "GreatTool"}
    line = "Tool Call: path/to/tool with args {}"
    assert "GreatTool" in _format_trace_line(line, tools_map)
    assert "Unrelated" in _format_trace_line("Unrelated", tools_map)


def test_generate_combined_html_report(tmp_path):
    output_path = os.path.join(tmp_path, "report.html")

    golden_results = [
        {
            "name": "test_golden",
            "passed": True,
            "turns": [
                {
                    "index": 1,
                    "semantic_score": 4,
                    "comparisons": [
                        {
                            "outcome": "PASS",
                            "type": "text",
                            "expected": "hello",
                            "actual": "hello",
                        }
                    ],
                }
            ],
            "expectations": [],
            "session_id": "sess_1",
            "session_parameters": {},
            "duration_s": 1.0,
        }
    ]

    sim_results = [
        {
            "name": "test_sim",
            "passed": True,
            "run": 1,
            "duration_s": 2.0,
            "goals": 1,
            "expectations": 0,
            "turns": 1,
            "session_id": "sess_2",
            "session_parameters": {},
            "step_details": [
                {
                    "goal": "test goal",
                    "success_criteria": "test criteria",
                    "status": "Completed",
                    "justification": "done",
                }
            ],
            "expectation_details": [],
            "detailed_trace": ["User: hi", "Agent Text: hello"],
        }
    ]

    tool_results = [
        {
            "name": "test_tool",
            "tool": "my_tool",
            "passed": True,
            "status": "PASSED",
            "latency_ms": 50,
            "errors": "",
        }
    ]

    callback_results = [
        {
            "name": "test_callback",
            "agent": "my_agent",
            "callback_type": "my_callback",
            "passed": True,
            "status": "PASSED",
            "error": "",
        }
    ]

    generate_combined_html_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=tool_results,
        callback_results=callback_results,
        output_path=output_path,
        app_name="projects/test-proj/locations/global/apps/test-app",
    )

    assert os.path.exists(output_path)
    with open(output_path, "r") as f:
        content = f.read()
        assert "Combined Eval Report" in content
        assert "test_golden" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
def test_generate_combined_html_report_gcs_success(mock_upload):
    mock_upload.return_value = "https://url"

    generate_combined_html_report(
        golden_results=[],
        sim_results=[],
        tool_results=[],
        callback_results=[],
        output_path="gs://bucket/report.html",
        app_name="projects/test-proj",
    )

    mock_upload.assert_called_once()


def test_generate_combined_report_from_dir(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    # Create dummy files
    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    tool_file = evals_dir / "tool_results.csv"
    df_tool = pd.DataFrame(
        [
            {
                "test_name": "test_tool",
                "tool": "my_tool",
                "status": "PASSED",
                "latency (ms)": 50,
                "errors": "",
            }
        ]
    )
    df_tool.to_csv(tool_file, index=False)

    callback_file = evals_dir / "callback_results.csv"
    df_callback = pd.DataFrame(
        [
            {
                "test_name": "test_callback",
                "agent_name": "my_agent",
                "callback_type": "my_callback",
                "status": "PASSED",
                "error_message": "",
            }
        ]
    )
    df_callback.to_csv(callback_file, index=False)

    output_path = evals_dir / "combined_report.html"

    generate_combined_report_from_dir(
        output_dir=str(evals_dir), output_path=str(output_path)
    )

    assert os.path.exists(output_path)
    with open(output_path, "r") as f:
        content = f.read()
        assert "Combined Eval Report" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


def test_generate_combined_report_from_dir_include_all(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    # Create dummy files
    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    tool_file = evals_dir / "tool_results.csv"
    df_tool = pd.DataFrame(
        [
            {
                "test_name": "test_tool",
                "tool": "my_tool",
                "status": "PASSED",
                "latency (ms)": 50,
                "errors": "",
            }
        ]
    )
    df_tool.to_csv(tool_file, index=False)

    output_path = evals_dir / "combined_report.html"

    generate_combined_report_from_dir(
        output_dir=str(evals_dir), output_path=str(output_path), include=["all"]
    )

    assert os.path.exists(output_path)
    with open(output_path, "r") as f:
        content = f.read()
        assert "test_sim" in content
        assert "test_tool" in content


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
def test_run_all_evals_filtering(
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/goldens/test1.yaml", "evals/goldens/test2.yaml"],
        ["evals/tool_tests/tool1.yaml"],
        ["evals/simulations/sim1.yaml"],
    ]

    # Mock load_golden_evals_from_yaml to return empty list
    mock_eval_utils.return_value.load_golden_evals_from_yaml.return_value = []

    run_all_evals(
        app_name="projects/p",
        filter_files=["test1.yaml"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    mock_eval_utils.return_value.load_golden_evals_from_yaml.assert_called_once_with(
        "evals/goldens/test1.yaml"
    )


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
def test_run_all_evals_substring_filtering(
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/goldens/error.yaml", "evals/goldens/other.yaml"],
        ["evals/tool_tests/tool1.yaml"],
        ["evals/simulations/sim1.yaml"],
    ]

    # Mock load_golden_evals_from_yaml to return empty list
    mock_eval_utils.return_value.load_golden_evals_from_yaml.return_value = []

    run_all_evals(
        app_name="projects/p",
        filter_files=["ERROR"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    mock_eval_utils.return_value.load_golden_evals_from_yaml.assert_called_once_with(
        "evals/goldens/error.yaml"
    )


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
@patch("cxas_scrapi.evals.runner.RunEvaluationOperationMetadata")
@patch("cxas_scrapi.utils.reporting.load_golden_results")
@patch("yaml.safe_load")
@patch("builtins.open", new_callable=mock_open)
def test_run_all_evals_tag_filtering(
    mock_open_file,
    mock_yaml_load,
    mock_load_golden,
    mock_proto,
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/goldens/test1.yaml"],
        ["evals/tool_tests/tool1.yaml"],
        ["evals/simulations/sim1.yaml"],
    ]

    # Mock load_golden_evals_from_yaml to return evaluations with tags
    mock_eval_utils.return_value.load_golden_evals_from_yaml.return_value = [
        {"name": "eval1", "tags": ["tag1"]},
        {"name": "eval2", "tags": ["tag2"]},
    ]

    # Mock yaml.safe_load to return simulations with tags
    mock_yaml_load.return_value = [
        {"name": "sim1", "tags": ["tag1"]},
        {"name": "sim2", "tags": ["tag2"]},
    ]

    mock_eval_client = mock_evaluations.return_value
    mock_eval_client.update_evaluation.return_value.name = "mock_name"

    run_all_evals(
        app_name="projects/p",
        filter_tags=["tag1"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    # Verify that only eval1 was updated/run
    mock_eval_client.update_evaluation.assert_called_once_with(
        evaluation={"name": "eval1", "tags": ["tag1"]}, app_name="projects/p"
    )


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
@patch("yaml.safe_load")
@patch("builtins.open", new_callable=mock_open)
def test_run_all_evals_include_filtering(
    mock_open_file,
    mock_yaml_load,
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/simulations/sim1.yaml"],
    ]
    mock_yaml_load.return_value = [{"name": "sim1"}]

    # Call with ONLY sims
    run_all_evals(
        app_name="projects/p",
        include=["sims"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    # Assert SimulationEvals was instantiated and run
    mock_sim_evals.assert_called_once_with(
        app_name="projects/p", rate_limiter=None
    )
    mock_sim_evals.return_value.run_simulations.assert_called_once()

    # Assert others were NOT called/instantiated
    mock_evaluations.assert_not_called()
    mock_tool_evals.assert_not_called()
    mock_callback_evals.assert_not_called()


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
def test_run_all_evals_include_tools(
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/tool_tests/tool1.yaml"],
    ]
    mock_tool_evals.return_value.load_tool_test_cases_from_file.return_value = [
        {"name": "case1"}
    ]

    # Call with ONLY tools
    run_all_evals(
        app_name="projects/p",
        include=["tools"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    # Assert ToolEvals was instantiated and run
    mock_tool_evals.assert_called_once_with(app_name="projects/p")
    mock_tool_evals.return_value.run_tool_tests.assert_called_once()

    # Assert others were NOT called/instantiated
    mock_evaluations.assert_not_called()
    mock_sim_evals.assert_not_called()
    mock_callback_evals.assert_not_called()


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
def test_run_all_evals_include_callbacks(
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True

    # Call with ONLY callbacks
    run_all_evals(
        app_name="projects/p",
        include=["callbacks"],
        goldens_dir="evals/goldens/",
        tool_test_file="evals/tool_tests/",
        simulation_dir="evals/simulations/",
    )

    # Assert CallbackEvals was instantiated and run
    mock_callback_evals.assert_called_once()
    mock_callback_evals.return_value.test_all_callbacks_in_app_dir.assert_called_once()

    # Assert others were NOT called/instantiated
    mock_evaluations.assert_not_called()
    mock_sim_evals.assert_not_called()
    mock_tool_evals.assert_not_called()


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data="evals:\n  - name: sim1\n    tags: [P0]",
)
def test_run_all_evals_dict_based_simulations(
    mock_file,
    mock_isdir,
    mock_exists,
    mock_glob,
    mock_eval_utils,
    mock_callback_evals,
    mock_sim_evals,
    mock_tool_evals,
    mock_evaluations,
):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/simulations/sims.yaml"],
    ]

    run_all_evals(
        app_name="projects/p",
        include=["sims"],
        simulation_dir="evals/simulations/",
    )

    # Verify SimulationEvals was instantiated and run
    mock_sim_evals.assert_called_once_with(
        app_name="projects/p", rate_limiter=None
    )
    mock_sim_evals.return_value.run_simulations.assert_called_once_with(
        [
            {
                "name": "sim1",
                "tags": ["P0"],
                "session_parameters": {},
                "expectations": [],
            }
        ],
        runs=1,
        parallel=1,
        modality="text",
    )


def test_load_sim_test_cases_merges_common_parameters():
    yaml_data = """
common_session_parameters:
  disable_disclaimer: true
  originationNumber: "1234567890"
evals:
  - name: sim1
    session_parameters:
      custom_param: "hello"
  - name: sim2
"""
    with patch("builtins.open", mock_open(read_data=yaml_data)):
        cases = _load_sim_test_cases("dummy.yaml")

        assert len(cases) == 2
        # Case 1 should have merged common params and its own params
        assert cases[0]["session_parameters"] == {
            "disable_disclaimer": True,
            "originationNumber": "1234567890",
            "custom_param": "hello",
        }
        # Case 2 should just have common params
        assert cases[1]["session_parameters"] == {
            "disable_disclaimer": True,
            "originationNumber": "1234567890",
        }


def test_load_sim_test_cases_merges_common_expectations():
    yaml_data = """
common_expectations:
  - "The agent welcomes the user"
  - "The agent behaves politely"
evals:
  - name: sim1
    expectations:
      - "The agent offers options"
  - name: sim2
"""
    with patch("builtins.open", mock_open(read_data=yaml_data)):
        cases = _load_sim_test_cases("dummy.yaml")

        assert len(cases) == 2
        # Case 1 should have merged expectations
        assert cases[0]["expectations"] == [
            "The agent welcomes the user",
            "The agent behaves politely",
            "The agent offers options",
        ]
        # Case 2 should just have common expectations
        assert cases[1]["expectations"] == [
            "The agent welcomes the user",
            "The agent behaves politely",
        ]
