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
"""Tests for the llm-lint CLI command."""

import argparse
import json
from pathlib import Path
from unittest import mock

from cxas_scrapi.cli.llm_lint import llm_lint, resolve_gcp_credentials
from cxas_scrapi.prompts import LLM_LINT_SYSTEM_PROMPT, LLM_LINT_USER_PROMPT


def test_resolve_gcp_credentials_from_cli():
    """Test resolving credentials directly from CLI arguments."""
    agent_dir = Path("/dummy/agent")
    proj, loc = resolve_gcp_credentials(
        agent_dir,
        cli_project_id="cli-proj",
        cli_location="cli-loc",
    )
    assert proj == "cli-proj"
    assert loc == "cli-loc"


def test_resolve_gcp_credentials_from_env(monkeypatch):
    """Test resolving credentials from environment variables."""
    monkeypatch.setenv("PROJECT_ID", "env-proj")
    monkeypatch.setenv("LOCATION", "env-loc")
    agent_dir = Path("/dummy/agent")
    proj, loc = resolve_gcp_credentials(agent_dir)
    assert proj == "env-proj"
    assert loc == "env-loc"


def test_resolve_gcp_credentials_from_config(tmp_path):
    """Test resolving credentials from walking up to gecx-config.json."""
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)

    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "config-proj",
        "location": "config-loc",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    proj, loc = resolve_gcp_credentials(agent_dir)
    assert proj == "config-proj"
    assert loc == "config-loc"


@mock.patch("cxas_scrapi.cli.llm_lint.GeminiGenerate", autospec=True)
def test_llm_lint_success(mock_gemini_cls, tmp_path):
    """Test execution of llm_lint when no global instruction exists.

    Uses mocking for the Gemini API call.
    """
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)
    instruction_file = agent_dir / "instruction.txt"
    instruction_file.write_text(
        "Don't use generic responses.", encoding="utf-8"
    )

    # Create active project gecx-config.json
    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-project",
        "location": "us-central1",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    # Stub GeminiGenerate instance
    mock_gemini_inst = mock_gemini_cls.return_value
    mock_gemini_inst.generate.return_value = "## SUMMARY\nAll good."

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        project_id=None,
        location=None,
        model="gemini-2.5-flash",
        output=None,
    )

    # Change directory to tmp_path so CWD resolution works
    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        llm_lint(args)

    # Assertions
    mock_gemini_cls.assert_called_once_with(
        project_id="test-project",
        location="us-central1",
        model_name="gemini-2.5-flash",
    )

    expected_user_prompt = LLM_LINT_USER_PROMPT.format(
        global_instruction_content="",
        instruction_content="Don't use generic responses.",
        dynamic_instruction_content="",
    )
    mock_gemini_inst.generate.assert_called_once_with(
        prompt=expected_user_prompt,
        system_prompt=LLM_LINT_SYSTEM_PROMPT,
    )

    # Check if the report is written to file
    report_file = agent_dir / "llm_lint_report.md"
    assert report_file.exists()
    assert report_file.read_text(encoding="utf-8") == "## SUMMARY\nAll good."


@mock.patch("cxas_scrapi.cli.llm_lint.GeminiGenerate", autospec=True)
def test_llm_lint_with_global_instruction(mock_gemini_cls, tmp_path):
    """Test execution of llm_lint when global instruction exists.

    Uses mocking for the Gemini API call.
    """
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)
    instruction_file = agent_dir / "instruction.txt"
    instruction_file.write_text(
        "Don't use generic responses.", encoding="utf-8"
    )

    # Create global instruction file at root (tmp_path)
    global_instruction_file = tmp_path / "global_instruction.txt"
    global_instruction_file.write_text("Always be polite.", encoding="utf-8")

    # Create active project gecx-config.json
    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-project",
        "location": "us-central1",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    # Stub GeminiGenerate instance
    mock_gemini_inst = mock_gemini_cls.return_value
    mock_gemini_inst.generate.return_value = (
        "## SUMMARY\nAll good with global rules."
    )

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        project_id=None,
        location=None,
        model="gemini-2.5-flash",
        output=None,
    )

    # Change directory to tmp_path so CWD resolution works
    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        llm_lint(args)

    # Assertions
    mock_gemini_cls.assert_called_once_with(
        project_id="test-project",
        location="us-central1",
        model_name="gemini-2.5-flash",
    )

    expected_user_prompt = LLM_LINT_USER_PROMPT.format(
        global_instruction_content="Always be polite.",
        instruction_content="Don't use generic responses.",
        dynamic_instruction_content="",
    )
    mock_gemini_inst.generate.assert_called_once_with(
        prompt=expected_user_prompt,
        system_prompt=LLM_LINT_SYSTEM_PROMPT,
    )

    # Check if the report is written to file
    report_file = agent_dir / "llm_lint_report.md"
    assert report_file.exists()
    assert (
        report_file.read_text(encoding="utf-8")
        == "## SUMMARY\nAll good with global rules."
    )


