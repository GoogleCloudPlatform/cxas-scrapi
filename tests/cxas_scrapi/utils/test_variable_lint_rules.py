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

"""Tests for variable lint rule coverage (V100-V106) and state AST extraction."""

import json
import typing

import pytest

from cxas_scrapi.utils.lint_rules.variables import (
    CallbackNestedPropertyExists,
    CallbackStaleFlatVar,
    CallbackVariableDeclared,
    CallbackVariableTypeMatch,
    EvalVariableDeclared,
    InstructionStaleFlatVar,
    InstructionVariableRef,
    ToolFlatVariableGroup,
    ToolVariableDeclared,
    ToolVariableSingleWriter,
    ToolVariableTypeMatch,
    _clear_schema_cache,
    _clear_writer_cache,
    _collect_state_accesses,
)
from cxas_scrapi.utils.linter import LintContext

_VAR_APP_JSON = """\
{
  "name": "test-app",
  "rootAgent": "Root_Agent",
  "variableDeclarations": [
    {
      "name": "customer",
      "schema": {
        "type": "OBJECT",
        "properties": {
          "auth_status": {"type": "STRING"},
          "api_failed": {"type": "STRING"},
          "account_id": {"type": "STRING"}
        }
      }
    },
    {
      "name": "_internal",
      "schema": {
        "type": "OBJECT",
        "properties": {
          "action_trigger": {"type": "STRING"},
          "escalation_topic": {"type": "STRING"}
        }
      }
    },
    {"name": "flat_str", "schema": {"type": "STRING"}},
    {"name": "freeform_obj", "schema": {"type": "OBJECT"}}
  ]
}
"""


@pytest.fixture
def var_context(tmp_path: typing.Any) -> LintContext:
    """LintContext with an app.json containing variableDeclarations."""
    (tmp_path / "app.json").write_text(_VAR_APP_JSON)
    _clear_schema_cache()
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )


def test_v100_eval_missing_nested_property(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    rule = EvalVariableDeclared()
    f = tmp_path / "evals" / "goldens" / "g.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "common_session_parameters:\n"
        "  customer:\n"
        "    account_id: 'a1'\n"
        "    bogus_field: 'x'\n"
    )
    results = rule.check(f, f.read_text(), var_context)
    assert len(results) == 1
    assert results[0].rule_id == "V100"
    assert "customer" in results[0].message
    assert "bogus_field" in results[0].message


