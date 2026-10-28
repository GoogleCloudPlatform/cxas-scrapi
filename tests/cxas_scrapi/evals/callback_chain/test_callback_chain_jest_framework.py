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

"""Tests for the Jest-style CES agent testing framework (matchers, mocks, snapshots, pytest plugin)."""

from __future__ import annotations

import copy
import datetime
import json
import pickle
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import cxas_scrapi
import cxas_scrapi.evals
import cxas_scrapi.evals.callback_chain as cc
import cxas_scrapi.evals.callback_evals as ce
from cxas_scrapi.evals.callback_chain import (
    AgentAssertionError,
    CallbackChainRunner,
    MockLlm,
    SessionSimulator,
    SnapshotManager,
    ToolSpy,
    expect_session,
    expect_stage,
    expect_turn,
    serialize_turn_snapshot,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    FunctionDeclaration,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    Part,
    Tool,
    ToolContext,
    UsageMetadata,
)

pytest_plugins = ["pytester"]


def _create_banking_agent_fixture(tmp_path: Path) -> Path:
    """Builds a self-contained retail banking agent directory for end-to-end testing."""
    app_dir = tmp_path / "banking_app"
    agent_dir = app_dir / "agents" / "banking_agent"
    cb1_dir = agent_dir / "before_agent_callbacks" / "before_agent_callbacks_01"
    cb2_dir = agent_dir / "before_agent_callbacks" / "before_agent_callbacks_02"
    bm_dir = agent_dir / "before_model_callbacks" / "before_model_guard"
    tool_dir = app_dir / "tools" / "get_balances"

    for d in (cb1_dir, cb2_dir, bm_dir, tool_dir):
        d.mkdir(parents=True, exist_ok=True)

    app_config = {
        "displayName": "banking_app",
        "rootAgent": "banking_agent",
        "variableDeclarations": [
            {
                "name": "current_account",
                "description": "Active account identifier for balance lookup",
                "schema": {"type": "STRING", "default": "UNSET"},
            },
            {
                "name": "customer_tier",
                "description": "Customer tier level",
                "schema": {"type": "STRING", "default": "standard"},
            },
        ],
    }
    (app_dir / "app.json").write_text(
        json.dumps(app_config, indent=2), encoding="utf-8"
    )

    (cb1_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['auth_verified'] = True\n"
        "    return None\n",
        encoding="utf-8",
    )
    (cb2_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    acct = callback_context.state.get('raw_account_id', 'ACC-100020003000')\n"
        "    callback_context.state['current_account'] = acct\n"
        "    callback_context.state.pop('raw_secret_token', None)\n"
        "    return None\n",
        encoding="utf-8",
    )
    (bm_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['bm_ran'] = True\n"
        "    return None\n",
        encoding="utf-8",
    )

    (agent_dir / "instruction.txt").write_text(
        "You are the Retail Banking Assistant.\n"
        "Active account: {current_account}\n"
        "Use {@TOOL: get_balances} to fetch balances.\n",
        encoding="utf-8",
    )

    (tool_dir / "get_balances.json").write_text(
        json.dumps(
            {
                "name": "get_balances",
                "displayName": "get_balances",
                "description": "Fetches customer account balances",
                "pythonFunction": {
                    "name": "get_balances",
                    "pythonCode": "tools/get_balances/python_code/code.py",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (tool_dir / "python_code").mkdir(parents=True, exist_ok=True)
    (tool_dir / "python_code" / "code.py").write_text(
        "def run(balance_flag: int = 0) -> dict:\n"
        "    return {'status': 200, 'balance': 1250}\n",
        encoding="utf-8",
    )

    agent_config = {
        "name": "banking_agent",
        "displayName": "banking_agent",
        "instruction": "agents/banking_agent/instruction.txt",
        "tools": ["get_balances"],
        "beforeAgentCallbacks": [
            {
                "name": "before_agent_callbacks_01",
                "pythonCode": "agents/banking_agent/before_agent_callbacks/before_agent_callbacks_01/python_code.py",
            },
            {
                "name": "before_agent_callbacks_02",
                "pythonCode": "agents/banking_agent/before_agent_callbacks/before_agent_callbacks_02/python_code.py",
            },
        ],
        "beforeModelCallbacks": [
            {
                "name": "before_model_guard",
                "pythonCode": "agents/banking_agent/before_model_callbacks/before_model_guard/python_code.py",
            }
        ],
    }
    (agent_dir / "banking_agent.json").write_text(
        json.dumps(agent_config, indent=2), encoding="utf-8"
    )
    return agent_dir


# ---------------------------------------------------------------------------
# 1. Public Exports & Codebase Symbol Alignments
# ---------------------------------------------------------------------------


def test_public_exports_across_all_package_levels() -> None:
    expected_symbols = [
        "AgentAssertionError",
        "StageExpectation",
        "TurnExpectation",
        "SessionExpectation",
        "expect_stage",
        "expect_turn",
        "expect_session",
        "MockLlm",
        "ToolSpy",
        "SnapshotManager",
        "serialize_turn_snapshot",
        "serialize_stage_snapshot",
        "serialize_session_snapshot",
    ]
    for mod in (cc, ce, cxas_scrapi.evals, cxas_scrapi):
        for sym in expected_symbols:
            assert hasattr(mod, sym), f"{sym} missing on {mod.__name__}"
            assert sym in mod.__all__, (
                f"{sym} missing in {mod.__name__}.__all__"
            )


def test_symbol_alignments_on_traces_and_runner() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_exec",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['x'] = 1\n"
                    "    return None\n",
                ),
                (
                    "cb_short",
                    "def before_agent_callback(ctx):\n"
                    "    return Content.from_text('halt')\n",
                ),
            ]
        }
    )
    diff = runner.compare_stage_modes(
        "before_agent", initial_state={"seed": "ok"}
    )
    assert diff.has_divergence is False
    assert diff.has_divergence == diff.has_diff
    assert diff.per_callback_trace.steps[0].status == "EXECUTED"
    assert diff.per_callback_trace.steps[1].status == "SHORT_CIRCUITED"

    fail_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_boom",
                    "def before_agent_callback(ctx):\n"
                    "    raise ValueError('bad state')\n",
                )
            ]
        }
    )
    _, fail_trace = fail_runner.run_before_agent(CallbackContext())
    assert fail_trace.steps[0].status == "FAILED"

    with pytest.raises(ValueError, match="either `ctx` or `initial_state`"):
        runner.compare_stage_modes(
            "before_agent",
            ctx=CallbackContext(),
            initial_state={"a": 1},
        )


