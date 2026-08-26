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
import json
import typing
from unittest.mock import MagicMock, patch

import pandas as pd
import yaml

from cxas_scrapi.cli.insights_cli import (
    handle_add_question,
    handle_analyze_metrics,
    handle_apply,
    handle_create_analysis_rule,
    handle_create_scorecard,
    handle_create_topic_model,
    handle_delete_autolabel_rule,
    handle_delete_dashboard,
    handle_diff,
    handle_diff_autolabel_rules,
    handle_diff_dashboards,
    handle_eval,
    handle_get_autolabel_rule,
    handle_get_dashboard,
    handle_list_autolabel_rules,
    handle_list_dashboards,
    handle_pull_autolabel_rules,
    handle_pull_dashboards,
    handle_push_autolabel_rules,
    handle_push_dashboards,
    handle_report,
    handle_smoke_test_scorecard,
    populate_insights_parser,
)
from cxas_scrapi.core.autolabel_sync import dump_autolabel_rules_yaml
from cxas_scrapi.core.dashboard_sync import dump_dashboards_yaml


def test_populate_insights_parser() -> None:
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
def test_handle_create_scorecard(mock_sc_cls: typing.Any) -> None:
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
def test_handle_add_question(mock_sc_cls: typing.Any) -> None:
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
def test_handle_create_topic_model(mock_im_cls: typing.Any) -> None:
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
def test_handle_create_analysis_rule(mock_ar_cls: typing.Any) -> None:
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
def test_handle_smoke_test_scorecard(mock_utils_cls: typing.Any) -> None:
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
def test_handle_analyze_metrics(
    mock_analytics_cls: typing.Any, tmp_path: typing.Any
) -> None:
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


@patch("cxas_scrapi.utils.insights_reconciler.InsightsReconciler")
@patch("cxas_scrapi.utils.insights_utils.InsightsUtils")
def test_handle_apply(
    mock_utils_cls: typing.Any,
    mock_reconciler_cls: typing.Any,
    tmp_path: typing.Any,
) -> None:
    cfg_file = tmp_path / "config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "project_id": "test-p",
                "location": "us-central1",
                "scorecards": [],
            },
            f,
        )

    mock_rec_inst = mock_reconciler_cls.return_value
    mock_rec_inst.apply.return_value = {"status": "APPLIED"}

    args = argparse.Namespace(
        config=str(cfg_file),
        dry_run=False,
        project_id=None,
        location=None,
    )
    handle_apply(args)
    mock_rec_inst.apply.assert_called_once_with(str(cfg_file), dry_run=False)


@patch("cxas_scrapi.utils.insights_reconciler.InsightsReconciler")
@patch("cxas_scrapi.utils.insights_utils.InsightsUtils")
def test_handle_diff(
    mock_utils_cls: typing.Any,
    mock_reconciler_cls: typing.Any,
    tmp_path: typing.Any,
) -> None:
    cfg_file = tmp_path / "config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "project_id": "test-p",
                "location": "us-central1",
                "scorecards": [],
            },
            f,
        )

    mock_rec_inst = mock_reconciler_cls.return_value
    mock_rec_inst.diff.return_value = {"project_id": "test-p", "scorecards": []}

    args = argparse.Namespace(
        config=str(cfg_file),
        project_id=None,
        location=None,
    )
    handle_diff(args)
    mock_rec_inst.diff.assert_called_once_with(str(cfg_file))


