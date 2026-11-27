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

"""Unit tests for MockLlm, ToolSpy, and LlmResponse snapshot serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.evals.callback_chain.mocks import (
    MockLlm,
    ToolSpy,
    deserialize_llm_response,
    serialize_llm_response,
)
from cxas_scrapi.utils.callback_libs import (
    Blob,
    Content,
    LlmRequest,
    LlmResponse,
    Part,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_mock_llm_fluent_queue_and_matching(tmp_path: Path) -> None:
    """Verifies MockLlm queue order, conditional predicates, tool calls, and snapshot save/load."""
    mock = (
        MockLlm()
        .when_prompt_contains("transfer")
        .transfer_to_agent("billing_agent")
        .call_tool("lookup_order", {"order_id": "123"})
        .reply_text("Your order #123 is shipped.")
        .default_reply("Fallback response.")
    )

    req_normal = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text("check order")])]
    )
    resp1 = mock(req_normal)
    assert resp1.content.parts[0].function_call.name == "lookup_order"
    assert resp1.content.parts[0].function_call.args == {"order_id": "123"}

    resp2 = mock(req_normal)
    assert resp2.content.parts[0].text == "Your order #123 is shipped."

    resp3 = mock(req_normal)
    assert resp3.content.parts[0].text == "Fallback response."

    req_transfer = LlmRequest(
        contents=[
            Content(role="user", parts=[Part.from_text("please transfer me")])
        ]
    )
    resp_transfer = mock(req_transfer)
    assert (
        resp_transfer.content.parts[0].function_call.name == "transfer_to_agent"
    )
    assert resp_transfer.content.parts[0].function_call.args == {
        "agent_name": "billing_agent"
    }
    assert mock.call_count == 4

    snap_file = tmp_path / "llm.snap.json"
    mock.save_snapshot(snap_file)
    loaded = MockLlm.from_snapshot(snap_file)
    assert (
        loaded(req_normal).content.parts[0].function_call.name == "lookup_order"
    )
    assert (
        loaded(req_normal).content.parts[0].text
        == "Your order #123 is shipped."
    )


def test_serialize_and_deserialize_llm_response_with_binary_blob() -> None:
    """Verifies lossless base64 round-trip for binary inline_data in LlmResponse snapshots."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\xff"
    resp = LlmResponse.from_parts(
        [
            Part.from_text("Here is the chart"),
            Part(inline_data=Blob(data=png_bytes, mime_type="image/png")),
        ]
    )
    data = serialize_llm_response(resp)
    restored = deserialize_llm_response(data)
    assert restored.content.parts[0].text == "Here is the chart"
    assert restored.content.parts[1].inline_data.data == png_bytes
    assert restored.content.parts[1].inline_data.mime_type == "image/png"


def test_tool_spy_recording_and_sequencing() -> None:
    """Verifies ToolSpy records calls, return values, exceptions, and reset."""
    spy = ToolSpy(
        side_effect=[{"temp": 72}, {"temp": 68}],
        name="get_weather",
    )
    assert spy(city="SF") == {"temp": 72}
    assert spy({"city": "NYC"}) == {"temp": 68}
    assert spy.call_count == 2
    spy.assert_called_with(city="NYC")
    assert spy.last_args == {"city": "NYC"}

    spy.reset()
    assert spy.call_count == 0

    err_spy = ToolSpy(side_effect=RuntimeError("db down"), name="fail_tool")
    with pytest.raises(RuntimeError, match="db down"):
        err_spy(x=1)
    assert err_spy.call_count == 1
    assert isinstance(err_spy.exceptions[-1], RuntimeError)


