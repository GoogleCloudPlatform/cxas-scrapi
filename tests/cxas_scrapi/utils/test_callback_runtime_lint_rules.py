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

"""Unit tests for callback runtime and Xbox v2 tool hazard rules (C011-C017)."""

import json
from pathlib import Path

import pytest

from cxas_scrapi.utils.lint_rules.callbacks import (
    CallbackInvalidXboxToolInvocation,
    NonStringStateDictKey,
    SharedStateReferenceAliasing,
    StateNoneDeletesKey,
    ToolInvalidXboxToolInvocation,
    UnconditionalCallbackShortCircuit,
    UnreturnedLlmResponseMutation,
    UnsupportedLlmRequestMutation,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    build_context,
    build_registry,
    run_rules,
)


@pytest.fixture
def context(tmp_path: Path) -> LintContext:
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evaluations",
        app_root=tmp_path,
        all_tool_names={"lookup_account", "MCP_listCustomerCards"},
    )


# ── C011: UnconditionalCallbackShortCircuit ──────────────────────────────


def test_c011_flags_earlier_callback_that_unconditionally_returns_non_none(
    tmp_path: Path, context: LintContext
) -> None:
    agent_dir = tmp_path / "agents" / "root_agent"
    cb1 = (
        agent_dir
        / "before_model_callbacks"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb2 = (
        agent_dir
        / "before_model_callbacks"
        / "before_model_callbacks_02"
        / "python_code.py"
    )
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    cb1.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    if callback_context.state.get('block'):\n"
        "        return LlmResponse.from_parts([Part.from_text('blocked')])\n"
        "    else:\n"
        "        return LlmResponse.from_parts([Part.from_text('default')])\n"
    )
    cb2.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )

    rule = UnconditionalCallbackShortCircuit()
    res1 = rule.check(cb1, cb1.read_text(), context)
    assert len(res1) == 1
    assert res1[0].rule_id == "C011"
    assert res1[0].severity == Severity.WARNING
    assert "before_model_callbacks_02" in res1[0].message

    # Last callback in stage is allowed to unconditionally return non-None
    cb2.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return LlmResponse.from_parts([Part.from_text('final')])\n"
    )
    assert rule.check(cb2, cb2.read_text(), context) == []


def test_c011_supports_flat_callback_layout_and_agent_json_order(
    tmp_path: Path, context: LintContext
) -> None:
    agent_dir = tmp_path / "agents" / "routing_agent"
    cb1 = agent_dir / "before_model_callbacks_01" / "python_code.py"
    cb2 = agent_dir / "before_model_callbacks_02" / "python_code.py"
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    (agent_dir / "routing_agent.json").write_text(
        json.dumps(
            {
                "displayName": "routing_agent",
                "beforeModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/routing_agent/before_model_callbacks_01/"
                            "python_code.py"
                        )
                    },
                    {
                        "pythonCode": (
                            "agents/routing_agent/before_model_callbacks_02/"
                            "python_code.py"
                        )
                    },
                ],
            }
        )
    )
    cb1.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return {'override': True}\n"
    )
    cb2.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )

    rule = UnconditionalCallbackShortCircuit()
    res = rule.check(cb1, cb1.read_text(), context)
    assert len(res) == 1
    assert res[0].rule_id == "C011"


def test_c011_clean_when_callback_has_return_none_or_fallthrough(
    tmp_path: Path, context: LintContext
) -> None:
    agent_dir = tmp_path / "agents" / "clean_agent"
    cb1 = agent_dir / "before_model_callbacks_01" / "python_code.py"
    cb2 = agent_dir / "before_model_callbacks_02" / "python_code.py"
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    cb1.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    if callback_context.state.get('short_circuit'):\n"
        "        return LlmResponse.from_parts([Part.from_text('stop')])\n"
        "    return None\n"
    )
    cb2.write_text(
        "def before_model_callback(callback_context, llm_request):\n    pass\n"
    )

    rule = UnconditionalCallbackShortCircuit()
    assert rule.check(cb1, cb1.read_text(), context) == []


