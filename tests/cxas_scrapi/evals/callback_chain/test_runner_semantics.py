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

"""Stage execution semantics, V1/V2 image parity, and compare_modes tests."""

from __future__ import annotations

import dataclasses
import enum
import json
import re
from pathlib import Path

import pytest

from cxas_scrapi.evals.callback_evals import (
    CallbackChainRunner,
    _synthesize_chained_callback_module,
)
from cxas_scrapi.utils.callback_libs import (
    Blob,
    CallbackContext,
    Content,
    FunctionDeclaration,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    Part,
    Tool,
    ToolContext,
    ToolDeclaration,
    orjson,
)


def _find_testdata_dir() -> Path:
    cur = Path(__file__).resolve().parent
    for root in (cur, *cur.parents):
        for sub in (root / "testdata", root / "tests" / "testdata"):
            if (sub / "telecom_app").is_dir():
                return sub
    return cur / "testdata"


TESTDATA_DIR = _find_testdata_dir()


@pytest.fixture(autouse=True)
def _hermetic_search_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points CXAS_SEARCH_PATHS to hermetic tests/testdata fixtures."""
    monkeypatch.setenv("CXAS_SEARCH_PATHS", str(TESTDATA_DIR))


# ==============================================================================
# 1. Multi-Callback Acceptance Scenarios
# ==============================================================================


def test_budget_agent_before_agent_mode_aliasing_hazard() -> None:
    """Verifies budget_agent before_agent reference-sharing divergence between modes."""
    runner = CallbackChainRunner.from_agent_dir(
        "cxas_app/finance_app/agents/budget_agent"
    )
    ctx = CallbackContext(
        state={
            "caller_data": {
                "intent": {"active_group": "G1"},
                "authentication": {
                    "groups": [
                        {
                            "id": "G1",
                            "products": [
                                {
                                    "IBAN": "DE001",
                                    "currency_code": "EUR",
                                    "product_category": "CURRENT_ACCOUNT",
                                    "product_id": "P1",
                                },
                                {
                                    "IBAN": "DE002",
                                    "currency_code": "EUR",
                                    "product_category": "CURRENT_ACCOUNT",
                                    "product_id": "P2",
                                },
                            ],
                        }
                    ]
                },
            }
        }
    )

    diff = runner.compare_modes(ctx, stage="before_agent")

    assert diff.per_callback_trace.executed_callbacks == [
        "before_agent_callbacks_01",
        "before_agent_callbacks_02",
        "before_agent_callbacks_03",
    ]
    assert diff.per_callback_trace.error is None
    assert diff.has_state_diff
    per_cb = diff.per_callback_trace.final_state["budget_all_accounts"][
        "all_accounts"
    ]
    comb = diff.combined_trace.final_state["budget_all_accounts"][
        "all_accounts"
    ]
    assert all("product_id" in a for a in per_cb)
    assert not any("product_id" in a for a in comb)


def test_freeze_card_agent_short_circuit_skips_text_sanitization() -> None:
    """Verifies freeze_card_agent short-circuit in callback 01 skips callback 02."""
    runner = CallbackChainRunner.from_agent_dir(
        "card_support_app/agents/freeze_card_agent"
    )
    ctx = CallbackContext(
        state={
            "freeze_card_tool_final_boolean": True,
            "payload_for_successful_freeze": {
                "ui_element": "x",
                "entity_type": "y",
                "data": {},
            },
        }
    )
    llm_resp = LlmResponse.from_parts(
        parts=[
            Part.from_text("Your card has been frozen. Internal status: OK"),
            Part.from_function_call("client_function_final_information", {}),
        ]
    )

    final_resp, trace = runner.run_after_model(ctx, llm_resp)

    assert trace.short_circuited_by == "after_model_callbacks_01"
    assert trace.skipped_callbacks == ["after_model_callbacks_02"]
    assert [p.text for p in final_resp.content.parts if p.text]


def test_telecom_routing_agent_before_model_chain_and_mock_tools() -> None:
    """Verifies routing_agent dual callback chain (01 -> 02) and mock_tools injection."""
    runner = CallbackChainRunner.from_agent_dir(
        "telecom_app/agents/routing_agent",
        mock_tools={
            "complete_wrapup_tool": {"status": "OK"},
            "wrapup_helper": {"status": "WRAPPED"},
        },
    )
    ctx = CallbackContext(
        session_id="sess-123",
        state={"mdn": "5551234567"},
    )
    req = LlmRequest(
        contents=[
            Content(
                role="user",
                parts=[Part.from_text("sys.remote-call-disconnected")],
            )
        ]
    )

    diff = runner.compare_modes(ctx, stage="before_model", llm_request=req)

    assert not diff.has_diff
    assert diff.combined_trace.executed_callbacks == [
        "before_model_callbacks_01",
        "before_model_callbacks_02",
    ]
    assert diff.combined_trace.final_state["user_disconnect"] is True
    assert diff.combined_trace.final_state["call_dropped_by"] == "far_end"
    assert diff.combined_trace.tool_calls[0]["tool"] == "complete_wrapup_tool"


def test_budget_agent_after_model_with_mock_tools_and_disabled_skip() -> None:
    """Verifies budget_agent after_model skips disabled callback 03 and runs mock_tools."""
    runner = CallbackChainRunner.from_agent_dir(
        "cxas_app/finance_app/agents/budget_agent",
        mock_tools={"check_auth": {"result": True}},
    )
    disabled_names = [
        s.name for s in runner.get_disabled_callbacks("after_model")
    ]
    assert "after_model_callbacks_03" in disabled_names

    ctx = CallbackContext(
        state={
            "auth_btx_list": {
                "budget_agent": {"budget_get_balances": ["BTX_BUDGET"]}
            },
        }
    )
    llm_resp = LlmResponse.from_parts(
        [Part.from_function_call("budget_get_balances", {"iban": "DE001"})]
    )

    final_resp, trace = runner.run_after_model(ctx, llm_resp)

    assert "after_model_callbacks_03" not in trace.executed_callbacks
    assert trace.disabled_callbacks == ["after_model_callbacks_03"]
    assert trace.executed_callbacks == [
        "after_model_callbacks_01",
        "after_model_callbacks_02",
    ]
    assert trace.final_state["auth_private"]["agent_to_authenticate"] == ""
    assert trace.tool_calls[0]["tool"] == "check_auth"
    assert (
        final_resp.content.parts[0].function_call.name == "budget_get_balances"
    )


# ==============================================================================
# 2. Dimension 1: Loading Order (Global -> Agent), Disabled Skip & State Init
# ==============================================================================


def test_global_before_model_and_after_model_execution_order_and_disabled_skip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Verifies app-level global callbacks run before agent callbacks and skip disabled ones."""
    app_dir = tmp_path / "my_app"
    agent_dir = app_dir / "agents" / "sub_agent"
    (app_dir / "global_bm").mkdir(parents=True)
    (app_dir / "global_am_enabled").mkdir(parents=True)
    (app_dir / "global_am_disabled").mkdir(parents=True)
    (agent_dir / "cb1").mkdir(parents=True)
    (agent_dir / "cb2_disabled").mkdir(parents=True)
    (agent_dir / "cb3").mkdir(parents=True)
    (agent_dir / "am_agent").mkdir(parents=True)

    (app_dir / "global_bm" / "python_code.py").write_text(
        "def before_model_callback(ctx, llm_request):\n"
        "    HELPER = 'global'\n"
        "    ctx.state.setdefault('order', []).append(HELPER)\n"
    )
    (agent_dir / "cb1" / "python_code.py").write_text(
        "def before_model_callback(ctx, llm_request):\n"
        "    assert 'HELPER' not in globals()\n"
        "    ctx.state['order'].append('agent_1')\n"
    )
    (agent_dir / "cb2_disabled" / "python_code.py").write_text(
        "def before_model_callback(ctx, llm_request):\n"
        "    ctx.state['order'].append('agent_2_disabled')\n"
    )
    (agent_dir / "cb3" / "python_code.py").write_text(
        "def before_model_callback(ctx, llm_request):\n"
        "    ctx.state['order'].append('agent_3')\n"
    )
    (app_dir / "global_am_enabled" / "python_code.py").write_text(
        "def after_model_callback(ctx, llm_response):\n"
        "    ctx.state.setdefault('am_order', []).append('global_am')\n"
    )
    (app_dir / "global_am_disabled" / "python_code.py").write_text(
        "def after_model_callback(ctx, llm_response):\n"
        "    ctx.state.setdefault('am_order', []).append('global_am_disabled')\n"
    )
    (agent_dir / "am_agent" / "python_code.py").write_text(
        "def after_model_callback(ctx, llm_response):\n"
        "    ctx.state.setdefault('am_order', []).append('agent_am')\n"
    )

    app_json = {
        "name": "test-app",
        "timeZoneSettings": {"timeZone": "Europe/Berlin"},
        "variableDeclarations": [
            {
                "name": "with_default_list",
                "schema": {"type": "ARRAY", "default": ["init"]},
            },
            {
                "name": "with_int_default",
                "schema": {"type": "INTEGER", "default": 3},
            },
            {
                "name": "with_null_default",
                "schema": {"type": "STRING", "default": None},
            },
            {
                "name": "without_default",
                "schema": {"type": "STRING"},
            },
        ],
        "globalBeforeModelCallbacks": [
            {"pythonCode": "global_bm/python_code.py"}
        ],
        "globalAfterModelCallbacks": [
            {"pythonCode": "global_am_enabled/python_code.py"},
            {
                "pythonCode": "global_am_disabled/python_code.py",
                "disabled": True,
            },
        ],
        "globalBeforeAgentCallbacks": [
            {"pythonCode": "ignored/python_code.py"}
        ],
    }
    agent_json = {
        "name": "sub_agent",
        "beforeModelCallbacks": [
            {"pythonCode": "agents/sub_agent/cb1/python_code.py"},
            {
                "pythonCode": "agents/sub_agent/cb2_disabled/python_code.py",
                "disabled": True,
            },
            {"pythonCode": "agents/sub_agent/cb3/python_code.py"},
        ],
        "afterModelCallbacks": [
            {"pythonCode": "agents/sub_agent/am_agent/python_code.py"}
        ],
    }
    (app_dir / "app.json").write_text(json.dumps(app_json))
    (agent_dir / "sub_agent.json").write_text(json.dumps(agent_json))

    with caplog.at_level("WARNING"):
        runner = CallbackChainRunner.from_agent_dir(
            agent_dir,
            modality="VOICE",
            channel="GOOGLE_TELEPHONY_PLATFORM",
            session_metadata={"ivr_id": "99"},
        )
    assert any(
        "globalBeforeAgentCallbacks" in r.message for r in caplog.records
    )

    ctx = CallbackContext(state={"user_key": "val"})
    resp, trace = runner.run_before_model(ctx)

    assert resp is None
    assert trace.executed_callbacks == ["global_bm", "cb1", "cb3"]
    assert trace.disabled_callbacks == ["cb2_disabled"]
    assert ctx.state["order"] == ["global", "agent_1", "agent_3"]
    assert ctx.state["with_default_list"] == ["init"]
    assert isinstance(ctx.state["with_int_default"], float)
    assert ctx.state["with_int_default"] == 3.0
    assert "with_null_default" not in ctx.state
    assert "without_default" not in ctx.state
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}\[Europe/Berlin\]$", ctx.state["current_date"]
    )
    assert ctx.state["_session"] == {
        "modality": "VOICE",
        "channel": "GOOGLE_TELEPHONY_PLATFORM",
    }
    assert ctx.state["session_metadata"] == {"ivr_id": "99"}
    assert ctx.agent_name == "sub_agent"

    # Global after_model execution order and disabled skip
    _, am_trace = runner.run_after_model(
        ctx, LlmResponse.from_parts([Part.from_text("hi")])
    )
    assert am_trace.executed_callbacks == ["global_am_enabled", "am_agent"]
    assert am_trace.disabled_callbacks == ["global_am_disabled"]
    assert ctx.state["am_order"] == ["global_am", "agent_am"]

    # Mutating ctx.state["with_default_list"] must not affect subsequent runs
    ctx.state["with_default_list"].append("mutated")
    ctx2 = CallbackContext()
    runner.run_before_model(ctx2)
    assert ctx2.state["with_default_list"] == ["init"]

    # When new_session=False, defaults and predefined variables are not injected
    ctx3 = CallbackContext(state={"order": []})
    runner.run_before_model(ctx3, new_session=False)
    assert "with_default_list" not in ctx3.state
    assert "current_date" not in ctx3.state
    assert "_session" not in ctx3.state


