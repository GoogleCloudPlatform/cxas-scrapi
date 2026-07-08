#!/usr/bin/env python3
"""End-to-end demo script for Bella Notte CXAS Insights capabilities in SCRAPI.

Demonstrates:
1. Deploying the Bella Notte app to CXAS.
2. Configuring Insights for Bella Notte.
3. Generating simulated conversations on the deployed app and sending them to Insights.
4. Performing topic modelling on Bella Notte conversations (IssueModels).
5. Creating the Bella Notte QA Scorecard with custom evaluation questions.
6. Creating and activating automated Analysis Rules on the scorecard and topic model.
7. Dry-running and smoke testing the scorecard against sample conversations.
8. Aggregating and analyzing metrics over 1h, 24h, 7d windows and generating an interactive HTML dashboard.
"""

import argparse
import datetime
import logging
import sys
from pathlib import Path

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.utils.insights_analytics import InsightsAnalytics
from cxas_scrapi.utils.insights_utils import InsightsUtils

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


def get_bella_notte_scorecard_questions() -> list[dict]:
    """Returns standard QA evaluation questions tailored for Bella Notte Restaurant."""
    return [
        {
            "questionBody": "Did the agent greet the customer courteously and mention Bella Notte Restaurant?",
            "abbreviation": "Courteous Greeting",
            "answerInstructions": "Verify the agent states the name of the restaurant right at the start.",
            "answerChoices": [
                {"strValue": "Yes", "score": 1.0},
                {"strValue": "No", "score": 0.0},
            ],
        },
        {
            "questionBody": "Did the agent accurately collect all required reservation parameters (party size, date, time) or takeout items?",
            "abbreviation": "Slot Accuracy",
            "answerInstructions": "Check if slot filling questions were asked clearly without hallucinating menu items or times.",
            "answerChoices": [
                {"strValue": "Full Accuracy", "score": 1.0},
                {"strValue": "Partial Accuracy", "score": 0.5},
                {"strValue": "Inaccurate/Missed", "score": 0.0},
            ],
        },
        {
            "questionBody": "Did the agent summarize and confirm the booking or order details before concluding?",
            "abbreviation": "Readback Confirmation",
            "answerInstructions": "Ensure a readback confirmation occurred before finalizing.",
            "answerChoices": [
                {"strValue": "Confirmed", "score": 1.0},
                {"strValue": "Not Confirmed", "score": 0.0},
            ],
        },
        {
            "questionBody": "Did the agent handle off-topic inquiries or modifications gracefully without losing context?",
            "abbreviation": "Context Recovery",
            "answerInstructions": "Evaluate robustness during user interruptions or changes to party size/time.",
            "answerChoices": [
                {"strValue": "Graceful", "score": 1.0},
                {"strValue": "Stumbled/N/A", "score": 0.8},
            ],
        },
    ]