def test_run_turn_dual_signature_copy_pickle_and_hops() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "init_cb",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['init_ran'] = True\n"
                    "    return None\n",
                )
            ],
            "before_model": [
                (
                    "bm_cb",
                    "def before_model_callback(ctx, req):\n"
                    "    ctx.state['bm_ran'] = True\n"
                    "    return None\n",
                )
            ],
        }
    )

    # 1. Legacy positional (ctx, llm_request, mock_llm) unpacking — including with MockLlm()
    ctx = CallbackContext(state={"legacy": True})
    resp, legacy_trace = runner.run_turn(
        ctx,
        LlmRequest(contents=[Content.from_text("ping")]),
        MockLlm().default_reply("pong"),
    )
    assert resp is not None
    assert legacy_trace.final_state["bm_ran"] is True
    # Positional call preserves legacy default (include_agent_callbacks=False)
    assert "init_ran" not in legacy_trace.final_state

    # 2. High-level Jest-style keyword invocation returning trace-compatible result
    llm = MockLlm().on_hop(1).reply_text("hello from mock")
    trace = runner.run_turn(
        user_input="hi there",
        initial_state={"jest": True},
        llm_callable=llm,
    )
    assert trace.final_state["init_ran"] is True
    assert trace.final_state["bm_ran"] is True
    assert trace.user_input == "hi there"
    # Single-agent fallback indexing on agent_hops[0] while len(agent_hops) == 0, plus .hops
    assert len(trace.agent_hops) == 0
    assert not bool(trace.agent_hops)
    assert len(trace.hops) == 1
    assert trace.hops[0] is trace[1]
    assert (
        trace.agent_hops[0].llm_steps[0].llm_request_snapshot
        is trace.llm_steps[0].request
    )

    # 3. Copy, deepcopy, and pickle support on _TurnRunResult
    copied = copy.copy(trace)
    deep_copied = copy.deepcopy(trace)
    unpickled = pickle.loads(pickle.dumps(trace))
    assert copied.user_input == "hi there"
    assert deep_copied.final_state["init_ran"] is True
    assert unpickled.final_state["bm_ran"] is True

    # 4. Conflict validation
    with pytest.raises(ValueError, match="both `ctx` and `initial_state`"):
        runner.run_turn(ctx, initial_state={"x": 1})
    with pytest.raises(ValueError, match="llm_callable"):
        runner.run_turn(
            user_input="hi",
            llm_callable=llm,
            mock_llm=LlmResponse.from_parts([Part.from_text("x")]),
        )


# ---------------------------------------------------------------------------
# 2. End-to-End Banking Agent "Before" and "After" Example
# ---------------------------------------------------------------------------


def test_banking_agent_balance_and_security_end_to_end(
    tmp_path: Path,
) -> None:
    banking_dir = _create_banking_agent_fixture(tmp_path)
    initial_state = {
        "raw_account_id": "ACC-100020003000",
        "raw_secret_token": "SECRET_P1",
    }
    snap_dir = tmp_path / "__snapshots__"

    runner = CallbackChainRunner.from_agent_dir(banking_dir)

    # 1. Stage-level mode parity assertion
    expect_stage(
        runner.compare_stage_modes("before_agent", initial_state=initial_state)
    ).to_be_mode_consistent()

    # 2. Full-turn lifecycle execution with declarative MockLlm
    llm = (
        MockLlm()
        .on_hop(1)
        .call_tool("get_balances", balance_flag=0)
        .on_hop(2)
        .reply_text("Your account balance is 1250 EUR.")
    )
    trace = runner.run_turn(
        user_input="What is my current account balance?",
        initial_state=initial_state,
        llm_callable=llm,
        tool_impls={"get_balances": lambda **kw: {"status": 200}},
    )

    # Verify imperative properties from the "Before" example also hold
    assert trace.before_agent_trace is not None
    assert trace.before_agent_trace.steps[1].status == "EXECUTED"
    prompt_str = str(trace.agent_hops[0].llm_steps[0].llm_request_snapshot)
    assert "current_account" in prompt_str
    assert "SECRET_P1" not in prompt_str

    # 3. Fluent Jest-style semantic assertions + snapshot lock
    expect_turn(
        trace, snapshot_dir=snap_dir
    ).to_succeed().to_have_executed_callback(
        "before_agent_callbacks_02", stage="before_agent"
    ).to_inject_state_update(
        "current_account", "ACC-100020003000"
    ).to_contain_in_system_instruction(
        "Retail Banking Assistant"
    ).not_.to_leak_in_prompt("SECRET_P1").to_have_called_tool(
        "get_balances", with_args={"balance_flag": 0}
    ).to_reply_matching(r"1250\s+EUR", regex=True).to_have_final_state(
        "current_account", "ACC-100020003000"
    ).to_match_snapshot("banking_balance_turn")

    # Second run against the written snapshot must pass identically
    expect_turn(trace, snapshot_dir=snap_dir).to_match_snapshot(
        "banking_balance_turn"
    )


# ---------------------------------------------------------------------------
# 3. StageExpectation Matchers & .not_ Chaining
# ---------------------------------------------------------------------------


def test_stage_expectation_matchers_and_not_chaining(tmp_path: Path) -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_one",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['step_1'] = 'done'\n"
                    "    ctx.state['existing'] = 'updated'\n"
                    "    ctx.state.pop('obsolete', None)\n"
                    "    tools.audit_log(event='start')\n"
                    "    return None\n",
                ),
                (
                    "cb_two",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['count'] = 42\n"
                    "    return Content.from_text('short-circuit reply')\n",
                ),
                (
                    "cb_three",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['never_runs'] = True\n"
                    "    return None\n",
                ),
            ]
        },
        mock_tools={"audit_log": lambda **kw: {"ok": True}},
        simulate_struct_numbers=False,
    )
    out, trace = runner.run_before_agent(
        CallbackContext(state={"existing": "old", "obsolete": "remove_me"})
    )

    # Pass both (out, trace) tuple and trace directly; test all spec-named stage matchers
    expect_stage(
        (out, trace), snapshot_dir=tmp_path
    ).to_succeed_without_errors().to_succeed().to_have_executed(
        "cb_one", "cb_two", exact_order=True
    ).not_.to_have_executed("cb_three").to_have_skipped(
        "cb_three"
    ).to_have_short_circuited(at_callback="cb_two").to_short_circuit(
        by="cb_two"
    ).to_return_override(Content).to_return_override(
        Content.from_text("short-circuit reply")
    ).not_.to_continue().to_set_state(
        step_1="done", count=42
    ).to_have_state_key("step_1", "done").to_have_added_state(
        "step_1", in_callback="cb_one", step_1="done"
    ).to_have_modified_state(
        "existing", in_callback="cb_one", existing="updated"
    ).to_have_removed_state(
        "obsolete", in_callback="cb_one"
    ).not_.to_have_state_key(
        "never_runs"
    ).not_.to_preserve_state().to_call_mock_tool(
        "audit_log", with_args={"event": "start"}, times=1
    ).not_.to_call_mock_tool(
        "non_existent_tool"
    ).to_have_no_warnings().not_.to_be_lint_clean().to_be_lint_clean(
        ignore_codes=["C011"]
    ).to_match_snapshot("stage_short_circuit")

    # Verify None value check in to_have_state_key
    trace.final_state["nullable"] = None
    expect_stage(trace).to_have_state_key("nullable", None)
    with pytest.raises(AgentAssertionError):
        expect_stage(trace).to_have_state_key("nullable", "non_null")

    # Double negation (.not_.not_) cancels out back to positive
    expect_stage(trace).not_.not_.to_succeed()

    # Preserve state check on no-op stage
    noop_runner = CallbackChainRunner.from_callbacks(
        {
            "after_agent": [
                ("noop", "def after_agent_callback(ctx): return None")
            ]
        }
    )
    ctx_noop = CallbackContext(state={"keep": "same"})
    noop_runner.run_after_agent(ctx_noop)
    _, noop_trace = noop_runner.run_after_agent(ctx_noop)
    expect_stage(noop_trace).to_continue().to_preserve_state()


