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

"""Tests for the canonical XML instruction validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from cxas_scrapi.migration.xml_validator import (
    main,
    validate_canonical_instruction,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

CANONICAL_EXAMPLES = [
    REPO_ROOT / "examples/bella_notte/agents/Bella_Notte_Host/instruction.txt",
    REPO_ROOT / "examples/bella_notte/agents/Reservation_Agent/instruction.txt",
    REPO_ROOT / "examples/bella_notte/agents/Takeout_Agent/instruction.txt",
    REPO_ROOT
    / ".agents/skills/cxas-agent-foundry/assets/project-template/cxas_app"
    / "Sample_Support_Agent/agents/root_agent/instruction.txt",
]


@pytest.mark.parametrize(
    "path", CANONICAL_EXAMPLES, ids=lambda p: p.parent.name
)
def test_canonical_examples_pass(path: Path) -> None:
    """Every canonical instruction in the repo validates clean."""
    assert path.exists(), f"missing fixture: {path}"
    text = path.read_text(encoding="utf-8")
    assert validate_canonical_instruction(text) == []


def test_empty_string_reports_missing_required_tags() -> None:
    diagnostics = validate_canonical_instruction("")
    assert "Instruction is empty." in diagnostics
    assert any(
        "Missing required top-level tag <role>" in d for d in diagnostics
    )
    assert any(
        "Missing required top-level tag <persona>" in d for d in diagnostics
    )


def test_whitespace_only_is_treated_as_empty() -> None:
    assert validate_canonical_instruction("   \n  \t  ") != []


def test_legacy_camelcase_schema_is_rejected() -> None:
    """The OLD <Agent><Conversation_Schema> shape produces specific
    banned-tag diagnostics naming the offending tags."""
    legacy = """
<Agent>
  <Name>FooBot</Name>
  <Role>do things</Role>
  <Persona>
    <communication_style>formal</communication_style>
  </Persona>
  <General_Instruction>be careful</General_Instruction>
  <Conversation_Schema>
    <state id="main">
      <instructions>do x</instructions>
      <transitions>
        <transition condition="done" next_state="end"/>
      </transitions>
    </state>
  </Conversation_Schema>
</Agent>
    """
    diagnostics = validate_canonical_instruction(legacy)
    joined = "\n".join(diagnostics)
    assert "<Agent>" in joined
    assert "<Conversation_Schema>" in joined
    assert "<state>" in joined
    assert "<transitions>" in joined
    assert "<Persona>" in joined
    assert "<General_Instruction>" in joined
    # Required canonical tags absent → also reported.
    assert any(
        "Missing required top-level tag <role>" in d for d in diagnostics
    )
    assert any(
        "Missing required top-level tag <persona>" in d for d in diagnostics
    )


def test_taskflow_required_when_steps_exist() -> None:
    """Bare <subtask>/<step> without a wrapping <taskflow> is rejected."""
    text = """
<role>r</role>
<persona>p</persona>
<subtask name="X">
  <step name="Y"><trigger>t</trigger><action>a</action></step>
</subtask>
    """
    diagnostics = validate_canonical_instruction(text)
    assert any(
        "Missing required top-level tag <taskflow>" in d for d in diagnostics
    )


def test_taskflow_not_required_when_no_steps() -> None:
    """Simple role+persona+guidelines instructions are valid."""
    text = """
<role>specialist</role>
<persona>warm</persona>
<guidelines><guideline name="x">be nice</guideline></guidelines>
    """
    assert validate_canonical_instruction(text) == []


def test_taskflow_without_subtasks_is_rejected() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow></taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    assert any("<taskflow> has 0 <subtask>" in d for d in diagnostics)


def test_subtask_without_steps_is_rejected() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="Empty"></subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    assert any('<subtask name="Empty"> has 0 <step>' in d for d in diagnostics)


def test_subtask_missing_name_attribute_is_rejected() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask>
    <step name="Y"><trigger>t</trigger><action>a</action></step>
  </subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    assert any(
        "<subtask> is missing the required 'name' attribute" in d
        for d in diagnostics
    )


def test_step_missing_name_attribute_is_rejected() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="X">
    <step><trigger>t</trigger><action>a</action></step>
  </subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    assert any(
        "missing the required 'name' attribute" in d and "<step>" in d
        for d in diagnostics
    )


def test_step_without_exactly_one_trigger_and_action_is_rejected() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="X">
    <step name="NoTrigger"><action>a</action></step>
    <step name="TwoActions">
      <trigger>t</trigger>
      <action>a1</action>
      <action>a2</action>
    </step>
  </subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    joined = "\n".join(diagnostics)
    assert '<step name="NoTrigger"> must contain exactly 1 <trigger>' in joined
    assert '<step name="TwoActions"> must contain exactly 1 <action>' in joined


def test_malformed_xml_returns_syntax_error_diagnostic() -> None:
    diagnostics = validate_canonical_instruction("<role>unclosed")
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith("XML syntax error:")


def test_wrong_tool_syntax_is_flagged() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="X">
    <step name="Y">
      <trigger>t</trigger>
      <action>1. Call {TOOL: foo}. 2. Call ${@TOOL: bar}.</action>
    </step>
  </subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    joined = "\n".join(diagnostics)
    assert "Wrong tool reference syntax: '{TOOL: foo}'" in joined
    assert "Wrong tool reference syntax: '${@TOOL: bar}'" in joined


def test_wrong_agent_syntax_is_flagged() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="X">
    <step name="Y">
      <trigger>t</trigger>
      <action>Transfer to ${AGENT: Foo} then {AGENT: Bar}.</action>
    </step>
  </subtask>
</taskflow>
    """
    diagnostics = validate_canonical_instruction(text)
    joined = "\n".join(diagnostics)
    assert "Wrong agent reference syntax: '${AGENT: Foo}'" in joined
    assert "Wrong agent reference syntax: '{AGENT: Bar}'" in joined


def test_correct_tool_and_agent_syntax_pass() -> None:
    text = """
<role>r</role>
<persona>p</persona>
<taskflow>
  <subtask name="X">
    <step name="Y">
      <trigger>t</trigger>
      <action>Call {@TOOL: foo} then transfer to {@AGENT: Bar}.</action>
    </step>
  </subtask>
</taskflow>
    """
    assert validate_canonical_instruction(text) == []


def test_main_cli_returns_zero_for_canonical_files(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([str(CANONICAL_EXAMPLES[0])])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_cli_returns_nonzero_for_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("<Agent><Name>x</Name></Agent>", encoding="utf-8")
    exit_code = main([str(bad)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "<Agent>" in out


def test_main_cli_reports_missing_files(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist.txt"
    exit_code = main([str(missing)])
    assert exit_code == 2
    out = capsys.readouterr().out
    assert "ERROR" in out


def test_main_cli_with_no_args_returns_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "usage:" in err
