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

"""Unit tests for offline tool and single-callback evaluation modes."""

import argparse
import asyncio
import json
import typing
from unittest.mock import patch

import google.auth
import pandas as pd
import pytest
from google.auth.exceptions import DefaultCredentialsError

from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.evals.callback_evals import CallbackEvals
from cxas_scrapi.evals.tool_evals import ToolEvals, _OfflineMockToolsDispatcher


def test_single_callback_offline_nested_layout(tmp_path: typing.Any) -> None:
    """Tests test_single_callback_offline on nested callback directory layout
    with zero network access.
    """
    cb_dir = (
        tmp_path
        / "agents"
        / "pilot_agent"
        / "before_model_callbacks"
        / "cb_guard"
    )
    cb_dir.mkdir(parents=True)
    agent_json = tmp_path / "agents" / "pilot_agent" / "pilot_agent.json"
    agent_json.write_text(
        json.dumps({"displayName": "Pilot Agent"}), encoding="utf-8"
    )

    code_file = cb_dir / "python_code.py"
    code_file.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['visited'] = True\n"
        "    return None\n",
        encoding="utf-8",
    )

    test_file = cb_dir / "test.py"
    test_file.write_text(
        "from python_code import before_model_callback\n"
        "from cxas_scrapi.utils.callback_libs import ("
        "CallbackContext, LlmRequest)\n\n"
        "def test_sets_visited():\n"
        "    ctx = CallbackContext(state={})\n"
        "    req = LlmRequest()\n"
        "    res = before_model_callback(ctx, req)\n"
        "    assert res is None\n"
        "    assert ctx.state['visited'] is True\n",
        encoding="utf-8",
    )

    evals = CallbackEvals()
    with patch("cxas_scrapi.evals.callback_evals.Agents") as mock_agents:
        # Match by displayName ("Pilot Agent") and shorthand ("before_model")
        df = evals.test_single_callback_for_agent(
            app_dir=str(tmp_path),
            agent_name="Pilot Agent",
            callback_type="before_model",
            test_file_path=str(test_file),
        )
        mock_agents.assert_not_called()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"
    assert df.iloc[0]["test_name"] == "test_sets_visited"


