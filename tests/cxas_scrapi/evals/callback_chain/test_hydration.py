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

"""Unit tests for instruction/tool hydration, local Python tools, and transfer hops."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from cxas_scrapi.evals.callback_chain import (
    CHAIN_LLM_ENV,
    STATE_REMOVED_SENTINEL,
    CallbackChainRunner,
    SessionSimulator,
    build_agent_tool_declarations,
    build_contents_with_state_updates,
    extract_in_scope_params,
    load_local_python_tools,
    render_agent_instruction,
)
from cxas_scrapi.evals.callback_chain.hydration import (
    _extract_python_tool_schema,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    Event,
    EventActions,
    FunctionDeclaration,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    Part,
    Tool,
    UsageMetadata,
)


def _create_sample_app(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Creates a realistic multi-agent app directory structure in `tmp_path`."""
    app_dir = tmp_path / "sample_app"
    root_dir = app_dir / "agents" / "root_agent"
    sub_dir = app_dir / "agents" / "billing_agent"
    tool_dir = app_dir / "tools" / "check_balance" / "python_code"
    root_dir.mkdir(parents=True)
    sub_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)

    app_cfg = {
        "displayName": "Sample App",
        "rootAgent": "root_agent",
        "variableDeclarations": [
            {
                "name": "brand_name",
                "schema": {"type": "STRING", "default": "AcmeCorp"},
            }
        ],
    }
    (app_dir / "app.json").write_text(json.dumps(app_cfg), encoding="utf-8")

    root_cfg = {
        "displayName": "root_agent",
        "instruction": "agents/root_agent/instruction.txt",
        "tools": ["check_balance"],
        "childAgents": ["billing_agent"],
    }
    (root_dir / "root_agent.json").write_text(
        json.dumps(root_cfg), encoding="utf-8"
    )
    (root_dir / "instruction.txt").write_text(
        "Welcome to {{brand_name}} (tier={{customer_tier}}, optional={{opt_var?}}, "
        "missing={{missing_opt?}}, single={customer_tier}, unknown={{unresolved_placeholder}}).\n"
        "{@startModality_CHAT}Chat-only banner.{@endModality_CHAT}\n"
        "Use {@TOOL: check_balance} to inspect balances or route to {@AGENT: billing_agent}.",
        encoding="utf-8",
    )

    sub_cfg = {
        "displayName": "billing_agent",
        "instruction": "agents/billing_agent/instruction.txt",
        "beforeAgentCallbacks": [
            {
                "name": "sub_ba",
                "inlineCode": (
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['sub_ba_ran'] = True\n"
                ),
            }
        ],
        "beforeModelCallbacks": [
            {
                "name": "sub_bm",
                "inlineCode": (
                    "def before_model_callback(ctx, req):\n"
                    "    ctx.state['sub_bm_ran'] = True\n"
                ),
            }
        ],
        "afterModelCallbacks": [
            {
                "name": "sub_am",
                "inlineCode": (
                    "def after_model_callback(ctx, resp):\n"
                    "    ctx.state['sub_am_ran'] = True\n"
                ),
            }
        ],
    }
    (sub_dir / "billing_agent.json").write_text(
        json.dumps(sub_cfg), encoding="utf-8"
    )
    (sub_dir / "instruction.txt").write_text(
        "You are the Billing Specialist for {{brand_name}}.",
        encoding="utf-8",
    )

    tool_cfg = {
        "displayName": "check_balance",
        "pythonFunction": {
            "name": "check_balance",
            "pythonCode": "tools/check_balance/python_code/python_code.py",
            "description": "Checks the account balance for a customer.",
        },
    }
    (app_dir / "tools" / "check_balance" / "check_balance.json").write_text(
        json.dumps(tool_cfg), encoding="utf-8"
    )
    (tool_dir / "python_code.py").write_text(
        "from typing import Any\n\n"
        "def run(account_id: str, include_pending: bool = False) -> dict[str, Any]:\n"
        '    """Returns account balance information."""\n'
        "    return {'account_id': account_id, 'balance': 250.0, 'pending': include_pending}\n",
        encoding="utf-8",
    )
    return app_dir, root_dir, sub_dir


def test_render_agent_instruction_variables_only_default(
    tmp_path: Path,
) -> None:
    """Verifies default variables_only mode, {{var}}, single-brace preservation, and tags."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(root_dir, app_dir=app_dir)

    state = runner.build_initial_state(
        {"customer_tier": "Gold", "opt_var": "present"},
        new_session=True,
    )
    rendered = render_agent_instruction(
        agent_dir=root_dir,
        app_dir=app_dir,
        state=state,
    )

    assert (
        "Welcome to AcmeCorp (tier=Gold, optional={{opt_var?}}, missing={{missing_opt?}}, "
        "single={customer_tier}, unknown={{unresolved_placeholder}})."
    ) in rendered
    assert (
        "{{% if _session.modality == 'CHAT' %}}Chat-only banner.{{% endif %}}"
        in rendered
    )
    assert (
        "Use check_balance to inspect balances or route to billing_agent."
        in rendered
    )
    assert "`check_balance`" not in rendered
    assert "{@TOOL:" not in rendered
    assert "{@AGENT:" not in rendered
    assert runner.render_instruction(state=state) == rendered


def test_render_agent_instruction_full_mode_and_prefixes() -> None:
    """Verifies template_engine='full', prefixes, optional ?, and single-brace preservation."""
    raw = (
        "User={{user:profile.name}} Active={{app:is_active}} "
        "Meta={{temp:meta}} Tags={{tags}} Opt={{opt_present?}} MissingOpt={{opt_missing?}} "
        "Single={single_var} "
        "{@startModality_VOICE}Voice active{@endModality_VOICE}"
        "{@startModality_CHAT}Chat active{@endModality_CHAT}"
    )
    state = {
        "profile": {"name": "Alice"},
        "is_active": True,
        "meta": {"k": 1},
        "tags": ["vip", 2],
        "opt_present": "yes",
        "single_var": "resolved_single",
        "modality": "VOICE",
    }
    rendered = render_agent_instruction(
        instruction_text=raw,
        state=state,
        template_engine="full",
    )
    assert "User=Alice" in rendered
    assert "Active=true" in rendered
    assert "Meta={k=1}" in rendered
    assert "Tags=[vip, 2]" in rendered
    assert "Opt=yes MissingOpt= Single={single_var}" in rendered
    assert "Voice active" in rendered
    assert "Chat active" not in rendered

    rendered_sub = render_agent_instruction(
        instruction_text=raw,
        state=state,
        template_engine="full",
        substitute_single_brace=True,
    )
    assert "Single=resolved_single" in rendered_sub


def test_nested_and_mismatched_modality_tags_in_full_mode() -> None:
    """Verifies nested modality/channel tags and mismatched tag resilience."""
    raw = (
        "{@startModality_VOICE}"
        "OuterVoice("
        "{@startChannel_WEB}InnerWeb{@endChannel_WEB}"
        "{@startChannel_MOBILE}InnerMobile{@endChannel_MOBILE}"
        ")"
        "{@endModality_VOICE}"
        "{@startModality_CHAT}ChatBlock{@endModality_CHAT}"
    )
    rendered = render_agent_instruction(
        instruction_text=raw,
        state={"modality": "VOICE", "channel": "WEB"},
        template_engine="full",
    )
    assert rendered == "OuterVoice(InnerWeb)"


def test_build_tool_declarations_and_load_local_python_tools(
    tmp_path: Path,
) -> None:
    """Verifies FunctionDeclaration schema building and local Python tool loading."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(root_dir, app_dir=app_dir)

    decls = build_agent_tool_declarations(
        agent_dir=root_dir, app_dir=app_dir, include_end_session=True
    )
    by_name = {d.name: d for d in decls}

    assert "check_balance" in by_name
    assert "transfer_to_agent" in by_name
    assert "end_session" in by_name

    cb_decl = by_name["check_balance"]
    assert "Checks the account balance" in (cb_decl.description or "")
    assert cb_decl.parameters is not None
    assert cb_decl.parameters["properties"]["account_id"]["type"] == "STRING"
    assert (
        cb_decl.parameters["properties"]["include_pending"]["type"] == "BOOLEAN"
    )
    assert cb_decl.parameters["required"] == ["account_id"]

    tr_decl = by_name["transfer_to_agent"]
    assert tr_decl.parameters is not None
    assert tr_decl.parameters["properties"]["agent_name"]["enum"] == [
        "billing_agent"
    ]

    local_tools = load_local_python_tools(
        app_dir=app_dir, agent_config=runner.agent_config
    )
    assert "check_balance" in local_tools

    seen_requests = []

    def fake_llm(req: Any) -> LlmResponse:
        seen_requests.append(req)
        if len(seen_requests) == 1:
            return LlmResponse.from_parts(
                [
                    Part.from_function_call(
                        "check_balance",
                        {"account_id": "ACC-99", "include_pending": True},
                    )
                ]
            )
        return LlmResponse.from_parts([Part.from_text("Your balance is 250.0")])

    final_resp, tt = runner.run_turn(
        CallbackContext(state={"customer_tier": "VIP"}),
        llm=fake_llm,
        execute_tools=True,
    )
    assert final_resp is not None
    assert final_resp.content.parts[0].text == "Your balance is 250.0"
    assert len(tt.tool_steps) == 1
    assert tt.tool_steps[0].final_tool_response == {
        "account_id": "ACC-99",
        "balance": 250.0,
        "pending": True,
    }
    assert seen_requests[0].config.system_instruction is not None
    assert "AcmeCorp" in str(seen_requests[0].config.system_instruction)
    assert len(seen_requests[0].config.tools) == 1