def test_stage_expectation_failure_and_mode_divergence() -> None:
    err_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_crash",
                    "def before_agent_callback(ctx):\n"
                    "    raise KeyError('missing_customer_id')\n",
                )
            ]
        }
    )
    _, err_trace = err_runner.run_before_agent(CallbackContext())
    expect_stage(err_trace).to_fail(
        with_callback="cb_crash",
        outcome="fallback",
        message_contains="missing_customer_id",
    ).not_.to_succeed()

    div_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_alias",
                    "def before_agent_callback(ctx):\n"
                    "    shared = ['a']\n"
                    "    ctx.state['list_a'] = shared\n"
                    "    ctx.state['list_b'] = shared\n"
                    "    return None\n",
                ),
                (
                    "cb_mutate",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['list_a'].append('b')\n"
                    "    return None\n",
                ),
            ]
        }
    )
    diff_report = div_runner.compare_stage_modes("before_agent")
    expect_stage(diff_report).not_.to_be_mode_consistent()

    with pytest.raises(AgentAssertionError) as exc_info:
        expect_stage(diff_report).to_be_mode_consistent()
    assert "list_b" in str(exc_info.value)

    with pytest.raises(AgentAssertionError, match="ModeDiffReport"):
        expect_stage(err_trace).to_be_mode_consistent()

    with pytest.raises(TypeError, match="expect_stage"):
        expect_stage("not_a_trace")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Self-Diagnosing AgentAssertionError Root-Cause Lineage (Callbacks & Tools)
# ---------------------------------------------------------------------------


def test_agent_assertion_error_traces_root_cause_callback_and_tool() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_setup",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['account_status'] = 'ACTIVE'\n"
                    "    return None\n",
                ),
                (
                    "cb_corruptor",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['account_status'] = 'SUSPENDED_BY_BUG'\n"
                    "    return None\n",
                ),
                (
                    "cb_unrelated",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['other_flag'] = 1\n"
                    "    return None\n",
                ),
            ]
        }
    )
    trace = runner.run_turn(
        user_input="check status",
        initial_state={},
        llm_callable=MockLlm().default_reply("status checked"),
    )

    with pytest.raises(AgentAssertionError) as exc_info:
        expect_turn(trace).to_set_state(account_status="ACTIVE")

    err = exc_info.value
    assert err.root_cause_callback == "cb_corruptor"
    assert any("cb_setup" in line for line in err.state_mutation_history)
    assert any(
        "cb_corruptor" in line and "SUSPENDED_BY_BUG" in line
        for line in err.state_mutation_history
    )
    assert "[Root-Cause Callback]: cb_corruptor" in str(err)
    assert "[State Mutation Lineage]:" in str(err)

    # Tool-induced state mutation attribution in lineage
    def mutating_tool(
        args: dict[str, Any], tool_ctx: ToolContext
    ) -> dict[str, Any]:
        tool_ctx.state["account_status"] = "MUTATED_BY_TOOL"
        return {"ok": True}

    trace_tool = runner.run_turn(
        user_input="mutate via tool",
        initial_state={},
        llm_callable=(
            MockLlm()
            .on_hop(1)
            .call_tool("update_status")
            .on_hop(2)
            .reply_text("done")
        ),
        tool_impls={"update_status": mutating_tool},
    )
    with pytest.raises(AgentAssertionError) as tool_exc:
        expect_turn(trace_tool).to_have_final_state("account_status", "ACTIVE")
    assert tool_exc.value.root_cause_callback == "tool:update_status"
    assert any(
        "tool:update_status" in line
        for line in tool_exc.value.state_mutation_history
    )


# ---------------------------------------------------------------------------
# 5. TurnExpectation Matchers (Prompt, Tools, Security, Single-Part, Hops)
# ---------------------------------------------------------------------------


def test_turn_expectation_all_matchers() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_model": [
                (
                    "bm_setup",
                    "def before_model_callback(ctx, req):\n"
                    "    req.config.system_instruction = Content.from_text(\n"
                    "        'System prompt for verified user. Unresolved: {missing_var}'\n"
                    "    )\n"
                    "    return None\n",
                )
            ],
            "before_tool": [
                (
                    "bt_guard",
                    "def before_tool_callback(tool, tool_input, ctx):\n"
                    "    if tool.name == 'blocked_tool':\n"
                    "        return {'blocked': True}\n"
                    "    return None\n",
                )
            ],
        }
    )

    spy = ToolSpy(returns={"balance": 500, "currency": "EUR"})
    llm = (
        MockLlm()
        .on_hop(1)
        .call_tools(
            [
                (
                    "get_balance",
                    {"account_id": "ACC_1", "include_pending": True},
                ),
                ("blocked_tool", {"reason": "test"}),
            ]
        )
        .on_hop(2)
        .reply_text("Your balance is 500 EUR.")
    )

    trace = runner.run_turn(
        user_input="Show balance",
        initial_state={"tier": "gold"},
        llm_callable=llm,
        tool_impls={
            "get_balance": spy,
            "blocked_tool": lambda **kw: {"blocked": False},
        },
        simulate_struct_numbers=False,
    )

    expect_turn(trace).to_succeed().to_invoke_llm(
        times=2
    ).not_.to_skip_llm().to_contain_in_system_instruction(
        "System prompt for verified user", hop=1
    ).to_have_in_prompt(
        "System prompt for verified user", hop=1
    ).to_have_in_prompt(
        re.compile(r"verified\s+user"), hop=1
    ).to_have_unresolved_placeholders(hop=1).to_have_called_tool(
        "get_balance",
        with_args={"account_id": "ACC_1", "include_pending": True},
        times=1,
    ).to_have_called_tool(
        "get_balance",
        partial_args={"account_id": "ACC_1"},
        times=1,
    ).not_.to_have_called_tool(
        "get_balance",
        with_args={"account_id": "ACC_1"},
    ).to_have_tool_response(
        "get_balance", balance=500, currency="EUR"
    ).to_short_circuit_tool(
        "blocked_tool", by="bt_guard"
    ).to_have_short_circuited_at("before_tool", "bt_guard").to_reply_matching(
        r"500\s+EUR", regex=True
    ).to_reply_with(re.compile(r"500\s+EUR")).to_set_state(
        tier="gold"
    ).to_have_final_state("tier", "gold").to_have_state_key("tier", "gold")

    spy.assert_called_once().assert_called_with(
        account_id="ACC_1", include_pending=True
    ).assert_called_with_partial(account_id="ACC_1")

    with pytest.raises(AgentAssertionError, match="hop 99"):
        expect_turn(trace).not_.to_have_in_prompt("anything", hop=99)
    with pytest.raises(ValueError, match="1-based"):
        expect_turn(trace).to_have_in_prompt("anything", hop=0)


def test_inject_state_update_does_not_false_positive_on_prompt_value() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['flag'] = 1\n"
                    "    return None\n",
                )
            ]
        }
    )
    req = LlmRequest(
        contents=[Content.from_text("I have 1 question", role="user")]
    )
    ctx = CallbackContext(state={})
    _, trace = runner.run_turn(
        ctx, req, MockLlm().default_reply("ok"), include_agent_callbacks=True
    )
    expect_turn(trace).not_.to_inject_state_update("flag")
    with pytest.raises(AgentAssertionError):
        expect_turn(trace).to_inject_state_update("flag")


