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

from cxas_scrapi.core.scorecards import Scorecards


@pytest.fixture
def mock_google_auth() -> typing.Any:
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.Session.request")
def test_create_scorecard_with_questions(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    mock_sc = MagicMock()
    mock_sc.status_code = 200
    mock_sc.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1"
    }

    mock_rev = MagicMock()
    mock_rev.status_code = 200
    mock_rev.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
    }

    mock_q = MagicMock()
    mock_q.status_code = 200
    mock_q.json.return_value = {
        "name": (
            "projects/p/locations/l/qaScorecards/sc1/revisions/r1/qaQuestions/q1"
        )
    }

    mock_request.side_effect = [mock_sc, mock_rev, mock_q]

    client = Scorecards(project_id="p", location="l")
    rev, questions = client.create_scorecard_with_questions(
        scorecard_id="sc1",
        display_name="Test SC",
        description="Test Desc",
        questions=[{"questionBody": "Q1"}],
    )

    assert rev["name"] == "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
    assert len(questions) == 1
    q_name = questions[0]["name"]
    assert q_name.endswith("/qaQuestions/q1")


@patch("requests.Session.request")
def test_delete_scorecard(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_request.return_value = mock_resp

    client = Scorecards(project_id="p", location="l")
    client.delete_scorecard("sc1")
    mock_request.assert_called_once()
    call_kwargs = mock_request.call_args[1]
    assert call_kwargs["method"] == "DELETE"
    assert call_kwargs["url"].endswith("/qaScorecards/sc1")


@patch("requests.Session.request")
def test_tune_and_deploy_revision(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {"name": "operations/op1"}

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {"name": "rev_name", "state": "READY"}

    mock_request.side_effect = [mock_resp1, mock_resp2]

    client = Scorecards(project_id="p", location="l")
    op = client.tune_revision("rev_name")
    assert op["name"] == "operations/op1"

    dep = client.deploy_revision("rev_name")
    assert dep["state"] == "READY"


@patch("requests.Session.request")
def test_activate_revision(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    mock_get1 = MagicMock()
    mock_get1.status_code = 200
    mock_get1.json.return_value = {"name": "rev_name", "state": "EDITABLE"}

    mock_tune = MagicMock()
    mock_tune.status_code = 200
    mock_tune.json.return_value = {"name": "operations/op1"}

    mock_get2 = MagicMock()
    mock_get2.status_code = 200
    mock_get2.json.return_value = {"name": "rev_name", "state": "TRAINING"}

    client = Scorecards(project_id="p", location="l")
    mock_request.side_effect = [mock_get1, mock_tune, mock_get2]

    res = client.activate_revision("rev_name", wait_for_ready=False)
    assert res["state"] == "TRAINING"


def test_sanitize_question(mock_google_auth: typing.Any) -> None:
    client = Scorecards(project_id="p", location="l")
    raw_question = {
        "name": "projects/p/locations/l/qaScorecards/sc1/revisions/r1/qaQuestions/q1",
        "createTime": "2026-08-20T00:00:00Z",
        "metrics": {"some": "metric"},
        "questionBody": "Did agent understand user?",
        "abbreviation": "intent_understanding",
        "answerChoices": [
            {"key": "yes", "body": "Agent understood.", "score": 1.0},
            {"key": "no", "strValue": "Agent failed.", "score": 0.0},
            {"key": "na", "score": 0.0},
        ],
    }
    sanitized = client._sanitize_question(raw_question)
    assert "name" not in sanitized
    assert "createTime" not in sanitized
    assert "metrics" not in sanitized
    assert sanitized["questionBody"] == "Did agent understand user?"
    assert len(sanitized["answerChoices"]) == 3
    assert sanitized["answerChoices"][0]["strValue"] == "Agent understood."
    assert sanitized["answerChoices"][1]["strValue"] == "Agent failed."
    assert sanitized["answerChoices"][2]["naValue"] is True
