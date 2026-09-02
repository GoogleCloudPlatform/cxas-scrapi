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

import io
import typing
from unittest.mock import MagicMock, patch

import pytest
from urllib3.response import HTTPResponse

from cxas_scrapi.core.insights import Insights


@pytest.fixture
def mock_google_auth() -> typing.Any:
    with patch("google.auth.default") as mock_auth:
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.expired = False
        mock_auth.return_value = (mock_creds, "fake_project")
        yield mock_creds


@patch("requests.Session.request")
def test_list_conversations(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
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


@patch("requests.Session.request")
def test_list_conversations_with_view(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_conversations with view parameter."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "conversations": [],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    _ = client.list_conversations(filter_str="some_filter", view="FULL")

    # Verify API was called with view param
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["params"]["view"] == "FULL"
    assert called_args[1]["params"]["filter"] == "some_filter"


@patch("requests.Session.request")
def test_get_conversation(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
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


@patch("time.sleep", return_value=None)
@patch("urllib3.connectionpool.HTTPConnectionPool._make_request")
def test_insights_request_retry_on_failure(
    mock_make_request: typing.Any,
    mock_sleep: typing.Any,
    mock_google_auth: typing.Any,
) -> None:
    """Test that Insights client retries on transient errors."""
    mock_resp1 = HTTPResponse(
        body=io.BytesIO(b"Service Unavailable"),
        status=503,
        preload_content=False,
    )
    mock_resp2 = HTTPResponse(
        body=io.BytesIO(b"Too Many Requests"),
        status=429,
        preload_content=False,
    )
    body_data = b'{"name": "test-resource"}'
    mock_resp3 = HTTPResponse(
        body=io.BytesIO(body_data),
        headers={"Content-Length": str(len(body_data))},
        status=200,
        preload_content=False,
    )

    mock_make_request.side_effect = [mock_resp1, mock_resp2, mock_resp3]

    client = Insights(project_id="p", location="l")
    res = client.get_conversation("c1")

    assert res == {"name": "test-resource"}
    assert mock_make_request.call_count == 3


@patch("requests.Session.request")
def test_list_autolabeling_rules(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_autolabeling_rules."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "autoLabelingRules": [
            {
                "name": "projects/p/locations/l/autoLabelingRules/r1",
                "displayName": "Rule 1",
            }
        ],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    rules = client.list_autolabeling_rules()

    assert len(rules) == 1
    assert rules[0]["displayName"] == "Rule 1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/autoLabelingRules"
    )


@patch("requests.Session.request")
def test_get_autolabeling_rule(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.get_autolabeling_rule."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/autoLabelingRules/r1",
        "displayName": "Rule 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")

    # With relative ID
    res1 = client.get_autolabeling_rule("r1")
    assert res1["displayName"] == "Rule 1"
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/autoLabelingRules/r1"
    )

    # With full resource name
    res2 = client.get_autolabeling_rule(
        "projects/p/locations/l/autoLabelingRules/r1"
    )
    assert res2["displayName"] == "Rule 1"


@patch("requests.Session.request")
def test_create_autolabeling_rule(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.create_autolabeling_rule."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/autoLabelingRules/r1",
        "displayName": "Rule 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {
        "displayName": "Rule 1",
        "labelKey": "category",
        "conditions": [{"condition": "", "value": "'default'"}],
    }
    rule = client.create_autolabeling_rule(payload, auto_labeling_rule_id="r1")

    assert rule["name"] == "projects/p/locations/l/autoLabelingRules/r1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/autoLabelingRules"
    )
    assert called_args[1]["params"] == {"autoLabelingRuleId": "r1"}
    assert called_args[1]["json"]["labelKey"] == "category"