def test_leak_in_prompt_detects_tool_response_and_tool_schema_leaks(
    tmp_path: Path,
) -> None:
    agent_dir = _create_banking_agent_fixture(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(agent_dir)
    llm = (
        MockLlm()
        .on_hop(1)
        .call_tool("get_balances", balance_flag=0)
        .on_hop(2)
        .reply_text("done")
    )
    trace = runner.run_turn(
        user_input="check balance",
        initial_state={},
        llm_callable=llm,
        tool_impls={
            "get_balances": lambda **kw: {
                "secret": "SECRET_IN_FUNCTION_RESPONSE"
            }
        },
    )
    expect_turn(trace).to_leak_in_prompt("SECRET_IN_FUNCTION_RESPONSE")
    with pytest.raises(AgentAssertionError):
        expect_turn(trace).not_.to_leak_in_prompt("SECRET_IN_FUNCTION_RESPONSE")


def test_turn_expectation_single_part_on_tool_call_and_system_tools() -> None:
    runner = CallbackChainRunner.from_callbacks({})

    single_part_resp = LlmResponse.from_parts(
        [
            Part(text="thinking...", thought=True),
            Part.from_function_call(
                name="transfer_to_agent", args={"agent_name": "specialist"}
            ),
        ]
    )
    trace_ok = runner.run_turn(
        user_input="transfer me",
        llm_callable=MockLlm().on_hop(1).respond(single_part_resp),
    )
    expect_turn(trace_ok).to_have_single_part_on_tool_call(
        "transfer_to_agent"
    ).to_have_only_single_part_on_client_function(
        "transfer_to_agent"
    ).to_have_Only_single_part_On_client_function(
        "transfer_to_agent"
    ).to_have_transferred_to("specialist").to_transfer_to("specialist")

    multi_part_resp = LlmResponse.from_parts(
        [
            Part.from_text("Transferring you now..."),
            Part.from_function_call(
                name="transfer_to_agent", args={"agent_name": "specialist"}
            ),
        ]
    )
    trace_bad = runner.run_turn(
        user_input="transfer me",
        llm_callable=MockLlm().on_hop(1).respond(multi_part_resp),
    )
    expect_turn(trace_bad).not_.to_have_single_part_on_tool_call(
        "transfer_to_agent"
    )
    with pytest.raises(AgentAssertionError, match="single non-thought part"):
        expect_turn(trace_bad).to_have_single_part_on_tool_call(
            "transfer_to_agent"
        )

    trace_end = runner.run_turn(
        user_input="bye",
        llm_callable=MockLlm().on_hop(1).end_session(reason="user_goodbye"),
    )
    expect_turn(trace_end).to_end_session(
        with_params={"reason": "user_goodbye"}
    )


def test_turn_expectation_expose_and_exclude_tools(tmp_path: Path) -> None:
    agent_dir = _create_banking_agent_fixture(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(agent_dir)
    trace = runner.run_turn(
        user_input="hello",
        llm_callable=MockLlm().on_hop(1).reply_text("hi"),
    )
    expect_turn(trace).to_expose_tools("get_balances").to_exclude_tools(
        "non_existent_admin_tool"
    )


# ---------------------------------------------------------------------------
# 6. MockLlm & ToolSpy Advanced Features
# ---------------------------------------------------------------------------


def test_mock_llm_predicates_one_shot_sequential_and_turn_reset(
    tmp_path: Path,
) -> None:
    llm = (
        MockLlm()
        .when_agent("router_agent")
        .once()
        .transfer_to_agent("billing_agent")
        .when_prompt_contains("VIP_CUSTOMER")
        .reply_text("Welcome VIP!")
        .when_after_tool("lookup_invoice", contains="INV_99")
        .reply_text("Invoice INV_99 is paid.")
        .then_call_tool("lookup_invoice", invoice_id="INV_99")
        .then_reply_text("Sequential fallback")
    )

    with pytest.raises(ValueError, match="1-based"):
        llm.on_hop(0)

    # 1. One-shot predicate: when_agent("router_agent").once()
    r1 = llm(
        LlmRequest(contents=[Content.from_text("route me")]),
        CallbackContext(agent_name="router_agent"),
    )
    assert r1.content.parts[0].function_call.name == "transfer_to_agent"

    # 2. Predicate: when_prompt_contains("VIP_CUSTOMER")
    r2 = llm(
        LlmRequest(contents=[Content.from_text("User is VIP_CUSTOMER")]),
        CallbackContext(agent_name="main_agent"),
    )
    assert r2.content.parts[0].text == "Welcome VIP!"

    # 3. Sequential rule 1: then_call_tool("lookup_invoice") (router_agent one-shot is now exhausted)
    r3 = llm(
        LlmRequest(contents=[Content.from_text("check invoice")]),
        CallbackContext(agent_name="router_agent"),
    )
    assert r3.content.parts[0].function_call.name == "lookup_invoice"

    # 4. Sequential rule 2: then_reply_text("Sequential fallback")
    r4 = llm(
        LlmRequest(contents=[Content.from_text("next message")]),
        CallbackContext(agent_name="main_agent"),
    )
    assert r4.content.parts[0].text == "Sequential fallback"

    # 5. Exhausted rules raise descriptive AssertionError
    with pytest.raises(
        AssertionError, match="MockLlm has no response configured for hop 5"
    ):
        llm(
            LlmRequest(contents=[Content.from_text("unscripted")]),
            CallbackContext(agent_name="main_agent"),
        )

    assert llm.call_count == 5
    llm.reset()
    assert llm.call_count == 0

    # 6. Per-turn hop reset & .on_turn(t).on_hop(h) in SessionSimulator
    agent_dir = _create_banking_agent_fixture(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(agent_dir)
    turn_llm = (
        MockLlm()
        .on_hop(1)
        .reply_text("default first hop")
        .on_turn(2)
        .on_hop(1)
        .reply_text("turn two override")
    )
    sim = runner.simulate_session(llm=turn_llm)
    t1 = sim.send("turn 1")
    t2 = sim.send("turn 2")
    expect_turn(t1).to_reply_matching("default first hop")
    expect_turn(t2).to_reply_matching("turn two override")


def test_tool_spy_wraps_positional_returns_none_and_side_effects() -> None:
    # 1. Wrapping positional functions directly
    spy_add = ToolSpy(wraps=lambda a, b: a + b)
    assert spy_add(2, 3) == 5
    spy_echo = ToolSpy(wraps=lambda x: x)
    assert spy_echo("hello") == "hello"

    # 2. Explicit returns=None
    spy_none = ToolSpy(returns=None)
    assert spy_none() is None

    # 3. Exact vs partial assert_called_with
    def real_calc(amount: int, tax: int = 2) -> dict[str, Any]:
        return {"total": amount + tax}

    spy_wrap = ToolSpy(wraps=real_calc)
    out = spy_wrap(amount=10, tax=5)
    assert out == {"total": 15}
    spy_wrap.assert_called_once().assert_called_with(
        amount=10, tax=5
    ).assert_called_with_partial(amount=10)

    with pytest.raises(AgentAssertionError):
        spy_wrap.assert_called_with(amount=10)
    with pytest.raises(AgentAssertionError):
        spy_wrap.assert_not_called()

    # 4. Sequence / generator of side_effects and 1:1 exceptions alignment
    gen = (
        x
        for x in [
            {"attempt": 1},
            RuntimeError("transient network error"),
            lambda **kw: {"attempt": 3, "echo": kw.get("q")},
        ]
    )
    spy_seq = ToolSpy(side_effect=gen)
    assert spy_seq(q="first") == {"attempt": 1}
    with pytest.raises(RuntimeError, match="transient network error"):
        spy_seq(q="second")
    assert spy_seq(q="third") == {"attempt": 3, "echo": "third"}
    assert spy_seq.call_count == 3
    assert len(spy_seq.exceptions) == 3
    assert spy_seq.exceptions[0] is None
    assert isinstance(spy_seq.exceptions[1], RuntimeError)
    assert spy_seq.exceptions[2] is None
    assert len(spy_seq.return_values) == 2

    # Reset rewinds generator side_effect
    spy_seq.reset()
    spy_seq.assert_not_called()
    assert spy_seq(q="restarted") == {"attempt": 1}


# ---------------------------------------------------------------------------
# 7. SessionExpectation & Multi-Agent Transfers
# ---------------------------------------------------------------------------


def test_session_expectation_multi_turn_transfers_and_budget(
    tmp_path: Path,
) -> None:
    router_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "router_ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['visited_router'] = True\n"
                    "    return None\n",
                )
            ]
        },
        agent_config={"displayName": "router_agent"},
    )
    billing_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "billing_ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['visited_billing'] = True\n"
                    "    return None\n",
                )
            ]
        },
        agent_config={"displayName": "billing_agent"},
    )

    llm = (
        MockLlm()
        .when_agent("router_agent")
        .transfer_to_agent("billing_agent")
        .when_agent("billing_agent")
        .respond(
            LlmResponse(
                content=Content.from_text("Billing specialist here."),
                usage_metadata=UsageMetadata(
                    prompt_token_count=40,
                    candidates_token_count=10,
                    total_token_count=50,
                ),
            )
        )
    )

    sim = SessionSimulator(
        router_runner,
        runners_by_agent={
            "router_agent": router_runner,
            "billing_agent": billing_runner,
        },
        initial_state={"session_started": True},
        llm_callable=llm,
        auto_follow_transfers=True,
    )
    sim.turn("I have a billing question")
    sim.turn("Thanks!")

    expect_session(
        sim, snapshot_dir=tmp_path
    ).to_succeed().to_have_zero_fallbacks().to_complete_turns(
        2
    ).to_have_turn_count(2).to_have_routed_agents(
        "router_agent", "billing_agent"
    ).to_follow_agent_path("router_agent", "billing_agent").to_end_on_agent(
        "billing_agent"
    ).to_have_final_state("visited_billing", True).to_set_state(
        visited_router=True, visited_billing=True
    ).to_have_state_key("session_started", True).to_stay_within_budget(
        max_total_tokens=500, max_duration_ms=60000.0
    ).to_match_snapshot("multi_turn_billing_session")

    with pytest.raises(AgentAssertionError, match="budget"):
        expect_session(sim).to_stay_within_budget(max_total_tokens=10)


