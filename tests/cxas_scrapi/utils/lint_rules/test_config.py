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

"""Unit tests for config lint rules, specifically Rule A011."""

import json
from pathlib import Path

import pytest

from cxas_scrapi.utils.lint_rules.config import (
    AgentMissingInstruction,
    GuidedAgentDefinitionOutOfSync,
)
from cxas_scrapi.utils.linter import LintContext, Severity, build_registry


@pytest.fixture
def context(tmp_path: Path) -> LintContext:
    """Minimal LintContext for rule testing."""
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        all_agent_names={"guided_agent", "normal_agent"},
        all_agent_display_names={"guided agent", "normal agent"},
        all_tool_names={"search_faq", "process_order", "end_session"},
        all_tool_dirs={},
    )


def test_a011_guided_agent_synced(tmp_path: Path, context: LintContext) -> None:
    """Test rule passes when definition.yaml and agent.json are synced."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = (
        "tools:\n"
        "  - process_order\n"
        "  - search_faq\n"
        "rules:\n"
        "  - Verify account first\n"
    )
    (agent_dir / "definition.yaml").write_text(yaml_content)

    agent_data = {
        "displayName": "Guided Support Agent",
        "tools": ["process_order", "search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": yaml_content,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 0


def test_a011_guided_agent_drift_yaml_inline_text(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule triggers error when definition.yaml has drift."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = (
        "tools:\n"
        "  - search_faq\n"
        "rules:\n"
        "  - Verify account first\n"
        "  - New rule in YAML only\n"
    )
    (agent_dir / "definition.yaml").write_text(yaml_content)

    inline_text = "tools:\n  - search_faq\nrules:\n  - Verify account first\n"
    agent_data = {
        "displayName": "Guided Support Agent",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": inline_text,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "A011"
    assert results[0].severity == Severity.ERROR
    assert "out of sync" in results[0].message
    assert "New rule in YAML only" in results[0].message
    assert "cxas agent sync-yaml" in results[0].fix_suggestion


def test_a011_guided_agent_missing_definition_yaml(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule triggers error when definition.yaml is missing."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    agent_data = {
        "displayName": "Guided Support Agent",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": "tools:\n  - search_faq\nrules:\n  - Rule A\n",
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "A011"
    assert results[0].severity == Severity.ERROR
    assert "missing definition.yaml" in results[0].message.lower()
    assert "cxas agent sync-yaml --to-yaml" in results[0].fix_suggestion


def test_a011_guided_agent_tools_diverge(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule triggers error when tool lists diverge."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = (
        "tools:\n  - process_order\n  - search_faq\nrules:\n  - Rule A\n"
    )
    (agent_dir / "definition.yaml").write_text(yaml_content)

    # agent.json only has search_faq, missing process_order
    agent_data = {
        "displayName": "Guided Support Agent",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": yaml_content,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "A011"
    assert results[0].severity == Severity.ERROR
    assert "Tools mismatch" in results[0].message
    assert "cxas agent sync-yaml" in results[0].fix_suggestion


def test_a011_guided_agent_forbidden_instruction_field(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule triggers error when forbidden 'instruction' field exists."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = "tools:\n  - search_faq\nrules:\n  - Rule A\n"
    (agent_dir / "definition.yaml").write_text(yaml_content)

    agent_data = {
        "displayName": "Guided Support Agent",
        "instruction": "Forbidden plain-text instruction field",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": yaml_content,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "A011"
    assert results[0].severity == Severity.ERROR
    assert "forbidden 'instruction'" in results[0].message
    assert "Remove 'instruction'" in results[0].fix_suggestion
    assert "cxas agent sync-yaml" in results[0].fix_suggestion


def test_a011_safely_ignores_non_guided_agent(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule safely ignores non-guided agents without false positives."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "normal_agent"
    agent_dir.mkdir(parents=True)

    (agent_dir / "instruction.txt").write_text("<role>Normal agent</role>")
    agent_data = {
        "displayName": "Standard Normal Agent",
        "tools": ["search_faq"],
    }
    agent_json = agent_dir / "normal_agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 0


def test_a011_ignores_app_json(tmp_path: Path, context: LintContext) -> None:
    """Test rule ignores app.json root file."""
    rule = GuidedAgentDefinitionOutOfSync()
    app_json = tmp_path / "app.json"
    app_json.write_text('{"name": "my_app", "rootAgent": "guided_agent"}')

    results = rule.check(app_json, app_json.read_text(), context)
    assert len(results) == 0


def test_a011_both_drift_and_forbidden_instruction_reported(
    tmp_path: Path, context: LintContext
) -> None:
    """Test rule reports both drift and forbidden instruction."""
    rule = GuidedAgentDefinitionOutOfSync()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = "tools:\n  - search_faq\nrules:\n  - Updated YAML rule\n"
    (agent_dir / "definition.yaml").write_text(yaml_content)

    stale_inline = "tools:\n  - search_faq\nrules:\n  - Stale JSON rule\n"
    agent_data = {
        "displayName": "Guided Support Agent",
        "instruction": "Forbidden plain-text instruction field",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": stale_inline,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 2
    assert all(r.rule_id == "A011" for r in results)
    messages = [r.message for r in results]
    assert any("out of sync" in m for m in messages)
    assert any("forbidden 'instruction'" in m for m in messages)


def test_a011_rule_registry_auto_registered() -> None:
    """Test Rule A011 is auto-registered in the RuleRegistry."""
    registry = build_registry()
    rule = registry.get("A011")
    assert rule is not None
    assert rule.id == "A011"
    assert rule.category == "config"
    assert rule.default_severity == Severity.ERROR
    assert "GuidedAgentDefinitionOutOfSync" in rule.__class__.__name__


def test_a004_exempts_guided_agent(
    tmp_path: Path, context: LintContext
) -> None:
    """Test Rule A004 exempts Guided Agents that use definition.yaml."""
    rule = AgentMissingInstruction()
    agent_dir = tmp_path / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True)

    yaml_content = "tools:\n  - search_faq\nrules:\n  - Rule A\n"
    (agent_dir / "definition.yaml").write_text(yaml_content)

    agent_data = {
        "displayName": "Guided Agent",
        "tools": ["search_faq"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": yaml_content,
            }
        },
    }
    agent_json = agent_dir / "agent.json"
    agent_json.write_text(json.dumps(agent_data, indent=2))

    # Should not flag missing instruction.txt for guided agents
    results = rule.check(agent_json, agent_json.read_text(), context)
    assert len(results) == 0

    # Normal agent without instruction.txt should still be flagged by A004
    normal_dir = tmp_path / "agents" / "normal_agent"
    normal_dir.mkdir(parents=True)
    normal_json = normal_dir / "normal_agent.json"
    normal_json.write_text(json.dumps({"displayName": "Normal Agent"}))

    results_normal = rule.check(normal_json, normal_json.read_text(), context)
    assert len(results_normal) == 1
    assert results_normal[0].rule_id == "A004"
