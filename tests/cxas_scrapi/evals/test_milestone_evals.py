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

"""Unit tests for CXAS 2.0 Platform Milestone Evaluation features."""

import argparse
import base64
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml

# Mock dependencies before imports if needed
sys.modules.setdefault("google.cloud.texttospeech", MagicMock())
sys.modules.setdefault("websocket", MagicMock())
sys.modules.setdefault("google.cloud.ces", MagicMock())
sys.modules.setdefault("google.cloud.secretmanager", MagicMock())
sys.modules.setdefault("google.cloud.bigquery", MagicMock())
sys.modules.setdefault("pandas_gbq", MagicMock())

from google.cloud.ces_v1beta import types  # noqa: E402

from cxas_scrapi.cli.main import (  # noqa: E402
    push_eval,
    run_eval,
    wait_for_evaluation_completion,
)
from cxas_scrapi.core.evaluations import Evaluations  # noqa: E402
from cxas_scrapi.evals.simulation_evals import SimulationEvals  # noqa: E402
from cxas_scrapi.utils import reporting  # noqa: E402
from cxas_scrapi.utils.eval_utils import (  # noqa: E402
    MILESTONE_ALLOWED_EVAL_KEYS,
    MILESTONE_ALLOWED_ROOT_KEYS,
    MILESTONE_ALLOWED_STEP_KEYS,
    EvalUtils,
)


@pytest.fixture
def mock_app_name() -> str:
    return "projects/test-proj/locations/us-central1/apps/test-app"


def test_is_milestone_eval_data() -> None:
    """Test EvalUtils.is_milestone_eval_data identification."""
    # Data with 'evals' key
    assert EvalUtils.is_milestone_eval_data({"evals": [{"name": "test"}]})

    # Data with 'steps' key
    assert EvalUtils.is_milestone_eval_data(
        {"name": "test", "steps": [{"goal": "Authenticate user"}]}
    )

    # List of test cases
    assert EvalUtils.is_milestone_eval_data(
        [{"name": "test", "steps": [{"success_criteria": "Done"}]}]
    )

    # Negative cases
    assert not EvalUtils.is_milestone_eval_data({})
    assert not EvalUtils.is_milestone_eval_data({"conversations": []})
    assert not EvalUtils.is_milestone_eval_data("plain text")
    assert not EvalUtils.is_milestone_eval_data([{"not_steps": 123}])


def test_sanitize_milestone_eval_dict() -> None:
    """Test sanitization against CES MilestoneYamlTranslator rules."""
    raw_data = {
        "evals": [
            {
                "name": "account_balance_check",
                "tags": ["smoke", "banking"],
                "audio_expectations": ["Tone is polite"],
                "naturalness_metric": {"pass_threshold": 4.0},
                "barge_in_after_ms": 500,
                "session_parameters": {"user_id": "123"},
                "steps": [
                    {
                        "goal": "Collect account number",
                        "success_criteria": "Account number received",
                        "inject_variables": {"auth_state": "verified"},
                        "barge_in_after_ms": 200,
                    },
                    {
                        "goal": "Provide balance",
                        "success_criteria": "Balance provided",
                        "inject_variables": {"late_var": "hoisted"},
                        "extra_client_key": "discard_me",
                    },
                ],
            }
        ],
        "common_session_parameters": {"env": "prod"},
        "unsupported_root_key": "drop_me",
    }

    sanitized = EvalUtils.sanitize_milestone_eval_dict(raw_data)

    # Root level checks
    for k in sanitized:
        assert k in MILESTONE_ALLOWED_ROOT_KEYS
    assert "unsupported_root_key" not in sanitized
    assert "common_session_parameters" in sanitized
    assert len(sanitized["evals"]) == 1

    eval_entry = sanitized["evals"][0]
    for k in eval_entry:
        assert k in MILESTONE_ALLOWED_EVAL_KEYS
    assert "audio_expectations" not in eval_entry
    assert "naturalness_metric" not in eval_entry
    assert "barge_in_after_ms" not in eval_entry

    # inject_variables hoisting check:
    # step 0 keeps its inject_variables, step 1's inject_variables is hoisted to session_parameters
    step0 = eval_entry["steps"][0]
    step1 = eval_entry["steps"][1]

    for k in step0:
        assert k in MILESTONE_ALLOWED_STEP_KEYS
    for k in step1:
        assert k in MILESTONE_ALLOWED_STEP_KEYS

    assert step0.get("inject_variables") == {"auth_state": "verified"}
    assert "inject_variables" not in step1
    assert eval_entry["session_parameters"].get("late_var") == "hoisted"


def test_convert_sim_evals_to_milestone_yaml(tmp_path: Any) -> None:
    """Test convert_sim_evals_to_milestone_yaml from files and directories."""
    sim1 = {
        "name": "case_1",
        "steps": [{"goal": "Goal 1", "success_criteria": "Crit 1"}],
    }
    sim2 = {
        "name": "case_2",
        "steps": [{"goal": "Goal 2", "success_criteria": "Crit 2"}],
    }

    file1 = tmp_path / "sim1.yaml"
    file2 = tmp_path / "sim2.yaml"
    file1.write_text(yaml.dump(sim1), encoding="utf-8")
    file2.write_text(yaml.dump(sim2), encoding="utf-8")

    # From directory
    yaml_out = EvalUtils.convert_sim_evals_to_milestone_yaml(str(tmp_path))
    parsed = yaml.safe_load(yaml_out)
    assert "evals" in parsed
    names = [e["name"] for e in parsed["evals"]]
    assert "case_1" in names
    assert "case_2" in names


