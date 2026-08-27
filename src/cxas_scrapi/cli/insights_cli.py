"""CLI module for handling Insights Operations."""

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
import logging
import sys
import tempfile


def _get_project_and_location_from_parent(parent: str) -> tuple[str, str]:
    """Helper to extract project and location from parent string."""
    parts = parent.split("/")
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "locations":
        print(
            f"Error: Invalid parent format: {parent}. "
            f"Expected projects/PROJ/locations/LOC"
        )
        sys.exit(1)
    return parts[1], parts[3]


def handle_list(args: argparse.Namespace) -> None:
    """Handles the 'insights list' command."""
    print(f"Listing scorecards in: {args.parent}")
    from cxas_scrapi.core.scorecards import Scorecards

    project_id, location = _get_project_and_location_from_parent(args.parent)
    scorecards_client = Scorecards(project_id=project_id, location=location)

    try:
        scorecards = scorecards_client.list_scorecards(parent=args.parent)
        for s in scorecards:
            print(f"Scorecard: {s['name']} ({s.get('displayName', 'N/A')})")
    except Exception as e:
        print(f"Failed to list scorecards: {e}")
        sys.exit(1)


def handle_export(args: argparse.Namespace) -> None:
    """Handles the 'insights export' command."""
    print(f"Exporting scorecard {args.scorecard_name} to {args.template}")
    import cxas_scrapi.utils.scorecard_template_manager as template_manager
    from cxas_scrapi.core.scorecards import Scorecards

    # Extract project/location from the full scorecard name.
    # Format: projects/PROJ/locations/LOC/qaScorecards/ID
    project_id, location = _get_project_and_location_from_parent(
        args.scorecard_name
    )
    scorecards_client = Scorecards(project_id=project_id, location=location)

    try:
        # Fetch the scorecard wrapper metadata
        scorecard = scorecards_client.get_scorecard(args.scorecard_name)

        # Fetch the latest revision to get the questions
        latest_revision_name = f"{args.scorecard_name}/revisions/latest"
        questions = scorecards_client.list_questions(latest_revision_name)

        # We explicitly list the fields to export, identical to the old behavior
        fields_to_export = (
            "order",
            "questionType",
            "questionMedium",
            "qaQuestionDataOptions",
            "questionBody",
            "answerChoices",
            "answerInstructions",
        )

        template_manager.save_scorecard_template(
            scorecard, questions, args.template, fields_to_export
        )
        print(f"Successfully exported to {args.template}")

    except Exception as e:
        print(f"Failed to export scorecard: {e}")
        sys.exit(1)


def handle_import(args: argparse.Namespace) -> None:
    """Handles the 'insights import' command."""
    if not args.scorecard_name and not args.parent:
        print(
            "Error: Must provide either --scorecard_name or --parent for "
            "import."
        )
        sys.exit(1)

    import cxas_scrapi.utils.scorecard_template_manager as template_manager
    from cxas_scrapi.utils.insights_utils import InsightsUtils

    target_id = None
    if args.scorecard_name:
        project_id, location = _get_project_and_location_from_parent(
            args.scorecard_name
        )
        target_id = args.scorecard_name.split("/")[-1]
    else:
        project_id, location = _get_project_and_location_from_parent(
            args.parent
        )

    print(f"Importing scorecard template from {args.template}")
    utils_client = InsightsUtils(project_id=project_id, location=location)

    try:
        scorecard_dict, questions = template_manager.load_scorecard_template(
            args.template
        )

        target_revision = utils_client.import_scorecard(
            scorecard_dict=scorecard_dict,
            questions=questions,
            target_scorecard_id=target_id,
        )
        print(f"Successfully imported template to revision: {target_revision}")

    except Exception as e:
        print(f"Failed to import scorecard: {e}")
        sys.exit(1)


def handle_copy(args: argparse.Namespace) -> None:
    """Handles the 'insights copy' command."""
    if not args.dst_scorecard_name and not args.parent:
        print(
            "Error: Must provide either --dst_scorecard_name or --parent "
            "for destination."
        )
        sys.exit(1)

    print(f"Copying scorecard from {args.scorecard_name}")
    # Run an export to a temp file, then import it
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w") as tmp_file:
        template_path = tmp_file.name

        # Shim the args for the export internal call
        export_args = argparse.Namespace()
        export_args.scorecard_name = args.scorecard_name
        export_args.template = template_path
        handle_export(export_args)

        # Shim the args for the import internal call
        import_args = argparse.Namespace()
        import_args.scorecard_name = args.dst_scorecard_name
        import_args.parent = args.parent
        import_args.template = template_path
        handle_import(import_args)

    print("Successfully completed copy operation.")


# --- New Scorecard Handlers ---


def handle_create_scorecard(args: argparse.Namespace) -> None:
    """Handles the 'insights create-scorecard' command."""
    print(f"Creating scorecard {args.scorecard_id} under {args.parent}...")
    import cxas_scrapi.utils.scorecard_template_manager as template_manager
    from cxas_scrapi.core.scorecards import Scorecards

    project_id, location = _get_project_and_location_from_parent(args.parent)
    scorecards_client = Scorecards(project_id=project_id, location=location)

    questions = []
    if getattr(args, "template", None):
        _, questions = template_manager.load_scorecard_template(args.template)

    try:
        revision, created_q = scorecards_client.create_scorecard_with_questions(
            scorecard_id=args.scorecard_id,
            display_name=args.display_name,
            description=getattr(args, "description", "") or "",
            questions=questions,
            parent=args.parent,
        )
        print(f"Successfully created scorecard revision: {revision['name']}")
        print(f"Added {len(created_q)} questions.")
    except Exception as e:
        print(f"Failed to create scorecard: {e}")
        sys.exit(1)


def handle_add_question(args: argparse.Namespace) -> None:
    """Handles the 'insights add-question' command."""
    print(f"Adding question to revision {args.revision_name}...")
    from cxas_scrapi.core.scorecards import Scorecards

    project_id, location = _get_project_and_location_from_parent(
        args.revision_name
    )
    scorecards_client = Scorecards(project_id=project_id, location=location)

    # Parse choices (format: "Yes=1.0,No=0.0" or JSON)
    choices_list = []
    if getattr(args, "answer_choices", None):
        for part in args.answer_choices.split(","):
            if "=" in part:
                key, val_str = part.split("=", 1)
                try:
                    val = float(val_str)
                except ValueError:
                    val = 0.0
                choices_list.append({"strValue": key.strip(), "score": val})

    question_dict = {
        "questionBody": args.question_body,
        "answerChoices": choices_list,
        "answerInstructions": getattr(args, "answer_instructions", "") or "",
    }
    if getattr(args, "abbreviation", None):
        question_dict["abbreviation"] = args.abbreviation

    try:
        res = scorecards_client.create_question(
            args.revision_name, question_dict
        )
        print(f"Successfully added question: {res['name']}")
    except Exception as e:
        print(f"Failed to add question: {e}")
        sys.exit(1)


# --- Topic Models (IssueModels) Handlers ---


def handle_list_topic_models(args: argparse.Namespace) -> None:
    """Handles the 'insights list-topic-models' command."""
    print(f"Listing topic models in: {args.parent}")
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(args.parent)
    im_client = IssueModels(project_id=project_id, location=location)

    try:
        models = im_client.list_topic_models(parent=args.parent)
        for m in models:
            state = m.get("state", "UNKNOWN")
            print(
                f"Topic Model: {m['name']} ({m.get('displayName', 'N/A')}) - State: {state}"
            )
    except Exception as e:
        print(f"Failed to list topic models: {e}")
        sys.exit(1)


