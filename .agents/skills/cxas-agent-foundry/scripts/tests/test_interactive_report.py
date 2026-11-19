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

"""Tests for generate_interactive_report.py.

Run with:
    uv run pytest .agents/skills/cxas-agent-foundry/scripts/tests/test_interactive_report.py -v
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT_PATH = os.path.join(_SCRIPTS_DIR, "generate_interactive_report.py")

spec = importlib.util.spec_from_file_location("generate_interactive_report", _SCRIPT_PATH)
report_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_module)


@pytest.fixture(autouse=True)
def _clear_ambient_location_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep interactive report tests hermetic regardless of caller shell env."""
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)


def test_fallback_categorize():
    """Verify fallback heuristic categorization."""
    assert report_module.fallback_categorize("Wait for tool call", "Tool not called") == "Tool Calling Issue"
    assert report_module.fallback_categorize("Simulation timeout", "Sim failed to complete") == "Sim Related"
    assert report_module.fallback_categorize("Must provide disclaimer", "Policy statement missing") == "Policy & Compliance"
    assert report_module.fallback_categorize("Must ask one question at a time", "Constraint violated") == "Constraint Violation"
    assert report_module.fallback_categorize("Escalate to human", "Fallback triggered") == "Out-of-Scope / Escalation"
    assert report_module.fallback_categorize("Random reason", "No specific keyword") == "Other Failure"


def test_get_app_metadata():
    """Verify app metadata resolution from app-name arg and config."""
    meta = report_module.get_app_metadata("projects/my-proj/locations/us/apps/my-app-123")
    assert meta["project_id"] == "my-proj"
    assert meta["location"] == "us"
    assert meta["app_id"] == "my-app-123"


def test_generate_dynamic_report_html_structure():
    """Verify HTML generation, parameter extraction, and calculations."""
    sim_data = [
        {
            "name": "test_auth_success",
            "passed": True,
            "session_id": "sess-111",
            "run": 1,
            "duration_s": 2.5,
            "session_parameters": {
                "user_tier": "gold",
                "region": "us-west"
            },
            "expectation_details": [
                {"expectation": "Verify caller", "status": "Met", "justification": "Verified"}
            ],
            "transcript": "User: Hello\nAgent: Hi, I verified your account."
        },
        {
            "name": "test_auth_fail_tool",
            "passed": False,
            "session_id": "sess-222",
            "run": 1,
            "duration_s": 4.1,
            "session_parameters": {
                "user_tier": "silver",
                "region": "us-east"
            },
            "expectation_details": [
                {"expectation": "Must call lookup_user tool", "status": "Not Met", "justification": "Tool was not called"}
            ],
            "transcript": "User: Check my status\nAgent: I cannot help."
        },
        {
            "name": "test_infra_timeout",
            "passed": False,
            "session_id": "sess-333",
            "run": 1,
            "duration_s": 12.0,
            "session_parameters": {
                "user_tier": "bronze",
                "region": "eu-west"
            },
            "expectation_details": [
                {"expectation": "Respond in time", "status": "Not Met", "justification": "Sim timeout infra glitch"}
            ],
            "transcript": "User: Hello"
        }
    ]

    html = report_module.generate_dynamic_report(
        sim_data,
        title="Custom Test Report",
        app_name_arg="projects/proj/locations/us/apps/app1"
    )

    assert "Custom Test Report" in html
    # Check overall vs adjusted pass rate (1 passed out of 3 total, 1 infra fail -> 1/2 = 50% adjusted)
    assert "Overall Pass Rate" in html
    assert "Adjusted Pass Rate" in html
    assert "33.3%" in html  # 1 / 3
    assert "50.0%" in html  # 1 / 2

    # Check that session parameter filters are dynamically created
    assert 'data-param="user_tier"' in html
    assert 'data-param="region"' in html
    assert "gold" in html
    assert "silver" in html
    assert "bronze" in html

    # Check CES session link
    assert "sess-111" in html
    assert "sess-222" in html
    assert "ces.cloud.google.com/projects/proj/locations/us/apps/app1" in html


