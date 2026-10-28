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

"""Single-turn cross-stage pipeline (`run_turn`) and multi-agent `SessionSimulator` tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from cxas_scrapi.evals.callback_evals import (
    CallbackChainRunner,
    LlmStepTrace,
    SessionSimulator,
    ToolStepTrace,
    TurnTrace,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    FunctionCall,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    Part,
    ToolContext,
)


def test_run_turn_full_pipeline_and_before_model_short_circuit() -> None:
    """Verifies run_turn full pipeline, before_model short-circuit, and non-LlmResponse error."""
    bm1 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    if callback_context.state.get('block_early'):\n"
        "        return LlmResponse.from_parts([Part.from_text('blocked by before_model')])\n"
        "    callback_context.state['bm_ran'] = True\n"
        "    llm_request.config = GenerateContentConfig(\n"
        "        system_instruction=Content(\n"
        "            role='system', parts=[Part.from_text('injected prompt')]\n"
        "        )\n"
        "    )\n"
    )
    am1 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    callback_context.state['am_saw_bm'] = callback_context.state.get('bm_ran', False)\n"
        "    text = llm_response.content.parts[0].text\n"
        "    return LlmResponse.from_parts([Part.from_text(text + ' [sanitized]')])\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_model": [("bm_01", bm1)],
            "after_model": [("am_01", am1)],
        },
        app_config={
            "variableDeclarations": [
                {
                    "name": "session_counter",
                    "schema": {"type": "INTEGER", "default": 1},
                }
            ]
        },
    )

    # Case 1: Normal full turn
    ctx1 = CallbackContext()
    llm_called_with_sys = []

    def mock_llm_fn(ctx: CallbackContext, req: LlmRequest) -> LlmResponse:
        llm_called_with_sys.append(req.config.system_instruction.parts[0].text)
        return LlmResponse.from_parts(
            [Part.from_text(f"model reply {int(ctx.state['session_counter'])}")]
        )

    final_resp, turn_trace = runner.run_turn(
        ctx1, LlmRequest(), mock_llm=mock_llm_fn
    )
    assert isinstance(turn_trace, TurnTrace)
    assert turn_trace.llm_invoked is True
    assert turn_trace.after_model_skipped is False
    assert llm_called_with_sys == ["injected prompt"]
    assert final_resp.content.parts[0].text == "model reply 1 [sanitized]"
    assert ctx1.state["bm_ran"] is True
    assert ctx1.state["am_saw_bm"] is True
    assert turn_trace.executed_callbacks == ["bm_01", "am_01"]
    assert len(turn_trace.llm_steps) == 1
    assert isinstance(turn_trace.llm_steps[0], LlmStepTrace)

    # Case 2: Short-circuit in before_model skips mock_llm and after_model
    ctx2 = CallbackContext(state={"block_early": True})
    mock_llm_spy = MagicMock(
        return_value=LlmResponse.from_parts([Part.from_text("never")])
    )
    early_resp, early_trace = runner.run_turn(
        ctx2, LlmRequest(), mock_llm=mock_llm_spy
    )
    assert early_trace.llm_invoked is False
    assert early_trace.after_model_skipped is True
    assert early_trace.after_model_trace is None
    assert early_trace.short_circuited_by == "bm_01"
    mock_llm_spy.assert_not_called()
    assert early_resp.content.parts[0].text == "blocked by before_model"

    # Case 3: mock_llm returning None raises TypeError
    with pytest.raises(TypeError, match="must return an LlmResponse"):
        runner.run_turn(
            CallbackContext(),
            LlmRequest(),
            mock_llm=lambda req: None,  # type: ignore[arg-type,return-value]
        )


def test_run_turn_execute_tools_and_system_tools() -> None:
    """Verifies run_turn full 5-stage loop, tool execution, and system tools."""
    ba = "def before_agent_callback(ctx):\n    ctx.state['ba_ran'] = True\n"
    aa = "def after_agent_callback(ctx):\n    ctx.state['aa_ran'] = True\n"
    bt = (
        "def before_tool_callback(tool, input, tool_context):\n"
        "    tool_context.state['last_fc_id'] = tool_context.function_call_id\n"
        "    if tool.name == 'blocked_tool':\n"
        "        return {'cached': True}\n"
        "    input['injected_arg'] = 'yes'\n"
        "    return None\n"
    )
    at = (
        "def after_tool_callback(tool, input, tool_context, tool_response):\n"
        "    if tool.name == 'get_balance':\n"
        "        return {'balance': tool_response['balance'], 'verified': True}\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [("ba_01", ba)],
            "after_agent": [("aa_01", aa)],
            "before_tool": [("bt_01", bt)],
            "after_tool": [("at_01", at)],
        },
        new_session=False,
    )

    tool_calls_log: list[dict] = []

    def get_balance_impl(input_args: dict, tool_ctx: ToolContext) -> dict:
        tool_calls_log.append(dict(input_args))
        return {"balance": 500}

    llm_responses = [
        LlmResponse.from_parts(
            [
                Part(
                    function_call=FunctionCall(
                        name="get_balance",
                        args={"account": "DE01"},
                        id="call_1",
                    )
                ),
                Part(
                    function_call=FunctionCall(
                        name="blocked_tool",
                        args={"x": 1},
                        id="call_2",
                    )
                ),
            ]
        ),
        LlmResponse.from_parts([Part.from_text("Your balance is 500 EUR.")]),
    ]

    ctx = CallbackContext(state={})
    final_resp, turn_trace = runner.run_turn(
        ctx,
        LlmRequest(),
        mock_llm=llm_responses,
        execute_tools=True,
        tool_impls={"get_balance": get_balance_impl},
        include_agent_callbacks=True,
        max_llm_calls=2,
    )
    assert final_resp.content.parts[0].text == "Your balance is 500 EUR."
    assert ctx.state["ba_ran"] is True
    assert ctx.state["aa_ran"] is True
    assert ctx.state["last_fc_id"] == "call_2"
    assert len(turn_trace.tool_steps) == 2
    assert len(turn_trace.llm_steps) == 2

    step1: ToolStepTrace = turn_trace.tool_steps[0]
    assert step1.tool_name == "get_balance"
    assert step1.function_call_id == "call_1"
    assert step1.short_circuited_by_before_tool is False
    assert step1.tool_executed is True
    assert step1.modified_input == {"account": "DE01", "injected_arg": "yes"}
    assert step1.final_tool_response == {"balance": 500.0, "verified": True}

    step2: ToolStepTrace = turn_trace.tool_steps[1]
    assert step2.tool_name == "blocked_tool"
    assert step2.short_circuited_by_before_tool is True
    assert step2.tool_executed is False
    assert step2.final_tool_response == {"cached": True}

    # System tools: transfer_to_agent and end_session
    ctx_transfer = CallbackContext(state={})
    _, tr_transfer = runner.run_turn(
        ctx_transfer,
        LlmRequest(),
        mock_llm=LlmResponse.from_parts(
            [Part.from_agent_transfer("fraud_agent")]
        ),
        execute_tools=True,
    )
    assert tr_transfer.transfer_to == "fraud_agent"
    assert len(tr_transfer.tool_steps) == 0

    ctx_end = CallbackContext(state={})
    _, tr_end = runner.run_turn(
        ctx_end,
        LlmRequest(),
        mock_llm=LlmResponse.from_parts(
            [Part.from_end_session(reason="completed", escalated=False)]
        ),
        execute_tools=True,
    )
    assert tr_end.end_session == {
        "reason": "completed",
        "session_escalated": False,
    }
    assert len(tr_end.tool_steps) == 0


def test_run_turn_adk_alignment_and_strict_tool_checks() -> None:
    """Verifies after_tool on before_tool short-circuit, multi-step history, and strict errors."""
    # before_tool short-circuit still invokes after_tool and appends history on hop 2
    bm = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['bm_calls'] = callback_context.state.get('bm_calls', 0) + 1\n"
    )
    bt = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    if tool.name == 'cached_lookup':\n"
        "        return {'cached': True}\n"
        "    return None\n"
    )
    at = (
        "def after_tool_callback(tool, input, callback_context, tool_response):\n"
        "    callback_context.state['after_tool_saw'] = dict(tool_response)\n"
        "    return None\n"
    )
    seen_contents_lengths: list[int] = []

    def mock_llm_fn(req: LlmRequest, ctx: CallbackContext) -> LlmResponse:
        seen_contents_lengths.append(len(req.contents or []))
        if len(seen_contents_lengths) == 1:
            return LlmResponse.from_parts(
                [Part.from_function_call("cached_lookup", {"q": "x"})]
            )
        return LlmResponse.from_parts([Part.from_text("done")])

    runner = CallbackChainRunner.from_callbacks(
        {"before_model": [bm], "before_tool": [bt], "after_tool": [at]},
        new_session=False,
    )
    resp, tt = runner.run_turn(
        CallbackContext(state={}),
        mock_llm=mock_llm_fn,
        execute_tools=True,
        max_llm_calls=2,
    )
    assert resp.content.parts[0].text == "done"
    assert tt.final_state["bm_calls"] == 2.0
    assert seen_contents_lengths == [0, 2]
    ts = tt.tool_steps[0]
    assert ts.short_circuited_by_before_tool is True
    assert ts.tool_executed is False
    assert ts.after_tool_trace is not None
    assert tt.final_state["after_tool_saw"] == {"cached": True}

    # before_model short-circuit with end_session or regular response + include_agent_callbacks
    bm_end = (
        "def before_model_callback(ctx, req):\n"
        "    if ctx.state.get('end_now'):\n"
        "        return LlmResponse.from_parts([\n"
        "            Part.from_end_session(reason='guardrail', escalated=True)\n"
        "        ])\n"
        "    return LlmResponse.from_parts([Part.from_text('short-circuit text')])\n"
    )
    aa_rec = "def after_agent_callback(ctx):\n    ctx.state['aa_ran'] = True\n"
    r_r3 = CallbackChainRunner.from_callbacks(
        {"before_model": [bm_end], "after_agent": [aa_rec]}, new_session=False
    )
    _, tt_end = r_r3.run_turn(
        CallbackContext(state={"end_now": True}), include_agent_callbacks=True
    )
    assert tt_end.llm_invoked is False
    assert tt_end.end_session == {
        "reason": "guardrail",
        "session_escalated": True,
    }
    assert tt_end.after_agent_trace is None

    _, tt_text = r_r3.run_turn(
        CallbackContext(state={}), include_agent_callbacks=True
    )
    assert tt_text.llm_invoked is False
    assert tt_text.after_agent_trace is not None
    assert tt_text.final_state["aa_ran"] is True

    # Missing tool sets missing_impl=True, tool_executed=False, raises KeyError in strict
    r_u1 = CallbackChainRunner.from_callbacks({}, new_session=False)
    _, tt_u1 = r_u1.run_turn(
        CallbackContext(state={}),
        mock_llm=LlmResponse.from_parts(
            [Part.from_function_call("unknown_tool", {})]
        ),
        execute_tools=True,
    )
    assert tt_u1.tool_steps[0].tool_executed is False
    assert tt_u1.tool_steps[0].missing_impl is True
    assert tt_u1.tool_steps[0].raw_tool_response is None

    with pytest.raises(KeyError, match="unknown_tool"):
        r_u1.run_turn(
            CallbackContext(state={}),
            mock_llm=LlmResponse.from_parts(
                [Part.from_function_call("unknown_tool", {})]
            ),
            execute_tools=True,
            strict=True,
        )

    # mock_llm sequence exhaustion raises IndexError
    with pytest.raises(IndexError, match="mock_llm sequence exhausted"):
        r_u1.run_turn(
            CallbackContext(state={}),
            mock_llm=[
                LlmResponse.from_parts([Part.from_function_call("t", {})])
            ],
            execute_tools=True,
            tool_impls={"t": {"ok": True}},
            max_llm_calls=2,
        )


def test_run_turn_multi_step_request_isolation_and_error_handling() -> None:
    """Verifies multi-step request isolation, tool exceptions, and error handling strategies."""
    # --- Multi-step request isolation + llm_steps ---
    bm_m1 = (
        "def before_model_callback(ctx, req):\n"
        "    if ctx.state.get('step', 0) == 0:\n"
        "        ctx.state['step'] = 1\n"
        "        req.config = GenerateContentConfig(\n"
        "            system_instruction=Content(\n"
        "                role='system', parts=[Part.from_text('step1 only')]\n"
        "            )\n"
        "        )\n"
        "    else:\n"
        "        ctx.state['step2_sys_text'] = (\n"
        "            req.config.system_instruction.parts[0].text\n"
        "            if req.config and req.config.system_instruction\n"
        "            else None\n"
        "        )\n"
    )
    am_m1 = (
        "def after_model_callback(ctx, resp):\n"
        "    ctx.state['am_count'] = ctx.state.get('am_count', 0) + 1\n"
    )
    runner_m1 = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm1", bm_m1)], "after_model": [("am1", am_m1)]},
        new_session=False,
    )
    base_req = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text("hello")])],
        config=GenerateContentConfig(
            system_instruction=Content(
                role="system", parts=[Part.from_text("base sys")]
            )
        ),
    )
    _, tt_m1 = runner_m1.run_turn(
        CallbackContext(state={}),
        llm_request=base_req,
        mock_llm=[
            LlmResponse.from_parts([Part.from_function_call("my_tool", {})]),
            LlmResponse.from_parts([Part.from_text("final")]),
        ],
        execute_tools=True,
        tool_impls={"my_tool": {"status": "ok"}},
        max_llm_calls=2,
    )
    assert tt_m1.final_state["step2_sys_text"] == "base sys"
    assert len(tt_m1.llm_steps) == 2
    assert tt_m1.llm_steps[0].step_index == 0
    assert tt_m1.llm_steps[1].step_index == 1
    assert len(tt_m1.llm_steps[1].request.contents) == 3

    # --- tool_impls raising exception ---
    def failing_tool(args: dict) -> dict:
        raise ValueError("backend timeout")

    runner_m2 = CallbackChainRunner.from_callbacks(
        {
            "after_tool": [
                (
                    "at1",
                    "def after_tool_callback(tool, input, ctx, resp):\n"
                    "    ctx.state['at_saw_resp'] = dict(resp)\n",
                )
            ]
        },
        new_session=False,
    )
    _, tt_m2 = runner_m2.run_turn(
        CallbackContext(state={}),
        mock_llm=LlmResponse.from_parts(
            [Part.from_function_call("bad_tool", {})]
        ),
        execute_tools=True,
        tool_impls={"bad_tool": failing_tool},
    )
    assert tt_m2.failed_stage == "tool_execution"
    assert tt_m2.failed_tool == "bad_tool"
    assert tt_m2.tool_steps[0].raw_tool_response is None
    assert "ValueError: backend timeout" in (
        tt_m2.tool_steps[0].tool_error or ""
    )
    assert tt_m2.tool_steps[0].after_tool_trace is None
    with pytest.raises(ValueError, match="backend timeout"):
        tt_m2.raise_for_error()

    with pytest.raises(ValueError, match="backend timeout"):
        runner_m2.run_turn(
            CallbackContext(state={}),
            mock_llm=LlmResponse.from_parts(
                [Part.from_function_call("bad_tool", {})]
            ),
            execute_tools=True,
            tool_impls={"bad_tool": failing_tool},
            strict=True,
        )

    # --- before_agent returning end_session or failing ---
    ba_end = (
        "def before_agent_callback(ctx):\n"
        "    if ctx.state.get('boom'):\n"
        "        raise RuntimeError('ba boom')\n"
        "    return Content(\n"
        "        role='model',\n"
        "        parts=[Part.from_end_session(reason='ba_exit', escalated=True)],\n"
        "    )\n"
    )
    aa_spy = "def after_agent_callback(ctx):\n    ctx.state['aa_ran'] = True\n"
    runner_s1 = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba_end)], "after_agent": [("aa", aa_spy)]},
        new_session=False,
    )
    _, tt_s1_end = runner_s1.run_turn(
        CallbackContext(state={}), include_agent_callbacks=True
    )
    assert tt_s1_end.end_session == {
        "reason": "ba_exit",
        "session_escalated": True,
    }
    assert tt_s1_end.after_agent_trace is None

    resp_s1_err, tt_s1_err = runner_s1.run_turn(
        CallbackContext(state={"boom": True}), include_agent_callbacks=True
    )
    assert tt_s1_err.failed_stage == "before_agent"
    assert tt_s1_err.after_agent_trace is None
    assert resp_s1_err.content.parts[0].text == "Sorry, something went wrong."

    # --- integer enum strategy (3 == END_SESSION, 1 == NONE) ---
    runner_s4_end = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba_end)]},
        new_session=False,
        error_handling_settings={"errorHandlingStrategy": 3},
    )
    _, tr_s4_end = runner_s4_end.run_before_agent(
        CallbackContext(state={"boom": True})
    )
    assert tr_s4_end.error_outcome == "end_session"

    runner_s4_none = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba_end)]},
        new_session=False,
        error_handling_settings={"errorHandlingStrategy": 1},
    )
    _, tr_s4_none = runner_s4_none.run_before_agent(
        CallbackContext(state={"boom": True})
    )
    assert tr_s4_none.error_outcome == "error"


def test_session_simulator_multi_turn_state_events_and_fallback_carryover() -> (
    None
):
    """Verifies SessionSimulator multi-turn state, event log, and fallback carryover."""
    ba = (
        "def before_agent_callback(ctx):\n"
        "    ctx.state['turns_seen'] = ctx.state.get('turns_seen', 0) + 1\n"
    )
    bm = (
        "def before_model_callback(ctx, req):\n"
        "    if ctx.state.get('fail_now'):\n"
        "        raise RuntimeError('transient boom')\n"
        "    ctx.state['history_len'] = len(req.contents or [])\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba_01", ba)], "before_model": [("bm_01", bm)]},
        app_config={
            "variableDeclarations": [
                {
                    "name": "session_tier",
                    "schema": {"type": "STRING", "default": "gold"},
                }
            ],
            "errorHandlingSettings": {
                "errorHandlingStrategy": "FALLBACK_RESPONSE",
                "fallbackResponseConfig": {"maxFallbackAttempts": 1},
            },
        },
    )

    sim = SessionSimulator(runner)
    assert sim.is_agent_entry() is True
    assert sim.turn_index == 0
    assert sim.state["session_tier"] == "gold"

    # Turn 1: normal reply
    t1 = sim.turn(
        "Hello agent",
        mock_llm=LlmResponse.from_parts([Part.from_text("Hi there!")]),
    )
    assert sim.turn_index == 1
    assert sim.is_agent_entry() is False
    assert sim.state["turns_seen"] == 1.0
    assert sim.state["history_len"] == 1.0
    assert len(sim.events) == 3
    assert len([e for e in sim.events if e.content is not None]) == 2
    assert sim.events[1].content is None
    assert sim.events[1].actions.state_delta == {"turns_seen": 1.0}
    assert t1.final_response.content.parts[0].text == "Hi there!"

    # Turn 2: first failure -> fallback (consecutive_fallback_count becomes 1)
    sim.state["fail_now"] = True
    t2 = sim.turn("Trigger error 1")
    assert t2.before_model_trace.error_outcome == "fallback"
    assert sim.consecutive_fallback_count == 1

    # Turn 3: second consecutive failure -> escalates to end_session (maxFallbackAttempts == 1)
    # (include_agent_callbacks=False so before_agent doesn't reset fallback count)
    _, t3 = sim.run_turn("Trigger error 2", include_agent_callbacks=False)
    assert t3.before_model_trace.error_outcome == "end_session"
    assert t3.end_session == {
        "reason": "escalation after errors",
        "session_escalated": False,
    }


def test_session_simulator_events_tools_fallback_and_transfer() -> None:
    """Verifies SessionSimulator Event log helpers, fallback count reset, and agent routing."""
    aa_code = (
        "def after_agent_callback(callback_context):\n"
        "    u_in = callback_context.get_last_user_input()\n"
        "    a_out = callback_context.get_last_agent_output()\n"
        "    callback_context.state['last_user'] = [p.text for p in u_in]\n"
        "    callback_context.state['last_agent'] = [p.text for p in a_out]\n"
        "    callback_context.state['event_roles'] = [\n"
        "        (e.content.role, [\n"
        "            'fc' if p.function_call else ('fr' if p.function_response else 'text')\n"
        "            for p in e.parts()\n"
        "        ])\n"
        "        for e in callback_context.events\n"
        "    ]\n"
    )
    bt_fail = (
        "def before_tool_callback(tool, args, tool_context):\n"
        "    raise RuntimeError('bt transient error')\n"
    )
    at_ok = (
        "def after_tool_callback(tool, args, tool_context, tool_response):\n"
        "    tool_context.state['at_ran'] = True\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_tool": [("bt", bt_fail)],
            "after_tool": [("at", at_ok)],
            "after_agent": [("aa", aa_code)],
        },
        image_version="v2",
        new_session=False,
    )
    sim = SessionSimulator(runner)
    step = [0]

    def llm(req: Any) -> LlmResponse:
        step[0] += 1
        if step[0] == 1:
            return LlmResponse.from_parts([Part.from_function_call("ping", {})])
        return LlmResponse.from_parts([Part.from_text("pong")])

    tt = sim.turn(
        "hello",
        mock_llm=llm,
        tool_impls={"ping": lambda a: {"r": 1}},
        max_llm_calls=2,
    )
    assert sim.state["last_user"] == ["hello"]
    assert sim.state["last_agent"] == [None, None, "pong"]
    assert sim.state["at_ran"] is True
    assert tt.consecutive_fallback_count_after == 0
    assert sim.consecutive_fallback_count == 0
    assert [
        next((r, list(ps)) for r, ps in [item])
        for item in sim.state["event_roles"]
    ] == [
        ("user", ["text"]),
        ("model", ["fc"]),
        ("user", ["fr"]),
        ("model", ["text"]),
    ]

    # transfer_to_agent routing via runners_by_agent and warning when missing
    r_root = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state.setdefault('visited', []).append('root')\n",
                )
            ]
        },
        new_session=False,
    )
    r_sub = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state.setdefault('visited', []).append('sub_agent')\n",
                )
            ]
        },
        new_session=False,
    )
    sim_transfer = SessionSimulator(
        r_root, runners_by_agent={"root": r_root, "sub_agent": r_sub}
    )
    t_switch = sim_transfer.turn(
        "switch to sub",
        mock_llm=LlmResponse.from_parts(
            [
                Part.from_function_call(
                    "transfer_to_agent", {"agent_name": "sub_agent"}
                )
            ]
        ),
    )
    assert t_switch.transfer_to == "sub_agent"
    assert sim_transfer.runner is r_sub
    sim_transfer.turn(
        "now in sub",
        mock_llm=LlmResponse.from_parts([Part.from_text("from sub")]),
    )
    assert sim_transfer.state["visited"] == ["root", "sub_agent"]

    # Missing target agent emits warning on TurnTrace
    t_missing = sim_transfer.turn(
        "switch to unknown",
        mock_llm=LlmResponse.from_parts(
            [
                Part.from_function_call(
                    "transfer_to_agent", {"agent_name": "nonexistent_agent"}
                )
            ]
        ),
    )
    assert any("nonexistent_agent" in w for w in t_missing.warnings)


def test_function_response_author_and_runtime_event_helpers(
    tmp_path: Path,
) -> None:
    """Verifies function_response event author and SessionSimulator.from_app_dir warnings."""
    r = CallbackChainRunner.from_callbacks(
        {},
        agent_config={"displayName": "shop_agent"},
        image_version="v2",
        new_session=False,
    )
    sim = SessionSimulator(r)
    step = [0]

    def llm(_req: Any) -> LlmResponse:
        step[0] += 1
        if step[0] == 1:
            return LlmResponse.from_parts(
                [Part.from_function_call("lookup", {"id": "1"})]
            )
        return LlmResponse.from_parts([Part.from_text("found item 1")])

    sim.turn(
        "where is item 1?",
        mock_llm=llm,
        tool_impls={"lookup": lambda a: {"status": "shipped"}},
        max_llm_calls=2,
    )
    assert [e.author for e in sim.events] == [
        "user",
        "shop_agent",
        "shop_agent",
        "shop_agent",
    ]
    assert [e.content.role for e in sim.events] == [
        "user",
        "model",
        "user",
        "model",
    ]
    assert [p.text for p in sim.ctx.get_last_user_input()] == [
        "where is item 1?"
    ]
    agent_parts = sim.ctx.get_last_agent_output()
    assert len(agent_parts) == 3
    assert agent_parts[0].function_call.name == "lookup"
    assert agent_parts[1].function_response.name == "lookup"
    assert agent_parts[2].text == "found item 1"

    # SessionSimulator.from_app_dir records broken agent in sim.warnings and raises on strict
    app_dir = tmp_path / "sim_app"
    good_ag = app_dir / "agents" / "good_agent"
    bad_ag = app_dir / "agents" / "bad_agent"
    good_ag.mkdir(parents=True)
    bad_ag.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "sim_app", "rootAgent": "good_agent"})
    )
    (good_ag / "good_agent.json").write_text(
        json.dumps({"displayName": "good_agent"})
    )
    (bad_ag / "bad_agent.json").write_text("{corrupt json")

    sim_app = SessionSimulator.from_app_dir(app_dir)
    assert sim_app.current_agent == "good_agent"
    assert any("C019" in w and "bad_agent" in w for w in sim_app.warnings)

    with pytest.raises(Exception):  # noqa: B017, PT011
        SessionSimulator.from_app_dir(app_dir, strict=True)
