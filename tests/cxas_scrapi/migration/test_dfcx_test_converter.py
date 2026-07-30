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


"""Tests for DFCX test case converter."""

import typing
from unittest.mock import MagicMock

import yaml

from cxas_scrapi.evals.turn_evals import (
    TurnExpectation,
    TurnOperator,
    TurnStep,
    TurnTestCase,
)
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    IRAgent,
    IRMetadata,
    MigrationIR,
)
from cxas_scrapi.migration.dfcx_test_converter import DFCXTestConverter


def _make_ir(*agent_names: str) -> MigrationIR:
    return MigrationIR(
        metadata=IRMetadata(app_name="test"),
        agents={
            name: IRAgent(type="FLOW", display_name=name, instruction="")
            for name in agent_names
        },
    )


def _make_source(*test_cases: dict) -> DFCXAgentIR:
    return DFCXAgentIR(
        name="test",
        display_name="test",
        default_language_code="en",
        test_cases=list(test_cases),
    )


def _text_turn(
    text: typing.Any,
    responses: typing.Any = None,
    flow: typing.Any = None,
    prev_flow: typing.Any = None,
    params: typing.Any = None,
) -> typing.Any:
    turn = {
        "userInput": {"input": {"text": {"text": text}}},
        "virtualAgentOutput": {},
    }
    if responses:
        turn["virtualAgentOutput"]["textResponses"] = [{"text": responses}]
    if flow:
        turn["virtualAgentOutput"]["currentFlow"] = {"name": flow}
    if params:
        turn["userInput"]["injectedParameters"] = params
    return turn


def _event_turn(
    event: typing.Any, responses: typing.Any = None, flow: typing.Any = None
) -> typing.Any:
    turn = {
        "userInput": {"input": {"event": {"event": event}}},
        "virtualAgentOutput": {},
    }
    if responses:
        turn["virtualAgentOutput"]["textResponses"] = [{"text": responses}]
    if flow:
        turn["virtualAgentOutput"]["currentFlow"] = {"name": flow}
    return turn


def _dtmf_turn(
    digits: typing.Any, responses: typing.Any = None, flow: typing.Any = None
) -> typing.Any:
    turn = {
        "userInput": {"input": {"dtmf": {"digits": digits}}},
        "virtualAgentOutput": {},
    }
    if responses:
        turn["virtualAgentOutput"]["textResponses"] = [{"text": responses}]
    if flow:
        turn["virtualAgentOutput"]["currentFlow"] = {"name": flow}
    return turn


def _empty_text_turn(flow: typing.Any = None) -> typing.Any:
    turn = {
        "userInput": {"input": {"text": {}}},
        "virtualAgentOutput": {},
    }
    if flow:
        turn["virtualAgentOutput"]["currentFlow"] = {"name": flow}
    return turn


def _mock_gemini(summary: typing.Any = "Agent greets the user") -> typing.Any:
    mock = MagicMock()
    mock.generate.return_value = f"1. {summary}"
    return mock


# --- Routing ---


def test_route_to_agent_direct_match() -> None:
    ir = _make_ir("MainMenu", "RootAgent")
    converter = DFCXTestConverter(ir)
    tc = {"testConfig": {"flow": "MainMenu"}}
    assert converter._route_test_to_agent(tc) == "MainMenu"


def test_route_to_agent_via_flow_map() -> None:
    ir = _make_ir("NavigationAgent")
    flow_map = {"Main Menu": "NavigationAgent"}
    converter = DFCXTestConverter(ir, flow_to_agent_map=flow_map)
    tc = {"testConfig": {"flow": "Main Menu"}}
    assert converter._route_test_to_agent(tc) == "NavigationAgent"


def test_route_to_agent_sanitized_fallback() -> None:
    ir = _make_ir("DefaultStartFlow")
    converter = DFCXTestConverter(ir)
    tc = {"testConfig": {"flow": "Default Start Flow"}}
    assert converter._route_test_to_agent(tc) == "DefaultStartFlow"