# ── C012: SharedStateReferenceAliasing ───────────────────────────────────


def test_c012_flags_mutable_local_assigned_to_multiple_state_keys(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    shared_list = [x for x in range(3)]\n"
        "    state['items_a'] = shared_list\n"
        "    callback_context.state['items_b'] = shared_list\n"
    )

    results = SharedStateReferenceAliasing().check(cb, cb.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "C012"
    assert results[0].severity == Severity.WARNING
    assert "shared_list" in results[0].message


def test_c012_clean_when_copied_or_reassigned_or_immutable(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "import copy\n\n"
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    label = 'active'\n"
        "    state['status_a'] = label\n"
        "    state['status_b'] = label\n"
        "    template = {'count': 0}\n"
        "    state['counter_a'] = template.copy()\n"
        "    state['counter_b'] = copy.deepcopy(template)\n"
        "    box = {'x': 1}\n"
        "    state['box_1'] = box\n"
        "    box = {'x': 2}\n"
        "    state['box_2'] = box\n"
    )

    assert (
        SharedStateReferenceAliasing().check(cb, cb.read_text(), context) == []
    )


# ── C013: StateNoneDeletesKey ────────────────────────────────────────────


def test_c013_flags_state_and_variables_none_assignment(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state['pending_intent'] = None\n"
        "    callback_context.variables['slot_value'] = None\n"
    )

    results = StateNoneDeletesKey().check(cb, cb.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C013" for r in results)
    assert all(r.severity == Severity.WARNING for r in results)
    assert "pending_intent" in results[0].message
    assert "slot_value" in results[1].message


def test_c013_clean_on_state_pop_or_empty_sentinel(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state.pop('pending_intent', None)\n"
        "    state['slot_value'] = ''\n"
        "    local_dict = {}\n"
        "    local_dict['k'] = None\n"
    )

    assert StateNoneDeletesKey().check(cb, cb.read_text(), context) == []


# ── C014: UnreturnedLlmResponseMutation ──────────────────────────────────


def test_c014_flags_in_place_llm_response_mutation_returning_none(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "after_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content.parts.append(Part.from_text('extra'))\n"
        "    return None\n"
    )

    results = UnreturnedLlmResponseMutation().check(cb, cb.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "C014"
    assert results[0].severity == Severity.WARNING


def test_c014_clean_when_mutated_llm_response_is_returned(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "after_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    parts = llm_response.content.parts\n"
        "    if parts:\n"
        "        llm_response.content.parts = [parts[0]]\n"
        "        return llm_response\n"
        "    return None\n"
    )

    assert (
        UnreturnedLlmResponseMutation().check(cb, cb.read_text(), context) == []
    )


# ── C015: UnsupportedLlmRequestMutation ──────────────────────────────────


def test_c015_flags_discarded_llm_request_mutations(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    llm_request.tools = []\n"
        "    llm_request.model = 'gemini-2.5-pro'\n"
        "    llm_request.config.temperature = 0.0\n"
        "    return None\n"
    )

    results = UnsupportedLlmRequestMutation().check(cb, cb.read_text(), context)
    assert len(results) == 3  # noqa: PLR2004
    assert all(r.rule_id == "C015" for r in results)
    messages = " ".join(r.message for r in results)
    assert "llm_request.tools" in messages
    assert "llm_request.model" in messages
    assert "llm_request.config.temperature" in messages


def test_c015_allows_contents_system_instruction_and_excluded_tools(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    llm_request.contents = []\n"
        "    llm_request.config.system_instruction = 'Be concise.'\n"
        "    llm_request.config.excluded_tools = ['secret_tool']\n"
        "    return None\n"
    )

    assert (
        UnsupportedLlmRequestMutation().check(cb, cb.read_text(), context) == []
    )


# ── C016: NonStringStateDictKey ──────────────────────────────────────────


def test_c016_flags_non_string_dict_keys_in_state_and_update(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state['by_code'] = {404: 'not_found'}\n"
        "    state.update({'nested': {(1, 2): 'coord'}})\n"
    )

    results = NonStringStateDictKey().check(cb, cb.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C016" for r in results)
    assert all(r.severity == Severity.ERROR for r in results)


def test_c016_clean_on_string_dict_keys(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state['by_code'] = {'404': 'not_found'}\n"
        "    state.update({'nested': {'1_2': 'coord'}})\n"
    )

    assert NonStringStateDictKey().check(cb, cb.read_text(), context) == []


# ── C017: InvalidXboxToolInvocation (Callbacks & Tools) ──────────────────


def test_c017_flags_keyword_args_and_getattr_hasattr_on_tools_in_callbacks(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    if hasattr(tools, 'MCP_listCustomerCards'):\n"
        "        fn = getattr(tools, 'MCP_listCustomerCards', None)\n"
        "    tools.MCP_listCustomerCards(cardProductCategory='DEBIT')\n"
        "    return None\n"
    )

    results = CallbackInvalidXboxToolInvocation().check(
        cb, cb.read_text(), context
    )
    assert len(results) == 3  # noqa: PLR2004
    assert all(r.rule_id == "C017" for r in results)
    assert all(r.severity == Severity.ERROR for r in results)


def test_c017_flags_keyword_args_and_getattr_in_python_tools(
    tmp_path: Path, context: LintContext
) -> None:
    tool_py = (
        tmp_path
        / "tools"
        / "orchestrate_cards"
        / "python_function"
        / "python_code.py"
    )
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "def orchestrate_cards(customer_id: str = '') -> dict:\n"
        '    """Orchestrate card lookup."""\n'
        "    if getattr(tools, 'lookup_account', None):\n"
        "        return tools.lookup_account(customer_id=customer_id)\n"
        "    return {}\n"
    )

    results = ToolInvalidXboxToolInvocation().check(
        tool_py, tool_py.read_text(), context
    )
    assert len(results) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C017" for r in results)


def test_c017_clean_on_positional_dict_and_shadowed_local_tools(
    tmp_path: Path, context: LintContext
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    tools.MCP_listCustomerCards({'cardProductCategory': 'DEBIT'})\n"
        "    tools.lookup_account()\n"
        "    return None\n\n"
        "def _local_helper():\n"
        "    tools = {'a': 1}\n"
        "    tools.setdefault('b', default=2)\n"
    )

    assert (
        CallbackInvalidXboxToolInvocation().check(cb, cb.read_text(), context)
        == []
    )


def test_end_to_end_run_rules_dispatches_c011_to_c017_across_callbacks_and_tools(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "demo", "displayName": "demo", "rootAgent": "root"})
    )
    agent_dir = tmp_path / "agents" / "root"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text("<role>Root</role>\n")
    (agent_dir / "root.json").write_text(json.dumps({"displayName": "root"}))

    bm1 = agent_dir / "before_model_callbacks_01" / "python_code.py"
    bm2 = agent_dir / "before_model_callbacks_02" / "python_code.py"
    am1 = agent_dir / "after_model_callbacks_01" / "python_code.py"
    bm1.parent.mkdir(parents=True)
    bm2.parent.mkdir(parents=True)
    am1.parent.mkdir(parents=True)

    bm1.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = callback_context.state\n"
        "    shared = {'k': 1}\n"
        "    state['a'] = shared\n"
        "    state['b'] = shared\n"
        "    state['gone'] = None\n"
        "    state['bad_key'] = {1: 'one'}\n"
        "    llm_request.tools = []\n"
        "    return LlmResponse.from_parts([Part.from_text('short-circuit')])\n"
    )
    bm2.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )
    am1.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content = None\n"
        "    return None\n"
    )

    tool_py = (
        tmp_path / "tools" / "bad_tool" / "python_function" / "python_code.py"
    )
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "def bad_tool(x: str = '') -> dict:\n"
        '    """Doc."""\n'
        "    return tools.other_tool(x=x)\n"
    )

    config = LintConfig.load(tmp_path)
    discovery = Discovery(tmp_path, tmp_path / "evaluations")
    ctx = build_context(tmp_path, config, discovery)
    registry = build_registry()
    report = LintReport()
    run_rules(
        registry,
        config,
        ctx,
        discovery,
        report,
        specific_rules={
            "C011",
            "C012",
            "C013",
            "C014",
            "C015",
            "C016",
            "C017",
        },
    )

    found_ids = {r.rule_id for r in report.results}
    assert found_ids == {
        "C011",
        "C012",
        "C013",
        "C014",
        "C015",
        "C016",
        "C017",
    }


