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

"""Unit tests for structure cross-reference lint rules S009-S012."""

import json
from pathlib import Path

from cxas_scrapi.utils.lint_rules.structure import (
    AgentResourceReferences,
    AppGuardrailReferences,
    ToolFileReferences,
    ToolsetFileReferences,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)


def _make_context(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def test_s009_app_guardrail_and_global_instruction_valid(
    tmp_path: Path,
) -> None:
    """S009 passes when guardrails (by dir or displayName) and globalInstruction exist."""
    gr_dir = tmp_path / "guardrails" / "safety_check"
    gr_dir.mkdir(parents=True)
    (gr_dir / "safety_check.json").write_text(
        json.dumps({"displayName": "Safety Check Guardrail"})
    )
    (tmp_path / "global_instruction.txt").write_text("Be helpful.")

    app_json = tmp_path / "app.json"
    content = json.dumps(
        {
            "displayName": "My App",
            "guardrails": ["safety_check", "Safety Check Guardrail"],
            "globalInstruction": "global_instruction.txt",
        }
    )
    app_json.write_text(content)

    results = AppGuardrailReferences().check(
        app_json, content, _make_context(tmp_path)
    )
    assert results == []


def test_s009_app_missing_guardrail_and_global_instruction(
    tmp_path: Path,
) -> None:
    """S009 flags missing guardrails and missing globalInstruction file."""
    (tmp_path / "guardrails").mkdir()
    app_json = tmp_path / "app.json"
    content = json.dumps(
        {
            "displayName": "My App",
            "guardrails": ["non_existent_guardrail"],
            "globalInstruction": "missing_global_instruction.txt",
        }
    )
    app_json.write_text(content)

    results = AppGuardrailReferences().check(
        app_json, content, _make_context(tmp_path)
    )
    assert len(results) == 2
    assert any("non_existent_guardrail" in r.message for r in results)
    assert any("missing_global_instruction.txt" in r.message for r in results)


def test_s010_agent_resource_references_valid(tmp_path: Path) -> None:
    """S010 passes when agent guardrails, toolsets, and instruction exist."""
    gr_dir = tmp_path / "guardrails" / "prompt_security"
    gr_dir.mkdir(parents=True)
    (gr_dir / "prompt_security.json").write_text(
        json.dumps({"displayName": "Prompt Security"})
    )

    ts_dir = tmp_path / "toolsets" / "billing_api"
    ts_dir.mkdir(parents=True)
    (ts_dir / "billing_api.json").write_text(
        json.dumps({"displayName": "Billing API"})
    )

    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text("<role>Root</role>")

    agent_json = agent_dir / "root_agent.json"
    content = json.dumps(
        {
            "displayName": "root_agent",
            "instruction": "agents/root_agent/instruction.txt",
            "guardrails": ["Prompt Security"],
            "toolsets": [{"toolset": "billing_api", "toolIds": ["get_bill"]}],
        }
    )
    agent_json.write_text(content)

    results = AgentResourceReferences().check(
        agent_json, content, _make_context(tmp_path)
    )
    assert results == []


def test_s010_agent_missing_guardrail_toolset_and_instruction(
    tmp_path: Path,
) -> None:
    """S010 flags missing guardrails, toolsets, and instruction file."""
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    agent_json = agent_dir / "root_agent.json"
    content = json.dumps(
        {
            "displayName": "root_agent",
            "instruction": "agents/root_agent/instruction.txt",
            "guardrails": ["missing_guardrail"],
            "toolsets": [{"toolset": "missing_toolset"}],
        }
    )
    agent_json.write_text(content)

    results = AgentResourceReferences().check(
        agent_json, content, _make_context(tmp_path)
    )
    assert len(results) == 3
    assert any("missing_guardrail" in r.message for r in results)
    assert any("missing_toolset" in r.message for r in results)
    assert any(
        "agents/root_agent/instruction.txt" in r.message for r in results
    )


def test_s011_tool_file_references(tmp_path: Path) -> None:
    """S011 checks pythonCode and openApiSchema file existence on disk."""
    tool_dir = tmp_path / "tools" / "lookup_order"
    tool_dir.mkdir(parents=True)
    tool_json = tool_dir / "lookup_order.json"
    tool_json.write_text(
        json.dumps(
            {
                "displayName": "lookup_order",
                "pythonFunction": {
                    "name": "lookup_order",
                    "pythonCode": "tools/lookup_order/python_function/python_code.py",
                },
            }
        )
    )

    rule = ToolFileReferences()
    ctx = _make_context(tmp_path)
    results = rule.check(tool_dir, "", ctx)
    assert len(results) == 1
    assert (
        "tools/lookup_order/python_function/python_code.py"
        in results[0].message
    )

    py_file = tool_dir / "python_function" / "python_code.py"
    py_file.parent.mkdir(parents=True)
    py_file.write_text("def lookup_order():\n    return {}\n")
    assert rule.check(tool_dir, "", ctx) == []


def test_s012_toolset_file_references_and_prefix(tmp_path: Path) -> None:
    """S012 checks openApiSchema prefix and file existence on disk."""
    ts_dir = tmp_path / "toolsets" / "orders_api"
    ts_dir.mkdir(parents=True)
    ts_json = ts_dir / "orders_api.json"
    ts_json.write_text(
        json.dumps(
            {
                "displayName": "orders_api",
                "openApiToolset": {
                    "openApiSchema": "open_api_toolset/open_api_schema.yaml"
                },
            }
        )
    )

    rule = ToolsetFileReferences()
    ctx = _make_context(tmp_path)
    results = rule.check(ts_dir, "", ctx)
    # Both wrong prefix and missing file
    assert len(results) == 2
    assert any("must be relative to the app root" in r.message for r in results)
    assert any("file not found" in r.message for r in results)

    # Fix prefix and create file
    schema_file = ts_dir / "open_api_toolset" / "open_api_schema.yaml"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("openapi: 3.0.0\npaths: {}\n")
    ts_json.write_text(
        json.dumps(
            {
                "displayName": "orders_api",
                "openApiToolset": {
                    "openApiSchema": (
                        "toolsets/orders_api/open_api_toolset/"
                        "open_api_schema.yaml"
                    )
                },
            }
        )
    )
    assert rule.check(ts_dir, "", ctx) == []


def test_structure_category_dispatches_s009_to_s012(tmp_path: Path) -> None:
    """Running structure category dispatches S009-S012 across targets."""
    (tmp_path / "app.json").write_text(
        json.dumps({"displayName": "App", "guardrails": ["dangling_gr"]})
    )
    registry = build_registry()
    config = LintConfig()
    discovery = Discovery(tmp_path, tmp_path / "evals")
    context = build_context(tmp_path, config, discovery)
    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        categories=["structure"],
    )
    rule_ids = {r.rule_id for r in report.results}
    assert "S009" in rule_ids
