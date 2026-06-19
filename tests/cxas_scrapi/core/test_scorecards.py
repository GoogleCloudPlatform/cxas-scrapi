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

from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.scorecards import Scorecards


@pytest.fixture
def mock_google_auth():
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.request")
def test_list_scorecards(mock_request, mock_google_auth):
    """Test Scorecards.list_scorecards."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "qaScorecards": [{"name": "projects/p/locations/l/qaScorecards/sc1"}],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    res = client.list_scorecards()

    assert len(res) == 1
    assert res[0]["name"] == "projects/p/locations/l/qaScorecards/sc1"

    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards"
    )


@patch("requests.request")
def test_get_scorecard(mock_request, mock_google_auth):
    """Test Scorecards.get_scorecard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1"
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    res = client.get_scorecard("projects/p/locations/l/qaScorecards/sc1")

    assert res["name"] == "projects/p/locations/l/qaScorecards/sc1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1"
    )


@patch("requests.request")
def test_create_scorecard(mock_request, mock_google_auth):
    """Test Scorecards.create_scorecard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1"
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    scorecard = {"displayName": "My Scorecard"}
    res = client.create_scorecard("sc1", scorecard)

    assert res["name"] == "projects/p/locations/l/qaScorecards/sc1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards"
    )
    assert called_args[1]["params"] == {"qaScorecardId": "sc1"}
    assert called_args[1]["json"] == scorecard


@patch("requests.request")
def test_get_latest_revision(mock_request, mock_google_auth):
    """Test Scorecards.get_latest_revision."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1"
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    res = client.get_latest_revision("projects/p/locations/l/qaScorecards/sc1")

    assert (
        res["name"] == "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1"
    )
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions/latest"
    )


@patch("requests.request")
def test_create_revision(mock_request, mock_google_auth):
    """Test Scorecards.create_revision."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/qaScorecards/sc1/revisions/rev-2"
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    res = client.create_revision("projects/p/locations/l/qaScorecards/sc1")

    assert (
        res["name"] == "projects/p/locations/l/qaScorecards/sc1/revisions/rev-2"
    )
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions"
    )
    assert called_args[1]["json"] == {}


@patch("requests.request")
def test_list_questions(mock_request, mock_google_auth):
    """Test Scorecards.list_questions."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "qaQuestions": [
            {
                "name": (
                    "projects/p/locations/l/qaScorecards/sc1/"
                    "revisions/rev-1/qaQuestions/q1"
                )
            }
        ],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    res = client.list_questions(
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1"
    )

    assert len(res) == 1
    assert res[0]["name"] == (
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )

    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions"
    )


@patch("requests.request")
def test_patch_question(mock_request, mock_google_auth):
    """Test Scorecards.patch_question."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": (
            "projects/p/locations/l/qaScorecards/sc1/"
            "revisions/rev-1/qaQuestions/q1"
        )
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    question = {"abbreviation": "Q1"}
    res = client.patch_question(
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1",
        question,
        update_mask="abbreviation",
    )

    assert res["name"] == (
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "PATCH"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )
    assert called_args[1]["params"] == {"updateMask": "abbreviation"}
    assert called_args[1]["json"] == question


@patch("requests.request")
def test_create_question(mock_request, mock_google_auth):
    """Test Scorecards.create_question."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": (
            "projects/p/locations/l/qaScorecards/sc1/"
            "revisions/rev-1/qaQuestions/q1"
        )
    }
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    question = {"questionType": "YES_NO"}
    res = client.create_question(
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1", question
    )

    assert res["name"] == (
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions"
    )
    assert called_args[1]["json"] == question


@patch("requests.request")
def test_delete_question(mock_request, mock_google_auth):
    """Test Scorecards.delete_question."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_request.return_value = mock_response

    client = Scorecards(project_id="p", location="l")
    client.delete_question(
        "projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )

    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "DELETE"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/qaScorecards/sc1/revisions/rev-1/qaQuestions/q1"
    )