def test_part2_dual_review_mock_llm_and_tool_spy_fixes() -> None:
    """Verifies Opus & Carbon fixes on MockLlm & ToolSpy:
    - `ToolSpy(side_effect=itertools.cycle(...))` does not hang
    - `MockLlm.when_after_tool` does not match stale FunctionResponses from earlier turns
    - `MockLlm.when_prompt_contains` does not re-fire on post-tool continuation hops or stale turns
    - `MockLlm.from_snapshot(TurnTrace)` skips `before_model` short-circuit steps (`llm_invoked=False`)
    """
    import itertools  # noqa: PLC0415

    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackChainRunner,
        SessionSimulator,
    )
    from cxas_scrapi.utils.callback_libs import CallbackContext  # noqa: PLC0415

    cyc_spy = ToolSpy(side_effect=itertools.cycle([{"v": 1}, {"v": 2}]))
    assert cyc_spy() == {"v": 1}
    assert cyc_spy() == {"v": 2}
    assert cyc_spy() == {"v": 1}
    cyc_spy.reset()
    assert cyc_spy() == {"v": 1}

    gen_spy = ToolSpy(side_effect=(x for x in [{"g": 10}, {"g": 20}]))
    assert gen_spy() == {"g": 10}
    assert gen_spy() == {"g": 20}
    gen_spy.reset()
    assert gen_spy() == {"g": 10}

    runner = CallbackChainRunner.from_callbacks(
        {
            "before_model": [
                (
                    "bm_block",
                    "def before_model_callback(ctx, req):\n"
                    "    if ctx.state.get('block'):\n"
                    "        return LlmResponse.from_parts([Part.from_text('BLOCKED')])\n"
                    "    return None\n",
                )
            ]
        },
        agent_config={"displayName": "main_ag"},
        new_session=False,
    )
    mock = (
        MockLlm()
        .when_prompt_contains("check status")
        .call_tool("get_status", {"id": "1"})
        .when_after_tool("get_status")
        .reply_text("Status is READY")
        .default_reply("Default reply")
    )
    sim = SessionSimulator(
        runner,
        llm=mock,
        tool_impls={"get_status": lambda _inp: {"status": "READY"}},
    )
    # Turn 1: matches `when_prompt_contains("check status")`, calls `get_status` ONCE (no infinite loop!),
    # then matches `when_after_tool("get_status")` -> "Status is READY"
    t1 = sim.turn("Please check status")
    assert len(t1.tool_steps) == 1
    assert t1.final_response.content.parts[0].text == "Status is READY"

    # Turn 2: `before_model` short-circuits (`llm_invoked=False`)
    sim.state["block"] = True
    t2 = sim.turn("Blocked turn")
    assert t2.llm_invoked is False
    assert t2.final_response.content.parts[0].text == "BLOCKED"

    # Turn 3: stale Turn 1 prompt & tool response in history must NOT re-trigger Turn 1 predicates!
    sim.state["block"] = False
    t3 = sim.turn("Goodbye")
    assert t3.final_response.content.parts[0].text == "Default reply"

    # Replay via `MockLlm.from_snapshot([t1, t2, t3])` skips `t2` (`llm_invoked=False`) cleanly!
    replay_llm = MockLlm.from_snapshot([t1, t2, t3])
    assert (
        len(replay_llm._sequential_rules) == 3
    )  # 2 hops from t1 + 0 from t2 + 1 from t3
    _, rt = runner.run_turn(
        CallbackContext(state={"block": False}),
        user_input="Please check status",
        llm=replay_llm,
        tool_impls={"get_status": lambda _inp: {"status": "READY"}},
    )
    assert rt.final_response.content.parts[0].text == "Status is READY"

    # `state_update_mode="in_place"` appends a `<state_update>` user Content after the FunctionResponse;
    # `when_after_tool` and `when_prompt_contains` still recognize the trailing tool response segment!
    in_place_runner = CallbackChainRunner.from_callbacks(
        {
            "after_tool": [
                (
                    "at_mut",
                    "def after_tool_callback(tool, input, tool_context, tool_response):\n"
                    "    tool_context.state['synced'] = 'yes'\n"
                    "    return None\n",
                )
            ]
        },
        agent_config={"displayName": "ip_ag", "instruction": "State: {synced}"},
        app_config={
            "variableDeclarations": [
                {
                    "name": "synced",
                    "schema": {"type": "STRING", "default": "no"},
                }
            ]
        },
    )
    ip_mock = (
        MockLlm()
        .when_prompt_contains("trigger tool")
        .call_tool("do_work", {})
        .when_after_tool("do_work")
        .reply_text("In-place after tool matched!")
        .default_reply("DEFAULT")
    )
    ip_sim = SessionSimulator(
        in_place_runner,
        llm=ip_mock,
        state_update_mode="in_place",
        tool_impls={"do_work": lambda _i: {"ok": True}},
    )
    ip_turn = ip_sim.turn("Please trigger tool")
    assert len(ip_turn.tool_steps) == 1
    assert (
        ip_turn.final_response.content.parts[0].text
        == "In-place after tool matched!"
    )

    # When Turn 1 ends at `max_llm_calls=1` (`[user, model fc, user FR]`), Turn 2's
    # real user prompt (`[.., model fc, user FR, user "please TURN2_MARKER now"]`)
    # must match `when_prompt_contains("TURN2_MARKER")` and NOT stale `when_after_tool`!
    cap_mock = (
        MockLlm()
        .when_prompt_contains("TURN1_CALL")
        .call_tool("lookup", {"id": "1"})
        .when_prompt_contains("TURN2_MARKER")
        .reply_text("matched turn2 prompt")
        .when_after_tool("lookup")
        .reply_text("STALE after_tool match")
        .default_reply("DEFAULT")
    )
    cap_sim = SessionSimulator(
        CallbackChainRunner.from_callbacks(
            {}, agent_config={"displayName": "cap_ag"}
        ),
        llm=cap_mock,
        tool_impls={"lookup": lambda _i: {"found": True}},
    )
    cap_sim.turn("please TURN1_CALL", max_llm_calls=1)
    t_after_cap = cap_sim.turn("please TURN2_MARKER now")
    assert (
        t_after_cap.final_response.content.parts[0].text
        == "matched turn2 prompt"
    )