def test_v100_eval_stale_flat_variable(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    rule = EvalVariableDeclared()
    f = tmp_path / "evals" / "goldens" / "g.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("session_parameters:\n  auth_status: 'authenticated'\n")
    results = rule.check(f, f.read_text(), var_context)
    assert len(results) == 1
    assert results[0].rule_id == "V100"
    assert "customer.auth_status" in results[0].message


def test_v100_eval_nested_under_non_object(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    rule = EvalVariableDeclared()
    f = tmp_path / "evals" / "goldens" / "g.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("variables:\n  flat_str:\n    sub_key: 'val'\n")
    results = rule.check(f, f.read_text(), var_context)
    assert len(results) == 1
    assert results[0].rule_id == "V100"
    assert "flat_str" in results[0].message
    assert "not an OBJECT" in results[0].message


def test_state_visitor_pop_and_in_comparisons(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    src = (
        "def before_model_callback(cb, llm):\n"
        "    state = cb.state\n"
        "    if 'undeclared_flag' in state:\n"
        "        state.pop('_internal.bogus_trigger', None)\n"
        "    if 'auth_status' not in cb.state:\n"
        "        pass\n"
    )
    accesses = _collect_state_accesses(src)
    assert ("read", "undeclared_flag", 3, None) in accesses
    assert ("read", "_internal.bogus_trigger", 4, None) in accesses
    assert ("read", "auth_status", 5, None) in accesses

    f = tmp_path / "python_code.py"
    f.write_text(src)

    v100_results = CallbackVariableDeclared().check(f, src, var_context)
    assert len(v100_results) == 1
    assert "undeclared_flag" in v100_results[0].message

    v102_results = CallbackNestedPropertyExists().check(f, src, var_context)
    assert len(v102_results) == 1
    assert "bogus_trigger" in v102_results[0].message

    v103_results = CallbackStaleFlatVar().check(f, src, var_context)
    assert len(v103_results) == 1
    assert "customer.auth_status" in v103_results[0].message

    # state.pop default arg should not trigger V101 type-mismatch check
    v101_results = CallbackVariableTypeMatch().check(f, src, var_context)
    assert v101_results == []


def test_state_visitor_update_keyword_arguments(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    src = (
        "def my_tool(context):\n"
        "    context.state.update(flat_str=123, missing_kw='val')\n"
    )
    f = tmp_path / "python_code.py"
    f.write_text(src)

    v100_results = ToolVariableDeclared().check(f, src, var_context)
    assert len(v100_results) == 1
    assert "missing_kw" in v100_results[0].message

    v101_results = ToolVariableTypeMatch().check(f, src, var_context)
    assert len(v101_results) == 1
    assert "flat_str" in v101_results[0].message
    assert "int" in v101_results[0].message


def test_load_var_schema_snake_case_app_yaml(tmp_path: typing.Any) -> None:
    (tmp_path / "app.yaml").write_text(
        "name: test-app\n"
        "variable_declarations:\n"
        "  - name: flat_str\n"
        "    schema:\n"
        "      type: STRING\n"
    )
    _clear_schema_cache()
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )
    f = tmp_path / "python_code.py"
    f.write_text("def cb(c):\n    c.state['undeclared'] = 'x'\n")
    results = CallbackVariableDeclared().check(f, f.read_text(), ctx)
    assert len(results) == 1
    assert "undeclared" in results[0].message


def test_freeform_object_allows_nested_keys(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    rule = EvalVariableDeclared()
    f = tmp_path / "evals" / "goldens" / "g.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "session_parameters:\n"
        "  freeform_obj:\n"
        "    items:\n"
        "      - id: 1\n"
        "    next_page_token: 'abc'\n"
    )
    results = rule.check(f, f.read_text(), var_context)
    assert results == []


def test_load_var_schema_skips_malformed_non_dict_entries_without_crashing(
    tmp_path: typing.Any,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"name": "test-app", "variableDeclarations": ["malformed_str", {"name": "typed_str", "schema": {"type": "STRING"}}]}'
    )
    _clear_schema_cache()
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )
    f = tmp_path / "python_code.py"
    f.write_text(
        "def cb(callback_context):\n"
        "    callback_context.state['typed_str'] = 'ok'\n"
        "    callback_context.state['undeclared'] = 'x'\n"
    )
    results = CallbackVariableDeclared().check(f, f.read_text(), ctx)
    assert len(results) == 1
    assert "undeclared" in results[0].message


def test_state_visitor_ignores_non_context_state_and_tracks_aliases(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    src = (
        "class Engine:\n"
        "    def step(self):\n"
        "        self.state['internal_step'] = 'init'\n"
        "        state = self.state\n"
        "        state['internal_key'] = 1\n"
        "\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = load_state(callback_context)\n"
        "    state['engine_only_key'] = 'v'\n"
        "    cs = callback_context.state\n"
        "    cs.get('undeclared_session_var')\n"
        "    cs['flat_str'] = 'valid'\n"
    )
    accesses = _collect_state_accesses(src)
    assert accesses == [
        ("read", "undeclared_session_var", 11, None),
        ("write", "flat_str", 12, "str"),
    ]


def test_state_visitor_function_local_shadow_does_not_disable_sibling_state_param() -> (
    None
):
    src = (
        "def _other_helper(self):\n"
        "    state = self.state\n"
        "    state['ignored_internal'] = 1\n"
        "\n"
        "def _mutate_session(state: dict):\n"
        "    state['undeclared_var'] = 'x'\n"
    )
    accesses = _collect_state_accesses(src)
    assert accesses == [("write", "undeclared_var", 6, "str")]


def test_state_visitor_tracks_rebound_state_alias_in_statement_order() -> None:
    src = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = callback_context.state\n"
        "    val = state.get('undeclared_key')\n"
        "    state = dict(state)\n"
        "    state['local_copy_only'] = val\n"
    )
    accesses = _collect_state_accesses(src)
    assert accesses == [("read", "undeclared_key", 3, None)]


def test_v100_recognizes_session_builtin_and_predefined_variables(
    tmp_path: typing.Any,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"name": "test-app", "variableDeclarations": [{"name": "flat_str", "schema": {"type": "STRING"}}], "predefinedVariableDeclarations": [{"name": "channel", "schema": {"type": "STRING"}}]}'
    )
    _clear_schema_cache()
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )
    f = tmp_path / "python_code.py"
    f.write_text(
        "def cb(callback_context):\n"
        "    sess = callback_context.state.get('_session', {})\n"
        "    meta = callback_context.state.get('session_metadata', {})\n"
        "    dt = callback_context.state.get('current_date')\n"
        "    ch = callback_context.state.get('channel')\n"
        "    bad = callback_context.state.get('missing_var')\n"
    )
    results = CallbackVariableDeclared().check(f, f.read_text(), ctx)
    assert len(results) == 1
    assert "missing_var" in results[0].message


