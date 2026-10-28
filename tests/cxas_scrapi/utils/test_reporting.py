# ruff: noqa: PLR0917
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

import datetime
import importlib.util
import inspect
import json
import os
import re
import sys
import typing
import wave
from unittest.mock import ANY, mock_open, patch

import pandas as pd
import pytest

from cxas_scrapi.evals import runner as evals_runner
from cxas_scrapi.evals.simulation_evals import SimulationEvals
from cxas_scrapi.prompts import naturalness_prompts
from cxas_scrapi.utils.eval_utils import (
    COMBINED_REPORT_FILENAME,
    COMBINED_REPORT_JSON_FILENAME,
    add_timestamp_suffix,
)
from cxas_scrapi.utils.latency_parser import LatencyParser
from cxas_scrapi.utils.reporting import (
    _aggregate_suite_audio_analysis,
    _build_turn_comparisons,
    _escape,
    _extract_transcript_messages,
    _fmt_duration,
    _format_trace_line,
    _get_acoustic_metrics_tooltips,
    _join_chunk_text,
    _load_sim_test_cases,
    _resolve_tool_name,
    _upload_to_gcs,
    generate_combined_html_report,
    generate_combined_json_report,
    generate_combined_report_from_dir,
    generate_html_report,
    run_all_evals,
)
from cxas_scrapi.utils.tracing.audio_analysis import ANALYSIS_REGISTRY


@patch("cxas_scrapi.utils.reporting.gcs_utils.GCSUtils")
def test_upload_to_gcs_success(mock_gcs_cls: typing.Any) -> None:
    mock_gcs = mock_gcs_cls.return_value
    mock_gcs.upload_string.return_value = (
        "https://storage.mtls.cloud.google.com/bucket/report.html"
    )

    res = _upload_to_gcs("gs://bucket/report.html", "<html></html>")
    assert res == "https://storage.mtls.cloud.google.com/bucket/report.html"


@patch("cxas_scrapi.utils.reporting.gcs_utils.GCSUtils")
def test_upload_to_gcs_failure(mock_gcs_cls: typing.Any) -> None:
    mock_gcs = mock_gcs_cls.return_value
    mock_gcs.upload_string.side_effect = Exception("error")

    res = _upload_to_gcs("gs://bucket/report.html", "<html></html>")
    assert res is None


@patch("cxas_scrapi.utils.reporting._get_html_head")
@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_success(
    mock_file: typing.Any,
    mock_upload: typing.Any,
    mock_get_html_head: typing.Any,
) -> None:
    mock_get_html_head.return_value = "<html><head></head><body>"
    mock_upload.return_value = "https://url"
    results = [{"name": "test", "passed": True, "run": 1}]

    generate_html_report(results, "gs://bucket/report.html", "text", "model")

    mock_upload.assert_called_once()
    mock_file.assert_not_called()


@patch("cxas_scrapi.utils.reporting._get_html_head")
@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_fallback_with_extension(
    mock_file: typing.Any,
    mock_upload: typing.Any,
    mock_get_html_head: typing.Any,
) -> None:
    mock_get_html_head.return_value = "<html><head></head><body>"
    mock_upload.return_value = None
    results = [{"name": "test", "passed": True, "run": 1}]

    generate_html_report(
        results, "gs://bucket/fail_report.html", "text", "model"
    )

    mock_upload.assert_called_once()
    mock_file.assert_called_once_with("fail_report.html", "w")


@patch("cxas_scrapi.utils.reporting._get_html_head")
@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_gcs_fallback_no_extension(
    mock_file: typing.Any,
    mock_upload: typing.Any,
    mock_get_html_head: typing.Any,
) -> None:
    mock_get_html_head.return_value = "<html><head></head><body>"
    mock_upload.return_value = None
    results = [{"name": "test", "passed": True, "run": 1}]

    # Path with no extension
    generate_html_report(results, "gs://bucket/no_ext", "text", "model")

    mock_upload.assert_called_once()
    mock_file.assert_called_once_with("report_fallback.html", "w")


@patch("cxas_scrapi.utils.reporting._get_html_head")
@patch("cxas_scrapi.utils.reporting.tools.Tools")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_tools_failure(
    mock_file: typing.Any,
    mock_tools_cls: typing.Any,
    mock_get_html_head: typing.Any,
) -> None:
    mock_get_html_head.return_value = "<html><head></head><body>"
    # Simulate Tools(app_name).get_tools_map() failing
    mock_tools_cls.return_value.get_tools_map.side_effect = Exception(
        "Tools failed"
    )

    results = [{"name": "test", "passed": True, "run": 1}]
    generate_html_report(
        results, "local.html", "text", "model", app_name="projects/p"
    )

    mock_file.assert_called_once_with("local.html", "w")


@patch("cxas_scrapi.utils.reporting._get_html_head")
@patch("builtins.open", new_callable=mock_open)
def test_generate_html_report_local(
    mock_file: typing.Any, mock_get_html_head: typing.Any
) -> None:
    mock_get_html_head.return_value = "<html><head></head><body>"
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


def test_fmt_duration() -> None:
    assert _fmt_duration(None) == ""
    assert _fmt_duration(30) == "30.0s"
    assert _fmt_duration(90) == "1.5m"


def test_escape() -> None:
    assert _escape('<script>&"') == "&lt;script&gt;&amp;&quot;"


def test_join_chunk_text() -> None:
    # Multi-chunk turn (filler said before a tool call, then the answer
    # after it returns) must render every chunk, not just the first.
    assert _join_chunk_text(
        [
            {"text": "Thanks. Checking that now."},
            {"text": "You're verified. The amount due is $4,120.80."},
        ]
    ) == (
        "Thanks. Checking that now. "
        "You're verified. The amount due is $4,120.80."
    )
    # Single chunk is unchanged.
    assert _join_chunk_text([{"text": "hello"}]) == "hello"
    # Empty / missing input.
    assert _join_chunk_text([]) == ""
    assert _join_chunk_text(None) == ""
    # Blank and text-less chunks are skipped, not rendered as gaps.
    assert _join_chunk_text([{"text": "  "}, {}, {"text": "kept"}]) == "kept"
    assert _join_chunk_text([{"text": None}]) == ""


def test_build_turn_comparisons_renders_unmatched_observed_response() -> None:
    # Real shape of a turn where the agent emitted a filler before a tool
    # call and the substantive answer after it. The platform matches the
    # filler to the expected reply (entry 0) and records the substantive
    # answer as an observed response with NO expectation (entry 3). The
    # renderer must surface both, not drop the unmatched one.
    turn = {
        "expectation_outcome": [
            {
                "expectation": {
                    "agent_response": {
                        "chunks": [
                            {"text": "Thanks. Checking now. You're set."}
                        ]
                    }
                },
                "observed_agent_response": {
                    "chunks": [{"text": "Thanks. Checking now."}]
                },
                "outcome": 2,
            },
            {
                "expectation": {
                    "tool_call": {"display_name": "verify_caller", "args": {}}
                },
                "observed_tool_call": {"display_name": "verify_caller"},
                "outcome": 1,
            },
            {
                "expectation": {"tool_response": {}},
                "outcome": 1,
            },
            {
                "observed_agent_response": {
                    "chunks": [{"text": "You're verified. $4,120.80 is due."}]
                },
                "outcome": 0,
            },
        ]
    }

    comps = _build_turn_comparisons(turn)

    # tool_response entry is skipped; the other three render.
    assert len(comps) == 3
    assert comps[0]["type"] == "text"
    assert comps[0]["actual"] == "Thanks. Checking now."
    assert comps[1]["type"] == "tool_call"
    assert comps[1]["actual"] == "verify_caller"
    # The unmatched observed answer is surfaced, not dropped.
    assert comps[2]["type"] == "text"
    assert comps[2]["expected"] == "(none)"
    assert comps[2]["actual"] == "You're verified. $4,120.80 is due."

    # Also assert that _extract_transcript_messages surfaces the full
    # conversation from turns with comparisons
    messages = _extract_transcript_messages(
        {"turns": [{"user_input": "What is my balance?", "comparisons": comps}]}
    )
    assert messages == [
        ["caller", "What is my balance?"],
        ["agent", "Thanks. Checking now."],
        ["event", "Tool Call: verify_caller"],
        ["agent", "You're verified. $4,120.80 is due."],
    ]


def test_resolve_tool_name() -> None:
    tools_map = {"projects/p/tools/t1": "MyTool"}
    assert _resolve_tool_name("projects/p/tools/t1", tools_map) == "MyTool"
    assert _resolve_tool_name("projects/p/tools/t2", tools_map) == "t2"
    assert _resolve_tool_name(None, tools_map) is None


def test_format_trace_line() -> None:
    tools_map = {"path/to/tool": "GreatTool"}
    line = "Tool Call: path/to/tool with args {}"
    assert "GreatTool" in _format_trace_line(line, tools_map)
    assert "Unrelated" in _format_trace_line("Unrelated", tools_map)


def test_generate_combined_html_report(tmp_path: typing.Any) -> None:
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
    with open(output_path) as f:
        content = f.read()
        assert "Eval Report" in content
        assert "test_golden" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


def test_generate_combined_html_report_renders_background_noise(
    tmp_path: typing.Any,
) -> None:
    output_path = os.path.join(tmp_path, "report_noise.html")
    sim_results = [
        {
            "name": "sim_noise",
            "run": 1,
            "passed": True,
            "turns": 1,
        }
    ]
    generate_combined_html_report(
        golden_results=[],
        sim_results=sim_results,
        tool_results=[],
        callback_results=[],
        output_path=output_path,
        app_name="projects/test-proj/locations/global/apps/test-app",
        bg_noise_file="call_center",
        bg_noise_snr=12.0,
    )
    assert os.path.exists(output_path)
    with open(output_path) as f:
        content = f.read()
        assert "BG: call_center (MEDIUM &middot; 12.0 dB SNR)" in content
        assert (
            "Continuous preset/file <code>call_center</code> at <b>MEDIUM</b> (12.0 dB SNR)"
            in content
        )
        assert "Background Noise Injection:" in content
        assert "toggleBgNoiseSample" not in content


def test_generate_combined_html_report_renders_naturalness(
    tmp_path: typing.Any,
) -> None:
    output_path = str(tmp_path / "report.html")
    sim_results = [
        {
            "name": "graded_sim",
            "run": 1,
            "passed": True,
            "goals": "1/1",
            "expectations": "0/0",
            "turns": 2,
            "duration_s": 1.0,
            "transcript": "User: hi\nAgent: hey there",
            "naturalness": "4.1/5",
            "naturalness_label": "Human-like",
            "naturalness_details": {
                "overall_score": 4.1,
                "overall_label": "Human-like",
                "turn_count": 1,
                "summary": "Sounds like a person.",
                "factor_averages": {"emotion": 4.0, "pacing": 4.2},
                "label_counts": {
                    "Bot-like": 0,
                    "Transitional": 0,
                    "Human-like": 1,
                },
                "turns": [
                    {
                        "turn_index": 0,
                        "agent_utterance": "hey there",
                        "label": "Human-like",
                        "score": 4.1,
                        "justification": "Warm and unscripted.",
                        "factors": [
                            {
                                "quality": "emotion",
                                "score": 4,
                                "value": "warm",
                                "reason": "Matched the caller's mood.",
                            },
                            {
                                "quality": "perceivedLatency",
                                "score": 1,
                                "value": "5.2s",
                                "reason": "unacceptable: 5.2s is slow",
                            },
                        ],
                    }
                ],
                "conversation_factors": [],
                "latency_ms_by_turn": {"0": 5200.0},
                "failure_reasons": ["perceived latency reached 5.2s on turn 0"],
            },
        },
        {"name": "ungraded_sim", "run": 1, "passed": True, "turns": 1},
    ]

    generate_combined_html_report(
        sim_results=sim_results,
        output_path=output_path,
        app_name="projects/test-proj/locations/global/apps/test-app",
    )

    with open(output_path) as f:
        content = f.read()

    assert "Human-like" in content
    assert "4.1/5" in content
    assert "Warm and unscripted." in content
    # The measured latency dimension and the hard failure it caused.
    assert "perceivedLatency" in content
    assert "5.2s" in content
    assert "perceived latency reached 5.2s on turn 0" in content
    # The ungraded run must still render, without a naturalness block.
    assert "ungraded_sim" in content


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
def test_generate_combined_html_report_gcs_success(
    mock_upload: typing.Any,
) -> None:
    mock_upload.return_value = "https://url"

    resolved_path = generate_combined_html_report(
        golden_results=[],
        sim_results=[],
        tool_results=[],
        callback_results=[],
        output_path="gs://bucket/report.html",
        app_name="projects/test-proj",
    )

    assert resolved_path == "https://url"
    mock_upload.assert_called_once()


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_combined_html_report_gcs_fallback(
    mock_file: typing.Any, mock_upload: typing.Any
) -> None:
    mock_upload.return_value = None

    resolved_path = generate_combined_html_report(
        golden_results=[],
        sim_results=[],
        tool_results=[],
        callback_results=[],
        output_path="gs://bucket/report.html",
        app_name="projects/test-proj",
    )

    assert resolved_path == "report.html"
    mock_file.assert_any_call("report.html", "w")