def test_end_session_conditional_injection(tmp_path: Path) -> None:
    """Verifies end_session is only injected when declared or referenced."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)

    # Default: root_agent does not list or reference end_session -> omitted
    decls_default = build_agent_tool_declarations(
        agent_dir=root_dir, app_dir=app_dir
    )
    assert "end_session" not in {d.name for d in decls_default}

    # Explicit include_end_session=True -> included
    decls_forced = build_agent_tool_declarations(
        agent_dir=root_dir, app_dir=app_dir, include_end_session=True
    )
    assert "end_session" in {d.name for d in decls_forced}

    # Referenced in instruction.txt -> included automatically
    (root_dir / "instruction.txt").write_text(
        "Call {@TOOL: end_session} when done.", encoding="utf-8"
    )
    decls_ref = build_agent_tool_declarations(
        agent_dir=root_dir, app_dir=app_dir
    )
    assert "end_session" in {d.name for d in decls_ref}


def test_ast_tool_schema_posonly_multiline_docstring_and_union_anyof(
    tmp_path: Path,
) -> None:
    """Verifies AST schema extraction for posonlyargs, docstrings, and Union."""
    py_file = tmp_path / "complex_tool.py"
    py_file.write_text(
        "def run(pos_req: str, pos_opt: int = 5, /, norm_req: str = 'x', "
        "flex: str | int | None = None) -> dict:\n"
        '    """First line of summary.\n'
        "    Second line of summary.\n\n"
        "    Args:\n"
        "        pos_req: Positional required parameter.\n"
        "        flex: Multi-type parameter.\n"
        '    """\n'
        "    return {}\n",
        encoding="utf-8",
    )
    doc, schema = _extract_python_tool_schema(py_file)
    assert doc == "First line of summary. Second line of summary."
    assert schema["required"] == ["pos_req"]
    assert schema["properties"]["pos_req"]["description"] == (
        "Positional required parameter."
    )
    assert schema["properties"]["pos_opt"]["type"] == "INTEGER"
    flex_prop = schema["properties"]["flex"]
    assert flex_prop["nullable"] is True
    assert "type" not in flex_prop
    assert flex_prop["anyOf"] == [{"type": "STRING"}, {"type": "INTEGER"}]


