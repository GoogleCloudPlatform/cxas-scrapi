#!/usr/bin/env python3
"""CLI Script to test a scorecard template live against a set of real Insights conversations."""

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

import pandas as pd

import cxas_scrapi.utils.scorecard_template_manager as template_manager
from cxas_scrapi.core.insights import Insights
from cxas_scrapi.utils.insights_utils import InsightsUtils


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy a draft scorecard template and run real live Insights QA analysis against selected conversations."
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Google Cloud Project ID.",
    )
    parser.add_argument(
        "--location",
        default="us-central1",
        help="Contact Center Insights location (e.g. us, us-central1).",
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to scorecard YAML/JSON template file.",
    )
    parser.add_argument(
        "--conversations",
        help="Comma-separated list of conversation IDs or resource names.",
    )
    parser.add_argument(
        "--conversations-file",
        help="Path to JSON file containing conversation objects or list of IDs.",
    )
    parser.add_argument(
        "--filter",
        help="Optional CEL filter to query conversations directly from Insights.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of conversations to analyze if querying by filter (default: 5).",
    )
    parser.add_argument(
        "--output",
        help="Optional output file path for evaluation results (.csv, .json, .md).",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # 1. Initialize Clients
    utils = InsightsUtils(project_id=args.project_id, location=args.location)
    insights_client = Insights(
        project_id=args.project_id, location=args.location
    )

    # 2. Resolve Target Conversations
    convo_names: list[str] = []
    if args.conversations:
        for c in args.conversations.split(","):
            c_clean = c.strip()
            if not c_clean:
                continue
            if c_clean.startswith("projects/"):
                convo_names.append(c_clean)
            else:
                convo_names.append(
                    f"{insights_client.parent}/conversations/{c_clean}"
                )

    elif args.conversations_file:
        try:
            with open(args.conversations_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        convo_names.append(
                            item
                            if item.startswith("projects/")
                            else f"{insights_client.parent}/conversations/{item}"
                        )
                    elif isinstance(item, dict):
                        name = (
                            item.get("conversation_name")
                            or item.get("name")
                            or item.get("conversation_id")
                            or item.get("id")
                        )
                        if name:
                            convo_names.append(
                                name
                                if name.startswith("projects/")
                                else f"{insights_client.parent}/conversations/{name}"
                            )
        except Exception as e:
            print(f"Failed to read conversations file: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.filter:
        print(
            f"Querying conversations from Insights matching filter: '{args.filter}' (limit={args.limit})..."
        )
        try:
            convos = insights_client.list_conversations(
                filter_str=args.filter, view="FULL"
            )
            sampled = convos[: args.limit]
            for c in sampled:
                convo_names.append(c["name"])
        except Exception as e:
            print(
                f"Failed to query conversations from Insights: {e}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print(
            "Error: Must specify --conversations, --conversations-file, or --filter.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not convo_names:
        print(
            "Error: No target conversations found to analyze.", file=sys.stderr
        )
        sys.exit(1)

    print(f"Selected {len(convo_names)} conversations for evaluation:")
    for idx, c_name in enumerate(convo_names):
        print(f"  {idx + 1}. {c_name.split('/')[-1]}")

    # 3. Load & Import Scorecard Template
    print(f"\nLoading scorecard template: {args.template}...")
    try:
        scorecard_dict, questions = template_manager.load_scorecard_template(
            args.template
        )
        display_name = scorecard_dict.get("displayName", "Custom Scorecard")
        sc_id = "sc-live-test-" + display_name.lower().replace(" ", "-")[:20]

        print(
            f"Syncing scorecard '{display_name}' ({len(questions)} questions) to Insights..."
        )
        revision_name = utils.import_scorecard(
            scorecard_dict=scorecard_dict,
            questions=questions,
            target_scorecard_id=sc_id,
        )
        print(f"✅ Scorecard synced to revision: {revision_name}")
    except Exception as e:
        print(f"Failed to sync scorecard to Insights: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Execute Real Insights QA Smoke Test / Analysis
    print(
        f"\nTriggering real Insights QA evaluation on {len(convo_names)} conversations..."
    )
    try:
        results = utils.smoke_test_scorecard(
            scorecard_revision=revision_name,
            conversations=convo_names,
            parent=insights_client.parent,
        )

    except Exception as e:
        print(
            f"Failed during Insights analysis execution: {e}", file=sys.stderr
        )
        sys.exit(1)

    # 5. Extract & Flatten Results for Human Inspection
    rows = []
    for r in results:
        convo_name = r.get("conversation_name", "")
        convo_id = convo_name.split("/")[-1]
        qa_answer = r.get("qa_answer", {})
        qa_questions = qa_answer.get("qaAnswers", []) or qa_answer.get(
            "qaQuestions", []
        )

        if not qa_questions and r.get("error"):
            rows.append(
                {
                    "conversation_id": convo_id,
                    "question": "ERROR",
                    "answer": "N/A",
                    "score": 0.0,
                    "rationale": str(r.get("error")),
                }
            )
            continue

        for qa in qa_questions:
            q_id = qa.get("qaQuestion", "").split("/")[-1]
            q_body = qa.get("questionBody", q_id)
            val_obj = qa.get("answerValue", {})
            ans_str = (
                val_obj.get("key") or val_obj.get("strValue") or str(val_obj)
                if isinstance(val_obj, dict)
                else str(val_obj)
            )
            score = (
                val_obj.get("score")
                if isinstance(val_obj, dict) and "score" in val_obj
                else qa.get("score", 0.0)
            )

            # Extract rationale if available in call metadata or qaAnswer
            rat_obj = (
                val_obj.get("rationale") if isinstance(val_obj, dict) else None
            )
            if isinstance(rat_obj, dict):
                rationale = rat_obj.get("rationale", "")
            elif isinstance(rat_obj, str):
                rationale = rat_obj
            else:
                rationale = str(
                    qa.get("rationale") or qa.get("answerRationale") or ""
                )

            rows.append(
                {
                    "conversation_id": convo_id,
                    "question": q_body,
                    "answer": ans_str,
                    "score": score,
                    "rationale": rationale,
                }
            )

    df = pd.DataFrame(rows)

    # 6. Display Structured Review Table
    print(
        "\n================================================================================"
    )
    print(f"📊 LIVE INSIGHTS EVALUATION RESULTS: {display_name}")
    print(
        "================================================================================"
    )
    if df.empty:
        print("No evaluation records returned.")
    else:
        # Group by conversation and display
        for c_id, group in df.groupby("conversation_id"):
            print(f"\n🗣️ Conversation: {c_id}")
            print(
                "--------------------------------------------------------------------------------"
            )
            for _, row in group.iterrows():
                score_badge = (
                    f"[{row['score']}]" if row["score"] is not None else ""
                )
                print(f"  • Q: {row['question']}")
                print(f"    Answer: {row['answer']} {score_badge}")
                if row["rationale"]:
                    print(f"    Rationale: {row['rationale']}")

    if args.output:
        try:
            if args.output.endswith(".csv"):
                df.to_csv(args.output, index=False)
            elif args.output.endswith(".json"):
                df.to_json(args.output, orient="records", indent=2)
            elif args.output.endswith(".md"):
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(
                        f"# Live Insights Evaluation Results: {display_name}\n\n"
                    )
                    headers = list(df.columns)
                    f.write("| " + " | ".join(headers) + " |\n")
                    f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
                    for _, row in df.iterrows():
                        clean_row = [
                            str(row[h]).replace("\n", " ").replace("|", "\\|")
                            for h in headers
                        ]
                        f.write("| " + " | ".join(clean_row) + " |\n")
            print(f"\n✅ Saved evaluation results table to: {args.output}")
        except Exception as e:
            print(f"Failed to write output: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