def handle_create_topic_model(args: argparse.Namespace) -> None:
    """Handles the 'insights create-topic-model' command."""
    print(f"Creating topic model '{args.display_name}' under {args.parent}...")
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(args.parent)
    im_client = IssueModels(project_id=project_id, location=location)

    try:
        model = im_client.create_topic_model_for_app(
            display_name=args.display_name,
            app_name=getattr(args, "app_name", None),
            filter_str=getattr(args, "filter", None),
            parent=args.parent,
            deploy=getattr(args, "deploy", True),
        )
        print(
            f"Successfully created topic model: {model.get('name', 'UNKNOWN')}"
        )
    except Exception as e:
        print(f"Failed to create topic model: {e}")
        sys.exit(1)


def handle_deploy_topic_model(args: argparse.Namespace) -> None:
    """Handles the 'insights deploy-topic-model' command."""
    print(f"Deploying topic model {args.issue_model_name}...")
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(
        args.issue_model_name
    )
    im_client = IssueModels(project_id=project_id, location=location)
    try:
        im_client.deploy_issue_model(args.issue_model_name)
        print("Successfully initiated deploy operation.")
    except Exception as e:
        print(f"Failed to deploy topic model: {e}")
        sys.exit(1)


def handle_undeploy_topic_model(args: argparse.Namespace) -> None:
    """Handles the 'insights undeploy-topic-model' command."""
    print(f"Undeploying topic model {args.issue_model_name}...")
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(
        args.issue_model_name
    )
    im_client = IssueModels(project_id=project_id, location=location)
    try:
        im_client.undeploy_issue_model(args.issue_model_name)
        print("Successfully initiated undeploy operation.")
    except Exception as e:
        print(f"Failed to undeploy topic model: {e}")
        sys.exit(1)


def handle_get_topic_model(args: argparse.Namespace) -> None:
    """Handles the 'insights get-topic-model' command."""
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(
        args.issue_model_name
    )
    im_client = IssueModels(project_id=project_id, location=location)
    try:
        model = im_client.get_topic_model(args.issue_model_name)
        print(f"Topic Model: {model['name']}")
        print(f"Display Name: {model.get('displayName')}")
        print(f"State: {model.get('state')}")
        if getattr(args, "include_stats", False):
            stats = im_client.calculate_issue_model_stats(args.issue_model_name)
            print(f"Stats: {stats}")
    except Exception as e:
        print(f"Failed to get topic model: {e}")
        sys.exit(1)


def handle_list_topics(args: argparse.Namespace) -> None:
    """Handles the 'insights list-topics' command."""
    from cxas_scrapi.core.issue_models import IssueModels

    project_id, location = _get_project_and_location_from_parent(
        args.issue_model_name
    )
    im_client = IssueModels(project_id=project_id, location=location)
    try:
        issues = im_client.list_issues(args.issue_model_name)
        print(f"Found {len(issues)} topics for model {args.issue_model_name}:")
        for i in issues:
            print(f" - {i['name']}: {i.get('displayName', 'N/A')}")
    except Exception as e:
        print(f"Failed to list topics: {e}")
        sys.exit(1)


# --- Analysis Rules Handlers ---


def handle_list_analysis_rules(args: argparse.Namespace) -> None:
    """Handles the 'insights list-analysis-rules' command."""
    print(f"Listing analysis rules in: {args.parent}")
    from cxas_scrapi.core.analysis_rules import AnalysisRules

    project_id, location = _get_project_and_location_from_parent(args.parent)
    ar_client = AnalysisRules(project_id=project_id, location=location)
    try:
        rules = ar_client.list_analysis_rules(parent=args.parent)
        for r in rules:
            print(
                f"Rule: {r['name']} ({r.get('displayName', 'N/A')}) - Active: {r.get('active', False)}"
            )
    except Exception as e:
        print(f"Failed to list analysis rules: {e}")
        sys.exit(1)


def handle_activate_scorecard(args: argparse.Namespace) -> None:
    """Handles the 'insights activate-scorecard' (and deploy-scorecard) command."""
    from cxas_scrapi.core.scorecards import Scorecards

    rev_name = getattr(args, "revision_name", None) or getattr(
        args, "scorecard_name", None
    )
    if not rev_name:
        print("Error: Must specify --revision-name.")
        sys.exit(1)

    project_id, location = _get_project_and_location_from_parent(rev_name)
    sc_client = Scorecards(project_id=project_id, location=location)

    print(f"Activating scorecard revision {rev_name}...")
    try:
        wait_ready = getattr(args, "wait", True)
        res = sc_client.activate_revision(
            rev_name,
            wait_for_ready=wait_ready,
            timeout_seconds=getattr(args, "timeout", 120) or 120,
        )
        state = res.get("state", "UNKNOWN")
        print(f"Scorecard revision state: {state}")
        if state == "READY":
            print(
                f"Successfully tuned and activated scorecard revision '{rev_name}'. It is now READY for live evaluation."
            )
        elif state == "TRAINING":
            print(
                f"Scorecard revision '{rev_name}' has started TRAINING/TUNING. It will become READY shortly."
            )
        else:
            print(f"Scorecard revision '{rev_name}' is in state '{state}'.")
    except Exception as e:
        print(f"Failed to activate scorecard revision: {e}")
        sys.exit(1)


def handle_validate_scorecard(args: argparse.Namespace) -> None:
    """Handles the 'insights validate-scorecard' command."""
    from cxas_scrapi.core.scorecards import Scorecards

    rev_name = getattr(args, "revision_name", None) or getattr(
        args, "scorecard_name", None
    )
    if not rev_name:
        print("Error: Must specify --revision-name.")
        sys.exit(1)

    project_id, location = _get_project_and_location_from_parent(rev_name)
    sc_client = Scorecards(project_id=project_id, location=location)

    try:
        rev = sc_client.get_revision(rev_name)
        state = rev.get("state", "UNKNOWN")
        print(f"Scorecard revision : {rev_name}")
        print(f"Current State      : {state}")
        if state == "EDITABLE":
            msg = f"[WARNING] Scorecard revision '{rev_name}' is currently in state 'EDITABLE' (Draft/Inactive). Run 'cxas insights activate-scorecard --revision-name {rev_name}' to tune and deploy it before use!"
            print(msg)
            if getattr(args, "strict", False):
                sys.exit(1)
        elif state != "READY":
            print(
                f"[WARNING] Scorecard revision '{rev_name}' is in state '{state}' (not yet READY)."
            )
            if getattr(args, "strict", False):
                sys.exit(1)
        else:
            print("Scorecard revision is valid and READY for use.")
    except Exception as e:
        print(f"Failed to validate scorecard revision: {e}")
        sys.exit(1)


