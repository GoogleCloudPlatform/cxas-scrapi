"""Unit tests for ScorecardEvalRunner."""

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

import typing
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.utils.scorecard_eval_runner import ScorecardEvalRunner


@pytest.fixture
def mock_gemini() -> typing.Any:
    return MagicMock()


@pytest.fixture
def sample_question() -> dict[str, typing.Any]:
    return {
        "questionBody": "Did the agent confirm the customer's account number?",
        "abbreviation": "confirm_account",
        "answerChoices": [
            {"key": "yes", "body": "Yes, confirmed", "score": 1.0},
            {"key": "no", "body": "No, did not confirm", "score": 0.0},
        ],
        "answerInstructions": "Check if agent asked for and repeated account number.",
    }


def test_evaluate_question_match(
    mock_gemini: typing.Any, sample_question: dict[str, typing.Any]
) -> None:
    mock_gemini.generate.return_value = {
        "answer_key": "yes",
        "rationale": "Agent confirmed account in Turn 2",
        "confidence": 0.95,
    }

    runner = ScorecardEvalRunner(gemini_client=mock_gemini)
    res = runner.evaluate_question(
        question=sample_question,
        conversation="Turn 1 [USER]: Hi\nTurn 2 [AGENT]: Can you confirm account 1234?",
        conversation_id="conv_1",
        expected_answer="yes",
    )

    assert res.predicted_answer == "yes"
    assert res.predicted_score == 1.0
    assert res.is_match is True
    assert res.rationale == "Agent confirmed account in Turn 2"


def test_evaluate_scorecard_on_calibration_set(
    mock_gemini: typing.Any, sample_question: dict[str, typing.Any]
) -> None:
    mock_gemini.generate.return_value = {
        "answer_key": "yes",
        "rationale": "Matches expected criteria",
        "confidence": 1.0,
    }

    scorecard_dict = {
        "qaScorecard": {"displayName": "Test Scorecard"},
        "qaQuestions": [sample_question],
    }
    calibration_dataset = [
        {
            "conversation_id": "case_1",
            "transcript": "Agent confirmed",
            "expected_answers": {"confirm_account": "yes"},
        },
        {
            "conversation_id": "case_2",
            "transcript": "Agent did not confirm",
            "expected_answers": {"confirm_account": "no"},
        },
    ]

    runner = ScorecardEvalRunner(gemini_client=mock_gemini)
    report = runner.evaluate_scorecard_on_calibration_set(
        scorecard_template=scorecard_dict,
        calibration_dataset=calibration_dataset,
    )

    assert report.total_conversations == 2
    assert report.total_evaluations == 2
    assert report.scorecard_display_name == "Test Scorecard"
    assert report.overall_accuracy == 0.5
    assert len(report.discrepancies) == 1
    assert report.discrepancies[0].conversation_id == "case_2"

    df = report.to_dataframe()
    assert len(df) == 2
