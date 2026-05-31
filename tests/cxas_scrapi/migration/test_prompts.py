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

"""Snapshot-style tests for the canonical-XML consolidation prompt."""

from __future__ import annotations

from pathlib import Path

from cxas_scrapi.migration import prompts as prompts_module
from cxas_scrapi.migration.prompts import Prompts

REPO_ROOT = Path(__file__).resolve().parents[3]
BELLA = (
    REPO_ROOT / "examples/bella_notte/agents/Bella_Notte_Host/instruction.txt"
)


def test_step_3b_prompt_has_system_and_template_keys() -> None:
    p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS
    assert set(p.keys()) == {"system", "template"}
    assert isinstance(p["system"], str) and p["system"]
    assert isinstance(p["template"], str) and p["template"]


def test_step_3b_system_lists_canonical_tag_vocabulary() -> None:
    sys_p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS["system"]
    for tag in (
        "<role>",
        "<persona>",
        "<primary_goal>",
        "<constraints>",
        "<guidelines>",
        "<taskflow>",
        "<subtask>",
        "<step>",
        "<trigger>",
        "<action>",
    ):
        assert tag in sys_p, f"system prompt missing canonical tag: {tag}"


def test_step_3b_system_forbids_legacy_tags() -> None:
    sys_p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS["system"]
    assert "FORBIDDEN" in sys_p
    for legacy in (
        "<Agent>",
        "<Conversation_Schema>",
        "<state>",
        "<transitions>",
        "<Persona>",
        "<General_Instruction>",
    ):
        assert legacy in sys_p, (
            f"system prompt should call out legacy tag: {legacy}"
        )


def test_step_3b_system_embeds_bella_notte_content() -> None:
    """The system prompt must include text from the canonical example file
    so any drift on the source file shows up here as a snapshot mismatch."""
    sys_p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS["system"]
    bella_text = BELLA.read_text(encoding="utf-8")
    # A characteristic snippet from the canonical example.
    for needle in (
        "You are the Bella Notte Host",
        '<subtask name="Greeting">',
        '<step name="Welcome">',
        # The {{...}} escaping survives — the literal example with single
        # braces is what Gemini reads, so the SOURCE file's single-brace
        # tokens are in the rendered prompt.
        "set_active_flow",
    ):
        assert needle in sys_p, (
            f"system prompt missing example snippet: {needle!r}"
        )
    # And the file content itself isn't empty.
    assert bella_text.strip()


def test_step_3b_template_formats_with_consolidator_keys() -> None:
    """The placeholders the consolidator hands in must all resolve."""
    out = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS["template"].format(
        agent_name="IdentityAuth",
        architecture_blueprint='{"role": "auth"}',
        resource_visualization="(tree view)",
        available_tools="- verify_kba",
        available_groups="- RootAgent [root]",
        self_group="IdentityAuth",
    )
    assert "IdentityAuth" in out
    assert "verify_kba" in out
    assert "{@TOOL:" in out  # literal token survives format()
    assert "{@AGENT:" in out


def test_canonical_example_loader_uses_source_file_when_present() -> None:
    """When the source tree is available, the loader prefers the file."""
    text = prompts_module._load_canonical_example_escaped()
    bella_text = BELLA.read_text(encoding="utf-8")
    # File content (with double-braces) is in the loaded string.
    escaped = bella_text.replace("{", "{{").replace("}", "}}")
    assert escaped == text


def test_canonical_example_loader_fallback_runs_without_crash() -> None:
    """The inline fallback is itself valid escaped content; smoke-test it
    by formatting through a tiny .format() template."""
    fallback = prompts_module._CANONICAL_EXAMPLE_FALLBACK
    # Inline fallback uses single braces (it's a raw example).
    escaped = fallback.replace("{", "{{").replace("}", "}}")
    template = "EXAMPLE:\n" + escaped
    out = template.format()  # no placeholders → format is identity
    assert "Bella Notte" in out
    assert "{@TOOL: set_active_flow}" in out
