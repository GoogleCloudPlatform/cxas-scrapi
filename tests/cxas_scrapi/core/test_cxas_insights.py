"""Unit tests for CXASInsights app facade."""

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

import pandas as pd
import pytest

from cxas_scrapi.core.cxas_insights import CXASInsights


@pytest.fixture
def mock_insights_facade() -> typing.Any:
    with patch("cxas_scrapi.core.cxas_insights.InsightsUtils") as mock_utils:
        mock_instance = MagicMock()
        mock_utils.return_value = mock_instance
        facade = CXASInsights(
            app_id="projects/test-proj/locations/us-central1/apps/agent-uuid-123"
        )
        yield facade, mock_instance


def test_init_parses_app_id() -> None:
    with patch("cxas_scrapi.core.cxas_insights.InsightsUtils"):
        facade = CXASInsights(
            app_id="projects/my-proj/locations/us-central1/apps/my-app"
        )
        assert facade.project_id == "my-proj"
        assert facade.location == "us-central1"
        assert facade.agent_uuid == "my-app"


def test_init_invalid_app_id() -> None:
    with pytest.raises(ValueError, match="Invalid app_id format"):
        CXASInsights(app_id="invalid/path/to/app")


def test_deploy_scorecards(mock_insights_facade: typing.Any) -> None:
    facade, mock_utils = mock_insights_facade
    mock_utils.import_scorecard.return_value = "projects/test-proj/locations/us-central1/qaScorecards/sc-csat/revisions/1"

    with patch(
        "cxas_scrapi.core.cxas_insights.template_manager.load_scorecard_template"
    ) as mock_load:
        mock_load.return_value = (
            {"displayName": "CSAT Scorecard"},
            [{"questionBody": "Was agent helpful?"}],
        )
        facade.add("fake/path/csat.yaml")
        revisions = facade.deploy()

        assert len(revisions) == 1
        assert "revisions/1" in revisions[0]
        mock_utils.import_scorecard.assert_called_once()
        mock_utils.setup_analysis_rule_for_app.assert_called_once()


def test_get_report(mock_insights_facade: typing.Any) -> None:
    facade, _mock_utils = mock_insights_facade

    expected_df = pd.DataFrame([{"conversation_name": "conv1", "score": 1.0}])
    facade.metrics_extractor = MagicMock()
    facade.metrics_extractor.get_evaluation_results.return_value = expected_df

    df = facade.get_report(filter_str="create_time > '2026-01-01'")
    assert not df.empty
    assert len(df) == 1
    facade.metrics_extractor.get_evaluation_results.assert_called_once()