def test_v104_recognizes_callback_substituted_instruction_placeholders(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    agent_dir = tmp_path / "agents" / "Payment_Agent"
    cb_dir = agent_dir / "before_model_callbacks" / "before_model_callback"
    cb_dir.mkdir(parents=True, exist_ok=True)
    (cb_dir / "python_code.py").write_text(
        "# '{unhandled_var}' in a comment must not count as substitution\n"
        "EXAMPLES = ['{bare_list_var}']\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    unhandled_var = 'local'\n"
        "    _ = f'f-string {unhandled_var} must not count as substitution'\n"
        "    llm_request.config.system_instruction = (\n"
        "        llm_request.config.system_instruction.replace(\n"
        "            '{methods_on_file}', 'Visa ending 1234'\n"
        "        )\n"
        "    )\n"
    )
    after_cb = agent_dir / "after_agent_callbacks" / "cb" / "python_code.py"
    after_cb.parent.mkdir(parents=True, exist_ok=True)
    after_cb.write_text(
        "def after_agent_callback(callback_context):\n"
        "    return 'x'.replace('{after_cb_var}', 'y')\n"
    )
    (agent_dir / "helpers.py").write_text("LOG = '{helper_var}'\n")

    instr_path = agent_dir / "instruction.txt"
    instr_path.write_text(
        "Available payment methods: {methods_on_file}\n"
        "Bare list in before_model: {bare_list_var}\n"
        "After callback replace: {after_cb_var}\n"
        "Helper file literal: {helper_var}\n"
        "Repeated undeclared on one line: {unhandled_var} and {unhandled_var}\n"
    )
    v104_results = InstructionVariableRef().check(
        instr_path, instr_path.read_text(), var_context
    )
    flagged = [r.message for r in v104_results]
    assert len(flagged) == 4  # noqa: PLR2004
    assert not any("methods_on_file" in m for m in flagged)
    assert any("bare_list_var" in m for m in flagged)
    assert any("after_cb_var" in m for m in flagged)
    assert any("helper_var" in m for m in flagged)
    assert any("unhandled_var" in m for m in flagged)


def test_predefined_only_app_runs_v100_through_v104(
    tmp_path: typing.Any,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"name": "test-app", "predefinedVariableDeclarations": ['
        '{"name": "channel", "schema": {"type": "STRING"}}, '
        '{"name": "caller", "schema": {"type": "OBJECT", '
        '"properties": {"tier": {"type": "STRING"}}}}'
        "]}"
    )
    _clear_schema_cache()
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )
    cb = tmp_path / "python_code.py"
    cb.write_text(
        "def cb(callback_context):\n"
        "    _ = callback_context.state.get('channel')\n"
        "    callback_context.state['channel'] = 123\n"
        "    _ = callback_context.state.get('caller.missing_prop')\n"
        "    _ = callback_context.state.get('tier')\n"
        "    _ = callback_context.state.get('undeclared_var')\n"
    )
    assert len(CallbackVariableDeclared().check(cb, cb.read_text(), ctx)) == 1
    assert len(CallbackVariableTypeMatch().check(cb, cb.read_text(), ctx)) == 1
    assert (
        len(CallbackNestedPropertyExists().check(cb, cb.read_text(), ctx)) == 1
    )
    assert len(CallbackStaleFlatVar().check(cb, cb.read_text(), ctx)) == 1

    instr = tmp_path / "agents" / "Root" / "instruction.txt"
    instr.parent.mkdir(parents=True, exist_ok=True)
    instr.write_text("Channel {channel}, tier {caller.tier}, bad {missing}.\n")
    v104 = InstructionVariableRef().check(instr, instr.read_text(), ctx)
    assert len(v104) == 1
    assert "missing" in v104[0].message


def test_v103_instruction_deduplicates_same_line_refs(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    instr_path = tmp_path / "agents" / "Root_Agent" / "instruction.txt"
    instr_path.parent.mkdir(parents=True, exist_ok=True)
    instr_path.write_text(
        "Check {auth_status} before using {auth_status} again.\n"
    )
    v103_results = InstructionStaleFlatVar().check(
        instr_path, instr_path.read_text(), var_context
    )
    assert len(v103_results) == 1
    assert "customer.auth_status" in v103_results[0].message


def test_v100_eval_validates_milestone_step_inject_variables(
    tmp_path: typing.Any, var_context: LintContext
) -> None:
    rule = EvalVariableDeclared()
    f = tmp_path / "evals" / "simulations" / "sim.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "evals:\n"
        "  - name: sim_1\n"
        "    steps:\n"
        "      - goal: 'Greet'\n"
        "        inject_variables:\n"
        "          flat_str: 'ok'\n"
        "          undeclared_sim_var: 'bad'\n"
    )
    results = rule.check(f, f.read_text(), var_context)
    assert len(results) == 1
    assert "undeclared_sim_var" in results[0].message


# ── Tool writers (V105-V106) ─────────────────────────────────────────────


def _writer_app(
    tmp_path: typing.Any,
    tools: dict[str, str],
    *,
    mode: str | None = None,
    options: dict | None = None,
) -> LintContext:
    """Writes an app with the given tool bodies and returns its context."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = {
        "name": "test-app",
        "rootAgent": "Root_Agent",
        "variableDeclarations": [
            {"name": "status", "schema": {"type": "STRING"}},
            {"name": "item", "schema": {"type": "STRING"}},
            {"name": "zip", "schema": {"type": "STRING"}},
            {"name": "notes", "schema": {"type": "STRING"}},
            {
                "name": "order",
                "schema": {
                    "type": "OBJECT",
                    "properties": {"order_id": {"type": "STRING"}},
                },
            },
        ],
    }
    if mode:
        config["toolExecutionMode"] = mode
    (tmp_path / "app.json").write_text(json.dumps(config))
    for name, body in tools.items():
        code = tmp_path / "tools" / name / "python_function" / "python_code.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text(body)
    _clear_schema_cache()
    _clear_writer_cache()
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
        options=options or {},
    )


def _tool_path(tmp_path: typing.Any, name: str) -> typing.Any:
    return tmp_path / "tools" / name / "python_function" / "python_code.py"


_LOOKUP = (
    "def lookup(n):\n"
    "    context.state['order'] = {'order_id': n}\n"
    "    context.state['status'] = 'Processing'\n"
)
_CANCEL = "def cancel():\n    context.state['status'] = 'Canceled'\n"


def test_v105_flags_variable_written_by_two_tools(tmp_path: typing.Any) -> None:
    ctx = _writer_app(tmp_path, {"lookup": _LOOKUP, "cancel": _CANCEL})
    rule_obj = ToolVariableSingleWriter()
    for tool, other in (("lookup", "cancel"), ("cancel", "lookup")):
        path = _tool_path(tmp_path, tool)
        results = rule_obj.check(path, path.read_text(), ctx)
        assert len(results) == 1
        assert results[0].rule_id == "V105"
        assert "'status'" in results[0].message
        assert other in results[0].message


def test_v105_single_writer_and_sequential_mode_are_quiet(
    tmp_path: typing.Any,
) -> None:
    ctx = _writer_app(
        tmp_path,
        {
            "lookup": _LOOKUP,
            "verify": "def v():\n    context.state['zip'] = 'x'\n",
        },
    )
    path = _tool_path(tmp_path, "lookup")
    assert ToolVariableSingleWriter().check(path, path.read_text(), ctx) == []

    ctx = _writer_app(
        tmp_path, {"lookup": _LOOKUP, "cancel": _CANCEL}, mode="SEQUENTIAL"
    )
    assert ToolVariableSingleWriter().check(path, path.read_text(), ctx) == []


def test_v105_ignore_option_and_callbacks_not_counted(
    tmp_path: typing.Any,
) -> None:
    first = tmp_path / "ignored"
    ctx = _writer_app(
        first,
        {"lookup": _LOOKUP, "cancel": _CANCEL},
        options={"V105": {"ignore": ["status"]}},
    )
    path = _tool_path(first, "lookup")
    assert ToolVariableSingleWriter().check(path, path.read_text(), ctx) == []

    # A before_model callback finishes before any tool runs, so no race.
    second = tmp_path / "callback"
    ctx = _writer_app(second, {"lookup": _LOOKUP})
    path = _tool_path(second, "lookup")
    callback = (
        second
        / "agents"
        / "Root_Agent"
        / "before_model_callbacks"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    callback.parent.mkdir(parents=True, exist_ok=True)
    callback.write_text(
        "def cb(callback_context):\n    callback_context.state['status'] = ''\n"
    )
    assert ToolVariableSingleWriter().check(path, path.read_text(), ctx) == []


@pytest.mark.parametrize("kind", ["before_tool", "after_tool"])
def test_v105_counts_tool_callback_writes(
    tmp_path: typing.Any, kind: str
) -> None:
    ctx = _writer_app(tmp_path, {"lookup": _LOOKUP})
    callback = (
        tmp_path
        / "agents"
        / "Root_Agent"
        / f"{kind}_callbacks"
        / f"{kind}_callbacks_01"
        / "python_code.py"
    )
    callback.parent.mkdir(parents=True, exist_ok=True)
    callback.write_text(
        "def cb(tool, input, callback_context):\n"
        "    callback_context.state['status'] = tool.name\n"
    )
    _clear_writer_cache()
    path = _tool_path(tmp_path, "lookup")
    results = ToolVariableSingleWriter().check(path, path.read_text(), ctx)
    assert len(results) == 1
    assert "'status'" in results[0].message
    assert f"Root_Agent {kind} callback" in results[0].message


@pytest.mark.parametrize(
    "write",
    [
        "context.state['order']['order_id'] = ''",
        "context.state.pop('order', None)",
        "del context.state['order']",
    ],
)
def test_v105_counts_nested_pop_and_del_writes(
    tmp_path: typing.Any, write: str
) -> None:
    ctx = _writer_app(
        tmp_path, {"lookup": _LOOKUP, "reset": f"def r():\n    {write}\n"}
    )
    path = _tool_path(tmp_path, "reset")
    results = ToolVariableSingleWriter().check(path, path.read_text(), ctx)
    assert len(results) == 1
    assert "'order'" in results[0].message
    assert "lookup" in results[0].message


def test_v105_local_dict_and_reads_are_not_writes(tmp_path: typing.Any) -> None:
    body = (
        "def r():\n"
        "    result = {}\n"
        "    result['order'] = context.state.get('order')\n"
        "    return context.state['order']['order_id']\n"
    )
    ctx = _writer_app(tmp_path, {"lookup": _LOOKUP, "read": body})
    path = _tool_path(tmp_path, "lookup")
    assert ToolVariableSingleWriter().check(path, path.read_text(), ctx) == []


def test_v106_flags_tool_writing_many_flat_variables(
    tmp_path: typing.Any,
) -> None:
    body = (
        "def lookup(n):\n"
        "    context.state['status'] = 'Processing'\n"
        "    context.state['item'] = 'lamp'\n"
        "    context.state['zip'] = '10001'\n"
        "    context.state['order'] = {'order_id': n}\n"
    )
    ctx = _writer_app(tmp_path, {"lookup": body})
    path = _tool_path(tmp_path, "lookup")
    results = ToolFlatVariableGroup().check(path, path.read_text(), ctx)
    assert len(results) == 1
    assert results[0].rule_id == "V106"
    assert "3 flat variables" in results[0].message
    assert "order" not in results[0].message.split("(")[1]

    ctx = _writer_app(
        tmp_path,
        {"lookup": body},
        options={"V106": {"max_flat_variables": 3}},
    )
    assert ToolFlatVariableGroup().check(path, path.read_text(), ctx) == []


def test_v106_two_flat_variables_are_fine(tmp_path: typing.Any) -> None:
    ctx = _writer_app(tmp_path, {"lookup": _LOOKUP})
    path = _tool_path(tmp_path, "lookup")
    assert ToolFlatVariableGroup().check(path, path.read_text(), ctx) == []
