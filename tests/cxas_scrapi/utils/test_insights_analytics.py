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
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils.insights_analytics import (
    InsightsAnalytics,
    calculate_start_time_iso,
)


def test_calculate_start_time_iso() -> None:
    assert calculate_start_time_iso("24h") is not None
    assert calculate_start_time_iso("7d") is not None
    assert calculate_start_time_iso("all") is None
    assert calculate_start_time_iso("invalid") is None


@pytest.fixture
def mock_google_auth() -> typing.Any:
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


def test_aggregate_metrics_and_html_dashboard(
    mock_google_auth: typing.Any,
) -> None:
    analytics = InsightsAnalytics(project_id="p", location="l")

    sample_convs = [
        {
            "name": "projects/p/locations/l/conversations/c1",
            "turnCount": "4",
            "duration": "120s",
            "sentiment": {"score": 0.6},
            "issues": [
                {"issue": "projects/p/locations/l/issueModels/im1/issues/iss1"}
            ],
            "qaAnswers": [
                {
                    "qaScorecardRevision": (
                        "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
                    ),
                    "qaQuestions": [
                        {
                            "qaQuestion": "q1",
                            "score": 1.0,
                            "potentialScore": 1.0,
                            "answerValue": {"strValue": "Yes"},
                        }
                    ],
                }
            ],
        },
        {
            "name": "projects/p/locations/l/conversations/c2",
            "turnCount": "6",
            "duration": "180s",
            "sentiment": {"score": 0.4},
            "issues": [
                {"issue": "projects/p/locations/l/issueModels/im1/issues/iss1"}
            ],
            "qaAnswers": [
                {
                    "qaScorecardRevision": (
                        "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
                    ),
                    "qaQuestions": [
                        {
                            "qaQuestion": "q1",
                            "score": 0.0,
                            "potentialScore": 1.0,
                            "answerValue": {"strValue": "No"},
                        }
                    ],
                }
            ],
        },
    ]

    report = analytics.aggregate_metrics(
        time_window="24h", app_name="bella_notte", conversations=sample_convs
    )

    kpis = report["kpis"]
    assert kpis["total_conversations"] == 2
    assert kpis["average_turns"] == 5.0
    assert kpis["average_duration_seconds"] == 150.0
    assert kpis["average_user_sentiment"] == 0.5
    assert kpis["overall_scorecard_pass_percentage"] == 50.0

    assert len(report["scorecards"]) == 1
    sc = report["scorecards"][0]
    assert (
        sc["revision"] == "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
    )
    assert sc["pass_percentage"] == 50.0
    assert sc["questions"][0]["answer_distribution"] == {"Yes": 1, "No": 1}

    assert len(report["topics"]) == 1
    assert report["topics"][0]["count"] == 2

    html = analytics.generate_html_dashboard(report, title="Test Dashboard")
    assert "Test Dashboard" in html
    assert "150.0s" in html
    assert "50.0%" in html
    assert "Scorecard Evaluations & Question Breakdown" in html


def test_aggregate_metrics_latest_analysis_fallback(
    mock_google_auth: typing.Any,
) -> None:
    analytics = InsightsAnalytics(project_id="p", location="l")
    sample_convs = [
        {
            "name": "projects/p/locations/l/conversations/c1",
            "turnCount": "2",
            "duration": "60s",
            "qaAnswers": [],
            "latestAnalysis": {
                "analysisResult": {
                    "callAnalysisMetadata": {
                        "qaScorecardResults": [
                            {
                                "qaScorecardRevision": (
                                    "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
                                ),
                                "qaAnswers": [
                                    {
                                        "qaQuestion": "q1",
                                        "score": 1.0,
                                        "potentialScore": 1.0,
                                        "answerValue": {"strValue": "Yes"},
                                    }
                                ],
                            }
                        ]
                    }
                }
            },
        }
    ]

    report = analytics.aggregate_metrics(conversations=sample_convs)
    kpis = report["kpis"]
    assert kpis["total_conversations"] == 1
    assert kpis["overall_scorecard_pass_percentage"] == 100.0
    assert len(report["scorecards"]) == 1
    sc = report["scorecards"][0]
    assert (
        sc["revision"] == "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
    )