# ==============================================================================
# 3. Dimension 2: Grouped vs Combined Chains
# ==============================================================================


@pytest.mark.parametrize("mode", ["per_callback", "combined"])
def test_before_agent_short_circuit_parity(mode: str) -> None:
    """Verifies before_agent execution and short-circuit parity across modes."""
    cb1 = (
        "def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:\n"
        "    print('before_agent_callback_1')\n"
        "    callback_context.variables['foo'] = 'baz'\n"
    )
    cb2 = (
        "def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:\n"
        "    print('before_agent_callback_2')\n"
        "    return Content(parts=[Part(text='hello from agent')], role='model')\n"
    )
    cb3 = (
        "def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:\n"
        "    print('is_ignored')\n"
        "    callback_context.variables['foo'] = 'overwritten'\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                ("cb_01", cb1),
                ("cb_02", cb2),
                ("cb_03", cb3),
            ]
        },
        mode=mode,  # type: ignore[arg-type]
        new_session=False,
    )
    ctx = CallbackContext()
    content, trace = runner.run_before_agent(ctx)

    assert content is not None
    assert content.role == "model"
    assert content.parts[0].text == "hello from agent"
    assert ctx.state == {"foo": "baz"}
    assert trace.executed_callbacks == ["cb_01", "cb_02"]
    assert trace.short_circuited is True
    assert trace.short_circuited_by == "cb_02"
    assert trace.skipped_callbacks == ["cb_03"]
    assert "before_agent_callback_1" in trace.stdout
    assert "before_agent_callback_2" in trace.stdout
    assert "is_ignored" not in trace.stdout


