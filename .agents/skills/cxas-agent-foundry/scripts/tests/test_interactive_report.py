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
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT_PATH = os.path.join(_SCRIPTS_DIR, "generate_interactive_report.py")

spec = importlib.util.spec_from_file_location("generate_interactive_report", _SCRIPT_PATH)
report_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_module)


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
