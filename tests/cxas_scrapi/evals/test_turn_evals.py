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


"""Unit tests for the TurnEvals testing utility."""

import typing
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cxas_scrapi.evals.turn_evals import (
    HistoricalContextConfig,
    TurnEvals,
    TurnExpectation,
    TurnOperator,
    TurnStep,
    TurnTestCase,
)
from cxas_scrapi.utils.eval_utils import (
    ExpectationResult,
    ExpectationStatus,
)


class MockTurnResponse:
    def __init__(self, dict_data: typing.Any) -> None:
        self._dict_data = dict_data

    @property
    def _pb(self) -> None:
        # We'll just define an object that MessageToDict can process, or
        # mock MessageToDict directly. For testing, it's easier to mock
        # MessageToDict
        pass


@pytest.fixture
def mock_turn_evals() -> typing.Any:
    with (
        patch("cxas_scrapi.evals.turn_evals.Sessions"),
        patch("cxas_scrapi.evals.turn_evals.Variables"),
    ):
        evals = TurnEvals(app_name="projects/p/locations/l/apps/a")
        return evals


def test_load_turn_test_cases_from_yaml(mock_turn_evals: typing.Any) -> None:
    yaml_str = """
tests:
  - name: test_greeting
    user: Hello
    variables:
      first_turn: true
    expectations:
      - type: contains
        value: Hi there
      - type: tool_called
        value: search
"""
    cases = mock_turn_evals.load_turn_test_cases_from_yaml(yaml_str)
    assert len(cases) == 1
    assert cases[0].name == "test_greeting"
    assert cases[0].user == "Hello"
    assert cases[0].variables["first_turn"] is True
    assert len(cases[0].expectations) == 2
    assert cases[0].expectations[0].type == TurnOperator.CONTAINS
    assert cases[0].expectations[0].value == "Hi there"
    assert cases[0].expectations[1].type == TurnOperator.TOOL_CALLED
    assert cases[0].expectations[1].value == "search"


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_validate_turn_test_success(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {
        "outputs": [
            {
                "text": "Hi there! I am your agent.",
                "diagnosticInfo": {
                    "messages": [
                        {
                            "chunks": [
                                {"text": "Hi there! I am your agent."},
                                {
                                    "toolCall": {
                                        "displayName": "product_search",
                                        "args": {"query": "shoes"},
                                    }
                                },
                                {
                                    "toolResponse": {
                                        "displayName": "product_search",
                                        "response": {"status": "SUCCESS"},
                                    }
                                },
                            ]
                        }
                    ]
                },
            }
        ]
    }

    test_case = TurnTestCase(
        name="test_1",
        user="hello",
        expectations=[
            TurnExpectation(type=TurnOperator.CONTAINS, value="your agent"),
            TurnExpectation(
                type=TurnOperator.EQUALS, value="Hi there! I am your agent. "
            ),  # Expects trailing space due to chunk mapping loop
            TurnExpectation(
                type=TurnOperator.TOOL_CALLED, value="product_search"
            ),
            TurnExpectation(
                type=TurnOperator.TOOL_INPUT, value={"query": "shoes"}
            ),
            TurnExpectation(
                type=TurnOperator.TOOL_OUTPUT, value={"status": "SUCCESS"}
            ),
            TurnExpectation(
                type=TurnOperator.NOT_CONTAINS, value="wrong string"
            ),
            TurnExpectation(
                type=TurnOperator.TOOL_NOT_CALLED, value="wrong_tool"
            ),
        ],
    )

    results = mock_turn_evals.validate_turn_test(test_case, MagicMock())
    assert len(results) == 7
    assert all(r["status"] == "SUCCESS" for r in results)


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_validate_turn_test_failures(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {
        "outputs": [
            {
                "diagnosticInfo": {
                    "messages": [
                        {
                            "chunks": [
                                {"text": "Nope."},
                                {
                                    "toolCall": {
                                        "displayName": "other_tool",
                                        "args": {"query": "hats"},
                                    }
                                },
                            ]
                        }
                    ]
                }
            }
        ]
    }

    test_case = TurnTestCase(
        name="test_2",
        user="hello",
        expectations=[
            TurnExpectation(type=TurnOperator.CONTAINS, value="your agent"),
            TurnExpectation(
                type=TurnOperator.EQUALS, value="Hi there! I am your agent. "
            ),
            TurnExpectation(
                type=TurnOperator.TOOL_CALLED, value="product_search"
            ),
            TurnExpectation(
                type=TurnOperator.TOOL_INPUT, value={"query": "shoes"}
            ),
            TurnExpectation(type=TurnOperator.NO_TOOLS_CALLED, value=None),
            TurnExpectation(type=TurnOperator.NOT_CONTAINS, value="Nope."),
            TurnExpectation(
                type=TurnOperator.TOOL_NOT_CALLED, value="other_tool"
            ),
        ],
    )

    results = mock_turn_evals.validate_turn_test(test_case, MagicMock())
    assert len(results) == 7
    assert all(r["status"] == "FAILURE" for r in results)
    assert any("CONTAINS failed" in r["justification"] for r in results)
    assert any("EQUALS failed" in r["justification"] for r in results)
    assert any("TOOL_CALLED failed" in r["justification"] for r in results)
    assert any("TOOL_INPUT failed" in r["justification"] for r in results)
    assert any("NO_TOOLS_CALLED failed" in r["justification"] for r in results)
    assert any("NOT_CONTAINS failed" in r["justification"] for r in results)
    assert any("TOOL_NOT_CALLED failed" in r["justification"] for r in results)


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_extract_signals(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {
        "text": "Hello there!",
        "toolCalls": {
            "toolCalls": [
                {"displayName": "my_tool", "args": {"param": "value"}}
            ]
        },
    }

    signals = mock_turn_evals._extract_signals(MagicMock())

    assert signals["full_text"] == "Hello there!"
    assert signals["called_tools"] == ["my_tool"]
    assert signals["tool_inputs"] == {"my_tool": {"param": "value"}}


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_run_turn_tests(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {"text": "Hello!"}

    # Mock the session run to return a dummy response
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="t1",
            user="hi",
            expectations=[
                TurnExpectation(type=TurnOperator.CONTAINS, value="Hello")
            ],
        )
    ]

    df = mock_turn_evals.run_turn_tests(cases)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "SUCCESS"
    assert df.iloc[0]["errors"] == ""
    assert mock_turn_evals.sessions_client.run.call_count == 1


@patch("cxas_scrapi.evals.turn_evals.evaluate_expectations")
@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_run_turn_tests_conversation_expectations(
    mock_message_to_dict: typing.Any,
    mock_evaluate_expectations: typing.Any,
    mock_turn_evals: typing.Any,
) -> None:
    mock_message_to_dict.return_value = {"text": "Hello!"}

    mock_evaluate_expectations.return_value = [
        ExpectationResult(
            expectation="Overall good",
            status=ExpectationStatus.MET,
            justification="Yes",
        )
    ]

    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="t1",
            turns=[TurnStep(turn="1", user="hi", expectations=[])],
            expectations=["Overall good"],
        )
    ]

    df = mock_turn_evals.run_turn_tests(cases)
    assert isinstance(df, pd.DataFrame)
    # 1 row for the turn, 1 row for the conversation expectation
    assert len(df) == 2

    # Check the conversation row
    conv_row = df[df["turn"] == "CONVERSATION"]
    assert len(conv_row) == 1
    assert conv_row.iloc[0]["status"] == "SUCCESS"
    assert "Overall good" in conv_row.iloc[0]["llm_results"]