@pytest.mark.parametrize("mode", ["per_callback", "combined"])
def test_before_model_short_circuit_and_request_mutation_parity(
    mode: str,
) -> None:
    """Verifies before_model request mutations and short-circuit parity across modes."""
    cb1 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    print('bm_1')\n"
        "    callback_context.state['step'] = '1'\n"
        "    llm_request.config.system_instruction = Content(\n"
        "        role='system', parts=[Part.from_text('sys_from_1')]\n"
        "    )\n"
        "    return None\n"
    )
    cb2 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    print('bm_2')\n"
        "    sys_p = llm_request.config.system_instruction.parts[0]\n"
        "    callback_context.state['saw_sys'] = sys_p.text\n"
        "    llm_request.config.system_instruction = Content(\n"
        "        role='system', parts=[Part.from_text('sys_from_2_on_short_circuit')]\n"
        "    )\n"
        "    return LlmResponse.from_parts([Part.from_text('short_circuit_model')])\n"
    )
    cb3 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    print('bm_3_skipped')\n"
        "    callback_context.state['step'] = '3'\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("cb_01", cb1), ("cb_02", cb2), ("cb_03", cb3)]},
        mode=mode,  # type: ignore[arg-type]
        new_session=False,
    )
    ctx = CallbackContext()
    req = LlmRequest(
        config=GenerateContentConfig(
            system_instruction=Content(
                role="system", parts=[Part.from_text("initial_sys")]
            )
        )
    )
    resp, trace = runner.run_before_model(ctx, req)

    assert resp is not None
    assert resp.content.parts[0].text == "short_circuit_model"
    assert ctx.state == {"step": "1", "saw_sys": "sys_from_1"}
    assert (
        req.config.system_instruction.parts[0].text
        == "sys_from_2_on_short_circuit"
    )
    assert (
        trace.final_request.config.system_instruction.parts[0].text
        == "sys_from_2_on_short_circuit"
    )
    assert trace.executed_callbacks == ["cb_01", "cb_02"]
    assert trace.skipped_callbacks == ["cb_03"]


@pytest.mark.parametrize("mode", ["per_callback", "combined"])
def test_after_model_short_circuit_parity(mode: str) -> None:
    """Verifies after_model chaining, short-circuit, override_response, and effective_response."""
    cb1 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    callback_context.state['am1'] = llm_response.content.parts[0].text\n"
        "    return None\n"
    )
    cb2 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    callback_context.state['am2'] = 'override'\n"
        "    return LlmResponse.from_parts([Part.from_text('replaced_by_am2')])\n"
    )
    cb3 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    callback_context.state['am3'] = 'skipped'\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("cb_01", cb1), ("cb_02", cb2), ("cb_03", cb3)]},
        mode=mode,  # type: ignore[arg-type]
        new_session=False,
    )
    ctx = CallbackContext()
    orig = LlmResponse.from_parts([Part.from_text("orig")])
    eff_resp, trace = runner.run_after_model(ctx, orig)

    assert eff_resp.content.parts[0].text == "replaced_by_am2"
    assert trace.override_response is not None
    assert trace.override_response.content.parts[0].text == "replaced_by_am2"
    assert trace.effective_response.content.parts[0].text == "replaced_by_am2"
    assert trace.executed_callbacks == ["cb_01", "cb_02"]
    assert trace.skipped_callbacks == ["cb_03"]
    assert ctx.state == {"am1": "orig", "am2": "override"}


@pytest.mark.parametrize("mode", ["per_callback", "combined"])
def test_before_tool_short_circuit_and_input_mutation_parity(
    mode: str,
) -> None:
    """Verifies before_tool preserves in-place input mutations when short-circuiting."""
    cb1 = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    input['added_by_1'] = 'v1'\n"
        "    callback_context.state['t1'] = tool.name\n"
        "    return None\n"
    )
    cb2 = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    input['added_by_2'] = input['added_by_1'] + '+v2'\n"
        "    return {'short_circuit': True}\n"
    )
    cb3 = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    input['added_by_3'] = 'unreachable'\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_tool": [("cb_01", cb1), ("cb_02", cb2), ("cb_03", cb3)]},
        mode=mode,  # type: ignore[arg-type]
        new_session=False,
    )
    ctx = ToolContext()
    tool = Tool(name="my_tool", description="desc")
    tool_in = {"orig": "0"}
    out, trace = runner.run_before_tool(ctx, tool, tool_in)

    assert out == {"short_circuit": True}
    assert tool_in == {"orig": "0", "added_by_1": "v1", "added_by_2": "v1+v2"}
    assert trace.final_tool_input == {
        "orig": "0",
        "added_by_1": "v1",
        "added_by_2": "v1+v2",
    }
    assert trace.executed_callbacks == ["cb_01", "cb_02"]
    assert trace.skipped_callbacks == ["cb_03"]


# ==============================================================================
# 4. Dimension 3: State Serialization, Diffing & compare_modes Normalization
# ==============================================================================


def test_state_key_deletion_and_none_removal_semantics() -> None:
    """Verifies CES runtime state key deletion and None assignment removal semantics."""
    cb1 = (
        "def before_agent_callback(callback_context):\n"
        "    del callback_context.state['to_delete']\n"
        "    callback_context.state['to_null'] = None\n"
        "    callback_context.state['kept'] = {'nested': [1, 2]}\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb1)]}, new_session=False
    )
    ctx = CallbackContext(
        state={"to_delete": "bye", "to_null": "old", "unchanged": 42}
    )
    runner.run_before_agent(ctx)

    assert "to_delete" not in ctx.state
    assert "to_null" not in ctx.state
    assert ctx.state["unchanged"] == 42.0
    assert ctx.state["kept"] == {"nested": [1.0, 2.0]}


def test_compare_modes_eliminates_tuple_vs_list_false_positives() -> None:
    """Verifies compare_modes normalizes end-of-chain JSON so tuple vs list is not flagged."""
    cb1 = (
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['coords'] = (10, 20)\n"
    )
    cb2 = (
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['sum'] = sum(callback_context.state['coords'])\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb1), ("cb_02", cb2)]}
    )
    ctx = CallbackContext()
    diff = runner.compare_modes(ctx, stage="before_agent")

    assert not diff.has_diff
    assert diff.per_callback_trace.final_state["coords"] == [10, 20]
    assert diff.combined_trace.final_state["coords"] == [10, 20]
    assert diff.per_callback_trace.final_state["sum"] == 30


# ==============================================================================
# 5. Dimension 4: Request / Response In-Place Mutations (before_model, after_model, tools)
# ==============================================================================