@patch("requests.Session.request")
def test_update_autolabeling_rule(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.update_autolabeling_rule."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/autoLabelingRules/r1",
        "displayName": "Updated Rule",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {"displayName": "Updated Rule"}

    # With string mask
    res1 = client.update_autolabeling_rule(
        "r1", payload, update_mask="displayName"
    )
    assert res1["displayName"] == "Updated Rule"
    assert mock_request.call_args[1]["params"] == {"updateMask": "displayName"}

    # With list mask
    res2 = client.update_autolabeling_rule(
        "projects/p/locations/l/autoLabelingRules/r1",
        payload,
        update_mask=["displayName", "active"],
    )
    assert res2["displayName"] == "Updated Rule"
    assert mock_request.call_args[1]["params"] == {
        "updateMask": "displayName,active"
    }


@patch("requests.Session.request")
def test_delete_autolabeling_rule(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.delete_autolabeling_rule."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    client.delete_autolabeling_rule("r1")

    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "DELETE"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/autoLabelingRules/r1"
    )


# --- Dashboard Tests ---


@patch("requests.Session.request")
def test_list_dashboards(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_dashboards."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "dashboards": [
            {
                "name": "projects/p/locations/l/dashboards/d1",
                "displayName": "Dashboard 1",
            }
        ],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    res = client.list_dashboards(filter_str="filter_val")

    assert len(res) == 1
    assert res[0]["name"] == "projects/p/locations/l/dashboards/d1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards"
    )
    assert called_args[1]["params"]["filter"] == "filter_val"


@patch("requests.Session.request")
def test_list_dashboards_pagination(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_dashboards with multiple pages."""
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {
        "dashboards": [{"name": "projects/p/locations/l/dashboards/d1"}],
        "nextPageToken": "page2_token",
    }
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {
        "dashboards": [{"name": "projects/p/locations/l/dashboards/d2"}],
        "nextPageToken": None,
    }
    mock_request.side_effect = [mock_resp1, mock_resp2]

    client = Insights(project_id="p", location="l")
    res = client.list_dashboards(max_pages=2)
    assert len(res) == 2
    assert res[0]["name"] == "projects/p/locations/l/dashboards/d1"
    assert res[1]["name"] == "projects/p/locations/l/dashboards/d2"


@patch("requests.Session.request")
def test_get_dashboard(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.get_dashboard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1",
        "displayName": "Dashboard 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")

    res1 = client.get_dashboard("d1")
    assert res1["displayName"] == "Dashboard 1"
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1"
    )

    res2 = client.get_dashboard("projects/p/locations/l/dashboards/d2")
    assert res2["displayName"] == "Dashboard 1"
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d2"
    )


@patch("requests.Session.request")
def test_create_dashboard(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.create_dashboard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1",
        "displayName": "Dashboard 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {"displayName": "Dashboard 1"}
    dash = client.create_dashboard(payload, dashboard_id="d1")

    assert dash["name"] == "projects/p/locations/l/dashboards/d1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards"
    )
    assert called_args[1]["params"] == {"dashboardId": "d1"}
    assert called_args[1]["json"]["displayName"] == "Dashboard 1"


@patch("requests.Session.request")
def test_update_dashboard(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.update_dashboard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1",
        "displayName": "Updated Dashboard",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {"displayName": "Updated Dashboard"}

    # With string mask and short name
    res1 = client.update_dashboard("d1", payload, update_mask="displayName")
    assert res1["displayName"] == "Updated Dashboard"
    assert mock_request.call_args[1]["params"] == {"updateMask": "displayName"}
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1"
    )

    # With list mask and full resource name
    res2 = client.update_dashboard(
        "projects/p/locations/l/dashboards/d1",
        payload,
        update_mask=["displayName", "description"],
    )
    assert res2["displayName"] == "Updated Dashboard"
    assert mock_request.call_args[1]["params"] == {
        "updateMask": "displayName,description"
    }


@patch("requests.Session.request")
def test_delete_dashboard(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.delete_dashboard."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    client.delete_dashboard("d1")

    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "DELETE"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1"
    )


# --- Chart Tests ---


@patch("requests.Session.request")
def test_list_charts(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_charts."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "charts": [
            {
                "name": "projects/p/locations/l/dashboards/d1/charts/c1",
                "displayName": "Chart 1",
            }
        ],
        "nextPageToken": None,
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    res = client.list_charts(parent="d1")

    assert len(res) == 1
    assert res[0]["name"] == "projects/p/locations/l/dashboards/d1/charts/c1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "GET"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts"
    )


@patch("requests.Session.request")
def test_list_charts_pagination(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.list_charts with pagination."""
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {
        "charts": [{"name": "projects/p/locations/l/dashboards/d1/charts/c1"}],
        "nextPageToken": "page2_token",
    }
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {
        "charts": [{"name": "projects/p/locations/l/dashboards/d1/charts/c2"}],
        "nextPageToken": None,
    }
    mock_request.side_effect = [mock_resp1, mock_resp2]

    client = Insights(project_id="p", location="l")
    res = client.list_charts(
        parent="projects/p/locations/l/dashboards/d1", max_pages=2
    )
    assert len(res) == 2
    assert res[0]["name"] == "projects/p/locations/l/dashboards/d1/charts/c1"
    assert res[1]["name"] == "projects/p/locations/l/dashboards/d1/charts/c2"


@patch("requests.Session.request")
def test_get_chart(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.get_chart."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1/charts/c1",
        "displayName": "Chart 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")

    # With full name
    res1 = client.get_chart("projects/p/locations/l/dashboards/d1/charts/c1")
    assert res1["displayName"] == "Chart 1"
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts/c1"
    )

    # With short ID and dashboard_id
    res2 = client.get_chart("c1", dashboard_id="d1")
    assert res2["displayName"] == "Chart 1"
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts/c1"
    )

    # Missing dashboard_id error
    with pytest.raises(ValueError, match="dashboard_id is required"):
        client.get_chart("c1")