def test_run_turn_tests_multi_turn_passes_historical_contexts(
    mock_turn_evals: typing.Any,
) -> None:
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="t1",
            historical_contexts=HistoricalContextConfig(
                session_id="some_context_id"
            ),
            turns=[
                TurnStep(turn="1", user="hi", expectations=[]),
                TurnStep(turn="2", user="how are you", expectations=[]),
            ],
        )
    ]

    mock_turn_evals.run_turn_tests(cases)

    assert mock_turn_evals.sessions_client.run.call_count == 2

    # Check first call
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[0]
    assert kwargs["historical_contexts"] == "some_context_id"

    # Check second call
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[1]
    assert kwargs["historical_contexts"] is None


def test_historical_context_config_mutually_exclusive() -> None:
    # Valid cases
    HistoricalContextConfig(session_id="sid")
    HistoricalContextConfig(test_name="tname")
    HistoricalContextConfig(utterances=[{"user": "hi"}])

    # Invalid cases
    with pytest.raises(ValueError):  # noqa: PT011
        HistoricalContextConfig(session_id="sid", test_name="tname")
    with pytest.raises(ValueError):  # noqa: PT011
        HistoricalContextConfig(session_id="sid", utterances=[{"user": "hi"}])
    with pytest.raises(ValueError):  # noqa: PT011
        HistoricalContextConfig(test_name="tname", utterances=[{"user": "hi"}])
    with pytest.raises(ValueError):  # noqa: PT011
        HistoricalContextConfig()  # None provided