def test_before_model_3_field_extraction_and_tool_filtering() -> None:
    """Verifies before_model returning None extracts system_instruction and excluded_tools."""
    cb1 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    llm_request.model = 'hacked-model'\n"
        "    llm_request.contents.append(\n"
        "        Content(role='user', parts=[Part.from_text('injected')])\n"
        "    )\n"
        "    llm_request.config.system_instruction = Content(\n"
        "        role='system', parts=[Part.from_text('new sys')]\n"
        "    )\n"
        "    llm_request.config.hide_tool('secret_tool')\n"
    )
    cb2 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['seen_model'] = llm_request.model\n"
        "    callback_context.state['seen_contents_len'] = len(llm_request.contents)\n"
        "    sys_p = llm_request.config.system_instruction.parts[0]\n"
        "    callback_context.state['seen_sys'] = sys_p.text\n"
        "    callback_context.state['seen_excluded_tools'] = llm_request.config.excluded_tools\n"
        "    callback_context.state['remaining_tools'] = [\n"
        "        f.name for f in llm_request.config.tools[0].function_declarations\n"
        "    ]\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("cb_01", cb1), ("cb_02", cb2)]},
        new_session=False,
    )

    def _make_req() -> LlmRequest:
        return LlmRequest(
            model="gemini-2.5-flash",
            contents=[Content(role="user", parts=[Part.from_text("original")])],
            config=GenerateContentConfig(
                system_instruction=Content(
                    role="system", parts=[Part.from_text("old sys")]
                ),
                tools=[
                    ToolDeclaration(
                        function_declarations=[
                            FunctionDeclaration(name="public_tool"),
                            FunctionDeclaration(name="secret_tool"),
                        ]
                    )
                ],
            ),
        )

    # 1. allow_content_update=False (default) in per_callback mode
    ctx_false = CallbackContext()
    req_false = _make_req()
    runner.run_before_model(
        ctx_false, req_false, mode="per_callback", allow_content_update=False
    )
    assert ctx_false.state["seen_model"] == "gemini-2.5-flash"
    assert ctx_false.state["seen_contents_len"] == 1
    assert ctx_false.state["seen_sys"] == "new sys"
    assert "seen_excluded_tools" not in ctx_false.state
    assert ctx_false.state["remaining_tools"] == ["public_tool"]

    # 2. allow_content_update=True in per_callback mode vs False in compare_modes
    ctx_true = CallbackContext()
    req_true = _make_req()
    runner.run_before_model(
        ctx_true, req_true, mode="per_callback", allow_content_update=True
    )
    assert ctx_true.state["seen_contents_len"] == 2
    assert len(req_true.contents) == 2

    diff_content_false = runner.compare_modes(
        CallbackContext(),
        stage="before_model",
        llm_request=_make_req(),
        allow_content_update=False,
    )
    assert diff_content_false.has_state_diff
    assert (
        diff_content_false.per_callback_trace.final_state["seen_contents_len"]
        == 1
    )
    assert (
        diff_content_false.combined_trace.final_state["seen_contents_len"] == 2
    )

    diff_content_true = runner.compare_modes(
        CallbackContext(),
        stage="before_model",
        llm_request=_make_req(),
        allow_content_update=True,
    )
    assert (
        diff_content_true.per_callback_trace.final_state["seen_contents_len"]
        == 2
    )
    assert (
        diff_content_true.combined_trace.final_state["seen_contents_len"] == 2
    )


def test_after_model_in_place_mutation_discarded_when_returning_none() -> None:
    """Verifies in-place after_model edits with return None are discarded in per_callback."""
    cb1 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    llm_response.content.parts[0].text = 'mutated_without_return'\n"
        "    return None\n"
    )
    cb2 = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    callback_context.state['cb2_saw_text'] = llm_response.content.parts[0].text\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("cb_01", cb1), ("cb_02", cb2)]},
        new_session=False,
    )
    ctx = CallbackContext()
    orig_resp = LlmResponse.from_parts([Part.from_text("original_text")])

    diff = runner.compare_modes(
        ctx, stage="after_model", llm_response=orig_resp
    )

    assert diff.has_state_diff
    assert (
        diff.per_callback_trace.final_state["cb2_saw_text"] == "original_text"
    )
    assert (
        diff.combined_trace.final_state["cb2_saw_text"]
        == "mutated_without_return"
    )
    assert not diff.has_response_diff
    assert diff.per_callback_trace.override_response is None
    assert (
        diff.per_callback_trace.effective_response.content.parts[0].text
        == "original_text"
    )
    assert (
        diff.per_callback_trace.final_response.content.parts[0].text
        == "original_text"
    )


def test_before_tool_and_after_tool_chains() -> None:
    """Verifies before_tool in-place input propagation and after_tool chain execution."""
    bt1 = (
        "def before_tool_callback(tool, input, tool_context):\n"
        "    input['injected'] = 'from_bt1'\n"
        "    tool_context.state['bt_ran'] = True\n"
        "    return None\n"
    )
    bt2 = (
        "def before_tool_callback(tool, input, tool_context):\n"
        "    tool_context.state['bt2_saw'] = input.get('injected')\n"
        "    return None\n"
    )
    at1 = (
        "def after_tool_callback(tool, input, tool_context, tool_response):\n"
        "    tool_response['mutated_in_place'] = True\n"
        "    return None\n"
    )
    at2 = (
        "def after_tool_callback(tool, input, tool_context, tool_response):\n"
        "    tool_context.state['at2_saw_mutated'] = 'mutated_in_place' in tool_response\n"
        "    return {'override': 'done'}\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {
            "before_tool": [("bt_01", bt1), ("bt_02", bt2)],
            "after_tool": [("at_01", at1), ("at_02", at2)],
        },
        new_session=False,
    )
    t_ctx = ToolContext()
    tool = Tool(name="lookup", description="lookup tool")
    tool_in = {"query": "cards"}

    bt_res, bt_trace = runner.run_before_tool(t_ctx, tool, tool_in)
    assert bt_res is None
    assert tool_in == {"query": "cards", "injected": "from_bt1"}
    assert t_ctx.state["bt2_saw"] == "from_bt1"
    assert bt_trace.final_tool_input == {
        "query": "cards",
        "injected": "from_bt1",
    }

    at_res, at_trace = runner.run_after_tool(
        t_ctx, tool, tool_in, {"raw": 123}, mode="per_callback"
    )
    assert at_res == {"override": "done"}
    assert t_ctx.state["at2_saw_mutated"] is False
    assert at_trace.short_circuited_by == "at_02"


# ==============================================================================
# 6. Dimension 5: Output Normalization (LlmResponse & before_agent Validation)
# ==============================================================================


def test_llm_response_normalization_reorders_function_calls_and_strips_turn_complete() -> (
    None
):
    """Verifies LlmResponse normalization strips turn_complete and moves function_call to end."""
    cb = (
        "def after_model_callback(callback_context, llm_response):\n"
        "    return LlmResponse(\n"
        "        content=Content(\n"
        "            role='model',\n"
        "            parts=[\n"
        "                Part.from_function_call('fn_1', {'a': 1}),\n"
        "                Part.from_text('first text'),\n"
        "                Part.from_function_call('fn_2', {'b': 2}),\n"
        "                Part.from_text('second text'),\n"
        "            ],\n"
        "        ),\n"
        "        partial=True,\n"
        "        turn_complete=True,\n"
        "    )\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("cb_01", cb)]}, new_session=False
    )
    ctx = CallbackContext()
    resp, _ = runner.run_after_model(
        ctx, LlmResponse.from_parts([Part.from_text("init")])
    )

    assert resp.partial is True
    assert resp.turn_complete is None
    parts = resp.content.parts
    assert [p.text for p in parts[:2]] == ["first text", "second text"]
    assert [p.function_call.name for p in parts[2:]] == ["fn_1", "fn_2"]


