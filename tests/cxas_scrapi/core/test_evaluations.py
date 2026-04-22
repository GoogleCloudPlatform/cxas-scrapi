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

from unittest.mock import MagicMock, mock_open, patch

import pytest
from google.cloud.ces_v1beta import types

from cxas_scrapi.core.evaluations import Evaluations, ExportFormat


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_evaluations_list(mock_client_cls):
    """Test Evaluations.list_evaluations."""
    mock_client = mock_client_cls.return_value
    mock_eval = MagicMock()
    mock_eval.name = "projects/p/locations/l/apps/a/evaluations/e1"
    mock_eval.display_name = "Eval 1"
    mock_client.list_evaluations.return_value = [mock_eval]

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    res = evals_client.list_evaluations()

    assert len(res) == 1
    assert res[0].display_name == "Eval 1"
    mock_client.list_evaluations.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_evaluations_get_map(mock_client_cls):
    """Test Evaluations.get_evaluations_map."""
    mock_client = mock_client_cls.return_value

    mock_eval1 = MagicMock()
    mock_eval1.name = "projects/p/locations/l/apps/a/evaluations/e1"
    mock_eval1.display_name = "Eval 1"
    mock_eval1.golden = True
    mock_eval1.scenario = None

    mock_eval2 = MagicMock()
    mock_eval2.name = "projects/p/locations/l/apps/a/evaluations/e2"
    mock_eval2.display_name = "Eval 2"
    mock_eval2.golden = None
    mock_eval2.scenario = True

    mock_client.list_evaluations.return_value = [mock_eval1, mock_eval2]

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    res_normal = evals_client.get_evaluations_map()
    assert (
        res_normal["goldens"]["projects/p/locations/l/apps/a/evaluations/e1"]
        == "Eval 1"
    )
    assert (
        res_normal["scenarios"]["projects/p/locations/l/apps/a/evaluations/e2"]
        == "Eval 2"
    )

    res_reverse = evals_client.get_evaluations_map(reverse=True)
    assert (
        res_reverse["goldens"]["Eval 1"]
        == "projects/p/locations/l/apps/a/evaluations/e1"
    )
    assert (
        res_reverse["scenarios"]["Eval 2"]
        == "projects/p/locations/l/apps/a/evaluations/e2"
    )


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_evaluations_get(mock_client_cls):
    """Test Evaluations.get_evaluation."""
    mock_client = mock_client_cls.return_value
    mock_eval = MagicMock()
    mock_eval.name = "projects/p/locations/l/apps/a/evaluations/e1"
    mock_client.get_evaluation.return_value = mock_eval

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    res = evals_client.get_evaluation(
        "projects/p/locations/l/apps/a/evaluations/e1"
    )

    assert res.name == "projects/p/locations/l/apps/a/evaluations/e1"
    mock_client.get_evaluation.assert_called_once()


def test_eval_dict_to_yaml():
    """Test static method eval_dict_to_yaml for the new dataset format."""
    eval_dict = {
        "display_name": "Test Eval",
        "golden": {
            "turns": [
                {
                    "steps": [
                        {
                            "user_input": {
                                "text": "hi",
                                "variables": {"locale": "en-US"},
                            }
                        },
                        {
                            "expectation": {
                                "agent_response": {
                                    "chunks": [{"text": "hello"}]
                                }
                            }
                        },
                        {
                            "expectation": {
                                "agent_transfer": {
                                    "target_agent": "projects/123/agents/456"
                                }
                            }
                        },
                        {
                            "user_input": {
                                "text": "next turn",
                                "variables": {"user": "test"},
                            }
                        },
                        {
                            "expectation": {
                                "tool_call": {
                                    "tool": "my_tool",
                                    "args": {"param": "val"},
                                }
                            }
                        },
                    ]
                }
            ],
            "evaluation_expectations": ["projects/123/expectations/abc"],
        },
    }

    res = Evaluations.eval_dict_to_yaml(eval_dict)

    # Check dataset format structure
    assert "conversations" in res
    assert len(res["conversations"]) == 1

    conv = res["conversations"][0]
    assert conv["conversation"] == "Test Eval"

    # Check session parameters extracted from userInput
    assert conv.get("session_parameters") == {"locale": "en-US", "user": "test"}

    # Check expectations logic
    assert conv["expectations"] == ["projects/123/expectations/abc"]

    # Check turn splitting
    # Turn 1: user "hi", agent "hello", tool_call transfer_to_agent
    # Turn 2: user "next turn", tool_call my_tool
    turns = conv["turns"]
    assert len(turns) == 2

    assert turns[0]["user"] == "hi"
    assert turns[0]["agent"] == "hello"
    assert len(turns[0]["tool_calls"]) == 1
    assert turns[0]["tool_calls"][0]["action"] == "transfer_to_agent"
    assert turns[0]["tool_calls"][0]["agent"] == "projects/123/agents/456"

    assert turns[1]["user"] == "next turn"
    assert len(turns[1]["tool_calls"]) == 1
    assert turns[1]["tool_calls"][0]["action"] == "my_tool"
    assert turns[1]["tool_calls"][0]["args"] == {"param": "val"}