# ---------------------------------------------------------------------------
# 8. Deterministic Snapshots: Scrubbing, Structured Diff, Negation, Scoping
# ---------------------------------------------------------------------------


def test_snapshot_scrubbing_structured_drift_and_negation(
    tmp_path: Path,
) -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_state",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['version'] = 'v1'\n"
                    "    return None\n",
                )
            ]
        }
    )
    resp_with_sig = LlmResponse.from_parts(
        [
            Part(
                text="thought text",
                thought=True,
                thought_signature=b"\x01\x02\x03\x04",
            ),
            Part.from_text("Hello v1"),
        ]
    )
    trace_v1 = runner.run_turn(
        user_input="hi",
        initial_state={
            "current_date": "2026-10-20T00:00:00Z",
        },
        llm_callable=MockLlm().on_hop(1).respond(resp_with_sig),
        simulate_struct_numbers=False,
    )
    trace_v1.final_state["binary_blob"] = b"raw_bytes"
    trace_v1.final_state["tag_set"] = {"b", "a"}
    trace_v1.final_state["timestamp"] = datetime.date(2026, 10, 20)
    trace_v1.final_state["nan_val"] = float("nan")

    snap_payload = serialize_turn_snapshot(trace_v1, include_full_prompt=True)
    assert (
        snap_payload["final_state"]["current_date"] == "<SCRUBBED_CURRENT_DATE>"
    )
    assert snap_payload["final_state"]["tag_set"] == ["a", "b"]
    assert snap_payload["final_state"]["nan_val"] == "<NaN>"
    assert (
        snap_payload["final_response"]["thought_signature"]
        == "<THOUGHT_SIGNATURE>"
    )
    assert "excluded_tools" in snap_payload["llm_steps"][0]
    assert "duration_ms" not in snap_payload

    # Negated snapshot when no file exists on disk must raise without creating any file
    empty_snap_dir = tmp_path / "empty_snaps"
    with pytest.raises(AgentAssertionError):
        expect_turn(trace_v1).not_.to_match_snapshot(
            "non_existent", snapshot_dir=empty_snap_dir
        )
    assert not empty_snap_dir.exists() or not list(
        empty_snap_dir.glob("*.json")
    )

    # Write initial snapshot
    expect_turn(trace_v1).to_match_snapshot(
        "versioned_turn", snapshot_dir=tmp_path
    )

    # Mutate output and verify drift raises AgentAssertionError with structured +Added / ~Modified / -Removed diff
    trace_v2 = runner.run_turn(
        user_input="hi",
        initial_state={"version": "v2_changed", "added_field": 123},
        llm_callable=MockLlm().on_hop(1).reply_text("Hello v2"),
        simulate_struct_numbers=False,
    )
    expect_turn(trace_v2).not_.to_match_snapshot(
        "versioned_turn", snapshot_dir=tmp_path
    )
    with pytest.raises(AgentAssertionError) as drift_exc:
        expect_turn(trace_v2).to_match_snapshot(
            "versioned_turn", snapshot_dir=tmp_path
        )
    drift_msg = str(drift_exc.value)
    assert "Snapshot Drift (+Added / ~Modified / -Removed)" in drift_msg
    assert "~Modified" in drift_msg
    assert "+Added" in drift_msg

    # Update snapshot with update_snapshots=True
    expect_turn(trace_v2).to_match_snapshot(
        "versioned_turn", snapshot_dir=tmp_path, update_snapshots=True
    )
    expect_turn(trace_v2).to_match_snapshot(
        "versioned_turn", snapshot_dir=tmp_path
    )


def test_multiple_unnamed_and_named_snapshots_scoped_to_test(
    tmp_path: Path,
) -> None:
    runner = CallbackChainRunner.from_callbacks({})
    t1 = runner.run_turn(
        user_input="one", llm_callable=MockLlm().default_reply("r1")
    )
    t2 = runner.run_turn(
        user_input="two", llm_callable=MockLlm().default_reply("r2")
    )

    expect_turn(t1).to_match_snapshot(snapshot_dir=tmp_path)
    expect_turn(t2).to_match_snapshot(snapshot_dir=tmp_path)
    expect_turn(t1).to_match_snapshot("custom_label", snapshot_dir=tmp_path)

    snap_file = tmp_path / "test_callback_chain_jest_framework.snap.json"
    assert snap_file.is_file()
    data = json.loads(snap_file.read_text(encoding="utf-8"))
    fn_name = "test_multiple_unnamed_and_named_snapshots_scoped_to_test"
    assert fn_name in data
    assert f"{fn_name}::2" in data
    assert f"{fn_name}::custom_label" in data


# ---------------------------------------------------------------------------
# 9. Pytest Plugin Fixtures & CLI Options (In-Process via pytester)
# ---------------------------------------------------------------------------


def test_pytest_plugin_fixtures_collect_traces(
    cxas_trace_collector: Any,
    cxas_snapshot: SnapshotManager,
    tmp_path: Path,
) -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_plugin",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['plugin_ok'] = True\n"
                    "    return None\n",
                )
            ]
        }
    )
    diff = runner.compare_stage_modes("before_agent")
    expect_stage(diff).to_be_mode_consistent()

    trace = runner.run_turn(
        user_input="plugin test",
        llm_callable=MockLlm().default_reply("plugin reply"),
    )
    expect_turn(trace).to_succeed()

    assert len(cxas_trace_collector.mode_diffs) == 1
    assert len(cxas_trace_collector.turn_traces) == 1
    assert cxas_trace_collector.has_traces is True
    assert isinstance(cxas_snapshot, SnapshotManager)