def test_before_agent_rejects_non_end_session_function_call_after_committing_state() -> (
    None
):
    """Verifies before_agent rejects non-end_session function calls after committing state."""
    bad_cb = (
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['set_by_cb'] = 'kept'\n"
        "    return Content(role='model', parts=[Part.from_agent_transfer('other_agent')])\n"
    )
    good_cb = (
        "def before_agent_callback(callback_context):\n"
        "    return Content(\n"
        "        role='model', parts=[Part.from_end_session(reason='done', escalated=True)]\n"
        "    )\n"
    )
    bad_runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("bad_cb", bad_cb)]},
        new_session=False,
        force_enable_exception_handling=True,
        error_handling_settings={
            "errorHandlingStrategy": "FALLBACK_RESPONSE",
            "fallbackResponseConfig": {"maxFallbackAttempts": 3},
        },
    )
    bad_ctx = CallbackContext()
    bad_out, bad_trace = bad_runner.run_before_agent(bad_ctx)
    assert bad_out is None
    assert bad_trace.failed_callback == "bad_cb"
    assert bad_trace.error_outcome == "error"
    assert "UNSUPPORTED_CALLBACK_RESULT" in (bad_trace.error or "")
    assert bad_ctx.state.get("set_by_cb") == "kept"

    good_runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("good_cb", good_cb)]}, new_session=False
    )
    good_out, good_trace = good_runner.run_before_agent(CallbackContext())
    assert good_trace.error is None
    assert good_out.parts[0].function_call.name == "end_session"


# ==============================================================================
# 7. Dimension 6: Exception Handling & Error Outcome Priority
# ==============================================================================


def test_exception_in_second_callback_state_preservation_and_error_outcomes() -> (
    None
):
    """Verifies exception in callback 02 preserves callback 01 state only in per_callback."""
    cb1 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['step1'] = 'committed'\n"
    )
    cb2 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['step2'] = 'uncommitted'\n"
        "    raise RuntimeError('boom in cb2')\n"
    )
    cb3 = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['step3'] = 'unreachable'\n"
    )

    runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("cb_01", cb1), ("cb_02", cb2), ("cb_03", cb3)]},
        new_session=False,
        error_handling_settings={
            "errorHandlingStrategy": "FALLBACK_RESPONSE",
            "fallbackResponseConfig": {
                "customFallbackMessages": {"en-US": "Custom fallback message"},
                "maxFallbackAttempts": 1,
            },
            "endSessionConfig": {"escalateSession": True},
        },
    )

    # First failure in per_callback mode -> outcome is "fallback" (0 < 1)
    ctx_per = CallbackContext(
        language_code="en-US", state={"init": "preserved"}
    )
    resp1, trace1 = runner.run_before_model(ctx_per, mode="per_callback")
    assert trace1.failed_callback == "cb_02"
    assert trace1.skipped_callbacks == ["cb_03"]
    assert trace1.error_outcome == "fallback"
    assert trace1.consecutive_fallback_count_after == 1
    assert resp1.content.parts[0].text == "Custom fallback message"
    assert ctx_per.state == {"init": "preserved", "step1": "committed"}

    # Stateless across runs unless consecutive_fallback_count is passed!
    ctx_stateless = CallbackContext(
        language_code="en-US", state={"init": "preserved"}
    )
    _, trace_stateless = runner.run_before_model(
        ctx_stateless, mode="per_callback"
    )
    assert trace_stateless.error_outcome == "fallback"

    # Passing consecutive_fallback_count -> "end_session" with reason="escalation after errors"
    ctx_comb = CallbackContext(
        language_code="en-US", state={"init": "preserved"}
    )
    resp2, trace2 = runner.run_before_model(
        ctx_comb,
        mode="combined",
        consecutive_fallback_count=trace1.consecutive_fallback_count_after,
    )
    assert trace2.failed_callback == "cb_02"
    assert trace2.error_outcome == "end_session"
    assert resp2.content.parts[0].function_call.name == "end_session"
    assert resp2.content.parts[0].function_call.args == {
        "reason": "escalation after errors",
        "session_escalated": True,
    }
    assert ctx_comb.state == {"init": "preserved"}


def test_fallback_utils_parity_all_cases() -> None:
    """Verifies tool fallback, END_SESSION reason, force_enable, and default fallback."""
    boom_tool = "def before_tool_callback(tool, input, ctx):\n    raise RuntimeError('tool boom')\n"
    boom_model = "def before_model_callback(ctx, req):\n    raise RuntimeError('model boom')\n"

    # (a) Tool fallback returns {"error": True}
    tool_runner = CallbackChainRunner.from_callbacks(
        {"before_tool": [("bt", boom_tool)]},
        new_session=False,
        error_handling_settings={"errorHandlingStrategy": "FALLBACK_RESPONSE"},
    )
    t_out, t_trace = tool_runner.run_before_tool(
        ToolContext(), Tool(name="t", description=""), {}
    )
    assert t_trace.error_outcome == "fallback"
    assert t_out == {"error": True}

    # (b) END_SESSION strategy produces reason="errors"
    es_runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm", boom_model)]},
        new_session=False,
        error_handling_settings={
            "errorHandlingStrategy": "END_SESSION",
            "endSessionConfig": {"escalateSession": False},
        },
    )
    es_out, es_trace = es_runner.run_before_model(CallbackContext())
    assert es_trace.error_outcome == "end_session"
    assert es_out.content.parts[0].function_call.args == {
        "reason": "errors",
        "session_escalated": False,
    }

    # (c) force_enable_exception_handling=True overrides strategy="NONE"
    force_runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm", boom_model)]},
        new_session=False,
        enable_exception_handling=False,
        force_enable_exception_handling=True,
        error_handling_settings={"errorHandlingStrategy": "NONE"},
    )
    f_out, f_trace = force_runner.run_before_model(CallbackContext())
    assert f_trace.error_outcome == "fallback"
    assert f_out.content.parts[0].text == "Sorry, something went wrong."

    # (d) Empty app_config (proto default UNSPECIFIED) -> fallback
    default_runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm", boom_model)]},
        new_session=False,
        app_config={"displayName": "app_without_error_settings"},
    )
    d_out, d_trace = default_runner.run_before_model(CallbackContext())
    assert d_trace.error_outcome == "fallback"
    assert d_out.content.parts[0].text == "Sorry, something went wrong."


def test_raise_on_error_true_propagates_exception() -> None:
    """Verifies raise_on_error=True re-raises callback exceptions immediately."""
    cb = "def before_agent_callback(ctx):\n    raise ValueError('explicit boom')\n"
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb)]},
        new_session=False,
        raise_on_error=True,
    )
    with pytest.raises(ValueError, match="explicit boom"):
        runner.run_before_agent(CallbackContext())


# ==============================================================================
# 8. Additional V1/V2 Sandbox Parity & Edge-Case Regression Tests
# ==============================================================================


def test_before_tool_internal_type_error_invoked_once() -> None:
    """Verifies internal TypeError inside before_tool is not retried by kwargs fallback."""
    cb = (
        "def before_tool_callback(tool, input, tool_context):\n"
        "    print('CALLED')\n"
        "    return input + 1\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_tool": [("bt_01", cb)]}, new_session=False
    )
    _, trace = runner.run_before_tool(
        ToolContext(), Tool(name="t", description=""), {"a": 1}
    )
    assert trace.stdout == "CALLED\n"
    assert "unsupported operand type(s) for +" in (trace.error or "")