def test_eval_dict_to_yaml_multi_agent():
    """Test eval_dict_to_yaml with multiple agent responses in a single turn."""
    eval_dict = {
        "display_name": "Test Multi Agent Eval",
        "golden": {
            "turns": [
                {
                    "steps": [
                        {
                            "user_input": {
                                "text": "hi",
                            }
                        },
                        {
                            "expectation": {
                                "agent_response": {
                                    "chunks": [{"text": "hello response 1"}]
                                }
                            }
                        },
                        {
                            "expectation": {
                                "agent_response": {
                                    "chunks": [{"text": "hello response 2"}]
                                }
                            }
                        },
                    ]
                }
            ],
        },
    }

    res = Evaluations.eval_dict_to_yaml(eval_dict)

    assert "conversations" in res
    assert len(res["conversations"]) == 1

    conv = res["conversations"][0]
    assert conv["conversation"] == "Test Multi Agent Eval"

    turns = conv["turns"]
    assert len(turns) == 1

    assert turns[0]["user"] == "hi"
    assert turns[0]["agent"] == ["hello response 1", "hello response 2"]


@patch("cxas_scrapi.core.evaluations.Evaluations.get_evaluation")
def test_export_evaluation(mock_get_eval):
    """Test Evaluations.export_evaluation."""
    mock_obj = MagicMock()
    mock_obj.display_name = "Exported Eval"

    # Mock the to_dict method
    with patch("cxas_scrapi.core.evaluations.type") as mock_type:
        mock_to_dict = MagicMock(
            return_value={
                "display_name": "Exported Eval",
                "golden": {"turns": []},
            }
        )
        mock_type.return_value.to_dict = mock_to_dict

        evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
        # We also need to mock credentials properly if it tries to init client
        with patch("cxas_scrapi.core.evaluations.EvaluationServiceClient"):
            yaml_str = evals_client.export_evaluation(
                "projects/p/locations/l/apps/a/evaluations/e1"
            )
            assert "conversation: Exported Eval" in yaml_str

            json_str = evals_client.export_evaluation(
                "projects/p/locations/l/apps/a/evaluations/e1",
                output_format=ExportFormat.JSON,
            )
            assert '"conversation": "Exported Eval"' in json_str


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_import_evaluations(mock_client_cls):
    """Test Evaluations.import_evaluations."""
    mock_client = mock_client_cls.return_value
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    # Test GCS URI
    evals_client.import_evaluations(
        gcs_uri="gs://bucket/file.csv", conflict_strategy=1
    )
    mock_client.import_evaluations.assert_called_once()

    mock_client.import_evaluations.reset_mock()

    # Test CSV Content
    evals_client.import_evaluations(csv_content=b"foo,bar")
    mock_client.import_evaluations.assert_called_once()

    mock_client.import_evaluations.reset_mock()

    # Test Conversations
    evals_client.import_evaluations(
        conversations=["projects/p/locations/l/apps/a/conversations/c1"]
    )
    mock_client.import_evaluations.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_list_evaluation_expectations(mock_client_cls):
    """Test Evaluations.list_evaluation_expectations."""
    mock_client = mock_client_cls.return_value
    mock_client.list_evaluation_expectations.return_value = ["exp1", "exp2"]

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    res = evals_client.list_evaluation_expectations()

    assert len(res) == 2
    mock_client.list_evaluation_expectations.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_get_evaluation_expectation(mock_client_cls):
    """Test Evaluations.get_evaluation_expectation."""
    mock_client = mock_client_cls.return_value
    mock_exp = MagicMock()
    mock_exp.name = "projects/p/locations/l/apps/a/evaluationExpectations/e1"
    mock_client.get_evaluation_expectation.return_value = mock_exp

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    res = evals_client.get_evaluation_expectation(
        "projects/p/locations/l/apps/a/evaluationExpectations/e1"
    )

    assert res.name == "projects/p/locations/l/apps/a/evaluationExpectations/e1"
    mock_client.get_evaluation_expectation.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_create_evaluation_expectation(mock_client_cls):
    """Test Evaluations.create_evaluation_expectation."""
    mock_client = mock_client_cls.return_value
    mock_client.create_evaluation_expectation.return_value = MagicMock(
        name="created_exp"
    )

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    # Test with dict
    evals_client.create_evaluation_expectation({"display_name": "New Exp"})

    mock_client.create_evaluation_expectation.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_update_evaluation_expectation(mock_client_cls):
    """Test Evaluations.update_evaluation_expectation."""
    mock_client = mock_client_cls.return_value

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    mock_exp = MagicMock()

    evals_client.update_evaluation_expectation(mock_exp)

    mock_client.update_evaluation_expectation.assert_called_once()


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_delete_evaluation_expectation(mock_client_cls):
    """Test Evaluations.delete_evaluation_expectation."""
    mock_client = mock_client_cls.return_value

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    evals_client.delete_evaluation_expectation(
        "projects/p/locations/l/apps/a/evaluationExpectations/e1"
    )

    mock_client.delete_evaluation_expectation.assert_called_once()


