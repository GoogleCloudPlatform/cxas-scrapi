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

"""Cross-callback AST static linter (C101-C109) and `cxas lint` integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from cxas_scrapi.utils.linter import LintResult

from cxas_scrapi.evals.callback_chain import CallbackChainRunner
from cxas_scrapi.utils.callback_libs import CallbackContext
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)


def test_static_linter_rules_c101_to_c108_and_runtime_diagnostics() -> None:
    """Verifies cross-callback AST rules C101-C109 and runtime aliasing diagnostics."""
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
        "C101",
        "C102",
        "C103",
        "C104",
        "C105",
        "C106",
        "C107",
        "C108",
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
    assert any("C102" in e and "Aliasing" in e for e in explanations)


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
        ("C104", "cb1"),
        ("C102", "cb1"),
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


def test_c104_severity_grading_and_c106_content_alias() -> None:
    """Verifies C104 INFO vs WARNING severity grading and C106 content alias detection."""
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
        ("C104", "INFO")
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
    assert [(f.rule_id, f.line) for f in s5_findings] == [("C106", 4)]


def test_cxas_lint_integration_c101_to_c108(tmp_path: Path) -> None:
    """Verifies C101-C108 registration in `cxas lint` RuleRegistry and `run_rules` execution."""
    registry = build_registry()
    for rule_id in (
        "C101",
        "C102",
        "C103",
        "C104",
        "C105",
        "C106",
        "C107",
        "C108",
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
        specific_rules={"C101", "C102", "C103"},
    )
    reported_ids = {res.rule_id for res in report.results}
    assert reported_ids == {"C101", "C102", "C103"}


def test_c109_load_error_global_mapping_and_lazy_import(
    tmp_path: Path,
) -> None:
    """Verifies C109 load error reporting, global callback mapping, and lazy pytest import."""
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

    # Global callback chain context and C109 on broken agent callback path
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
    assert registry.get("C109") is not None

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
        specific_rules={"C101", "C109"},
    )
    by_rule = {res.rule_id: res for res in report.results}
    assert "C101" in by_rule
    assert "global_bm_01" in str(by_rule["C101"].file)
    assert "C109" in by_rule

    # R3-5: V001 schema validation emits WARNING (not ERROR) for preview global callback fields in app.json
    report_v001 = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report_v001,
        categories=["schema"],
        specific_rules={"V001"},
    )
    assert len(report_v001.results) == 1
    assert report_v001.results[0].rule_id == "V001"
    assert report_v001.results[0].severity.value == "warning"
    assert "globalBeforeModelCallbacks" in report_v001.results[0].message
    assert "Preview field" in report_v001.results[0].message


def test_linter_severity_preservation_for_a009_e012_and_explicit_config_override(
    tmp_path: Path,
) -> None:
    """Verifies R2-8 (A009/E012 explicit WARNING findings preserved) and R2-10 (config rules override)."""
    from cxas_scrapi.utils.linter import (  # noqa: PLC0415
        Discovery,
        LintConfig,
        LintReport,
        Severity,
        build_context,
        build_registry,
        run_rules,
    )

    app_dir = tmp_path / "sev_app"
    ag_dir = app_dir / "agents" / "root_agent"
    cb_dir = ag_dir / "before_tool_callbacks" / "cb1"
    evals_dir = app_dir / "evals"
    cb_dir.mkdir(parents=True)
    evals_dir.mkdir(parents=True)

    # A009: Accent mismatch emits explicit Severity.WARNING while missing voice emits default Severity.ERROR
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "sev_app",
                "rootAgent": "root_agent",
                "modelSettings": {"model": "gemini-composite-v1"},
                "languageSettings": {
                    "defaultLanguageCode": "en-US",
                    "supportedLanguageCodes": ["es-US"],
                },
                "audioProcessingConfig": {
                    "synthesizeSpeechConfigs": {
                        "en-US": {
                            "voice": "en-US-Chirp3-HD-Aoede",
                            "instruction": "# Director's Notes\nAccent: British English\n",
                        },
                        "es-US": {
                            "voice": "",
                            "instruction": "# Director's Notes\nAccent: US Spanish\n",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (ag_dir / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "instruction": "agents/root_agent/instruction.txt",
                "beforeToolCallbacks": [
                    {
                        "pythonCode": "agents/root_agent/before_tool_callbacks/cb1/python_code.py"
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ag_dir / "instruction.txt").write_text("Hello\n", encoding="utf-8")
    (cb_dir / "python_code.py").write_text(
        "def before_tool_callback(tool, input, callback_context):\n"
        "    return 'not-a-dict'\n",
        encoding="utf-8",
    )

    # E012: Unknown key in shadow eval emits explicit Severity.WARNING
    shadows_dir = evals_dir / "shadows"
    shadows_dir.mkdir(parents=True)
    (shadows_dir / "shadow_test.yaml").write_text(
        "config:\n"
        "  replay_mode: hybrid\n"
        "  unknown_top_key: 123\n"
        "shadow_evals:\n"
        "  - name: c1\n"
        "    conversation_id: conv-1\n"
        "    expectations:\n"
        "      - user said hello\n",
        encoding="utf-8",
    )

    registry = build_registry()
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
        specific_rules={"A009", "E012", "C105"},
    )

    a009_results = [r for r in report.results if r.rule_id == "A009"]
    assert any(
        r.severity == Severity.WARNING and "Accent:" in r.message
        for r in a009_results
    )
    assert any(
        r.severity == Severity.ERROR and "missing a voice" in r.message
        for r in a009_results
    )

    e012_results = [r for r in report.results if r.rule_id == "E012"]
    assert len(e012_results) == 1
    assert e012_results[0].severity == Severity.WARNING
    assert "Unknown key 'unknown_top_key'" in e012_results[0].message

    c105_results = [r for r in report.results if r.rule_id == "C105"]
    assert len(c105_results) == 1
    assert c105_results[0].severity == Severity.ERROR

    # R2-10: Explicit config override `C105: warning` in cxaslint.yaml overrides C105 to WARNING
    (app_dir / "cxaslint.yaml").write_text(
        "rules:\n  C105: warning\n",
        encoding="utf-8",
    )
    config_override = LintConfig.load(app_dir)
    context_override = build_context(app_dir, config_override, discovery)
    report_override = LintReport()
    run_rules(
        registry,
        config_override,
        context_override,
        discovery,
        report_override,
        specific_rules={"C105"},
    )
    c105_overridden = [
        r for r in report_override.results if r.rule_id == "C105"
    ]
    assert len(c105_overridden) == 1
    assert c105_overridden[0].severity == Severity.WARNING


def test_discovery_combines_flat_callbacks_global_callbacks_and_limit_agents(
    tmp_path: Path,
) -> None:
    """Verifies N1: Discovery.discover_callbacks supports flat callback dirs (#526), global callbacks, seen_paths deduplication, and limit_agents together."""
    from cxas_scrapi.utils.linter import Discovery  # noqa: PLC0415

    app_dir = tmp_path / "combined_discovery_app"
    # Agent A: nested callback + flat callback directory
    ag_a_nested = (
        app_dir / "agents" / "agent_a" / "before_model_callbacks" / "cb_nested"
    )
    ag_a_flat = app_dir / "agents" / "agent_a" / "before_tool_callbacks_01"
    # Agent B: flat callback directory (should be excluded when limit_agents=["agent_a"])
    ag_b_flat = app_dir / "agents" / "agent_b" / "after_model_callbacks_01"
    # Global callback directory
    global_bm = app_dir / "global_before_model_callbacks" / "global_cb_01"

    for d in (ag_a_nested, ag_a_flat, ag_b_flat, global_bm):
        d.mkdir(parents=True)
        (d / "python_code.py").write_text("# callback\n", encoding="utf-8")
    (app_dir / "app.json").write_text(
        '{"rootAgent": "agent_a"}\n', encoding="utf-8"
    )

    # 1. Without limit_agents: discovers agent_a (nested + flat), agent_b (flat), and __global__
    disc_all = Discovery(app_dir, app_dir / "evals")
    found_all = [
        (agent_name, cb_type, cb_name)
        for agent_name, cb_type, cb_name, _ in disc_all.discover_callbacks()
    ]
    assert found_all == [
        ("agent_a", "before_model_callbacks", "cb_nested"),
        ("agent_a", "before_tool_callbacks", "before_tool_callbacks_01"),
        ("agent_b", "after_model_callbacks", "after_model_callbacks_01"),
        ("__global__", "global_before_model_callbacks", "global_cb_01"),
    ]

    # 2. With limit_agents=["agent_a"]: filters out agent_b while keeping agent_a (nested + flat) and __global__
    disc_limited = Discovery(
        app_dir, app_dir / "evals", limit_agents=["agent_a"]
    )
    found_limited = [
        (agent_name, cb_type, cb_name)
        for agent_name, cb_type, cb_name, _ in disc_limited.discover_callbacks()
    ]
    assert found_limited == [
        ("agent_a", "before_model_callbacks", "cb_nested"),
        ("agent_a", "before_tool_callbacks", "before_tool_callbacks_01"),
        ("__global__", "global_before_model_callbacks", "global_cb_01"),
    ]


def test_pr548_review_lint_fixes_c103_to_c109_and_runtime_traps_dedup(
    tmp_path: Path,
) -> None:
    """Verifies PR #548 review fixes across C103-C109 and C011/C012 deduplication."""
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackSpec,
        lint_stage_chain,
    )

    # 1. C103 statement-order read-before-write & AugAssign (`state['counter'] += 1`)
    c103_code = (
        "def before_agent_callback(ctx):\n"
        "    val = ctx.state['read_first']\n"
        "    ctx.state['read_first'] = val + 1\n"
        "    ctx.state['counter'] += 1\n"
        "    after_write = ctx.state['read_first']\n"
    )
    f_c103 = lint_stage_chain(
        "before_agent",
        [CallbackSpec(name="cb1", stage="before_agent", python_code=c103_code)],
    )
    c103_lines = [f.line for f in f_c103 if f.rule_id == "C103"]
    assert c103_lines == [2, 4]

    # 2. C106 catches `if skip: return None` before `return llm_response` and isolates sibling alias branches
    c106_early_none = (
        "def after_model_callback(ctx, llm_response):\n"
        "    llm_response.content.parts.append(Part.from_text('x'))\n"
        "    if ctx.state.get('skip'):\n"
        "        return None\n"
        "    return llm_response\n"
    )
    f_c106 = lint_stage_chain(
        "after_model",
        [
            CallbackSpec(
                name="am1", stage="after_model", python_code=c106_early_none
            )
        ],
    )
    assert [(f.rule_id, f.line) for f in f_c106] == [("C106", 2)]

    # 3. C107 AugAssign on direct `llm_request.model += ...` reports `llm_request.model` (not `.config.model`)
    c107_aug = (
        "def before_model_callback(ctx, llm_request):\n"
        "    llm_request.model += '-v2'\n"
        "    return None\n"
    )
    f_c107 = lint_stage_chain(
        "before_model",
        [CallbackSpec(name="bm1", stage="before_model", python_code=c107_aug)],
    )
    assert len(f_c107) == 1
    assert "`llm_request.model`" in f_c107[0].message
    assert "`llm_request.config.model`" not in f_c107[0].message

    # 4. C108 nested non-str dict var (`{"nested": d}`) and `state.update({"a": {1: "x"}})` & `state.update(foo=None)`
    c108_upd = (
        "def before_agent_callback(ctx):\n"
        "    d = {1: 'bad'}\n"
        "    ctx.state['wrapped'] = {'nested': d}\n"
        "    ctx.state.update({'a': {2: 'bad2'}})\n"
        "    ctx.state.update(foo=None)\n"
    )
    f_c108 = lint_stage_chain(
        "before_agent",
        [CallbackSpec(name="cb_u", stage="before_agent", python_code=c108_upd)],
        image_version="v2",
        include_info=True,
    )
    assert [f.rule_id for f in f_c108].count("C108") == 2
    assert any(f.rule_id == "C104" and "foo" in f.message for f in f_c108)

    # 5. cxas lint integration:
    #    - C011/C104 and C012/C106 deduplication
    #    - Same-line multiple findings (`state['undef_a'] or state['undef_b']`)
    #    - Resilient C109 on `<agent>.json` while still linting valid sibling callbacks
    app_dir = tmp_path / "dedup_and_resilient_app"
    ag_dir = app_dir / "agents" / "test_agent"
    ba1_dir = ag_dir / "before_agent_callbacks" / "ba_01"
    am1_dir = ag_dir / "after_model_callbacks" / "am_01"
    ba1_dir.mkdir(parents=True)
    am1_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "app", "rootAgent": "test_agent"}),
        encoding="utf-8",
    )
    # test_agent.json has one missing pythonCode AND valid ba_01 + am_01 callbacks
    (ag_dir / "test_agent.json").write_text(
        json.dumps(
            {
                "displayName": "test_agent",
                "instruction": "agents/test_agent/instruction.txt",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": "agents/test_agent/before_agent_callbacks/missing_cb/python_code.py"
                    },
                    {
                        "pythonCode": "agents/test_agent/before_agent_callbacks/ba_01/python_code.py"
                    },
                ],
                "afterModelCallbacks": [
                    {
                        "pythonCode": "agents/test_agent/after_model_callbacks/am_01/python_code.py"
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (ag_dir / "instruction.txt").write_text("Test\n", encoding="utf-8")
    (ba1_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['solo_none'] = None\n"
        "    if 'solo_none' in callback_context.state:\n"
        "        pass\n"
        "    _ = callback_context.state['undef_a'] or callback_context.state['undef_b']\n"
        "    return None\n",
        encoding="utf-8",
    )
    (am1_dir / "python_code.py").write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content.parts = []\n"
        "    return None\n",
        encoding="utf-8",
    )

    registry = build_registry()
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
        specific_rules={"C011", "C012", "C103", "C104", "C106", "C109"},
    )
    rule_ids = [r.rule_id for r in report.results]
    # Single-callback None is reported by C011 without duplicate C104
    assert "C011" in rule_ids
    assert "C104" not in rule_ids
    # Single-callback unreturned after_model mutation is reported by C012 (ERROR) without duplicate C106 (WARNING)
    assert "C012" in rule_ids
    assert "C106" not in rule_ids
    # Both undef_a and undef_b on the same line are reported by C103
    c103_res = [r for r in report.results if r.rule_id == "C103"]
    assert len(c103_res) == 2
    # C109 is reported on test_agent.json AND valid sibling callbacks (ba_01) were still linted!
    c109_res = [r for r in report.results if r.rule_id == "C109"]
    assert len(c109_res) == 1
    assert c109_res[0].file.endswith("test_agent.json")


def test_part1_dual_review_lint_regressions(tmp_path: Path) -> None:
    """Verifies Part 1 lint fixes from Opus & Carbon reviews:
    - C104/C106 still fire when `--rule C104,C106` is used or C011/C012 are disabled (`rules` / `per_file`)
    - C106 partial-branch `return None` still fires even when C012 flags an unrelated line in the same file
    - C103 catches trailing `.get()` after earlier unguarded `state['k']`, supports tuple unpacking,
      catches `variableDeclarations` without `default`, and deduplicates against V100 on undeclared keys
    - Flat `pythonCode` cross-callback C104 is NOT suppressed by callback name collision
    - `lint_app_dir()` resiliently emits C109 on `<agent>.json` while linting valid sibling callbacks
    """
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackSpec,
        lint_app_dir,
        lint_stage_chain,
    )
    from cxas_scrapi.utils.linter import Severity  # noqa: PLC0415

    # 1. C103: trailing `.get()` does NOT silence earlier `state['crash_key']`;
    #    tuple unpacking `state['a'], state['b'] = 1, 2` is recognized;
    #    declared variable without default is flagged by C103!
    c103_order_code = (
        "def before_agent_callback(ctx):\n"
        "    bad = ctx.state['crash_key']\n"
        "    later = ctx.state.get('crash_key')\n"
        "    ctx.state['a'], ctx.state['b'] = 1, 2\n"
        "    ok_tuple = ctx.state['a'] + ctx.state['b']\n"
        "    no_def = ctx.state['declared_without_default']\n"
        "    has_def = ctx.state['declared_with_default']\n"
    )
    f_order = lint_stage_chain(
        "before_agent",
        [
            CallbackSpec(
                name="cb_ord", stage="before_agent", python_code=c103_order_code
            )
        ],
        app_config={
            "variableDeclarations": [
                {
                    "name": "declared_without_default",
                    "schema": {"type": "STRING"},
                },
                {
                    "name": "declared_with_default",
                    "schema": {"type": "STRING", "default": "ok"},
                },
            ]
        },
    )
    c103_lines = [f.line for f in f_order if f.rule_id == "C103"]
    assert c103_lines == [2, 6]

    # 2. C108 on `state.setdefault("k", {1: "bad"})`
    f_sd = lint_stage_chain(
        "before_agent",
        [
            CallbackSpec(
                name="cb_sd",
                stage="before_agent",
                python_code=(
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state.setdefault('k', {1: 'bad'})\n"
                ),
            )
        ],
        image_version="v2",
    )
    assert [f.rule_id for f in f_sd] == ["C108"]

    # 3. Real `cxas lint` companion-rule matrix + flat callbacks + resilient `lint_app_dir`
    app_dir = tmp_path / "companion_matrix_app"
    ag_dir = app_dir / "agents" / "main_ag"
    flat_dir = ag_dir / "callbacks"
    am_dir = ag_dir / "after_model_callbacks" / "am_mix"
    flat_dir.mkdir(parents=True)
    am_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "app",
                "rootAgent": "main_ag",
                "variableDeclarations": [
                    {"name": "no_def_var", "schema": {"type": "STRING"}},
                    {
                        "name": "token",
                        "schema": {"type": "STRING", "default": "x"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ag_dir / "instruction.txt").write_text("Hello\n", encoding="utf-8")
    (flat_dir / "step_one.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['token'] = None\n"
        "    _ = callback_context.state['no_def_var']\n"
        "    _ = callback_context.state['undeclared_xyz']\n",
        encoding="utf-8",
    )
    (flat_dir / "step_two.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    if 'token' in callback_context.state:\n"
        "        pass\n",
        encoding="utf-8",
    )
    # Mixed after_model: standalone LlmResponse(...) on line 2 (flagged by C012)
    # PLUS partial-branch `return None` mutation on line 3 (must STILL be flagged by C106!)
    (am_dir / "python_code.py").write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    LlmResponse.from_parts([Part.from_text('unused')])\n"
        "    llm_response.content.parts.append(Part.from_text('mut'))\n"
        "    if callback_context.state.get('token'):\n"
        "        return None\n"
        "    return llm_response\n",
        encoding="utf-8",
    )
    (ag_dir / "main_ag.json").write_text(
        json.dumps(
            {
                "displayName": "main_ag",
                "instruction": "agents/main_ag/instruction.txt",
                "beforeAgentCallbacks": [
                    {"pythonCode": "agents/main_ag/callbacks/missing.py"},
                    {"pythonCode": "agents/main_ag/callbacks/step_one.py"},
                    {"pythonCode": "agents/main_ag/callbacks/step_two.py"},
                ],
                "afterModelCallbacks": [
                    {
                        "pythonCode": "agents/main_ag/after_model_callbacks/am_mix/python_code.py"
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 3a. Resilient `lint_app_dir()` reports C109 on `main_ag.json` AND still finds C104/C103/C106
    app_findings = lint_app_dir(app_dir)
    app_rule_ids = {f.rule_id for f in app_findings}
    assert {"C109", "C104", "C103", "C106"}.issubset(app_rule_ids)
    c109_app = [f for f in app_findings if f.rule_id == "C109"]
    assert len(c109_app) == 1
    assert c109_app[0].stage == "before_agent"
    assert c109_app[0].path is not None
    assert c109_app[0].path.endswith("main_ag.json")

    registry = build_registry()
    discovery = Discovery(app_dir, app_dir / "evals")

    # 3b. Default full `cxas lint`:
    #     - Flat cross-callback `token = None` emits C104 (not swallowed by `'callbacks'` name collision)
    #     - Mixed after_model emits BOTH C012@2 and C106@3
    #     - `no_def_var` emits C103@3, while `undeclared_xyz` is reported by V100@4 without duplicate C103@4
    cfg_default = LintConfig.load(app_dir)
    ctx_default = build_context(app_dir, cfg_default, discovery)
    rep_default = LintReport()
    run_rules(registry, cfg_default, ctx_default, discovery, rep_default)
    assert any(r.rule_id == "C104" and r.line == 2 for r in rep_default.results)
    assert any(r.rule_id == "C012" and r.line == 2 for r in rep_default.results)
    assert any(r.rule_id == "C106" and r.line == 3 for r in rep_default.results)
    assert any(r.rule_id == "C103" and r.line == 3 for r in rep_default.results)
    assert any(r.rule_id == "V100" and r.line == 4 for r in rep_default.results)
    assert not any(
        r.rule_id == "C103" and r.line == 4 for r in rep_default.results
    )

    # 3c. Explicit `--rule C104,C106,C103` (without C011/C012/V100) still emits C104, C106, and BOTH C103s!
    solo_dir = tmp_path / "solo_app"
    solo_cb = solo_dir / "agents" / "ag" / "after_model_callbacks" / "am1"
    solo_ba = solo_dir / "agents" / "ag" / "before_agent_callbacks" / "ba1"
    solo_cb.mkdir(parents=True)
    solo_ba.mkdir(parents=True)
    (solo_dir / "app.json").write_text(
        json.dumps({"displayName": "s", "rootAgent": "ag"}), encoding="utf-8"
    )
    (solo_dir / "agents" / "ag" / "instruction.txt").write_text(
        "Hi\n", encoding="utf-8"
    )
    (solo_dir / "agents" / "ag" / "ag.json").write_text(
        json.dumps(
            {
                "displayName": "ag",
                "instruction": "agents/ag/instruction.txt",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": "agents/ag/before_agent_callbacks/ba1/python_code.py"
                    }
                ],
                "afterModelCallbacks": [
                    {
                        "pythonCode": "agents/ag/after_model_callbacks/am1/python_code.py"
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (solo_ba / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n"
        "    ctx.state['solo'] = None\n"
        "    if 'solo' in ctx.state:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (solo_cb / "python_code.py").write_text(
        "def after_model_callback(ctx, llm_response):\n"
        "    llm_response.content.parts = []\n"
        "    return None\n",
        encoding="utf-8",
    )
    solo_disc = Discovery(solo_dir, solo_dir / "evals")
    cfg_solo = LintConfig.load(solo_dir)
    ctx_solo = build_context(solo_dir, cfg_solo, solo_disc)
    rep_only = LintReport()
    run_rules(
        registry,
        cfg_solo,
        ctx_solo,
        solo_disc,
        rep_only,
        specific_rules={"C104", "C106"},
    )
    assert {r.rule_id for r in rep_only.results} == {"C104", "C106"}

    # 3d. Turning off C011 & C012 globally (`Severity.OFF`) or via `per_file` lets C104 & C106 take over!
    cfg_off = LintConfig.load(solo_dir)
    cfg_off.rules["C011"] = Severity.OFF
    cfg_off.rules["C012"] = Severity.OFF
    ctx_off = build_context(solo_dir, cfg_off, solo_disc)
    rep_off = LintReport()
    run_rules(
        registry, cfg_off, ctx_off, solo_disc, rep_off, categories=["callbacks"]
    )
    assert {"C104", "C106"}.issubset({r.rule_id for r in rep_off.results})

    cfg_pf = LintConfig.load(solo_dir)
    cfg_pf.per_file["agents/ag/**"] = {"C011": "off", "C012": "off"}
    ctx_pf = build_context(solo_dir, cfg_pf, solo_disc)
    rep_pf = LintReport()
    run_rules(
        registry, cfg_pf, ctx_pf, solo_disc, rep_pf, categories=["callbacks"]
    )
    assert {"C104", "C106"}.issubset({r.rule_id for r in rep_pf.results})

    # 4. C104 <-> C011 line-level dedup: setdefault/update(kw)/set_variable still emit C104 when C011 is active
    c104_dir = tmp_path / "c104_line_dedup"
    c104_ba = c104_dir / "agents" / "ag" / "before_agent_callbacks" / "ba1"
    c104_ba.mkdir(parents=True)
    (c104_dir / "app.json").write_text(
        json.dumps({"displayName": "c104", "rootAgent": "ag"}), encoding="utf-8"
    )
    (c104_dir / "agents" / "ag" / "ag.json").write_text(
        json.dumps(
            {
                "displayName": "ag",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": "agents/ag/before_agent_callbacks/ba1/python_code.py"
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (c104_ba / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n"
        "    ctx.state['direct'] = None\n"
        "    ctx.state.setdefault('sd', None)\n"
        "    ctx.state.update(kw_none=None)\n"
        "    ctx.set_variable('sv', None)\n"
        "    if ('direct' in ctx.state or 'sd' in ctx.state or 'kw_none' in ctx.state or 'sv' in ctx.state):\n"
        "        pass\n",
        encoding="utf-8",
    )
    c104_disc = Discovery(c104_dir, c104_dir / "evals")
    cfg_c104 = LintConfig.load(c104_dir)
    ctx_c104 = build_context(c104_dir, cfg_c104, c104_disc)
    rep_c104 = LintReport()
    run_rules(
        registry,
        cfg_c104,
        ctx_c104,
        c104_disc,
        rep_c104,
        categories=["callbacks"],
    )
    c011_lines = {r.line for r in rep_c104.results if r.rule_id == "C011"}
    c104_lines = {r.line for r in rep_c104.results if r.rule_id == "C104"}
    assert c011_lines == {2}
    assert c104_lines == {3, 4, 5}

    # 5. Discovery.discover_callbacks collision-free paths (`cb_a/python_code/code.py` vs `cb_b/python_code/code.py`)
    #    and out-of-app pythonCode containment
    outside_py = tmp_path / "outside_escape.py"
    outside_py.write_text(
        "def before_agent_callback(ctx):\n    ctx.state['x'] = None\n",
        encoding="utf-8",
    )
    disc_app = tmp_path / "disc_collision_app"
    cb_a_file = disc_app / "agents" / "ag" / "cb_a" / "python_code" / "code.py"
    cb_b_file = disc_app / "agents" / "ag" / "cb_b" / "python_code" / "code.py"
    cb_a_file.parent.mkdir(parents=True)
    cb_b_file.parent.mkdir(parents=True)
    cb_a_file.write_text(
        "def before_agent_callback(ctx):\n    ctx.state['a'] = None\n",
        encoding="utf-8",
    )
    cb_b_file.write_text(
        "def before_agent_callback(ctx):\n    ctx.state['b'] = None\n",
        encoding="utf-8",
    )
    (disc_app / "app.json").write_text(
        json.dumps({"displayName": "disc", "rootAgent": "ag"}), encoding="utf-8"
    )
    (disc_app / "agents" / "ag" / "ag.json").write_text(
        json.dumps(
            {
                "displayName": "ag",
                "beforeAgentCallbacks": [
                    {"pythonCode": "agents/ag/cb_a/python_code/code.py"},
                    {"pythonCode": "agents/ag/cb_b/python_code/code.py"},
                    {"pythonCode": str(outside_py)},
                    {"pythonCode": "../outside_escape.py"},
                ],
            }
        ),
        encoding="utf-8",
    )
    d_col = Discovery(disc_app, disc_app / "evals")
    cfg_col = LintConfig.load(disc_app)
    ctx_col = build_context(disc_app, cfg_col, d_col)
    rep_col = LintReport()
    run_rules(
        registry, cfg_col, ctx_col, d_col, rep_col, specific_rules={"C011"}
    )
    linted_files = {r.file for r in rep_col.results if r.rule_id == "C011"}
    assert linted_files == {
        "agents/ag/cb_a/python_code/code.py",
        "agents/ag/cb_b/python_code/code.py",
    }

    # 6. `lint_app_dir` handles non-UTF-8 fallback callback files without crashing & skips orphan dirs when <agent>.json exists
    enc_app = tmp_path / "enc_resilient_app"
    ag1_orphan = (
        enc_app
        / "agents"
        / "ag_with_json"
        / "before_agent_callbacks"
        / "orphan"
    )
    ag2_no_json = (
        enc_app / "agents" / "ag_no_json" / "before_agent_callbacks" / "bad_enc"
    )
    ag1_orphan.mkdir(parents=True)
    ag2_no_json.mkdir(parents=True)
    (enc_app / "app.json").write_text(
        json.dumps({"displayName": "enc", "rootAgent": "ag_with_json"}),
        encoding="utf-8",
    )
    (enc_app / "agents" / "ag_with_json" / "ag_with_json.json").write_text(
        json.dumps({"displayName": "ag_with_json"}), encoding="utf-8"
    )
    (ag1_orphan / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n    _ = ctx.state['orphan_unseeded']\n",
        encoding="utf-8",
    )
    (ag2_no_json / "python_code.py").write_bytes(b"\xff\xfeinvalid_utf8")
    enc_findings = lint_app_dir(enc_app)
    assert not any(f.callback_name == "orphan" for f in enc_findings)
    assert any(
        f.rule_id == "C109" and f.callback_name == "bad_enc"
        for f in enc_findings
    )


def _run_cxas_lint_callbacks(
    app_dir: Path,
    specific_rules: set[str],
    limit_agents: set[str] | None = None,
) -> list[LintResult]:
    config = LintConfig.load(app_dir)
    discovery = Discovery(
        app_dir / config.app_dir,
        app_dir / config.evals_dir,
        limit_agents=limit_agents,
    )
    context = build_context(app_dir, config, discovery)
    report = LintReport()
    run_rules(
        build_registry(),
        config,
        context,
        discovery,
        report,
        categories=["callbacks"],
        specific_rules=specific_rules,
    )
    return list(report.results)


def test_pr548_lint_python_code_containment_c109(tmp_path: Path) -> None:
    """Verifies `pythonCode` paths escaping `app_root` are never read and emit
    C109 in both `lint_app_dir()` and `cxas lint`.
    """
    from cxas_scrapi.evals.callback_chain.lint import (  # noqa: PLC0415
        lint_app_dir,
        load_agent_lint_specs_resilient,
    )

    outside = tmp_path / "outside.py"
    outside.write_text(
        "def before_agent_callback(ctx):\n"
        "    return Content(role='model', parts=[Part.from_text('x')])\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    ag_dir = app_dir / "agents" / "demo_agent"
    ag_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "app",
                "rootAgent": "demo_agent",
                "globalBeforeModelCallbacks": [{"pythonCode": str(outside)}],
            }
        ),
        encoding="utf-8",
    )
    (ag_dir / "demo_agent.json").write_text(
        json.dumps(
            {
                "displayName": "demo_agent",
                "beforeAgentCallbacks": [{"pythonCode": "../outside.py"}],
            }
        ),
        encoding="utf-8",
    )

    _, _, stage_specs, c109 = load_agent_lint_specs_resilient(ag_dir, app_dir)
    assert stage_specs["before_agent"] == []
    assert stage_specs["before_model"] == []
    assert len(c109) == 2  # noqa: PLR2004
    assert all("not allowed" in f.message for f in c109)
    assert {f.stage for f in c109} == {"before_agent", "before_model"}

    findings = lint_app_dir(app_dir)
    assert {f.rule_id for f in findings} == {"C109"}
    assert not any(f.rule_id == "C101" for f in findings)

    results = _run_cxas_lint_callbacks(app_dir, {"C101", "C109"})
    c109_results = [r for r in results if r.rule_id == "C109"]
    assert len(c109_results) == 2  # noqa: PLR2004
    assert not [r for r in results if r.rule_id == "C101"]
    assert all(str(outside) not in r.file for r in results)

    # Missing escaping paths also get the escape note (never stat()-ed).
    (ag_dir / "demo_agent.json").write_text(
        json.dumps(
            {
                "displayName": "demo_agent",
                "beforeAgentCallbacks": [{"pythonCode": "../../missing.py"}],
                "afterAgentCallbacks": [{"pythonCode": "agents/nope.py"}],
            }
        ),
        encoding="utf-8",
    )
    _, _, _, c109 = load_agent_lint_specs_resilient(ag_dir, app_dir)
    by_stage = {f.stage: f.message for f in c109}
    assert "not allowed" in by_stage["before_agent"]
    assert "not allowed" not in by_stage["after_agent"]
    assert "file not found" in by_stage["after_agent"]


def test_pr548_lint_app_yaml_c103_matches_v100(tmp_path: Path) -> None:
    """Verifies C103 honors `app.yaml` variable defaults like V100."""
    from cxas_scrapi.evals.callback_chain.lint import (  # noqa: PLC0415
        lint_app_dir,
    )

    app_dir = tmp_path / "yaml_app"
    cb_dir = app_dir / "agents" / "demo_agent" / "before_agent_callbacks"
    (cb_dir / "cb_01").mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        "displayName: yaml_app\n"
        "rootAgent: demo_agent\n"
        "variableDeclarations:\n"
        "  - name: greeting\n"
        "    schema:\n"
        "      type: STRING\n"
        "      default: hello\n",
        encoding="utf-8",
    )
    (app_dir / "agents" / "demo_agent" / "demo_agent.json").write_text(
        json.dumps(
            {
                "displayName": "demo_agent",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/demo_agent/before_agent_callbacks/"
                            "cb_01/python_code.py"
                        )
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (cb_dir / "cb_01" / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    g = callback_context.state['greeting']\n"
        "    return None\n",
        encoding="utf-8",
    )
    assert [f for f in lint_app_dir(app_dir) if f.rule_id == "C103"] == []
    results = _run_cxas_lint_callbacks(app_dir, {"C103", "V100"})
    assert [r for r in results if r.rule_id in {"C103", "V100"}] == []

    (cb_dir / "cb_01" / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    g = callback_context.state['undeclared_key']\n"
        "    return None\n",
        encoding="utf-8",
    )
    assert [f.rule_id for f in lint_app_dir(app_dir)] == ["C103"]
    results = _run_cxas_lint_callbacks(app_dir, {"C103"})
    assert [r.rule_id for r in results] == ["C103"]


def test_pr548_cxas_lint_surfaces_wrong_named_agent_json_c109(
    tmp_path: Path,
) -> None:
    """Verifies `cxas lint` reports exactly one C109 for `<agent>/<wrong>.json`
    (with or without visited callback files in that agent).
    """
    app_dir = tmp_path / "app"
    (app_dir / "agents").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "app", "rootAgent": "good_agent"}),
        encoding="utf-8",
    )
    good = app_dir / "agents" / "good_agent"
    (good / "before_agent_callbacks" / "cb_01").mkdir(parents=True)
    (good / "good_agent.json").write_text(
        json.dumps({"displayName": "good_agent"}), encoding="utf-8"
    )
    (good / "before_agent_callbacks" / "cb_01" / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n    return None\n",
        encoding="utf-8",
    )

    with_cbs = app_dir / "agents" / "cb_agent"
    (with_cbs / "before_agent_callbacks" / "cb_01").mkdir(parents=True)
    (with_cbs / "wrong.json").write_text(
        json.dumps({"displayName": "cb_agent"}), encoding="utf-8"
    )
    (with_cbs / "instruction.txt").write_text("hi\n", encoding="utf-8")
    (
        with_cbs / "before_agent_callbacks" / "cb_01" / "python_code.py"
    ).write_text(
        "def before_agent_callback(callback_context):\n    return None\n",
        encoding="utf-8",
    )

    no_cbs = app_dir / "agents" / "plain_agent"
    no_cbs.mkdir(parents=True)
    (no_cbs / "misnamed.json").write_text(
        json.dumps({"displayName": "plain_agent"}), encoding="utf-8"
    )
    (no_cbs / "instruction.txt").write_text("hi\n", encoding="utf-8")

    results = _run_cxas_lint_callbacks(app_dir, {"C109"})
    c109_files = sorted(r.file for r in results if r.rule_id == "C109")
    assert c109_files == [
        "agents/cb_agent/wrong.json",
        "agents/plain_agent/misnamed.json",
    ]
    msgs = [r.message for r in results if r.rule_id == "C109"]
    assert any("Expected agent config 'cb_agent.json'" in m for m in msgs)
    assert any("Expected agent config 'plain_agent.json'" in m for m in msgs)

    # Layout B: agent without `instruction.txt` (not in `all_agent_names`).
    no_inst = app_dir / "agents" / "bare_agent"
    no_inst.mkdir(parents=True)
    (no_inst / "bare.json").write_text(
        json.dumps({"displayName": "bare_agent", "instruction": "hi"}),
        encoding="utf-8",
    )
    results = _run_cxas_lint_callbacks(app_dir, {"C109"})
    assert sorted(r.file for r in results if r.rule_id == "C109") == [
        "agents/bare_agent/bare.json",
        "agents/cb_agent/wrong.json",
        "agents/plain_agent/misnamed.json",
    ]

    # `--agents` filter (`limit_agents`) excludes other agents' C109.
    results = _run_cxas_lint_callbacks(
        app_dir, {"C109"}, limit_agents={"good_agent"}
    )
    assert [r for r in results if r.rule_id == "C109"] == []
    results = _run_cxas_lint_callbacks(
        app_dir, {"C109"}, limit_agents={"good_agent", "bare_agent"}
    )
    assert [r.file for r in results if r.rule_id == "C109"] == [
        "agents/bare_agent/bare.json"
    ]

    # Layout A: single-agent app with only `agents/only_agent/wrong.json`.
    single_app = tmp_path / "single_app"
    only_agent = single_app / "agents" / "only_agent"
    only_agent.mkdir(parents=True)
    (single_app / "app.json").write_text(
        json.dumps({"displayName": "single_app"}), encoding="utf-8"
    )
    (only_agent / "wrong.json").write_text(
        json.dumps({"displayName": "only_agent"}), encoding="utf-8"
    )
    results = _run_cxas_lint_callbacks(single_app, {"C109"})
    assert [r.file for r in results if r.rule_id == "C109"] == [
        "agents/only_agent/wrong.json"
    ]

    # Malformed `app.yaml` surfaces C109 even when no agent file is visited.
    (single_app / "app.json").unlink()
    (single_app / "app.yaml").write_text("rootAgent: [unclosed\n")
    results = _run_cxas_lint_callbacks(single_app, {"C109"})
    c109_files = sorted(r.file for r in results if r.rule_id == "C109")
    assert c109_files == ["agents/only_agent/wrong.json", "app.yaml"]


def test_pr548_lint_ast_parse_null_bytes_and_syntax_warnings() -> None:
    """Verifies lint catches `ValueError` (Python 3.10 null bytes) as C109 and
    silences `SyntaxWarning` from unescaped regex escapes.
    """
    import warnings  # noqa: PLC0415

    from cxas_scrapi.evals.callback_chain.lint import (  # noqa: PLC0415
        collect_subscript_or_in_keys_by_callback,
        collect_subscript_or_in_keys_in_specs,
        collect_written_state_keys_in_specs,
        lint_stage_chain,
    )
    from cxas_scrapi.evals.callback_chain.traces import (  # noqa: PLC0415
        CallbackSpec,
    )

    null_spec = CallbackSpec(
        name="cb_null",
        stage="before_agent",
        python_code="def before_agent_callback(ctx):\n    return None\x00\n",
        path="cb_null.py",
    )
    warn_spec = CallbackSpec(
        name="cb_warn",
        stage="before_agent",
        python_code=(
            "def before_agent_callback(ctx):\n"
            "    pat = '\\d+'\n"
            "    ctx.state['k'] = pat\n"
            "    return None\n"
        ),
        path="cb_warn.py",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        specs = [null_spec, warn_spec]
        assert collect_written_state_keys_in_specs("before_agent", specs) == {
            "k"
        }
        collect_subscript_or_in_keys_in_specs("before_agent", specs)
        collect_subscript_or_in_keys_by_callback("before_agent", specs)
        findings = lint_stage_chain("before_agent", specs)
    c109 = [f for f in findings if f.rule_id == "C109"]
    assert len(c109) == 1
    assert c109[0].callback_name == "cb_null"
    assert c109[0].line >= 1
    assert "cb_null" in c109[0].message