def test_route_to_agent_no_flow() -> None:
    ir = _make_ir("RootAgent")
    converter = DFCXTestConverter(ir)
    tc = {"testConfig": {}}
    assert converter._route_test_to_agent(tc) is None


def test_route_to_agent_unknown_flow() -> None:
    ir = _make_ir("RootAgent")
    converter = DFCXTestConverter(ir)
    tc = {"testConfig": {"flow": "NonexistentFlow"}}
    assert converter._route_test_to_agent(tc) is None


# --- Fuzzy match mode (no gemini_client) ---


def test_convert_text_produces_fuzzy_match() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "greeting test",
            "tags": ["#smoke"],
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome! How can I help you today?"],
                    flow="RootAgent",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, report = converter.convert_all(source)

    assert "RootAgent" in tests_by_agent
    assert len(tests_by_agent["RootAgent"]) == 1

    tc = tests_by_agent["RootAgent"][0]
    assert tc.name == "greeting test"
    assert "#migrated-dfcx" in tc.tags
    assert "#smoke" in tc.tags
    assert len(tc.turns) == 1
    assert tc.turns[0].user == "hello"

    exps = tc.turns[0].expectations
    assert len(exps) == 1
    assert exps[0].type == TurnOperator.FUZZY_MATCH
    assert "Welcome! How can I help you today?" in exps[0].value
    assert report["fuzzy_match_assertions"] == 1


# --- Behavioral mode (with gemini_client) ---


def test_convert_text_produces_behavioral_string() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "greeting test",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome! How can I help you today?"],
                    flow="RootAgent",
                ),
            ],
        }
    )
    gemini = _mock_gemini("Agent greets the user and offers help")
    converter = DFCXTestConverter(ir, gemini_client=gemini)
    tests_by_agent, report = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    exps = tc.turns[0].expectations
    assert len(exps) == 1
    assert isinstance(exps[0], str)
    assert exps[0] == "Agent greets the user and offers help"
    assert "#behavioral" in tc.tags
    assert report["behavioral_assertions"] == 1
    assert report["fuzzy_match_assertions"] == 0


def test_behavioral_caches_identical_responses() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "test1",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello", responses=["Same response."], flow="RootAgent"
                ),
            ],
        },
        {
            "displayName": "test2",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello", responses=["Same response."], flow="RootAgent"
                ),
            ],
        },
    )
    gemini = _mock_gemini("Agent gives same response")
    converter = DFCXTestConverter(ir, gemini_client=gemini)
    converter.convert_all(source)

    assert gemini.generate.call_count == 1


def test_behavioral_fallback_on_gemini_failure() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "test",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome to our service!"],
                    flow="RootAgent",
                ),
            ],
        }
    )
    gemini = MagicMock()
    gemini.generate.return_value = None
    converter = DFCXTestConverter(ir, gemini_client=gemini)
    tests_by_agent, _ = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    exp = tc.turns[0].expectations[0]
    assert isinstance(exp, str)
    assert "Agent responds appropriately" in exp


def test_batch_summarize_multiple_unique() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "test1",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome! How can I help?"],
                    flow="RootAgent",
                ),
            ],
        },
        {
            "displayName": "test2",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "check balance",
                    responses=["Your balance is $100."],
                    flow="RootAgent",
                ),
            ],
        },
    )
    gemini = MagicMock()
    gemini.generate.return_value = (
        "1. Agent greets the user and offers help.\n"
        "2. Agent reports the account balance."
    )
    converter = DFCXTestConverter(ir, gemini_client=gemini)
    tests_by_agent, _ = converter.convert_all(source)

    assert gemini.generate.call_count == 1
    tc1 = tests_by_agent["RootAgent"][0]
    tc2 = tests_by_agent["RootAgent"][1]
    assert tc1.turns[0].expectations[0] == (
        "Agent greets the user and offers help."
    )
    assert tc2.turns[0].expectations[0] == (
        "Agent reports the account balance."
    )