@patch("cxas_scrapi.core.evaluations.AgentServiceClient")
def test_get_evaluation_thresholds(mock_agent_client_cls):
    """Test Evaluations.get_evaluation_thresholds."""
    mock_agent_client = mock_agent_client_cls.return_value

    # Create a mock App with thresholds
    app_obj = types.App()

    # Safely assign values simulating what the API would return
    metrics_thresholds = app_obj.evaluation_metrics_thresholds
    thresholds = metrics_thresholds.golden_evaluation_metrics_thresholds
    turn_thresholds = thresholds.turn_level_metrics_thresholds
    turn_thresholds.semantic_similarity_success_threshold = 3
    turn_thresholds.overall_tool_invocation_correctness_threshold = 1.0

    mock_agent_client.get_app.return_value = app_obj

    with patch(
        "cxas_scrapi.core.evaluations.json_format.MessageToDict"
    ) as mock_message_to_dict:
        mock_message_to_dict.return_value = {
            "evaluation_metrics_thresholds": {
                "golden_evaluation_metrics_thresholds": {
                    "turn_level_metrics_thresholds": {
                        "semantic_similarity_success_threshold": 3
                    }
                }
            }
        }
        evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
        res = evals_client.get_evaluation_thresholds()

    mock_agent_client.get_app.assert_called_once()

    assert "golden_evaluation_metrics_thresholds" in res
    assert (
        res["golden_evaluation_metrics_thresholds"][
            "turn_level_metrics_thresholds"
        ]["semantic_similarity_success_threshold"]
        == 3
    )