# ── QA Reproduction & Adversarial Edge-Case Tests (QA-001..QA-006) ───────


def test_qa_001_c014_flags_partial_return_none_path_after_mutation(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-001: C014 must flag mutation followed by conditional return None."""
    cb1 = (
        tmp_path
        / "agents"
        / "root"
        / "after_model_callbacks_01"
        / "python_code.py"
    )
    cb2 = (
        tmp_path
        / "agents"
        / "root"
        / "after_model_callbacks_02"
        / "python_code.py"
    )
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    cb1.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content = 'mutated'\n"
        "    if callback_context.state.get('skip'):\n"
        "        return None\n"
        "    return llm_response\n"
    )
    cb2.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    if callback_context.state.get('modify'):\n"
        "        llm_response.content = 'mutated'\n"
        "        if callback_context.state.get('abort'):\n"
        "            return None\n"
        "    return llm_response\n"
    )

    rule = UnreturnedLlmResponseMutation()
    res1 = rule.check(cb1, cb1.read_text(), context)
    assert len(res1) == 1
    assert res1[0].rule_id == "C014"

    res2 = rule.check(cb2, cb2.read_text(), context)
    assert len(res2) == 1
    assert res2[0].rule_id == "C014"


def test_qa_002_c017_flags_context_tools_and_respects_tools_param_shadowing(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-002: C017 must catch context.tools.* and not flag shadowed param."""
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(context, llm_request):\n"
        "    if hasattr(context.tools, 'lookup_account'):\n"
        "        fn = getattr(context.tools, 'lookup_account', None)\n"
        "    context.tools.lookup_account(id='123')\n"
        "    tools.lookup_account(**{'id': '123'})\n"
        "    return None\n\n"
        "def _helper_with_param(tools):\n"
        "    return tools.setdefault('k', default=1)\n"
    )
    tool_py = (
        tmp_path / "tools" / "my_tool" / "python_function" / "python_code.py"
    )
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "def my_tool(id: str = '') -> dict:\n"
        '    """Doc."""\n'
        "    if hasattr(context.tools, 'lookup_account'):\n"
        "        return context.tools.lookup_account(id=id)\n"
        "    return {}\n\n"
        "def _inner(tools):\n"
        "    return tools.custom_method(key='val')\n"
    )

    cb_res = CallbackInvalidXboxToolInvocation().check(
        cb, cb.read_text(), context
    )
    assert len(cb_res) == 4  # noqa: PLR2004
    assert all(r.rule_id == "C017" for r in cb_res)

    tool_res = ToolInvalidXboxToolInvocation().check(
        tool_py, tool_py.read_text(), context
    )
    assert len(tool_res) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C017" for r in tool_res)