def test_before_tool_and_after_tool_empty_dict_or_non_dict_does_not_short_circuit() -> (
    None
):
    """Verifies tool callback return values ({} and non-dict) under v1 and v2."""
    bt1 = "def before_tool_callback(tool, input, ctx):\n    return {}\n"
    bt2 = (
        "def before_tool_callback(tool, input, ctx):\n"
        "    ctx.state['bt2_ran'] = True\n"
        "    return None\n"
    )
    at1 = "def after_tool_callback(tool, input, ctx, resp):\n    return {}\n"
    at2 = (
        "def after_tool_callback(tool, input, ctx, resp):\n"
        "    ctx.state['at2_ran'] = True\n"
        "    return 'not_a_dict'\n"
    )

    # In V2 + per_callback: both {} and 'not_a_dict' are treated as None
    runner_v2 = CallbackChainRunner.from_callbacks(
        {
            "before_tool": [("bt1", bt1), ("bt2", bt2)],
            "after_tool": [("at1", at1), ("at2", at2)],
        },
        new_session=False,
        image_version="v2",
    )
    ctx = ToolContext()
    tool = Tool(name="t", description="")
    bt_out, bt_trace = runner_v2.run_before_tool(ctx, tool, {"k": "v"})
    assert bt_out is None
    assert not bt_trace.short_circuited
    assert bt_trace.executed_callbacks == ["bt1", "bt2"]
    assert ctx.state["bt2_ran"] is True

    at_out, at_trace = runner_v2.run_after_tool(ctx, tool, {"k": "v"}, {"r": 1})
    assert at_out is None
    assert not at_trace.short_circuited
    assert at_trace.executed_callbacks == ["at1", "at2"]
    assert ctx.state["at2_ran"] is True

    # In V1 (default): returning 'not_a_dict' fails pydantic validation -> fallback {'error': True}
    runner_v1 = CallbackChainRunner.from_callbacks(
        {
            "before_tool": [("bt1", bt1), ("bt2", bt2)],
            "after_tool": [("at1", at1), ("at2", at2)],
        },
        new_session=False,
        image_version="v1",
    )
    v1_out, v1_trace = runner_v1.run_after_tool(
        ToolContext(), tool, {"k": "v"}, {"r": 1}
    )
    assert v1_trace.failed_callback == "at2"
    assert v1_trace.error_outcome == "fallback"
    assert v1_out == {"error": True}


def test_orjson_non_str_keys_and_combined_end_of_chain_serialization_failure() -> (
    None
):
    """Verifies v2 orjson non-str dict key failure and end_of_chain_serialization attribution."""
    cb1 = (
        "def before_agent_callback(ctx):\n"
        "    ctx.state['bad_map'] = {1: 'one'}\n"
    )
    cb2 = (
        "def before_agent_callback(ctx):\n"
        "    ctx.state['cb2_saw'] = ctx.state['bad_map'][1]\n"
    )
    runner_v2 = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb1), ("cb_02", cb2)]},
        new_session=False,
        image_version="v2",
    )
    diff = runner_v2.compare_modes(CallbackContext(), stage="before_agent")
    assert diff.has_error_diff is True
    assert diff.has_execution_order_diff is True
    assert diff.per_callback_trace.failed_callback == "cb_01"
    assert diff.per_callback_trace.error_phase == "callback_execution"
    assert diff.per_callback_trace.executed_callbacks == ["cb_01"]
    assert diff.combined_trace.failed_callback is None
    assert diff.combined_trace.error_phase == "end_of_chain_serialization"
    assert diff.combined_trace.culprit_callback == "cb_01"
    assert diff.combined_trace.executed_callbacks == ["cb_01", "cb_02"]

    # Also test NaN/Inf serialization in orjson
    payload = orjson.loads(
        orjson.dumps({"nan": float("nan"), "inf": float("inf")})
    )
    assert payload == {"nan": None, "inf": None}


def test_simulate_struct_numbers_mode_difference() -> None:
    """Verifies int becomes float via Protobuf Struct in per_callback vs int in combined."""
    cb1 = "def before_agent_callback(ctx):\n    ctx.state['count'] = 3\n"
    cb2 = (
        "def before_agent_callback(ctx):\n"
        "    ctx.state['type_name'] = type(ctx.state['count']).__name__\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb1), ("cb_02", cb2)]},
        new_session=False,
        simulate_struct_numbers=True,
    )
    diff = runner.compare_modes(CallbackContext(), stage="before_agent")
    assert diff.has_state_diff is True
    assert diff.per_callback_trace.final_state["type_name"] == "float"
    assert diff.combined_trace.final_state["type_name"] == "int"


def test_deleted_default_variable_not_reinjected_across_stages() -> None:
    """Verifies a deleted default variable is not re-injected in subsequent stages."""
    ba = "def before_agent_callback(ctx):\n    del ctx.state['temp_var']\n"
    aa = "def after_agent_callback(ctx):\n    ctx.state['reinjected'] = 'temp_var' in ctx.state\n"
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba)], "after_agent": [("aa", aa)]},
        app_config={
            "variableDeclarations": [
                {
                    "name": "temp_var",
                    "schema": {"type": "STRING", "default": "hello"},
                }
            ]
        },
    )
    ctx = CallbackContext()
    runner.run_before_agent(ctx)
    assert "temp_var" not in ctx.state
    runner.run_after_agent(ctx)
    assert ctx.state["reinjected"] is False