def handle_create_analysis_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights create-analysis-rule' command."""
    print(
        f"Creating analysis rule '{args.display_name}' under {args.parent}..."
    )
    from cxas_scrapi.core.analysis_rules import AnalysisRules
    from cxas_scrapi.core.scorecards import Scorecards

    project_id, location = _get_project_and_location_from_parent(args.parent)
    ar_client = AnalysisRules(project_id=project_id, location=location)
    sc_client = Scorecards(project_id=project_id, location=location)

    sc_list = (
        args.scorecard_revisions.split(",")
        if getattr(args, "scorecard_revisions", None)
        else None
    )
    im_list = (
        args.issue_models.split(",")
        if getattr(args, "issue_models", None)
        else None
    )

    if sc_list:
        for rev in sc_list:
            try:
                r_obj = sc_client.get_revision(rev)
                state = r_obj.get("state")
                if state == "EDITABLE":
                    print(
                        f"[WARNING] Scorecard revision '{rev}' is in state 'EDITABLE' "
                        f"(Draft/Inactive). Triggering automatic tuning/activation so it "
                        f"can be evaluated by CCAI Insights..."
                    )
                    sc_client.tune_revision(rev)
                elif state != "READY":
                    print(
                        f"[WARNING] Scorecard revision '{rev}' is in state '{state}' (not READY)."
                    )
            except Exception as e:
                logging.debug(
                    "CLI scorecard revision state check failed for %s: %s",
                    rev,
                    e,
                )

    try:
        rule = ar_client.create_rule_for_app(
            display_name=args.display_name,
            app_name=getattr(args, "app_name", None),
            filter_str=getattr(args, "filter", None),
            scorecard_revisions=sc_list,
            issue_models=im_list,
            run_summarization=getattr(args, "run_summarization", True),
            run_sentiment=getattr(args, "run_sentiment", True),
            active=getattr(args, "active", True),
            parent=args.parent,
            rule_id=getattr(args, "rule_id", None),
        )
        print(f"Successfully created analysis rule: {rule['name']}")
    except Exception as e:
        print(f"Failed to create analysis rule: {e}")
        sys.exit(1)


def handle_activate_analysis_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights activate-analysis-rule' command."""
    print(f"Setting active={args.active} on rule {args.rule_name}...")
    from cxas_scrapi.core.analysis_rules import AnalysisRules

    project_id, location = _get_project_and_location_from_parent(args.rule_name)
    ar_client = AnalysisRules(project_id=project_id, location=location)
    try:
        ar_client.activate_analysis_rule(args.rule_name, active=args.active)
        print("Successfully updated rule status.")
    except Exception as e:
        print(f"Failed to activate analysis rule: {e}")
        sys.exit(1)


def handle_get_analysis_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights get-analysis-rule' command."""
    from cxas_scrapi.core.analysis_rules import AnalysisRules

    project_id, location = _get_project_and_location_from_parent(args.rule_name)
    ar_client = AnalysisRules(project_id=project_id, location=location)
    try:
        rule = ar_client.get_analysis_rule(args.rule_name)
        print(f"Rule: {rule['name']}")
        print(f"Display Name: {rule.get('displayName')}")
        print(f"Active: {rule.get('active')}")
        print(f"Filter: {rule.get('conversationFilter')}")
        print(f"Annotator Selector: {rule.get('annotatorSelector')}")
    except Exception as e:
        print(f"Failed to get analysis rule: {e}")
        sys.exit(1)


def handle_delete_analysis_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights delete-analysis-rule' command."""
    print(f"Deleting analysis rule {args.rule_name}...")
    from cxas_scrapi.core.analysis_rules import AnalysisRules

    project_id, location = _get_project_and_location_from_parent(args.rule_name)
    ar_client = AnalysisRules(project_id=project_id, location=location)
    try:
        ar_client.delete_analysis_rule(args.rule_name)
        print("Successfully deleted analysis rule.")
    except Exception as e:
        print(f"Failed to delete analysis rule: {e}")
        sys.exit(1)


# --- Smoke Test & Metrics Handlers ---


def handle_smoke_test_scorecard(args: argparse.Namespace) -> None:
    """Handles the 'insights smoke-test-scorecard' command."""
    print(f"Running smoke test for scorecard {args.scorecard_name}...")
    import json

    from cxas_scrapi.utils.insights_utils import InsightsUtils

    target_parent = getattr(args, "parent", None)
    if not target_parent and args.scorecard_name.startswith("projects/"):
        target_parent = "/".join(args.scorecard_name.split("/")[:4])

    if not target_parent:
        print(
            "Error: Must provide --parent if --scorecard-name is not full resource path."
        )
        sys.exit(1)

    project_id, location = _get_project_and_location_from_parent(target_parent)
    utils = InsightsUtils(project_id=project_id, location=location)

    conv_list = []
    if getattr(args, "conversations", None):
        conv_list.extend(
            [c.strip() for c in args.conversations.split(",") if c.strip()]
        )
    if getattr(args, "simulate_file", None):
        try:
            with open(args.simulate_file, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    conv_list.extend(data)
                elif isinstance(data, dict):
                    conv_list.append(data)
        except Exception as e:
            print(f"Failed to load simulate file: {e}")
            sys.exit(1)

    if not conv_list:
        print("Error: Must specify --conversations or --simulate-file.")
        sys.exit(1)

    try:
        results = utils.smoke_test_scorecard(
            args.scorecard_name, conv_list, parent=target_parent
        )
        print("\n--- Smoke Test Results ---")
        for r in results:
            conv_label = (
                r.get("conversation_name")
                or f"index #{r.get('conversation_index', 'unknown')}"
            )
            print(f"Conversation: {conv_label} | Status: {r.get('status')}")
            if r.get("error"):
                print(f"  Error: {r['error']}")
            elif r.get("qa_answer"):
                ans = r["qa_answer"]
                print(f"  Scorecard: {ans.get('qaScorecardRevision')}")
                for qa in ans.get("qaQuestions", []):
                    val = qa.get("answerValue", {}).get("strValue") or qa.get(
                        "answerValue", {}
                    ).get("numValue")
                    q_id = qa.get("qaQuestion", "").split("/")[-1]
                    print(
                        f"    Q: {q_id} => Answer: {val} (Score: {qa.get('score', 0)})"
                    )
    except Exception as e:
        print(f"Failed during smoke test: {e}")
        sys.exit(1)


def handle_analyze_metrics(args: argparse.Namespace) -> None:
    """Handles the 'insights analyze-metrics' command."""
    print(
        f"Aggregating insights metrics under {args.parent} (time_window={args.time_window})..."
    )
    import json

    from cxas_scrapi.utils.insights_analytics import InsightsAnalytics

    project_id, location = _get_project_and_location_from_parent(args.parent)
    analytics = InsightsAnalytics(project_id=project_id, location=location)

    try:
        report = analytics.aggregate_metrics(
            time_window=getattr(args, "time_window", "24h") or "24h",
            app_name=getattr(args, "app_name", None),
            custom_filter=getattr(args, "filter", None),
            parent=args.parent,
        )

        kpis = report.get("kpis", {})
        print("\n--- Insights Metrics Summary ---")
        print(f"Total Conversations : {kpis.get('total_conversations', 0)}")
        print(f"Average Turns       : {kpis.get('average_turns', 0)}")
        print(
            f"Average Duration    : {kpis.get('average_duration_seconds', 0)}s"
        )
        print(f"User Sentiment Avg  : {kpis.get('average_user_sentiment', 0)}")
        print(
            f"Scorecard Pass Rate : {kpis.get('overall_scorecard_pass_percentage', 0)}%\n"
        )

        if getattr(args, "json_output", None):
            with open(args.json_output, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"Saved JSON report to {args.json_output}")

        html_path = (
            getattr(args, "html_output", "insights_dashboard.html")
            or "insights_dashboard.html"
        )
        html_content = analytics.generate_html_dashboard(report)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Generated interactive HTML dashboard at: {html_path}")

    except Exception as e:
        print(f"Failed to analyze metrics: {e}")
        sys.exit(1)


def handle_apply(args: argparse.Namespace) -> None:
    """Handles the 'insights apply' command for declarative reconciliation."""
    print(f"Declaratively applying Insights configuration from: {args.config}")
    import json

    import yaml

    from cxas_scrapi.utils.insights_reconciler import InsightsReconciler
    from cxas_scrapi.utils.insights_utils import InsightsUtils

    # Read config to get default project and location
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    project_id = getattr(args, "project_id", None) or config.get("project_id")
    location = getattr(args, "location", None) or config.get(
        "location", "us-central1"
    )

    if not project_id:
        print(
            "Error: project_id must be specified in the config file or via --project_id."
        )
        sys.exit(1)

    utils = InsightsUtils(project_id=project_id, location=location)
    reconciler = InsightsReconciler(insights_utils=utils)

    try:
        result = reconciler.apply(args.config, dry_run=args.dry_run)
        print("\n--- Insights Reconciliation Result ---")
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"Failed to apply configuration: {e}")
        sys.exit(1)