def test_parse_batch_response_fallback() -> None:
    result = DFCXTestConverter._parse_batch_response(None, 3)
    assert len(result) == 3
    assert all("Agent responds" in s for s in result)


# --- Event input ---


def test_convert_event_input() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "welcome event",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _event_turn(
                    "welcome",
                    responses=["Hi there, how can I assist?"],
                    flow="RootAgent",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, _ = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert tc.turns[0].event == "welcome"
    assert tc.turns[0].user is None


# --- DTMF as text ---


def test_convert_dtmf_as_text() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "dtmf test",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _dtmf_turn(
                    "*7",
                    responses=["You pressed star seven. Connecting you now."],
                    flow="RootAgent",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, report = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert tc.turns[0].user == "*7"
    assert "#dtmf-as-text" in tc.tags
    assert report["dtmf_as_text_count"] == 1


# --- Multi-turn with flow change / agent_transfer ---


def test_convert_multi_turn_with_agent_transfer() -> None:
    ir = _make_ir("RootAgent", "BillingAgent")
    flow_map = {"Default Start Flow": "RootAgent", "Billing": "BillingAgent"}
    source = _make_source(
        {
            "displayName": "transfer to billing",
            "testConfig": {"flow": "Default Start Flow"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome! How can I help?"],
                    flow="Default Start Flow",
                ),
                _text_turn(
                    "check my bill",
                    responses=["Let me transfer you to billing."],
                    flow="Billing",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir, flow_to_agent_map=flow_map)
    tests_by_agent, report = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert len(tc.turns) == 2

    turn2_exps = tc.turns[1].expectations
    types = [
        e.type if isinstance(e, TurnExpectation) else "str" for e in turn2_exps
    ]
    assert TurnOperator.AGENT_TRANSFER in types
    assert TurnOperator.FUZZY_MATCH in types
    transfer = next(
        e
        for e in turn2_exps
        if isinstance(e, TurnExpectation)
        and e.type == TurnOperator.AGENT_TRANSFER
    )
    assert transfer.value == "BillingAgent"
    assert report["agent_transfer_assertions"] >= 1


def test_agent_transfer_with_behavioral() -> None:
    ir = _make_ir("RootAgent", "BillingAgent")
    flow_map = {"Root": "RootAgent", "Billing": "BillingAgent"}
    source = _make_source(
        {
            "displayName": "transfer behavioral",
            "testConfig": {"flow": "Root"},
            "testCaseConversationTurns": [
                _text_turn("hello", responses=["Welcome!"], flow="Root"),
                _text_turn(
                    "billing",
                    responses=["Transferring to billing now."],
                    flow="Billing",
                ),
            ],
        }
    )
    gemini = _mock_gemini("Agent confirms transfer to billing")
    converter = DFCXTestConverter(
        ir, flow_to_agent_map=flow_map, gemini_client=gemini
    )
    tests_by_agent, report = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    turn2_exps = tc.turns[1].expectations
    has_transfer = any(
        isinstance(e, TurnExpectation) and e.type == TurnOperator.AGENT_TRANSFER
        for e in turn2_exps
    )
    has_behavioral = any(isinstance(e, str) for e in turn2_exps)
    assert has_transfer
    assert has_behavioral
    assert report["agent_transfer_assertions"] >= 1
    assert report["behavioral_assertions"] >= 1


# --- SSML stripping ---


def test_ssml_stripping() -> None:
    ssml = (
        "<speak>Thank you for calling "
        '<prosody rate="slow">support</prosody>.</speak>'
    )
    assert DFCXTestConverter._strip_ssml(ssml) == (
        "Thank you for calling support."
    )


def test_ssml_stripping_plain_text() -> None:
    assert DFCXTestConverter._strip_ssml("Hello world") == "Hello world"


# --- Empty text turn skipping ---


def test_empty_text_turns_skipped() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "with empty turn",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hi",
                    responses=["Hello! How can I help?"],
                    flow="RootAgent",
                ),
                _empty_text_turn(flow="RootAgent"),
                _text_turn(
                    "check balance",
                    responses=["Your balance is $100."],
                    flow="RootAgent",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, report = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert len(tc.turns) == 2
    assert tc.turns[0].user == "hi"
    assert tc.turns[1].user == "check balance"
    assert report["empty_text_turns_collapsed"] == 1


# --- Post-consolidation rerouting ---


def test_reroute_after_consolidation() -> None:
    test_cases = {
        "FlowA": [{"name": "test1"}],
        "FlowB": [{"name": "test2"}],
        "FlowC": [{"name": "test3"}],
    }
    grouping = {
        "AgentAlpha": {"agents": ["FlowA", "FlowB"]},
        "AgentBeta": {"agents": ["FlowC"]},
    }

    result = DFCXTestConverter.reroute_after_consolidation(test_cases, grouping)

    assert set(result.keys()) == {"AgentAlpha", "AgentBeta"}
    assert len(result["AgentAlpha"]) == 2
    assert len(result["AgentBeta"]) == 1


# --- Skip cases ---


def test_skip_test_no_assertions() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "no response test",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn("hi", responses=[], flow="RootAgent"),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, report = converter.convert_all(source)
    assert report["skipped"] == 1
    assert not tests_by_agent


def test_skip_test_short_response() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "short response",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn("hi", responses=["OK"], flow="RootAgent"),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    _, report = converter.convert_all(source)
    assert report["skipped"] == 1


# --- Serialize to YAML ---


def test_serialize_to_yaml_fuzzy_match() -> None:
    tests_by_agent = {
        "RootAgent": [
            TurnTestCase(
                name="yaml test",
                tags=["#migrated-dfcx"],
                turns=[
                    TurnStep(
                        turn="Turn 1",
                        user="hello",
                        expectations=[
                            TurnExpectation(
                                type=TurnOperator.FUZZY_MATCH, value="Welcome"
                            )
                        ],
                    )
                ],
            )
        ]
    }
    yamls = DFCXTestConverter.serialize_to_yaml(tests_by_agent)
    assert "RootAgent" in yamls
    parsed = yaml.safe_load(yamls["RootAgent"])
    assert len(parsed["tests"]) == 1
    assert parsed["tests"][0]["name"] == "yaml test"
    assert parsed["tests"][0]["turns"][0]["user"] == "hello"


def test_serialize_to_yaml_behavioral() -> None:
    tests_by_agent = {
        "RootAgent": [
            TurnTestCase(
                name="behavioral test",
                tags=["#migrated-dfcx", "#behavioral"],
                turns=[
                    TurnStep(
                        turn="Turn 1",
                        user="hello",
                        expectations=[
                            "Agent greets the user and offers help",
                            TurnExpectation(
                                type=TurnOperator.AGENT_TRANSFER,
                                value="BillingAgent",
                            ),
                        ],
                    )
                ],
            )
        ]
    }
    yamls = DFCXTestConverter.serialize_to_yaml(tests_by_agent)
    parsed = yaml.safe_load(yamls["RootAgent"])
    exps = parsed["tests"][0]["turns"][0]["expectations"]
    assert "Agent greets the user and offers help" in exps
    assert any(
        isinstance(e, dict) and e.get("type") == "agent_transfer" for e in exps
    )


# --- Deduplication ---


def test_duplicate_names_deduplicated() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "same name",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome to our service!"],
                    flow="RootAgent",
                ),
            ],
        },
        {
            "displayName": "same name",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hi",
                    responses=["Hello, how can I help you?"],
                    flow="RootAgent",
                ),
            ],
        },
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, _ = converter.convert_all(source)

    names = [tc.name for tc in tests_by_agent["RootAgent"]]
    assert len(names) == 2
    assert len(set(names)) == 2


