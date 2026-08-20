"""Unit tests for InsightsReconciler."""

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
import yaml

from cxas_scrapi.utils.insights_reconciler import InsightsReconciler


@pytest.fixture
def mock_insights_utils() -> typing.Any:
    utils = MagicMock()
    utils.project_id = "test-proj"
    utils.location = "us-central1"
    utils.scorecards_client.parent = "projects/test-proj/locations/us-central1"
    utils.analysis_rules_client.parent = (
        "projects/test-proj/locations/us-central1"
    )
    return utils


@pytest.fixture
def temp_config(tmp_path: typing.Any) -> str:
    config_content = {
        "version": 1,
        "project_id": "test-proj",
        "location": "us-central1",
        "scorecards": [
            {
                "template": str(tmp_path / "test_scorecard.yaml"),
                "scorecard_id": "test-sc",
                "apply_to": [
                    {
                        "rule_id": "live_rule_1",
                        "filter": "latest_agent_version = 'v2'",
                        "percentage": 100,
                    },
                    {
                        "backfill": True,
                        "filter": "create_time > '2026-01-01'",
                        "percentage": 100.0,
                    },
                ],
            }
        ],
    }

    config_path = tmp_path / "insights_config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_content, f)

    # Create dummy scorecard template file
    sc_path = tmp_path / "test_scorecard.yaml"
    sc_content = {
        "qaScorecard": {"displayName": "Test Scorecard"},
        "qaQuestions": [{"questionBody": "Question 1?"}],
    }
    with open(sc_path, "w", encoding="utf-8") as f:
        yaml.dump(sc_content, f)

    return str(config_path)


def test_reconciler_diff(
    mock_insights_utils: typing.Any, temp_config: str
) -> None:
    mock_insights_utils.scorecards_client.get_scorecard.return_value = {
        "name": "projects/test-proj/locations/us-central1/qaScorecards/test-sc"
    }
    mock_insights_utils.scorecards_client.get_latest_revision.return_value = {
        "name": "projects/test-proj/locations/us-central1/qaScorecards/test-sc/revisions/1"
    }

    reconciler = InsightsReconciler(mock_insights_utils)
    reconciler.metrics_extractor.get_missing_conversations = MagicMock(
        return_value=["conv1", "conv2"]
    )

    diff = reconciler.diff(temp_config)

    assert diff["project_id"] == "test-proj"
    assert len(diff["scorecards"]) == 1
    sc_plan = diff["scorecards"][0]
    assert sc_plan["scorecard_id"] == "test-sc"
    assert sc_plan["exists_in_gcp"] is True
    assert len(sc_plan["rules"]) == 1
    assert sc_plan["rules"][0]["rule_id"] == "live_rule_1"
    assert len(sc_plan["backfills"]) == 1
    assert sc_plan["backfills"][0]["estimated_missing_conversations"] == 2


def test_reconciler_apply(
    mock_insights_utils: typing.Any, temp_config: str
) -> None:
    mock_insights_utils.import_scorecard.return_value = "projects/test-proj/locations/us-central1/qaScorecards/test-sc/revisions/1"
    reconciler = InsightsReconciler(mock_insights_utils)
    reconciler.metrics_extractor.get_missing_conversations = MagicMock(
        return_value=["conv1"]
    )

    result = reconciler.apply(temp_config, dry_run=False)

    assert result["status"] == "APPLIED"
    assert len(result["results"]) == 1
    mock_insights_utils.import_scorecard.assert_called_once()
    mock_insights_utils.scorecards_client.deploy_revision.assert_called_once()
    mock_insights_utils.analysis_rules_client.create_analysis_rule.assert_called_once()
    mock_insights_utils.scorecards_client.bulk_analyze_conversations.assert_called_once()