@patch("cxas_scrapi.core.evaluations.types")
@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_run_evaluation(mock_client_cls, mock_types):
    """Test Evaluations.run_evaluation."""

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    mock_map = {
        "goldens": {
            "First Golden": "projects/p/locations/l/apps/a/evaluations/g1",
            "Second Golden": "projects/p/locations/l/apps/a/evaluations/g2",
        },
        "scenarios": {
            "First Scenario": "projects/p/locations/l/apps/a/evaluations/s1"
        },
    }

    with patch.object(
        evals_client, "_get_or_load_evals_map", return_value=mock_map
    ):
        # Test running by display name list
        mock_types.RunEvaluationRequest.reset_mock()
        evals_client.run_evaluation(
            evaluations=["First Golden", "First Scenario"]
        )
        request_kwargs = mock_types.RunEvaluationRequest.call_args[1]
        assert set(request_kwargs["evaluations"]) == {
            "projects/p/locations/l/apps/a/evaluations/g1",
            "projects/p/locations/l/apps/a/evaluations/s1",
        }

        # Test single evaluation display name
        mock_types.RunEvaluationRequest.reset_mock()
        evals_client.run_evaluation(evaluations="Second Golden")
        request_kwargs = mock_types.RunEvaluationRequest.call_args[1]
        assert list(request_kwargs["evaluations"]) == [
            "projects/p/locations/l/apps/a/evaluations/g2"
        ]

        # Test run by eval_type "goldens"
        mock_types.RunEvaluationRequest.reset_mock()
        evals_client.run_evaluation(eval_type="goldens")
        request_kwargs = mock_types.RunEvaluationRequest.call_args[1]
        assert set(request_kwargs["evaluations"]) == {
            "projects/p/locations/l/apps/a/evaluations/g1",
            "projects/p/locations/l/apps/a/evaluations/g2",
        }

        # Test run by eval_type "all"
        mock_types.RunEvaluationRequest.reset_mock()
        evals_client.run_evaluation(eval_type="all")
        request_kwargs = mock_types.RunEvaluationRequest.call_args[1]
        assert len(request_kwargs["evaluations"]) == 3

        # Test error cases
        with pytest.raises(ValueError):
            evals_client.run_evaluation()

        with pytest.raises(ValueError):
            evals_client.run_evaluation(evaluations=["Not Found"])


@patch("cxas_scrapi.core.evaluations.types")
@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_get_evaluation_run(mock_client_cls, mock_types):
    """Test Evaluations.get_evaluation_run."""
    mock_client = mock_client_cls.return_value
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    mock_types.GetEvaluationRunRequest.reset_mock()
    evals_client.get_evaluation_run(
        evaluation_run_id="projects/p/locations/l/apps/a/evaluationRuns/r1"
    )

    mock_client.get_evaluation_run.assert_called_once()
    request_kwargs = mock_types.GetEvaluationRunRequest.call_args[1]
    assert (
        request_kwargs["name"]
        == "projects/p/locations/l/apps/a/evaluationRuns/r1"
    )


@patch("cxas_scrapi.core.evaluations.types")
@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_list_evaluation_results_by_run(mock_client_cls, mock_types):
    """Test Evaluations.list_evaluation_results_by_run."""
    mock_client = mock_client_cls.return_value
    mock_run = MagicMock()
    mock_run.evaluation_results = ["res1", "res2"]
    mock_client.get_evaluation_run.return_value = mock_run

    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    res = evals_client.list_evaluation_results_by_run(
        evaluation_run_id="projects/p/locations/l/apps/other/evaluationRuns/r1"
    )

    mock_client.get_evaluation_run.assert_called_once_with(
        name="projects/p/locations/l/apps/other/evaluationRuns/r1"
    )
    assert mock_client.get_evaluation_result.call_count == 2
    assert len(res) == 2

    # Test error condition
    with pytest.raises(ValueError):
        evals_client.list_evaluation_results_by_run(
            evaluation_run_id="invalid_format"
        )


class MockEval:
    """Mock Evaluation proto object for testing."""

    def __init__(self, display_name, data_dict):
        self.display_name = display_name
        self.data_dict = data_dict

    @classmethod
    def to_dict(cls, obj):
        return obj.data_dict


