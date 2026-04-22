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

"""Tests for evaluation utility functions."""

import sys
from unittest.mock import MagicMock, mock_open, patch

# Mock dependencies before importing EvalUtils
sys.modules["google.cloud.texttospeech"] = MagicMock()
sys.modules["websocket"] = MagicMock()
sys.modules["google.cloud.ces"] = MagicMock()
sys.modules["google.cloud.secretmanager"] = MagicMock()
sys.modules["google.cloud.bigquery"] = MagicMock()
sys.modules["pandas_gbq"] = MagicMock()

from cxas_scrapi.utils.eval_utils import EvalUtils, Turn  # noqa: E402


def test_evals_to_dataframe_empty():
    """Test evals_to_dataframe with empty list."""
    utils = EvalUtils(app_name="p/l/a/a")
    df = utils.evals_to_dataframe([])
    assert df is not None


def test_evals_to_dataframe_with_data():
    """Test evals_to_dataframe with valid metrics."""
    utils = EvalUtils(app_name="p/l/a/a")

    class MockEvalResult:
        @classmethod
        def to_dict(cls, obj):
            return obj.res_dict

    res = MockEvalResult()
    res.res_dict = {
        "name": "eval/123",
        "evaluation_status": "PASS",
        "evaluation_run": "eval_run/123",
        "golden_result": {
            "semantic_similarity_result": {"score": 5},
            "overall_tool_invocation_result": {"tool_invocation_score": 1.0},
            "expectation_results": [
                {
                    "expectation": "Agent should pass",
                    "met_count": 0,
                    "not_met_count": 1,
                    "met_percentage": 0.0,
                    "not_met_percentage": 100.0,
                }
            ],
        },
    }

    df_dict = utils.evals_to_dataframe([res])

    assert len(df_dict["summary"]) == 1
    assert "semantic_score" in df_dict["summary"].columns
    assert "tool_invocation_score" in df_dict["summary"].columns

    assert len(df_dict["failures"]) == 1
    assert df_dict["failures"].iloc[0]["expected"] == "Agent should pass"

    assert len(df_dict["metadata"]) == 1
    assert df_dict["metadata"].iloc[0]["evaluation_run"] == "eval_run/123"
    assert df_dict["metadata"].iloc[0]["expected"] == "Agent should pass"
    assert df_dict["metadata"].iloc[0]["outcome"] == "FAIL"
    assert df_dict["metadata"].iloc[0]["score"] == "0 / 1"


def test_to_bigquery():
    """Test to_bigquery export without requiring pandas."""
    utils = EvalUtils(app_name="projects/test_project/locations/l/apps/a")

    # Mock the dataframe and its to_gbq method
    mock_df = MagicMock()

    # Mock out the google.cloud.bigquery and pandas_gbq imports
    sys.modules["google"] = MagicMock()
    sys.modules["google.cloud"] = MagicMock()
    sys.modules["google.cloud.bigquery"] = MagicMock()
    sys.modules["pandas_gbq"] = MagicMock()

    utils.to_bigquery(mock_df, "my_dataset.my_table")

    mock_df.to_gbq.assert_called_once_with(
        destination_table="my_dataset.my_table",
        project_id="test_project",
        if_exists="append",
        credentials=utils.creds,
    )

    # Cleanup mocks
    del sys.modules["google.cloud.bigquery"]
    del sys.modules["pandas_gbq"]


def test_load_golden_eval_from_compressed_yaml():
    """Test load_golden_eval_from_yaml with compressed format."""
    # We want to test that EvalUtils.load_golden_eval_from_yaml parses this
    # correctly from the local example file.

    test_file_path = "tests/testdata/compressed_example.yaml"
    with (
        patch("cxas_scrapi.utils.eval_utils.uuid.uuid4") as mock_uuid,
        patch("cxas_scrapi.utils.eval_utils.Evaluations") as mock_eval_cls,
    ):
        mock_uuid.return_value = "mock_uuid"
        mock_eval_instance = mock_eval_cls.return_value
        foc_mock = mock_eval_instance.find_or_create_evaluation_expectation
        foc_mock.return_value = (
            "projects/p/locations/l/apps/a/evaluationExpectations/exp1"
        )

        utils = EvalUtils(app_name="projects/p/locations/l/apps/a")
        result = utils.load_golden_eval_from_yaml(test_file_path)

        # Verify "Unlock_Intent1" (the first conversation) was picked up
        assert result["displayName"] == "Unlock_Intent1"
        assert result["tags"] == ["direct", "p0", "compressed_example"]

        # Verify turns
        turns = result["golden"]["turns"]
        assert len(turns) == 2  # 2 turns in Unlock_Intent1

        # Turn 1: Implicit greeting -> Agent response
        # user: None -> event: welcome
        # agent: In a sentence or two...
        turn0_steps = turns[0]["steps"]
        assert turn0_steps[0]["userInput"]["event"]["event"] == "welcome"
        assert (
            turn0_steps[1]["expectation"]["agentResponse"]["chunks"][0]["text"]
            == "In a sentence or two, what are you calling about today?"
        )

        # Turn 2: User input -> Tool calls
        # user: "unlock a phone"
        # agent: # silent transfer (so no agentResponse expectation)
        # tool_calls: retrieve_intent_matches, transfer_to_cx
        turn1_steps = turns[1]["steps"]
        assert turn1_steps[0]["userInput"]["text"] == "unlock a phone"

        # Check tool calls
        # We expect toolCall expectations for each tool in the list
        # The order depends on implementation, but likely sequential

        # First tool: retrieve_intent_matches
        tool1 = turn1_steps[1]["expectation"]["toolCall"]
        assert (
            tool1["tool"]
            == "projects/p/locations/l/apps/a/tools/retrieve_intent_matches"
        )
        assert tool1["id"] == "adk-mock_uuid"

        # Second tool: transfer_to_cx
        tool2 = turn1_steps[2]["expectation"]["toolCall"]
        assert (
            tool2["tool"]
            == "projects/p/locations/l/apps/a/tools/transfer_to_cx"
        )
        assert tool2["id"] == "adk-mock_uuid"
        assert tool2["args"] == {"intent": "Unlock"}

        # Verify evaluation expectations
        eval_exps = result["golden"]["evaluationExpectations"]
        assert len(eval_exps) == 1
        assert (
            eval_exps[0]
            == "projects/p/locations/l/apps/a/evaluationExpectations/exp1"
        )
        mock_eval_instance.find_or_create_evaluation_expectation.assert_any_call(
            llm_prompt=(
                "There must be a transfer_to_cx tool call with the intent "
                "parameter set to Unlock"
            )
        )


