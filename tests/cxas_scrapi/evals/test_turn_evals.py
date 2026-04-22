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
    def __init__(self, dict_data):
        self._dict_data = dict_data

    @property
    def _pb(self):
        # We'll just define an object that MessageToDict can process, or
        # mock MessageToDict directly. For testing, it's easier to mock
        # MessageToDict
        pass


@pytest.fixture
def mock_turn_evals():
    with (
        patch("cxas_scrapi.evals.turn_evals.Sessions"),
        patch("cxas_scrapi.evals.turn_evals.Variables"),
    ):
        evals = TurnEvals(app_name="projects/p/locations/l/apps/a")
        return evals


def test_load_turn_test_cases_from_yaml(mock_turn_evals):
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
    assert len(cases[0].expectations) == 2  # noqa: PLR2004
    assert cases[0].expectations[0].type == TurnOperator.CONTAINS
    assert cases[0].expectations[0].value == "Hi there"
    assert cases[0].expectations[1].type == TurnOperator.TOOL_CALLED
    assert cases[0].expectations[1].value == "search"


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_validate_turn_test_success(mock_message_to_dict, mock_turn_evals):
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
        ],
    )

    results = mock_turn_evals.validate_turn_test(test_case, MagicMock())
    assert len(results) == 5  # noqa: PLR2004
    assert all(r["status"] == "SUCCESS" for r in results)


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_validate_turn_test_failures(mock_message_to_dict, mock_turn_evals):
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
        ],
    )

    results = mock_turn_evals.validate_turn_test(test_case, MagicMock())
    assert len(results) == 5  # noqa: PLR2004
    assert all(r["status"] == "FAILURE" for r in results)
    assert any("CONTAINS failed" in r["justification"] for r in results)
    assert any("EQUALS failed" in r["justification"] for r in results)
    assert any("TOOL_CALLED failed" in r["justification"] for r in results)
    assert any("TOOL_INPUT failed" in r["justification"] for r in results)
    assert any("NO_TOOLS_CALLED failed" in r["justification"] for r in results)


@patch("cxas_scrapi.evals.turn_evals.MessageToDict")
def test_extract_signals(mock_message_to_dict, mock_turn_evals):
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
def test_run_turn_tests(mock_message_to_dict, mock_turn_evals):
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
    mock_message_to_dict, mock_evaluate_expectations, mock_turn_evals
):
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
    assert len(df) == 2  # noqa: PLR2004

    # Check the conversation row
    conv_row = df[df["turn"] == "CONVERSATION"]
    assert len(conv_row) == 1
    assert conv_row.iloc[0]["status"] == "SUCCESS"
    assert "Overall good" in conv_row.iloc[0]["llm_results"]


def test_run_turn_tests_multi_turn_passes_historical_contexts(mock_turn_evals):
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

    assert mock_turn_evals.sessions_client.run.call_count == 2  # noqa: PLR2004

    # Check first call
    args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[0]
    assert kwargs["historical_contexts"] == "some_context_id"

    # Check second call
    args, kwargs = mock_turn_evals.sessions_client.run.call_args_list[1]
    assert kwargs["historical_contexts"] is None


def test_historical_context_config_mutually_exclusive():
    # Valid cases
    HistoricalContextConfig(session_id="sid")
    HistoricalContextConfig(test_name="tname")
    HistoricalContextConfig(utterances=[{"user": "hi"}])

    # Invalid cases
    with pytest.raises(ValueError):
        HistoricalContextConfig(session_id="sid", test_name="tname")
    with pytest.raises(ValueError):
        HistoricalContextConfig(session_id="sid", utterances=[{"user": "hi"}])
    with pytest.raises(ValueError):
        HistoricalContextConfig(test_name="tname", utterances=[{"user": "hi"}])
    with pytest.raises(ValueError):
        HistoricalContextConfig()  # None provided


def test_run_turn_tests_resolves_test_name_dependency(mock_turn_evals):
    mock_turn_evals.sessions_client.run.return_value = MagicMock()

    # Mock dependency manager to return resolved ID only for "parent_test"
    def resolve_session_id(name):
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
    args, kwargs = mock_turn_evals.sessions_client.run.call_args
    assert kwargs["historical_contexts"] == "resolved_session_123"


def test_run_turn_tests_passes_utterances_directly(mock_turn_evals):
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
    args, kwargs = mock_turn_evals.sessions_client.run.call_args
    assert kwargs["historical_contexts"] == utterances


def test_topological_sort(mock_turn_evals):
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


def test_topological_sort_circular_dependency(mock_turn_evals):
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