@patch("cxas_scrapi.utils.scorecard_eval_runner.ScorecardEvalRunner")
def test_handle_eval(mock_runner_cls: typing.Any, tmp_path: typing.Any) -> None:
    calib_file = tmp_path / "calibration.json"
    with open(calib_file, "w", encoding="utf-8") as f:
        json.dump([{"conversation_id": "c1", "transcript": "text"}], f)

    template_file = tmp_path / "template.yaml"
    template_file.write_text(
        "qaScorecard:\n  displayName: Test\nqaQuestions: []\n"
    )

    mock_report = MagicMock()
    mock_report.scorecard_display_name = "Test"
    mock_report.total_conversations = 1
    mock_report.total_evaluations = 1
    mock_report.overall_accuracy = 1.0
    mock_report.question_metrics = {}
    mock_report.discrepancies = []
    mock_report.to_dict.return_value = {"overall_accuracy": 1.0}

    mock_runner_inst = mock_runner_cls.return_value
    mock_runner_inst.evaluate_scorecard_on_calibration_set.return_value = (
        mock_report
    )

    out_file = tmp_path / "eval_out.json"
    args = argparse.Namespace(
        template=str(template_file),
        calibration_set=str(calib_file),
        goldens=str(calib_file),
        model="gemini-2.5-flash",
        project_id="test-p",
        location="global",
        output=str(out_file),
    )
    handle_eval(args)
    mock_runner_inst.evaluate_scorecard_on_calibration_set.assert_called_once()
    assert out_file.exists()


@patch("cxas_scrapi.utils.metrics_extractor.MetricsExtractor")
@patch("cxas_scrapi.core.scorecards.Scorecards")
def test_handle_report(
    mock_sc_cls: typing.Any,
    mock_extractor_cls: typing.Any,
    tmp_path: typing.Any,
) -> None:
    mock_ext_inst = mock_extractor_cls.return_value
    mock_ext_inst.get_evaluation_results.return_value = pd.DataFrame(
        [{"conversation_id": "c1", "question_id": "q1", "score": 1.0}]
    )

    csv_file = tmp_path / "report.csv"
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        filter="turn_count > 2",
        scorecards="sc1,sc2",
        output=str(csv_file),
    )
    handle_report(args)
    mock_ext_inst.get_evaluation_results.assert_called_once_with(
        filter_str="turn_count > 2",
        scorecard_names=["sc1", "sc2"],
    )
    assert csv_file.exists()


def test_populate_insights_parser_declarative_and_eval_commands() -> None:
    parser = argparse.ArgumentParser()
    populate_insights_parser(parser)

    # apply
    args = parser.parse_args(["apply", "--config", "insights_config.yaml"])
    assert args.insights_command == "apply"
    assert args.config == "insights_config.yaml"

    # diff
    args = parser.parse_args(["diff", "--config", "insights_config.yaml"])
    assert args.insights_command == "diff"

    # eval
    args = parser.parse_args(
        ["eval", "--template", "t.yaml", "--calibration-set", "calib.json"]
    )
    assert args.insights_command == "eval"

    # report
    args = parser.parse_args(["report", "--parent", "projects/p/locations/l"])
    assert args.insights_command == "report"