def test_qa_003_c016_flags_direct_non_string_state_subscript_and_nested_var(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-003: C016 must flag state[1] = 'a' and nested bad local dicts."""
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state[1] = 'a'\n"
        "    callback_context.state[(1, 2)] = 'b'\n"
        "    state['nested'] = {'inner': {1: 'a'}}\n"
        "    bad_inner = {2: 'b'}\n"
        "    state['wrapped'] = {'sub': bad_inner}\n"
    )

    results = NonStringStateDictKey().check(cb, cb.read_text(), context)
    assert len(results) == 4  # noqa: PLR2004
    assert all(r.rule_id == "C016" for r in results)


def test_qa_004_c013_flags_state_update_with_none_value(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-004: C013 must flag state.update({'k': None}) and keyword None."""
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(context):\n"
        "    state = context.state\n"
        "    state.update({'k': None})\n"
        "    context.state.update(other_key=None)\n"
    )

    results = StateNoneDeletesKey().check(cb, cb.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C013" for r in results)


def test_qa_005_c011_flags_local_response_var_and_try_finally_or_else(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-005: C011 must flag local LlmResponse variable and try/finally."""
    agent_dir = tmp_path / "agents" / "root_agent"
    cb1 = (
        agent_dir
        / "before_model_callbacks"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb2 = (
        agent_dir
        / "before_model_callbacks"
        / "before_model_callbacks_02"
        / "python_code.py"
    )
    cb3 = (
        agent_dir
        / "before_model_callbacks"
        / "before_model_callbacks_03"
        / "python_code.py"
    )
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    cb3.parent.mkdir(parents=True)

    cb1.write_text(
        "def _helper():\n"
        "    return LlmResponse.from_parts([Part.from_text('helper')])\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    resp = LlmResponse.from_parts([Part.from_text('short')])\n"
        "    return resp\n"
    )
    cb2.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    try:\n"
        "        do_work()\n"
        "    finally:\n"
        "        return LlmResponse.from_parts([Part.from_text('finally')])\n"
    )
    cb3.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n"
    )

    rule = UnconditionalCallbackShortCircuit()
    res1 = rule.check(cb1, cb1.read_text(), context)
    assert len(res1) == 1
    assert res1[0].rule_id == "C011"

    res2 = rule.check(cb2, cb2.read_text(), context)
    assert len(res2) == 1
    assert res2[0].rule_id == "C011"


def test_qa_006_c012_flags_chained_mutable_state_assignment(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-006: C012 must flag chained mutable assignments to state keys."""
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_agent_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state['a'] = state['b'] = {}\n"
        "    d = state['c'] = []\n"
        "    state['d'] = d\n"
    )

    results = SharedStateReferenceAliasing().check(cb, cb.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert all(r.rule_id == "C012" for r in results)


def test_qa_c011_helper_function_and_reversed_agent_json_order(
    tmp_path: Path, context: LintContext
) -> None:
    """Verify helper function does not trigger C011 and agent.json order wins."""
    agent_dir = tmp_path / "agents" / "ordered_agent"
    cb1 = agent_dir / "before_model_callbacks_01" / "python_code.py"
    cb2 = agent_dir / "before_model_callbacks_02" / "python_code.py"
    cb1.parent.mkdir(parents=True)
    cb2.parent.mkdir(parents=True)
    # Reverse order in agent.json: cb2 runs first, cb1 runs last!
    (agent_dir / "ordered_agent.json").write_text(
        json.dumps(
            {
                "displayName": "ordered_agent",
                "beforeModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/ordered_agent/before_model_callbacks_02/"
                            "python_code.py"
                        )
                    },
                    {
                        "pythonCode": (
                            "agents/ordered_agent/before_model_callbacks_01/"
                            "python_code.py"
                        )
                    },
                ],
            }
        )
    )
    # cb1 is last in agent.json, so even though it unconditionally returns
    # LlmResponse, it does not make any subsequent callback unreachable.
    cb1.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    return LlmResponse.from_parts([Part.from_text('final')])\n"
    )
    # cb2 is first in agent.json, has a helper that unconditionally returns
    # a dict, but the entry callback itself is conditional.
    cb2.write_text(
        "def helper():\n"
        "    return {'always': True}\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    if callback_context.state.get('flag'):\n"
        "        return LlmResponse.from_parts([Part.from_text('cond')])\n"
        "    return None\n"
    )

    rule = UnconditionalCallbackShortCircuit()
    assert rule.check(cb1, cb1.read_text(), context) == []
    assert rule.check(cb2, cb2.read_text(), context) == []


def test_qa_c015_allows_excluded_tools_append_and_contents_append(
    tmp_path: Path, context: LintContext
) -> None:
    """Verify allowed in-place method calls on excluded_tools and contents."""
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    llm_request.config.excluded_tools.append('hidden_tool')\n"
        "    llm_request.contents.append('extra_turn')\n"
        "    return None\n"
    )
    assert (
        UnsupportedLlmRequestMutation().check(cb, cb.read_text(), context) == []
    )
