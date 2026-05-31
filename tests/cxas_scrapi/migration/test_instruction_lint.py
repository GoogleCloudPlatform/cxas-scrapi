# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for `cxas_scrapi.migration.instruction_lint.lint_instruction_text`.

The helper is a thin wrapper around the registered instruction lint
rules, curated to the schema/syntax rules that operate on standalone
text. These tests verify the curation behaves as advertised: canonical
text passes, legacy text fires the right rule IDs, and cross-file
rules (I008/I009) stay silent without a real project root.
"""

from __future__ import annotations

from cxas_scrapi.migration.instruction_lint import lint_instruction_text

CANONICAL = """\
<role>do things</role>
<persona>- be helpful</persona>
<taskflow>
  <subtask name="Greet">
    <step name="Welcome">
      <trigger>start</trigger>
      <action>1. Transfer to {@AGENT: BillingAgent}.</action>
    </step>
  </subtask>
</taskflow>
"""

LEGACY = """\
<Agent>
  <Name>X</Name>
  <Conversation_Schema>
    <state id="main">
      <transitions>
        <transition condition="x" next_state="y"/>
      </transitions>
    </state>
  </Conversation_Schema>
</Agent>
"""


def test_canonical_text_has_no_errors() -> None:
    results = lint_instruction_text(CANONICAL, "GreetAgent")
    assert results == []


def test_legacy_text_fires_i015_banned_tags() -> None:
    results = lint_instruction_text(LEGACY, "LegacyAgent")
    rule_ids = {r.rule_id for r in results}
    assert "I015" in rule_ids
    # I001 also fires because <role>/<persona>/<taskflow> are absent.
    assert "I001" in rule_ids


def test_legacy_text_does_not_false_positive_on_cross_file_rules() -> None:
    """I008/I009 need agent/tool registries we don't populate here — they
    must not fire on standalone text."""
    results = lint_instruction_text(LEGACY, "LegacyAgent")
    rule_ids = {r.rule_id for r in results}
    assert "I008" not in rule_ids
    assert "I009" not in rule_ids


def test_wrong_tool_syntax_fires_i011() -> None:
    bad = CANONICAL.replace("{@AGENT: BillingAgent}", "${TOOL: get_balance}")
    results = lint_instruction_text(bad, "X")
    assert any(r.rule_id == "I011" for r in results)


def test_filename_appears_in_results() -> None:
    results = lint_instruction_text(LEGACY, "MyAgent")
    assert results
    assert all(r.file.endswith("MyAgent.txt") for r in results)
