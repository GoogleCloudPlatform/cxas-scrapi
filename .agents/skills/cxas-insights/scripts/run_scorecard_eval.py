#!/usr/bin/env python3
"""CLI Script to run rapid scorecard question evaluation against golden conversations."""

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

import yaml

from cxas_scrapi.utils.scorecard_eval_runner import ScorecardEvalRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate scorecard question prompts directly against golden conversation datasets."
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to scorecard YAML/JSON template file.",
    )
    parser.add_argument(
        "--goldens",
        required=True,
        help="Path to JSON or YAML file containing golden conversation test cases.",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model name to use for evaluation (default: gemini-2.5-flash).",
    )
    parser.add_argument(
        "--project-id",
        dest="project_id",
        default="default-project",
        help="Google Cloud Project ID for Vertex AI evaluation.",
    )
    parser.add_argument(
        "--location",
        default="global",
        help="Vertex AI location (default: global).",
    )
    parser.add_argument(
        "--output",
        help="Optional path to output the JSON evaluation report.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    # Load calibration dataset
    try:
        with open(args.calibration_set, encoding="utf-8") as f:
            if args.calibration_set.endswith(
                ".json"
            ) or args.calibration_set.endswith(".json5"):
                calibration_cases = json.load(f)
            else:
                calibration_cases = yaml.safe_load(f)
    except Exception as e:
        print(
            f"Error loading calibration dataset from {args.calibration_set}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(calibration_cases, list):
        calibration_cases = [calibration_cases]

    print(f"Loaded {len(calibration_cases)} QA calibration conversation cases.")
    print(f"Evaluating template: {args.template} with model: {args.model}")

    runner = ScorecardEvalRunner(
        project_id=args.project_id,
        location=args.location,
        model_name=args.model,
    )

    try:
        report = runner.evaluate_scorecard_on_calibration_set(
            scorecard_template=args.template,
            calibration_dataset=calibration_cases,
        )
    except Exception as e:
        print(f"Evaluation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print summary report
    print("\n==================================================")
    print(f"📊 Scorecard Evaluation Report: {report.scorecard_display_name}")
    print("==================================================")
    print(f"Total Conversations Evaluated : {report.total_conversations}")
    print(f"Total Question Evaluations    : {report.total_evaluations}")
    if report.overall_accuracy is not None:
        print(
            f"Overall Accuracy / Agreement  : {report.overall_accuracy * 100:.1f}%"
        )

    print("\n📋 Per-Question Breakdown:")
    for q_id, q_m in report.question_metrics.items():
        acc_str = (
            f"{q_m['accuracy'] * 100:.1f}%"
            if q_m["accuracy"] is not None
            else "N/A"
        )
        print(f"  • [{q_id}]")
        print(f"    - Accuracy     : {acc_str}")
        print(f"    - Evaluated    : {q_m['total_evaluated']}")
        print(f"    - Discrepancies: {q_m['discrepancies_count']}")

    if report.discrepancies:
        print(
            f"\n⚠️  Discrepancies & Disagreements ({len(report.discrepancies)}):"
        )
        for d in report.discrepancies:
            print(
                f"  - Case ID '{d.conversation_id}' on Question '{d.question_id}':"
            )
            print(f"    Expected:  {d.expected_answer}")
            print(f"    Predicted: {d.predicted_answer}")
            print(f"    Rationale: {d.rationale}")

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)
            print(f"\n✅ Saved detailed report to: {args.output}")
        except Exception as e:
            print(f"Failed to write output report: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
