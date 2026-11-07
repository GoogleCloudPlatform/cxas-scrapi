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

"""Unit tests for deterministic runtime-trap lint rules (PR 3)."""

import json
from pathlib import Path

from cxas_scrapi.utils.lint_rules.runtime_traps import (
    AfterModelUnreturnedResponse,
    AsyncToolMissingInactivityTimeout,
    CallbackLlmResponseTraps,
    CallbackSandboxTypingAndImports,
    CallbackStateNoneAssignment,
    CesJinjaSubsetSyntax,
    GuardrailRe2QuantifierSpacing,
    LlmPolicyGuardrailLimit,
    ToolEntryMustBeFirstDef,
    ToolReturnDictAnnotation,
    ToolSandboxTypingAndImports,
    ToolStateNoneAssignment,
    VariableCountLimit,
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


def _ctx(tmp_path: Path) -> LintContext:
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )


def test_t015_flags_helper_defined_above_entry_tool_function(
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "tools" / "lookup_order" / "python_function"
    tool_dir.mkdir(parents=True)
    py_file = tool_dir / "python_code.py"

    # Bad: helper above entry def
    py_file.write_text(
        "def _normalize(val: str) -> str:\n"
        "    return val.strip()\n\n"
        "def lookup_order(order_id: str = '') -> dict:\n"
        "    return {'id': _normalize(order_id)}\n"
    )
    res = ToolEntryMustBeFirstDef().check(
        py_file, py_file.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 1
    assert res[0].rule_id == "T015"
    assert "_normalize" in res[0].message

    # Good: entry def first, helper below
    py_file.write_text(
        "def lookup_order(order_id: str = '') -> dict:\n"
        "    return {'id': _normalize(order_id)}\n\n"
        "def _normalize(val: str) -> str:\n"
        "    return val.strip()\n"
    )
    assert (
        ToolEntryMustBeFirstDef().check(
            py_file, py_file.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_t016_requires_dict_return_annotation_on_python_tool(
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "tools" / "lookup_order" / "python_function"
    tool_dir.mkdir(parents=True)
    py_file = tool_dir / "python_code.py"

    py_file.write_text(
        "def lookup_order(order_id: str = ''):\n    return {'id': order_id}\n"
    )
    res = ToolReturnDictAnnotation().check(
        py_file, py_file.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 1
    assert res[0].rule_id == "T016"

    for valid_ret in ("dict", "dict[str, str]", "Dict[str, Any]"):
        py_file.write_text(
            f"def lookup_order(order_id: str = '') -> {valid_ret}:\n"
            "    return {'id': order_id}\n"
        )
        assert (
            ToolReturnDictAnnotation().check(
                py_file, py_file.read_text(), _ctx(tmp_path)
            )
            == []
        )


def test_c011_flags_state_none_assignments_in_callbacks_and_tools(
    tmp_path: Path,
) -> None:
    cb_file = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)
    cb_file.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    s = callback_context.state\n"
        "    s['account_id'] = None\n"
        "    callback_context.state.update({'user_role': None})\n"
        "    local_cache = {}\n"
        "    local_cache['temp'] = None\n"
        "    return None\n"
    )
    cb_res = CallbackStateNoneAssignment().check(
        cb_file, cb_file.read_text(), _ctx(tmp_path)
    )
    assert len(cb_res) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C011" for r in cb_res)

    tool_file = tmp_path / "tools" / "clear_slot" / "python_function.py"
    tool_file.parent.mkdir(parents=True)
    tool_file.write_text(
        "def clear_slot() -> dict:\n"
        "    context.state['slot'] = ''\n"
        "    return {'ok': True}\n"
    )
    assert (
        ToolStateNoneAssignment().check(
            tool_file, tool_file.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_c012_flags_unreturned_mutations_in_after_model_callback(
    tmp_path: Path,
) -> None:
    cb_file = (
        tmp_path
        / "agents"
        / "root"
        / "after_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)

    # Bad 1: Mutates llm_response.content.parts without returning llm_response
    cb_file.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content.parts.append(Part.from_text(text='bye'))\n"
        "    return None\n"
    )
    res1 = AfterModelUnreturnedResponse().check(
        cb_file, cb_file.read_text(), _ctx(tmp_path)
    )
    assert len(res1) == 1
    assert res1[0].rule_id == "C012"

    # Bad 2: Standalone LlmResponse.from_parts(...) expression without return
    cb_file.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    LlmResponse.from_parts(parts=[Part.from_text(text='fixed')])\n"
    )
    res2 = AfterModelUnreturnedResponse().check(
        cb_file, cb_file.read_text(), _ctx(tmp_path)
    )
    assert len(res2) == 1

    # Good: Explicitly returns mutated llm_response
    cb_file.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content.parts.append(Part.from_text(text='bye'))\n"
        "    return llm_response\n"
    )
    assert (
        AfterModelUnreturnedResponse().check(
            cb_file, cb_file.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_c018_catches_part_and_llm_response_traps(tmp_path: Path) -> None:
    cb_file = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)
    cb_file.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    p1 = Part.from_text(text='Hold on', partial=True)\n"
        "    p2 = Part.from_end_session(session_escalated=True)\n"
        "    r1 = LlmResponse.from_parts(\n"
        "        parts=[Part.from_function_call(name='lookup', args={})]\n"
        "    )\n"
        "    r1.partial = True\n"
        "    r2 = LlmResponse.from_parts(\n"
        "        parts=[Part.from_text(text='<warm> One moment please.')]\n"
        "    )\n"
        "    r2.partial = True\n"
        "    return LlmResponse.from_parts(parts=[])\n"
    )
    res = CallbackLlmResponseTraps().check(
        cb_file, cb_file.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 5  # noqa: PLR2004
    assert all(r.rule_id == "C018" for r in res)

    # Valid inactivity silence handler + plain-text partial response
    cb_file.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    if llm_request.last_user_content == '<no user activity>':\n"
        "        return LlmResponse.from_parts(parts=[])\n"
        "    bridge = LlmResponse.from_parts(\n"
        "        parts=[Part.from_text(text='Let me check that now.')]\n"
        "    )\n"
        "    bridge.partial = True\n"
        "    return bridge\n"
    )
    assert (
        CallbackLlmResponseTraps().check(
            cb_file, cb_file.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_c019_catches_unimported_typing_blocked_imports_and_threadpool(
    tmp_path: Path,
) -> None:
    py_file = tmp_path / "tools" / "fetch_profile" / "python_code.py"
    py_file.parent.mkdir(parents=True)
    py_file.write_text(
        "import requests\n"
        "from concurrent.futures import ThreadPoolExecutor\n\n"
        "def fetch_profile(user_id: Optional[str] = '') -> Dict[str, Any]:\n"
        "    with ThreadPoolExecutor() as pool:\n"
        "        pool.submit(tools.lookup_account, {'id': user_id})\n"
        "    return {}\n"
    )
    res = ToolSandboxTypingAndImports().check(
        py_file, py_file.read_text(), _ctx(tmp_path)
    )
    messages = "\n".join(r.message for r in res)
    assert "requests" in messages
    assert "Optional" in messages
    assert "Dict" in messages
    assert "Any" in messages
    assert "ThreadPoolExecutor" in messages

    # Clean version with ces_requests, explicit typing imports, and async_tools
    py_file.write_text(
        "import asyncio\n"
        "from typing import Any\n"
        "from ces_requests import ces_requests\n\n"
        "async def fetch_profile(user_id: str = '') -> dict[str, Any]:\n"
        "    await asyncio.gather(async_tools.lookup_account({'id': user_id}))\n"
        "    return {}\n"
    )
    assert (
        ToolSandboxTypingAndImports().check(
            py_file, py_file.read_text(), _ctx(tmp_path)
        )
        == []
    )
    assert (
        CallbackSandboxTypingAndImports().check(
            py_file, py_file.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_i020_catches_unsupported_ces_jinja_syntax(tmp_path: Path) -> None:
    inst = tmp_path / "agents" / "root_agent" / "instruction.txt"
    inst.parent.mkdir(parents=True)
    inst.write_text(
        "{# Internal author note #}\n"
        "{% if 'admin' not in roles %}\n"
        "Hello {{ customer['first_name'] }}!\n"
        "{% endif %}\n"
        "<inline_example>\n"
        "{{ ignored_in_example['x'] }}\n"
        "</inline_example>\n"
    )
    res = CesJinjaSubsetSyntax().check(inst, inst.read_text(), _ctx(tmp_path))
    assert len(res) == 3  # noqa: PLR2004
    assert all(r.rule_id == "I020" for r in res)

    inst.write_text(
        "{% if not ('admin' in roles) %}\n"
        "Hello {{ customer.first_name }}!\n"
        "{% endif %}\n"
    )
    assert (
        CesJinjaSubsetSyntax().check(inst, inst.read_text(), _ctx(tmp_path))
        == []
    )


def test_a014_a015_a016_and_s009_config_and_guardrail_rules(
    tmp_path: Path,
) -> None:
    # Async tool without inactivityTimeout
    tdir = tmp_path / "tools" / "slow_job"
    tdir.mkdir(parents=True)
    (tdir / "slow_job.json").write_text(
        json.dumps({"displayName": "slow_job", "executionType": "ASYNCHRONOUS"})
    )

    # 11 enabled llmPolicy guardrails + 1 contentFilter with spaced RE2 {10, 15}
    for i in range(11):
        gdir = tmp_path / "guardrails" / f"policy_{i:02d}"
        gdir.mkdir(parents=True)
        (gdir / f"policy_{i:02d}.json").write_text(
            json.dumps(
                {
                    "displayName": f"policy_{i:02d}",
                    "enabled": True,
                    "llmPolicy": {"prompt": "Check {{guardrail_instruction}}"},
                }
            )
        )

    bad_cf_dir = tmp_path / "guardrails" / "pii_filter"
    bad_cf_dir.mkdir(parents=True)
    (bad_cf_dir / "pii_filter.json").write_text(
        json.dumps(
            {
                "displayName": "pii_filter",
                "contentFilter": {"matchContentsRegexp": [r"\b\d{10, 15}\b"]},
            }
        )
    )

    app_file = tmp_path / "app.json"
    decls = [
        {"name": f"var_{i}", "schema": {"type": "STRING"}} for i in range(96)
    ]
    app_data = {
        "name": "demo",
        "displayName": "Demo",
        "variableDeclarations": decls,
    }
    app_file.write_text(json.dumps(app_data))

    ctx = _ctx(tmp_path)
    assert (
        len(
            AsyncToolMissingInactivityTimeout().check(
                app_file, app_file.read_text(), ctx
            )
        )
        == 1
    )
    assert (
        len(VariableCountLimit().check(app_file, app_file.read_text(), ctx))
        == 1
    )
    assert (
        len(
            LlmPolicyGuardrailLimit().check(app_file, app_file.read_text(), ctx)
        )
        == 1
    )
    s009_res = GuardrailRe2QuantifierSpacing().check(bad_cf_dir, "", ctx)
    assert len(s009_res) == 1
    assert s009_res[0].rule_id == "S009"
    assert "{10,15}" in s009_res[0].fix_suggestion

    # Fix inactivityTimeout and RE2 quantifier
    app_data["audioProcessingConfig"] = {"inactivityTimeout": "20s"}
    app_data["variableDeclarations"] = decls[:10]
    app_file.write_text(json.dumps(app_data))
    (bad_cf_dir / "pii_filter.json").write_text(
        json.dumps(
            {
                "displayName": "pii_filter",
                "contentFilter": {"matchContentsRegexp": [r"\b\d{10,15}\b"]},
            }
        )
    )
    assert (
        AsyncToolMissingInactivityTimeout().check(
            app_file, app_file.read_text(), ctx
        )
        == []
    )
    assert VariableCountLimit().check(app_file, app_file.read_text(), ctx) == []
    assert GuardrailRe2QuantifierSpacing().check(bad_cf_dir, "", ctx) == []


def test_sample_support_agent_passes_all_runtime_trap_rules() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    template_root = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
    )
    cfg = LintConfig.load(template_root)
    app_dir = template_root / cfg.app_dir
    discovery = Discovery(app_dir, template_root / cfg.evals_dir)
    ctx = build_context(template_root, cfg, discovery)
    reg = build_registry()
    report = LintReport()
    pr3_rules = {
        "T015",
        "T016",
        "C011",
        "C012",
        "C018",
        "C019",
        "I020",
        "A014",
        "A015",
        "A016",
        "S009",
    }
    run_rules(reg, cfg, ctx, discovery, report, specific_rules=pr3_rules)
    assert report.results == [], [str(r) for r in report.results]