def handle_diff(args: argparse.Namespace) -> None:
    """Handles the 'insights diff' command."""
    print(f"Calculating diff for Insights configuration: {args.config}")
    import json

    import yaml

    from cxas_scrapi.utils.insights_reconciler import InsightsReconciler
    from cxas_scrapi.utils.insights_utils import InsightsUtils

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    project_id = getattr(args, "project_id", None) or config.get("project_id")
    location = getattr(args, "location", None) or config.get(
        "location", "us-central1"
    )

    if not project_id:
        print(
            "Error: project_id must be specified in the config file or via --project_id."
        )
        sys.exit(1)

    utils = InsightsUtils(project_id=project_id, location=location)
    reconciler = InsightsReconciler(insights_utils=utils)

    try:
        diff_result = reconciler.diff(args.config)
        print("\n--- Insights Declarative Diff ---")
        print(json.dumps(diff_result, indent=2, default=str))
    except Exception as e:
        print(f"Failed to calculate diff: {e}")
        sys.exit(1)


def handle_eval(args: argparse.Namespace) -> None:
    """Handles the 'insights eval' command for rapid scorecard prompt testing."""
    calib_set = getattr(args, "calibration_set", None) or getattr(
        args, "goldens", None
    )
    print(
        f"Evaluating scorecard template {args.template} against calibration dataset {calib_set}..."
    )
    import json

    from cxas_scrapi.utils.scorecard_eval_runner import ScorecardEvalRunner

    try:
        with open(calib_set, encoding="utf-8") as f:
            if calib_set.endswith(".json") or calib_set.endswith(".json5"):
                calibration_cases = json.load(f)
            else:
                import yaml

                calibration_cases = yaml.safe_load(f)

        if not isinstance(calibration_cases, list):
            calibration_cases = [calibration_cases]

        runner = ScorecardEvalRunner(
            project_id=getattr(args, "project_id", "default-project")
            or "default-project",
            location=getattr(args, "location", "global") or "global",
            model_name=getattr(args, "model", "gemini-2.5-flash")
            or "gemini-2.5-flash",
        )

        report = runner.evaluate_scorecard_on_calibration_set(
            scorecard_template=args.template,
            calibration_dataset=calibration_cases,
        )

        print("\n--- Scorecard Evaluation Summary ---")
        print(f"Scorecard Display Name : {report.scorecard_display_name}")
        print(f"Conversations Evaluated: {report.total_conversations}")
        print(f"Total Evaluations      : {report.total_evaluations}")
        if report.overall_accuracy is not None:
            print(
                f"Overall Accuracy       : {report.overall_accuracy * 100:.1f}%"
            )

        print("\nPer-Question Breakdown:")
        for q_id, q_m in report.question_metrics.items():
            acc_str = (
                f"{q_m['accuracy'] * 100:.1f}%"
                if q_m["accuracy"] is not None
                else "N/A"
            )
            print(
                f"  [{q_id}] Accuracy: {acc_str} (Discrepancies: {q_m['discrepancies_count']})"
            )

        if report.discrepancies:
            print(f"\n⚠️  Discrepancies Found ({len(report.discrepancies)}):")
            for d in report.discrepancies[:5]:
                print(
                    f"  - Conv '{d.conversation_id}' on '{d.question_id}': Expected '{d.expected_answer}', Predicted '{d.predicted_answer}'"
                )
                print(f"    Rationale: {d.rationale}")

        if getattr(args, "output", None):
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)
            print(f"\nSaved evaluation report to: {args.output}")

    except Exception as e:
        print(f"Failed during scorecard evaluation: {e}")
        sys.exit(1)


def handle_report(args: argparse.Namespace) -> None:
    """Handles the 'insights report' command to export tidy DataFrames."""
    print(f"Extracting evaluation results from {args.parent}...")
    from cxas_scrapi.core.scorecards import Scorecards
    from cxas_scrapi.utils.metrics_extractor import MetricsExtractor

    project_id, location = _get_project_and_location_from_parent(args.parent)
    scorecards_client = Scorecards(project_id=project_id, location=location)
    extractor = MetricsExtractor(insights_client=scorecards_client)

    sc_names = (
        [s.strip() for s in args.scorecards.split(",")]
        if getattr(args, "scorecards", None)
        else None
    )

    try:
        df = extractor.get_evaluation_results(
            filter_str=getattr(args, "filter", None),
            scorecard_names=sc_names,
        )
        print(f"\nRetrieved {len(df)} evaluation records.")
        if not df.empty:
            print(df.head(10).to_string())

        output_path = getattr(args, "output", None)
        if output_path:
            if output_path.endswith(".csv"):
                df.to_csv(output_path, index=False)
            elif output_path.endswith(".json"):
                df.to_json(output_path, orient="records", indent=2)
            print(f"\nSaved extracted metrics to: {output_path}")

    except Exception as e:
        print(f"Failed to generate report: {e}")
        sys.exit(1)


def handle_pull_autolabel_rules(args: argparse.Namespace) -> None:
    """Handles the 'insights pull-autolabel-rules' command."""
    from cxas_scrapi.core.autolabel_sync import (
        dump_autolabel_rules_yaml,
        export_remote_rules_to_yaml_dict,
    )
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.parent)
    client = Insights(project_id=project_id, location=location)

    out_path = (
        getattr(args, "out", None)
        or getattr(args, "file", None)
        or "autolabel_rules.yaml"
    )
    print(f"Fetching autolabeling rules from {args.parent}...")
    try:
        rules = client.list_autolabeling_rules(parent=args.parent)
        data = export_remote_rules_to_yaml_dict(rules, project_id, location)
        dump_autolabel_rules_yaml(data, out_path)
        print(
            f"Successfully exported {len(rules)} autolabeling rule(s) to {out_path}"
        )
    except Exception as e:
        print(f"Failed to pull autolabeling rules: {e}")
        sys.exit(1)


def handle_diff_autolabel_rules(args: argparse.Namespace) -> None:
    """Handles the 'insights diff-autolabel-rules' command."""
    from cxas_scrapi.core.autolabel_sync import (
        diff_autolabel_rules,
        load_autolabel_rules_yaml,
    )
    from cxas_scrapi.core.insights import Insights

    file_path = getattr(args, "file", None) or "autolabel_rules.yaml"
    try:
        data = load_autolabel_rules_yaml(file_path)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        sys.exit(1)

    parent = getattr(args, "parent", None)
    if parent:
        project_id, location = _get_project_and_location_from_parent(parent)
    else:
        project_id = data.get("project_id")
        location = data.get("location")
        if not project_id or not location:
            print(
                "Error: Specify --parent or include project_id and location in YAML."
            )
            sys.exit(1)
        parent = f"projects/{project_id}/locations/{location}"

    client = Insights(project_id=project_id, location=location)
    print(f"Comparing local rules in '{file_path}' against {parent}...")
    try:
        remote_rules = client.list_autolabeling_rules(parent=parent)
        diff_res = diff_autolabel_rules(data, remote_rules)
        print(diff_res["report"])
    except Exception as e:
        print(f"Failed to diff autolabeling rules: {e}")
        sys.exit(1)


