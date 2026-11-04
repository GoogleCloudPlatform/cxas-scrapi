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

from cxas_scrapi.migration import prompts as prompts_module
from cxas_scrapi.migration.prompts import Prompts


def test_step_3b_prompt_has_system_and_template_keys() -> None:
    # The dict carries the core system + template plus optional cached-
    # content variants (cache_shared_template / cache_per_group_template)
    # that the designer uses when a Gemini context cache is available.
    # We only assert the load-bearing keys are present and well-formed;
    # extra cache-variant keys are allowed.
    p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS
    assert {"system", "template"} <= set(p.keys())
    assert isinstance(p["system"], str)
    assert p["system"]
    assert isinstance(p["template"], str)
    assert p["template"]


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


def test_step_3b_system_embeds_canonical_example_content() -> None:
    """The system prompt must include text from the canonical example."""
    sys_p = Prompts.STEP_3B_CONSOLIDATION_INSTRUCTIONS["system"]
    for needle in (
        "You are the Concierge Host",
        '<subtask name="Greeting">',
        '<step name="Welcome">',
        "route_to_support",
    ):
        assert needle in sys_p, (
            f"system prompt missing example snippet: {needle!r}"
        )


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


def test_canonical_example_loader_fallback_runs_without_crash() -> None:
    """The inline fallback is itself valid escaped content; smoke-test it
    by formatting through a tiny .format() template."""
    fallback = prompts_module._CANONICAL_EXAMPLE_FALLBACK
    # Inline fallback uses single braces (it's a raw example).
    escaped = fallback.replace("{", "{{").replace("}", "}}")
    template = "EXAMPLE:\n" + escaped
    out = template.format()  # no placeholders → format is identity
    assert "Concierge Host" in out
    assert "{@TOOL: route_to_support}" in out
