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

import argparse
from unittest.mock import patch

from cxas_scrapi.cli.insights_cli import (
    handle_add_question,
    handle_analyze_metrics,
    handle_create_analysis_rule,
    handle_create_scorecard,
    handle_create_topic_model,
    handle_smoke_test_scorecard,
    populate_insights_parser,
)


def test_populate_insights_parser():
    parser = argparse.ArgumentParser()
    populate_insights_parser(parser)
    # Test subcommands exist by parsing valid args
    args = parser.parse_args(
        [
            "list-scorecards",
            "--parent",
            "projects/p/locations/l",
        ]
    )
    assert args.insights_command == "list-scorecards"

    args = parser.parse_args(
        [
            "create-scorecard",
            "--parent",
            "projects/p/locations/l",
            "--scorecard-id",
            "sc1",
            "--display-name",
            "SC 1",
        ]
    )
    assert args.insights_command == "create-scorecard"

    args = parser.parse_args(
        [
            "create-topic-model",
            "--parent",
            "projects/p/locations/l",
            "--display-name",
            "TM 1",
        ]
    )
    assert args.insights_command == "create-topic-model"

    args = parser.parse_args(
        [
            "create-analysis-rule",
            "--parent",
            "projects/p/locations/l",
            "--display-name",
            "AR 1",
        ]
    )
    assert args.insights_command == "create-analysis-rule"

    args = parser.parse_args(
        [
            "analyze-metrics",
            "--parent",
            "projects/p/locations/l",
            "--time-window",
            "7d",
        ]
    )
    assert args.insights_command == "analyze-metrics"


@patch("cxas_scrapi.core.scorecards.Scorecards")
def test_handle_create_scorecard(mock_sc_cls):
    mock_inst = mock_sc_cls.return_value
    mock_inst.create_scorecard_with_questions.return_value = (
        {"name": "projects/p/locations/l/qaScorecards/sc1/revisions/r1"},
        [],
    )
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        scorecard_id="sc1",
        display_name="Test SC",
        description="Desc",
        template=None,
    )
    handle_create_scorecard(args)
    mock_inst.create_scorecard_with_questions.assert_called_once()


@patch("cxas_scrapi.core.scorecards.Scorecards")
def test_handle_add_question(mock_sc_cls):
    mock_inst = mock_sc_cls.return_value
    mock_inst.create_question.return_value = {
        "name": (
            "projects/p/locations/l/qaScorecards/sc1/revisions/r1/"
            "qaQuestions/q1"
        )
    }
    args = argparse.Namespace(
        revision_name="projects/p/locations/l/qaScorecards/sc1/revisions/r1",
        question_body="Q1",
        answer_choices="Yes=1.0,No=0.0",
        answer_instructions="Rubric",
        abbreviation="q1_abbr",
    )
    handle_add_question(args)
    mock_inst.create_question.assert_called_once()
    call_args = mock_inst.create_question.call_args[0]
    rev_name = call_args[0]
    assert rev_name == "projects/p/locations/l/qaScorecards/sc1/revisions/r1"
    assert call_args[1]["questionBody"] == "Q1"
    assert call_args[1]["answerChoices"] == [
        {"strValue": "Yes", "score": 1.0},
        {"strValue": "No", "score": 0.0},
    ]


@patch("cxas_scrapi.core.issue_models.IssueModels")
def test_handle_create_topic_model(mock_im_cls):
    mock_inst = mock_im_cls.return_value
    mock_inst.create_topic_model_for_app.return_value = {
        "name": "projects/p/locations/l/issueModels/im1"
    }
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        display_name="TM 1",
        app_name="bella_notte",
        deploy=True,
    )
    handle_create_topic_model(args)
    mock_inst.create_topic_model_for_app.assert_called_once_with(
        display_name="TM 1",
        app_name="bella_notte",
        filter_str=None,
        parent="projects/p/locations/l",
        deploy=True,
    )


@patch("cxas_scrapi.core.analysis_rules.AnalysisRules")
def test_handle_create_analysis_rule(mock_ar_cls):
    mock_inst = mock_ar_cls.return_value
    mock_inst.create_rule_for_app.return_value = {
        "name": "projects/p/locations/l/analysisRules/ar1"
    }
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        display_name="AR 1",
        app_name="bella_notte",
        filter=None,
        scorecard_revisions=None,
        issue_models=None,
        run_summarization=True,
        run_sentiment=True,
        active=True,
        rule_id=None,
    )
    handle_create_analysis_rule(args)
    mock_inst.create_rule_for_app.assert_called_once()


@patch("cxas_scrapi.utils.insights_utils.InsightsUtils")
def test_handle_smoke_test_scorecard(mock_utils_cls):
    mock_inst = mock_utils_cls.return_value
    mock_inst.smoke_test_scorecard.return_value = [
        {"conversation_name": "conv1", "status": "PASSED", "qa_answer": {}}
    ]
    args = argparse.Namespace(
        scorecard_name="projects/p/locations/l/qaScorecards/sc1/revisions/r1",
        conversations="conv1,conv2",
        simulate_file=None,
        parent="projects/p/locations/l",
    )
    handle_smoke_test_scorecard(args)
    mock_inst.smoke_test_scorecard.assert_called_once_with(
        "projects/p/locations/l/qaScorecards/sc1/revisions/r1",
        ["conv1", "conv2"],
        parent="projects/p/locations/l",
    )


@patch("cxas_scrapi.utils.insights_analytics.InsightsAnalytics")
def test_handle_analyze_metrics(mock_analytics_cls, tmp_path):
    mock_inst = mock_analytics_cls.return_value
    mock_inst.aggregate_metrics.return_value = {
        "kpis": {"total_conversations": 5}
    }
    mock_inst.generate_html_dashboard.return_value = "<html>Dashboard</html>"

    html_file = tmp_path / "dash.html"
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        time_window="24h",
        app_name="bella_notte",
        filter=None,
        html_output=str(html_file),
        json_output=None,
    )
    handle_analyze_metrics(args)
    mock_inst.aggregate_metrics.assert_called_once()
    assert html_file.read_text() == "<html>Dashboard</html>"