def test_generate_combined_report_from_dir(tmp_path: typing.Any) -> None:
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

    output_path = evals_dir / COMBINED_REPORT_FILENAME

    generate_combined_report_from_dir(
        output_dir=str(evals_dir), output_path=str(output_path)
    )

    assert os.path.exists(output_path)
    with open(output_path) as f:
        content = f.read()
        assert "Eval Report" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


def test_generate_combined_report_from_dir_include_all(
    tmp_path: typing.Any,
) -> None:
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

    output_path = evals_dir / COMBINED_REPORT_FILENAME

    generate_combined_report_from_dir(
        output_dir=str(evals_dir), output_path=str(output_path), include=["all"]
    )

    assert os.path.exists(output_path)
    with open(output_path) as f:
        content = f.read()
        assert "test_sim" in content
        assert "test_tool" in content


def test_generate_combined_json_report_local(tmp_path: typing.Any) -> None:
    output_path = tmp_path / COMBINED_REPORT_JSON_FILENAME

    sim_results = [
        {
            "name": "test_sim",
            "passed": True,
            "detailed_trace": ["User: hi\nAgent Text: hello"],
            "_processed_trace": [("user", "hi")],
        },
        {"name": "test_sim", "passed": False},
    ]
    golden_results = [
        {
            "name": "test_golden",
            "passed": True,
            "turns": [{"semantic_score": 3.5}],
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
            "passed": False,
            "status": "FAILED",
            "error": "boom",
        }
    ]

    resolved_path = generate_combined_json_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=tool_results,
        callback_results=callback_results,
        output_path=str(output_path),
        app_name="projects/p/locations/l/apps/a",
        sim_wall_clock_s=12.5,
    )

    assert resolved_path == str(output_path)
    with open(output_path) as f:
        report = json.load(f)

    assert report["schema_version"] == 1
    assert report["app_name"] == "projects/p/locations/l/apps/a"
    assert report["sim_wall_clock_s"] == 12.5

    summary = report["summary"]
    assert summary["total"] == 5
    assert summary["passed"] == 3
    assert summary["pass_rate_pct"] == 60.0
    assert summary["simulation"] == {"total": 2, "passed": 1}
    assert summary["golden"] == {"total": 1, "passed": 1}
    assert summary["tool"] == {"total": 1, "passed": 1}
    assert summary["callback"] == {"total": 1, "passed": 0}

    results = report["results"]
    assert results["simulation"][0]["name"] == "test_sim"
    assert results["simulation"][0]["detailed_trace"] == [
        "User: hi\nAgent Text: hello"
    ]
    # Internal keys are stripped from the JSON output.
    assert "_processed_trace" not in results["simulation"][0]
    assert results["golden"][0]["turns"] == [{"semantic_score": 3.5}]
    assert results["tool"][0]["latency_ms"] == 50
    assert results["callback"][0]["error"] == "boom"


def test_generate_combined_json_report_serializes_non_json_values(
    tmp_path: typing.Any,
) -> None:
    output_path = tmp_path / "report.json"

    generate_combined_json_report(
        sim_results=[
            {
                "name": "test_sim",
                "passed": True,
                "started_at": datetime.datetime(2026, 7, 16, 12, 0, 0),
            }
        ],
        output_path=str(output_path),
    )

    with open(output_path) as f:
        report = json.load(f)
    assert "2026-07-16" in report["results"]["simulation"][0]["started_at"]


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_combined_json_report_gcs_success(
    mock_file: typing.Any, mock_upload: typing.Any
) -> None:
    mock_upload.return_value = "https://url"

    resolved_path = generate_combined_json_report(
        sim_results=[{"name": "test_sim", "passed": True}],
        output_path="gs://bucket/report.json",
    )

    assert resolved_path == "https://url"
    mock_upload.assert_called_once_with(
        "gs://bucket/report.json", ANY, content_type="application/json"
    )
    mock_file.assert_not_called()


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_combined_json_report_gcs_fallback(
    mock_file: typing.Any, mock_upload: typing.Any
) -> None:
    mock_upload.return_value = None

    resolved_path = generate_combined_json_report(
        sim_results=[{"name": "test_sim", "passed": True}],
        output_path="gs://bucket/report.json",
    )

    assert resolved_path == "report.json"
    mock_file.assert_any_call("report.json", "w")


@patch("cxas_scrapi.utils.reporting._upload_to_gcs")
@patch("builtins.open", new_callable=mock_open)
def test_generate_combined_json_report_gcs_fallback_no_extension(
    mock_file: typing.Any, mock_upload: typing.Any
) -> None:
    mock_upload.return_value = None

    resolved_path = generate_combined_json_report(
        sim_results=[{"name": "test_sim", "passed": True}],
        output_path="gs://bucket/no_ext",
    )

    assert resolved_path == "report_fallback.json"
    mock_file.assert_any_call("report_fallback.json", "w")


def test_generate_combined_report_from_dir_json(tmp_path: typing.Any) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

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
                "status": "FAILED",
                "error_message": "boom",
            }
        ]
    )
    df_callback.to_csv(callback_file, index=False)

    resolved_path = generate_combined_report_from_dir(
        output_dir=str(evals_dir), report_format="json"
    )

    # Defaults to combined_report.json in the output directory.
    assert resolved_path == str(evals_dir / COMBINED_REPORT_JSON_FILENAME)
    assert os.path.exists(resolved_path)

    with open(resolved_path) as f:
        report = json.load(f)

    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 2
    assert report["results"]["simulation"][0]["name"] == "test_sim"
    assert report["results"]["tool"][0]["name"] == "test_tool"
    assert report["results"]["tool"][0]["passed"] is True
    assert report["results"]["callback"][0]["name"] == "test_callback"
    assert report["results"]["callback"][0]["passed"] is False