@pytest.mark.parametrize("image_version", ["v1", "v2"])
def test_image_version_v1_vs_v2_parameterized(
    image_version: str,
) -> None:
    """Verifies v1 vs v2 sandbox image behavior across state, request, and tool callbacks."""
    cb_nested_int = (
        "import datetime, uuid\n"
        "def before_agent_callback(ctx):\n"
        "    ctx.state['nested'] = {1: 'v'}\n"
        "    ctx.state['ts'] = datetime.datetime(2026, 10, 16, 12, 30, 0)\n"
        "    ctx.state['day'] = datetime.date(2026, 10, 16)\n"
        "    ctx.state['uid'] = uuid.UUID('12345678-1234-5678-1234-567812345678')\n"
    )
    runner_state = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_nested", cb_nested_int)]},
        new_session=False,
        image_version=image_version,  # type: ignore[arg-type]
    )
    _, tr_state = runner_state.run_before_agent(CallbackContext(state={}))
    assert tr_state.image_version == image_version
    if image_version == "v1":
        assert tr_state.error is None
        assert tr_state.final_state["nested"] == {"1": "v"}
        assert tr_state.final_state["ts"].startswith("2026-10-16T12:30:00")
        assert tr_state.final_state["day"] == "2026-10-16"
        assert (
            tr_state.final_state["uid"]
            == "12345678-1234-5678-1234-567812345678"
        )
    else:
        assert tr_state.error is not None
        assert "TypeError" in tr_state.error

    cb_top_int = "def before_agent_callback(ctx):\n    ctx.state[1] = 'bad'\n"
    runner_top = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_top", cb_top_int)]},
        new_session=False,
        image_version=image_version,  # type: ignore[arg-type]
    )
    _, tr_top = runner_top.run_before_agent(CallbackContext(state={}))
    assert tr_top.error is not None
    if image_version == "v1":
        assert "ValidationError" in tr_top.error
    else:
        assert "TypeError" in tr_top.error

    class Color(enum.Enum):
        RED = "red"

    @dataclasses.dataclass
    class Point:
        x: int
        y: int

    cb_native = (
        "import datetime\n"
        "def before_agent_callback(ctx):\n"
        "    ctx.state['dt'] = datetime.datetime(2026, 10, 16, 8, 0, 0)\n"
        "    ctx.state['color'] = injected_color\n"
        "    ctx.state['pt'] = injected_point\n"
    )
    runner_native = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_native", cb_native)]},
        new_session=False,
        image_version=image_version,  # type: ignore[arg-type]
    )
    _, tr_native = runner_native.run_before_agent(
        CallbackContext(state={}),
        extra_globals={
            "injected_color": Color.RED,
            "injected_point": Point(1, 2),
        },
    )
    assert tr_native.error is None
    assert tr_native.final_state["color"] == "red"
    assert tr_native.final_state["pt"] == {"x": 1.0, "y": 2.0}

    bm_set = (
        "def before_model_callback(ctx, req):\n"
        "    req.config = GenerateContentConfig(\n"
        "        system_instruction=Content(role='system', parts=[Part.from_text('initial sys')])\n"
        "    )\n"
    )
    bm_clear = (
        "def before_model_callback(ctx, req):\n"
        "    req.config.system_instruction = None\n"
    )
    bm_inspect = (
        "def before_model_callback(ctx, req):\n"
        "    ctx.state['saw_sys_none'] = (req.config.system_instruction is None)\n"
    )
    runner_bm = CallbackChainRunner.from_callbacks(
        {
            "before_model": [
                ("bm_set", bm_set),
                ("bm_clear", bm_clear),
                ("bm_inspect", bm_inspect),
            ]
        },
        new_session=False,
        image_version=image_version,  # type: ignore[arg-type]
    )
    _, tr_bm = runner_bm.run_before_model(
        CallbackContext(state={}), LlmRequest()
    )
    if image_version == "v1":
        assert tr_bm.final_state["saw_sys_none"] is False
    else:
        assert tr_bm.final_state["saw_sys_none"] is True

    bt_non_dict = (
        "def before_tool_callback(tool, input, ctx):\n    return 'invalid'\n"
    )
    bt_after = "def before_tool_callback(tool, input, ctx):\n    ctx.state['ran_after'] = True\n"
    runner_bt = CallbackChainRunner.from_callbacks(
        {"before_tool": [("bt1", bt_non_dict), ("bt2", bt_after)]},
        new_session=False,
        image_version=image_version,  # type: ignore[arg-type]
    )
    bt_out, tr_bt = runner_bt.run_before_tool(
        ToolContext(state={}), Tool(name="t", description=""), {}
    )
    if image_version == "v1":
        assert tr_bt.failed_callback == "bt1"
        assert tr_bt.error_outcome == "fallback"
        assert bt_out == {"error": True}
    else:
        assert tr_bt.error is None
        assert bt_out is None
        assert tr_bt.final_state["ran_after"] is True

    diff_report = runner_state.compare_modes(
        CallbackContext(state={}),
        stage="before_agent",
        image_version=image_version,  # type: ignore[arg-type]
    )
    assert diff_report.image_version == image_version
    assert diff_report.per_callback_trace.image_version == image_version
    assert diff_report.combined_trace.image_version == image_version


def test_combined_mode_tool_callback_empty_dict_breaks_and_compare_modes_detects_diff() -> (
    None
):
    """Verifies combined mode tool callback returning {} breaks loop and compare_modes flags it."""
    c1 = "def before_tool_callback(tool, input, callback_context):\n    return {}\n"
    c2 = (
        "def before_tool_callback(tool, input, callback_context):\n"
        "    callback_context.state['second_ran'] = True\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_tool": [("c1", c1), ("c2", c2)]},
        new_session=False,
    )
    report = runner.compare_modes(
        ToolContext(state={}),
        stage="before_tool",
        tool=Tool(name="t", description=""),
        tool_input={"a": 1},
    )
    assert report.per_callback_trace.executed_callbacks == ["c1", "c2"]
    assert report.combined_trace.executed_callbacks == ["c1"]
    assert report.combined_trace.short_circuited_by == "c1"
    assert report.combined_trace.skipped_callbacks == ["c2"]
    assert report.combined_trace.final_response is None
    assert report.has_execution_order_diff is True
    assert report.has_state_diff is True
    assert report.has_diff is True
    explanations = report.explain()
    assert any("empty dict `{}`" in msg for msg in explanations)


def test_simulate_struct_numbers_coerces_initial_state_and_tool_payloads() -> (
    None
):
    """Verifies simulate_struct_numbers=True coerces initial state and tool payloads to float."""
    b1 = "def before_agent_callback(ctx):\n    ctx.state['t1'] = type(ctx.state['n']).__name__\n"
    b2 = "def before_agent_callback(ctx):\n    ctx.state['t2'] = type(ctx.state['n']).__name__\n"
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("b1", b1), ("b2", b2)]},
        new_session=False,
        simulate_struct_numbers=True,
    )
    _, tr_per = runner.run_before_agent(
        CallbackContext(state={"n": 5}), mode="per_callback"
    )
    _, tr_comb = runner.run_before_agent(
        CallbackContext(state={"n": 5}), mode="combined"
    )
    assert tr_per.final_state["t1"] == "float"
    assert tr_per.final_state["t2"] == "float"
    assert tr_comb.final_state["t1"] == "float"
    assert tr_comb.final_state["t2"] == "float"

    at_cb = (
        "def after_tool_callback(tool, input, ctx, resp):\n"
        "    ctx.state['in_type'] = type(input['count']).__name__\n"
        "    ctx.state['resp_type'] = type(resp['total']).__name__\n"
    )
    runner_tool = CallbackChainRunner.from_callbacks(
        {"after_tool": [("at1", at_cb)]},
        new_session=False,
        simulate_struct_numbers=True,
    )
    _, tr_tool = runner_tool.run_after_tool(
        ToolContext(state={}),
        Tool(name="t", description=""),
        {"count": 3},
        {"total": 7},
    )
    assert tr_tool.final_state["in_type"] == "float"
    assert tr_tool.final_state["resp_type"] == "float"


def test_strict_mode_and_raise_for_error() -> None:
    """Verifies CallbackTrace.raise_for_error(), TurnTrace.raise_for_error(), and strict=True."""
    bad_cb = "def after_model_callback(ctx, resp):\n    return {'not': 'an_llm_response'}\n"
    runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("bad_am", bad_cb)]}, new_session=False
    )

    out, trace = runner.run_after_model(
        CallbackContext(), LlmResponse.from_parts([Part.from_text("ok")])
    )
    assert trace.error_outcome == "fallback"
    assert out is not None
    with pytest.raises(TypeError, match="Expected LlmResponse"):
        trace.raise_for_error()

    with pytest.raises(TypeError, match="Expected LlmResponse"):
        runner.run_after_model(
            CallbackContext(),
            LlmResponse.from_parts([Part.from_text("ok")]),
            strict=True,
        )

    strict_runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("bad_am", bad_cb)]},
        new_session=False,
        strict=True,
    )
    with pytest.raises(TypeError, match="Expected LlmResponse"):
        strict_runner.run_after_model(
            CallbackContext(), LlmResponse.from_parts([Part.from_text("ok")])
        )


