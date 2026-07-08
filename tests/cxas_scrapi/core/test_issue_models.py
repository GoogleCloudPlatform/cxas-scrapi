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

from cxas_scrapi.core.issue_models import IssueModels


@pytest.fixture
def mock_google_auth():
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.Session.request")
def test_create_topic_model_for_app(mock_request, mock_google_auth):
    mock_resp_create = MagicMock()
    mock_resp_create.status_code = 200
    mock_resp_create.json.return_value = {
        "name": "projects/p/locations/l/issueModels/im1",
        "displayName": "Topic Model Bella Notte",
    }

    mock_resp_deploy = MagicMock()
    mock_resp_deploy.status_code = 200
    mock_resp_deploy.json.return_value = {
        "name": "projects/p/locations/l/issueModels/im1/operations/op1"
    }

    mock_request.side_effect = [mock_resp_create, mock_resp_deploy]

    client = IssueModels(project_id="p", location="l")
    model = client.create_topic_model_for_app(
        display_name="Topic Model Bella Notte",
        app_name="bella_notte",
        deploy=True,
    )

    assert model["name"] == "projects/p/locations/l/issueModels/im1"
    assert mock_request.call_count == 2

    create_call = mock_request.call_args_list[0][1]
    assert create_call["method"] == "POST"
    assert "issueModels" in create_call["url"]
    assert create_call["json"]["displayName"] == "Topic Model Bella Notte"
    filter_val = create_call["json"]["inputDataConfig"]["filter"]
    assert filter_val == 'labels.cxas_app="bella_notte"'

    deploy_call = mock_request.call_args_list[1][1]
    assert deploy_call["method"] == "POST"
    assert deploy_call["url"].endswith(":deploy")


@patch("requests.Session.request")
def test_list_issues_and_stats(mock_request, mock_google_auth):
    mock_resp_stats = MagicMock()
    mock_resp_stats.status_code = 200
    mock_resp_stats.json.return_value = {"issueCount": 12}

    mock_resp_issues = MagicMock()
    mock_resp_issues.status_code = 200
    mock_resp_issues.json.return_value = {
        "issues": [
            {"name": "projects/p/locations/l/issueModels/im1/issues/iss1"}
        ],
        "nextPageToken": None,
    }

    mock_request.side_effect = [mock_resp_stats, mock_resp_issues]

    client = IssueModels(project_id="p", location="l")
    stats = client.calculate_issue_model_stats("im1")
    issues = client.list_issues("im1")

    assert stats["issueCount"] == 12
    assert len(issues) == 1
    iss_name = issues[0]["name"]
    assert iss_name == "projects/p/locations/l/issueModels/im1/issues/iss1"
