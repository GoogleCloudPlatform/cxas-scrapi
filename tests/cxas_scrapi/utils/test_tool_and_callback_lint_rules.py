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

"""Unit tests for tool (T003-T014) and callback (C004, C007) lint rules."""

from pathlib import Path

import pytest

from cxas_scrapi.utils.lint_rules.callbacks import (
    ReturnsDictNotLlmResponse,
    ToolNamingConvention,
    WrongArgCount,
    WrongFunctionName,
)
from cxas_scrapi.utils.lint_rules.tools import (
    CompositeToolConversationalPacing,
    FunctionNameMismatch,
    HighCardinalityArgs,
    KwargsInSignature,
    MissingToolDescriptionInJSON,
    MissingTypeHints,
    NoneDefaultValue,
    ToolDisplayNameUnreferenced,
)
from cxas_scrapi.utils.linter import LintContext


@pytest.fixture
def context(tmp_path: Path) -> LintContext:
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path,
        all_tool_names={"get_balance", "start_lookups"},
    )


def test_t003_and_t004_skip_private_helper_functions_above_tool_def(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "capture_payment" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text(
        "def _group(g):\n"
        "    return {'raw': g}\n\n"
        "def capture_payment(amount: str = '', method: str = '') -> dict:\n"
        "    return _group(amount)\n"
    )

    assert FunctionNameMismatch().check(f, f.read_text(), context) == []
    assert MissingTypeHints().check(f, f.read_text(), context) == []


def test_t003_and_t011_allow_runtime_context_parameter(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "update_language" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text(
        "def update_language(new_language: str = '', context=None) -> dict:\n"
        "    return {'ok': True}\n"
    )

    assert NoneDefaultValue().check(f, f.read_text(), context) == []

    tool_dir2 = tmp_path / "tools" / "mark_order_issue" / "python_function"
    tool_dir2.mkdir(parents=True)
    f2 = tool_dir2 / "python_code.py"
    f2.write_text("def mark_order_issue(context) -> dict:\n    return {}\n")
    assert MissingTypeHints().check(f2, f2.read_text(), context) == []


def test_t009_and_t011_inspect_entry_tool_function_not_private_helper(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "submit_payment" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text(
        "def _log_event(tag, extra=None, **kwargs):\n"
        "    return {}\n\n"
        "def submit_payment(amount: str = '') -> dict:\n"
        "    return _log_event('pay')\n"
    )

    assert KwargsInSignature().check(f, f.read_text(), context) == []
    assert NoneDefaultValue().check(f, f.read_text(), context) == []


def test_t012_and_t014_recognize_remote_agent_tool_description(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "call_outbound_payment"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "call_outbound_payment.json"
    f.write_text(
        '{"displayName": "call_outbound_payment", '
        '"remoteAgentTool": {"name": "call_outbound_payment", '
        '"description": "Before calling this tool, speak a brief '
        'conversational pacing phrase."}}'
    )

    assert MissingToolDescriptionInJSON().check(f, f.read_text(), context) == []
    assert (
        CompositeToolConversationalPacing().check(f, f.read_text(), context)
        == []
    )


def test_c004_ignores_dict_return_in_helper_functions(
    tmp_path: Path, context: LintContext
) -> None:
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "guard_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def _build_payload():\n"
        "    return {'status': 'ok'}\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    def _nested():\n"
        "        return {1, 2, 3}\n"
        "    _build_payload()\n"
        "    return None\n"
    )

    assert ReturnsDictNotLlmResponse().check(f, f.read_text(), context) == []


def test_c007_ignores_python_collection_methods_on_local_tools_var(
    tmp_path: Path, context: LintContext
) -> None:
    cb_dir = tmp_path / "agents" / "root" / "after_model_callbacks" / "after_01"
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    tools = {'a': 1}\n"
        "    for k, v in tools.items():\n"
        "        pass\n"
        "    tool_set = set()\n"
        "    tools = tool_set\n"
        "    tools.add('x')\n"
        "    tools.start_lookups({})\n"
    )

    assert ToolNamingConvention().check(f, f.read_text(), context) == []


def test_t011_parses_complex_type_annotations_with_commas_via_ast(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "set_next_agent_actions" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text(
        "from typing import Any, Optional\n\n"
        "def set_next_agent_actions(\n"
        "    customized_response: Optional[dict[str, Any]] = None,\n"
        "    active_lookup_flows: Optional[dict[str, Any]] = None,\n"
        ") -> None:\n"
        "    pass\n"
    )

    results = NoneDefaultValue().check(f, f.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert "Parameter 'customized_response'" in results[0].message
    assert "Parameter 'active_lookup_flows'" in results[1].message


def test_t005_skips_private_helper_functions_above_tool_def(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "lookup_order" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text(
        "def _format_ts(timestamp: str) -> str:\n"
        "    return timestamp\n\n"
        "def lookup_order(order_id: str = '') -> dict:\n"
        "    return {'ts': _format_ts('now')}\n"
    )

    assert HighCardinalityArgs().check(f, f.read_text(), context) == []


def test_c004_handles_async_callback_functions(
    tmp_path: Path, context: LintContext
) -> None:
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "async_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "async def before_model_callback(callback_context, llm_request):\n"
        "    return {'bad': 'dict'}\n"
    )

    results = ReturnsDictNotLlmResponse().check(f, f.read_text(), context)
    assert len(results) == 1
    assert results[0].rule_id == "C004"


def test_t008_recognizes_tools_invoked_from_callbacks_tools_or_guardrails(
    tmp_path: Path, context: LintContext
) -> None:
    (tmp_path / "agents" / "root_agent").mkdir(parents=True)
    (tmp_path / "agents" / "root_agent" / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": []}'
    )

    # 1. Helper tool invoked from a callback via tools.cb_helper(...)
    cb_dir = (
        tmp_path / "agents" / "root_agent" / "before_model_callbacks" / "cb_01"
    )
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    tools.cb_helper({})\n"
    )
    t1_dir = tmp_path / "tools" / "cb_helper" / "python_function"
    t1_dir.mkdir(parents=True)
    f1 = t1_dir / "python_code.py"
    f1.write_text("def cb_helper() -> dict:\n    return {}\n")
    (tmp_path / "tools" / "cb_helper" / "cb_helper.json").write_text(
        '{"name": "cb_helper", "displayName": "cb_helper"}'
    )
    assert (
        ToolDisplayNameUnreferenced().check(f1, f1.read_text(), context) == []
    )

    # 2. Helper tool invoked from a guardrail action.toolCall
    gr_dir = tmp_path / "guardrails" / "esc_guardrail"
    gr_dir.mkdir(parents=True)
    (gr_dir / "esc_guardrail.json").write_text(
        '{"action": {"toolCall": {"tool": "gr_helper"}}}'
    )
    t2_dir = tmp_path / "tools" / "gr_helper" / "python_function"
    t2_dir.mkdir(parents=True)
    f2 = t2_dir / "python_code.py"
    f2.write_text("def gr_helper() -> dict:\n    return {}\n")
    (tmp_path / "tools" / "gr_helper" / "gr_helper.json").write_text(
        '{"name": "gr_helper", "displayName": "gr_helper"}'
    )
    assert (
        ToolDisplayNameUnreferenced().check(f2, f2.read_text(), context) == []
    )

    # 3. Prefix collision (cb_helper vs cb) or comment must still flag T008
    t3_dir = tmp_path / "tools" / "cb" / "python_function"
    t3_dir.mkdir(parents=True)
    f3 = t3_dir / "python_code.py"
    f3.write_text("def cb() -> dict:\n    return {}\n")
    (tmp_path / "tools" / "cb" / "cb.json").write_text(
        '{"name": "cb", "displayName": "cb"}'
    )
    assert (
        len(ToolDisplayNameUnreferenced().check(f3, f3.read_text(), context))
        == 1
    )


def test_t012_accepts_python_function_docstring_when_json_omits_output_only_desc(
    tmp_path: Path, context: LintContext
) -> None:
    tool_dir = tmp_path / "tools" / "get_current_bill_rules"
    py_dir = tool_dir / "python_function"
    py_dir.mkdir(parents=True)
    json_f = tool_dir / "get_current_bill_rules.json"
    json_f.write_text(
        '{"name": "get_current_bill_rules", '
        '"displayName": "get_current_bill_rules", '
        '"pythonFunction": {"name": "get_current_bill_rules"}}'
    )
    py_f = py_dir / "python_code.py"
    py_f.write_text(
        "def get_current_bill_rules() -> dict:\n"
        '    """Return billing rules for the current bill."""\n'
        "    return {}\n"
    )

    assert (
        MissingToolDescriptionInJSON().check(
            json_f, json_f.read_text(), context
        )
        == []
    )

    # A triple-quoted literal in the body (not a docstring) must still flag T012
    py_f.write_text(
        "def get_current_bill_rules() -> dict:\n"
        '    query = """SELECT 1"""\n'
        "    return {'q': query}\n"
    )
    assert (
        len(
            MissingToolDescriptionInJSON().check(
                json_f, json_f.read_text(), context
            )
        )
        == 1
    )


def test_c007_ignores_comments_and_bypass_tool_prefixes(
    tmp_path: Path,
) -> None:
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path,
        all_tool_names={"known_tool"},
        bypass_tool_prefixes={"common_mcp_"},
    )
    cb_dir = tmp_path / "agents" / "root" / "before_model_callbacks_01"
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "# tools.CCFV_commented_out_tool({'a': 1})\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    tools.common_mcp_endSession({})\n"
        "    tools.known_tool({})\n"
        "    return None\n"
    )

    assert ToolNamingConvention().check(f, f.read_text(), ctx) == []
    assert WrongFunctionName().check(f, f.read_text(), ctx) == []
    assert WrongArgCount().check(f, f.read_text(), ctx) == []