def test_populate_insights_parser_autolabel_commands() -> None:
    parser = argparse.ArgumentParser()
    populate_insights_parser(parser)

    # pull-autolabel-rules
    args = parser.parse_args(
        [
            "pull-autolabel-rules",
            "--parent",
            "projects/p/locations/l",
            "--out",
            "rules.yaml",
        ]
    )
    assert args.insights_command == "pull-autolabel-rules"
    assert args.out == "rules.yaml"

    # diff-autolabel-rules
    args = parser.parse_args(["diff-autolabel-rules", "--file", "rules.yaml"])
    assert args.insights_command == "diff-autolabel-rules"

    # push-autolabel-rules
    args = parser.parse_args(
        ["push-autolabel-rules", "--file", "rules.yaml", "--dry-run", "--force"]
    )
    assert args.insights_command == "push-autolabel-rules"
    assert args.dry_run is True
    assert args.force is True

    # list-autolabel-rules
    args = parser.parse_args(
        ["list-autolabel-rules", "--parent", "projects/p/locations/l"]
    )
    assert args.insights_command == "list-autolabel-rules"

    # get-autolabel-rule
    args = parser.parse_args(
        [
            "get-autolabel-rule",
            "--rule-name",
            "projects/p/locations/l/autoLabelingRules/r1",
        ]
    )
    assert args.insights_command == "get-autolabel-rule"

    # delete-autolabel-rule
    args = parser.parse_args(
        [
            "delete-autolabel-rule",
            "--rule-name",
            "projects/p/locations/l/autoLabelingRules/r1",
        ]
    )
    assert args.insights_command == "delete-autolabel-rule"


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_pull_autolabel_rules(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    mock_client = mock_insights_cls.return_value
    mock_client.list_autolabeling_rules.return_value = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "Rule 1",
            "labelKey": "k1",
            "conditions": [],
        }
    ]

    out_file = tmp_path / "pulled_rules.yaml"
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        out=str(out_file),
    )
    handle_pull_autolabel_rules(args)
    assert out_file.exists()
    assert "Rule 1" in out_file.read_text()


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_diff_autolabel_rules(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    mock_client = mock_insights_cls.return_value
    mock_client.list_autolabeling_rules.return_value = []

    file_path = tmp_path / "rules.yaml"
    dump_autolabel_rules_yaml(
        {
            "version": "1.0",
            "project_id": "p",
            "location": "l",
            "autolabeling_rules": [
                {
                    "rule_id": "r1",
                    "label_key": "k",
                    "conditions": [{"condition": "", "value": "'v'"}],
                }
            ],
        },
        file_path,
    )

    args = argparse.Namespace(
        file=str(file_path),
        parent=None,
    )
    handle_diff_autolabel_rules(args)
    mock_client.list_autolabeling_rules.assert_called_once()


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_push_autolabel_rules(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    mock_client = mock_insights_cls.return_value
    mock_client.list_autolabeling_rules.return_value = []

    file_path = tmp_path / "rules.yaml"
    dump_autolabel_rules_yaml(
        {
            "version": "1.0",
            "project_id": "p",
            "location": "l",
            "autolabeling_rules": [
                {
                    "rule_id": "r1",
                    "label_key": "k",
                    "conditions": [{"condition": "", "value": "'v'"}],
                }
            ],
        },
        file_path,
    )

    args = argparse.Namespace(
        file=str(file_path),
        parent="projects/p/locations/l",
        dry_run=False,
        force=False,
    )
    handle_push_autolabel_rules(args)
    mock_client.create_autolabeling_rule.assert_called_once()


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_list_and_get_and_delete_autolabel(
    mock_insights_cls: typing.Any,
) -> None:
    mock_client = mock_insights_cls.return_value
    mock_client.list_autolabeling_rules.return_value = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "R1",
        }
    ]
    mock_client.get_autolabeling_rule.return_value = {
        "name": "projects/p/locations/l/autoLabelingRules/r1"
    }

    # list
    handle_list_autolabel_rules(
        argparse.Namespace(parent="projects/p/locations/l")
    )
    mock_client.list_autolabeling_rules.assert_called_once()

    # get
    handle_get_autolabel_rule(
        argparse.Namespace(
            rule_name="projects/p/locations/l/autoLabelingRules/r1"
        )
    )
    mock_client.get_autolabeling_rule.assert_called_once()

    # delete
    handle_delete_autolabel_rule(
        argparse.Namespace(
            rule_name="projects/p/locations/l/autoLabelingRules/r1"
        )
    )
    mock_client.delete_autolabeling_rule.assert_called_once()


# --- Dashboard CLI Tests ---