def test_eval_dict_to_yaml_scenario_milestone() -> None:
    """Test Evaluations.eval_dict_to_yaml with scenario milestone dictionary."""
    eval_dict = {
        "display_name": "Scenario_Milestone_Test",
        "tags": ["scenario_tag"],
        "scenario": {
            "session_parameters": {"account_type": "checking"},
            "evaluation_expectations": [
                "projects/p/locations/l/apps/a/evaluationExpectations/exp1"
            ],
            "milestones": [
                {
                    "goal": "Verify user identity",
                    "successCriteria": "User passes 2FA",
                    "maxTurnCount": 4,
                    "staticUtterance": "Hello, please verify",
                }
            ],
        },
    }

    out_yaml = Evaluations.eval_dict_to_yaml(eval_dict)
    assert "evals" in out_yaml
    eval_entry = out_yaml["evals"][0]
    assert eval_entry["name"] == "Scenario_Milestone_Test"
    assert eval_entry["tags"] == ["scenario_tag"]
    assert eval_entry["session_parameters"] == {"account_type": "checking"}
    assert len(eval_entry["steps"]) == 1
    step = eval_entry["steps"][0]
    assert step["goal"] == "Verify user identity"
    assert step["success_criteria"] == "User passes 2FA"
    assert step["max_turns"] == 4
    assert step["static_utterance"] == "Hello, please verify"


