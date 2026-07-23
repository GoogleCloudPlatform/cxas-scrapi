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


"""Tests for the Tier 1 read-only linter check in
:meth:`StructuralConsolidator.synthesize_instructions`."""

from __future__ import annotations

import typing

import pytest

from cxas_scrapi.migration import structural_consolidator as sc_module
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationIR,
)
from cxas_scrapi.migration.structural_consolidator import StructuralConsolidator

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
    returns the xml_response on 2B call."""

    def __init__(
        self,
        gemini_client: typing.Any,
        *,
        xml_response: str,
    ) -> None:
        self.gemini = gemini_client
        self._xml_response = xml_response
        self.calls_2b: list[dict] = []

    async def run_step_2a(self, **kwargs: typing.Any) -> typing.Any:
        return {"role": "stub", "agent_metadata": {}}

    async def run_step_2b_instructions(
        self, **kwargs: typing.Any
    ) -> typing.Any:
        self.calls_2b.append(kwargs)
        return self._xml_response


def _patch_designer_class(
    monkeypatch: typing.Any, *, xml_response: str
) -> typing.Any:
    instances: list[_FakeDesigner] = []

    def factory(gemini_client: typing.Any) -> typing.Any:
        inst = _FakeDesigner(gemini_client, xml_response=xml_response)
        instances.append(inst)
        return inst

    factory.build_2a_shared_context = lambda ir: ("sys-2a", "shared-2a")
    factory.build_2b_shared_context = lambda ir: ("sys-2b", "shared-2b")

    monkeypatch.setattr(sc_module, "AsyncAgentDesigner", factory)
    return instances


def _patch_tree_view(monkeypatch: typing.Any) -> None:
    """Make the combined tree view non-empty so synthesis proceeds."""
    monkeypatch.setattr(
        sc_module,
        "_build_combined_tree_view",
        lambda members, source_data, ir: "stub tree view",
    )


class _FakeGemini:
    async def create_cache(
        self, system_prompt: str, shared_content: str, ttl_seconds: int = 300
    ) -> str | None:
        return None


@pytest.mark.asyncio
async def test_canonical_xml_on_first_call_no_warning(
    monkeypatch: typing.Any,
) -> None:
    instances = _patch_designer_class(monkeypatch, xml_response=CANONICAL_XML)
    _patch_tree_view(monkeypatch)
    ir = _ir()
    consolidator = StructuralConsolidator(ir, gemini_client=_FakeGemini())
    groupings = {"GreetAgent": {"agents": ["GreetAgent"], "is_root": True}}

    statuses = await consolidator.synthesize_instructions(ir, groupings)

    assert statuses == {"GreetAgent": "ok"}
    assert len(instances) == 1
    assert len(instances[0].calls_2b) == 1


@pytest.mark.asyncio
async def test_bad_xml_returns_warning_but_keeps_instructions(
    monkeypatch: typing.Any, caplog: typing.Any
) -> None:
    """Tier 1 Check: A schema failure in Stage 1 must NOT trigger retries.

    It must log a warning, return 'warning', and save the instructions
    to the IR.
    """
    instances = _patch_designer_class(monkeypatch, xml_response=LEGACY_XML)
    _patch_tree_view(monkeypatch)
    ir = _ir()
    consolidator = StructuralConsolidator(ir, gemini_client=_FakeGemini())
    groupings = {"GreetAgent": {"agents": ["GreetAgent"], "is_root": True}}

    with caplog.at_level("WARNING"):
        statuses = await consolidator.synthesize_instructions(ir, groupings)

    assert statuses == {"GreetAgent": "warning"}
    # Instructions are still saved despite the validation failure.
    assert ir.agents["GreetAgent"].instruction.lstrip().startswith("<Agent>")
    # No retries were attempted (only 1 call to 2B)
    assert len(instances[0].calls_2b) == 1
    # Warning was logged, indicating proceeding to Stage 2 optimization.
    assert any(
        "GreetAgent" in rec.getMessage()
        and "Proceeding to Stage 2 optimization" in rec.getMessage()
        for rec in caplog.records
    )