def test_pytest_plugin_cli_flags_inprocess_with_pytester(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_sub_jest="""
        import os
        from cxas_scrapi.evals.callback_chain import (
            CallbackChainRunner, MockLlm, expect_stage, expect_turn
        )
        from cxas_scrapi.utils.callback_libs import CallbackContext

        def _make_runner(val="initial"):
            return CallbackChainRunner.from_callbacks({
                "before_agent": [
                    ("cb", f"def before_agent_callback(ctx):\\n    ctx.state['v'] = {val!r}\\n    return None\\n")
                ]
            })

        def test_sub_turn(request):
            val = request.config.getoption("--cxas-llm") or "initial"
            trace = _make_runner(val).run_turn(
                user_input="hello", llm_callable=MockLlm().default_reply("ok")
            )
            expect_turn(trace).to_succeed().to_match_snapshot("sub_snap")

        def test_env_mock():
            assert os.environ.get("CXAS_CALLBACK_LLM") is None

        class TestClassA:
            def test_same_name(self):
                _, tr = _make_runner("class_a").run_before_agent(CallbackContext(state={}))
                expect_stage(tr).to_match_snapshot()

        class TestClassB:
            def test_same_name(self):
                _, tr = _make_runner("class_b").run_before_agent(CallbackContext(state={}))
                expect_stage(tr).to_match_snapshot()
        """
    )

    # 1. Initial run with --cxas-llm=mock creates snapshots, keeps CXAS_CALLBACK_LLM unset,
    #    and avoids class method snapshot key collision
    res1 = pytester.runpytest_inprocess(
        "-p",
        "cxas_scrapi.evals.callback_chain.pytest_plugin",
        "--cxas-llm=mock",
        "-q",
    )
    assert res1.ret == 0
    snap_file = pytester.path / "__snapshots__" / "test_sub_jest.snap.json"
    assert snap_file.is_file()
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    assert "TestClassA::test_same_name" in snap_data
    assert "TestClassB::test_same_name" in snap_data
    assert "test_sub_turn::sub_snap" in snap_data

    # 2. Run with drifted state fails snapshot comparison
    res2 = pytester.runpytest_inprocess(
        "-p",
        "cxas_scrapi.evals.callback_chain.pytest_plugin",
        "--cxas-llm=gemini-2.5-flash",
        "-k",
        "test_sub_turn",
    )
    assert res2.ret != 0

    # 3. Run with --cxas-update-snapshots -> updates snapshot and passes
    res3 = pytester.runpytest_inprocess(
        "-p",
        "cxas_scrapi.evals.callback_chain.pytest_plugin",
        "--cxas-update-snapshots",
        "--cxas-llm=gemini-2.5-flash",
        "-k",
        "test_sub_turn",
        "-q",
    )
    assert res3.ret == 0
    updated_data = json.loads(snap_file.read_text(encoding="utf-8"))
    assert (
        updated_data["test_sub_turn::sub_snap"]["final_state"]["v"]
        == "gemini-2.5-flash"
    )


# ---------------------------------------------------------------------------
# 10. Multi-Name Negation, State Coercion, & Edge-Case Semantics
# ---------------------------------------------------------------------------


def test_negated_multi_name_matchers_require_none_of() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_first",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['added_a'] = 'val_a'\n"
                    "    ctx.state['mod_a'] = 'new_a'\n"
                    "    ctx.state.pop('rem_a', None)\n"
                    "    return Content.from_text('stop')\n",
                ),
                (
                    "cb_skipped",
                    "def before_agent_callback(ctx):\n    return None\n",
                ),
            ]
        },
        simulate_struct_numbers=False,
    )
    _, st_trace = runner.run_before_agent(
        CallbackContext(state={"mod_a": "old_a", "rem_a": 1})
    )

    # Negating multi-name matchers must fail if ANY of the listed names matched
    with pytest.raises(AgentAssertionError):
        expect_stage(st_trace).not_.to_have_executed("cb_first", "cb_never")
    expect_stage(st_trace).not_.to_have_executed("cb_never_1", "cb_never_2")

    with pytest.raises(AgentAssertionError):
        expect_stage(st_trace).not_.to_have_skipped("cb_skipped", "cb_first")
    expect_stage(st_trace).not_.to_have_skipped("cb_first")

    with pytest.raises(AgentAssertionError):
        expect_stage(st_trace).not_.to_have_added_state("added_a", "added_b")
    expect_stage(st_trace).not_.to_have_added_state("added_b", "added_c")

    with pytest.raises(AgentAssertionError):
        expect_stage(st_trace).not_.to_have_modified_state("mod_a", "mod_b")
    expect_stage(st_trace).not_.to_have_modified_state("mod_b", "mod_c")

    with pytest.raises(AgentAssertionError):
        expect_stage(st_trace).not_.to_have_removed_state("rem_a", "rem_b")
    expect_stage(st_trace).not_.to_have_removed_state("rem_b", "rem_c")

    # Non-executed callback in `in_callback` produces explicit diagnostic note
    with pytest.raises(
        AgentAssertionError, match="did not execute in stage 'before_agent'"
    ):
        expect_stage(st_trace).to_have_modified_state(
            "mod_a", in_callback="typo_callback"
        )

    # Turn-level multi-name negation (`to_expose_tools`, `to_have_executed_callback`)
    req = LlmRequest(
        contents=[Content.from_text("hello", role="user")],
        config=GenerateContentConfig(
            tools=[
                Tool(
                    function_declarations=[
                        FunctionDeclaration(
                            name="admin_delete", description="delete"
                        )
                    ]
                )
            ]
        ),
    )
    turn_runner = CallbackChainRunner.from_callbacks(
        {
            "before_model": [
                (
                    "bm_one",
                    "def before_model_callback(ctx, req):\n    return None\n",
                )
            ]
        }
    )
    turn_trace = turn_runner.run_turn(
        CallbackContext(state={}),
        req,
        MockLlm().default_reply("ok"),
    )
    with pytest.raises(AgentAssertionError):
        expect_turn(turn_trace).not_.to_expose_tools(
            "admin_delete", "admin_refund"
        )
    expect_turn(turn_trace).not_.to_expose_tools("admin_refund", "admin_export")

    with pytest.raises(AgentAssertionError):
        expect_turn(turn_trace).not_.to_have_executed_callback(
            "bm_one", "bm_missing"
        )
    expect_turn(turn_trace).not_.to_have_executed_callback(
        "bm_missing_1", "bm_missing_2"
    )


def test_noop_callback_int_to_float_coercion_not_reported_as_modification() -> (
    None
):
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "noop_cb",
                    "def before_agent_callback(ctx):\n    return None\n",
                ),
                (
                    "setter_cb",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['target'] = 'wrong'\n"
                    "    return None\n",
                ),
            ]
        }
    )
    _, trace = runner.run_before_agent(
        CallbackContext(state={"count": 1, "ratio": 2.5})
    )
    expect_stage(trace).not_.to_have_modified_state(
        "count", in_callback="noop_cb"
    )
    expect_stage(trace).not_.to_have_modified_state("count")

    with pytest.raises(AgentAssertionError) as exc_info:
        expect_stage(trace).to_have_state_key("target", "expected")
    msg = str(exc_info.value)
    assert "[Root-Cause Callback]: setter_cb" in msg
    assert "modified 'count'" not in msg


