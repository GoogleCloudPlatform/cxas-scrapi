"""Unit tests for MetricsExtractor."""

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

import pandas as pd
import pytest

from cxas_scrapi.utils.metrics_extractor import MetricsExtractor


@pytest.fixture
def mock_insights_client() -> typing.Any:
    return MagicMock()


@pytest.fixture
def sample_conversations() -> list[dict[str, typing.Any]]:
    return [
        {
            "name": "projects/test-proj/locations/us-central1/conversations/conv-1",
            "latestAnalysis": {
                "name": "projects/test-proj/locations/us-central1/conversations/conv-1/analyses/123",
                "analysisResult": {
                    "callAnalysisMetadata": {
                        "qaScorecardResults": [
                            {
                                "qaScorecardRevision": "projects/test-proj/locations/us-central1/qaScorecards/sc-csat/revisions/rev-1",
                                "qaAnswers": [
                                    {
                                        "qaQuestion": "projects/test-proj/locations/us-central1/qaScorecards/sc-csat/revisions/rev-1/qaQuestions/q1",
                                        "questionBody": "Did the agent greet the customer?",
                                        "answerValue": {
                                            "strValue": "Yes",
                                            "score": 1.0,
                                            "potentialScore": 1.0,
                                            "normalizedScore": 1.0,
                                        },
                                        "tags": ["greeting"],
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
        },
        {
            "name": "projects/test-proj/locations/us-central1/conversations/conv-2",
            "latestAnalysis": {
                "name": "projects/test-proj/locations/us-central1/conversations/conv-2/analyses/456",
                "analysisResult": {
                    "callAnalysisMetadata": {"qaScorecardResults": []}
                },
            },
        },
    ]


def test_get_evaluation_results(
    mock_insights_client: typing.Any,
    sample_conversations: list[dict[str, typing.Any]],
) -> None:
    mock_insights_client.list_conversations.return_value = sample_conversations
    extractor = MetricsExtractor(mock_insights_client)

    df = extractor.get_evaluation_results()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    row = df.iloc[0]
    assert (
        row["conversation_name"]
        == "projects/test-proj/locations/us-central1/conversations/conv-1"
    )
    assert (
        row["scorecard_revision"]
        == "projects/test-proj/locations/us-central1/qaScorecards/sc-csat/revisions/rev-1"
    )
    assert row["answer_value"] == "Yes"
    assert row["score"] == 1.0


def test_get_evaluation_results_filter_scorecard(
    mock_insights_client: typing.Any,
    sample_conversations: list[dict[str, typing.Any]],
) -> None:
    mock_insights_client.list_conversations.return_value = sample_conversations
    extractor = MetricsExtractor(mock_insights_client)

    df = extractor.get_evaluation_results(
        scorecard_names=[
            "projects/test-proj/locations/us-central1/qaScorecards/other-sc"
        ]
    )
    assert df.empty


def test_get_missing_conversations(
    mock_insights_client: typing.Any,
    sample_conversations: list[dict[str, typing.Any]],
) -> None:
    mock_insights_client.list_conversations.return_value = sample_conversations
    extractor = MetricsExtractor(mock_insights_client)

    missing = extractor.get_missing_conversations(
        filter_str="",
        target_scorecard="projects/test-proj/locations/us-central1/qaScorecards/sc-csat/revisions/rev-1",
    )

    assert len(missing) == 1
    assert (
        missing[0]
        == "projects/test-proj/locations/us-central1/conversations/conv-2"
    )