def test_dashboard_cli_subparsers() -> None:
    """Test dashboard CLI subparsers argument parsing."""
    parser = argparse.ArgumentParser()
    populate_insights_parser(parser)

    # 1. pull-dashboards
    args_pull = parser.parse_args(
        ["pull-dashboards", "--parent", "projects/p/locations/l", "--out", "my_dashboards.yaml"]
    )
    assert args_pull.insights_command == "pull-dashboards"
    assert args_pull.parent == "projects/p/locations/l"
    assert args_pull.out == "my_dashboards.yaml"

    # 2. diff-dashboards
    args_diff = parser.parse_args(
        ["diff-dashboards", "--file", "custom.yaml", "--parent", "projects/p/locations/l"]
    )
    assert args_diff.insights_command == "diff-dashboards"
    assert args_diff.file == "custom.yaml"

    # 3. push-dashboards
    args_push = parser.parse_args(
        ["push-dashboards", "--dry-run", "--force"]
    )
    assert args_push.insights_command == "push-dashboards"
    assert args_push.dry_run is True
    assert args_push.force is True

    # 4. list-dashboards
    args_list = parser.parse_args(
        ["list-dashboards", "--parent", "projects/p/locations/l"]
    )
    assert args_list.insights_command == "list-dashboards"

    # 5. get-dashboard
    args_get = parser.parse_args(
        ["get-dashboard", "--dashboard-name", "projects/p/locations/l/dashboards/d1"]
    )
    assert args_get.insights_command == "get-dashboard"
    assert args_get.dashboard_name == "projects/p/locations/l/dashboards/d1"

    # 6. delete-dashboard
    args_del = parser.parse_args(
        ["delete-dashboard", "--dashboard-name", "projects/p/locations/l/dashboards/d1"]
    )
    assert args_del.insights_command == "delete-dashboard"


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_pull_dashboards(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    """Test handle_pull_dashboards command."""
    mock_client = mock_insights_cls.return_value
    mock_client.list_dashboards.return_value = [
        {
            "name": "projects/p/locations/l/dashboards/d1",
            "displayName": "Dashboard 1",
            "rootContainer": {"widgets": []},
            "readOnly": False,
        }
    ]

    out_file = tmp_path / "pulled_dashboards.yaml"
    args = argparse.Namespace(
        parent="projects/p/locations/l",
        out=str(out_file),
    )
    handle_pull_dashboards(args)
    assert out_file.exists()
    mock_client.list_dashboards.assert_called_once_with(
        parent="projects/p/locations/l"
    )


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_diff_dashboards(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    """Test handle_diff_dashboards command."""
    mock_client = mock_insights_cls.return_value
    mock_client.list_dashboards.return_value = []

    file_path = tmp_path / "dashboards.yaml"
    dump_dashboards_yaml(
        {
            "version": "1.0",
            "project_id": "p",
            "location": "l",
            "dashboards": [
                {
                    "dashboard_id": "d1",
                    "display_name": "Dash 1",
                    "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
                }
            ],
        },
        file_path,
    )

    args = argparse.Namespace(
        file=str(file_path),
        parent="projects/p/locations/l",
    )
    handle_diff_dashboards(args)
    mock_client.list_dashboards.assert_called_once()


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_push_dashboards(
    mock_insights_cls: typing.Any, tmp_path: typing.Any
) -> None:
    """Test handle_push_dashboards command."""
    mock_client = mock_insights_cls.return_value
    mock_client.list_dashboards.return_value = []

    file_path = tmp_path / "dashboards.yaml"
    dump_dashboards_yaml(
        {
            "version": "1.0",
            "project_id": "p",
            "location": "l",
            "dashboards": [
                {
                    "dashboard_id": "d1",
                    "display_name": "Dash 1",
                    "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
                }
            ],
        },
        file_path,
    )

    args = argparse.Namespace(
        file=str(file_path),
        parent="projects/p/locations/l",
        dry_run=False,
        force=False,
    )
    handle_push_dashboards(args)
    mock_client.create_dashboard.assert_called_once()


@patch("cxas_scrapi.core.insights.Insights")
def test_handle_list_and_get_and_delete_dashboards(
    mock_insights_cls: typing.Any,
) -> None:
    """Test list, get, and delete dashboard CLI commands."""
    mock_client = mock_insights_cls.return_value
    mock_client.list_dashboards.return_value = [
        {
            "name": "projects/p/locations/l/dashboards/d1",
            "displayName": "D1",
            "readOnly": False,
            "rootContainer": {"widgets": []},
        }
    ]
    mock_client.get_dashboard.return_value = {
        "name": "projects/p/locations/l/dashboards/d1",
        "displayName": "D1",
    }

    # list
    handle_list_dashboards(
        argparse.Namespace(parent="projects/p/locations/l")
    )
    mock_client.list_dashboards.assert_called_once()

    # get
    handle_get_dashboard(
        argparse.Namespace(
            dashboard_name="projects/p/locations/l/dashboards/d1"
        )
    )
    mock_client.get_dashboard.assert_called_once()

    # delete
    handle_delete_dashboard(
        argparse.Namespace(
            dashboard_name="projects/p/locations/l/dashboards/d1"
        )
    )
    mock_client.delete_dashboard.assert_called_once()