def test_openapi_tool_schema_extraction(tmp_path: Path) -> None:
    """Verifies OpenAPI tool parameter extraction in build_agent_tool_declarations."""
    app_dir = tmp_path / "oa_app"
    tool_dir = app_dir / "tools" / "search_kb"
    tool_dir.mkdir(parents=True)
    (tool_dir / "search_kb.json").write_text(
        json.dumps(
            {
                "displayName": "search_kb",
                "openApiTool": {
                    "name": "search_kb",
                    "description": "Searches the knowledge base.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {"query": {"type": "STRING"}},
                        "required": ["query"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    decls = build_agent_tool_declarations(
        app_dir=app_dir,
        agent_config={"displayName": "oa_agent", "tools": ["search_kb"]},
    )
    by_name = {d.name: d for d in decls}
    assert "search_kb" in by_name
    assert by_name["search_kb"].parameters["required"] == ["query"]


def test_session_simulator_auto_follow_transfers(tmp_path: Path) -> None:
    """Verifies SessionSimulator with auto_follow_transfers=True hops to sub_agent."""
    app_dir, _, _ = _create_sample_app(tmp_path)

    sim = SessionSimulator.from_app_dir(
        app_dir,
        auto_follow_transfers=True,
    )
    assert sim.current_agent == "root_agent"

    tt = sim.turn(
        "I have a billing dispute",
        llm=[
            LlmResponse.from_parts(
                [
                    Part.from_function_call(
                        "transfer_to_agent", {"agent_name": "billing_agent"}
                    )
                ]
            ),
            LlmResponse.from_parts(
                [Part.from_text("Hello from Billing Specialist!")]
            ),
        ],
    )

    assert sim.current_agent == "billing_agent"
    assert tt.routed_agents == ["root_agent", "billing_agent"]
    assert tt.transfer_to is None
    assert sim.is_agent_entry() is False
    assert len(tt.agent_hops) == 2
    assert tt.agent_hops[0].transfer_to == "billing_agent"
    assert tt.agent_hops[1].transfer_to is None
    assert tt.final_response is not None
    assert (
        tt.final_response.content.parts[0].text
        == "Hello from Billing Specialist!"
    )
    assert sim.state.get("sub_ba_ran") is True
    assert sim.state.get("sub_bm_ran") is True
    assert sim.state.get("sub_am_ran") is True
    assert len(tt.llm_steps) == 2


def test_mid_turn_state_mutation_rehydrates_instruction_on_hop_2(
    tmp_path: Path,
) -> None:
    """Verifies mid-turn state updates re-render system_instruction on Hop 2."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    runner = CallbackChainRunner.from_agent_dir(
        root_dir,
        app_dir=app_dir,
    )
    runner.set_callbacks(
        "after_tool",
        [
            (
                "upgrade_tier",
                "def after_tool_callback(tool, args, ctx, resp):\n"
                "    ctx.state['customer_tier'] = 'Diamond'\n",
            )
        ],
    )

    seen_instructions: list[str] = []
    seen_tools: list[list[str]] = []

    def fake_llm(req: LlmRequest) -> LlmResponse:
        seen_instructions.append(str(req.config.system_instruction))
        decls = (
            req.config.tools[0].function_declarations
            if req.config.tools
            else []
        )
        seen_tools.append([d.name for d in decls])
        if len(seen_instructions) == 1:
            return LlmResponse.from_parts(
                [Part.from_function_call("check_balance", {"account_id": "A1"})]
            )
        return LlmResponse.from_parts([Part.from_text("Done")])

    custom_tool = Tool(
        function_declarations=[
            FunctionDeclaration(
                name="custom_override_tool",
                description="Caller override tool",
                parameters={"type": "OBJECT", "properties": {}},
            )
        ]
    )
    runner.run_turn(
        CallbackContext(state={"customer_tier": "Bronze"}),
        llm_request=LlmRequest(
            config=GenerateContentConfig(tools=[custom_tool])
        ),
        llm=fake_llm,
        execute_tools=True,
    )

    assert len(seen_instructions) == 2
    assert "tier=Bronze" in seen_instructions[0]
    assert "tier=Diamond" in seen_instructions[1]
    # Caller-provided tools override is preserved across both hops
    assert seen_tools[0] == ["custom_override_tool"]
    assert seen_tools[1] == ["custom_override_tool"]


def test_deferred_transfer_pairs_function_response_in_history_and_routed_agents(
    tmp_path: Path,
) -> None:
    """Verifies deferred transfer FunctionResponse pairing and routed_agents."""
    app_dir, _, _ = _create_sample_app(tmp_path)
    sim = SessionSimulator.from_app_dir(app_dir, auto_follow_transfers=False)

    t1 = sim.turn(
        "Transfer me to billing",
        llm=LlmResponse.from_parts(
            [
                Part.from_function_call(
                    "transfer_to_agent", {"agent_name": "billing_agent"}
                )
            ]
        ),
    )
    assert t1.transfer_to == "billing_agent"
    assert t1.routed_agents == ["root_agent"]
    assert sim.current_agent == "billing_agent"
    assert sim.is_agent_entry() is True

    captured_requests: list[LlmRequest] = []

    def capture_turn2(req: LlmRequest) -> LlmResponse:
        captured_requests.append(req)
        return LlmResponse.from_parts([Part.from_text("Billing agent here.")])

    t2 = sim.turn("Check my invoice", llm=capture_turn2)
    assert t2.routed_agents == ["billing_agent"]
    assert len(captured_requests) == 1
    contents = captured_requests[0].contents
    # Expect: [Turn 1 user, Turn 1 model FunctionCall, paired FunctionResponse, Turn 2 user]
    assert len(contents) == 4
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "transfer_to_agent"
    assert contents[2].role == "user"
    assert contents[2].parts[0].function_response is not None
    assert contents[2].parts[0].function_response.name == "transfer_to_agent"
    assert contents[3].role == "user"
    assert contents[3].parts[0].text == "Check my invoice"


def test_app_config_template_engine_type_default_detection() -> None:
    """Verifies app_config variableDeclarations default for template_engine_type is honored."""
    app_cfg = {
        "variableDeclarations": [
            {
                "name": "template_engine_type",
                "schema": {"type": "STRING", "default": "full"},
            }
        ]
    }
    raw = "{@startModality_CHAT}ChatMode:{tier}{@endModality_CHAT}"
    rendered = render_agent_instruction(
        instruction_text=raw,
        app_config=app_cfg,
        state={"modality": "CHAT", "tier": "Platinum"},
    )
    assert rendered == "ChatMode:{tier}"


def test_substitute_single_brace_override_in_variables_only_mode() -> None:
    """Verifies substitute_single_brace=True works alongside variables_only mode."""
    raw = (
        "Hello {name}! {@startModality_VOICE}Speak clearly.{@endModality_VOICE}"
    )
    rendered = render_agent_instruction(
        instruction_text=raw,
        state={"name": "Bob"},
        template_engine="variables_only",
        substitute_single_brace=True,
    )
    assert "Hello Bob!" in rendered
    assert (
        "{{% if _session.modality == 'VOICE' %}}Speak clearly.{{% endif %}}"
        in rendered
    )


def test_data_store_tool_declaration_building() -> None:
    """Verifies dataStoreTool entries in agent_config produce FunctionDeclarations."""
    agent_cfg = {
        "displayName": "ds_agent",
        "dataStoreTool": {
            "name": "faq_datastore",
            "description": "Searches FAQ documents.",
        },
    }
    decls = build_agent_tool_declarations(agent_config=agent_cfg)
    by_name = {d.name: d for d in decls}
    assert "faq_datastore" in by_name
    assert by_name["faq_datastore"].parameters["required"] == ["query"]


def test_dynamic_variable_state_update_injection_and_modes(
    tmp_path: Path,
) -> None:
    """Verifies <state_update> / <current_state> injection, filtering, and REMOVED sentinel."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    (root_dir / "instruction.txt").write_text(
        "Hello tier={customer_tier} opt={opt_var} static={{brand_name}}",
        encoding="utf-8",
    )
    runner = CallbackChainRunner.from_agent_dir(root_dir, app_dir=app_dir)
    runner.set_callbacks(
        "after_tool",
        [
            (
                "mutate_and_delete",
                "def after_tool_callback(tool, args, ctx, resp):\n"
                "    ctx.state['customer_tier'] = 'PLATINUM'\n"
                "    ctx.state.pop('opt_var', None)\n"
                "    ctx.state['[undeclared] secret'] = 'hidden'\n"
                "    ctx.state['out_of_scope'] = 'ignored'\n",
            )
        ],
    )

    sim = SessionSimulator(
        runner,
        state={
            "customer_tier": "GOLD",
            "opt_var": "init_val",
            "out_of_scope": "not_in_instruction",
            "[undeclared] foo": "bar",
        },
    )

    seen_contents_t1: list[list[Content]] = []

    def llm_turn1(req: LlmRequest) -> LlmResponse:
        seen_contents_t1.append(list(req.contents))
        if len(seen_contents_t1) == 1:
            return LlmResponse.from_parts(
                [Part.from_function_call("check_balance", {"account_id": "A1"})]
            )
        return LlmResponse.from_parts([Part.from_text("Turn 1 complete")])

    sim.turn("Check my balance", llm=llm_turn1)
    assert len(seen_contents_t1) == 2

    # Hop 1: rearrange puts initial <state_update> before Turn 1 user message
    hop1_texts = [
        p.text
        for c in seen_contents_t1[0]
        for p in (c.parts or [])
        if p.text is not None
    ]
    assert "<state_update>" in hop1_texts
    assert "customer_tier: GOLD" in hop1_texts
    assert "opt_var: init_val" in hop1_texts
    assert not any("out_of_scope" in t for t in hop1_texts)
    assert not any("undeclared" in t for t in hop1_texts)

    # Hop 2: rearrange updates initial <state_update> with latest values & REMOVED -> null
    hop2_texts = [
        p.text
        for c in seen_contents_t1[1]
        for p in (c.parts or [])
        if p.text is not None
    ]
    assert "customer_tier: PLATINUM" in hop2_texts
    assert "opt_var: null" in hop2_texts

    # Turn 2: if before_agent or a tool in Turn 2 mutates state, rearrange places
    # Turn 2's <state_update> right before Turn 2's user event, while Turn 1's
    # <state_update> stays before Turn 1's user event.
    runner.set_callbacks(
        "before_agent",
        [
            (
                "turn2_update",
                "def before_agent_callback(ctx):\n"
                "    ctx.state['customer_tier'] = 'EMERALD'\n",
            )
        ],
    )
    seen_contents_t2: list[list[Content]] = []

    def llm_turn2(req: LlmRequest) -> LlmResponse:
        seen_contents_t2.append(list(req.contents))
        return LlmResponse.from_parts([Part.from_text("Turn 2 complete")])

    sim.turn("What now?", llm=llm_turn2)
    t2_contents = seen_contents_t2[0]
    assert t2_contents[0].parts[0].text == "<state_update>"
    t1_delta_texts = [p.text for p in t2_contents[0].parts if p.text]
    assert "customer_tier: PLATINUM" in t1_delta_texts
    assert "opt_var: null" in t1_delta_texts
    assert t2_contents[-1].parts[0].text == "What now?"
    assert t2_contents[-2].parts[0].text == "<state_update>"
    t2_delta_texts = [p.text for p in t2_contents[-2].parts if p.text]
    assert "customer_tier: EMERALD" in t2_delta_texts

    # Verify in_place and current_state modes directly on build_contents_with_state_updates
    events = [
        Event(
            author="user",
            actions=EventActions(state_delta={"customer_tier": "SILVER"}),
        ),
        Event(
            author="user",
            content=Content(role="user", parts=[Part.from_text("Hi")]),
        ),
        Event(
            author="root_agent",
            content=Content(role="model", parts=[Part.from_text("Hello")]),
            actions=EventActions(
                state_delta={
                    "customer_tier": "DIAMOND",
                    "opt_var": STATE_REMOVED_SENTINEL,
                }
            ),
        ),
        Event(
            author="user",
            content=Content(role="user", parts=[Part.from_text("Follow up")]),
        ),
    ]
    in_place_contents = build_contents_with_state_updates(
        events, {"customer_tier", "opt_var"}, mode="in_place"
    )
    # In-place inserts <state_update> immediately after the event that produced the delta
    assert in_place_contents[0].parts[0].text == "<state_update>"
    assert "customer_tier: SILVER" in [
        p.text for p in in_place_contents[0].parts
    ]
    assert in_place_contents[3].parts[0].text == "<state_update>"
    assert "customer_tier: DIAMOND" in [
        p.text for p in in_place_contents[3].parts
    ]
    assert "opt_var: null" in [p.text for p in in_place_contents[3].parts]

    current_state_contents = build_contents_with_state_updates(
        events, {"customer_tier", "opt_var"}, mode="current_state"
    )
    # current_state inserts a single <current_state> right before the last user event
    assert current_state_contents[-1].parts[0].text == "Follow up"
    assert current_state_contents[-2].parts[0].text == "\n<current_state>\n"
    assert "customer_tier: DIAMOND\n" in [
        p.text for p in current_state_contents[-2].parts
    ]
    assert "opt_var: null\n" in [
        p.text for p in current_state_contents[-2].parts
    ]


def test_extract_in_scope_params_full_vs_variables_only() -> None:
    """Verifies extract_in_scope_params handles {var}, {{static}}, and {% if/for %}."""
    raw = (
        "Static={{brand_name}} Dyn={user:account.tier} "
        "{@startModality_VOICE}VoiceBlock{@endModality_VOICE}"
        "{% if _session.modality == 'VOICE' and flags.voice_enabled %}"
        "{% for item in cart.items %}{{item.name}}:{extra_fee}{% endfor %}"
        "{% endif %}"
    )
    vo_params = extract_in_scope_params(raw, template_engine="variables_only")
    assert vo_params == {"account", "extra_fee"}

    full_params = extract_in_scope_params(raw, template_engine="full")
    assert full_params == {"account", "extra_fee", "flags", "cart", "_session"}


def test_auto_follow_token_latency_and_max_agent_transfers_warning(
    tmp_path: Path,
) -> None:
    """Verifies token/latency aggregation, agent_hops, and max_agent_transfers warning."""
    app_dir, _root_dir, sub_dir = _create_sample_app(tmp_path)
    # Allow billing_agent to transfer back to root_agent for ping-pong test
    sub_cfg = json.loads(
        (sub_dir / "billing_agent.json").read_text(encoding="utf-8")
    )
    sub_cfg["childAgents"] = ["root_agent"]
    (sub_dir / "billing_agent.json").write_text(
        json.dumps(sub_cfg), encoding="utf-8"
    )

    sim = SessionSimulator.from_app_dir(app_dir, auto_follow_transfers=True)
    tt = sim.turn(
        "Route me to billing",
        llm=[
            LlmResponse(
                content=Content(
                    role="model",
                    parts=[
                        Part.from_function_call(
                            "transfer_to_agent", {"agent_name": "billing_agent"}
                        )
                    ],
                ),
                usage_metadata=UsageMetadata(
                    prompt_token_count=100,
                    candidates_token_count=20,
                    total_token_count=120,
                ),
            ),
            LlmResponse(
                content=Content(
                    role="model",
                    parts=[Part.from_text("Billing handled.")],
                ),
                usage_metadata=UsageMetadata(
                    prompt_token_count=40,
                    candidates_token_count=10,
                    total_token_count=50,
                ),
            ),
        ],
    )
    assert tt.prompt_tokens == 140
    assert tt.candidates_tokens == 30
    assert tt.total_tokens == 170
    assert tt.llm_duration_ms >= 0.0
    assert tt.callback_duration_ms >= 0.0
    assert tt.duration_ms >= tt.llm_duration_ms
    assert len(tt.agent_hops) == 2
    assert tt.agent_hops[0].total_tokens == 120
    assert tt.agent_hops[1].total_tokens == 50

    # Now test ping-pong hitting max_agent_transfers=2
    sim_loop = SessionSimulator.from_app_dir(
        app_dir, auto_follow_transfers=True, max_agent_transfers=2
    )
    targets = ["billing_agent", "root_agent", "billing_agent"]
    idx = {"i": 0}

    def ping_pong_llm(req: LlmRequest) -> LlmResponse:
        t = targets[idx["i"]]
        idx["i"] += 1
        return LlmResponse.from_parts(
            [Part.from_function_call("transfer_to_agent", {"agent_name": t})]
        )

    tt_loop = sim_loop.turn("Loop transfer", llm=ping_pong_llm)
    assert tt_loop.routed_agents == [
        "root_agent",
        "billing_agent",
        "root_agent",
    ]
    assert tt_loop.transfer_to == "billing_agent"
    assert sim_loop.current_agent == "billing_agent"
    assert sim_loop.is_agent_entry() is True
    assert any("max_agent_transfers=2" in w for w in tt_loop.warnings)


def test_cli_and_callback_evals_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies CLI parser and handlers for test-callbacks --llm."""
    from cxas_scrapi.cli.main import (  # noqa: PLC0415
        get_parser,
        test_callbacks,
    )
    from cxas_scrapi.evals.callback_evals import CallbackEvals  # noqa: PLC0415

    parser = get_parser()
    args_tc = parser.parse_args(
        [
            "test-callbacks",
            "--app-dir",
            str(tmp_path),
            "--llm",
            "gemini-2.5-flash",
        ]
    )
    assert args_tc.llm == "gemini-2.5-flash"

    # Reject --llm when combined with --lint-only
    args_incompat = parser.parse_args(
        [
            "test-callbacks",
            "--app-dir",
            str(tmp_path),
            "--lint-only",
            "--llm",
            "gemini-2.5-flash",
        ]
    )
    with pytest.raises(SystemExit) as exc_info:
        test_callbacks(args_incompat)
    assert exc_info.value.code == 2

    # Verify CallbackEvals.test_all_callbacks_in_app_dir sets and restores CXAS_CALLBACK_LLM
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    cb_dir = root_dir / "before_agent_callbacks" / "cb1"
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n    return None\n",
        encoding="utf-8",
    )
    (cb_dir / "test.py").write_text(
        "import os\n"
        "def test_env_injected():\n"
        f"    assert os.environ.get('{CHAIN_LLM_ENV}') == 'gemini-2.5-flash'\n",
        encoding="utf-8",
    )
    df = CallbackEvals().test_all_callbacks_in_app_dir(
        app_dir=str(app_dir),
        include_chain=False,
        llm="gemini-2.5-flash",
    )
    assert not df.empty
    assert list(df["status"]) == ["PASSED"]


def test_predefined_vars_deleted_defaults_and_in_place_current_state_ordering(
    tmp_path: Path,
) -> None:
    """Verifies predefined vars, deleted defaults, and state_update_mode ordering."""
    app_dir, root_dir, _ = _create_sample_app(tmp_path)
    (root_dir / "instruction.txt").write_text(
        "Date={current_date} Chan={_session.channel} Tier={brand_name} "
        "StaticTier={{brand_name}} Stage={stage_marker}",
        encoding="utf-8",
    )
    runner = CallbackChainRunner.from_agent_dir(root_dir, app_dir=app_dir)
    runner.set_callbacks(
        "before_model",
        [
            (
                "delete_default",
                "def before_model_callback(ctx, req):\n"
                "    if ctx.state.get('brand_name') == 'AcmeCorp':\n"
                "        del ctx.state['brand_name']\n"
                "    ctx.state['stage_marker'] = 'bm_t1'\n",
            )
        ],
    )
    runner.set_callbacks(
        "after_agent",
        [
            (
                "aa_mutate",
                "def after_agent_callback(ctx):\n"
                "    if ctx.turn_index == 1:\n"
                "        ctx.state['stage_marker'] = 'aa_t1'\n",
            )
        ],
    )

    # 1. Test default rearrange mode with current_date, _session, and deleted default
    sim_rearrange = SessionSimulator(runner, state={"stage_marker": "init"})
    seen_rearrange: list[LlmRequest] = []

    def llm_rearrange(req: LlmRequest) -> LlmResponse:
        seen_rearrange.append(req)
        return LlmResponse.from_parts(
            [Part.from_text(f"Reply {len(seen_rearrange)}")]
        )

    sim_rearrange.turn("Turn 1", llm=llm_rearrange)
    assert "brand_name" not in sim_rearrange.state
    t1_texts = [
        p.text
        for c in seen_rearrange[0].contents
        for p in (c.parts or [])
        if p.text is not None
    ]
    assert any(t.startswith("current_date: ") for t in t1_texts)
    assert "_session: {modality=TEXT, channel=API}" in t1_texts
    assert "brand_name: AcmeCorp" in t1_texts

    sim_rearrange.turn("Turn 2", llm=llm_rearrange)
    t2_sys = seen_rearrange[1].config.system_instruction.parts[0].text
    # Deleted default variable must remain literal {{brand_name}} and emit brand_name: null
    assert "StaticTier={{brand_name}}" in t2_sys
    t2_texts = [
        p.text
        for c in seen_rearrange[1].contents
        for p in (c.parts or [])
        if p.text is not None
    ]
    assert "brand_name: null" in t2_texts
    assert "stage_marker: aa_t1" in t2_texts

    # 2. Test multi-turn ordering in `in_place` mode (after_agent & before_model deltas
    # are attached to the agent's model event, so <state_update> appears AFTER "Reply 1")
    sim_in_place = SessionSimulator(
        runner, state={"stage_marker": "init"}, state_update_mode="in_place"
    )
    seen_in_place: list[LlmRequest] = []

    def llm_in_place(req: LlmRequest) -> LlmResponse:
        seen_in_place.append(req)
        return LlmResponse.from_parts(
            [Part.from_text(f"Reply {len(seen_in_place)}")]
        )

    sim_in_place.turn("Turn 1", llm=llm_in_place)
    sim_in_place.turn("Turn 2", llm=llm_in_place)
    ip_contents = seen_in_place[1].contents
    # Expected content sequence in `in_place` mode on Turn 2:
    # [0] <state_update> (initial session state)
    # [1] "Turn 1" (user)
    # [2] "Reply 1" (model)
    # [3] <state_update> (Turn 1 agent callbacks: brand_name: null, stage_marker: aa_t1)
    # [4] "Turn 2" (user)
    assert ip_contents[0].parts[0].text == "<state_update>"
    assert ip_contents[1].parts[0].text == "Turn 1"
    assert ip_contents[2].parts[0].text == "Reply 1"
    assert ip_contents[3].parts[0].text == "<state_update>"
    ip_agent_delta_texts = [p.text for p in ip_contents[3].parts if p.text]
    assert "brand_name: null" in ip_agent_delta_texts
    assert "stage_marker: aa_t1" in ip_agent_delta_texts
    assert ip_contents[4].parts[0].text == "Turn 2"

    # 3. Test multi-turn ordering in `current_state` mode
    sim_cs = SessionSimulator(
        runner,
        state={"stage_marker": "init"},
        state_update_mode="current_state",
    )
    seen_cs: list[LlmRequest] = []

    def llm_cs(req: LlmRequest) -> LlmResponse:
        seen_cs.append(req)
        return LlmResponse.from_parts([Part.from_text(f"Reply {len(seen_cs)}")])

    sim_cs.turn("Turn 1", llm=llm_cs)
    sim_cs.turn("Turn 2", llm=llm_cs)
    cs_contents = seen_cs[1].contents
    # Expected content sequence in `current_state` mode on Turn 2:
    # [0] "Turn 1" (user)
    # [1] "Reply 1" (model)
    # [2] "\n<current_state>\n" (accumulated state right before last user message)
    # [3] "Turn 2" (user)
    assert cs_contents[0].parts[0].text == "Turn 1"
    assert cs_contents[1].parts[0].text == "Reply 1"
    assert cs_contents[2].parts[0].text == "\n<current_state>\n"
    cs_state_texts = [p.text for p in cs_contents[2].parts if p.text]
    assert "brand_name: null\n" in cs_state_texts
    assert "stage_marker: aa_t1\n" in cs_state_texts
    assert cs_contents[3].parts[0].text == "Turn 2"


def test_five_stage_state_delta_attribution_and_ordering_snapshot(
    tmp_path: Path,
) -> None:
    """Verifies 5-stage state_delta attribution and contents snapshots."""
    app = tmp_path / "five_stage_app"
    ag = app / "agents" / "root_agent"
    ag.mkdir(parents=True)
    (ag / "instruction.txt").write_text(
        "ba={ba} bm={bm} am={am} bt={bt} at={at}", encoding="utf-8"
    )
    cbs = {
        "beforeAgentCallbacks": (
            "ba",
            "def before_agent_callback(callback_context):\n"
            "    callback_context.state['ba'] = f'BA{callback_context.turn_index}'\n",
        ),
        "beforeModelCallbacks": (
            "bm",
            "def before_model_callback(callback_context, llm_request):\n"
            "    callback_context.state['bm'] = callback_context.state.get('bm', 0) + 1\n",
        ),
        "afterModelCallbacks": (
            "am",
            "def after_model_callback(callback_context, llm_response):\n"
            "    callback_context.state['am'] = callback_context.state.get('am', 0) + 1\n",
        ),
        "beforeToolCallbacks": (
            "bt",
            "def before_tool_callback(tool, input, tool_context):\n"
            "    tool_context.state['bt'] = 'BT'\n",
        ),
        "afterToolCallbacks": (
            "at",
            "def after_tool_callback(tool, input, tool_context, tool_response):\n"
            "    tool_context.state['at'] = 'AT'\n",
        ),
    }
    cfg: dict[str, Any] = {
        "name": "root_agent",
        "displayName": "root_agent",
        "instruction": "agents/root_agent/instruction.txt",
        "tools": ["lookup"],
    }
    for key, (folder, code) in cbs.items():
        d = ag / folder / "cb1"
        d.mkdir(parents=True)
        (d / "python_code.py").write_text(code, encoding="utf-8")
        cfg[key] = [
            {"pythonCode": f"agents/root_agent/{folder}/cb1/python_code.py"}
        ]
    (ag / "root_agent.json").write_text(json.dumps(cfg), encoding="utf-8")

    td = app / "tools" / "lookup" / "python_function"
    td.mkdir(parents=True)
    (td / "python_code.py").write_text(
        "def lookup(q: str) -> dict:\n    return {'r': q}\n", encoding="utf-8"
    )
    (app / "tools" / "lookup" / "lookup.json").write_text(
        json.dumps(
            {
                "name": "lookup",
                "pythonFunction": {
                    "name": "lookup",
                    "pythonCode": "tools/lookup/python_function/python_code.py",
                },
            }
        ),
        encoding="utf-8",
    )
    (app / "app.json").write_text(
        json.dumps({"rootAgent": "root_agent"}), encoding="utf-8"
    )

    def _run_mode(
        mode: str,
    ) -> tuple[list[list[str]], list[tuple[str, bool, list[str]]]]:
        calls: list[LlmRequest] = []

        def _fake_llm(req: LlmRequest) -> LlmResponse:
            calls.append(copy.deepcopy(req))
            n = len(calls)
            if n == 1:
                parts = [Part.from_function_call("lookup", {"q": "x"})]
            else:
                parts = [Part.from_text(f"answer{n}")]
            return LlmResponse(
                content=Content(role="model", parts=parts),
                usage_metadata=UsageMetadata(
                    prompt_token_count=10,
                    candidates_token_count=5,
                    total_token_count=15,
                ),
            )

        sim = SessionSimulator.from_app_dir(
            app,
            llm=_fake_llm,
            state_update_mode=mode,  # type: ignore[arg-type]
        )
        sim.turn("t1")
        sim.turn("t2")
        flat_calls = [
            [
                getattr(p, "text", None)
                or (
                    "<fc>"
                    if getattr(p, "function_call", None)
                    else "<fr>"
                    if getattr(p, "function_response", None)
                    else "?"
                )
                for c in call.contents
                for p in (c.parts or [])
            ]
            for call in calls
        ]
        ev_summary = [
            (
                e.author,
                e.content is not None,
                sorted(e.actions.state_delta.keys()),
            )
            for e in sim.events
        ]
        return flat_calls, ev_summary

    rearrange_calls, rearrange_events = _run_mode("rearrange")
    in_place_calls, in_place_events = _run_mode("in_place")

    expected_events = [
        ("user", True, []),
        ("root_agent", False, ["ba"]),
        ("root_agent", True, ["am", "bm"]),
        ("root_agent", True, ["at", "bt"]),
        ("root_agent", True, ["am", "bm"]),
        ("user", True, []),
        ("root_agent", False, ["ba"]),
        ("root_agent", True, ["am", "bm"]),
    ]
    assert rearrange_events == expected_events
    assert in_place_events == expected_events

    assert rearrange_calls == [
        ["<state_update>", "ba: BA1", "</state_update>", "t1"],
        [
            "<state_update>",
            "bt: BT",
            "at: AT",
            "bm: 1.0",
            "am: 1.0",
            "ba: BA1",
            "</state_update>",
            "t1",
            "<fc>",
            "<fr>",
        ],
        [
            "<state_update>",
            "bm: 2.0",
            "am: 2.0",
            "bt: BT",
            "at: AT",
            "ba: BA1",
            "</state_update>",
            "t1",
            "<fc>",
            "<fr>",
            "answer2",
            "<state_update>",
            "ba: BA2",
            "</state_update>",
            "t2",
        ],
    ]

    assert in_place_calls == [
        ["t1", "<state_update>", "ba: BA1", "</state_update>"],
        [
            "t1",
            "<state_update>",
            "ba: BA1",
            "</state_update>",
            "<fc>",
            "<state_update>",
            "bm: 1.0",
            "am: 1.0",
            "</state_update>",
            "<fr>",
            "<state_update>",
            "bt: BT",
            "at: AT",
            "</state_update>",
        ],
        [
            "t1",
            "<state_update>",
            "ba: BA1",
            "</state_update>",
            "<fc>",
            "<state_update>",
            "bm: 1.0",
            "am: 1.0",
            "</state_update>",
            "<fr>",
            "<state_update>",
            "bt: BT",
            "at: AT",
            "</state_update>",
            "answer2",
            "<state_update>",
            "bm: 2.0",
            "am: 2.0",
            "</state_update>",
            "t2",
            "<state_update>",
            "ba: BA2",
            "</state_update>",
        ],
    ]


def _make_probe_event(
    author: str,
    text: str | None = None,
    *,
    role: str | None = None,
    fc: str | None = None,
    fr: str | None = None,
) -> Event:
    """Helper to construct Event instances for get_last_* parity tests."""
    if text is None and fc is None and fr is None:
        return Event(author=author, content=None)
    if fc is not None:
        part = Part.from_function_call(name=fc, args={})
    elif fr is not None:
        part = Part.from_function_response(name=fr, response={"ok": 1})
    else:
        part = Part.from_text(text or "")
    eff_role = role or ("user" if author == "user" else "model")
    return Event(author=author, content=Content(role=eff_role, parts=[part]))


def _v1_ref_last_user_input(events: list[Event]) -> list[Part]:
    """Reference implementation of CES v1 get_last_user_input."""
    parts: list[Part] = []
    for event in reversed(events):
        if parts and not event.is_user():
            break
        parts += event.parts()
    parts.reverse()
    return parts


def _v1_ref_last_agent_output(events: list[Event]) -> list[Part]:
    """Reference implementation of CES v1 get_last_agent_output."""
    parts: list[Part] = []
    for event in reversed(events):
        if parts and not event.is_agent():
            break
        parts += event.parts()
    parts.reverse()
    return parts


def _v2_ref_last_user_input(events: list[Event]) -> list[Part]:
    """Reference implementation of CES v2 get_last_user_input."""
    user_events: list[Event] = []
    for event in reversed(events):
        if event.is_user():
            user_events.append(event)
        elif user_events:
            break
    output: list[Part] = []
    for event in reversed(user_events):
        output.extend(event.parts())
    return output


def _v2_ref_last_agent_output(events: list[Event]) -> list[Part]:
    """Reference implementation of CES v2 get_last_agent_output."""
    agent_events: list[Event] = []
    for event in reversed(events):
        if event.is_agent():
            agent_events.append(event)
        elif agent_events:
            break
    output: list[Part] = []
    for event in reversed(agent_events):
        output.extend(event.parts())
    return output


def _format_parts(parts: list[Part]) -> list[str]:
    """Formats Part list into compact strings for assertion readability."""
    out: list[str] = []
    for p in parts:
        if getattr(p, "text", None):
            out.append(str(p.text))
        elif getattr(p, "function_call", None):
            out.append("FC:" + p.function_call.name)
        elif getattr(p, "function_response", None):
            out.append("FR:" + p.function_response.name)
    return out


@pytest.mark.parametrize(
    (
        "scenario_name",
        "events",
        "exp_v1_user",
        "exp_v1_agent",
        "exp_v2_user",
        "exp_v2_agent",
    ),
    [
        (
            "t1_full_turn_plus_aa_none",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag"),
                _make_probe_event("ag", fc="lookup"),
                _make_probe_event("ag", fr="lookup", role="user"),
                _make_probe_event("ag", "answer1"),
                _make_probe_event("ag"),
            ],
            ["answer1"],
            ["FC:lookup", "FR:lookup", "answer1"],
            ["t1"],
            ["FC:lookup", "FR:lookup", "answer1"],
        ),
        (
            "bm_turn2_after_ba_delta",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag"),
                _make_probe_event("ag", "answer1"),
                _make_probe_event("user", "t2"),
                _make_probe_event("ag"),
            ],
            ["t2"],
            ["answer1", "t2"],
            ["t2"],
            [],
        ),
        (
            "bm_turn2_no_ba_delta",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag", "answer1"),
                _make_probe_event("user", "t2"),
            ],
            ["t2"],
            ["answer1", "t2"],
            ["t2"],
            ["answer1"],
        ),
        (
            "trailing_user_none",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag", "answer1"),
                _make_probe_event("user"),
            ],
            ["t1", "answer1"],
            ["answer1"],
            [],
            ["answer1"],
        ),
        (
            "after_agent_delta_followed_by_turn2_no_ba_delta",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag", "answer1"),
                _make_probe_event("ag"),
                _make_probe_event("user", "t2"),
            ],
            ["t2"],
            ["answer1", "t2"],
            ["t2"],
            ["answer1"],
        ),
        (
            "turn1_after_agent_stage_probe",
            [
                _make_probe_event("user", "t1"),
                _make_probe_event("ag"),
                _make_probe_event("ag", "answer1"),
            ],
            ["answer1"],
            ["answer1"],
            ["t1"],
            ["answer1"],
        ),
    ],
)
def test_get_last_user_and_agent_output_v1_and_v2_parity(
    scenario_name: str,
    events: list[Event],
    exp_v1_user: list[str],
    exp_v1_agent: list[str],
    exp_v2_user: list[str],
    exp_v2_agent: list[str],
) -> None:
    """Verifies get_last_user_input / get_last_agent_output match v1 and v2 refs."""
    del scenario_name
    # Default CallbackContext uses v1 ("v1" is default both online and locally)
    ctx_v1 = CallbackContext(events=events)
    assert ctx_v1._image_version == "v1"
    v1_user = _format_parts(ctx_v1.get_last_user_input())
    v1_agent = _format_parts(ctx_v1.get_last_agent_output())
    ref_v1_user = _format_parts(_v1_ref_last_user_input(events))
    ref_v1_agent = _format_parts(_v1_ref_last_agent_output(events))
    assert v1_user == ref_v1_user == exp_v1_user
    assert v1_agent == ref_v1_agent == exp_v1_agent

    # Explicit v2 CallbackContext matches the v2 sandbox CallbackContext semantics
    ctx_v2 = CallbackContext(events=events, image_version="v2")
    assert ctx_v2._image_version == "v2"
    v2_user = _format_parts(ctx_v2.get_last_user_input())
    v2_agent = _format_parts(ctx_v2.get_last_agent_output())
    ref_v2_user = _format_parts(_v2_ref_last_user_input(events))
    ref_v2_agent = _format_parts(_v2_ref_last_agent_output(events))
    assert v2_user == ref_v2_user == exp_v2_user
    assert v2_agent == ref_v2_agent == exp_v2_agent


def test_session_simulator_before_model_get_last_agent_output() -> None:
    """Verifies SessionSimulator Turn 2 before_model under v1 (default) and v2."""
    # 1. Under image_version="v2", Turn 2 before_model sees [] when before_agent mutates
    seen_with_ba_v2: list[list[str]] = []
    runner_with_ba_v2 = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['n'] = ctx.state.get('n', 0) + 1\n",
                )
            ],
            "before_model": [
                (
                    "bm",
                    "def before_model_callback(ctx, req):\n"
                    "    ctx.state['seen_agent'] = [\n"
                    "        p.text for p in ctx.get_last_agent_output() if p.text\n"
                    "    ]\n",
                )
            ],
        },
        agent_config={"displayName": "ag", "instruction": "Count {n}"},
        image_version="v2",
        new_session=False,
    )
    sim_with_ba_v2 = SessionSimulator(
        runner_with_ba_v2, hydrate_from_agent=True
    )
    sim_with_ba_v2.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    seen_with_ba_v2.append(list(sim_with_ba_v2.ctx.state["seen_agent"]))
    sim_with_ba_v2.turn(
        "t2", mock_llm=LlmResponse.from_parts([Part.from_text("answer2")])
    )
    seen_with_ba_v2.append(list(sim_with_ba_v2.ctx.state["seen_agent"]))
    assert seen_with_ba_v2 == [[], []]

    # 2. Under default image_version="v1", Turn 2 before_model sees ["answer1", "t2"]
    seen_with_ba_v1: list[list[str]] = []
    runner_with_ba_v1 = CallbackChainRunner.from_callbacks(
        {
            "before_agent": [
                (
                    "ba",
                    "def before_agent_callback(ctx):\n"
                    "    ctx.state['n'] = ctx.state.get('n', 0) + 1\n",
                )
            ],
            "before_model": [
                (
                    "bm",
                    "def before_model_callback(ctx, req):\n"
                    "    ctx.state['seen_agent'] = [\n"
                    "        p.text for p in ctx.get_last_agent_output() if p.text\n"
                    "    ]\n",
                )
            ],
        },
        agent_config={"displayName": "ag", "instruction": "Count {n}"},
        new_session=False,
    )
    sim_with_ba_v1 = SessionSimulator(
        runner_with_ba_v1, hydrate_from_agent=True
    )
    sim_with_ba_v1.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    seen_with_ba_v1.append(list(sim_with_ba_v1.ctx.state["seen_agent"]))
    sim_with_ba_v1.turn(
        "t2", mock_llm=LlmResponse.from_parts([Part.from_text("answer2")])
    )
    seen_with_ba_v1.append(list(sim_with_ba_v1.ctx.state["seen_agent"]))
    assert seen_with_ba_v1 == [["t1"], ["answer1", "t2"]]

    # 3. Under image_version="v2" without before_agent delta, Turn 2 sees ["answer1"]
    runner_no_ba = CallbackChainRunner.from_callbacks(
        {
            "before_model": [
                (
                    "bm",
                    "def before_model_callback(ctx, req):\n"
                    "    ctx.state['seen_agent'] = [\n"
                    "        p.text for p in ctx.get_last_agent_output() if p.text\n"
                    "    ]\n",
                )
            ],
        },
        agent_config={"displayName": "ag", "instruction": "Static"},
        image_version="v2",
        new_session=False,
    )
    sim_no_ba = SessionSimulator(runner_no_ba, hydrate_from_agent=True)
    sim_no_ba.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    assert sim_no_ba.ctx.state["seen_agent"] == []
    sim_no_ba.turn(
        "t2", mock_llm=LlmResponse.from_parts([Part.from_text("answer2")])
    )
    assert sim_no_ba.ctx.state["seen_agent"] == ["answer1"]


def test_session_simulator_user_ctx_image_version_consistency() -> None:
    """Verifies SessionSimulator syncs sim.ctx._image_version when ctx is passed in."""
    ba_code = (
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['ba'] = (\n"
        "        callback_context.state.get('ba', 0) + 1\n"
        "    )\n"
    )
    bm_code = (
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['n_bm'] = len(\n"
        "        callback_context.get_last_agent_output()\n"
        "    )\n"
    )

    def _hist() -> list[Event]:
        return [
            Event(
                author="user",
                content=Content(role="user", parts=[Part.from_text("t0")]),
            ),
            Event(
                author="root_agent",
                content=Content(
                    role="model", parts=[Part.from_text("answer0")]
                ),
            ),
        ]

    # Row 3: runner v2, user ctx default (v1) -> synced to v2 without warning
    r_v2 = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba_code)], "before_model": [("bm", bm_code)]},
        image_version="v2",
        new_session=False,
    )
    ctx_default = CallbackContext(state={}, events=_hist())
    sim_row3 = SessionSimulator(r_v2, ctx=ctx_default)
    assert not sim_row3.warnings
    sim_row3.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    assert sim_row3.state["n_bm"] == 0.0
    assert sim_row3.ctx._image_version == "v2"
    assert [p.text for p in sim_row3.ctx.get_last_user_input()] == ["t1"]

    # Row 4: runner v1, user ctx explicit v2 -> synced to v1 with warning
    r_v1 = CallbackChainRunner.from_callbacks(
        {"before_agent": [("ba", ba_code)], "before_model": [("bm", bm_code)]},
        image_version="v1",
        new_session=False,
    )
    ctx_v2 = CallbackContext(state={}, events=_hist(), image_version="v2")
    sim_row4 = SessionSimulator(r_v1, ctx=ctx_v2)
    assert any("overriding ctx._image_version" in w for w in sim_row4.warnings)
    sim_row4.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    assert sim_row4.state["n_bm"] == 2.0
    assert sim_row4.ctx._image_version == "v1"
    assert [p.text for p in sim_row4.ctx.get_last_user_input()] == ["answer1"]

    # Row 5: runner v1, sim image_version="v2", user ctx default (v1) -> synced to v2
    ctx_row5 = CallbackContext(state={}, events=_hist())
    sim_row5 = SessionSimulator(r_v1, ctx=ctx_row5, image_version="v2")
    assert not sim_row5.warnings
    sim_row5.turn(
        "t1", mock_llm=LlmResponse.from_parts([Part.from_text("answer1")])
    )
    assert sim_row5.state["n_bm"] == 0.0
    assert sim_row5.ctx._image_version == "v2"
    assert [p.text for p in sim_row5.ctx.get_last_user_input()] == ["t1"]


def test_local_python_tool_ambiguous_fallback_raises_value_error(
    tmp_path: Path,
) -> None:
    """Verifies load_local_python_tools raises ValueError listing candidates when fn_name is missing and multiple public functions exist."""
    app_dir = tmp_path / "ambig_app"
    ag_dir = app_dir / "agents" / "root_agent"
    tool_dir = app_dir / "tools" / "my_tool" / "python_function"
    ag_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "ambig_app", "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    (ag_dir / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent", "tools": ["my_tool"]}),
        encoding="utf-8",
    )
    (app_dir / "tools" / "my_tool" / "my_tool.json").write_text(
        json.dumps(
            {
                "displayName": "my_tool",
                "pythonFunction": {
                    "name": "nonexistent_entry",
                    "pythonCode": "tools/my_tool/python_function/python_code.py",
                },
            }
        ),
        encoding="utf-8",
    )
    (tool_dir / "python_code.py").write_text(
        "def helper_alpha(x: int) -> dict:\n"
        "    return {'a': x}\n\n"
        "def main_beta(x: int) -> dict:\n"
        "    return {'b': x}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"multiple public candidate functions exist.*helper_alpha.*main_beta",
    ):
        load_local_python_tools(app_dir=app_dir, agent_dir=ag_dir)