def test_inject_state_update_sources_and_leak_field_safety() -> None:
    req = LlmRequest(
        contents=[
            Content(
                role="user",
                parts=[
                    Part.from_text("<current_state>\n"),
                    Part.from_text("tier: standard\n"),
                    Part.from_text("stale_only: yes\n"),
                    Part.from_text("</current_state>\n"),
                ],
            ),
            Content(
                role="user",
                parts=[
                    Part.from_text("<state_update>"),
                    Part.from_text("tier: gold"),
                    Part.from_text("fresh_key: active"),
                    Part.from_text("</state_update>"),
                ],
            ),
            Content.from_text("Hello world", role="user"),
        ]
    )
    runner = CallbackChainRunner.from_callbacks({})
    trace = runner.run_turn(
        CallbackContext(state={}), req, MockLlm().default_reply("hi")
    )

    # Default source="state_update" checks only <state_update>, not <current_state>
    expect_turn(trace).to_inject_state_update("tier", "gold")
    expect_turn(trace).to_inject_state_update(
        keys=["tier", "fresh_key"], tier="gold"
    )
    expect_turn(trace).not_.to_inject_state_update("stale_only")
    with pytest.raises(AgentAssertionError):
        expect_turn(trace).not_.to_inject_state_update(
            keys=["stale_only", "fresh_key"]
        )

    # source="current_state" checks <current_state>
    expect_turn(trace).to_inject_state_update(
        "stale_only", "yes", source="current_state"
    )
    expect_turn(trace).to_inject_state_update(
        "tier", "standard", source="current_state"
    )

    # source="any" merges both with <state_update> taking precedence over <current_state>
    expect_turn(trace).to_inject_state_update("stale_only", "yes", source="any")
    expect_turn(trace).to_inject_state_update("tier", "gold", source="any")

    # Snapshot records injected_state_update and injected_current_state separately
    snap = serialize_turn_snapshot(trace)
    llm_step_snap = snap["llm_steps"][0]
    assert llm_step_snap["injected_state_update"] == {
        "tier": "gold",
        "fresh_key": "active",
    }
    assert llm_step_snap["injected_current_state"] == {
        "tier": "standard",
        "stale_only": "yes",
    }

    # to_leak_in_prompt does not false-positive on LlmRequest JSON schema field names
    expect_turn(trace).not_.to_leak_in_prompt("parts", "role", "contents")


def test_session_turn_one_based_and_mockllm_strict_per_turn_hops() -> None:
    runner = CallbackChainRunner.from_callbacks(
        {}, agent_config={"displayName": "main_agent"}
    )
    llm = (
        MockLlm()
        .on_turn(1)
        .on_hop(1)
        .reply_text("turn 1 hop 1")
        .on_turn(2)
        .on_hop(1)
        .reply_text("turn 2 hop 1")
    )
    sim = SessionSimulator(runner, llm_callable=llm)
    sim.turn("first")
    sim.turn("second")

    sess_exp = expect_session(sim)
    sess_exp.turn(1).to_reply_matching("turn 1 hop 1")
    sess_exp.turn(2).to_reply_matching("turn 2 hop 1")
    sess_exp.turn(-1).to_reply_matching("turn 2 hop 1")

    with pytest.raises(ValueError, match="1-based"):
        sess_exp.turn(0)
    with pytest.raises(AgentAssertionError, match="1-based turn 3"):
        sess_exp.turn(3)
    with pytest.raises(ValueError, match="requires at least one budget limit"):
        sess_exp.to_stay_within_budget()

    # MockLlm on_hop(3) must not fire as a fallback on turn 2 hop 1 when turn 1 had 2 hops
    strict_llm = (
        MockLlm()
        .on_turn(1)
        .on_hop(1)
        .call_tool("ping")
        .on_turn(1)
        .on_hop(2)
        .reply_text("turn 1 done")
        .on_hop(3)
        .reply_text("should not fire on turn 2 hop 1")
    )
    sim2 = SessionSimulator(
        runner,
        llm_callable=strict_llm,
        tool_impls={"ping": lambda **kw: {"pong": True}},
    )
    sim2.turn("go")
    t2 = sim2.turn("second turn without hop 1 rule")
    expect_turn(t2).to_fail(message_contains="no response configured")


def test_toolspy_single_dict_forwarding_and_snapshot_diff_truncation(
    tmp_path: Path,
) -> None:
    spy_a = ToolSpy(wraps=lambda a: a)
    spy_x = ToolSpy(wraps=lambda x: x)
    assert spy_a({"a": 5}) == {"a": 5}
    assert spy_x({"x": 5}) == {"x": 5}

    # Snapshot unified diff is capped at 80 lines for large payloads
    mgr = SnapshotManager(snapshot_dir=tmp_path)
    large_v1 = {f"key_{i:03d}": f"val_{i}" for i in range(120)}
    large_v2 = {f"key_{i:03d}": f"changed_{i}" for i in range(120)}
    mgr.assert_matches(large_v1, name="large_snap")
    with pytest.raises(AgentAssertionError) as exc_info:
        mgr.assert_matches(large_v2, name="large_snap")
    assert "more unified diff lines truncated" in str(exc_info.value)


def test_callback_evals_registers_pytest_plugin_and_persists_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cxas_scrapi.cli.main import main as cli_main  # noqa: PLC0415
    from cxas_scrapi.evals.callback_evals import CallbackEvals  # noqa: PLC0415

    app_dir = tmp_path / "sample_app"
    agent_dir = app_dir / "agents" / "support_agent"
    cb_dir = agent_dir / "before_agent_callbacks" / "init_state"
    cb_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "sample_app", "rootAgent": "support_agent"}),
        encoding="utf-8",
    )
    (agent_dir / "support_agent.json").write_text(
        json.dumps(
            {
                "displayName": "support_agent",
                "beforeAgentCallbacks": [
                    {
                        "description": "init_state",
                        "pythonCode": "before_agent_callbacks/init_state/python_code.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cb_code_path = cb_dir / "python_code.py"
    cb_code_path.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['step_version'] = 1\n"
        "    return None\n",
        encoding="utf-8",
    )
    chain_test_path = agent_dir / "test_chain.py"
    chain_test_path.write_text(
        "from python_code import CallbackContext, runner, expect_stage\n\n"
        "def test_chain_with_plugin_and_snapshot(cxas_trace_collector, cxas_snapshot):\n"
        "    res, trace = runner.run_before_agent(CallbackContext(state={}))\n"
        "    assert len(cxas_trace_collector.stage_traces) == 1\n"
        "    expect_stage(trace).to_have_state_key('step_version').to_match_snapshot('init_snap')\n"
        "    assert cxas_snapshot.resolve_snapshot_path()[0].name == 'test_chain.snap.json'\n",
        encoding="utf-8",
    )

    ce = CallbackEvals()
    # 1. First run creates the snapshot next to original test_chain.py
    df1 = ce.test_all_callbacks_in_app_dir(str(app_dir), chain_only=True)
    assert len(df1) == 1
    assert df1.iloc[0]["status"] == "PASSED"
    snap_file = agent_dir / "__snapshots__" / "test_chain.snap.json"
    assert snap_file.is_file()

    # 2. Mutate callback state -> second run must fail on snapshot drift
    cb_code_path.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['step_version'] = 2\n"
        "    return None\n",
        encoding="utf-8",
    )
    df2 = ce.test_all_callbacks_in_app_dir(str(app_dir), chain_only=True)
    assert len(df2) == 1
    assert df2.iloc[0]["status"] == "FAILED"
    assert "Snapshot" in str(df2.iloc[0]["error_message"])

    # 3. Refresh snapshots via --cxas-update-snapshots -> passes and updates file
    df3 = ce.test_all_callbacks_in_app_dir(
        str(app_dir),
        chain_only=True,
        pytest_args=["--cxas-update-snapshots"],
    )
    assert len(df3) == 1
    assert df3.iloc[0]["status"] == "PASSED"

    # 4. Subsequent run without update flag now passes against updated baseline
    df4 = ce.test_all_callbacks_in_app_dir(str(app_dir), chain_only=True)
    assert len(df4) == 1
    assert df4.iloc[0]["status"] == "PASSED"

    # 5. CLI cxas test-callbacks --update-snapshots also refreshes drifted snapshots
    cb_code_path.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['step_version'] = 3\n"
        "    return None\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cxas",
            "test-callbacks",
            "--app-dir",
            str(app_dir),
            "--chain-only",
            "--update-snapshots",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        cli_main()
    assert exit_info.value.code == 0

    df5 = ce.test_all_callbacks_in_app_dir(str(app_dir), chain_only=True)
    assert len(df5) == 1
    assert df5.iloc[0]["status"] == "PASSED"

    # 6. Outer unnamed snapshot counter is preserved across nested CallbackEvals runs
    outer_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb",
                    "def before_agent_callback(ctx):\n    ctx.state['x'] = 1\n    return None\n",
                )
            ]
        }
    )
    _, outer_tr1 = outer_runner.run_before_agent(CallbackContext(state={}))
    _, outer_tr2 = outer_runner.run_before_agent(
        CallbackContext(state={"second": True})
    )
    outer_snap_dir = tmp_path / "outer_snaps"
    expect_stage(outer_tr1, snapshot_dir=outer_snap_dir).to_match_snapshot()
    ce.test_all_callbacks_in_app_dir(str(app_dir), chain_only=True)
    expect_stage(outer_tr2, snapshot_dir=outer_snap_dir).to_match_snapshot()
    outer_snap_data = json.loads(
        next(outer_snap_dir.glob("*.snap.json")).read_text(encoding="utf-8")
    )
    assert len(outer_snap_data) == 2