def handle_push_autolabel_rules(args: argparse.Namespace) -> None:
    """Handles the 'insights push-autolabel-rules' command."""
    from cxas_scrapi.core.autolabel_sync import (
        load_autolabel_rules_yaml,
        sync_autolabel_rules,
    )
    from cxas_scrapi.core.insights import Insights

    file_path = getattr(args, "file", None) or "autolabel_rules.yaml"
    try:
        data = load_autolabel_rules_yaml(file_path)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        sys.exit(1)

    parent = getattr(args, "parent", None)
    if parent:
        project_id, location = _get_project_and_location_from_parent(parent)
    else:
        project_id = data.get("project_id")
        location = data.get("location")
        if not project_id or not location:
            print(
                "Error: Specify --parent or include project_id and location in YAML."
            )
            sys.exit(1)
        parent = f"projects/{project_id}/locations/{location}"

    client = Insights(project_id=project_id, location=location)
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    action_label = "Dry-run syncing" if dry_run else "Syncing"
    print(f"{action_label} local rules in '{file_path}' to {parent}...")
    try:
        summary = sync_autolabel_rules(
            client=client,
            file_path=file_path,
            parent=parent,
            force=force,
            dry_run=dry_run,
        )
        if not dry_run:
            print("\n--- Sync Summary ---")
            created_str = (
                ", ".join(summary["created"]) if summary["created"] else "none"
            )
            updated_str = (
                ", ".join(summary["updated"]) if summary["updated"] else "none"
            )
            deleted_str = (
                ", ".join(summary["deleted"]) if summary["deleted"] else "none"
            )
            print(f"Created : {len(summary['created'])} ({created_str})")
            print(f"Updated : {len(summary['updated'])} ({updated_str})")
            print(f"Deleted : {len(summary['deleted'])} ({deleted_str})")
            if summary.get("skipped_delete"):
                print(
                    f"Skipped Deletions (use --force to delete) : {len(summary['skipped_delete'])}"
                )
        else:
            print(summary["diff_report"])
    except Exception as e:
        print(f"Failed to push autolabeling rules: {e}")
        sys.exit(1)


def handle_list_autolabel_rules(args: argparse.Namespace) -> None:
    """Handles the 'insights list-autolabel-rules' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.parent)
    client = Insights(project_id=project_id, location=location)

    print(f"Listing autolabeling rules under {args.parent}...")
    try:
        rules = client.list_autolabeling_rules(parent=args.parent)
        print(f"Found {len(rules)} rule(s):")
        for r in rules:
            rid = r.get("name", "").split("/")[-1]
            status = "ACTIVE" if r.get("active", True) else "INACTIVE"
            print(
                f" - [{status}] {rid}: '{r.get('displayName', '')}' (Key: {r.get('labelKey')}, Conditions: {len(r.get('conditions', []))})"
            )
    except Exception as e:
        print(f"Failed to list autolabeling rules: {e}")
        sys.exit(1)


def handle_get_autolabel_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights get-autolabel-rule' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.rule_name)
    client = Insights(project_id=project_id, location=location)

    try:
        rule = client.get_autolabeling_rule(args.rule_name)
        print(json.dumps(rule, indent=2))
    except Exception as e:
        print(f"Failed to get autolabeling rule: {e}")
        sys.exit(1)


def handle_delete_autolabel_rule(args: argparse.Namespace) -> None:
    """Handles the 'insights delete-autolabel-rule' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.rule_name)
    client = Insights(project_id=project_id, location=location)

    try:
        client.delete_autolabeling_rule(args.rule_name)
        print(f"Successfully deleted autolabeling rule: {args.rule_name}")
    except Exception as e:
        print(f"Failed to delete autolabeling rule: {e}")
        sys.exit(1)


def handle_pull_dashboards(args: argparse.Namespace) -> None:
    """Handles the 'insights pull-dashboards' command."""
    from cxas_scrapi.core.dashboard_sync import (
        dump_dashboards_yaml,
        export_remote_dashboards_to_yaml_dict,
    )
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.parent)
    client = Insights(project_id=project_id, location=location)

    out_file = getattr(args, "out", None) or "dashboards.yaml"
    print(f"Fetching configurable dashboards from {args.parent}...")
    try:
        remote_dashboards = client.list_dashboards(parent=args.parent)
        print(f"Found {len(remote_dashboards)} remote dashboard(s).")
        yaml_data = export_remote_dashboards_to_yaml_dict(
            remote_dashboards, project_id=project_id, location=location
        )
        dump_dashboards_yaml(yaml_data, out_file)
        print(
            f"Successfully exported {len(yaml_data.get('dashboards', []))} custom dashboard(s) to {out_file}"
        )
    except Exception as e:
        print(f"Failed to pull dashboards: {e}")
        sys.exit(1)


def handle_diff_dashboards(args: argparse.Namespace) -> None:
    """Handles the 'insights diff-dashboards' command."""
    from cxas_scrapi.core.dashboard_sync import (
        diff_dashboards,
        load_dashboards_yaml,
    )
    from cxas_scrapi.core.insights import Insights

    file_path = getattr(args, "file", None) or "dashboards.yaml"
    try:
        data = load_dashboards_yaml(file_path)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        sys.exit(1)

    parent = getattr(args, "parent", None)
    if parent:
        project_id, location = _get_project_and_location_from_parent(parent)
    else:
        project_id = data.get("project_id")
        location = data.get("location")
        if not project_id or not location:
            print(
                "Error: Specify --parent or include project_id and location in YAML."
            )
            sys.exit(1)
        parent = f"projects/{project_id}/locations/{location}"

    client = Insights(project_id=project_id, location=location)
    print(f"Comparing local dashboards in '{file_path}' against {parent}...")
    try:
        remote_dashboards = client.list_dashboards(parent=parent)
        diff_res = diff_dashboards(data, remote_dashboards)
        print(diff_res["report"])
    except Exception as e:
        print(f"Failed to diff dashboards: {e}")
        sys.exit(1)


def handle_push_dashboards(args: argparse.Namespace) -> None:
    """Handles the 'insights push-dashboards' command."""
    from cxas_scrapi.core.dashboard_sync import (
        load_dashboards_yaml,
        sync_dashboards,
    )
    from cxas_scrapi.core.insights import Insights

    file_path = getattr(args, "file", None) or "dashboards.yaml"
    try:
        data = load_dashboards_yaml(file_path)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        sys.exit(1)

    parent = getattr(args, "parent", None)
    if parent:
        project_id, location = _get_project_and_location_from_parent(parent)
    else:
        project_id = data.get("project_id")
        location = data.get("location")
        if not project_id or not location:
            print(
                "Error: Specify --parent or include project_id and location in YAML."
            )
            sys.exit(1)
        parent = f"projects/{project_id}/locations/{location}"

    client = Insights(project_id=project_id, location=location)
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    action_label = "Dry-run syncing" if dry_run else "Syncing"
    print(f"{action_label} local dashboards in '{file_path}' to {parent}...")
    try:
        summary = sync_dashboards(
            client=client,
            file_path=file_path,
            parent=parent,
            force=force,
            dry_run=dry_run,
        )
        if not dry_run:
            print("\n--- Sync Summary ---")
            created_str = (
                ", ".join(summary["created"]) if summary["created"] else "none"
            )
            updated_str = (
                ", ".join(summary["updated"]) if summary["updated"] else "none"
            )
            deleted_str = (
                ", ".join(summary["deleted"]) if summary["deleted"] else "none"
            )
            print(f"Created : {len(summary['created'])} ({created_str})")
            print(f"Updated : {len(summary['updated'])} ({updated_str})")
            print(f"Deleted : {len(summary['deleted'])} ({deleted_str})")
            if summary.get("skipped_delete"):
                print(
                    f"Skipped Deletions (use --force to delete) : {len(summary['skipped_delete'])}"
                )
    except Exception as e:
        print(f"Failed to push dashboards: {e}")
        sys.exit(1)