@patch("requests.Session.request")
def test_create_chart(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.create_chart."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1/charts/c1",
        "displayName": "Chart 1",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {"displayName": "Chart 1"}
    chart = client.create_chart("d1", payload, chart_id="c1")

    assert chart["name"] == "projects/p/locations/l/dashboards/d1/charts/c1"
    mock_request.assert_called_once()
    called_args = mock_request.call_args
    assert called_args[1]["method"] == "POST"
    assert (
        called_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts"
    )
    assert called_args[1]["params"] == {"chartId": "c1"}
    assert called_args[1]["json"]["displayName"] == "Chart 1"


@patch("requests.Session.request")
def test_update_chart(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.update_chart."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "projects/p/locations/l/dashboards/d1/charts/c1",
        "displayName": "Updated Chart",
    }
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")
    payload = {"displayName": "Updated Chart"}

    # With short ID and dashboard_id
    res1 = client.update_chart(
        "c1", payload, dashboard_id="d1", update_mask="displayName"
    )
    assert res1["displayName"] == "Updated Chart"
    assert mock_request.call_args[1]["params"] == {"updateMask": "displayName"}
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts/c1"
    )

    # With full name
    res2 = client.update_chart(
        "projects/p/locations/l/dashboards/d1/charts/c1",
        payload,
        update_mask=["displayName", "description"],
    )
    assert res2["displayName"] == "Updated Chart"
    assert mock_request.call_args[1]["params"] == {
        "updateMask": "displayName,description"
    }

    # Missing dashboard_id error
    with pytest.raises(ValueError, match="dashboard_id is required"):
        client.update_chart("c1", payload)


@patch("requests.Session.request")
def test_delete_chart(
    mock_request: typing.Any, mock_google_auth: typing.Any
) -> None:
    """Test Insights.delete_chart."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_request.return_value = mock_response

    client = Insights(project_id="p", location="l")

    # With short ID and dashboard_id
    client.delete_chart("c1", dashboard_id="d1")
    mock_request.assert_called_once()
    assert (
        mock_request.call_args[1]["url"]
        == "https://l-contactcenterinsights.googleapis.com/v1/projects/p/locations/l/dashboards/d1/charts/c1"
    )

    # Missing dashboard_id error
    with pytest.raises(ValueError, match="dashboard_id is required"):
        client.delete_chart("c1")
