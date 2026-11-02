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

"""Tests for variable lint rule coverage (V100-V103) and state AST extraction."""

import typing

import pytest

from cxas_scrapi.utils.lint_rules.variables import (
    CallbackNestedPropertyExists,
    CallbackStaleFlatVar,
    CallbackVariableDeclared,
    CallbackVariableTypeMatch,
    EvalVariableDeclared,
    ToolVariableDeclared,
    ToolVariableTypeMatch,
    _clear_schema_cache,
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