def test_run_turn_tests_resolves_test_name_dependency(
    mock_turn_evals: typing.Any,
) -> None:
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    # Mock dependency manager to return resolved ID only for "parent_test"
    def resolve_session_id(name: typing.Any) -> str | None:
        if name == "parent_test":
            return "resolved_session_123"
        return None

    mock_turn_evals.dependency_manager.resolve_session_id = MagicMock(
        side_effect=resolve_session_id
    )

    cases = [
        TurnTestCase(
            name="t2",
            historical_contexts=HistoricalContextConfig(
                test_name="parent_test"
            ),
            turns=[TurnStep(turn="1", user="hi", expectations=[])],
        )
    ]

    mock_turn_evals.run_turn_tests(cases)

    assert mock_turn_evals.sessions_client.run.call_count == 1
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args
    assert kwargs["historical_contexts"] == "resolved_session_123"


def test_run_turn_tests_passes_utterances_directly(
    mock_turn_evals: typing.Any,
) -> None:
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    utterances = [{"user": "Hello"}, {"agent": "Hi!"}]
    cases = [
        TurnTestCase(
            name="t3",
            historical_contexts=HistoricalContextConfig(utterances=utterances),
            turns=[TurnStep(turn="1", user="hi", expectations=[])],
        )
    ]

    mock_turn_evals.run_turn_tests(cases)

    assert mock_turn_evals.sessions_client.run.call_count == 1
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args
    assert kwargs["historical_contexts"] == utterances


def test_topological_sort(mock_turn_evals: typing.Any) -> None:
    # Setup cases
    c1 = TurnTestCase(
        name="child1",
        historical_contexts=HistoricalContextConfig(test_name="parent"),
        turns=[],
    )
    c2 = TurnTestCase(name="parent", turns=[])
    c3 = TurnTestCase(
        name="grandchild",
        historical_contexts=HistoricalContextConfig(test_name="child1"),
        turns=[],
    )

    cases = [c1, c2, c3]

    sorted_cases = mock_turn_evals._topological_sort(cases)

    # Expected order: parent, child1, grandchild
    assert [c.name for c in sorted_cases] == ["parent", "child1", "grandchild"]


def test_topological_sort_circular_dependency(
    mock_turn_evals: typing.Any,
) -> None:
    # Setup cases
    c1 = TurnTestCase(
        name="a",
        historical_contexts=HistoricalContextConfig(test_name="b"),
        turns=[],
    )
    c2 = TurnTestCase(
        name="b",
        historical_contexts=HistoricalContextConfig(test_name="a"),
        turns=[],
    )

    cases = [c1, c2]

    with pytest.raises(ValueError, match="Circular dependency detected"):
        mock_turn_evals._topological_sort(cases)