def test_load_golden_evals_from_compressed_yaml():
    """Test load_golden_evals_from_yaml returns all conversations."""
    test_file_path = "tests/testdata/compressed_example.yaml"
    with (
        patch("cxas_scrapi.utils.eval_utils.uuid.uuid4") as mock_uuid,
        patch("cxas_scrapi.utils.eval_utils.Evaluations") as mock_eval_cls,
    ):
        mock_uuid.return_value = "mock_uuid"
        mock_eval_instance = mock_eval_cls.return_value
        mock_eval_instance.find_or_create_evaluation_expectation.side_effect = (
            lambda llm_prompt, **kwargs: f"resolved/{llm_prompt[:10]}"
        )

        utils = EvalUtils(app_name="projects/p/locations/l/apps/a")
        results = utils.load_golden_evals_from_yaml(test_file_path)

        assert isinstance(results, list)
        assert len(results) == 3

        # Verify names
        assert results[0]["displayName"] == "Unlock_Intent1"
        assert results[1]["displayName"] == "Unlock.Intent2"
        assert results[2]["displayName"] == "Datastore_Intent1"

        # Verify expectations were resolved
        assert results[0]["golden"]["evaluationExpectations"][0].startswith(
            "resolved/"
        )
        assert results[2]["golden"]["evaluationExpectations"][0].startswith(
            "resolved/"
        )


def test_load_golden_eval_from_exported_yaml():
    test_file_path = "tests/testdata/exported_eval_example.yaml"
    with (
        patch("cxas_scrapi.utils.eval_utils.uuid.uuid4") as mock_uuid,
        patch("cxas_scrapi.utils.eval_utils.Evaluations") as mock_eval_cls,
    ):
        mock_uuid.return_value = "mock_uuid"

        # Make the mock stringify the display_name so it passes assertion
        mock_eval_instance = mock_eval_cls.return_value
        mock_eval_instance.find_or_create_evaluation_expectation.side_effect = (
            lambda **kwargs: kwargs.get(
                "display_name", "Simple tool expectation 1"
            )
        )

        utils = EvalUtils(app_name="projects/p/locations/l/apps/a")
        result = utils.load_golden_eval_from_yaml(test_file_path)

        # Verify it returns the parsed data correctly
        assert result["displayName"] == "Basic Product Search Simplified"
        assert len(result["golden"]["turns"]) == 4
        assert (
            result["golden"]["turns"][0]["steps"][0]["userInput"]["event"][
                "event"
            ]
            == "WelcomeEvent"
        )
        assert (
            result["golden"]["evaluationExpectations"][0]
            == "Simple tool expectation 1"
        )


def test_process_dataset_turn_with_tool_mapping():
    """Test _process_dataset_turn correctly resolves tool names."""
    utils = EvalUtils(app_name="projects/p/locations/l/apps/a")
    utils.tool_map = {
        "my_tool": "projects/p/locations/l/apps/a/tools/resolved_tool"
    }

    turn = {
        "user": "run my tool",
        "tool_calls": [
            {
                "action": "my_tool",
                "args": {"arg1": "val1"},
                "output": "res1",
            }
        ],
    }

    result = utils._process_dataset_turn(Turn.model_validate(turn), {}, False)

    steps = result["steps"]

    # User input step
    assert steps[0]["userInput"]["text"] == "run my tool"

    # Tool call expectation
    tool_call = steps[1]["expectation"]["toolCall"]
    assert (
        tool_call["tool"] == "projects/p/locations/l/apps/a/tools/resolved_tool"
    )
    assert tool_call["args"] == {"arg1": "val1"}

    # Tool response expectation
    tool_res = steps[2]["expectation"]["toolResponse"]
    assert (
        tool_res["tool"] == "projects/p/locations/l/apps/a/tools/resolved_tool"
    )
    assert tool_res["response"] == "res1"