# --- Injected parameters ---


def test_first_turn_injected_parameters() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "params test",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hello",
                    responses=["Welcome, credit card holder!"],
                    flow="RootAgent",
                    params={"account-type": "credit card"},
                ),
                _text_turn(
                    "check balance",
                    responses=["Your balance is one hundred dollars."],
                    flow="RootAgent",
                ),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, _ = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert tc.turns[0].variables == {"account-type": "credit card"}
    assert tc.turns[1].variables == {}


# --- Report ---


def test_report_counts() -> None:
    ir = _make_ir("RootAgent", "BillingAgent")
    flow_map = {"Root": "RootAgent", "Billing": "BillingAgent"}
    source = _make_source(
        {
            "displayName": "test1",
            "testConfig": {"flow": "Root"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hi",
                    responses=["Hello, welcome to support!"],
                    flow="Root",
                ),
                _text_turn(
                    "billing",
                    responses=["Transferring to billing now."],
                    flow="Billing",
                ),
            ],
        },
        {
            "displayName": "test2",
            "testConfig": {},
            "testCaseConversationTurns": [
                _text_turn("hi", responses=["hello"], flow="Root"),
            ],
        },
    )
    converter = DFCXTestConverter(ir, flow_to_agent_map=flow_map)
    _, report = converter.convert_all(source)

    assert report["total_source_tests"] == 2
    assert report["converted"] == 1
    assert report["skipped"] == 1
    assert report["fuzzy_match_assertions"] >= 1
    assert report["agent_transfer_assertions"] >= 1