@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_build_search_index(mock_client_cls):
    """Test Evaluations.build_search_index."""
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    evals_client.list_evaluations = MagicMock(
        return_value=[
            MockEval(
                "My Eval 1", {"foo": "bar", "tools": "projects/p/tools/t1"}
            ),
            MockEval("My Eval 2", {"foo": "baz", "variables": "var_1"}),
        ]
    )

    evals_client.build_search_index()

    assert len(evals_client._eval_search_index) == 2
    assert "projects/p/tools/t1" in evals_client._eval_search_index["My Eval 1"]
    assert "var_1" in evals_client._eval_search_index["My Eval 2"]

    # Test that calling it again without force=True doesn't rebuild
    evals_client.list_evaluations = MagicMock()
    evals_client.build_search_index()
    evals_client.list_evaluations.assert_not_called()

    # Test force=True
    evals_client.build_search_index(force=True)
    evals_client.list_evaluations.assert_called_once()


@patch("cxas_scrapi.core.evaluations.Agents")
@patch("cxas_scrapi.core.evaluations.Tools")
@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_search_evaluations(mock_client_cls, mock_tools_cls, mock_agents_cls):
    """Test Evaluations.search_evaluations."""
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")
    evals_client.list_evaluations = MagicMock(
        return_value=[
            MockEval(
                "Eval Tool 1",
                {"tools": "projects/p/tools/t1", "variables": "var1"},
            ),
            MockEval("Eval Agent 1", {"agents": "projects/p/agents/a1"}),
            MockEval(
                "Eval Both",
                {
                    "tools": "projects/p/tools/t2",
                    "agents": "projects/p/agents/a1",
                },
            ),
        ]
    )

    mock_tools_instance = mock_tools_cls.return_value
    mock_tools_instance.get_tools_map.return_value = {
        "My Tool 1": "projects/p/tools/t1",
        "My Tool 2": "projects/p/tools/t2",
    }

    mock_agents_instance = mock_agents_cls.return_value
    mock_agents_instance.get_agents_map.return_value = {
        "My Agent 1": "projects/p/agents/a1",
    }

    # Search for tool 1
    res = evals_client.search_evaluations(
        app_name="projects/p/locations/l/apps/a", tools=["My Tool 1"]
    )
    assert res == ["Eval Tool 1"]

    # Search for agent 1
    res = evals_client.search_evaluations(
        app_name="projects/p/locations/l/apps/a", agents=["My Agent 1"]
    )
    assert set(res) == {"Eval Agent 1", "Eval Both"}

    # Search by variable
    res = evals_client.search_evaluations(
        app_name="projects/p/locations/l/apps/a", variables=["var1"]
    )
    assert res == ["Eval Tool 1"]

    # Search by multiple
    res = evals_client.search_evaluations(
        app_name="projects/p/locations/l/apps/a",
        tools=["My Tool 2"],
        agents=["My Agent 1"],
    )
    assert res == ["Eval Both"]

    # Test error cases
    with pytest.raises(ValueError):
        evals_client.search_evaluations(
            app_name="projects/p/locations/l/apps/a", tools=["Invalid Tool"]
        )

    with pytest.raises(ValueError):
        evals_client.search_evaluations(
            app_name="projects/p/locations/l/apps/a", agents=["Invalid Agent"]
        )

    with pytest.raises(
        ValueError, match="Must provide at least one search term"
    ):
        evals_client.search_evaluations(
            app_name="projects/p/locations/l/apps/a"
        )