def test_run_turn_tests_with_event(mock_turn_evals: typing.Any) -> None:
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="t_event",
            user="<event>welcome</event>",
            expectations=[],
        )
    ]

    mock_turn_evals.run_turn_tests(cases)

    assert mock_turn_evals.sessions_client.run.call_count == 1
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args
    assert kwargs["text"] is None
    assert kwargs["event"] == "welcome"


def test_run_turn_tests_multi_turn_with_event(
    mock_turn_evals: typing.Any,
) -> None:
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="t_multi_event",
            turns=[
                TurnStep(
                    turn="1",
                    user="<event>welcome</event>",
                    expectations=[],
                ),
                TurnStep(turn="2", user="regular text", expectations=[]),
            ],
        )
    ]

    mock_turn_evals.run_turn_tests(cases)

    assert mock_turn_evals.sessions_client.run.call_count == 2

    # Check first call (event)
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[0]
    assert kwargs["text"] is None
    assert kwargs["event"] == "welcome"

    # Check second call (text)
    _args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[1]
    assert kwargs["text"] == "regular text"
    assert kwargs["event"] is None


@patch("cxas_scrapi.evals.turn_evals.Variables")
@patch("cxas_scrapi.evals.turn_evals.Sessions")
def test_turn_evals_init_with_rate_limiter(
    mock_sessions: typing.Any, mock_variables: typing.Any
) -> None:
    mock_rate_limiter = MagicMock()
    _ = TurnEvals(
        app_name="projects/p/locations/l/apps/a",
        rate_limiter=mock_rate_limiter,
    )
    mock_sessions.assert_called_once_with(
        app_name="projects/p/locations/l/apps/a",
        creds=None,
        rate_limiter=mock_rate_limiter,
    )


@patch("cxas_scrapi.evals.turn_evals.GeminiGenerate")
@patch("cxas_scrapi.evals.turn_evals.Variables")
@patch("cxas_scrapi.evals.turn_evals.Sessions")
def test_turn_evals_default_vertex_location(
    mock_sessions: typing.Any,
    mock_variables: typing.Any,
    mock_gemini: typing.Any,
) -> None:
    evals = TurnEvals(app_name="projects/p/locations/l/apps/a")
    assert evals.vertex_location == "global"
    mock_gemini.assert_called_once_with(
        project_id="p",
        location="global",
        credentials=None,
    )


@patch("cxas_scrapi.evals.turn_evals.GeminiGenerate")
@patch("cxas_scrapi.evals.turn_evals.Variables")
@patch("cxas_scrapi.evals.turn_evals.Sessions")
def test_turn_evals_custom_vertex_location(
    mock_sessions: typing.Any,
    mock_variables: typing.Any,
    mock_gemini: typing.Any,
) -> None:
    evals = TurnEvals(
        app_name="projects/p/locations/l/apps/a",
        vertex_location="us-central1",
    )
    assert evals.vertex_location == "us-central1"
    mock_gemini.assert_called_once_with(
        project_id="p",
        location="us-central1",
        credentials=None,
    )


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_run_turn_tests_with_background_noise_config(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {"text": "Hello!"}
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="test_call_center_noise",
            user="hi",
            expectations=[
                TurnExpectation(type=TurnOperator.CONTAINS, value="Hello")
            ],
            config={
                "background_noise": {
                    "preset": "call_center",
                    "snr": 12.0,
                }
            },
        )
    ]

    df = mock_turn_evals.run_turn_tests(cases)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "SUCCESS"

    mock_turn_evals.sessions_client.run.assert_called_once()
    call_kwargs = mock_turn_evals.sessions_client.run.call_args.kwargs
    assert call_kwargs["background_noise_file"] == "call_center"
    assert call_kwargs["bg_noise_snr"] == 12.0