@patch("cxas_scrapi.core.evaluations.AuthorizedSession")
def test_import_evaluations_rest_yaml_content(
    mock_session_cls: Any, mock_app_name: str
) -> None:
    """Test Evaluations.import_evaluations via REST with yaml_content."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "name": f"{mock_app_name}/operations/op123",
        "done": True,
        "response": {"importedCount": 2},
    }
    mock_session.post.return_value = mock_resp

    eval_client = Evaluations(app_name=mock_app_name)
    yaml_content = "evals:\n  - name: test\n    steps: []"

    res = eval_client.import_evaluations(
        app_name=mock_app_name,
        yaml_content=yaml_content,
        conflict_strategy="OVERWRITE",
    )

    assert res["done"] is True
    mock_session.post.assert_called_once()
    called_url, called_kwargs = mock_session.post.call_args
    assert called_url[0].endswith(":importEvaluations")
    body = called_kwargs["json"]
    assert "yamlContent" in body
    decoded = base64.b64decode(body["yamlContent"]).decode("utf-8")
    assert decoded == yaml_content
    assert body["importOptions"]["conflictResolutionStrategy"] == "OVERWRITE"


def test_parse_eval_results_and_dataframe_milestones(
    mock_app_name: str,
) -> None:
    """Test _parse_eval_results and evals_to_dataframe handling of scenario milestone results."""
    eval_utils = EvalUtils(app_name=mock_app_name)

    mock_results = [
        {
            "name": f"{mock_app_name}/evaluations/e1/evaluationResults/res1",
            "evaluation_status": "PASS",
            "execution_state": "COMPLETED",
            "scenario_result": {
                "milestone_results": [
                    {
                        "milestone": {
                            "goal": "Transfer funds",
                            "success_criteria": "Amount transferred",
                        },
                        "status": "COMPLETED",
                        "justification": "Funds transferred successfully",
                    },
                    {
                        "milestone": {
                            "goal": "Send confirmation SMS",
                            "success_criteria": "SMS confirmed",
                        },
                        "status": "NOT_STARTED",
                        "justification": "Phone number was missing",
                    },
                ],
                "evaluation_expectation_results": [
                    {
                        "prompt": "Agent was courteous",
                        "outcome": 1,
                        "explanation": "Spoke politely",
                    }
                ],
            },
        }
    ]

    with (
        patch.object(eval_utils, "get_evaluation") as mock_get_eval,
        patch.object(eval_utils, "list_evaluations", return_value=[]),
    ):
        mock_eval = MagicMock()
        mock_eval.display_name = "Transfer Funds Scenario"
        mock_get_eval.return_value = mock_eval

        df_dict = eval_utils.evals_to_dataframe(results=mock_results)

    df_summary = df_dict["summary"]
    df_failures = df_dict["failures"]
    df_metadata = df_dict["metadata"]

    assert not df_summary.empty
    assert df_summary["display_name"].iloc[0] == "Transfer Funds Scenario"

    # Verify failure recorded for NOT_STARTED milestone
    assert not df_failures.empty
    milestone_failures = df_failures[df_failures["failure_type"] == "Milestone"]
    assert len(milestone_failures) == 1
    assert milestone_failures["expected"].iloc[0] == "Send confirmation SMS"
    assert "(NOT_STARTED)" in milestone_failures["actual"].iloc[0]

    # Verify metadata records both milestones and expectation
    assert not df_metadata.empty
    milestone_meta = df_metadata[df_metadata["type"] == "Milestone"]
    assert len(milestone_meta) == 2
    assert set(milestone_meta["outcome"]) == {"PASS", "FAIL"}


def test_simulation_evals_requires_local_execution() -> None:
    """Test SimulationEvals.requires_local_execution classification."""
    # Text-only standard milestone -> platform compatible
    text_tc = {
        "name": "basic_text",
        "modality": "text",
        "steps": [{"goal": "Greet", "success_criteria": "Hello"}],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(text_tc)
    assert not requires_local
    assert reason == ""

    # Audio modality -> requires local
    audio_tc = {"name": "audio_case", "modality": "audio", "steps": []}
    requires_local, reason = SimulationEvals.requires_local_execution(audio_tc)
    assert requires_local
    assert "Audio modality" in reason

    # Audio expectations -> requires local
    audio_exp_tc = {
        "name": "audio_exp",
        "audio_expectations": ["No echo"],
        "steps": [],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(
        audio_exp_tc
    )
    assert requires_local
    assert "audio_expectations" in reason

    # Naturalness metric -> requires local
    nat_tc = {
        "name": "nat_case",
        "naturalness_metric": {"pass_threshold": 3.0},
        "steps": [],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(nat_tc)
    assert requires_local
    assert "naturalness_metric" in reason

    # Barge-in (top-level and step-level, including 0ms) -> requires local
    barge_tc = {"name": "barge_case", "barge_in_after_ms": 500, "steps": []}
    requires_local, reason = SimulationEvals.requires_local_execution(barge_tc)
    assert requires_local
    assert "barge_in_after_ms" in reason

    step_barge_tc = {
        "name": "step_barge_case",
        "steps": [
            {"goal": "Ask policy"},
            {"goal": "Interrupt", "barge_in_after_ms": 0},
        ],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(
        step_barge_tc
    )
    assert requires_local
    assert "Step barge_in_after_ms" in reason

    # use_tool_fakes -> platform compatible
    fakes_tc = {
        "name": "fakes_case",
        "use_tool_fakes": True,
        "steps": [{"goal": "Check balance"}],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(fakes_tc)
    assert not requires_local
    assert reason == ""

    # audio_analysis -> requires local
    audio_analysis_tc = {
        "name": "audio_analysis_case",
        "audio_analysis": True,
        "steps": [],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(
        audio_analysis_tc
    )
    assert requires_local
    assert "audio_analysis" in reason

    # voice_config -> requires local
    voice_config_tc = {
        "name": "voice_config_case",
        "voice_config": {"voice_id": "alloy"},
        "steps": [],
    }
    requires_local, reason = SimulationEvals.requires_local_execution(
        voice_config_tc
    )
    assert requires_local
    assert "voice_config" in reason


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_simulation_evals_partitioning_and_platform_run(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    """Test SimulationEvals.run_simulations with run_on_platform=True."""
    sim = SimulationEvals(app_name=mock_app_name)

    tc_platform = {
        "name": "platform_case",
        "modality": "text",
        "steps": [{"goal": "Goal", "success_criteria": "Done"}],
    }
    tc_local = {
        "name": "local_audio_case",
        "modality": "audio",
        "steps": [{"goal": "Goal", "success_criteria": "Done"}],
    }

    platform_result = [
        {
            "name": "platform_case",
            "passed": True,
            "status": "PASS",
            "goals": "1/1",
            "expectations": "0/0",
            "duration_s": 1.2,
            "run": 1,
            "runs": 1,
            "platform": True,
        }
    ]
    local_result = [
        {
            "name": "local_audio_case",
            "passed": True,
            "status": "PASS",
            "goals": "1/1",
            "expectations": "0/0",
            "duration_s": 2.5,
            "run": 1,
            "runs": 1,
            "platform": False,
        }
    ]

    with (
        patch.object(
            sim, "run_simulations_on_platform", return_value=platform_result
        ) as mock_plat,
        patch.object(
            sim, "_aggregate_simulation_results", return_value=local_result
        ),
    ):
        combined = sim.run_simulations(
            [tc_platform, tc_local],
            runs=1,
            run_on_platform=True,
        )

        assert len(combined) == 2
        mock_plat.assert_called_once_with(
            [tc_platform], runs=1, timeout_seconds=600
        )
        assert combined[0]["name"] == "platform_case"
        assert combined[1]["name"] == "local_audio_case"


def test_cli_push_eval_milestone_detection(
    mock_app_name: str, tmp_path: Any
) -> None:
    """Test CLI push_eval auto-detection of Milestone format."""
    milestone_file = tmp_path / "milestones.yaml"
    milestone_data = {
        "evals": [
            {
                "name": "m1",
                "steps": [{"goal": "Goal 1", "success_criteria": "Done"}],
            }
        ]
    }
    milestone_file.write_text(yaml.dump(milestone_data), encoding="utf-8")

    args = argparse.Namespace(app_name=mock_app_name, file=str(milestone_file))

    with patch(
        "cxas_scrapi.utils.eval_utils.EvalUtils.push_milestone_evals_from_yaml"
    ) as mock_push_milestone:
        push_eval(args)
        mock_push_milestone.assert_called_once()


def test_cli_run_eval_type_and_run_count(mock_app_name: str) -> None:
    """Test CLI run_eval passing eval_type and run_count."""
    args = argparse.Namespace(
        app_name=mock_app_name,
        evaluation_id=None,
        display_name_prefix=None,
        tags=[],
        eval_type="scenarios",
        run_count=3,
        modality="text",
        golden_run_method="STABLE",
        wait=False,
    )

    with patch(
        "cxas_scrapi.core.evaluations.Evaluations.run_evaluation"
    ) as mock_run_eval:
        run_eval(args)
        mock_run_eval.assert_called_once_with(
            evaluations=None,
            eval_type="scenarios",
            run_count=3,
            app_name=mock_app_name,
            modality="text",
            golden_run_method="STABLE",
        )


@patch("cxas_scrapi.core.evaluations.AuthorizedSession")
def test_evaluations_rest_run_and_poll_fallback(
    mock_session_cls: Any, mock_app_name: str
) -> None:
    """Test REST execution and polling fallback for Milestone evaluations."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    run_resp = MagicMock()
    run_resp.json.return_value = {
        "name": f"{mock_app_name}/operations/op-run-1",
        "metadata": {
            "evaluationRun": f"{mock_app_name}/evaluationRuns/run-123"
        },
    }
    mock_session.post.return_value = run_resp

    eval_client = Evaluations(app_name=mock_app_name)
    op_dict = eval_client.run_evaluation_rest(
        evaluations=[f"{mock_app_name}/evaluations/eval-1"],
        app_name=mock_app_name,
        modality="text",
        run_count=1,
    )
    assert (
        op_dict["metadata"]["evaluationRun"]
        == f"{mock_app_name}/evaluationRuns/run-123"
    )

    run_get_resp = MagicMock()
    run_get_resp.json.return_value = {
        "name": f"{mock_app_name}/evaluationRuns/run-123",
        "state": "COMPLETED",
        "evaluationResults": [
            f"{mock_app_name}/evaluations/eval-1/results/res-1"
        ],
    }
    res_get_resp = MagicMock()
    res_get_resp.json.return_value = {
        "name": f"{mock_app_name}/evaluations/eval-1/results/res-1",
        "evaluationStatus": "PASS",
        "scenarioResult": {
            "milestoneResults": [
                {
                    "milestone": {"goal": "Step 1"},
                    "status": "COMPLETED",
                    "justification": "Done",
                }
            ]
        },
    }
    mock_session.get.side_effect = [run_get_resp, res_get_resp]

    eval_utils = EvalUtils(app_name=mock_app_name)
    eval_utils.eval_client = eval_client
    with patch.object(
        eval_client, "get_evaluation_run", side_effect=RuntimeError("gRPC off")
    ):
        results = eval_utils.wait_for_run_and_get_results(
            f"{mock_app_name}/evaluationRuns/run-123"
        )
    assert len(results) == 1
    assert (
        results[0]["scenarioResult"]["milestoneResults"][0]["status"]
        == "COMPLETED"
    )