def test_pr548_tool_spy_dispatches_ces_dict_payload_like_mock_tools() -> None:
    """Verifies `ToolSpy(wraps=...)` handles `spy(payload)` for 0-arg,
    positional, and `**kwargs` callables, including via callback `tools.*`.
    """
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackChainRunner,
    )
    from cxas_scrapi.utils.callback_libs import (  # noqa: PLC0415
        CallbackContext,
    )

    assert ToolSpy(wraps=lambda: {"ok": True})({"x": 1}) == {"ok": True}
    assert ToolSpy(wraps=lambda **kw: {"echo": kw})({"x": 1}) == {
        "echo": {"x": 1}
    }
    assert ToolSpy(wraps=lambda a: a)({"a": 5}) == {"a": 5}
    kw_spy = ToolSpy(wraps=lambda **kw: {"echo": kw})
    assert kw_spy(q="direct") == {"echo": {"q": "direct"}}
    seq_spy = ToolSpy(side_effect=[lambda **kw: {"n": kw.get("n")}])
    assert seq_spy({"n": 2}) == {"n": 2}

    cb_spy = ToolSpy(wraps=lambda **kw: {"echo": kw})
    zero_spy = ToolSpy(wraps=lambda: {"zero": True})
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "cb_spy",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['a'] = tools.kw_tool({'foo': 'bar'})"
                    "['echo']\n"
                    "    ctx.state['b'] = tools.zero_tool()['zero']\n"
                    "    ctx.state['c'] = async_tools.kw_tool({'k': 1})()"
                    "['echo']\n"
                    "    return None\n",
                )
            ]
        },
        mock_tools={"kw_tool": cb_spy, "zero_tool": zero_spy},
        raise_on_error=True,
    )
    _, trace = runner.run_before_agent(CallbackContext(state={}))
    assert trace.final_state["a"] == {"foo": "bar"}
    assert trace.final_state["b"] is True
    assert trace.final_state["c"] == {"k": 1}
    assert cb_spy.calls == [{"foo": "bar"}, {"k": 1}]
    assert zero_spy.calls == [{}]
