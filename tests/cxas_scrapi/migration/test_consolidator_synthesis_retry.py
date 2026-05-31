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

"""Tests for the canonical-XML validate-then-retry loop in
:meth:`StructuralConsolidator.synthesize_instructions`."""

from __future__ import annotations

import pytest

from cxas_scrapi.migration import structural_consolidator as sc_module
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationIR,
)
from cxas_scrapi.migration.structural_consolidator import (
    StructuralConsolidator,
    XMLSchemaError,
    _build_validator_feedback,
)
from cxas_scrapi.utils.linter import LintResult, Severity

CANONICAL_XML = """\
<role>do things</role>
<persona>- be helpful</persona>
<taskflow>
  <subtask name="Greet">
    <step name="Welcome">
      <trigger>Conversation begins.</trigger>
      <action>1. Say hello.</action>
    </step>
  </subtask>
</taskflow>
"""

LEGACY_XML = """\
<Agent>
  <Name>X</Name>
  <Role>do</Role>
  <Conversation_Schema>
    <state id="m"><instructions>x</instructions><transitions/></state>
  </Conversation_Schema>
</Agent>
"""


def _ir() -> MigrationIR:
    return MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            "set_active_flow": IRTool(
                id="set_active_flow",
                name="projects/p/locations/us/apps/t/tools/set_active_flow",
                type="OPENAPI",
                payload={},
            )
        },
        agents={
            "GreetAgent": IRAgent(
                type="PLAYBOOK",
                display_name="GreetAgent",
                instruction="(placeholder)",
            ),
        },
    )


class _FakeDesigner:
    """Replaces AsyncAgentDesigner. Returns a fixed blueprint then
    cycles through ``xml_responses`` for each 2B call."""

    def __init__(
        self,
        gemini_client,
        *,
        xml_responses: list[str],
    ):
        self.gemini = gemini_client
        self._xml_responses = list(xml_responses)
        self.calls_2b: list[dict] = []

    async def run_step_2a(self, **kwargs):
        return {"role": "stub", "agent_metadata": {}}

    async def run_step_2b_instructions(self, **kwargs):
        self.calls_2b.append(kwargs)
        if not self._xml_responses:
            return ""
        return self._xml_responses.pop(0)


def _patch_designer_class(monkeypatch, *, xml_responses: list[str]):
    """Patch AsyncAgentDesigner so synthesize_instructions uses the fake."""
    instances: list[_FakeDesigner] = []

    def factory(gemini_client):
        inst = _FakeDesigner(gemini_client, xml_responses=xml_responses)
        instances.append(inst)
        return inst

    monkeypatch.setattr(sc_module, "AsyncAgentDesigner", factory)
    return instances


def _patch_tree_view(monkeypatch):
    """Make the combined tree view non-empty so synthesis proceeds."""
    monkeypatch.setattr(
        sc_module,
        "_build_combined_tree_view",
        lambda members, source_data, ir: "stub tree view",
    )


@pytest.mark.asyncio
async def test_canonical_xml_on_first_call_no_retry(monkeypatch):
    instances = _patch_designer_class(
        monkeypatch, xml_responses=[CANONICAL_XML]
    )
    _patch_tree_view(monkeypatch)
    ir = _ir()
    consolidator = StructuralConsolidator(ir, gemini_client=None)
    groupings = {"GreetAgent": {"agents": ["GreetAgent"], "is_root": True}}
    consolidated_ir = ir  # for this test we don't care about consolidation

    statuses = await consolidator.synthesize_instructions(
        consolidated_ir, groupings
    )

    assert statuses == {"GreetAgent": "ok"}
    assert len(instances) == 1
    assert len(instances[0].calls_2b) == 1
    assert instances[0].calls_2b[0].get("feedback") is None


@pytest.mark.asyncio
async def test_bad_then_good_xml_retries_once_then_succeeds(monkeypatch):
    instances = _patch_designer_class(
        monkeypatch, xml_responses=[LEGACY_XML, CANONICAL_XML]
    )
    _patch_tree_view(monkeypatch)
    ir = _ir()
    consolidator = StructuralConsolidator(ir, gemini_client=None)
    groupings = {"GreetAgent": {"agents": ["GreetAgent"], "is_root": True}}

    statuses = await consolidator.synthesize_instructions(ir, groupings)

    assert statuses == {"GreetAgent": "ok"}
    assert len(instances[0].calls_2b) == 2
    retry_kwargs = instances[0].calls_2b[1]
    assert retry_kwargs.get("feedback") is not None
    assert "FAILED CANONICAL-XML VALIDATION" in retry_kwargs["feedback"]
    assert "<Agent>" in retry_kwargs["feedback"]


@pytest.mark.asyncio
async def test_bad_xml_twice_raises_xml_schema_error(monkeypatch):
    _patch_designer_class(monkeypatch, xml_responses=[LEGACY_XML, LEGACY_XML])
    _patch_tree_view(monkeypatch)
    ir = _ir()
    consolidator = StructuralConsolidator(ir, gemini_client=None)
    groupings = {"GreetAgent": {"agents": ["GreetAgent"], "is_root": True}}

    with pytest.raises(XMLSchemaError) as excinfo:
        await consolidator.synthesize_instructions(ir, groupings)
    err = excinfo.value
    assert err.group_name == "GreetAgent"
    assert any("<Agent>" in d.message for d in err.diagnostics)
    # I015 (banned legacy tags) is the rule that catches the <Agent> wrapper
    assert any(d.rule_id == "I015" for d in err.diagnostics)


def test_build_validator_feedback_format() -> None:
    feedback = _build_validator_feedback(
        [
            LintResult(
                file="GreetAgent.txt",
                rule_id="I015",
                severity=Severity.ERROR,
                message="Banned legacy XML tag '<Agent>'",
            ),
            LintResult(
                file="GreetAgent.txt",
                rule_id="I001",
                severity=Severity.ERROR,
                message="Missing required XML tag: <role>",
            ),
        ]
    )
    assert "FAILED CANONICAL-XML VALIDATION" in feedback
    assert "[I015]" in feedback
    assert "Banned legacy XML tag '<Agent>'" in feedback
    assert "[I001]" in feedback
    assert "Missing required XML tag: <role>" in feedback
    assert "Emit only the corrected XML." in feedback