def test_sanitize_milestone_eval_dict_edge_cases() -> None:
    """Test edge cases in sanitize_milestone_eval_dict (empty inputs, missing goals/criteria, invalid max_turns)."""
    assert EvalUtils.sanitize_milestone_eval_dict(None) == {"evals": []}
    assert EvalUtils.sanitize_milestone_eval_dict({}) == {"evals": []}

    # Single test case dict (without outer 'evals' wrapper)
    single_case = {
        "steps": [
            {},  # Both goal and success_criteria missing -> defaults filled
            {"goal": "Only goal"},  # success_criteria defaults to goal
            {"success_criteria": "Only criteria"},  # goal defaults to criteria
        ],
        "max_turns": "not_an_int",
    }
    res = EvalUtils.sanitize_milestone_eval_dict(single_case)
    assert len(res["evals"]) == 1
    ev = res["evals"][0]
    assert ev["name"] == "milestone_eval_1"
    assert "max_turns" not in ev
    assert ev["steps"][0] == {
        "goal": "Step 1",
        "success_criteria": "Goal achieved",
    }
    assert ev["steps"][1] == {
        "goal": "Only goal",
        "success_criteria": "Only goal",
    }
    assert ev["steps"][2] == {
        "goal": "Only criteria",
        "success_criteria": "Only criteria",
    }


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_run_simulations_on_platform_end_to_end(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    """Test SimulationEvals.run_simulations_on_platform full orchestration and result mapping."""
    sim = SimulationEvals(app_name=mock_app_name)
    test_cases = [
        {
            "name": "Scenario_A",
            "session_parameters": {"tier": "gold"},
            "steps": [{"goal": "Verify tier", "success_criteria": "Verified"}],
        },
        {
            "name": "Scenario_Missing",
            "steps": [{"goal": "Never ran", "success_criteria": "Done"}],
        },
    ]

    mock_raw_results = [
        {
            "name": f"{mock_app_name}/evaluations/e1/evaluationResults/r1",
            "evaluation_status": "PASS",
            "execution_state": "COMPLETED",
            "evaluation_run": f"{mock_app_name}/evaluationRuns/run-99",
            "scenario_result": {
                "conversation": f"{mock_app_name}/conversations/conv-abc",
                "milestone_results": [
                    {
                        "milestone": {"goal": "Verify tier"},
                        "status": "COMPLETED",
                        "justification": "Tier verified",
                    }
                ],
                "evaluation_expectation_results": [
                    {
                        "prompt": "Polite tone",
                        "outcome": "PASS",
                        "explanation": "Polite",
                    }
                ],
            },
        }
    ]

    with (
        patch.object(
            sim,
            "push_to_platform",
            return_value={
                "response": {
                    "evaluations": [{"name": f"{mock_app_name}/evaluations/e1"}]
                }
            },
        ),
        patch("cxas_scrapi.core.evaluations.Evaluations") as mock_eval_cls,
        patch("cxas_scrapi.utils.eval_utils.EvalUtils") as mock_utils_cls,
    ):
        mock_eval_inst = mock_eval_cls.return_value
        mock_op = MagicMock()
        mock_op.name = f"{mock_app_name}/evaluationRuns/run-99"
        mock_eval_inst.run_evaluation.return_value = mock_op

        # Use a real EvalUtils instance for dataframe conversion
        real_utils = EvalUtils(app_name=mock_app_name)
        mock_utils_inst = mock_utils_cls.return_value
        mock_utils_inst.wait_for_run_and_get_results.return_value = (
            mock_raw_results
        )
        with (
            patch.object(real_utils, "get_evaluation") as mock_get_eval,
            patch.object(real_utils, "list_evaluations", return_value=[]),
        ):
            mock_ev_obj = MagicMock()
            mock_ev_obj.display_name = "Scenario_A"
            mock_get_eval.return_value = mock_ev_obj
            mock_utils_inst.evals_to_dataframe.side_effect = (
                real_utils.evals_to_dataframe
            )

            sim_results = sim.run_simulations_on_platform(test_cases, runs=1)

    assert len(sim_results) == 2
    res_a = next(r for r in sim_results if r["name"] == "Scenario_A")
    res_missing = next(
        r for r in sim_results if r["name"] == "Scenario_Missing"
    )

    assert res_a["passed"] is True
    assert res_a["status"] == "PASS"
    assert res_a["goals"] == "1/1"
    assert res_a["expectations"] == "1/1"
    assert res_a["session_id"] == "conv-abc"
    assert res_a["step_details"][0]["status"] == "Completed"
    assert res_a["step_details"][0]["success_criteria"] == "Verified"
    assert res_a["expectation_details"][0]["status"] == "Met"

    assert res_missing["passed"] is False
    assert res_missing["status"] == "FAIL"
    assert res_missing["goals"] == "0/0"


def test_convert_sim_evals_directory_merges_file_common_fields(
    tmp_path: Path,
) -> None:
    file1 = tmp_path / "01_suite.yaml"
    file1.write_text(
        yaml.safe_dump(
            {
                "common_session_parameters": {"region": "US", "tier": "gold"},
                "common_expectations": ["Always greet warmly"],
                "initial_event": {"event": "SESSION_START"},
                "evals": [
                    {
                        "name": "Case_1",
                        "session_parameters": {"tier": "platinum"},
                        "expectations": ["Confirm order ID"],
                        "steps": [
                            {
                                "goal": "Check order",
                                "success_criteria": "Order status shared",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    file2 = tmp_path / "02_suite.yaml"
    file2.write_text(
        yaml.safe_dump(
            {
                "common_session_parameters": {"region": "EU"},
                "evals": [
                    {
                        "name": "Case_2",
                        "steps": [{"goal": "Ask refund"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    yaml_str = EvalUtils.convert_sim_evals_to_milestone_yaml(str(tmp_path))
    parsed = yaml.safe_load(yaml_str)

    assert len(parsed["evals"]) == 2
    case1 = parsed["evals"][0]
    assert case1["name"] == "Case_1"
    assert case1["session_parameters"] == {"region": "US", "tier": "platinum"}
    assert case1["expectations"] == ["Always greet warmly", "Confirm order ID"]
    assert case1["initial_event"] == {"event": "SESSION_START"}

    case2 = parsed["evals"][1]
    assert case2["name"] == "Case_2"
    assert case2["session_parameters"] == {"region": "EU"}


def test_sanitize_milestone_dict_expectations_and_metadata() -> None:
    raw_data = {
        "common_expectations": [
            {"title": "Tone", "expectation": "Remain professional"},
        ],
        "evals": [
            {
                "name": "Dict_Exp_Case",
                "metadata": {"owner": "team-a"},
                "expectations": [
                    {"title": "Politeness", "expectation": "Say thank you"},
                    {"prompt": "Never reveal internal IDs"},
                    "Plain string expectation",
                ],
                "steps": [
                    {
                        "goal": "Ask question",
                        "success_criteria": "Answered",
                        "protocol": "Speak concisely",
                        "max_turn_count": 3,
                        "metadata": {"step_tag": "s1"},
                    }
                ],
            }
        ],
    }

    sanitized = EvalUtils.sanitize_milestone_eval_dict(raw_data)
    assert sanitized["common_expectations"] == ["Tone: Remain professional"]
    ev = sanitized["evals"][0]
    assert ev["metadata"] == {"owner": "team-a"}
    assert ev["expectations"] == [
        "Politeness: Say thank you",
        "Never reveal internal IDs",
        "Plain string expectation",
    ]
    step = ev["steps"][0]
    assert step["response_guide"] == "Speak concisely"
    assert step["max_turns"] == 3
    assert step["metadata"] == {"step_tag": "s1"}


def test_import_evaluations_raises_on_response_error_messages(
    mock_app_name: str,
) -> None:
    evals = Evaluations(app_name=mock_app_name, creds=MagicMock())
    yaml_str = yaml.safe_dump(
        {
            "evals": [
                {
                    "name": "Bad_Case",
                    "steps": [{"goal": "G1", "success_criteria": "S1"}],
                }
            ]
        }
    )

    mock_session = MagicMock()
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {
        "name": "projects/p/locations/l/operations/op-err",
        "done": True,
        "response": {
            "importFailureCount": 1,
            "errorMessages": ["Invalid milestone schema at line 2"],
        },
    }
    mock_session.post.return_value = mock_post_resp

    with (
        patch.object(
            evals,
            "_get_rest_session",
            return_value=mock_session,
        ),
        patch.object(
            evals,
            "_get_rest_headers",
            return_value={"Content-Type": "application/json"},
        ),
        pytest.raises(RuntimeError, match="Invalid milestone schema at line 2"),
    ):
        evals.import_evaluations(
            app_name=mock_app_name,
            yaml_content=yaml_str,
        )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_run_simulations_on_platform_error_and_lro_rest_fallback(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    sim = SimulationEvals(app_name=mock_app_name)
    test_cases = [
        {
            "name": "Scenario_Err",
            "steps": [{"goal": "Say hi", "success_criteria": "Agent replies"}],
        }
    ]
    mock_raw_results = [
        {
            "name": f"{mock_app_name}/evaluations/ev-err/results/res-err",
            "evaluationStatus": "FAIL",
            "executionState": "ERROR",
            "errorInfo": {"errorMessage": "Agent runtime crashed"},
            "scenarioResult": {
                "conversation": f"{mock_app_name}/conversations/conv-err",
                "milestoneResults": [
                    {
                        "milestone": {
                            "goal": "Say hi",
                            "successCriteria": "Agent replies",
                        },
                        "status": "NOT_STARTED",
                        "explanation": "Did not start",
                    }
                ],
            },
        }
    ]

    with (
        patch.object(
            sim,
            "push_to_platform",
            return_value={"response": {"evaluations": []}},
        ),
        patch("cxas_scrapi.core.evaluations.Evaluations") as mock_eval_cls,
        patch("cxas_scrapi.utils.eval_utils.EvalUtils") as mock_utils_cls,
    ):
        mock_eval_inst = mock_eval_cls.return_value
        mock_op = MagicMock()
        mock_op.metadata = None
        mock_op.name = (
            "projects/test-proj/locations/us-central1/operations/op-1"
        )
        mock_op._refresh.side_effect = RuntimeError("gRPC refresh failed")
        mock_eval_inst.run_evaluation.return_value = mock_op
        mock_eval_inst.get_operation_rest.return_value = {
            "metadata": {"evaluationRun": f"{mock_app_name}/evaluationRuns/r-1"}
        }

        real_utils = EvalUtils(app_name=mock_app_name)
        mock_utils_inst = mock_utils_cls.return_value
        mock_utils_inst.wait_for_run_and_get_results.return_value = (
            mock_raw_results
        )
        with (
            patch.object(real_utils, "get_evaluation") as mock_get_eval,
            patch.object(real_utils, "list_evaluations", return_value=[]),
        ):
            mock_get_eval.return_value = {"displayName": "Scenario_Err"}
            mock_utils_inst.evals_to_dataframe.side_effect = (
                real_utils.evals_to_dataframe
            )
            with patch("time.sleep"):
                sim_results = sim.run_simulations_on_platform(
                    test_cases, runs=1
                )

    assert len(sim_results) == 1
    res = sim_results[0]
    assert res["passed"] is False
    assert res["error"] == "Agent runtime crashed"
    assert res["step_details"][0]["success_criteria"] == "Agent replies"


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_run_simulations_respects_run_level_modality_and_fakes(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    sim = SimulationEvals(app_name=mock_app_name)
    test_cases = [
        {
            "name": "Text_Looking_Case",
            "steps": [{"goal": "Check balance", "success_criteria": "Done"}],
        }
    ]

    with (
        patch.object(sim, "run_simulations_on_platform") as mock_plat,
        patch.object(
            sim,
            "_aggregate_simulation_results",
            return_value=[{"name": "Text_Looking_Case", "passed": True}],
        ) as mock_local,
    ):
        results = sim.run_simulations(
            test_cases=test_cases,
            runs=1,
            parallel=1,
            modality="audio",
            run_on_platform=True,
        )

    mock_plat.assert_not_called()
    mock_local.assert_called_once()
    assert len(results) == 1


def test_wait_for_evaluation_completion_rest_fallback_for_milestones(
    mock_app_name: str,
) -> None:
    real_utils = EvalUtils(app_name=mock_app_name)
    res_id = f"{mock_app_name}/evaluations/ev-1/results/res-1"

    initial_df = {
        "summary": pd.DataFrame(
            [
                {
                    "eval_result_id": res_id,
                    "execution_state": "COMPLETED",
                    "display_name": "Scenario_CLI",
                }
            ]
        )
    }
    rest_result = {
        "name": res_id,
        "evaluationStatus": "PASS",
        "executionState": "COMPLETED",
        "scenarioResult": {
            "milestoneResults": [
                {
                    "milestone": {
                        "goal": "Step 1",
                        "successCriteria": "Done",
                    },
                    "status": "COMPLETED",
                    "explanation": "Completed on platform",
                }
            ]
        },
    }

    with (
        patch.object(
            real_utils,
            "evals_to_dataframe",
            side_effect=[
                initial_df,
                initial_df,
            ],
        ) as mock_to_df,
        patch.object(
            real_utils.eval_client,
            "get_evaluation_result",
            return_value={"name": res_id, "scenarioResult": {}},
        ),
        patch.object(
            real_utils.eval_client,
            "get_evaluation_result_rest",
            return_value=rest_result,
        ) as mock_get_rest,
    ):
        wait_for_evaluation_completion(
            eval_utils=real_utils,
            old_result_ids=[],
            app_name=mock_app_name,
            expected_count=1,
            timeout_seconds=10,
        )
        mock_get_rest.assert_called_once_with(res_id)
        assert mock_to_df.call_count == 2
        assert mock_to_df.call_args_list[1].kwargs["results"] == [rest_result]


def test_generate_combined_report_from_dir_forwards_platform_sims(
    mock_app_name: str, tmp_path: Path
) -> None:
    """Verify generate_combined_report_from_dir(run=True, platform_sims=True) forwards platform_sims through reporting.run_all_evals to evals_runner.run_all_evals."""
    mock_run_results = {
        "simulation": [
            {
                "name": "platform_case",
                "passed": True,
                "status": "PASS",
                "goals": "1/1",
                "expectations": "0/0",
                "duration_s": 1.0,
                "run": 1,
                "runs": 1,
                "platform": True,
            }
        ],
        "golden": [],
        "tool": [],
        "callback": [],
    }
    out_file = str(tmp_path / "combined_report.html")

    with (
        patch(
            "cxas_scrapi.evals.runner.run_all_evals",
            return_value=mock_run_results,
        ) as mock_runner,
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_html_report",
            return_value=out_file,
        ) as mock_html,
    ):
        res_path = reporting.generate_combined_report_from_dir(
            output_dir=str(tmp_path),
            app_name=mock_app_name,
            run=True,
            include=["sims"],
            platform_sims=True,
        )

    assert res_path == out_file
    mock_runner.assert_called_once()
    assert mock_runner.call_args.kwargs.get("platform_sims") is True
    mock_html.assert_called_once()


def test_scenario_milestones_detection_and_sanitization() -> None:
    """Verify scenario.milestones dicts are detected and sanitized into evals YAML format."""
    raw_scenario_dict = {
        "display_name": "Order_Milestone_Scenario",
        "tags": ["P0"],
        "scenario": {
            "session_parameters": {"order_id": "12345"},
            "milestones": [
                {
                    "goal": "Ask order status",
                    "successCriteria": "Agent provides status",
                    "responseGuide": "Be polite",
                    "maxTurnCount": 4,
                }
            ],
        },
    }
    assert EvalUtils.is_milestone_eval_data(raw_scenario_dict)
    sanitized = EvalUtils.sanitize_milestone_eval_dict(raw_scenario_dict)
    assert len(sanitized["evals"]) == 1
    ev = sanitized["evals"][0]
    assert ev["name"] == "Order_Milestone_Scenario"
    assert ev["session_parameters"] == {"order_id": "12345"}
    assert ev["steps"][0] == {
        "goal": "Ask order status",
        "success_criteria": "Agent provides status",
        "response_guide": "Be polite",
        "max_turns": 4,
    }


def test_cli_push_eval_directory_support(
    mock_app_name: str, tmp_path: Path
) -> None:
    """Verify push_eval supports a directory path containing Milestone YAML files."""
    sim_file = tmp_path / "suite.yaml"
    sim_file.write_text(
        yaml.safe_dump(
            {
                "evals": [
                    {
                        "name": "dir_case",
                        "steps": [
                            {"goal": "Say hello", "success_criteria": "Greeted"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(app_name=mock_app_name, file=str(tmp_path))

    with patch(
        "cxas_scrapi.utils.eval_utils.EvalUtils.push_milestone_evals_from_yaml"
    ) as mock_push:
        push_eval(args)
        mock_push.assert_called_once_with(
            source=str(tmp_path),
            app_name=mock_app_name,
            conflict_strategy="OVERWRITE",
        )


def test_run_simulations_docstring_present() -> None:
    """Verify SimulationEvals.run_simulations has a valid Python docstring."""
    assert SimulationEvals.run_simulations.__doc__ is not None
    assert "run_on_platform" in SimulationEvals.run_simulations.__doc__


def test_sanitize_milestone_eval_dict_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that sanitize_milestone_eval_dict logs warnings for stripped fields and hoisted inject_variables."""
    raw_data = {
        "evals": [
            {
                "name": "warning_eval",
                "audio_analysis": True,
                "voice_config": {"voice": "alloy"},
                "steps": [
                    {
                        "goal": "Step 0",
                        "inject_variables": {"v0": "val0"},
                    },
                    {
                        "goal": "Step 1",
                        "barge_in_after_ms": 100,
                        "inject_variables": {"v1": "val1"},
                    },
                ],
            }
        ]
    }
    with caplog.at_level("WARNING"):
        sanitized = EvalUtils.sanitize_milestone_eval_dict(raw_data)

    assert "Stripping unsupported client-only field(s)" in caplog.text
    assert "audio_analysis" in caplog.text
    assert "voice_config" in caplog.text
    assert "barge_in_after_ms" in caplog.text
    assert (
        "Hoisting inject_variables from step 1 to session_parameters"
        in caplog.text
    )
    assert sanitized["evals"][0]["session_parameters"]["v1"] == "val1"


def test_evaluations_run_evaluation_tool_fakes(mock_app_name: str) -> None:
    """Test Evaluations.run_evaluation and run_evaluation_rest with use_tool_fakes."""
    eval_client = Evaluations(app_name=mock_app_name)
    eval_client.client = MagicMock()
    eval_id = f"{mock_app_name}/evaluations/ev-1"

    eval_client.run_evaluation(
        evaluations=[eval_id],
        use_tool_fakes=True,
    )
    call_args = eval_client.client.run_evaluation.call_args[1]
    req = call_args["request"]
    assert (
        req.config.tool_call_behaviour == types.EvaluationToolCallBehaviour.FAKE
    )

    eval_client.run_evaluation(
        evaluations=[eval_id],
        use_tool_fakes=False,
    )
    call_args = eval_client.client.run_evaluation.call_args[1]
    req = call_args["request"]
    assert (
        req.config.tool_call_behaviour == types.EvaluationToolCallBehaviour.REAL
    )

    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"name": "op1"}
    with patch.object(
        eval_client, "_get_rest_session", return_value=mock_session
    ):
        eval_client.run_evaluation_rest(
            evaluations=[eval_id],
            use_tool_fakes=True,
        )
        post_kwargs = mock_session.post.call_args[1]
        assert post_kwargs["json"]["config"]["toolCallBehaviour"] == "FAKE"

        eval_client.run_evaluation_rest(
            evaluations=[eval_id],
            use_tool_fakes=False,
        )
        post_kwargs = mock_session.post.call_args[1]
        assert post_kwargs["json"]["config"]["toolCallBehaviour"] == "REAL"

        eval_client.run_evaluation_rest(
            evaluations=[eval_id],
            use_tool_fakes=None,
        )
        post_kwargs = mock_session.post.call_args[1]
        assert "toolCallBehaviour" not in post_kwargs["json"]["config"]


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_simulation_evals_run_simulations_platform_tool_fakes(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    """Test SimulationEvals.run_simulations with platform routing and tool fakes."""
    sim = SimulationEvals(app_name=mock_app_name)
    tc1 = {
        "name": "case_fakes",
        "use_tool_fakes": True,
        "steps": [{"goal": "Goal 1"}],
    }
    tc2 = {
        "name": "case_normal",
        "steps": [{"goal": "Goal 2"}],
    }

    with patch.object(
        sim, "run_simulations_on_platform", return_value=[]
    ) as mock_plat:
        sim.run_simulations(
            test_cases=[tc2],
            runs=1,
            run_on_platform=True,
            use_tool_fakes=True,
        )
        mock_plat.assert_called_once_with(
            [tc2],
            runs=1,
            timeout_seconds=600,
            use_tool_fakes=True,
        )

    with patch.object(
        sim, "run_simulations_on_platform", return_value=[]
    ) as mock_plat:
        sim.run_simulations(
            test_cases=[tc1, tc2],
            runs=1,
            run_on_platform=True,
        )
        assert mock_plat.call_count == 2
        mock_plat.assert_any_call(
            [tc1],
            runs=1,
            timeout_seconds=600,
            use_tool_fakes=True,
        )
        mock_plat.assert_any_call(
            [tc2],
            runs=1,
            timeout_seconds=600,
        )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.Tools")
@patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate")
def test_simulation_evals_run_level_audio_routing(
    mock_genai: Any,
    mock_tools: Any,
    mock_sessions: Any,
    mock_app_name: str,
) -> None:
    """Test that run-level audio parameters force local client execution."""
    sim = SimulationEvals(app_name=mock_app_name)
    tc = {
        "name": "text_case",
        "steps": [{"goal": "Goal 1"}],
    }

    for kwarg in [
        {"audio_analysis": True},
        {"capture_agent_audio": True},
        {"background_noise_file": "cafe.wav"},
        {"burst_noise_files": ["click.wav"]},
    ]:
        with (
            patch.object(sim, "run_simulations_on_platform") as mock_plat,
            patch.object(
                sim,
                "_aggregate_simulation_results",
                return_value=[{"name": "text_case", "passed": True}],
            ) as mock_local,
        ):
            sim.run_simulations(
                test_cases=[tc],
                runs=1,
                run_on_platform=True,
                **kwarg,
            )
            mock_plat.assert_not_called()
            mock_local.assert_called_once()
