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
    _resolve_tool_name,
    _upload_to_gcs,
    generate_combined_html_report,
    generate_combined_report_from_dir,
    generate_html_report,
)


@patch("cxas_scrapi.utils.reporting.GCSUtils")
def test_upload_to_gcs_success(mock_gcs_cls):
  mock_gcs = mock_gcs_cls.return_value
  mock_gcs.upload_string.return_value = "https://storage.mtls.cloud.google.com/bucket/report.html"

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
    mock_file, mock_upload):
  mock_upload.return_value = None
  results = [{"name": "test", "passed": True, "run": 1}]

  generate_html_report(results, "gs://bucket/fail_report.html", "text", "model")

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
      "Tools failed")

  results = [{"name": "test", "passed": True, "run": 1}]
  generate_html_report(results,
                       "local.html",
                       "text",
                       "model",
                       app_name="projects/p")

  mock_file.assert_called_once_with("local.html", "w")


@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_local(mock_file):
  results = [{
      "name":
          "test_eval",
      "passed":
          False,
      "error":
          "Timeout",
      "run":
          1,
      "session_id":
          "sess123",
      "turns":
          5,
      "detailed_trace": ["User: hello", "Agent Text: hi"],
      "step_details": [{
          "goal": "g",
          "status": "Completed",
          "success_criteria": "c",
          "justification": "j"
      }],
      "expectation_details": [{
          "expectation": "e",
          "status": "Met",
          "justification": "j2"
      }]
  }]

  generate_html_report(results=results,
                       output_path="local.html",
                       modality="audio",
                       model="gemini-3.1-pro-preview",
                       app_name="projects/p1/locations/l1/apps/a1",
                       wall_clock_s=120.5)

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
  assert _escape("<script>&\"") == "&lt;script&gt;&amp;&quot;"


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

    golden_results = [{
        "name": "test_golden",
        "passed": True,
        "turns": [{
            "index": 1,
            "semantic_score": 4,
            "comparisons": [{
                "outcome": "PASS",
                "type": "text",
                "expected": "hello",
                "actual": "hello"
            }]
        }],
        "expectations": [],
        "session_id": "sess_1",
        "session_parameters": {},
        "duration_s": 1.0
    }]

    sim_results = [{
        "name": "test_sim",
        "passed": True,
        "run": 1,
        "duration_s": 2.0,
        "goals": 1,
        "expectations": 0,
        "turns": 1,
        "session_id": "sess_2",
        "session_parameters": {},
        "step_details": [{
            "goal": "test goal",
            "success_criteria": "test criteria",
            "status": "Completed",
            "justification": "done"
        }],
        "expectation_details": [],
        "detailed_trace": ["User: hi", "Agent Text: hello"]
    }]

    tool_results = [{
        "name": "test_tool",
        "tool": "my_tool",
        "passed": True,
        "status": "PASSED",
        "latency_ms": 50,
        "errors": ""
    }]

    callback_results = [{
        "name": "test_callback",
        "agent": "my_agent",
        "callback_type": "my_callback",
        "passed": True,
        "status": "PASSED",
        "error": ""
    }]

    generate_combined_html_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=tool_results,
        callback_results=callback_results,
        output_path=output_path,
        app_name="projects/test-proj/locations/global/apps/test-app"
    )

    assert os.path.exists(output_path)
    with open(output_path, "r") as f:
        content = f.read()
        assert "Combined Eval Report" in content
        assert "test_golden" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


def test_generate_combined_report_from_dir(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    # Create dummy files
    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    tool_file = evals_dir / "tool_results.csv"
    df_tool = pd.DataFrame([{
        "test_name": "test_tool",
        "tool": "my_tool",
        "status": "PASSED",
        "latency (ms)": 50,
        "errors": ""
    }])
    df_tool.to_csv(tool_file, index=False)

    callback_file = evals_dir / "callback_results.csv"
    df_callback = pd.DataFrame([{
        "test_name": "test_callback",
        "agent_name": "my_agent",
        "callback_type": "my_callback",
        "status": "PASSED",
        "error_message": ""
    }])
    df_callback.to_csv(callback_file, index=False)

    output_path = evals_dir / "combined_report.html"

    generate_combined_report_from_dir(
        evals_dir=str(evals_dir),
        output_path=str(output_path)
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
    df_tool = pd.DataFrame([{
        "test_name": "test_tool",
        "tool": "my_tool",
        "status": "PASSED",
        "latency (ms)": 50,
        "errors": ""
    }])
    df_tool.to_csv(tool_file, index=False)

    output_path = evals_dir / "combined_report.html"

    generate_combined_report_from_dir(
        evals_dir=str(evals_dir),
        output_path=str(output_path),
        include=["all"]
    )

    assert os.path.exists(output_path)
    with open(output_path, "r") as f:
        content = f.read()
        assert "test_sim" in content
        assert "test_tool" in content