def test_run_turn_tests_interactive_with_background_noise_config(
    mock_turn_evals: typing.Any,
) -> None:
    mock_interactive = MagicMock()
    mock_turn_evals.sessions_client.create_interactive_session.return_value = (
        mock_interactive
    )
    mock_interactive.send_turn.return_value = {
        "text": "Hello!",
        "outputs": [{"text": "Hello!"}],
    }

    cases = [
        TurnTestCase(
            name="test_bidi_noise",
            turns=[
                TurnStep(
                    turn="1",
                    user="hi",
                    expectations=[
                        TurnExpectation(
                            type=TurnOperator.CONTAINS, value="Hello"
                        )
                    ],
                )
            ],
            config={
                "single_bidi_stream": True,
                "background_noise": {
                    "preset": "traffic",
                    "snr": 8.0,
                },
            },
        )
    ]

    with patch("cxas_scrapi.evals.turn_evals.MessageToDict") as mock_m2d:
        mock_m2d.return_value = {"text": "Hello!"}
        df = mock_turn_evals.run_turn_tests(cases)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "SUCCESS"

    mock_turn_evals.sessions_client.create_interactive_session.assert_called_once()
    create_kwargs = mock_turn_evals.sessions_client.create_interactive_session.call_args.kwargs
    assert create_kwargs["background_noise_file"] == "traffic"
    assert create_kwargs["bg_noise_snr"] == 8.0


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_run_turn_tests_with_background_noise_level(
    mock_message_to_dict: typing.Any, mock_turn_evals: typing.Any
) -> None:
    mock_message_to_dict.return_value = {"text": "Hello!"}
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    cases = [
        TurnTestCase(
            name="test_level_noise",
            user="hi",
            expectations=[
                TurnExpectation(type=TurnOperator.CONTAINS, value="Hello")
            ],
            config={
                "background_noise": "cafe:LOW",
            },
        )
    ]

    df = mock_turn_evals.run_turn_tests(cases)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1

    mock_turn_evals.sessions_client.run.assert_called_once()
    call_kwargs = mock_turn_evals.sessions_client.run.call_args.kwargs
    assert call_kwargs["background_noise_file"] == "cafe"
    assert call_kwargs["bg_noise_snr"] == 20.0


def test_load_turn_test_cases_from_yaml_with_common_noise_and_params(
    mock_turn_evals: typing.Any,
) -> None:
    yaml_text = """
common_background_noise: "call_center"
common_bg_noise_level: "MEDIUM"
common_params:
  burst_noise: ["dog_barking"]
  burst_noise_level: "HIGH"
  custom_flag: true
tests:
  - name: test_inherits_all
    user: "Hello"
    expectations:
      - type: contains
        value: "Hi"
  - name: test_overrides_noise
    config:
      background_noise: "cafe:LOW"
    user: "Help"
    expectations:
      - type: contains
        value: "Sure"
  - name: test_opts_out
    config:
      background_noise: false
    user: "Cancel"
    expectations:
      - type: contains
        value: "Done"
"""
    test_cases = mock_turn_evals.load_turn_test_cases_from_yaml(yaml_text)
    assert len(test_cases) == 3

    tc0 = test_cases[0]
    assert tc0.config["background_noise"] == "call_center"
    assert tc0.config["bg_noise_level"] == "MEDIUM"
    assert tc0.config["burst_noise"] == ["dog_barking"]
    assert tc0.config["burst_noise_level"] == "HIGH"
    assert tc0.config["custom_flag"] is True

    tc1 = test_cases[1]
    assert tc1.config["background_noise"] == "cafe:LOW"
    assert tc1.config["bg_noise_level"] == "MEDIUM"

    tc2 = test_cases[2]
    assert tc2.config["background_noise"] is False