def test_main_cli_with_input_file(tmp_path):
    """Test CLI execution with a provided JSON input file."""
    test_json = tmp_path / "sim_results.json"
    output_html = tmp_path / "report.html"

    data = {
        "results": [
            {
                "name": "eval_1",
                "passed": True,
                "session_parameters": {"flow": "billing"},
                "duration_s": 1.5,
                "transcript": "User: Pay bill\nAgent: Done"
            }
        ]
    }
    test_json.write_text(json.dumps(data), encoding="utf-8")

    test_args = [
        "generate_interactive_report.py",
        "--input", str(test_json),
        "--output", str(output_html),
        "--title", "CLI Test Report"
    ]

    with patch.object(sys, "argv", test_args):
        report_module.main()

    assert output_html.exists()
    content = output_html.read_text(encoding="utf-8")
    assert "CLI Test Report" in content
    assert "eval_1" in content
    assert "flow" in content


def test_call_llm_for_categories_empty_project_and_location_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify empty project short-circuits and Gemini location precedence."""
    failures = [
        {
            "eval_name": "e1",
            "expectation": "Must call tool",
            "justification": "Not called",
        }
    ]
    with (
        patch.object(
            report_module,
            "get_app_metadata",
            return_value={"project_id": "", "gemini_location": "europe-west1"},
        ),
        patch("cxas_scrapi.utils.gemini.GeminiGenerate") as mock_gemini_cls,
    ):
        assert report_module.call_llm_for_categories(failures) == {}
        mock_gemini_cls.assert_not_called()

    fake_client = MagicMock()
    fake_client.generate.return_value = '{"0": ["Tool Calling Issue"]}'

    # Metadata gemini_location used when env and explicit arg are unset.
    with (
        patch.object(
            report_module,
            "get_app_metadata",
            return_value={
                "project_id": "proj-1",
                "gemini_location": "europe-west1",
            },
        ),
        patch(
            "cxas_scrapi.utils.gemini.GeminiGenerate",
            return_value=fake_client,
        ) as mock_gemini_cls,
    ):
        cats = report_module.call_llm_for_categories(failures)
        assert cats == {0: ["Tool Calling Issue"]}
        mock_gemini_cls.assert_called_once_with(
            project_id="proj-1", location="europe-west1"
        )

    # GOOGLE_CLOUD_LOCATION beats gecx-config.json metadata.
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    with (
        patch.object(
            report_module,
            "get_app_metadata",
            return_value={
                "project_id": "proj-1",
                "gemini_location": "europe-west1",
            },
        ),
        patch(
            "cxas_scrapi.utils.gemini.GeminiGenerate",
            return_value=fake_client,
        ) as mock_gemini_cls,
    ):
        cats = report_module.call_llm_for_categories(failures)
        assert cats == {0: ["Tool Calling Issue"]}
        mock_gemini_cls.assert_called_once_with(
            project_id="proj-1", location="europe-west4"
        )

    # Explicit gemini_location argument overrides env var and metadata.
    with (
        patch.object(
            report_module,
            "get_app_metadata",
            return_value={
                "project_id": "proj-1",
                "gemini_location": "europe-west1",
            },
        ),
        patch(
            "cxas_scrapi.utils.gemini.GeminiGenerate",
            return_value=fake_client,
        ) as mock_gemini_cls,
    ):
        cats = report_module.call_llm_for_categories(
            failures, gemini_location="asia-southeast1"
        )
        assert cats == {0: ["Tool Calling Issue"]}
        mock_gemini_cls.assert_called_once_with(
            project_id="proj-1", location="asia-southeast1"
        )


def test_main_cli_run_forwards_gemini_location(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --run --gemini-location forwards flag to runner and report."""
    monkeypatch.chdir(tmp_path)
    runner_script = (
        tmp_path
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "scripts"
        / "scrapi-sim-runner.py"
    )
    runner_script.parent.mkdir(parents=True)
    runner_script.write_text("# stub\n", encoding="utf-8")

    sim_json = tmp_path / "sim_results_latest.json"
    sim_json.write_text(
        json.dumps({"results": [{"name": "e1", "passed": True}]}),
        encoding="utf-8",
    )
    output_html = tmp_path / "out.html"

    test_args = [
        "generate_interactive_report.py",
        "--run",
        "--gemini-location",
        "us-central1",
        "--output",
        str(output_html),
    ]
    with (
        patch.object(sys, "argv", test_args),
        patch.object(report_module.subprocess, "run") as mock_subproc,
        patch.object(
            report_module,
            "generate_dynamic_report",
            return_value="<html>ok</html>",
        ) as mock_gen_dyn,
    ):
        report_module.main()

    mock_subproc.assert_called_once()
    called_cmd = mock_subproc.call_args.args[0]
    assert called_cmd[-2:] == ["--gemini-location", "us-central1"]
    assert mock_gen_dyn.call_args.kwargs["gemini_location"] == "us-central1"