@mock.patch("cxas_scrapi.cli.llm_lint.GeminiGenerate", autospec=True)
def test_llm_lint_with_dynamic_callbacks(mock_gemini_cls, tmp_path):
    """Test execution of llm_lint when callbacks contain no DIs.

    Verifies only the base report is generated.
    """
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)
    instruction_file = agent_dir / "instruction.txt"
    instruction_file.write_text(
        "Don't use generic responses.", encoding="utf-8"
    )

    # Create a dummy before_model_callback file
    # (no dict variables containing "instruction" inside)
    cb_dir = agent_dir / "before_model_callbacks" / "my_callback"
    cb_dir.mkdir(parents=True)
    cb_code_file = cb_dir / "python_code.py"
    cb_code_file.write_text(
        "def before_model_callback(): pass", encoding="utf-8"
    )

    # Create active project gecx-config.json
    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-project",
        "location": "us-central1",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    # Stub GeminiGenerate instance
    mock_gemini_inst = mock_gemini_cls.return_value
    mock_gemini_inst.generate.side_effect = [
        "## SUMMARY\nBase instructions are great."
    ]

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        project_id=None,
        location=None,
        model="gemini-2.5-flash",
        output=None,
    )

    # Change directory to tmp_path so CWD resolution works
    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        llm_lint(args)

    # Verify call count (only 1 for base because callback has no DI dict)
    assert mock_gemini_inst.generate.call_count == 1

    # Check if base report is written to file and callback is skipped
    base_report = agent_dir / "llm_lint_report.md"
    cb_report = (
        agent_dir / "llm_lint_report_before_model_callbacks_my_callback.md"
    )

    assert base_report.exists()
    assert (
        base_report.read_text(encoding="utf-8")
        == "## SUMMARY\nBase instructions are great."
    )
    assert not cb_report.exists()


@mock.patch("cxas_scrapi.cli.llm_lint.GeminiGenerate", autospec=True)
def test_llm_lint_with_dict_dynamic_instructions(mock_gemini_cls, tmp_path):
    """Test execution of llm_lint when callbacks contain dynamic DIs.

    Verifies reports are mapped correctly by state.
    """
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)
    instruction_file = agent_dir / "instruction.txt"
    instruction_file.write_text(
        "Don't use generic responses.", encoding="utf-8"
    )

    # Create a callback defining dynamic instructions dictionary mapped by state
    cb_dir = agent_dir / "before_agent_callbacks" / "my_callback"
    cb_dir.mkdir(parents=True)
    cb_code_file = cb_dir / "python_code.py"
    cb_code_file.write_text(
        "state_instructions = {\n"
        "  'payment_failed': 'Explain the payment failure reason.',\n"
        "  'payment_success': 'Confirm payment details.'\n"
        "}",
        encoding="utf-8",
    )

    # Create active project gecx-config.json
    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-project",
        "location": "us-central1",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    # Stub GeminiGenerate instance (1 base run + 2 state runs)
    mock_gemini_inst = mock_gemini_cls.return_value
    mock_gemini_inst.generate.side_effect = [
        "## SUMMARY\nBase report.",
        "## SUMMARY\nPayment failed lint report.",
        "## SUMMARY\nPayment success lint report.",
    ]

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        project_id=None,
        location=None,
        model="gemini-2.5-flash",
        output=None,
    )

    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        llm_lint(args)

    # Verify call count (1 base + 2 states)
    assert mock_gemini_inst.generate.call_count == 3

    # Check report outputs
    base_report = agent_dir / "llm_lint_report.md"
    failed_report_name = (
        "llm_lint_report_before_agent_callbacks_my_callback_payment_failed.md"
    )
    failed_state_report = agent_dir / failed_report_name
    success_report_name = (
        "llm_lint_report_before_agent_callbacks_my_callback_payment_success.md"
    )
    success_state_report = agent_dir / success_report_name

    assert base_report.exists()
    assert base_report.read_text(encoding="utf-8") == "## SUMMARY\nBase report."

    assert failed_state_report.exists()
    assert (
        failed_state_report.read_text(encoding="utf-8")
        == "## SUMMARY\nPayment failed lint report."
    )

    assert success_state_report.exists()
    assert (
        success_state_report.read_text(encoding="utf-8")
        == "## SUMMARY\nPayment success lint report."
    )


@mock.patch("cxas_scrapi.cli.llm_lint.GeminiGenerate", autospec=True)
def test_llm_lint_warning_on_non_recommended_callbacks(
    mock_gemini_cls, tmp_path
):
    """Test dynamic DIs outside before_agent_callbacks warn."""
    agent_dir = tmp_path / "agents" / "my-agent"
    agent_dir.mkdir(parents=True)
    instruction_file = agent_dir / "instruction.txt"
    instruction_file.write_text(
        "Don't use generic responses.", encoding="utf-8"
    )

    # Create callback under a non-recommended callback type
    cb_dir = agent_dir / "before_model_callbacks" / "my_callback"
    cb_dir.mkdir(parents=True)
    cb_code_file = cb_dir / "python_code.py"
    cb_code_file.write_text(
        "state_instructions = {'state_a': 'Dynamic prompt content.'}",
        encoding="utf-8",
    )

    # Create active project gecx-config.json
    config_path = tmp_path / "gecx-config.json"
    config_data = {
        "gcp_project_id": "test-project",
        "location": "us-central1",
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    # Stub GeminiGenerate instance
    mock_gemini_inst = mock_gemini_cls.return_value
    mock_gemini_inst.generate.side_effect = [
        "## SUMMARY\nBase report.",
        "## SUMMARY\nState A report.",
    ]

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        project_id=None,
        location=None,
        model="gemini-2.5-flash",
        output=None,
    )

    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        llm_lint(args)

    # Check that the warning was prepended to the report file
    failed_report_name = (
        "llm_lint_report_before_model_callbacks_my_callback_state_a.md"
    )
    state_report = agent_dir / failed_report_name
    assert state_report.exists()
    content = state_report.read_text(encoding="utf-8")
    assert "> [!WARNING]" in content
    assert "**LINTER WARNING**" in content
    assert "before_model_callbacks/my_callback" in content