def test_process_dataset_turn_with_multi_agent_responses():
    """Test _process_dataset_turn with multiple agent responses."""
    utils = EvalUtils(app_name="projects/p/locations/l/apps/a")

    # Case 1: List of responses
    turn_list = {
        "user": "hello",
        "agent": ["response 1", "response 2"],
    }
    result_list = utils._process_dataset_turn(
        Turn.model_validate(turn_list), {}, False
    )
    steps_list = result_list["steps"]
    assert steps_list[0]["userInput"]["text"] == "hello"
    assert (
        steps_list[1]["expectation"]["agentResponse"]["chunks"][0]["text"]
        == "response 1"
    )
    assert (
        steps_list[2]["expectation"]["agentResponse"]["chunks"][0]["text"]
        == "response 2"
    )

    # Case 2: Single string response (backward compatibility)
    turn_str = {
        "user": "hi",
        "agent": "single response",
    }
    result_str = utils._process_dataset_turn(
        Turn.model_validate(turn_str), {}, False
    )
    steps_str = result_str["steps"]
    assert steps_str[0]["userInput"]["text"] == "hi"
    assert (
        steps_str[1]["expectation"]["agentResponse"]["chunks"][0]["text"]
        == "single response"
    )


def test_create_and_run_evaluation_from_yaml():
    """Test create_and_run_evaluation_from_yaml orchestration."""
    utils = EvalUtils(app_name="projects/p/locations/l/apps/a")

    with (
        patch.object(utils, "load_golden_eval_from_yaml") as mock_load,
        patch.object(utils, "create_evaluation") as mock_create,
        patch.object(utils, "run_evaluation") as mock_run,
    ):
        mock_load.return_value = {"displayName": "Test Eval", "golden": {}}
        mock_created_eval = MagicMock()
        mock_created_eval.display_name = "Test Eval"
        mock_created_eval.name = "projects/p/locations/l/apps/a/evaluations/e1"
        mock_create.return_value = mock_created_eval

        mock_run_res = MagicMock()
        mock_run.return_value = mock_run_res

        res = utils.create_and_run_evaluation_from_yaml("test.yaml")

        mock_load.assert_called_once_with("test.yaml")
        mock_create.assert_called_once_with(
            evaluation={"displayName": "Test Eval", "golden": {}},
            app_name="projects/p/locations/l/apps/a",
        )
        mock_run.assert_called_once_with(
            evaluations=["projects/p/locations/l/apps/a/evaluations/e1"],
            app_name="projects/p/locations/l/apps/a",
            modality="text",
            run_count=None,
        )

        assert res["evaluation"] == mock_created_eval
        assert res["run"] == mock_run_res


def test_load_golden_eval_from_direct_export_yaml():
    """Test Case 1b: load_golden_eval_from_yaml with direct export format."""
    dummy_yaml = {
        "name": "Direct_Export_Eval",
        "turns": [{"user": "hello", "agent": "hi there"}],
        "expectations": ["Must say hi"],
    }

    with (
        patch("builtins.open", mock_open(read_data="")),
        patch("yaml.safe_load", return_value=dummy_yaml),
        patch("cxas_scrapi.utils.eval_utils.Evaluations") as mock_eval_cls,
    ):
        mock_eval_instance = mock_eval_cls.return_value
        foc_mock = mock_eval_instance.find_or_create_evaluation_expectation
        foc_mock.return_value = "exp/1"

        utils = EvalUtils(app_name="p/l/a/a")
        result = utils.load_golden_eval_from_yaml("dummy.yaml")

        assert result["displayName"] == "Direct_Export_Eval"
        assert len(result["golden"]["turns"]) == 1
        assert result["golden"]["evaluationExpectations"] == ["exp/1"]
        mock_eval_instance.find_or_create_evaluation_expectation.assert_called_once_with(
            llm_prompt="Must say hi"
        )


def test_process_conversation_expectations():
    """Test _process_conversation_expectations with various formats."""
    utils = EvalUtils(app_name="p/l/a/a")

    with patch.object(
        utils.eval_client, "find_or_create_evaluation_expectation"
    ) as mock_find:
        mock_find.side_effect = ["exp/string", "exp/dict"]

        exps = [
            "Just a string prompt",
            {"prompt": "Dict prompt", "displayName": "My Name"},
            {"other": "format"},
            123,
        ]

        result = utils._process_conversation_expectations(exps)

        assert result == [
            "exp/string",
            "exp/dict",
            "{'other': 'format'}",
            "123",
        ]

        # Check first call
        mock_find.assert_any_call(llm_prompt="Just a string prompt")

        # Check second call
        mock_find.assert_any_call(
            llm_prompt="Dict prompt", display_name="My Name"
        )
