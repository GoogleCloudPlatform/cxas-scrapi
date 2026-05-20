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

from cxas_scrapi.core.insights import Insights


@pytest.fixture
def mock_google_auth():
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.request")
def test_list_conversations(mock_request, mock_google_auth):
    """Test Insights.list_conversations."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "conversations": [
            {"name": "projects/p/locations/l/conversations/c1", "labels": {}}
        ],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    res = client.list_conversations(filter_str="some_filter")

    assert len(res) == 1
    assert res[0]["name"] == "projects/p/locations/l/conversations/c1"

    # Verify API was called correctly
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/conversations"
    )
    assert called_args[1]["params"]["filter"] == "some_filter"


@patch("requests.request")
def test_get_conversation(mock_request, mock_google_auth):
    """Test Insights.get_conversation."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/conversations/c1"
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")

    # Test with ID only
    res1 = client.get_conversation("c1")
    assert res1["name"] == "projects/p/locations/l/conversations/c1"
    mock_request.assert_called_with(
        method="GET",
        url="https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/conversations/c1",
        headers={
            "Authorization": "Bearer mock_token_for_tests",
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-user-project": "p",
            "User-Agent": client.user_agent,
        },
        json=None,
        params=None,
        timeout=60.0,
    )

    res2 = client.get_conversation("projects/p/locations/l/conversations/c2")
    assert res2["name"] == "projects/p/locations/l/conversations/c1"
    mock_request.assert_called_with(
        method="GET",
        url="https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/conversations/c2",
        headers={
            "Authorization": "Bearer mock_token_for_tests",
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-user-project": "p",
            "User-Agent": client.user_agent,
        },
        json=None,
        params=None,
        timeout=60.0,
    )