def handle_list_dashboards(args: argparse.Namespace) -> None:
    """Handles the 'insights list-dashboards' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(args.parent)
    client = Insights(project_id=project_id, location=location)

    print(f"Listing dashboards under {args.parent}...")
    try:
        dashboards = client.list_dashboards(parent=args.parent)
        print(f"Found {len(dashboards)} dashboard(s):")
        for d in dashboards:
            did = d.get("name", "").split("/")[-1]
            read_only = (
                "SYSTEM_READ_ONLY" if d.get("readOnly", False) else "CUSTOM"
            )
            num_tabs = len(d.get("rootContainer", {}).get("widgets", []))
            print(
                f" - [{read_only}] {did}: '{d.get('displayName', '')}' (Tabs: {num_tabs})"
            )
    except Exception as e:
        print(f"Failed to list dashboards: {e}")
        sys.exit(1)


def handle_get_dashboard(args: argparse.Namespace) -> None:
    """Handles the 'insights get-dashboard' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(
        args.dashboard_name
    )
    client = Insights(project_id=project_id, location=location)

    try:
        dashboard = client.get_dashboard(args.dashboard_name)
        print(json.dumps(dashboard, indent=2))
    except Exception as e:
        print(f"Failed to get dashboard: {e}")
        sys.exit(1)


def handle_delete_dashboard(args: argparse.Namespace) -> None:
    """Handles the 'insights delete-dashboard' command."""
    from cxas_scrapi.core.insights import Insights

    project_id, location = _get_project_and_location_from_parent(
        args.dashboard_name
    )
    client = Insights(project_id=project_id, location=location)

    try:
        client.delete_dashboard(args.dashboard_name)
        print(f"Successfully deleted dashboard: {args.dashboard_name}")
    except Exception as e:
        print(f"Failed to delete dashboard: {e}")
        sys.exit(1)