@patch("cxas_scrapi.core.evaluations.json_format")
@patch("cxas_scrapi.core.evaluations.types")
@patch("cxas_scrapi.core.evaluations.EvaluationServiceClient")
def test_evaluations_create_evaluation(
    mock_client_cls, mock_types, mock_json_format
):
    """Test Evaluations.create_evaluation."""
    mock_client = mock_client_cls.return_value
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    # Test with dict
    evaluation_dict = {"display_name": "New Eval"}
    mock_eval_msg = MagicMock()
    mock_types.Evaluation.return_value = mock_eval_msg

    evals_client.create_evaluation(evaluation=evaluation_dict)

    mock_json_format.ParseDict.assert_called_once_with(
        evaluation_dict, mock_eval_msg._pb, ignore_unknown_fields=True
    )
    mock_types.CreateEvaluationRequest.assert_called_once_with(
        parent="projects/p/locations/l/apps/a", evaluation=mock_eval_msg
    )
    mock_client.create_evaluation.assert_called_once()

    # Test with object
    mock_client.create_evaluation.reset_mock()
    mock_types.CreateEvaluationRequest.reset_mock()
    evaluation_obj = MagicMock()

    evals_client.create_evaluation(evaluation=evaluation_obj)

    mock_types.CreateEvaluationRequest.assert_called_once_with(
        parent="projects/p/locations/l/apps/a", evaluation=evaluation_obj
    )
    mock_client.create_evaluation.assert_called_once()

    # Test missing app_name
    evals_client.app_name = None
    with pytest.raises(ValueError, match="app_name is required"):
        evals_client.create_evaluation(evaluation=evaluation_dict)


@patch("os.makedirs")
@patch.object(Evaluations, "export_evaluation")
@patch.object(Evaluations, "get_evaluations_map")
def test_bulk_export_evals(mock_get_map, mock_export, mock_makedirs):
    """Test Evaluations.bulk_export_evals."""
    evals_client = Evaluations(app_name="projects/p/locations/l/apps/a")

    # Mock get_evaluations_map
    mock_get_map.return_value = {
        "goldens": {
            "Golden 1": "projects/p/locations/l/apps/a/evaluations/g1",
            "Golden-2!": "projects/p/locations/l/apps/a/evaluations/g2",
        },
        "scenarios": {
            "Scenario 1": "projects/p/locations/l/apps/a/evaluations/s1"
        },
    }

    # Mock export_evaluation
    mock_export.return_value = "yaml_content"

    # Test 1: Exporting goldens
    evals_client.bulk_export_evals("goldens", "/valid/dir")

    # 2 exports should happen
    assert mock_export.call_count == 2
    mock_export.assert_any_call(
        "projects/p/locations/l/apps/a/evaluations/g1",
        output_format=ExportFormat.YAML,
        output_path="/valid/dir/evals/Golden_1.yaml",
    )
    mock_export.assert_any_call(
        "projects/p/locations/l/apps/a/evaluations/g2",
        output_format=ExportFormat.YAML,
        output_path="/valid/dir/evals/Golden-2_.yaml",
    )

    # Check that dir was made
    mock_makedirs.assert_called_with("/valid/dir/evals", exist_ok=True)

    # Reset mocks
    mock_export.reset_mock()
    mock_makedirs.reset_mock()

    # Test 2: Exporting scenarios
    evals_client.bulk_export_evals("scenarios", "/valid/dir")

    assert mock_export.call_count == 1
    mock_export.assert_called_once_with(
        "projects/p/locations/l/apps/a/evaluations/s1",
        output_format=ExportFormat.YAML,
        output_path="/valid/dir/evals/Scenario_1.yaml",
    )

    # Test 3: Bad type
    with pytest.raises(
        ValueError, match="eval_type must be either 'goldens' or 'scenarios'"
    ):
        evals_client.bulk_export_evals("typo", "/valid/dir")

    # Test 4: File write exception inside the loop (invalid file path logic,
    # caught by try/except)
    mock_export.reset_mock()
    mock_export.side_effect = Exception("Export failed")

    # This shouldn't crash, it should catch Exception and print failure
    m_open = mock_open()
    with patch("builtins.open", m_open):
        evals_client.bulk_export_evals("scenarios", "/valid/dir")
    assert mock_export.call_count == 1

    # Test 5: Invalid directory (os.makedirs fails)
    mock_makedirs.side_effect = PermissionError("Permission denied")
    with pytest.raises(PermissionError):
        evals_client.bulk_export_evals("scenarios", "/invalid/dir")