def test_single_callback_offline_flat_layout_and_json_ref(
    tmp_path: typing.Any,
) -> None:
    """Tests offline single callback resolution on flat layout and agent.json
    references.
    """
    agent_dir = tmp_path / "agents" / "router"
    flat_cb_dir = agent_dir / "after_tool_callbacks_audit"
    flat_cb_dir.mkdir(parents=True)

    code_file = flat_cb_dir / "python_code.py"
    code_file.write_text(
        "def after_tool_callback(tool, input_args, tool_context, response):\n"
        "    tool_context.state['last_tool'] = tool.name\n"
        "    return response\n",
        encoding="utf-8",
    )

    agent_json = agent_dir / "router.json"
    agent_json.write_text(
        json.dumps(
            {
                "displayName": "Router",
                "afterToolCallbacks": [
                    {
                        "pythonCode": (
                            "agents/router/after_tool_callbacks_audit/"
                            "python_code.py"
                        )
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    test_file = tmp_path / "test_after_tool.py"
    test_file.write_text(
        "from python_code import after_tool_callback\n"
        "from cxas_scrapi.utils.callback_libs import Tool, ToolContext\n\n"
        "def test_records_last_tool():\n"
        "    ctx = ToolContext(state={})\n"
        "    t = Tool(name='lookup_order', description='Lookup')\n"
        "    out = after_tool_callback(t, {'id': '1'}, ctx, {'ok': True})\n"
        "    assert out == {'ok': True}\n"
        "    assert ctx.state['last_tool'] == 'lookup_order'\n",
        encoding="utf-8",
    )

    evals = CallbackEvals()
    with patch("cxas_scrapi.evals.callback_evals.Agents") as mock_agents:
        df = evals.test_single_callback_offline(
            app_dir=str(tmp_path),
            agent_name="router",
            callback_type="after_tool_callback",
            test_file_path=str(test_file),
        )
        mock_agents.assert_not_called()

    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"


def test_single_callback_cli_exit_codes(tmp_path: typing.Any) -> None:
    """Tests CLI handler exit codes for test-single-callback with --app-dir."""
    cb_dir = (
        tmp_path / "agents" / "pilot" / "before_agent_callbacks" / "init_cb"
    )
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "def before_agent(ctx):\n    return {'ready': True}\n",
        encoding="utf-8",
    )

    pass_test = tmp_path / "test_pass.py"
    pass_test.write_text(
        "from python_code import before_agent\n"
        "def test_ok():\n"
        "    assert before_agent(None) == {'ready': True}\n",
        encoding="utf-8",
    )

    fail_test = tmp_path / "test_fail.py"
    fail_test.write_text(
        "from python_code import before_agent\n"
        "def test_bad():\n"
        "    assert before_agent(None) == {'ready': False}\n",
        encoding="utf-8",
    )

    pass_args = argparse.Namespace(
        app_name=None,
        app_dir=str(tmp_path),
        agent_name="pilot",
        callback_type="before_agent",
        test_file_path=str(pass_test),
        log_file=None,
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as exc_pass:
        cli_main.test_single_callback(pass_args)
    assert exc_pass.value.code == 0

    fail_args = argparse.Namespace(
        app_name=None,
        app_dir=str(tmp_path),
        agent_name="pilot",
        callback_type="before_agent",
        test_file_path=str(fail_test),
        log_file=None,
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as exc_fail:
        cli_main.test_single_callback(fail_args)
    assert exc_fail.value.code == 1

    missing_both = argparse.Namespace(
        app_name=None,
        app_dir=None,
        agent_name="pilot",
        callback_type="before_agent",
        test_file_path=str(pass_test),
        log_file=None,
        pytest_args=None,
    )
    with pytest.raises(SystemExit) as exc_missing:
        cli_main.test_single_callback(missing_both)
    assert exc_missing.value.code == 1


def _scaffold_local_app_with_tools(tmp_path: typing.Any) -> None:
    """Creates a local app directory with sync, async, and non-Python tools."""
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Offline Demo App",
                "variableDeclarations": [
                    {"name": "default_currency", "schema": {"default": "EUR"}}
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. Sync Python tool that accesses context.state and calls peer mock tool
    sync_dir = tmp_path / "tools" / "calc_balance"
    (sync_dir / "python_function").mkdir(parents=True)
    (sync_dir / "calc_balance.json").write_text(
        json.dumps(
            {
                "displayName": "Calculate Balance",
                "pythonFunction": {
                    "name": "calculate_balance",
                    "pythonCode": (
                        "tools/calc_balance/python_function/python_code.py"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    (sync_dir / "python_function" / "python_code.py").write_text(
        "def calculate_balance(account_id: str, bonus: float = 0.0) -> dict:\n"
        "    tier = context.state.get('tier', 'standard')\n"
        "    currency = context.state.get('default_currency', 'USD')\n"
        "    fx = tools.MCP_getRate({'currency': currency})\n"
        "    base = 100.0 if tier == 'gold' else 50.0\n"
        "    total = (base + bonus) * fx.get('multiplier', 1.0)\n"
        "    context.state['last_calculated'] = total\n"
        "    return {\n"
        "        'status': 'OK',\n"
        "        'account_id': account_id,\n"
        "        'balance': total,\n"
        "        'currency': currency,\n"
        "    }\n",
        encoding="utf-8",
    )

    # 2. Async Python tool
    async_dir = tmp_path / "tools" / "async_lookup"
    (async_dir / "python_function").mkdir(parents=True)
    (async_dir / "async_lookup.json").write_text(
        json.dumps(
            {
                "displayName": "Async Order Lookup",
                "pythonFunction": {"name": "lookup_order_async"},
            }
        ),
        encoding="utf-8",
    )
    (async_dir / "python_function" / "python_code.py").write_text(
        "import asyncio\n\n"
        "async def lookup_order_async(order_id: str) -> dict:\n"
        "    await asyncio.sleep(0.001)\n"
        "    context.set_variable('looked_up_order', order_id)\n"
        "    return {'order_id': order_id, 'status': 'SHIPPED'}\n",
        encoding="utf-8",
    )

    # 3. Non-Python tool (e.g. DataStore / Connector without python_code.py)
    non_py_dir = tmp_path / "tools" / "faq_datastore"
    non_py_dir.mkdir(parents=True)
    (non_py_dir / "faq_datastore.json").write_text(
        json.dumps(
            {
                "displayName": "FAQ Datastore",
                "dataStoreTool": {"name": "faq_datastore"},
            }
        ),
        encoding="utf-8",
    )


def test_offline_tool_tests_sync_async_context_and_skipped(
    tmp_path: typing.Any,
) -> None:
    """Verifies offline tool tests on sync and async Python tools, context/state
    mutation, Xbox v2 mock tool calls, and non-Python tool SKIPPED handling
    with zero GCP client instantiation.
    """
    _scaffold_local_app_with_tools(tmp_path)

    test_yaml = tmp_path / "tool_tests.yaml"
    test_yaml.write_text(
        """
tests:
  - name: "sync_tool_with_state_and_mock"
    tool: "Calculate Balance"
    args:
      account_id: "ACC-1"
      bonus: 25.0
    variables:
      tier: "gold"
      default_currency: null
    mock_tools:
      MCP_getRate:
        multiplier: 2.0
    expectations:
      response:
        - path: "$.status"
          operator: "equals"
          value: "OK"
        - path: "$.balance"
          operator: "equals"
          value: 250.0
        - path: "$.currency"
          operator: "equals"
          value: "EUR"
      variables:
        - path: "$.last_calculated"
          operator: "equals"
          value: 250.0

  - name: "async_tool_by_function_name"
    tool_name: "lookup_order_async"
    input:
      order_id: "ORD-42"
    expected_output:
      order_id: "ORD-42"
      status: "SHIPPED"
    expectations:
      variables:
        - path: "$.looked_up_order"
          operator: "equals"
          value: "ORD-42"

  - name: "non_python_tool_skipped"
    tool: "FAQ Datastore"
    args:
      query: "hours"
""",
        encoding="utf-8",
    )

    with (
        patch("cxas_scrapi.evals.tool_evals.Tools") as mock_tools_cls,
        patch.object(
            ToolEvals, "_mine_tool_data", wraps=ToolEvals._mine_tool_data
        ),
        patch(
            "cxas_scrapi.evals.tool_evals.Variables.__init__",
            return_value=None,
        ) as mock_vars_init,
        patch("cxas_scrapi.evals.tool_evals.Apps") as mock_apps_cls,
    ):
        tu = ToolEvals(app_dir=str(tmp_path), offline=True)
        cases = tu.load_tool_test_cases_from_file(str(test_yaml))
        df = tu.run_tool_tests(cases)

        mock_tools_cls.assert_not_called()
        mock_vars_init.assert_not_called()
        mock_apps_cls.assert_not_called()

    assert len(df) == 3
    by_name = {row["test_name"]: row for _, row in df.iterrows()}
    assert by_name["sync_tool_with_state_and_mock"]["status"] == "PASSED"
    assert by_name["async_tool_by_function_name"]["status"] == "PASSED"
    assert by_name["non_python_tool_skipped"]["status"] == "SKIPPED"
    assert "non-Python tool" in by_name["non_python_tool_skipped"]["errors"]


def test_offline_tool_tests_cli_pass_and_fail_exit_codes(
    tmp_path: typing.Any,
) -> None:
    """Verifies CLI exit code 0 when all offline tool tests pass and 1 when any
    assertion fails.
    """
    _scaffold_local_app_with_tools(tmp_path)

    pass_file = tmp_path / "pass_tools.yaml"
    pass_file.write_text(
        """
- tool_name: "async_lookup"
  parameters:
    order_id: "ORD-99"
  expected_response:
    order_id: "ORD-99"
    status: "SHIPPED"
""",
        encoding="utf-8",
    )

    fail_file = tmp_path / "fail_tools.yaml"
    fail_file.write_text(
        """
- tool_name: "async_lookup"
  parameters:
    order_id: "ORD-99"
  expected_response:
    order_id: "ORD-99"
    status: "CANCELLED"
""",
        encoding="utf-8",
    )

    pass_args = argparse.Namespace(
        app_name=None,
        app_dir=str(tmp_path),
        offline=True,
        test_file=str(pass_file),
        debug=False,
    )
    with pytest.raises(SystemExit) as exc_pass:
        cli_main.test_tools(pass_args)
    assert exc_pass.value.code == 0

    fail_args = argparse.Namespace(
        app_name=None,
        app_dir=str(tmp_path),
        offline=True,
        test_file=str(fail_file),
        debug=False,
    )
    with pytest.raises(SystemExit) as exc_fail:
        cli_main.test_tools(fail_args)
    assert exc_fail.value.code == 1


def test_qa_zero_network_and_credentials_requirement(
    tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that both test-single-callback and test-tools offline mode
    make zero GCP auth or network calls when GOOGLE_APPLICATION_CREDENTIALS
    points to a nonexistent path and google.auth.default raises an error.
    """

    def _forbid_auth(*_args: typing.Any, **_kwargs: typing.Any) -> typing.Any:
        raise DefaultCredentialsError(
            "Offline mode must never call google.auth"
        )

    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/creds.json"
    )
    monkeypatch.setattr(google.auth, "default", _forbid_auth)

    _scaffold_local_app_with_tools(tmp_path)
    ag_dir = tmp_path / "agents" / "billing_payments"
    cb_dir = ag_dir / "before_model_callbacks" / "cb_main"
    cb_dir.mkdir(parents=True)
    (ag_dir / "billing_payments.json").write_text(
        json.dumps({"displayName": "Billing & Payments"}), encoding="utf-8"
    )
    (cb_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['checked'] = True\n"
        "    return None\n",
        encoding="utf-8",
    )
    cb_test = cb_dir / "test.py"
    cb_test.write_text(
        "from python_code import before_model_callback\n"
        "from cxas_scrapi.utils.callback_libs import (\n"
        "    CallbackContext, LlmRequest\n"
        ")\n\n"
        "def test_cb():\n"
        "    ctx = CallbackContext(state={})\n"
        "    before_model_callback(ctx, LlmRequest())\n"
        "    assert ctx.state['checked'] is True\n",
        encoding="utf-8",
    )

    cb_evals = CallbackEvals()
    cb_df = cb_evals.test_single_callback_for_agent(
        app_dir=str(tmp_path),
        agent_name="Billing & Payments",
        callback_type="before_model",
        test_file_path=str(cb_test),
    )
    assert len(cb_df) == 1
    assert cb_df.iloc[0]["status"] == "PASSED"

    # Also test JSON test file loading + offline tool execution
    json_test_file = tmp_path / "tool_tests.json"
    json_test_file.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "name": "json_offline_calc",
                        "tool": "Calculate Balance",
                        "args": {"account_id": "ACC-9", "bonus": 10.0},
                        "variables": ["default_currency"],
                        "state": {"tier": "gold"},
                        "mock_tools": {"MCP_getRate": {"multiplier": 1.0}},
                        "expected_output": {
                            "status": "OK",
                            "account_id": "ACC-9",
                            "balance": 110.0,
                            "currency": "EUR",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    tu = ToolEvals(app_dir=str(tmp_path), offline=True)
    cases = tu.load_tool_test_cases_from_file(str(json_test_file))
    tool_df = tu.run_tool_tests(cases)
    assert len(tool_df) == 1
    assert tool_df.iloc[0]["status"] == "PASSED"


def test_qa_async_tool_inside_running_event_loop_and_error_dict(
    tmp_path: typing.Any,
) -> None:
    """Verifies async tool execution when an asyncio event loop is already
    running, and verifies unhandled exception (ERROR) vs returning an error
    dict ({'status': 'ERROR', 'agent_action': ...}).
    """
    _scaffold_local_app_with_tools(tmp_path)
    err_dir = tmp_path / "tools" / "error_handling_tool"
    (err_dir / "python_function").mkdir(parents=True)
    (err_dir / "error_handling_tool.json").write_text(
        json.dumps(
            {
                "displayName": "Error Handling Tool",
                "pythonFunction": {"name": "handle_or_raise"},
            }
        ),
        encoding="utf-8",
    )
    (err_dir / "python_function" / "python_code.py").write_text(
        "def handle_or_raise(should_raise: bool = False) -> dict:\n"
        "    if should_raise:\n"
        "        raise RuntimeError('backend timeout')\n"
        "    return {'status': 'ERROR', 'agent_action': 'escalate_to_human'}\n",
        encoding="utf-8",
    )

    tu = ToolEvals(app_dir=str(tmp_path), offline=True)
    cases = tu.load_tool_test_cases_from_data(
        [
            {
                "name": "async_in_running_loop",
                "tool": "Async Order Lookup",
                "args": {"order_id": "ORD-77"},
                "expected_output": {"order_id": "ORD-77", "status": "SHIPPED"},
            },
            {
                "name": "structured_error_dict_passes",
                "tool": "Error Handling Tool",
                "args": {"should_raise": False},
                "expected_status": "ERROR",
                "expected_output": {
                    "status": "ERROR",
                    "agent_action": "escalate_to_human",
                },
            },
            {
                "name": "unhandled_exception_records_error",
                "tool": "Error Handling Tool",
                "args": {"should_raise": True},
            },
        ]
    )

    async def _invoke_within_running_loop() -> pd.DataFrame:
        return tu.run_tool_tests(cases)

    df = asyncio.run(_invoke_within_running_loop())
    by_name = {row["test_name"]: row for _, row in df.iterrows()}
    assert by_name["async_in_running_loop"]["status"] == "PASSED"
    assert by_name["structured_error_dict_passes"]["status"] == "PASSED"
    assert by_name["unhandled_exception_records_error"]["status"] == "ERROR"
    assert (
        "backend timeout"
        in by_name["unhandled_exception_records_error"]["errors"]
    )


def test_qa_001_xbox_v2_dispatcher_rejects_keyword_arguments(
    tmp_path: typing.Any,
) -> None:
    """QA-001: In production CES Xbox v2 (system_library.py),
    `tools.<name>({...})` ONLY accepts a single positional dict (or None) and
    raises TypeError on keyword arguments (`tools.my_tool(foo='bar')`).
    _OfflineMockToolsDispatcher must enforce this contract and raise TypeError
    on keyword arguments.
    """
    dispatcher = _OfflineMockToolsDispatcher(
        mock_tools={"MCP_listCustomerCards": {"cards": []}}
    )
    with pytest.raises(TypeError):
        dispatcher.MCP_listCustomerCards(cardProductCategory="DEBIT")


def test_qa_002_test_tools_cli_fails_when_all_tests_skipped(
    tmp_path: typing.Any,
) -> None:
    """QA-002: `cxas test-tools --offline` says 'SKIPPED non-Python tools in
    offline mode do not fail if at least one test ran', yet exits 0 with
    'FINAL RESULT: PASS (All N tools passed)' when 100% of tests are SKIPPED
    and 0 tests actually ran/passed.
    """
    _scaffold_local_app_with_tools(tmp_path)
    only_skipped_file = tmp_path / "only_skipped.yaml"
    only_skipped_file.write_text(
        """
tests:
  - name: "datastore_only"
    tool: "FAQ Datastore"
    args:
      query: "hours"
""",
        encoding="utf-8",
    )

    args = argparse.Namespace(
        app_name=None,
        app_dir=str(tmp_path),
        offline=True,
        test_file=str(only_skipped_file),
        debug=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_main.test_tools(args)
    assert exc_info.value.code == 1


def test_qa_003_context_turn_index_does_not_leak_into_state(
    tmp_path: typing.Any,
) -> None:
    """QA-003: `known_ctx_fields` in `run_offline_tool_tests` has a leading-space
    typo (`" turnIndex"`), causing `context: {"turnIndex": 3}` to leak
    `turnIndex` into `context.state`.
    """
    tool_dir = tmp_path / "tools" / "inspect_ctx"
    (tool_dir / "python_function").mkdir(parents=True)
    (tool_dir / "inspect_ctx.json").write_text(
        json.dumps(
            {
                "displayName": "Inspect Context",
                "pythonFunction": {"name": "inspect_ctx"},
            }
        ),
        encoding="utf-8",
    )
    (tool_dir / "python_function" / "python_code.py").write_text(
        "def inspect_ctx() -> dict:\n"
        "    return {\n"
        "        'turn_index': context.turn_index,\n"
        "        'state_keys': sorted(list(context.state.keys())),\n"
        "    }\n",
        encoding="utf-8",
    )

    tu = ToolEvals(app_dir=str(tmp_path), offline=True)
    cases = tu.load_tool_test_cases_from_data(
        [
            {
                "name": "turn_index_not_in_state",
                "tool": "Inspect Context",
                "context": {"turnIndex": 3, "session_var": "active"},
                "expected_output": {
                    "turn_index": 3,
                    "state_keys": ["session_var"],
                },
            }
        ]
    )
    df = tu.run_tool_tests(cases)
    assert df.iloc[0]["status"] == "PASSED", df.iloc[0]["errors"]


def test_qa_004_single_callback_disambiguates_colocated_test_file(
    tmp_path: typing.Any,
) -> None:
    """QA-004: When an agent has multiple callbacks in the same stage (e.g.
    `before_model_callbacks/cb_guard/python_code.py` and
    `before_model_callbacks/cb_audit/python_code.py`, or flat
    `before_model_callbacks_01` and `before_model_callbacks_02`), passing
    `--test-file-path` inside one of those callback directories (alongside its
    `python_code.py`) should disambiguate and run that callback rather than
    failing with 'Multiple callbacks found'.
    """
    ag_dir = tmp_path / "agents" / "multi_agent"
    cb1 = ag_dir / "before_model_callbacks_01"
    cb2 = ag_dir / "before_model_callbacks_02"
    cb1.mkdir(parents=True)
    cb2.mkdir(parents=True)

    (cb1 / "python_code.py").write_text(
        "def before_model_callback(ctx, req):\n"
        "    ctx.state['cb'] = 1\n"
        "    return None\n",
        encoding="utf-8",
    )
    (cb2 / "python_code.py").write_text(
        "def before_model_callback(ctx, req):\n"
        "    ctx.state['cb'] = 2\n"
        "    return None\n",
        encoding="utf-8",
    )
    test_cb2 = cb2 / "test.py"
    test_cb2.write_text(
        "from python_code import before_model_callback\n"
        "from cxas_scrapi.utils.callback_libs import (\n"
        "    CallbackContext, LlmRequest\n"
        ")\n\n"
        "def test_cb2():\n"
        "    ctx = CallbackContext(state={})\n"
        "    before_model_callback(ctx, LlmRequest())\n"
        "    assert ctx.state['cb'] == 2\n",
        encoding="utf-8",
    )

    evals = CallbackEvals()
    df = evals.test_single_callback_for_agent(
        app_dir=str(tmp_path),
        agent_name="multi_agent",
        callback_type="before_model",
        test_file_path=str(test_cb2),
    )
    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"
