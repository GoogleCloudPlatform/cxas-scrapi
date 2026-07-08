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

from cxas_scrapi.core.analysis_rules import AnalysisRules


@pytest.fixture
def mock_google_auth():
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.Session.request")
def test_create_rule_for_app(mock_request, mock_google_auth):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "name": "projects/p/locations/l/analysisRules/ar1",
        "displayName": "Rule Bella Notte",
        "active": True,
    }
    mock_request.return_value = mock_resp

    client = AnalysisRules(project_id="p", location="l")
    rule = client.create_rule_for_app(
        display_name="Rule Bella Notte",
        app_name="bella_notte",
        scorecard_revisions=[
            "projects/p/locations/l/qaScorecards/sc1/revisions/latest"
        ],
        issue_models=["projects/p/locations/l/issueModels/im1"],
    )

    assert rule["name"] == "projects/p/locations/l/analysisRules/ar1"
    mock_request.assert_called_once()
    call_kwargs = mock_request.call_args[1]
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["json"]["displayName"] == "Rule Bella Notte"
    conv_filter = call_kwargs["json"]["conversationFilter"]
    assert conv_filter == 'agent_id="bella_notte"'
    selector = call_kwargs["json"]["annotatorSelector"]
    assert selector["runSummarizationAnnotator"] is True
    assert selector["runSentimentAnnotator"] is True
    assert selector["runIssueModelAnnotator"] is True
    assert selector["runQaAnnotator"] is True
    assert selector["qaConfig"]["scorecardList"]["qaScorecardRevisions"] == [
        "projects/p/locations/l/qaScorecards/sc1/revisions/latest"
    ]
    assert selector["issueModels"] == ["projects/p/locations/l/issueModels/im1"]


@patch("requests.Session.request")
def test_activate_and_delete_rule(mock_request, mock_google_auth):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"active": False}
    mock_request.return_value = mock_resp

    client = AnalysisRules(project_id="p", location="l")
    res = client.activate_analysis_rule("ar1", active=False)
    assert res["active"] is False

    client.delete_analysis_rule("ar1")
    assert mock_request.call_count == 2