def test_resolve_llm_missing_json_and_mockllm_snapshot_validation(
    tmp_path: Path,
) -> None:
    """Verifies resolve_llm raises FileNotFoundError for missing .json and MockLlm.from_snapshot validates inputs."""
    from cxas_scrapi.evals.callback_chain import resolve_llm  # noqa: PLC0415

    missing_json = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="LLM snapshot file not found"):
        resolve_llm(str(missing_json))

    with pytest.raises(TypeError, match="Unsupported snapshot source type"):
        MockLlm.from_snapshot(12345)

    with pytest.raises(
        ValueError,
        match="Snapshot mapping must contain 'responses', 'turns_responses'",
    ):
        MockLlm.from_snapshot({"unrelated_key": 1})

    with pytest.raises(ValueError, match="Unsupported LLM snapshot format"):
        MockLlm.from_snapshot({"format": "unknown_format_v99", "responses": []})

    with pytest.raises(
        ValueError, match="Snapshot 'responses' field must be a list"
    ):
        MockLlm.from_snapshot({"responses": "not_a_list"})

    # Also verify MockLlm.save_snapshot writes only clean SDK fields
    m = MockLlm().then_reply_text("clean snapshot")
    out_snap = tmp_path / "clean.snap.json"
    m.save_snapshot(out_snap)
    saved_obj = json.loads(out_snap.read_text(encoding="utf-8"))
    assert set(saved_obj.keys()) == {"format", "responses"}
    assert saved_obj["format"] == "cxas_llm_snapshot_v1"
    reloaded = MockLlm.from_snapshot(out_snap)
    resp = reloaded(LlmRequest(), CallbackContext(state={}))
    assert resp.content.parts[0].text == "clean snapshot"


def test_unnamed_snapshot_negation_and_ci_mode(tmp_path: Path) -> None:
    """Verifies .not_.to_match_snapshot() on 2nd unnamed snapshot and SnapshotManager(ci_mode=True)."""
    runner = CallbackChainRunner.from_callbacks({})
    t1 = runner.run_turn(
        user_input="first", llm_callable=MockLlm().default_reply("reply_1")
    )
    t2 = runner.run_turn(
        user_input="second", llm_callable=MockLlm().default_reply("reply_2")
    )
    t2_drifted = runner.run_turn(
        user_input="second",
        llm_callable=MockLlm().default_reply("reply_2_drifted"),
    )

    # Record 1st and 2nd unnamed snapshots
    expect_turn(t1, snapshot_dir=tmp_path).to_match_snapshot()
    expect_turn(t2, snapshot_dir=tmp_path).to_match_snapshot()

    # Reset unnamed counter as if starting a new test run, match 1st unnamed,
    # then assert .not_.to_match_snapshot() on 2nd unnamed snapshot
    from cxas_scrapi.evals.callback_chain.snapshots import (  # noqa: PLC0415
        _ACTIVE_SNAPSHOT_CONTEXT,
        _reset_unnamed_snapshot_counts,
    )

    active_ctx = _ACTIVE_SNAPSHOT_CONTEXT.get()
    if isinstance(active_ctx, dict):
        active_ctx["unnamed_counts"] = {}
    else:
        _reset_unnamed_snapshot_counts()

    expect_turn(t1, snapshot_dir=tmp_path).to_match_snapshot()
    expect_turn(t2_drifted, snapshot_dir=tmp_path).not_.to_match_snapshot()
    with pytest.raises(AgentAssertionError, match="NOT to match snapshot"):
        expect_turn(t2, snapshot_dir=tmp_path).not_.to_match_snapshot()

    # Verify ci_mode=True fails when a snapshot entry does not exist yet
    ci_mgr = SnapshotManager(snapshot_dir=tmp_path, ci_mode=True)
    with pytest.raises(AgentAssertionError, match="CI mode enabled"):
        ci_mgr.assert_matches({"x": 1}, name="missing_in_ci")


def test_background_thread_traces_attach_to_innermost_collector_and_combined_safe_copy() -> (
    None
):
    """Verifies ThreadPoolExecutor worker threads attach traces to the innermost active collector and combined mode isolates non-deepcopyable state."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_bg",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['ran'] = True\n"
                    "    return None\n",
                )
            ]
        }
    )

    main_turns: list[Any] = []
    main_stages: list[Any] = []
    from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
        _pop_active_stage_collector,
        _push_active_stage_collector,
    )
    from cxas_scrapi.evals.callback_chain.turn import (  # noqa: PLC0415
        _pop_active_turn_collector,
        _push_active_turn_collector,
    )

    t_tok = _push_active_turn_collector(main_turns)
    s_tok = _push_active_stage_collector(main_stages)
    try:

        def _bg_work() -> None:
            runner.run_turn(
                user_input="from_worker_thread",
                llm_callable=MockLlm().default_reply("bg"),
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_bg_work).result()

        assert len(main_turns) == 1
        assert len(main_stages) >= 1
    finally:
        _pop_active_turn_collector(t_tok, main_turns)
        _pop_active_stage_collector(s_tok, main_stages)

    # Combined mode with non-deepcopyable object created in step 1 still deep-copies sibling mutable containers
    comb_runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb1",
                    "def before_agent_callback(ctx):\n"
                    "    import threading\n"
                    "    ctx.state['_lock'] = threading.Lock()\n"
                    "    ctx.state['items'] = [1, 2]\n"
                    "    return None\n",
                ),
                (
                    "cb2",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['items'].append(3)\n"
                    "    ctx.state.pop('_lock', None)\n"
                    "    return None\n",
                ),
            ]
        },
        mode="combined",
        new_session=False,
        simulate_struct_numbers=False,
    )
    _, comb_trace = comb_runner.run_before_agent(CallbackContext(state={}))
    assert comb_trace.steps[0].state_after["items"] == [1, 2]
    assert comb_trace.steps[1].state_after["items"] == [1, 2, 3]
    assert any("[deepcopy]" in w for w in comb_trace.warnings)