def test_report_counts_behavioral() -> None:
    ir = _make_ir("RootAgent")
    source = _make_source(
        {
            "displayName": "test1",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn(
                    "hi",
                    responses=["Hello, welcome to support!"],
                    flow="RootAgent",
                ),
            ],
        }
    )
    gemini = _mock_gemini("Agent greets user")
    converter = DFCXTestConverter(ir, gemini_client=gemini)
    _, report = converter.convert_all(source)

    assert report["behavioral_assertions"] == 1
    assert report["fuzzy_match_assertions"] == 0


# --- FUZZY_MATCH truncation ---


def test_fuzzy_match_truncated_to_max_length() -> None:
    ir = _make_ir("RootAgent")
    long_response = "A" * 200
    source = _make_source(
        {
            "displayName": "long response",
            "testConfig": {"flow": "RootAgent"},
            "testCaseConversationTurns": [
                _text_turn("hi", responses=[long_response], flow="RootAgent"),
            ],
        }
    )
    converter = DFCXTestConverter(ir)
    tests_by_agent, _ = converter.convert_all(source)

    tc = tests_by_agent["RootAgent"][0]
    assert len(tc.turns[0].expectations[0].value) == 100


# --- Extract response text ---


def test_extract_response_text() -> None:
    converter = DFCXTestConverter(_make_ir("RootAgent"))
    output = {
        "textResponses": [{"text": ["Hello, how can I help?"]}],
    }
    assert converter._extract_response_text(output) == "Hello, how can I help?"


def test_extract_response_text_strips_ssml() -> None:
    converter = DFCXTestConverter(_make_ir("RootAgent"))
    output = {
        "textResponses": [{"text": ["<speak>Hello world</speak>"]}],
    }
    assert converter._extract_response_text(output) == "Hello world"


def test_extract_response_text_skips_short() -> None:
    converter = DFCXTestConverter(_make_ir("RootAgent"))
    output = {
        "textResponses": [{"text": ["OK"]}],
    }
    assert converter._extract_response_text(output) == ""


def test_extract_response_text_empty() -> None:
    converter = DFCXTestConverter(_make_ir("RootAgent"))
    assert converter._extract_response_text({}) == ""