def test_synthesized_module_no_import_time_side_effects_and_supports_patch_globals() -> (
    None
):
    """Verifies _synthesize_chained_callback_module supports patch_globals and isolation."""
    cb1 = (
        "SIDE_EFFECT = 1 / 0 if False else 'cb1'\n"
        "def helper():\n    return 'from_cb1'\n"
        "def before_agent_callback(ctx):\n"
        "    ctx.state['h1'] = helper()\n"
        "    ctx.state['ext'] = EXTERNAL_TOKEN\n"
    )
    cb2 = (
        "def helper():\n    return 'from_cb2'\n"
        "def before_agent_callback(ctx):\n"
        "    ctx.state['h2'] = helper()\n"
    )
    mod_code = _synthesize_chained_callback_module(
        "before_agent", [("cb_01", cb1), ("cb_02", cb2)]
    )
    ns: dict[str, object] = {}
    exec(mod_code, ns)

    assert "helper" not in ns
    assert ns["callbacks"]["cb_01"].helper() == "from_cb1"  # type: ignore[index]
    assert ns["callbacks"]["cb_02"].helper() == "from_cb2"  # type: ignore[index]

    ns["patch_globals"](EXTERNAL_TOKEN="patched_value")  # type: ignore[operator]
    ctx = CallbackContext(state={})
    ns["before_agent_callback"](ctx)  # type: ignore[operator]
    assert ctx.state["h1"] == "from_cb1"
    assert ctx.state["h2"] == "from_cb2"
    assert ctx.state["ext"] == "patched_value"


def test_compare_modes_preserves_raw_trace_final_state_and_exposes_normalized_states() -> (
    None
):
    """Verifies compare_modes preserves raw final_state and stores normalized_states."""
    cb = "def before_agent_callback(ctx):\n    ctx.state['num'] = 7\n"
    runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb_01", cb)]},
        new_session=False,
        simulate_struct_numbers=False,
    )
    report = runner.compare_modes(
        CallbackContext(state={}), stage="before_agent"
    )
    assert isinstance(report.per_callback_trace.final_state["num"], int)
    assert isinstance(report.combined_trace.final_state["num"], int)
    assert "per_callback" in report.normalized_states
    assert "combined" in report.normalized_states
    assert report.normalized_states["per_callback"]["num"] == 7


def test_v1_dict_return_and_synthesized_module_deleted_default_semantics() -> (
    None
):
    """Verifies V1 accepts LlmResponse/Content dicts and synthesized module respects deletions."""
    am_dict = (
        "def after_model_callback(ctx, resp):\n"
        "    return {'content': {'role': 'model', 'parts': [{'text': 'from_dict'}]}}\n"
    )
    r_v1 = CallbackChainRunner.from_callbacks(
        {"after_model": [am_dict]}, new_session=False, image_version="v1"
    )
    out_v1, tr_v1 = r_v1.run_after_model(
        CallbackContext(), LlmResponse.from_parts([Part.from_text("orig")])
    )
    assert tr_v1.error is None
    assert out_v1.content.parts[0].text == "from_dict"

    cb_del = (
        "def before_agent_callback(ctx):\n"
        "    ctx.state['seen_temp'] = 'temp_var' in ctx.state\n"
        "    ctx.state.pop('temp_var', None)\n"
    )
    mod_code = _synthesize_chained_callback_module(
        "before_agent",
        [("cb_del", cb_del)],
        app_config={
            "variableDeclarations": [
                {
                    "name": "temp_var",
                    "schema": {"type": "STRING", "default": "init_val"},
                }
            ]
        },
    )
    ns: dict[str, object] = {}
    exec(mod_code, ns)
    ctx = CallbackContext(state={})
    ns["before_agent_callback"](ctx)  # type: ignore[operator]
    assert ctx.state["seen_temp"] is True
    assert "temp_var" not in ctx.state
    ns["before_agent_callback"](ctx)  # type: ignore[operator]
    assert ctx.state["seen_temp"] is False


def test_blob_str_part_from_json_and_fixture_roundtrip(
    tmp_path: Path,
) -> None:
    """Verifies Blob(data=str) and Part.from_json preserve UTF-8 bytes and fixtures round-trip."""
    b_str = Blob(data="hello", mime_type="text/plain")
    assert b_str.data == b"hello"
    b_model = Blob.model_validate({"data": "hello", "mime_type": "text/plain"})
    assert b_model.data == b"hello"

    p_json = Part.from_json(data='{"k":1}')
    assert p_json.inline_data is not None
    assert p_json.inline_data.data == b"eyJrIjoxfQ=="
    assert p_json.inline_data.raw_data == b'{"k":1}'

    for ver in ("v1", "v2"):
        r = CallbackChainRunner.from_callbacks(
            {
                "after_model": [
                    (
                        "cb_json",
                        "def after_model_callback(callback_context, llm_response):\n"
                        "    return LlmResponse.from_parts([Part.from_json(data='{\"k\":1}')])\n",
                    )
                ]
            },
            image_version=ver,
        )
        out, tr = r.run_after_model(
            CallbackContext(), LlmResponse.from_parts([Part.from_text("in")])
        )
        assert tr.error is None
        assert out.content.parts[0].inline_data is not None
        assert out.content.parts[0].inline_data.data == b"eyJrIjoxfQ=="
        assert out.content.parts[0].inline_data.raw_data == b'{"k":1}'

    fix_path = tmp_path / "f1_fixture.json"
    r_rec = CallbackChainRunner.from_callbacks(
        {
            "after_model": [
                (
                    "cb_inspect",
                    "def after_model_callback(callback_context, llm_response):\n"
                    "    p0 = llm_response.content.parts[0].inline_data\n"
                    "    p1 = llm_response.content.parts[1].inline_data\n"
                    "    callback_context.state['png_len'] = len(p0.data)\n"
                    "    callback_context.state['json_raw'] = p1.raw_data.decode('utf-8')\n"
                    "    return None\n",
                )
            ]
        }
    )
    resp_in = LlmResponse.from_parts(
        [
            Part(
                inline_data=Blob(
                    data=b"\x89PNG\r\n\x1a\n\x00\xff", mime_type="image/png"
                )
            ),
            Part.from_json(data='{"k":1}'),
        ]
    )
    r_rec.record_fixture(
        CallbackContext(),
        "after_model",
        fix_path,
        llm_response=resp_in,
    )
    cmp = r_rec.compare_modes_from_fixture(fix_path)
    assert cmp.has_diff is False
    assert cmp.per_callback_trace.final_state["png_len"] == 10
    assert cmp.per_callback_trace.final_state["json_raw"] == '{"k":1}'
    out_parts = cmp.per_callback_trace.final_response.content.parts
    assert out_parts[0].inline_data.data == b"\x89PNG\r\n\x1a\n\x00\xff"
    assert out_parts[1].inline_data.data == b"eyJrIjoxfQ=="
    assert out_parts[1].inline_data.raw_data == b'{"k":1}'