def test_generate_combined_report_from_dir_json_explicit_output(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    output_path = tmp_path / "custom_report.json"

    resolved_path = generate_combined_report_from_dir(
        output_dir=str(evals_dir),
        output_path=str(output_path),
        report_format="json",
    )

    assert resolved_path == str(output_path)
    with open(output_path) as f:
        report = json.load(f)
    assert report["summary"]["total"] == 1


def test_generate_combined_report_from_dir_invalid_format(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    with pytest.raises(ValueError, match="Unsupported report format"):
        generate_combined_report_from_dir(
            output_dir=str(evals_dir), report_format="xml"
        )


def test_generate_combined_report_from_dir_html_default_unchanged(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    resolved_path = generate_combined_report_from_dir(output_dir=str(evals_dir))

    assert resolved_path == str(evals_dir / COMBINED_REPORT_FILENAME)
    with open(resolved_path) as f:
        assert "Eval Report" in f.read()


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
@patch("glob.glob")
@patch("os.path.exists")
@patch("os.path.isdir")
def test_run_all_evals_filtering(
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
    mock_open_file: typing.Any,
    mock_yaml_load: typing.Any,
    mock_load_golden: typing.Any,
    mock_proto: typing.Any,
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
    mock_open_file: typing.Any,
    mock_yaml_load: typing.Any,
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
        app_name="projects/p",
        rate_limiter=None,
        expectations_only=False,
        deployment_id=None,
        vertex_location="global",
        naturalness=None,
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
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
    mock_file: typing.Any,
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
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
        app_name="projects/p",
        rate_limiter=None,
        expectations_only=False,
        deployment_id=None,
        vertex_location="global",
        naturalness=None,
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
        sim_user_model=None,
        eval_model=None,
        modality="text",
        background_noise_file=None,
        bg_noise_snr=None,
        burst_noise_files=None,
        burst_noise_snr=None,
        use_tool_fakes=False,
        skip_playback_wait=False,
        single_bidi_stream=False,
        progress_callback=ANY,
        capture_agent_audio=False,
        naturalness=None,
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
def test_run_all_evals_with_deployment_id(
    mock_open_file: typing.Any,
    mock_yaml_load: typing.Any,
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/simulations/sim1.yaml"],
    ]
    mock_yaml_load.return_value = [{"name": "sim1"}]

    run_all_evals(
        app_name="projects/p",
        include=["sims"],
        simulation_dir="evals/simulations/",
        deployment_id="dep123",
    )

    mock_sim_evals.assert_called_once_with(
        app_name="projects/p",
        rate_limiter=None,
        expectations_only=False,
        deployment_id="dep123",
        vertex_location="global",
        naturalness=None,
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
def test_run_all_evals_custom_vertex_location(
    mock_open_file: typing.Any,
    mock_yaml_load: typing.Any,
    mock_isdir: typing.Any,
    mock_exists: typing.Any,
    mock_glob: typing.Any,
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
) -> None:
    mock_exists.return_value = True
    mock_isdir.return_value = True
    mock_glob.side_effect = [
        ["evals/simulations/sim1.yaml"],
    ]
    mock_yaml_load.return_value = [{"name": "sim1"}]

    run_all_evals(
        app_name="projects/p",
        include=["sims"],
        simulation_dir="evals/simulations/",
        vertex_location="europe-west1",
    )

    mock_sim_evals.assert_called_once_with(
        app_name="projects/p",
        rate_limiter=None,
        expectations_only=False,
        deployment_id=None,
        vertex_location="europe-west1",
        naturalness=None,
    )


def test_load_sim_test_cases_merges_common_parameters() -> None:
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


def test_load_sim_test_cases_merges_common_expectations() -> None:
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


def test_load_sim_test_cases_without_common_naturalness() -> None:
    """Existing sim files must not grow a naturalness key."""
    yaml_data = """
evals:
  - name: sim1
"""
    with patch("builtins.open", mock_open(read_data=yaml_data)):
        cases = _load_sim_test_cases("dummy.yaml")

    assert "naturalness_metric" not in cases[0]


def test_load_sim_test_cases_merges_common_naturalness_metric() -> None:
    yaml_data = """
common_naturalness_metric:
  pass_threshold: 3.5
  turn_weight: 0.8
evals:
  - name: inherits
  - name: overrides
    naturalness_metric:
      pass_threshold: 4.0
  - name: opts_out
    naturalness_metric: false
"""
    with patch("builtins.open", mock_open(read_data=yaml_data)):
        cases = _load_sim_test_cases("dummy.yaml")

    assert cases[0]["naturalness_metric"] == {
        "pass_threshold": 3.5,
        "turn_weight": 0.8,
    }
    # A per-eval mapping wins key-by-key.
    assert cases[1]["naturalness_metric"] == {
        "pass_threshold": 4.0,
        "turn_weight": 0.8,
    }
    # An explicit false opts a single eval out of the file-wide metric.
    assert cases[2]["naturalness_metric"] is False


def test_load_sim_test_cases_merges_common_background_noise() -> None:
    yaml_data = """
common_background_noise:
  preset: cafe
  snr: 10.0
evals:
  - name: inherits
  - name: overrides
    background_noise:
      preset: call_center
      snr: 12.0
  - name: opts_out
    background_noise: false
"""
    with patch("builtins.open", mock_open(read_data=yaml_data)):
        cases = _load_sim_test_cases("dummy.yaml")

    assert len(cases) == 3
    assert cases[0]["background_noise"] == {"preset": "cafe", "snr": 10.0}
    assert cases[1]["background_noise"] == {
        "preset": "call_center",
        "snr": 12.0,
    }
    assert cases[2]["background_noise"] is False
    assert cases[2]["config"]["background_noise"] is False


def test_generate_combined_report_from_dir_timestamped(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    # Create dummy files with timestamp
    timestamp = "20260622_171403"
    sim_file = evals_dir / f"sim_results_{timestamp}.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    tool_file = evals_dir / f"tool_results_{timestamp}.csv"
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

    callback_file = evals_dir / f"callback_results_{timestamp}.csv"
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

    # Call generate_combined_report_from_dir without output_path (so it
    # resolves it) and with run=False (so it loads from files)
    resolved_path = generate_combined_report_from_dir(
        output_dir=str(evals_dir),
        run=False,
    )

    expected_output_path = os.path.join(
        str(evals_dir),
        add_timestamp_suffix(COMBINED_REPORT_FILENAME, timestamp),
    )
    assert resolved_path == expected_output_path
    assert os.path.exists(expected_output_path)

    with open(expected_output_path) as f:
        content = f.read()
        assert "Eval Report" in content
        assert "test_sim" in content
        assert "test_tool" in content
        assert "test_callback" in content


@patch("cxas_scrapi.evals.runner.Evaluations")
@patch("cxas_scrapi.evals.runner.ToolEvals")
@patch("cxas_scrapi.evals.runner.SimulationEvals")
@patch("cxas_scrapi.evals.runner.CallbackEvals")
@patch("cxas_scrapi.evals.runner.EvalUtils")
def test_run_all_evals_writes_timestamped_files(
    mock_eval_utils: typing.Any,
    mock_callback_evals: typing.Any,
    mock_sim_evals: typing.Any,
    mock_tool_evals: typing.Any,
    mock_evaluations: typing.Any,
    tmp_path: typing.Any,
) -> None:
    # Set up real files in tmp_path
    sims_dir = tmp_path / "simulations"
    sims_dir.mkdir()
    sim_yaml = sims_dir / "sims.yaml"
    sim_yaml.write_text("evals:\n  - name: sim1\n    tags: [P0]")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Mock SimulationEvals.run_simulations to return dummy results
    mock_sim_evals.return_value.run_simulations.return_value = [
        {"name": "sim1", "passed": True}
    ]

    timestamp = "20260622_171403"

    run_all_evals(
        app_name="projects/p",
        include=["sims"],
        simulation_dir=str(sims_dir),
        output_dir=str(output_dir),
        timestamp=timestamp,
    )

    # Verify file was written with timestamp
    expected_file = output_dir / f"sim_results_{timestamp}.json"
    assert expected_file.exists()

    with open(expected_file) as f:
        data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "sim1"
        assert data[0]["passed"] is True


@patch("cxas_scrapi.utils.reporting.evals_runner.run_all_evals")
def test_run_all_evals_expectations_only(
    mock_run_all_evals: typing.Any,
) -> None:
    run_all_evals(
        app_name="projects/p",
        expectations_only=True,
    )
    mock_run_all_evals.assert_called_once_with(
        app_name="projects/p",
        modality="text",
        sim_user_model=None,
        eval_model=None,
        runs=1,
        goldens_dir=None,
        tool_test_file=None,
        simulation_dir=None,
        app_dir=None,
        output_dir=None,
        filter_files=None,
        filter_tags=None,
        filter_names=None,
        parallel=1,
        golden_parallel=1,
        golden_timeout=600,
        include=None,
        bg_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
        timestamp=None,
        expectations_only=True,
        deployment_id=None,
        skip_playback_wait=False,
        single_bidi_stream=False,
        progress_callback=None,
        capture_agent_audio=False,
        vertex_location="global",
        naturalness=None,
    )


@pytest.fixture
def sim_runner() -> typing.Any:
    scripts_dir = os.path.abspath(".agents/skills/cxas-agent-foundry/scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    resolve_patch = patch(
        "config.resolve_project_dir", return_value="/dummy/project"
    )
    path_patch = patch(
        "config.get_project_path",
        side_effect=lambda *parts: os.path.join("/dummy/project", *parts),
    )
    with resolve_patch, path_patch:
        spec = importlib.util.spec_from_file_location(
            "scrapi_sim_runner",
            os.path.join(scripts_dir, "scrapi-sim-runner.py"),
        )
        runner = importlib.util.module_from_spec(spec)
        sys.modules["scrapi_sim_runner"] = runner
        spec.loader.exec_module(runner)
    return runner


def test_sim_runner_load_sim_templates_merges_common_expectations(
    sim_runner: typing.Any,
) -> None:
    yaml_data = """
common_expectations:
  - "Common expectation 1"
  - "Common expectation 2"
evals:
  - name: sim1
    expectations:
      - "Specific expectation 1"
  - name: sim2
"""
    with patch("os.path.exists", return_value=True):  # noqa: SIM117
        with patch("builtins.open", mock_open(read_data=yaml_data)):
            templates = sim_runner.load_sim_templates()

            assert len(templates) == 2
            assert templates["sim1"]["expectations"] == [
                "Specific expectation 1",
                "Common expectation 1",
                "Common expectation 2",
            ]
            assert templates["sim2"]["expectations"] == [
                "Common expectation 1",
                "Common expectation 2",
            ]


def test_sim_runner_load_sim_templates_handles_missing_common_expectations(
    sim_runner: typing.Any,
) -> None:
    yaml_data = """
evals:
  - name: sim1
    expectations:
      - "Specific expectation 1"
"""
    with patch("os.path.exists", return_value=True):  # noqa: SIM117
        with patch("builtins.open", mock_open(read_data=yaml_data)):
            templates = sim_runner.load_sim_templates()

            assert len(templates) == 1
            assert templates["sim1"]["expectations"] == [
                "Specific expectation 1"
            ]


def test_sim_runner_load_sim_templates_backward_compatibility_with_list(
    sim_runner: typing.Any,
) -> None:
    yaml_data = """
- name: sim1
  expectations:
    - "Specific expectation 1"
- name: sim2
  expectations:
    - "Specific expectation 2"
"""
    with patch("os.path.exists", return_value=True):  # noqa: SIM117
        with patch("builtins.open", mock_open(read_data=yaml_data)):
            templates = sim_runner.load_sim_templates()

            assert len(templates) == 2
            assert templates["sim1"]["expectations"] == [
                "Specific expectation 1"
            ]
            assert templates["sim2"]["expectations"] == [
                "Specific expectation 2"
            ]


def _make_sample_turn_trace(
    with_filler: bool = False,
) -> dict[str, typing.Any]:
    child_spans = [
        {
            "name": "VAD",
            "startTime": "2026-10-02T14:00:00.000Z",
            "endTime": "2026-10-02T14:00:01.000Z",
            "duration": "1.000s",
            "attributes": {
                "user transcript": "My internet is down",
            },
        },
        {
            "name": "AGENT: Troubleshoting_Agent",
            "startTime": "2026-10-02T14:00:01.000Z",
            "endTime": "2026-10-02T14:00:03.500Z",
            "duration": "2.500s",
            "childSpans": [
                {
                    "name": "CALLBACK: before_model_callback",
                    "startTime": "2026-10-02T14:00:01.000Z",
                    "endTime": "2026-10-02T14:00:01.180Z",
                    "duration": "0.180s",
                    "attributes": {
                        "[debug] detailed latency": {
                            "total code execution latency (ms)": 60,
                            "waiting for external calls (ms)": 0,
                            "sandbox init latency (ms)": 5,
                        }
                    },
                },
                {
                    "name": "CALLBACK: before_model_callback",
                    "startTime": "2026-10-02T14:00:01.180Z",
                    "endTime": "2026-10-02T14:00:01.300Z",
                    "duration": "0.120s",
                    "attributes": {
                        "[debug] detailed latency": {
                            "total code execution latency (ms)": 40,
                            "waiting for external calls (ms)": 0,
                            "sandbox init latency (ms)": 0,
                        }
                    },
                },
                {
                    "name": "LLM",
                    "startTime": "2026-10-02T14:00:01.300Z",
                    "endTime": "2026-10-02T14:00:02.100Z",
                    "duration": "0.800s",
                    "attributes": {
                        "model": "gemini-2.5-flash-001",
                        "time to first token (ms)": 450,
                        "time to first audio (ms)": 0 if with_filler else 700,
                    },
                },
                {
                    "name": "TOOL: verify_equipment_status",
                    "startTime": "2026-10-02T14:00:01.800Z",
                    "endTime": "2026-10-02T14:00:03.000Z",
                    "duration": "1.200s",
                    "attributes": {
                        "toolDisplayName": "verify_equipment_status",
                        "time to first audio (ms)": 150 if with_filler else 0,
                    },
                },
                {
                    "name": "LLM",
                    "startTime": "2026-10-02T14:00:03.000Z",
                    "endTime": "2026-10-02T14:00:03.500Z",
                    "duration": "0.500s",
                    "attributes": {
                        "model": "gemini-2.5-flash-001",
                        "time to first token (ms)": 220,
                        "time to first audio (ms)": 400 if with_filler else 0,
                    },
                },
            ],
        },
    ]
    return {
        "messages": [
            {"role": "user", "chunks": [{"text": "My internet is down"}]},
            {
                "role": "agent",
                "chunks": [{"text": "Let me check your equipment right now."}],
            },
        ],
        "rootSpan": {
            "name": "TURN",
            "startTime": "2026-10-02T14:00:00.000Z",
            "endTime": "2026-10-02T14:00:03.500Z",
            "duration": "3.500s",
            "childSpans": child_spans,
        },
    }


def test_perceived_latency_rendering_in_html_reports(
    tmp_path: typing.Any,
) -> None:
    # Create an agent JSON definition to verify Option 1 callback folder resolution
    agent_dir = tmp_path / "agents" / "Troubleshoting_Agent"
    agent_dir.mkdir(parents=True)
    agent_json = agent_dir / "Troubleshoting_Agent.json"
    agent_json.write_text(
        json.dumps(
            {
                "displayName": "Troubleshoting_Agent",
                "beforeModelCallbacks": [
                    {
                        "description": " Callback 1",
                        "pythonCode": "agents/Troubleshoting_Agent/before_model_callbacks/before_model_callbacks_01/python_code.py",
                    },
                    {
                        "description": "Disconnect Callback",
                        "pythonCode": "agents/Troubleshoting_Agent/before_model_callbacks/before_model_callbacks_disconnect/python_code.py",
                    },
                ],
            }
        )
    )

    turn_trace = _make_sample_turn_trace(with_filler=True)
    sim_results = [
        {
            "name": "test_voice_sim",
            "passed": True,
            "run": 1,
            "duration_s": 3.5,
            "goals": "1/1",
            "expectations": "1/1",
            "turns": 1,
            "session_id": "sess_pl_1",
            "detailed_trace": [
                "User: My internet is down",
                "Tool Call: verify_equipment_status",
                "Tool Response: {'status': 'online'}",
                "Agent Text: Let me check your equipment right now.",
                "[Callback: BeforeModel (Troubleshoting_Agent)] (180.0 ms)",
                "[Callback: BeforeModel (Troubleshoting_Agent)] (120.0 ms)",
            ],
            "turn_traces": [turn_trace],
            "step_details": [],
            "expectation_details": [],
        }
    ]

    sim_report_path = os.path.join(tmp_path, "sim_report.html")
    generate_html_report(
        results=sim_results,
        output_path=sim_report_path,
        modality="audio",
        model="gemini-2.5-flash",
    )
    with open(sim_report_path) as f:
        sim_html = f.read()

    # Verify Suite, Conversation, and Turn drawers exist and are collapsed by default
    assert 'class="pl-suite-details latency-drawer"' in sim_html
    assert '<span class="pl-arrow">&#9654;</span>' in sim_html
    assert 'class="pl-conv-details latency-drawer"' in sim_html
    assert "pl-turn-details" in sim_html
    assert "wf-first-audio-line" in sim_html
    assert "toggleAllLatency()" in sim_html
    assert "verify_equipment_status" in sim_html
    # Verify Option 1 callback folder names (without python_code.py) and collapsible views
    assert "before_model_callbacks_01" in sim_html
    assert "before_model_callbacks_disconnect" in sim_html
    assert "python_code.py" not in sim_html
    assert "cb-sub-details" in sim_html
    assert "wf-cb-details" in sim_html
    assert "<th>Observation</th>" in sim_html
    assert "Actionable Guidance" not in sim_html
    assert "Callback Time Decomposition" in sim_html
    assert "code 60ms + sandbox 120ms" in sim_html
    assert "cb-spans-details" in sim_html
    assert "Callback Spans (2)" in sim_html
    assert "Customer Speech (VAD)" not in sim_html
    assert "wf-fa-pin" in sim_html
    assert "Audio Streaming" in sim_html
    assert "pl-stack-legend" in sim_html
    assert "pl-legend-dot" in sim_html

    # Verify combined HTML report also includes all three collapsed latency drawers
    combined_report_path = os.path.join(tmp_path, "combined_report.html")
    generate_combined_html_report(
        golden_results=[],
        sim_results=sim_results,
        tool_results=[],
        callback_results=[],
        output_path=combined_report_path,
        sim_modality="audio",
    )
    with open(combined_report_path) as f:
        combined_html = f.read()

    assert 'class="pl-suite-details latency-drawer"' in combined_html
    assert '<span class="pl-arrow">&#9654;</span>' in combined_html
    assert 'class="pl-conv-details latency-drawer"' in combined_html
    assert "pl-turn-details" in combined_html
    assert "wf-first-audio-line" in combined_html
    assert "before_model_callbacks_01" in combined_html
    assert "python_code.py" not in combined_html
    assert "Callback Time Decomposition" in combined_html
    assert "cb-spans-details" in combined_html
    assert "Callback Spans (2)" in combined_html


def test_perceived_latency_standalone_tts_and_barge_in_fallback() -> None:
    # 1. Cascaded turn with standalone TTS span
    cascaded_turn = {
        "messages": [
            {"role": "user", "chunks": [{"text": "Schedule a technician"}]},
            {"role": "agent", "chunks": [{"text": "I can help with that."}]},
        ],
        "rootSpan": {
            "name": "root",
            "startTime": "2026-10-02T14:00:00.000Z",
            "endTime": "2026-10-02T14:00:03.000Z",
            "duration": "3.000s",
            "attributes": {
                "perceived latency (ms)": 1150,
                "input types": ["audio"],
            },
            "childSpans": [
                {
                    "name": "VAD",
                    "startTime": "2026-10-02T14:00:00.000Z",
                    "endTime": "2026-10-02T14:00:01.000Z",
                    "duration": "1.000s",
                    "attributes": {},
                },
                {
                    "name": "LLM",
                    "startTime": "2026-10-02T14:00:01.100Z",
                    "endTime": "2026-10-02T14:00:02.200Z",
                    "duration": "1.100s",
                    "attributes": {
                        "model": "gemini-2.5-flash",
                        "time to first chunk (ms)": 600,
                    },
                },
                {
                    "name": "TTS",
                    "startTime": "2026-10-02T14:00:01.900Z",
                    "endTime": "2026-10-02T14:00:02.400Z",
                    "duration": "0.500s",
                    "attributes": {
                        "model": "gemini-composite-v1",
                        "time to first audio (ms)": 250,
                        "audio duration (ms)": 1800,
                    },
                },
            ],
        },
    }
    res_cascaded = LatencyParser.parse_turn_perceived_latency(cascaded_turn, 1)
    assert res_cascaded is not None
    assert round(res_cascaded["pl_ms"]) == 1150
    assert round(res_cascaded["first_audio_rel_ms"]) == 1150
    assert all(w["kind"] != "vad" for w in res_cascaded["waterfall_spans"])
    assert res_cascaded["pl_source"] == "TTS (gemini-composite-v1)"
    bd = res_cascaded["breakdown_ms"]
    assert round(bd["llm_ttfc_ms"]) == 800
    assert round(bd["tts_ms"]) == 250

    # 2. Barged-in turn where TTS span is missing from childSpans
    barged_in_turn = {
        "messages": [
            {"role": "user", "chunks": [{"text": "Wait actually tomorrow"}]},
            {"role": "agent", "chunks": [{"text": "Checking tomorrow..."}]},
        ],
        "rootSpan": {
            "name": "root",
            "startTime": "2026-10-02T14:00:05.000Z",
            "endTime": "2026-10-02T14:00:08.000Z",
            "duration": "3.000s",
            "attributes": {
                "perceived latency (ms)": 1600,
                "agent audio uri": "gs://bucket/agent.wav",
                "input types": ["audio"],
            },
            "childSpans": [
                {
                    "name": "VAD",
                    "startTime": "2026-10-02T14:00:05.000Z",
                    "endTime": "2026-10-02T14:00:06.000Z",
                    "duration": "1.000s",
                    "attributes": {},
                },
                {
                    "name": "LLM",
                    "startTime": "2026-10-02T14:00:06.200Z",
                    "endTime": "2026-10-02T14:00:07.500Z",
                    "duration": "1.300s",
                    "attributes": {
                        "model": "gemini-2.5-flash",
                        "time to first chunk (ms)": 900,
                    },
                },
            ],
        },
    }
    res_barged = LatencyParser.parse_turn_perceived_latency(barged_in_turn, 2)
    assert res_barged is not None
    assert round(res_barged["pl_ms"]) == 1600
    assert res_barged["pl_source"] == "TTS (Barged-in)"
    bd_barged = res_barged["breakdown_ms"]
    assert round(bd_barged["llm_ttfc_ms"]) == 900
    assert round(bd_barged["tts_ms"]) == 500


def test_audio_analysis_tab_naturalness_and_grades(
    tmp_path: typing.Any,
) -> None:
    run1 = {
        "name": "sim_1",
        "run": 1,
        "passed": True,
        "naturalness_details": {
            "overall_score": 4.5,
            "overall_label": "Human-like",
            "turns": [
                {
                    "label": "Human-like",
                    "factors": [
                        {"quality": "emotion", "score": 4.5},
                        {"quality": "pacing", "score": 4.0},
                    ],
                }
            ],
            "conversation_factors": [
                {"quality": "personaConsistency", "score": 5.0}
            ],
        },
    }
    run2 = {
        "name": "sim_2",
        "run": 1,
        "passed": True,
        "naturalness_details": {
            "overall_score": 3.2,
            "overall_label": "Transitional",
            "turns": [
                {
                    "label": "Transitional",
                    "factors": [
                        {"quality": "emotion", "score": 3.0},
                        {"quality": "pacing", "score": 3.5},
                    ],
                }
            ],
        },
    }
    run3 = {
        "name": "sim_3",
        "run": 1,
        "passed": False,
        "naturalness_details": {
            "overall_score": 1.8,
            "overall_label": "Bot-like",
            "turns": [
                {
                    "label": "Bot-like",
                    "factors": [
                        {"quality": "scriptedness", "score": 1.5},
                        {"quality": "emotion", "score": 2.0},
                    ],
                }
            ],
        },
    }

    # Direct aggregation check
    agg = _aggregate_suite_audio_analysis(sim_results=[run1, run2, run3])
    assert agg["total_runs"] == 3
    assert agg["graded_runs"] == 3
    assert agg["mean_score"] == pytest.approx(3.17, 0.05)
    assert agg["turn_grade_counts"]["Human-like"] == 1
    assert agg["turn_grade_counts"]["Transitional"] == 1
    assert agg["turn_grade_counts"]["Bot-like"] == 1

    out_html = tmp_path / "combined.html"
    generate_combined_html_report(
        sim_results=[run1, run2, run3], output_path=str(out_html)
    )
    html_content = out_html.read_text(encoding="utf-8")

    assert "tab-panel-quality" in html_content
    assert "report-tabs-nav" in html_content
    assert "switchReportTab('quality'" in html_content
    assert "Audio &amp; Conversation Quality" in html_content
    assert "Grade distribution" in html_content
    assert "Human-like" in html_content
    assert "Transitional" in html_content
    assert "Bot-like" in html_content
    assert 'data-grade="human_like"' in html_content
    assert 'data-grade="transitional"' in html_content
    assert 'data-grade="bot_like"' in html_content
    assert "tip-badge" in html_content
    assert "data-tip=" in html_content
    assert (
        "Did the agent correctly read the user" in html_content
        or "emotional state and respond" in html_content
    )
    assert "Is the agent the same person throughout" in html_content


def test_audio_analysis_tab_language_switch_and_mismatch(
    tmp_path: typing.Any,
) -> None:
    run1 = {
        "name": "run_en",
        "run": 1,
        "passed": True,
        "routed_language": "en",
        "transcript": "Hello, how can I help you today?",
    }
    run2 = {
        "name": "run_es",
        "run": 1,
        "passed": True,
        "language": "es",
        "transcript": "Hola, gracias por llamar.",
    }
    run3 = {
        "name": "run_sw_ok",
        "run": 1,
        "passed": True,
        "language_switch": {"requested_language": "es", "adhered": True},
    }
    run4 = {
        "name": "run_sw_fail",
        "run": 1,
        "passed": False,
        "language_switch": {
            "requested_language": "es",
            "adhered": False,
            "mismatch": True,
        },
        "naturalness_details": {
            "failure_reasons": [
                "Agent stayed in English after user requested Spanish"
            ]
        },
    }

    agg = _aggregate_suite_audio_analysis(sim_results=[run1, run2, run3, run4])
    assert agg["total_runs"] == 4
    lang_keys = [lb["key"] for lb in agg["language_bars"]]
    assert "english" in lang_keys
    assert "spanish" in lang_keys
    assert "language_switch" in lang_keys

    # Check language switch mismatch
    sw_bar = next(
        lb for lb in agg["language_bars"] if lb["key"] == "language_switch"
    )
    assert sw_bar["count"] == 2
    assert sw_bar["mismatch_count"] == 1

    out_html = tmp_path / "combined.html"
    generate_combined_html_report(
        sim_results=[run1, run2, run3, run4], output_path=str(out_html)
    )
    html_content = out_html.read_text(encoding="utf-8")

    assert "Language &amp; language switch" in html_content
    assert "filterAudioRunsByLang" in html_content
    assert 'data-mismatch="true"' in html_content
    assert "language_mismatch" in html_content


def test_audio_analysis_tab_multimodal_audio_checks_and_issues(
    tmp_path: typing.Any,
) -> None:
    run1 = {
        "name": "audio_pass",
        "run": 1,
        "passed": True,
        "audio_analysis": {
            "agent_voice_consistency": {"result": "PASS"},
            "no_long_pauses": {"result": "PASS"},
            "agent_having_trouble": {"result": "PASS"},
            "agent_looping": {"result": "PASS"},
            "agent_cutoff": {"result": "PASS"},
        },
        "naturalness_details": {
            "overall_score": 4.5,
            "factor_averages": {
                "pacing": 4.5,
                "emotion": 4.0,
            },
            "turns": [
                {
                    "label": "Human-like",
                    "factors": [
                        {"quality": "pacing", "score": 5},
                        {"quality": "emotion", "score": 4},
                    ],
                }
            ],
            "conversation_factors": [
                {"quality": "coherence", "score": 5},
                {"quality": "conversational_flow", "score": 4},
            ],
        },
        "wer": 0.04,
    }
    run2 = {
        "name": "audio_fail",
        "run": 1,
        "passed": False,
        "audio_analysis": {
            "agent_voice_consistency": {"result": "FAIL"},
            "no_long_pauses": {"result": "FAIL"},
            "agent_having_trouble": {"result": "FAIL"},
            "agent_looping": {"result": "FAIL"},
            "agent_cutoff": {"result": "FAIL"},
        },
        "naturalness_details": {
            "overall_score": 2.0,
            "factor_averages": {
                "pacing": 2.0,
                "emotion": 1.5,
            },
            "turns": [
                {
                    "label": "Bot-like",
                    "factors": [
                        {"quality": "pacing", "score": 2},
                        {"quality": "emotion", "score": 1},
                    ],
                }
            ],
            "conversation_factors": [
                {"quality": "coherence", "score": 2},
                {"quality": "conversational_flow", "score": 2},
            ],
        },
        "wer": 0.35,
    }

    agg = _aggregate_suite_audio_analysis(sim_results=[run1, run2])
    checks_map = {c["key"]: c for c in agg["audio_checks"]}
    for ck in [
        "agent_voice_consistency",
        "no_long_pauses",
        "agent_having_trouble",
        "agent_looping",
        "agent_cutoff",
    ]:
        assert checks_map[ck]["pass_count"] == 1
        assert checks_map[ck]["fail_count"] == 1
        assert checks_map[ck]["total_count"] == 2

    assert agg["wer_summary"]["high_wer_count"] == 1
    assert agg["wer_summary"]["total_count"] == 2

    issue_keys = [ib["key"] for ib in agg["issue_bars"]]
    for expected_issue in [
        "voice_drift",
        "long_pauses",
        "having_trouble",
        "agent_looping",
        "audio_cutoff",
        "high_wer",
    ]:
        assert expected_issue in issue_keys

    out_html = tmp_path / "sim_report.html"
    generate_html_report(
        [run1, run2], str(out_html), modality="audio", model="gemini-2.5-flash"
    )
    html_content = out_html.read_text(encoding="utf-8")

    assert "tab-panel-quality" in html_content
    assert "Audio checks &amp; flow" in html_content
    assert "filterAudioRunsByIssue" in html_content
    assert "data-issues=" in html_content
    assert "audio_cutoff" in html_content
    assert "tip-badge" in html_content
    assert "data-tip=" in html_content
    # Tooltip text pulled from ANALYSIS_REGISTRY
    assert (
        "determine if all the audio files are from the same speaker"
        in html_content
    )
    assert "pauses longer than 20 seconds" in html_content
    assert "repeats the same sentence or phrase multiple times" in html_content
    assert "agent_voice_consistency" in ANALYSIS_REGISTRY

    # Tooltip text pulled from naturalness_prompts
    assert "Did the agent correctly read the user" in html_content
    assert "Conversational rhythm" in html_content
    assert "emotion" in naturalness_prompts.DEFAULT_TURN_QUALITIES


def test_audio_analysis_in_combined_json_report(tmp_path: typing.Any) -> None:
    golden_results = [
        {
            "name": "g1",
            "run": 1,
            "passed": True,
            "modality": "audio",
            "naturalness_details": {"overall_score": 4.2},
        }
    ]
    sim_results = [
        {
            "name": "s1",
            "run": 1,
            "passed": False,
            "naturalness_details": {"overall_score": 2.1},
            "audio_analysis": {"no_long_pauses": {"result": "FAIL"}},
        }
    ]

    out_json = tmp_path / "combined.json"
    generate_combined_json_report(
        golden_results=golden_results,
        sim_results=sim_results,
        output_path=str(out_json),
    )
    data = json.loads(out_json.read_text(encoding="utf-8"))

    assert "audio_analysis" in data["summary"]
    audio_summary = data["summary"]["audio_analysis"]
    assert audio_summary["total_runs"] == 2
    assert audio_summary["graded_runs"] == 2
    assert audio_summary["mean_score"] == pytest.approx(3.15, 0.05)
    assert len(audio_summary["grade_bands"]) == 3
    assert len(audio_summary["run_rows"]) == 2
    assert audio_summary["run_rows"][0]["eval_type"] == "golden"
    assert audio_summary["run_rows"][1]["eval_type"] == "sim"
    assert "long_pauses" in audio_summary["run_rows"][1]["issue_keys"]


def test_audio_conversations_section_and_drawer(tmp_path: typing.Any) -> None:
    sim_run = {
        "name": "order_lookup_sim",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "routed_language": "en",
        "latency_p50": 340,
        "_conv_latency": {"p50_ms": 340.0},
        "wer": 0.03,
        "audio_analysis": {
            "agent_voice_consistency": {
                "result": "PASS",
                "details": "Single speaker tone detected.",
            },
            "no_long_pauses": {
                "result": "PASS",
                "details": "No abnormal latency.",
            },
        },
        "naturalness_details": {
            "overall_score": 4.6,
            "overall_label": "Human-like",
            "turns": [
                {
                    "turn_index": 1,
                    "label": "Human-like",
                    "score": 4.5,
                    "justification": "Clear and natural speech rhythm.",
                    "factors": [
                        {"quality": "pacing", "score": 4.5},
                        {"quality": "emotion", "score": 4.7},
                    ],
                }
            ],
            "conversation_factors": [
                {
                    "quality": "coherence",
                    "score": 4.8,
                    "justification": "Followed topic accurately.",
                }
            ],
        },
        "transcript": [
            {"role": "caller", "text": "Can I check my order status?"},
            {
                "role": "agent",
                "text": "Certainly, let me pull up that information.",
            },
        ],
        "duration_seconds": 24.5,
        "duration_s": 24.5,
    }

    golden_run = {
        "name": "billing_issue_golden",
        "run": 2,
        "passed": False,
        "modality": "audio",
        "routed_language": "es",
        "language_switch": True,
        "language_mismatch": True,
        "failure_reasons": ["Responded in English instead of Spanish"],
        "latency_p50": 980,
        "_conv_latency": {"p50_ms": 980.0},
        "wer": 0.28,
        "audio_analysis": {
            "agent_voice_consistency": {
                "result": "PASS",
                "details": "Consistent speaker timbre.",
            },
            "no_long_pauses": {
                "result": "FAIL",
                "details": "Extended silence detected between turns.",
            },
            "agent_having_trouble": {
                "result": "FAIL",
                "details": "Agent requested clarification repeatedly.",
            },
        },
        "naturalness_details": {
            "overall_score": 2.2,
            "overall_label": "Bot-like",
            "turns": [
                {
                    "turn_index": 1,
                    "label": "Bot-like",
                    "score": 2.0,
                    "justification": "Robotic tone and delayed response.",
                    "factors": [
                        {"quality": "pacing", "score": 2.0},
                        {"quality": "scriptedness", "score": 1.8},
                    ],
                }
            ],
            "conversation_factors": [
                {
                    "quality": "coherence",
                    "score": 2.5,
                    "justification": "Conversation flow was broken.",
                }
            ],
        },
        "transcript": (
            "Caller: Necesito ayuda con mi factura.\n"
            "Agent: Sorry, please repeat your account number."
        ),
        "duration_seconds": 58.2,
        "duration_s": 58.2,
    }

    # 1. Combined report
    out_comb = tmp_path / "combined_convs.html"
    generate_combined_html_report(
        golden_results=[golden_run],
        sim_results=[sim_run],
        output_path=str(out_comb),
    )
    comb_html = out_comb.read_text(encoding="utf-8")

    # Nav tab for Conversations
    assert "switchReportTab('conversations')" in comb_html
    assert 'id="tab-conv-n">2</span>' in comb_html

    # Conversations section
    assert '<section id="conversations"' in comb_html
    assert 'data-tab="conversations"' in comb_html
    assert 'id="audio-conv-search"' in comb_html
    assert 'id="audio-facets"' in comb_html
    assert 'id="audio-active-filters"' in comb_html
    assert 'id="audio-runs-table"' in comb_html
    assert 'id="audio-conv-pager"' in comb_html
    assert 'id="audio-conv-scrim"' in comb_html
    assert 'id="audio-conv-drawer"' in comb_html

    # Facet groups rendered in initial HTML
    assert 'data-g="outcome"' in comb_html
    assert 'data-g="grade"' in comb_html
    assert 'data-g="type"' in comb_html
    assert 'data-g="lang"' in comb_html
    assert 'data-g="checks"' in comb_html
    assert 'data-g="issue"' in comb_html

    # Sortable columns
    assert 'class="sortable" data-sort="run"' in comb_html
    assert 'class="sortable" data-sort="eval"' in comb_html
    assert 'class="sortable" data-sort="type"' in comb_html
    assert 'class="sortable" data-sort="outcome"' in comb_html
    assert 'class="sortable" data-sort="grade"' in comb_html
    assert 'class="sortable r" data-sort="score"' in comb_html
    assert 'class="sortable" data-sort="lang"' in comb_html
    assert 'class="sortable" data-sort="checks"' in comb_html
    assert 'class="sortable r" data-sort="turns"' in comb_html
    assert 'class="sortable r" data-sort="latency"' in comb_html
    assert 'class="sortable r" data-sort="wer"' in comb_html
    assert "Flags / Issues" in comb_html

    # Table rows with pre-rendered data attributes
    assert 'class="audio-run-row clickable"' in comb_html
    assert 'data-row-id="conv-0"' in comb_html
    assert 'data-row-id="conv-1"' in comb_html
    assert 'data-outcome="passed"' in comb_html
    assert 'data-outcome="failed"' in comb_html
    assert 'data-grade="human_like"' in comb_html
    assert 'data-grade="bot_like"' in comb_html
    assert 'data-type="sim"' in comb_html
    assert 'data-type="golden"' in comb_html
    assert 'data-checks="PASS"' in comb_html
    assert 'data-checks="FAIL"' in comb_html

    # Drawer container elements
    assert 'id="dr-h"' in comb_html
    assert 'id="dr-b"' in comb_html
    assert 'id="dr-f"' in comb_html
    assert "stepAudioConv(-1)" in comb_html
    assert "navigator.clipboard.writeText" in comb_html
    assert "switchReportTab('overview')" in comb_html

    # Serialized JSON data blob
    assert 'id="audio-conv-data"' in comb_html
    json_match = re.search(
        r'<script type="application/json" id="audio-conv-data">\s*(\{.*?\})\s*</script>',
        comb_html,
        re.DOTALL,
    )
    assert json_match is not None
    embedded_data = json.loads(json_match.group(1))
    assert "run_rows" in embedded_data
    assert len(embedded_data["run_rows"]) == 2

    # Check transcript and detail normalization in embedded data
    rows = {r["row_id"]: r for r in embedded_data["run_rows"]}
    # In generate_combined_html_report, golden_results are passed first then sim_results
    assert len(rows["conv-1"]["transcript_messages"]) == 2
    assert rows["conv-1"]["transcript_messages"][0][0] == "caller"
    assert "order status" in rows["conv-1"]["transcript_messages"][0][1]
    sim_checks = {c["key"]: c for c in rows["conv-1"]["audio_checks_detail"]}
    assert sim_checks["agent_voice_consistency"]["result"] == "PASS"

    assert len(rows["conv-0"]["transcript_messages"]) == 2
    assert rows["conv-0"]["failure_reasons"] == [
        "Responded in English instead of Spanish"
    ]
    gld_checks = {c["key"]: c for c in rows["conv-0"]["audio_checks_detail"]}
    assert gld_checks["no_long_pauses"]["result"] == "FAIL"
    assert (
        "Extended silence detected"
        in gld_checks["no_long_pauses"]["justification"]
    )
    assert "audio_src" in rows["conv-0"]
    assert "audio_src" in rows["conv-1"]
    assert "Full Conversation" in comb_html

    # 2. Standalone simulation report
    out_sim = tmp_path / "sim_convs.html"
    generate_html_report(
        [sim_run],
        str(out_sim),
        modality="audio",
        model="gemini-2.5-flash",
    )
    sim_html = out_sim.read_text(encoding="utf-8")
    assert 'id="report-nav"' in sim_html
    assert 'id="tab-overview"' in sim_html
    assert 'id="tab-quality"' in sim_html
    assert 'id="tab-conversations"' in sim_html
    assert "switchReportTab('conversations'" in sim_html
    assert 'id="tab-conv-n">1</span>' in sim_html
    assert '<section id="conversations"' in sim_html
    assert 'id="audio-runs-table"' in sim_html
    assert 'id="audio-conv-drawer"' in sim_html
    assert 'id="audio-conv-data"' in sim_html


def test_perceived_latency_redesigned_dashboard_and_tabs(
    tmp_path: typing.Any,
) -> None:
    sample_trace = {
        "name": "Turn",
        "startTime": "2026-10-26T01:00:00.000Z",
        "endTime": "2026-10-26T01:00:03.500Z",
        "duration": "3.5s",
        "attributes": {
            "perceived latency (ms)": 1850.0,
            "input types": ["audio"],
        },
        "childSpans": [
            {
                "name": "VAD",
                "startTime": "2026-10-26T01:00:00.000Z",
                "endTime": "2026-10-26T01:00:00.500Z",
                "duration": "0.5s",
            },
            {
                "name": "Callback",
                "startTime": "2026-10-26T01:00:00.500Z",
                "endTime": "2026-10-26T01:00:00.800Z",
                "duration": "0.3s",
                "attributes": {
                    "agent": "Troubleshooting_Agent",
                    "stage": "BeforeModel",
                    "description": "before_model_callbacks_01",
                    "[debug] detailed latency": {
                        "total code execution latency (ms)": 60,
                        "sandbox init latency (ms)": 5,
                    },
                },
            },
            {
                "name": "TOOL: verify_equipment_status",
                "startTime": "2026-10-26T01:00:00.800Z",
                "endTime": "2026-10-26T01:00:01.300Z",
                "duration": "0.5s",
                "attributes": {
                    "tool": "verify_equipment_status",
                },
            },
            {
                "name": "LLM",
                "startTime": "2026-10-26T01:00:01.300Z",
                "endTime": "2026-10-26T01:00:02.500Z",
                "duration": "1.2s",
                "attributes": {
                    "agent": "Troubleshooting_Agent",
                    "model": "gemini-2.5-flash",
                    "time to first chunk (ms)": 650.0,
                },
            },
            {
                "name": "TTS",
                "startTime": "2026-10-26T01:00:02.500Z",
                "endTime": "2026-10-26T01:00:03.500Z",
                "duration": "1.0s",
                "attributes": {
                    "time to first audio (ms)": 350.0,
                },
            },
        ],
    }

    golden_results = [
        {
            "name": "golden_equipment_check",
            "type": "golden",
            "modality": "audio",
            "passed": True,
            "duration_s": 3.5,
            "turns": [
                {
                    "user_text": "Is the router online?",
                    "agent_text": "Let me check.",
                    "root_span": sample_trace,
                }
            ],
            "turn_traces": [
                {"root_span": sample_trace},
            ],
        }
    ]

    sim_results = [
        {
            "name": "sim_troubleshoot_flow",
            "type": "sim",
            "modality": "audio",
            "passed": True,
            "turns": 2,
            "run": 0,
            "turn_traces": [
                {"root_span": sample_trace},
                {"root_span": sample_trace},
            ],
        }
    ]

    report_path = os.path.join(tmp_path, "canary_combined_report.html")
    generate_combined_html_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=[],
        callback_results=[],
        output_path=report_path,
        golden_modality="audio",
        sim_modality="audio",
    )

    with open(report_path) as f:
        html = f.read()

    assert '<header class="top">' in html
    assert '<nav class="nav" id="report-nav">' in html
    assert 'data-tab="overview"' in html
    assert 'data-tab="latency"' in html
    assert 'data-tab="goldens"' in html
    assert 'data-tab="sims"' in html
    assert '<input type="search" id="q"' in html
    assert "<kbd>/</kbd>" in html
    assert 'id="theme"' in html

    assert 'id="tab-overview"' in html
    assert 'id="tab-latency"' in html
    assert 'id="tab-goldens"' in html
    assert 'id="tab-sims"' in html

    assert 'id="lat-n"' in html
    assert '<div class="tiles">' in html
    assert "Perceived p50" in html
    assert "Perceived p90" in html
    assert "Perceived p95" in html
    assert "LLM first chunk p50" in html
    assert "LLM calls / turn" in html
    assert "Reasoning loops / turn" in html

    assert 'id="lat-trend"' in html
    assert "Perceived latency by turn" in html
    assert "<svg" in html
    assert 'class="hit"' in html
    assert "Turn volume" in html

    assert 'id="lat-tbl"' in html
    assert "Perceived latency, all turns" in html
    assert "Perceived latency, after the first turn" in html
    assert "LLM time to first chunk" in html
    assert "LLM call latency, per call" in html
    assert "LLM total latency, per turn with an LLM call" in html
    assert "BeforeModel callback time, per turn" in html
    assert "Tool time, per turn with a tool call" in html
    assert "First TTS audio" in html
    assert "Reasoning loops per turn (BeforeModel steps)" in html
    assert "LLM calls per turn" in html

    assert "Where a turn's time goes" in html
    assert 'id="lat-parts"' in html
    assert "Tool latency" in html
    assert 'id="lat-tools"' in html
    assert "verify_equipment_status" in html

    assert "Slowest evaluations" in html
    assert 'id="lat-slow"' in html
    assert "jumpTo(" in html

    assert 'class="pl-suite-details latency-drawer"' in html
    assert '<span class="pl-arrow">&#9654;</span>' in html
    assert "Table A: Pre-Speech Component Breakdown" in html
    assert "Callback Time Decomposition" in html


def test_audio_analysis_phase1_metrics_and_intent_filter(
    tmp_path: typing.Any,
) -> None:
    # 1. Run with barge-in during decisive tool call, wall of text TTS, markdown link, prompt leakage
    run1 = {
        "name": "sim_refund_flow",
        "file_name": "evals/goldens/refund_policy.json",
        "run": 1,
        "passed": False,
        "modality": "audio",
        "turn_traces": [
            {
                "rootSpan": {
                    "name": "Turn 1",
                    "childSpans": [
                        {
                            "name": "AGENT: triage",
                            "childSpans": [
                                {
                                    "name": "TTS",
                                    "attributes": {
                                        "audio duration (ms)": 14000.0
                                    },
                                    "duration": "14.0s",
                                }
                            ],
                        },
                        {
                            "name": "VAD: speech_detected",
                            "attributes": {"barge-in": True},
                        },
                        {
                            "name": "TOOL: process_refund",
                            "attributes": {"tool": "process_refund"},
                        },
                    ],
                },
                "parts": [
                    {
                        "role": "agent",
                        "text": (
                            "Certainly! Please visit [help portal]"
                            "(https://example.com/help) <no speech> for more"
                            " info."
                        ),
                    },
                    {
                        "role": "caller",
                        "text": "I want to speak with a human agent please.",
                    },
                ],
            },
            {
                "rootSpan": {
                    "name": "Turn 2",
                    "childSpans": [],
                },
                "parts": [
                    {
                        "role": "agent",
                        "text": (
                            "Certainly! I am transferring to billing_subagent"
                            " now."
                        ),
                    },
                    {
                        "role": "caller",
                        "text": (
                            "Operator, give me a real person or human"
                            " representative now."
                        ),
                    },
                ],
            },
        ],
        "detailed_trace": [
            "routing to billing_agent",
            "handoff to support_flow",
            "transfer to billing_subagent",
        ],
    }

    # 2. Run with second intent
    run2 = {
        "name": "sim_tech_support",
        "source_file": "evals/sims/tech_support.yaml",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "turn_traces": [
            {
                "rootSpan": {
                    "name": "Turn 1",
                    "childSpans": [
                        {
                            "name": "TTS",
                            "duration": "2.0s",
                        }
                    ],
                },
                "parts": [
                    {
                        "role": "agent",
                        "text": (
                            "Hello, how can I assist you with your equipment"
                            " today?"
                        ),
                    }
                ],
            }
        ],
    }

    agg = _aggregate_suite_audio_analysis(sim_results=[run1, run2])
    assert agg["total_runs"] == 2
    assert agg["barge_stats"]["barge_turns"] == 1
    assert agg["barge_stats"]["sessions_with_barge_in"] == 1

    # Verify intent extraction in intents_list
    intent_keys = [it["key"] for it in agg["intents_list"]]
    assert "refund_policy" in intent_keys
    assert "tech_support" in intent_keys

    # Verify per-run fields
    r1 = next(r for r in agg["run_rows"] if r["eval_name"] == "sim_refund_flow")
    assert r1["intent"] == "refund_policy"
    assert r1["barge_turns"] == 1
    assert r1["max_tts_ms"] == 14000.0
    assert r1["caller_requested_human"] is True

    # Verify Phase 1 issue keys on run 1
    r1_issues = r1["issue_keys"]
    assert "verdict_barged_missed" in r1_issues
    assert "wall_of_text" in r1_issues
    assert "markdown_link_tts_leakage" in r1_issues
    assert "prompt_or_directive_leakage" in r1_issues
    assert "early_ack_redundancy" in r1_issues
    assert "subagent_transfer_verbalization" in r1_issues
    assert "subagent_ping_pong_loop" in r1_issues
    assert "caller_requested_human" in r1_issues
    assert "ignored_user" in r1_issues

    # Generate combined HTML report and verify template rendering
    out_html = tmp_path / "combined_phase1.html"
    generate_combined_html_report(
        sim_results=[run1, run2], output_path=str(out_html)
    )
    html_content = out_html.read_text(encoding="utf-8")

    # Intent filter wrap and buttons
    assert 'id="intent-wrap"' in html_content
    assert 'id="intent-btn"' in html_content
    assert 'data-g="intent"' in html_content
    assert 'data-gv="refund_policy"' in html_content
    assert 'data-gv="tech_support"' in html_content
    assert "{'key':" not in html_content

    # Barge-In bar and flags
    assert "Caller Barge-In &amp; Interruption Rate" in html_content
    assert "verdict_barged_missed" in html_content
    assert "wall_of_text" in html_content
    assert "markdown_link_tts_leakage" in html_content
    assert "prompt_or_directive_leakage" in html_content
    assert "early_ack_redundancy" in html_content
    assert "subagent_transfer_verbalization" in html_content
    assert "subagent_ping_pong_loop" in html_content
    assert "caller_requested_human" in html_content
    assert "ignored_user" in html_content


def test_acoustic_metrics_tooltips() -> None:
    tips = _get_acoustic_metrics_tooltips()
    expected_keys = {
        "tts_cache_seam",
        "loudness_jump",
        "midturn_voice_seam",
        "audio_truncation",
        "acoustic_consistency",
    }
    assert expected_keys.issubset(tips.keys())
    assert "3.0" in tips["loudness_jump"] or "dB" in tips["loudness_jump"]
    assert (
        "7.0" in tips["midturn_voice_seam"]
        or "11.0" in tips["midturn_voice_seam"]
    )
    assert "vintage seam" in tips["tts_cache_seam"]
    assert (
        "0.6" in tips["audio_truncation"]
        or "syllable" in tips["audio_truncation"]
    )


def test_audio_analysis_acoustic_metrics_and_drift_aggregation(
    tmp_path: typing.Any,
) -> None:
    run1 = {
        "name": "sim_acoustic_good",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "audio_metrics": {
            "call": {
                "lufs_mean": -21.4,
                "lufs_sd": 0.8,
                "max_adj_dlufs": 1.2,
                "rate_mean": 4.2,
                "rate_cov_pct": 8.5,
                "f0_median_sd_st": 1.1,
                "max_seam_st": 3.2,
                "flags": [],
            },
            "turns": [
                {
                    "turn": 1,
                    "dur_s": 2.4,
                    "lufs": -21.0,
                    "artic_rate": 4.1,
                    "f0_median_hz": 180.0,
                    "max_seam_st": 2.1,
                    "tts": "fresh",
                },
                {
                    "turn": 2,
                    "dur_s": 3.1,
                    "lufs": -21.8,
                    "artic_rate": 4.3,
                    "f0_median_hz": 182.0,
                    "max_seam_st": 3.2,
                    "tts": "fresh",
                },
            ],
        },
    }

    run2 = {
        "name": "sim_acoustic_drift_and_jump",
        "run": 1,
        "passed": False,
        "modality": "audio",
        "audio_metrics": {
            "call": {
                "lufs_mean": -24.0,
                "lufs_sd": 3.2,
                "max_adj_dlufs": 4.8,
                "rate_mean": 3.8,
                "rate_cov_pct": 14.0,
                "f0_median_sd_st": 3.5,
                "max_seam_st": 12.4,
                "flags": ["max_adj_dlufs", "intra_turn_speaker_change"],
            },
            "turns": [],
        },
        "audio_drift": [
            {
                "turn": 2,
                "flags": ["SEAM", "LOUD", "MIDTURN", "TRUNC"],
                "tts": "cached",
                "dlufs": 4.8,
                "dur_s": 1.2,
                "n_syl": 3,
                "agent_text": "Thank you for reaching customer support today.",
            }
        ],
    }

    run3 = {
        "name": "sim_tts_cache_transition_spans",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "turn_traces": [
            {
                "rootSpan": {
                    "name": "Turn 1",
                    "childSpans": [
                        {
                            "name": "TTS",
                            "attributes": {
                                "cache hit": True,
                                "time to first audio (ms)": 12.0,
                            },
                        }
                    ],
                }
            },
            {
                "rootSpan": {
                    "name": "Turn 2",
                    "childSpans": [
                        {
                            "name": "TTS",
                            "attributes": {
                                "cache hit": False,
                                "time to first audio (ms)": 380.0,
                            },
                        }
                    ],
                }
            },
        ],
    }

    suite_audio = _aggregate_suite_audio_analysis(
        sim_results=[run1, run2, run3], golden_results=[]
    )
    ac_sum = suite_audio["acoustic_summary"]
    assert ac_sum["measured_runs"] == 3
    assert ac_sum["clean_runs"] == 1
    assert ac_sum["loud_runs"] == 1
    assert ac_sum["midturn_runs"] == 1
    assert ac_sum["trunc_runs"] == 1
    assert ac_sum["seam_runs"] == 2

    # Check run rows
    rows = suite_audio["run_rows"]
    r1, r2, r3 = rows[0], rows[1], rows[2]
    assert r1["acoustic_detail"]["measured"] is True
    assert "loudness_jump" not in r1["issue_keys"]

    assert r2["acoustic_detail"]["measured"] is True
    assert "loudness_jump" in r2["issue_keys"]
    assert "tts_cache_seam" in r2["issue_keys"]
    assert "midturn_voice_seam" in r2["issue_keys"]
    assert "audio_truncation" in r2["issue_keys"]

    assert "tts_cache_seam" in r3["issue_keys"]

    # Generate combined HTML report and verify template rendering
    out_html = tmp_path / "combined_acoustic.html"
    generate_combined_html_report(
        sim_results=[run1, run2, run3], output_path=str(out_html)
    )
    html_text = out_html.read_text(encoding="utf-8")
    assert "Acoustic Signal Consistency" in html_text
    assert "tts_cache_seam" in html_text
    assert "loudness_jump" in html_text
    assert "midturn_voice_seam" in html_text
    assert "audio_truncation" in html_text


def test_voice_jumps_are_a_top_line_metric(tmp_path: typing.Any) -> None:
    jump = {
        "part": 2,
        "t_s": 6.3,
        "pause_s": 0.4,
        "d_st": 7.8,
        "d_db": 7.7,
        "jump": True,
    }
    smooth = dict(jump, d_st=1.7, d_db=8.5, jump=False)

    def run(name: str, joins: list, flags: list) -> dict:
        return {
            "name": name,
            "run": 1,
            "passed": True,
            "modality": "audio",
            "audio_metrics": {
                "call": {"lufs_mean": -21.0, "max_adj_dlufs": 1.0, "flags": []},
                "turns": [],
            },
            "audio_drift": {
                "rows": [
                    {"turn": 1, "flags": flags, "joins": joins, "dur_s": 6.0},
                ],
            },
        }

    runs = [
        run("sim_jump", [jump], ["JUMP"]),
        run("sim_smooth_join", [smooth], []),
        run("sim_one_part", [], []),
    ]
    suite_audio = _aggregate_suite_audio_analysis(
        sim_results=runs, golden_results=[]
    )
    ac_sum = suite_audio["acoustic_summary"]
    assert ac_sum["join_runs"] == 2
    assert ac_sum["jump_runs"] == 1
    assert ac_sum["jump_turns"] == 1
    assert ac_sum["jump_free_pct"] == 50
    # a voice jump is an acoustic defect: the jump run isn't clean
    assert ac_sum["clean_runs"] == 2
    rows = {r["eval_name"]: r for r in suite_audio["run_rows"]}
    assert "voice_jump" in rows["sim_jump"]["issue_keys"]
    assert "voice_jump" not in rows["sim_smooth_join"]["issue_keys"]

    out_html = tmp_path / "voice_jumps.html"
    generate_combined_html_report(sim_results=runs, output_path=str(out_html))
    html_text = out_html.read_text(encoding="utf-8")
    assert 'id="ov-tile-voice-jump"' in html_text
    assert "1 / 2 runs jump-free" in html_text


def test_audio_analysis_barge_in_eval_fields(tmp_path: typing.Any) -> None:
    run = {
        "name": "sim_barge_in_evaluation",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "step_details": [
            {
                "turn": "turn_1",
                "goal": "Inquire account balance",
                "status": "SUCCESS",
            },
            {
                "turn": "turn_2",
                "goal": "Interrupt agent before confirmation finishes",
                "status": "SUCCESS",
                "barge_in_after_ms": 1500,
                "agent_interrupted": True,
                "barge_in_awareness_injected": True,
                "vad_barge_in": True,
                "vad_skip_reason": None,
                "barge_in_metadata": {
                    "interrupted": True,
                    "played_audio_duration_ms": 1520.0,
                    "total_audio_duration_ms": 4200.0,
                },
            },
        ],
    }

    suite_audio = _aggregate_suite_audio_analysis(
        sim_results=[run], golden_results=[]
    )
    row = suite_audio["run_rows"][0]
    assert row["barge_turns"] == 1
    b_evs = row["acoustic_detail"]["barge_in_details"]
    assert len(b_evs) == 1
    ev = b_evs[0]
    assert ev["step"] == "turn_2"
    assert ev["barge_in_after_ms"] == 1500
    assert ev["agent_interrupted"] is True
    assert ev["vad_barge_in"] is True
    assert ev["played_audio_duration_ms"] == 1520.0
    assert ev["total_audio_duration_ms"] == 4200.0

    out_html = tmp_path / "combined_barge.html"
    generate_combined_html_report(sim_results=[run], output_path=str(out_html))
    html_text = out_html.read_text(encoding="utf-8")
    assert "Caller Barge-In &amp; Interruption Rate" in html_text


def test_audio_analysis_overview_tiles_and_intent_actions(
    tmp_path: typing.Any,
) -> None:
    run1 = {
        "name": "sim_check_pass",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "audio_analysis": {
            "agent_voice_consistency": {"result": "PASS"},
            "no_long_pauses": {"result": "PASS"},
        },
        "audio_checks": {
            "overall_status": "PASS",
            "passed": 2,
            "failed": 0,
            "checks": [],
        },
        "audio_acoustic": {
            "lufs_mean": -19.5,
            "pitch_hz_mean": 180.0,
            "speech_rate_sps": 4.1,
            "max_adj_dlufs": 1.2,
            "max_seam_st": 0.5,
            "flags": [],
        },
        "audio_metrics": {
            "lufs_mean": -19.5,
            "pitch_hz_mean": 180.0,
            "speech_rate_sps": 4.1,
            "max_adj_dlufs": 1.2,
            "max_seam_st": 0.5,
            "flags": [],
        },
        "_conv_latency": {"p50_ms": 1100.0},
        "duration_s": 15.0,
        "naturalness": "4.5/5",
        "naturalness_label": "Human-like",
        "naturalness_details": {
            "overall_score": 4.5,
            "overall_label": "Human-like",
            "latency_ms_by_turn": {"0": 1100.0},
        },
        "language_mismatch": False,
        "wer": 0.04,
        "transcription_wer": 0.04,
        "intent": "account_balance",
        "turn_traces": [_make_sample_turn_trace(with_filler=False)],
    }
    run2 = {
        "name": "sim_check_fail",
        "run": 1,
        "passed": False,
        "modality": "audio",
        "audio_analysis": {
            "agent_voice_consistency": {"result": "FAIL"},
            "no_long_pauses": {"result": "PASS"},
        },
        "audio_checks": {
            "overall_status": "FAIL",
            "passed": 1,
            "failed": 2,
            "checks": [],
        },
        "audio_acoustic": {
            "lufs_mean": -26.0,
            "pitch_hz_mean": 210.0,
            "speech_rate_sps": 5.5,
            "max_adj_dlufs": 5.8,
            "max_seam_st": 8.0,
            "flags": ["max_adj_dlufs"],
        },
        "audio_metrics": {
            "lufs_mean": -26.0,
            "pitch_hz_mean": 210.0,
            "speech_rate_sps": 5.5,
            "max_adj_dlufs": 5.8,
            "max_seam_st": 8.0,
            "flags": ["max_adj_dlufs"],
        },
        "_conv_latency": {"p50_ms": 2600.0},
        "duration_s": 22.0,
        "naturalness": "2.1/5",
        "naturalness_label": "Bot-like",
        "naturalness_details": {
            "overall_score": 2.1,
            "overall_label": "Bot-like",
            "latency_ms_by_turn": {"0": 2600.0},
        },
        "language_mismatch": True,
        "wer": 0.18,
        "transcription_wer": 0.18,
        "intent": "transfer_funds",
        "turn_traces": [
            {
                "rootSpan": {
                    "name": "Turn 1",
                    "childSpans": [
                        {
                            "name": "TTS",
                            "attributes": {
                                "cache hit": True,
                                "time to first audio (ms)": 20.0,
                            },
                        }
                    ],
                }
            }
        ],
        "step_details": [
            {
                "turn": "turn_1",
                "goal": "Transfer money",
                "status": "SUCCESS",
                "barge_in_after_ms": 1200,
                "agent_interrupted": True,
                "barge_in_awareness_injected": True,
                "vad_barge_in": True,
                "barge_in_metadata": {
                    "interrupted": True,
                    "played_audio_duration_ms": 1200.0,
                    "total_audio_duration_ms": 3000.0,
                },
            }
        ],
    }

    suite_audio = _aggregate_suite_audio_analysis(
        sim_results=[run1, run2], golden_results=[]
    )
    ov = suite_audio.get("overview_metrics")
    assert ov is not None
    assert ov["checks_total_runs"] == 2
    assert ov["checks_pass_runs"] == 1
    assert ov["checks_pass_pct"] == 50
    assert ov["lang_matched_runs"] == 1
    assert ov["lang_mismatch_runs"] == 1
    assert ov["lang_adherence_pct"] == 50
    assert ov["flagged_runs"] >= 1
    assert ov["total_llm_turns"] >= 2
    assert ov["avg_llm_turns"] >= 1.0
    assert suite_audio["checks_pass_pct"] == 50
    assert suite_audio["lang_adherence_pct"] == 50
    assert suite_audio["run_rows"][0]["llm_turns_count"] >= 1
    assert suite_audio["run_rows"][0]["acoustic_measured"] is True

    out_html = tmp_path / "combined_overview_tiles.html"
    generate_combined_html_report(
        sim_results=[run1, run2],
        output_path=str(out_html),
        app_name="projects/p/locations/l/apps/a",
    )
    html_text = out_html.read_text(encoding="utf-8")

    assert 'id="overview-tiles"' in html_text
    assert 'class="tiles overview-tiles"' in html_text
    assert 'id="ov-tile-nat"' in html_text
    assert "Audio Naturalness" in html_text
    assert 'id="ov-tile-human"' in html_text
    assert "Human-like Runs" in html_text
    assert 'id="ov-tile-pl"' in html_text
    assert "Perceived Latency" in html_text
    assert 'id="ov-tile-slo"' in html_text
    assert 'id="ov-tile-llm-turns"' in html_text
    assert "Avg # LLM Turns" in html_text
    assert 'id="ov-tile-checks"' in html_text
    assert "Audio Checks" in html_text
    assert 'id="ov-tile-acoustic"' in html_text
    assert "Acoustic Consistency" in html_text
    assert 'id="ov-tile-wer"' in html_text
    assert "Transcription WER" in html_text
    assert 'id="ov-tile-lang"' in html_text
    assert "Language Adherence" in html_text
    assert 'id="ov-tile-barge"' in html_text
    assert "Caller Barge-In Rate" in html_text

    # Tab navigation numbers
    assert 'id="tab-overview-n"' in html_text
    assert 'id="tab-audio-n"' in html_text
    assert 'id="tab-conv-n"' in html_text

    assert "selectAllIntents" in html_text
    assert "intent-master-cb" in html_text
    assert "masterCb.indeterminate = someChecked" in html_text


def test_audio_analysis_opt_in_gating_and_flags(tmp_path: typing.Any) -> None:
    text_run1 = {
        "name": "text_sim_1",
        "run": 1,
        "passed": True,
        "modality": "text",
        "intent": "check_balance",
        "transcript": [
            {"role": "user", "text": "What is my balance?"},
            {"role": "agent", "text": "Your balance is $50."},
        ],
    }
    text_run2 = {
        "name": "text_sim_2",
        "run": 2,
        "passed": False,
        "modality": "text",
        "intent": "check_balance",
        "transcript": [
            {"role": "user", "text": "Transfer money"},
            {"role": "agent", "text": "How much?"},
        ],
    }
    audio_run = {
        "name": "audio_sim_1",
        "run": 1,
        "passed": True,
        "modality": "audio",
        "intent": "audio_check",
        "naturalness_details": {
            "overall_score": 4.5,
            "overall_label": "Human-like",
            "turns": [],
        },
        "audio_analysis": {
            "agent_voice_consistency": {
                "result": "PASS",
                "details": "Consistent tone detected.",
            }
        },
        "transcript": [
            {"role": "caller", "text": "Hello audio"},
            {"role": "agent", "text": "Hi there!"},
        ],
    }

    # 1. Text-only runs with default (None): has_audio_analysis must be False
    suite_audio_text = _aggregate_suite_audio_analysis(
        sim_results=[text_run1, text_run2], golden_results=[]
    )
    assert suite_audio_text["has_audio_analysis"] is False
    assert suite_audio_text["total_runs"] == 2

    # Generated HTML report for text-only must NOT render Audio Analysis tab or audio tiles
    text_html_path = tmp_path / "text_only_report.html"
    generate_combined_html_report(
        sim_results=[text_run1, text_run2],
        output_path=str(text_html_path),
        app_name="projects/p/locations/l/apps/a",
    )
    text_html = text_html_path.read_text(encoding="utf-8")
    assert '<button type="button" data-tab="quality"' not in text_html
    assert 'id="tab-quality"' not in text_html
    assert 'id="ov-tile-nat"' not in text_html
    assert 'id="ov-tile-human"' not in text_html
    assert 'id="ov-tile-checks"' not in text_html
    assert 'id="ov-tile-acoustic"' not in text_html
    assert 'id="ov-tile-wer"' not in text_html
    assert 'id="ov-tile-lang"' not in text_html
    assert 'id="ov-tile-barge"' not in text_html
    # Conversations tab and LLM turns tile should still be present
    assert 'data-tab="conversations"' in text_html
    assert 'id="ov-tile-llm-turns"' in text_html

    # 2. Audio run with explicit audio_analysis=False: suppresses has_audio_analysis
    suite_audio_suppressed = _aggregate_suite_audio_analysis(
        sim_results=[audio_run], golden_results=[], audio_analysis=False
    )
    assert suite_audio_suppressed["has_audio_analysis"] is False

    suppressed_html_path = tmp_path / "suppressed_audio_report.html"
    generate_combined_html_report(
        sim_results=[audio_run],
        output_path=str(suppressed_html_path),
        app_name="projects/p/locations/l/apps/a",
        audio_analysis=False,
    )
    suppressed_html = suppressed_html_path.read_text(encoding="utf-8")
    assert '<button type="button" data-tab="quality"' not in suppressed_html
    assert 'id="tab-quality"' not in suppressed_html
    assert 'id="ov-tile-nat"' not in suppressed_html

    # 3. Audio run with default/None or True: has_audio_analysis must be True
    suite_audio_enabled = _aggregate_suite_audio_analysis(
        sim_results=[audio_run], golden_results=[], audio_analysis=True
    )
    assert suite_audio_enabled["has_audio_analysis"] is True

    enabled_html_path = tmp_path / "enabled_audio_report.html"
    generate_combined_html_report(
        sim_results=[audio_run],
        output_path=str(enabled_html_path),
        app_name="projects/p/locations/l/apps/a",
        audio_analysis=True,
    )
    enabled_html = enabled_html_path.read_text(encoding="utf-8")
    assert '<button type="button" data-tab="quality"' in enabled_html
    assert 'id="tab-quality"' in enabled_html
    assert 'id="ov-tile-nat"' in enabled_html

    # 4. Standalone report: verify gating logic in render_standalone_report_scaffold
    standalone_text_path = tmp_path / "standalone_text.html"
    generate_html_report(
        [text_run1, text_run2],
        str(standalone_text_path),
        "text",
        "gemini-test",
    )
    standalone_text = standalone_text_path.read_text(encoding="utf-8")
    assert 'id="tab-quality"' not in standalone_text
    assert 'data-tab="conversations"' in standalone_text

    standalone_audio_path = tmp_path / "standalone_audio.html"
    generate_html_report(
        [audio_run],
        str(standalone_audio_path),
        "audio",
        "gemini-test",
        audio_analysis=False,
    )
    standalone_audio = standalone_audio_path.read_text(encoding="utf-8")
    assert 'id="tab-quality"' not in standalone_audio

    standalone_audio_enabled_path = tmp_path / "standalone_audio_enabled.html"
    generate_html_report(
        [audio_run],
        str(standalone_audio_enabled_path),
        "audio",
        "gemini-test",
        audio_analysis=True,
    )
    standalone_audio_enabled = standalone_audio_enabled_path.read_text(
        encoding="utf-8"
    )
    assert 'id="tab-quality"' in standalone_audio_enabled

    # 5. Verify SimulationEvals and run_all_evals defaults
    sim_evals = SimulationEvals(app_name="projects/p/locations/l/apps/a")
    assert sim_evals.audio_analysis is False

    sig = inspect.signature(evals_runner.run_all_evals)
    assert sig.parameters["audio_analysis"].default is False


def test_summarize_and_gate_evals_k_of_n_and_fail_closed() -> None:
    from cxas_scrapi.utils.reporting import (  # noqa: PLC0415
        summarize_and_gate_evals,
    )

    sim_results = [
        {"name": "case_a", "passed": True},
        {"name": "case_a", "passed": False},
        {"name": "case_a", "passed": True},
        {
            "name": "case_b",
            "passed": False,
            "expectations": [
                {"expectation": "Must greet caller", "status": "Not Met"}
            ],
        },
        {"name": "case_b", "passed": False},
        {"name": "case_b", "passed": True},
    ]

    summary = summarize_and_gate_evals(
        sim_results=sim_results,
        min_run_passes=2,
        min_pass_rate=0.8,
    )
    assert summary["passed_cases"] == 1
    assert summary["total_cases"] == 2
    assert summary["pass_rate"] == 0.5
    assert summary["gate_passed"] is False

    empty_summary = summarize_and_gate_evals(
        sim_results=[],
        min_run_passes=1,
        min_pass_rate=0.5,
    )
    assert empty_summary["total_cases"] == 0
    assert empty_summary["gate_passed"] is False
    assert "failing closed" in empty_summary["gate_reason"]


@patch("cxas_scrapi.utils.reporting.gcs_utils.GCSUtils")
def test_write_eval_stamp_and_github_step_summary_and_publish_bundle(
    mock_gcs_cls: typing.Any,
    tmp_path: typing.Any,
) -> None:
    from cxas_scrapi.utils.reporting import (  # noqa: PLC0415
        publish_eval_report_bundle,
        summarize_and_gate_evals,
        write_eval_stamp,
        write_github_step_summary,
    )

    mock_uploader = mock_gcs_cls.return_value
    mock_uploader.upload_file.side_effect = lambda uri, _local, **_kw: (
        f"https://storage.mtls.cloud.google.com/{uri.removeprefix('gs://')}"
    )

    report_file = tmp_path / "combined_report.html"
    report_file.write_text("<html>report</html>")
    (tmp_path / "sim_results.json").write_text("[]")

    gate = summarize_and_gate_evals(
        sim_results=[{"name": "case_1", "passed": True}],
        min_run_passes=1,
        min_pass_rate=1.0,
    )
    stamp_path = write_eval_stamp(
        output_dir=str(tmp_path),
        suite_name="sims-text",
        gate_summary=gate,
        modality="text",
        runs=1,
    )
    assert os.path.isfile(stamp_path)

    published = publish_eval_report_bundle(
        output_dir=str(tmp_path),
        report_path=str(report_file),
        gcs_bundle_uri="gs://my-bucket/evals/main/run-1",
        suite_name="sims-text",
    )
    assert "report_url" in published
    assert "latest_url" in published
    latest_calls = [
        c
        for c in mock_uploader.upload_file.call_args_list
        if "latest-sims-text.html" in c.args[0]
    ]
    assert len(latest_calls) == 1
    assert latest_calls[0].kwargs["cache_control"] == "no-cache, max-age=0"

    summary_md = tmp_path / "step_summary.md"
    write_github_step_summary(
        gate_summary=gate,
        suite_name="sims-text",
        published_urls=published,
        summary_path=str(summary_md),
    )
    md_text = summary_md.read_text()
    assert "### sims-text" in md_text
    assert "**1 / 1 cases passed**" in md_text


def test_aggregate_suite_audio_auto_measures_when_agent_wavs_exist(
    tmp_path: typing.Any,
) -> None:
    session_id = "test-session-123"
    wav_file = tmp_path / f"turn_0_agent_{session_id}.wav"

    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)  # 1s silence

    sim_results = [
        {
            "name": "auto_measure_sim",
            "session_id": session_id,
            "agent_audio_paths": {0: str(wav_file)},
            "runs": [
                {
                    "run": 1,
                    "passed": True,
                    "session_id": session_id,
                    "agent_audio_paths": {0: str(wav_file)},
                }
            ],
        }
    ]

    fake_call = {
        "conversation_id": session_id,
        "lufs_mean": -18.5,
        "rate_mean": 3.8,
        "f0_median_sd_st": 1.2,
        "max_seam_st": 2.0,
        "flags": [],
        "drift_reasons": [],
    }
    fake_turns = [
        {
            "turn_index": 0,
            "duration_s": 1.0,
            "lufs": -18.5,
            "rate_syl_per_s": 3.8,
            "f0_median_hz": 180.0,
            "flags": [],
        }
    ]
    with patch(
        "cxas_scrapi.utils.tracing.audio_metrics.measure_call",
        return_value=(fake_call, fake_turns),
    ):
        agg = _aggregate_suite_audio_analysis(sim_results=sim_results)
    assert agg is not None
    conv_data = json.loads(agg["conv_data_json"])
    run_entry = conv_data["run_rows"][0]
    assert run_entry.get("acoustic_measured") is True
    assert sim_results[0]["runs"][0].get("audio_metrics") is not None


def test_load_sim_test_cases_with_common_params_and_background_noise(
    tmp_path: typing.Any,
) -> None:
    yaml_content = """
common_background_noise: "cafe"
common_bg_noise_level: "MEDIUM"
common_params:
  user_type: "premium"
  session_timeout: 30
common_expectations:
  - "Common expectation 1"
evals:
  - name: "case_inherits_common"
    steps:
      - goal: "Check status"
  - name: "case_overrides_level"
    bg_noise_level: "HIGH"
    steps:
      - goal: "Check status"
  - name: "case_overrides_preset"
    background_noise: "traffic"
    steps:
      - goal: "Check status"
  - name: "case_opts_out"
    background_noise: false
    steps:
      - goal: "Check status"
  - name: "case_dict_merge"
    background_noise:
      preset: "call_center"
      level: "LOW"
    steps:
      - goal: "Check status"
"""
    yaml_file = tmp_path / "sims.yaml"
    yaml_file.write_text(yaml_content)

    cases = _load_sim_test_cases(str(yaml_file))
    assert len(cases) == 5

    # Case 0: inherits common
    c0 = cases[0]
    assert c0["background_noise"] == "cafe"
    assert c0["bg_noise_level"] == "MEDIUM"
    assert c0["session_parameters"]["user_type"] == "premium"
    assert c0["session_parameters"]["session_timeout"] == 30
    assert "Common expectation 1" in c0["expectations"]

    # Case 1: overrides level
    c1 = cases[1]
    assert c1["background_noise"] == "cafe"
    assert c1["bg_noise_level"] == "HIGH"

    # Case 2: overrides preset
    c2 = cases[2]
    assert c2["background_noise"] == "traffic"
    assert c2["bg_noise_level"] == "MEDIUM"

    # Case 3: opts out
    c3 = cases[3]
    assert c3["background_noise"] is False
    assert "bg_noise_level" not in c3

    # Case 4: dict merge
    c4 = cases[4]
    assert isinstance(c4["background_noise"], dict)
    assert c4["background_noise"]["preset"] == "call_center"
    assert c4["background_noise"]["level"] == "LOW"
