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

"""Cross-callback AST static linter (C011-C019) and `cxas lint` integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from cxas_scrapi.evals.callback_evals import CallbackChainRunner
from cxas_scrapi.utils.callback_libs import CallbackContext
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)


def test_static_linter_rules_c011_to_c018_and_runtime_diagnostics() -> None:
    """Verifies cross-callback AST rules C011-C019 and runtime aliasing diagnostics."""
    ba1 = (
        "def before_agent_callback(ctx):\n"
        "    shared = {'items': [1, 2]}\n"
        "    ctx.variables['a'] = shared\n"
        "    ctx.variables['b'] = shared\n"
        "    ctx.state['reset_me'] = None\n"
        "    ctx.state['bad_v2_key'] = {123: 'val'}\n"
    )
    ba2 = (
        "def before_agent_callback(ctx):\n"
        "    if 'reset_me' in ctx.state:\n"
        "        pass\n"
        "    val = ctx.state['undeclared_key']\n"
        "    ctx.state['a']['items'].pop()\n"
    )
    bm1 = (
        "def before_model_callback(ctx, llm_request):\n"
        "    llm_request.model = 'gemini-2.5-pro'\n"
        "    return LlmResponse.from_parts([Part.from_text('unconditional short-circuit')])\n"
    )
    bm2 = (
        "def before_model_callback(ctx, llm_request):\n"
        "    ctx.state['unreachable'] = True\n"
    )
    am1 = (
        "def after_model_callback(ctx, llm_response):\n"
        "    llm_response.content.parts.append(Part.from_text('in-place edit'))\n"
        "    return None\n"
    )
    bt1 = (
        "def before_tool_callback(tool, input, ctx):\n"
        "    if input.get('empty'):\n"
        "        return {}\n"
        "    return 'not_a_dict'\n"
    )

    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [("ba_01", ba1), ("ba_02", ba2)],
            "before_model": [("bm_01", bm1), ("bm_02", bm2)],
            "after_model": [("am_01", am1)],
            "before_tool": [("bt_01", bt1)],
        },
        new_session=False,
        image_version="v2",
    )

    findings = runner.lint()
    rule_ids = {f.rule_id for f in findings}
    assert {
        "C011",
        "C012",
        "C013",
        "C014",
        "C015",
        "C016",
        "C017",
        "C018",
    }.issubset(rule_ids)

    aliasing_cb1 = (
        "def before_agent_callback(ctx):\n"
        "    box = {'items': [1, 2]}\n"
        "    ctx.state['k1'] = box\n"
        "    ctx.state['k2'] = box\n"
    )
    aliasing_cb2 = (
        "def before_agent_callback(ctx):\n    ctx.state['k2']['items'].pop()\n"
    )
    alias_runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ac_01", aliasing_cb1), ("ac_02", aliasing_cb2)]},
        new_session=False,
    )
    diff = alias_runner.compare_modes(
        CallbackContext(state={}), stage="before_agent"
    )
    assert diff.has_state_diff is True
    assert any("aliasing" in w for w in diff.combined_trace.warnings)
    explanations = diff.explain()
    assert any("C012" in e and "Aliasing" in e for e in explanations)


def test_lint_customer_false_positive_regressions() -> None:
    """Verifies no false positives on guarded returns, nested functions, and state aliases."""
    noop_bm = "def before_model_callback(callback_context, llm_request):\n    return None\n"
    noop_am = "def after_model_callback(callback_context, llm_response):\n    return None\n"

    l1_early_none = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    if not callback_context.state.get('x'):\n"
        "        return None\n"
        "    return LlmResponse.from_parts([Part.from_text('hi')])\n"
    )
    r_l1a = CallbackChainRunner.from_callbacks(
        {"before_model": [("cb1", l1_early_none), ("cb2", noop_bm)]},
        new_session=False,
    )
    assert r_l1a.lint("before_model") == []

    l1_helper = (
        "def _impl(ctx):\n"
        "    return None\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    return _impl(callback_context)\n"
    )
    r_l1b = CallbackChainRunner.from_callbacks(
        {"before_model": [("cb1", l1_helper), ("cb2", noop_bm)]},
        new_session=False,
    )
    assert r_l1b.lint("before_model") == []

    l3_sanitizer = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    def _helper(x):\n"
        "        return None\n"
        "    parts = llm_response.content.parts\n"
        "    if any(p.text for p in parts) and any(p.function_call for p in parts):\n"
        "        llm_response.content.parts = [p for p in parts if p.function_call]\n"
        "        return llm_response\n"
        "    return None\n"
    )
    r_l3 = CallbackChainRunner.from_callbacks(
        {"after_model": [("cb1", l3_sanitizer), ("cb2", noop_am)]},
        new_session=False,
    )
    assert r_l3.lint("after_model") == []

    l2_nested_tool = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    def _get_dict(v):\n"
        "        return {}\n"
        "    cfg = _get_dict(input)\n"
        "    return None\n"
    )
    r_l2 = CallbackChainRunner.from_callbacks(
        {"before_tool": [("bt1", l2_nested_tool)]}, new_session=False
    )
    assert r_l2.lint("before_tool") == []

    l4_cb1 = (
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    acc = {'a': [1]}\n"
        "    state['k1'] = acc\n"
        "    state['k2'] = acc\n"
        "    state['journeys'] = {}\n"
        "    state['reset'] = None\n"
    )
    l4_cb2 = (
        "def before_agent_callback(callback_context):\n"
        "    if 'reset' in callback_context.state:\n"
        "        pass\n"
        "    x = callback_context.state['journeys']\n"
    )
    l5_bm = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    y = callback_context.state['journeys']\n"
    )
    r_l4 = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [("cb1", l4_cb1), ("cb2", l4_cb2)],
            "before_model": [("bm1", l5_bm)],
        },
        new_session=False,
    )
    findings_l4 = r_l4.lint()
    assert [(f.rule_id, f.callback_name) for f in findings_l4] == [
        ("C014", "cb1"),
        ("C012", "cb1"),
    ]

    l6_cb = (
        "def before_agent_callback(callback_context):\n"
        "    name = 'budget_agent'\n"
        "    callback_context.state['last_agent'] = name\n"
        "    callback_context.state['current_agent'] = name\n"
        "    box = {'a': 1}\n"
        "    callback_context.state['box1'] = box\n"
        "    box = {'b': 2}\n"
        "    callback_context.state['box2'] = box\n"
    )
    r_l6 = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb1", l6_cb)]}, new_session=False
    )
    assert r_l6.lint("before_agent") == []


def test_c014_severity_grading_and_c016_content_alias() -> None:
    """Verifies C014 INFO vs WARNING severity grading and C016 content alias detection."""
    c_reset = (
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state['routing_to'] = None\n"
    )
    c_get_only = (
        "def before_agent_callback(callback_context):\n"
        "    x = callback_context.state.get('routing_to')\n"
    )
    r_benign = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb1", c_reset), ("cb2", c_get_only)]},
        new_session=False,
    )
    assert r_benign.lint("before_agent") == []
    info_findings = r_benign.lint("before_agent", include_info=True)
    assert [(f.rule_id, f.severity) for f in info_findings] == [
        ("C014", "INFO")
    ]

    c_alias_mut = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    content = llm_response.content\n"
        "    if content and content.parts:\n"
        "        content.parts = [Part.from_text('filtered')]\n"
        "    return None\n"
    )
    r_s5 = CallbackChainRunner.from_callbacks(
        {"after_model": [("am1", c_alias_mut)]},
        new_session=False,
    )
    s5_findings = r_s5.lint("after_model")
    assert [(f.rule_id, f.line) for f in s5_findings] == [("C016", 4)]


def test_cxas_lint_integration_c011_to_c018(tmp_path: Path) -> None:
    """Verifies C011-C018 registration in `cxas lint` RuleRegistry and `run_rules` execution."""
    registry = build_registry()
    for rule_id in (
        "C011",
        "C012",
        "C013",
        "C014",
        "C015",
        "C016",
        "C017",
        "C018",
    ):
        r_obj = registry.get(rule_id)
        assert r_obj is not None
        assert r_obj.category == "callbacks"

    app_dir = tmp_path / "lint_app"
    agent_dir = app_dir / "agents" / "demo_agent"
    cb1_dir = agent_dir / "before_agent_callbacks" / "cb_01"
    cb2_dir = agent_dir / "before_agent_callbacks" / "cb_02"
    cb1_dir.mkdir(parents=True)
    cb2_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "lint_app",
                "rootAgent": "demo_agent",
            }
        )
    )
    (agent_dir / "demo_agent.json").write_text(
        json.dumps(
            {
                "displayName": "demo_agent",
                "instruction": "agents/demo_agent/instruction.txt",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/demo_agent/before_agent_callbacks/cb_01/python_code.py"
                        )
                    },
                    {
                        "pythonCode": (
                            "agents/demo_agent/before_agent_callbacks/cb_02/python_code.py"
                        )
                    },
                ],
            }
        )
    )
    (agent_dir / "instruction.txt").write_text("You are a helpful agent.\n")
    (cb1_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    shared = {'a': [1]}\n"
        "    callback_context.state['k1'] = shared\n"
        "    callback_context.state['k2'] = shared\n"
        "    return Content(role='model', parts=[Part.from_text('stop')])\n"
    )
    (cb2_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    x = callback_context.state['missing_var']\n"
        "    return None\n"
    )

    config = LintConfig.load(app_dir)
    discovery = Discovery(app_dir / config.app_dir, app_dir / config.evals_dir)
    context = build_context(app_dir, config, discovery)
    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        categories=["callbacks"],
        specific_rules={"C011", "C012", "C013"},
    )
    reported_ids = {res.rule_id for res in report.results}
    assert reported_ids == {"C011", "C012", "C013"}


def test_c019_load_error_global_mapping_and_lazy_import(
    tmp_path: Path,
) -> None:
    """Verifies C019 load error reporting, global callback mapping, and lazy pytest import."""
    # Importing build_registry in a fresh python process does not import pytest
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from cxas_scrapi.utils.linter import build_registry; "
                "build_registry(); "
                "assert 'pytest' not in sys.modules, 'pytest was eagerly imported'"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    # runner.get_stage_specs / runner.set_callbacks public API
    r_pub = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb1", "def before_agent_callback(ctx): pass\n")]},
        new_session=False,
    )
    specs = r_pub.get_stage_specs("before_agent")
    assert len(specs) == 1
    r_pub.set_callbacks("before_agent", [])
    assert r_pub.get_stage_specs("before_agent") == []

    # Global callback chain context and C019 on broken agent callback path
    app_dir = tmp_path / "global_and_broken_app"
    global_cb_dir = app_dir / "global_before_model_callbacks" / "global_bm_01"
    agent_ok_dir = app_dir / "agents" / "ok_agent"
    agent_bm_dir = agent_ok_dir / "before_model_callbacks" / "agent_bm_01"
    agent_broken_dir = app_dir / "agents" / "broken_agent"
    broken_cb_dir = agent_broken_dir / "before_agent_callbacks" / "broken_cb_01"
    global_cb_dir.mkdir(parents=True)
    agent_bm_dir.mkdir(parents=True)
    broken_cb_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "global_and_broken_app",
                "rootAgent": "ok_agent",
                "globalBeforeModelCallbacks": [
                    {
                        "pythonCode": "global_before_model_callbacks/global_bm_01/python_code.py"
                    }
                ],
            }
        )
    )
    (global_cb_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return LlmResponse.from_parts([Part.from_text('global short-circuit')])\n"
    )
    (agent_ok_dir / "ok_agent.json").write_text(
        json.dumps(
            {
                "displayName": "ok_agent",
                "instruction": "agents/ok_agent/instruction.txt",
                "beforeModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/ok_agent/before_model_callbacks/agent_bm_01/python_code.py"
                        )
                    }
                ],
            }
        )
    )
    (agent_ok_dir / "instruction.txt").write_text("OK agent\n")
    (agent_bm_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )

    # broken_agent references a missing pythonCode file on disk
    (agent_broken_dir / "broken_agent.json").write_text(
        json.dumps(
            {
                "displayName": "broken_agent",
                "instruction": "agents/broken_agent/instruction.txt",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/broken_agent/before_agent_callbacks/missing_dir/python_code.py"
                        )
                    }
                ],
            }
        )
    )
    (agent_broken_dir / "instruction.txt").write_text("Broken agent\n")
    (broken_cb_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n    return None\n"
    )

    registry = build_registry()
    assert registry.get("C019") is not None

    config = LintConfig.load(app_dir)
    discovery = Discovery(app_dir / config.app_dir, app_dir / config.evals_dir)
    context = build_context(app_dir, config, discovery)
    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        categories=["callbacks"],
        specific_rules={"C011", "C019"},
    )
    by_rule = {res.rule_id: res for res in report.results}
    assert "C011" in by_rule
    assert "global_bm_01" in str(by_rule["C011"].file)
    assert "C019" in by_rule