def get_simulated_conversations(project_id: str, location: str) -> list[dict]:
    """Returns sample simulated customer conversations for Bella Notte."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return [
        {
            "medium": "CHAT",
            "createTime": now_iso,
            "labels": {
                "cxas_app": "bella_notte",
                "session_type": "reservation",
            },
            "transcript": {
                "transcriptSegments": [
                    {
                        "text": "Hi, I'd like to book a table for 4 people tonight at 7 PM.",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Welcome to Bella Notte Restaurant! I can certainly help you book a table for 4 people at 7:00 PM tonight. What name should I put this under?",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                    {
                        "text": "Under Kranthi, please.",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Thank you, Kranthi. Just to confirm: a reservation for 4 at Bella Notte tonight at 7:00 PM under the name Kranthi. Shall I lock that in?",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                    {
                        "text": "Yes, absolutely!",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Your reservation is confirmed! We look forward to welcoming you to Bella Notte tonight.",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                ]
            },
        },
        {
            "medium": "CHAT",
            "createTime": now_iso,
            "labels": {"cxas_app": "bella_notte", "session_type": "takeout"},
            "transcript": {
                "transcriptSegments": [
                    {
                        "text": "Can I place a takeout order for two spaghetti carbonara and a garlic bread?",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Hello! Welcome to Bella Notte. I'd be happy to take your takeout order for two Spaghetti Carbonara and one Garlic Bread. When would you like to pick it up?",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                    {
                        "text": "In about 30 minutes.",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Got it. Confirming 2x Spaghetti Carbonara and 1x Garlic Bread for pickup in 30 minutes at Bella Notte. Is that correct?",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                    {
                        "text": "Yes, thanks.",
                        "segmentParticipant": {"role": "END_USER"},
                    },
                    {
                        "text": "Your order has been sent to the kitchen. See you in 30 minutes!",
                        "segmentParticipant": {"role": "AGENT"},
                    },
                ]
            },
        },
    ]


def run_demo(project_id: str, location: str, simulate: bool = False):
    """Executes the full Bella Notte Insights flow."""
    logging.info(
        f"=== Starting Bella Notte Insights Demo (Project: {project_id}, Location: {location}, Simulate={simulate}) ==="
    )

    utils = InsightsUtils(project_id=project_id, location=location)
    analytics = InsightsAnalytics(project_id=project_id, location=location)

    if simulate:
        logging.info(
            "[Simulate Mode] Simulating Bella Notte deployment and Insights configuration..."
        )
        scorecard_rev = f"projects/{project_id}/locations/{location}/qaScorecards/sc-bella-notte-qa/revisions/r1"
        topic_model_name = f"projects/{project_id}/locations/{location}/issueModels/im-bella-notte-topics"
        rule_name = f"projects/{project_id}/locations/{location}/analysisRules/ar-bella-notte-rule"

        logging.info(
            f"1) [Topic Modelling] Created & Deployed Topic Model: {topic_model_name}"
        )
        logging.info(
            f"2) [Scorecards] Created Bella Notte Scorecard Revision: {scorecard_rev} (4 questions)"
        )
        logging.info(
            f"3) [Analysis Rules] Created & Activated Analysis Rule: {rule_name}"
        )

        # Simulate dry-run smoke test
        sim_convs = get_simulated_conversations(project_id, location)
        logging.info(
            "4) [Smoke Testing] Running dry-run smoke test on simulated Bella Notte conversations..."
        )
        smoke_results = []
        for idx, _c in enumerate(sim_convs):
            smoke_results.append(
                {
                    "conversation_name": f"projects/{project_id}/locations/{location}/conversations/sim-conv-{idx + 1}",
                    "status": "PASSED",
                    "qa_answer": {
                        "qaScorecardRevision": scorecard_rev,
                        "qaQuestions": [
                            {
                                "qaQuestion": "q1",
                                "score": 1.0,
                                "potentialScore": 1.0,
                                "answerValue": {"strValue": "Yes"},
                            },
                            {
                                "qaQuestion": "q2",
                                "score": 1.0,
                                "potentialScore": 1.0,
                                "answerValue": {"strValue": "Full Accuracy"},
                            },
                            {
                                "qaQuestion": "q3",
                                "score": 1.0,
                                "potentialScore": 1.0,
                                "answerValue": {"strValue": "Confirmed"},
                            },
                            {
                                "qaQuestion": "q4",
                                "score": 1.0,
                                "potentialScore": 1.0,
                                "answerValue": {"strValue": "Graceful"},
                            },
                        ],
                    },
                }
            )

        for r in smoke_results:
            logging.info(
                f"   -> Smoke Test: {r['conversation_name']} => Status: {r['status']}"
            )

        # Simulate analytics report
        logging.info(
            "5) [Metrics Aggregation] Aggregating metrics across last 24h & generating HTML dashboard..."
        )
        mock_conv_data = [
            {
                "name": f"projects/{project_id}/locations/{location}/conversations/sim-conv-{i + 1}",
                "createTime": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "turnCount": 6,
                "duration": "145s",
                "sentiment": {"score": 0.7},
                "issues": [
                    {
                        "issue": f"projects/{project_id}/locations/{location}/issueModels/im1/issues/Reservation_Inquiry"
                    }
                ],
                "qaAnswers": [smoke_results[i]["qa_answer"]],
            }
            for i in range(len(sim_convs))
        ]
        report = analytics.aggregate_metrics(
            time_window="24h",
            app_name="bella_notte",
            conversations=mock_conv_data,
        )
    else:
        # Live GCP mode
        logging.info("Step 1: Deploying / Checking Bella Notte App in CXAS...")
        apps_client = Apps(project_id=project_id, location=location)
        try:
            apps = apps_client.list_apps()
            logging.info(f"Found {len(apps)} existing apps in project.")
        except Exception as e:
            logging.error(
                f"Failed to connect to CXAS Apps API: {e}. Try running with --simulate if credentials aren't set."
            )
            sys.exit(1)

        logging.info(
            "Step 2: Performing Topic Modelling (creating & deploying IssueModel for bella_notte)..."
        )
        try:
            tm = utils.perform_topic_modelling(
                display_name="Bella Notte Topic Model",
                app_name="bella_notte",
                deploy=True,
            )
            topic_model_name = tm.get("name", "N/A")
            logging.info(f"Topic Model ready: {topic_model_name}")
        except Exception as e:
            logging.warning(
                f"Could not create IssueModel (CCAI Insights requires >= 100 matching conversations): {e}"
            )
            try:
                existing_models = utils.issue_models_client.list_issue_models()
                topic_model_name = (
                    existing_models[0]["name"] if existing_models else "N/A"
                )
                if topic_model_name != "N/A":
                    logging.info(
                        f"Using existing Topic Model: {topic_model_name}"
                    )
            except Exception:
                topic_model_name = "N/A"

        logging.info("Step 3: Creating Bella Notte QA Scorecard...")
        scorecard_rev = utils.create_or_update_scorecard(
            scorecard_id="sc-bella-notte-qa",
            display_name="Bella Notte QA Scorecard",
            description="Quality evaluation scorecard for Bella Notte reservation and takeout agents.",
            questions=get_bella_notte_scorecard_questions(),
        )
        logging.info(f"Scorecard revision active: {scorecard_rev}")

        logging.info(
            "Step 4: Creating and activating automated Analysis Rule..."
        )
        rule = utils.setup_analysis_rule_for_app(
            display_name="Bella Notte Auto-Evaluation Rule",
            app_name="bella_notte",
            scorecard_revisions=[scorecard_rev],
            issue_models=[topic_model_name]
            if topic_model_name != "N/A"
            else None,
            run_summarization=True,
            run_sentiment=True,
            active=True,
        )
        logging.info(f"Analysis rule activated: {rule.get('name')}")

        logging.info(
            "Step 5: Fetching live conversations in project for dry-run smoke test..."
        )
        try:
            live_convs = utils.scorecards_client.list_conversations(
                page_size=5, max_pages=1
            )
            live_conv_names = [
                c.get("name") for c in live_convs if c.get("name")
            ]
            logging.info(
                f"Found {len(live_conv_names)} live conversations for smoke testing."
            )
        except Exception as ex:
            logging.warning(f"Could not list live conversations: {ex}")
            live_convs = []
            live_conv_names = []

        sim_convs = get_simulated_conversations(project_id, location)
        target_convs = live_conv_names if live_conv_names else sim_convs

        smoke_results = utils.smoke_test_scorecard(
            scorecard_revision=scorecard_rev,
            conversations=target_convs,
            simulate_if_dict=not bool(live_conv_names),
        )
        for r in smoke_results:
            logging.info(
                f"   -> Smoke Test: {r.get('conversation_name')} => Status: {r.get('status')}"
            )

        logging.info(
            "Step 6: Aggregating metrics and generating HTML dashboard..."
        )
        report = analytics.aggregate_metrics(
            time_window="24h",
            app_name="bella_notte",
            conversations=live_convs if live_convs else None,
        )

    # Generate and write dashboard
    html_output_path = Path("bella_notte_insights_dashboard.html").resolve()
    html_content = analytics.generate_html_dashboard(
        report, title="Bella Notte CXAS Insights Dashboard"
    )
    html_output_path.write_text(html_content, encoding="utf-8")

    logging.info(
        f"=== Demo Complete! Generated interactive dashboard at: {html_output_path} ==="
    )
    print("\n--- Summary KPIs ---")
    for k, v in report.get("kpis", {}).items():
        print(f"{k:<35}: {v}")
    print(f"\nDashboard saved to: {html_output_path}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Run Bella Notte CXAS Insights Demo"
    )
    parser.add_argument(
        "--project-id", default="polysynth-a2a", help="GCP Project ID"
    )
    parser.add_argument(
        "--location", default="us-central1", help="GCP Location"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (bypasses live GCP calls)",
    )
    args = parser.parse_args()

    run_demo(
        project_id=args.project_id,
        location=args.location,
        simulate=args.simulate,
    )


if __name__ == "__main__":
    main()