def populate_insights_parser(parser_insights: argparse.ArgumentParser) -> None:
    """Populates the provided insights parser with its subcommands."""

    insights_subparsers = parser_insights.add_subparsers(
        title="Insights Operations", dest="insights_command", required=True
    )

    # 1. 'list-scorecards' subcommand
    parser_list = insights_subparsers.add_parser(
        "list-scorecards", help="List all QA Scorecards under a parent."
    )
    parser_list.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_list.set_defaults(func=handle_list)

    # 2. 'export-scorecard-from-insights' subcommand
    parser_export = insights_subparsers.add_parser(
        "export-scorecard-from-insights",
        help="Export a Scorecard and its questions to a JSON/YAML template.",
    )
    parser_export.add_argument(
        "--scorecard_name",
        required=True,
        help="Full resource name of the scorecard (e.g. projects/*/locations/*/qaScorecards/*).",
    )
    parser_export.add_argument(
        "--template",
        required=True,
        help="Local path for the scorecard template file (.json, .yaml).",
    )
    parser_export.set_defaults(func=handle_export)

    # 3. 'import-scorecard-to-insights' subcommand
    parser_import = insights_subparsers.add_parser(
        "import-scorecard-to-insights",
        help="Import a JSON/YAML template as an editable SDK Scorecard revision.",
    )
    parser_import.add_argument(
        "--template",
        required=True,
        help="Local path to the scorecard template file (.json, .yaml).",
    )
    parser_import.add_argument(
        "--scorecard_name",
        help="Optional: Full resource name of an existing scorecard to overwrite. If omitted, --parent must be provided.",
    )
    parser_import.add_argument(
        "--parent",
        help="Optional: Parent resource name (projects/*/locations/*) to create a brand new scorecard under. If omitted, --scorecard_name must be provided.",
    )
    parser_import.set_defaults(func=handle_import)

    # 4. 'copy-scorecard' subcommand
    parser_copy = insights_subparsers.add_parser(
        "copy-scorecard",
        help="Copy a Scorecard's questions into a new destination Scorecard.",
    )
    parser_copy.add_argument(
        "--scorecard_name",
        required=True,
        help="Full resource name of the SOURCE scorecard.",
    )
    parser_copy.add_argument(
        "--dst_scorecard_name",
        help="Optional: Full resource name of the DESTINATION scorecard to overwrite. If omitted, --parent must be provided.",
    )
    parser_copy.add_argument(
        "--parent",
        help="Optional: Parent resource name to create a brand new copied scorecard under. If omitted, --dst_scorecard_name must be provided.",
    )
    parser_copy.set_defaults(func=handle_copy)

    # 5. 'create-scorecard' subcommand
    parser_create_sc = insights_subparsers.add_parser(
        "create-scorecard",
        help="Create a brand new Scorecard and optionally load questions from a template.",
    )
    parser_create_sc.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_create_sc.add_argument(
        "--scorecard-id", required=True, help="Unique ID for the scorecard."
    )
    parser_create_sc.add_argument(
        "--display-name", required=True, help="Human readable display name."
    )
    parser_create_sc.add_argument(
        "--description", help="Scorecard description."
    )
    parser_create_sc.add_argument(
        "--template", help="Optional JSON/YAML template with initial questions."
    )
    parser_create_sc.set_defaults(func=handle_create_scorecard)

    # 6. 'add-question' subcommand
    parser_add_q = insights_subparsers.add_parser(
        "add-question",
        help="Add a question to a scorecard revision.",
    )
    parser_add_q.add_argument(
        "--revision-name",
        required=True,
        help="Full resource name of scorecard revision.",
    )
    parser_add_q.add_argument(
        "--question-body", required=True, help="Question text."
    )
    parser_add_q.add_argument(
        "--answer-choices",
        help="Comma-separated choices and scores, e.g. 'Yes=1.0,No=0.0'.",
    )
    parser_add_q.add_argument(
        "--answer-instructions", help="Instructions/rubric for evaluators."
    )
    parser_add_q.add_argument(
        "--abbreviation", help="Short abbreviation label."
    )
    parser_add_q.set_defaults(func=handle_add_question)

    # 6a. 'activate-scorecard' and 'deploy-scorecard' subcommands
    for sc_cmd in ["activate-scorecard", "deploy-scorecard"]:
        parser_act_sc = insights_subparsers.add_parser(
            sc_cmd,
            help="Tune/activate and deploy a scorecard revision out of draft/EDITABLE state.",
        )
        parser_act_sc.add_argument(
            "--revision-name",
            required=True,
            help="Full resource name of scorecard revision.",
        )
        parser_act_sc.add_argument(
            "--timeout",
            type=int,
            default=120,
            help="Timeout in seconds to wait for READY state before deploying.",
        )
        parser_act_sc.add_argument(
            "--no-wait",
            dest="wait",
            action="store_false",
            help="Do not wait for READY state after tuning.",
        )
        parser_act_sc.set_defaults(func=handle_activate_scorecard)

    # 6b. 'validate-scorecard' subcommand
    parser_val_sc = insights_subparsers.add_parser(
        "validate-scorecard",
        help="Validate that a scorecard revision is READY and not in draft/EDITABLE state.",
    )
    parser_val_sc.add_argument(
        "--revision-name",
        required=True,
        help="Full resource name of scorecard revision.",
    )
    parser_val_sc.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero code if not in READY state.",
    )
    parser_val_sc.set_defaults(func=handle_validate_scorecard)

    # 7. 'list-topic-models' subcommand
    parser_list_tm = insights_subparsers.add_parser(
        "list-topic-models", help="List all topic models under a parent."
    )
    parser_list_tm.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_list_tm.set_defaults(func=handle_list_topic_models)

    # 8. 'create-topic-model' subcommand
    parser_create_tm = insights_subparsers.add_parser(
        "create-topic-model", help="Create a topic model on a CXAS app."
    )
    parser_create_tm.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_create_tm.add_argument(
        "--display-name", required=True, help="Topic model display name."
    )
    parser_create_tm.add_argument(
        "--app-name", help="CXAS app name to filter conversations on."
    )
    parser_create_tm.add_argument(
        "--filter", help="Custom conversation filter string."
    )
    parser_create_tm.add_argument(
        "--deploy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Immediately deploy model after creation.",
    )
    parser_create_tm.set_defaults(func=handle_create_topic_model)

    # 9. 'deploy-topic-model' & 'undeploy-topic-model' & 'get-topic-model' & 'list-topics'
    parser_deploy_tm = insights_subparsers.add_parser(
        "deploy-topic-model", help="Deploy a topic model."
    )
    parser_deploy_tm.add_argument(
        "--issue-model-name", required=True, help="Full issue model name."
    )
    parser_deploy_tm.set_defaults(func=handle_deploy_topic_model)

    parser_undeploy_tm = insights_subparsers.add_parser(
        "undeploy-topic-model", help="Undeploy a topic model."
    )
    parser_undeploy_tm.add_argument(
        "--issue-model-name", required=True, help="Full issue model name."
    )
    parser_undeploy_tm.set_defaults(func=handle_undeploy_topic_model)

    parser_get_tm = insights_subparsers.add_parser(
        "get-topic-model", help="Get topic model details."
    )
    parser_get_tm.add_argument(
        "--issue-model-name", required=True, help="Full issue model name."
    )
    parser_get_tm.add_argument(
        "--include-stats",
        action="store_true",
        help="Calculate and include model stats.",
    )
    parser_get_tm.set_defaults(func=handle_get_topic_model)

    parser_list_topics = insights_subparsers.add_parser(
        "list-topics", help="List detected topics in a model."
    )
    parser_list_topics.add_argument(
        "--issue-model-name", required=True, help="Full issue model name."
    )
    parser_list_topics.set_defaults(func=handle_list_topics)

    # 10. 'list-analysis-rules' & 'create-analysis-rule' & 'activate-analysis-rule' & 'get-analysis-rule' & 'delete-analysis-rule'
    parser_list_ar = insights_subparsers.add_parser(
        "list-analysis-rules", help="List analysis rules under a parent."
    )
    parser_list_ar.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_list_ar.set_defaults(func=handle_list_analysis_rules)

    parser_create_ar = insights_subparsers.add_parser(
        "create-analysis-rule", help="Create an automated analysis rule."
    )
    parser_create_ar.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_create_ar.add_argument(
        "--display-name", required=True, help="Rule display name."
    )
    parser_create_ar.add_argument("--app-name", help="Target CXAS app name.")
    parser_create_ar.add_argument("--filter", help="Conversation filter query.")
    parser_create_ar.add_argument(
        "--scorecard-revisions",
        help="Comma-separated scorecard revisions to evaluate automatically.",
    )
    parser_create_ar.add_argument(
        "--issue-models",
        help="Comma-separated issue models to run automatically.",
    )
    parser_create_ar.add_argument(
        "--run-summarization",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run automatic summarization.",
    )
    parser_create_ar.add_argument(
        "--run-sentiment",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run sentiment analysis.",
    )
    parser_create_ar.add_argument(
        "--active",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Activate rule immediately.",
    )
    parser_create_ar.add_argument("--rule-id", help="Optional rule ID.")
    parser_create_ar.set_defaults(func=handle_create_analysis_rule)

    parser_activate_ar = insights_subparsers.add_parser(
        "activate-analysis-rule", help="Activate or deactivate a rule."
    )
    parser_activate_ar.add_argument(
        "--rule-name", required=True, help="Full analysis rule name."
    )
    parser_activate_ar.add_argument(
        "--active",
        type=lambda x: str(x).lower() in ("true", "1", "yes"),
        required=True,
        help="true or false.",
    )
    parser_activate_ar.set_defaults(func=handle_activate_analysis_rule)

    parser_get_ar = insights_subparsers.add_parser(
        "get-analysis-rule", help="Get analysis rule details."
    )
    parser_get_ar.add_argument(
        "--rule-name", required=True, help="Full analysis rule name."
    )
    parser_get_ar.set_defaults(func=handle_get_analysis_rule)

    parser_del_ar = insights_subparsers.add_parser(
        "delete-analysis-rule", help="Delete an analysis rule."
    )
    parser_del_ar.add_argument(
        "--rule-name", required=True, help="Full analysis rule name."
    )
    parser_del_ar.set_defaults(func=handle_delete_analysis_rule)

    # 11. 'smoke-test-scorecard' subcommand
    parser_smoke = insights_subparsers.add_parser(
        "smoke-test-scorecard",
        help="Dry-run and smoke test a scorecard revision on conversations.",
    )
    parser_smoke.add_argument(
        "--scorecard-name",
        required=True,
        help="Full resource name of scorecard revision.",
    )
    parser_smoke.add_argument(
        "--conversations",
        help="Comma-separated conversation names or IDs to evaluate.",
    )
    parser_smoke.add_argument(
        "--simulate-file",
        help="Path to JSON file containing simulated conversation turns or transcripts.",
    )
    parser_smoke.add_argument("--parent", help="Parent resource name.")
    parser_smoke.set_defaults(func=handle_smoke_test_scorecard)

    # 12. 'analyze-metrics' subcommand
    parser_metrics = insights_subparsers.add_parser(
        "analyze-metrics",
        help="Aggregate metrics across time window and generate HTML dashboard.",
    )
    parser_metrics.add_argument(
        "--parent", required=True, help="Parent resource name."
    )
    parser_metrics.add_argument(
        "--time-window",
        default="24h",
        help="Time window filter (e.g. 1h, 24h, 7d, 30d, all).",
    )
    parser_metrics.add_argument(
        "--app-name", help="Filter by specific CXAS app name."
    )
    parser_metrics.add_argument(
        "--filter", help="Additional custom conversation filter string."
    )
    parser_metrics.add_argument(
        "--html-output",
        default="insights_dashboard.html",
        help="Output path for interactive HTML dashboard (default: insights_dashboard.html).",
    )
    parser_metrics.add_argument(
        "--json-output", help="Optional output path for JSON metrics report."
    )
    parser_metrics.set_defaults(func=handle_analyze_metrics)
    # 13. 'apply' subcommand for declarative reconciliation
    parser_apply = insights_subparsers.add_parser(
        "apply",
        help="Declaratively reconcile scorecards, rules, and backfills from a config file.",
    )
    parser_apply.add_argument(
        "--config",
        required=True,
        help="Path to the declarative YAML config file (e.g. insights_config.yaml).",
    )
    parser_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying live GCP resources.",
    )
    parser_apply.add_argument(
        "--project-id",
        dest="project_id",
        help="Optional override for GCP project ID.",
    )
    parser_apply.add_argument(
        "--location",
        help="Optional override for location (defaults to us-central1).",
    )
    parser_apply.set_defaults(func=handle_apply)

    # 14. 'diff' subcommand
    parser_diff = insights_subparsers.add_parser(
        "diff",
        help="Preview diff between local declarative config and live GCP state.",
    )
    parser_diff.add_argument(
        "--config",
        required=True,
        help="Path to the declarative YAML config file.",
    )
    parser_diff.add_argument(
        "--project-id",
        dest="project_id",
        help="Optional override for GCP project ID.",
    )
    parser_diff.add_argument(
        "--location",
        help="Optional override for location.",
    )
    parser_diff.set_defaults(func=handle_diff)

    # 15. 'eval' subcommand for rapid prompt testing against calibration datasets
    parser_eval = insights_subparsers.add_parser(
        "eval",
        help="Evaluate scorecard question prompts directly against QA calibration conversation datasets.",
    )
    parser_eval.add_argument(
        "--template",
        required=True,
        help="Path to local scorecard YAML/JSON template.",
    )
    parser_eval.add_argument(
        "--calibration-set",
        "--goldens",
        dest="calibration_set",
        required=True,
        help="Path to JSON/YAML file with QA calibration conversation cases.",
    )
    parser_eval.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model to use for evaluation (default: gemini-2.5-flash).",
    )
    parser_eval.add_argument(
        "--project-id",
        dest="project_id",
        help="GCP Project ID for Vertex AI evaluation.",
    )
    parser_eval.add_argument(
        "--location",
        default="global",
        help="Vertex AI location (default: global).",
    )
    parser_eval.add_argument(
        "--output",
        help="Optional path to save JSON evaluation report.",
    )
    parser_eval.set_defaults(func=handle_eval)

    # 16. 'report' subcommand
    parser_report = insights_subparsers.add_parser(
        "report",
        help="Extract and flatten evaluation results into a tidy DataFrame / CSV / JSON.",
    )
    parser_report.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (projects/*/locations/*).",
    )
    parser_report.add_argument(
        "--filter",
        help="Optional CEL conversation filter.",
    )
    parser_report.add_argument(
        "--scorecards",
        help="Optional comma-separated scorecard names to filter.",
    )
    parser_report.add_argument(
        "--output",
        help="Output file path (.csv or .json).",
    )
    parser_report.set_defaults(func=handle_report)

    # 17. AutoLabeling Rules subcommands
    parser_pull_al = insights_subparsers.add_parser(
        "pull-autolabel-rules",
        help="Export all autolabeling rules from project to a declarative YAML file.",
    )
    parser_pull_al.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_pull_al.add_argument(
        "--out",
        "--file",
        dest="out",
        default="autolabel_rules.yaml",
        help="Output YAML file path (default: autolabel_rules.yaml).",
    )
    parser_pull_al.set_defaults(func=handle_pull_autolabel_rules)

    parser_diff_al = insights_subparsers.add_parser(
        "diff-autolabel-rules",
        help="Compare local autolabel_rules.yaml against active rules in GCP.",
    )
    parser_diff_al.add_argument(
        "--file",
        default="autolabel_rules.yaml",
        help="Path to local autolabel_rules.yaml (default: autolabel_rules.yaml).",
    )
    parser_diff_al.add_argument(
        "--parent",
        help="Optional parent resource name override (e.g. projects/*/locations/*).",
    )
    parser_diff_al.set_defaults(func=handle_diff_autolabel_rules)

    parser_push_al = insights_subparsers.add_parser(
        "push-autolabel-rules",
        help="Deploy and sync local autolabel_rules.yaml to GCP project.",
    )
    parser_push_al.add_argument(
        "--file",
        default="autolabel_rules.yaml",
        help="Path to local autolabel_rules.yaml (default: autolabel_rules.yaml).",
    )
    parser_push_al.add_argument(
        "--parent",
        help="Optional parent resource name override (e.g. projects/*/locations/*).",
    )
    parser_push_al.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without creating, updating, or deleting remote rules.",
    )
    parser_push_al.add_argument(
        "--force",
        action="store_true",
        help="Delete remote rules that are missing from local YAML.",
    )
    parser_push_al.set_defaults(func=handle_push_autolabel_rules)

    parser_list_al = insights_subparsers.add_parser(
        "list-autolabel-rules",
        help="List autolabeling rules under a parent project/location.",
    )
    parser_list_al.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_list_al.set_defaults(func=handle_list_autolabel_rules)

    parser_get_al = insights_subparsers.add_parser(
        "get-autolabel-rule",
        help="Get full details of a specific autolabeling rule.",
    )
    parser_get_al.add_argument(
        "--rule-name",
        required=True,
        help="Full resource name of the autolabeling rule.",
    )
    parser_get_al.set_defaults(func=handle_get_autolabel_rule)

    parser_del_al = insights_subparsers.add_parser(
        "delete-autolabel-rule",
        help="Delete an autolabeling rule by resource name.",
    )
    parser_del_al.add_argument(
        "--rule-name",
        required=True,
        help="Full resource name of the autolabeling rule to delete.",
    )
    parser_del_al.set_defaults(func=handle_delete_autolabel_rule)

    # 14. Configurable Dashboard subcommands
    parser_pull_dash = insights_subparsers.add_parser(
        "pull-dashboards",
        help="Export all custom configurable dashboards from project to a declarative YAML file.",
    )
    parser_pull_dash.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_pull_dash.add_argument(
        "--out",
        "--file",
        dest="out",
        default="dashboards.yaml",
        help="Output YAML file path (default: dashboards.yaml).",
    )
    parser_pull_dash.set_defaults(func=handle_pull_dashboards)

    parser_diff_dash = insights_subparsers.add_parser(
        "diff-dashboards",
        help="Compare local dashboards.yaml against active dashboards in GCP.",
    )
    parser_diff_dash.add_argument(
        "--file",
        default="dashboards.yaml",
        help="Path to local dashboards.yaml (default: dashboards.yaml).",
    )
    parser_diff_dash.add_argument(
        "--parent",
        help="Optional parent resource name override (e.g. projects/*/locations/*).",
    )
    parser_diff_dash.set_defaults(func=handle_diff_dashboards)

    parser_push_dash = insights_subparsers.add_parser(
        "push-dashboards",
        help="Deploy and sync local dashboards.yaml to GCP project.",
    )
    parser_push_dash.add_argument(
        "--file",
        default="dashboards.yaml",
        help="Path to local dashboards.yaml (default: dashboards.yaml).",
    )
    parser_push_dash.add_argument(
        "--parent",
        help="Optional parent resource name override (e.g. projects/*/locations/*).",
    )
    parser_push_dash.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without creating, updating, or deleting remote dashboards.",
    )
    parser_push_dash.add_argument(
        "--force",
        action="store_true",
        help="Delete remote custom dashboards that are missing from local YAML.",
    )
    parser_push_dash.set_defaults(func=handle_push_dashboards)

    parser_list_dash = insights_subparsers.add_parser(
        "list-dashboards",
        help="List configurable dashboards under a parent project/location.",
    )
    parser_list_dash.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_list_dash.set_defaults(func=handle_list_dashboards)

    parser_get_dash = insights_subparsers.add_parser(
        "get-dashboard",
        help="Get full details of a specific configurable dashboard.",
    )
    parser_get_dash.add_argument(
        "--dashboard-name",
        required=True,
        help="Full resource name of the dashboard.",
    )
    parser_get_dash.set_defaults(func=handle_get_dashboard)

    parser_del_dash = insights_subparsers.add_parser(
        "delete-dashboard",
        help="Delete a configurable dashboard by resource name.",
    )
    parser_del_dash.add_argument(
        "--dashboard-name",
        required=True,
        help="Full resource name of the dashboard to delete.",
    )
    parser_del_dash.set_defaults(func=handle_delete_dashboard)
