#!/usr/bin/env python3
"""CLI Script to declaratively diff or apply Insights configurations."""

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

from cxas_scrapi.utils.insights_reconciler import InsightsReconciler
from cxas_scrapi.utils.insights_utils import InsightsUtils


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Declaratively reconcile CCAI Insights scorecards, rules, and backfills."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the declarative YAML config file (e.g. insights_config.yaml).",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Preview diff between local configuration and live GCP state.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to live GCP environment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run when applying (same as --diff).",
    )
    parser.add_argument(
        "--project-id",
        dest="project_id",
        help="Optional override for GCP project ID.",
    )
    parser.add_argument(
        "--location",
        help="Optional override for Insights location (defaults to config or us-central1).",
    )
    parser.add_argument(
        "--output",
        help="Optional path to output the result as JSON.",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.diff and not args.apply:
        print("Error: Must specify either --diff or --apply.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Failed to read config file {args.config}: {e}", file=sys.stderr)
        sys.exit(1)

    project_id = args.project_id or config.get("project_id")
    location = args.location or config.get("location", "us-central1")

    if not project_id:
        print(
            "Error: project_id must be specified in config or via --project_id.",
            file=sys.stderr,
        )
        sys.exit(1)

    utils = InsightsUtils(project_id=project_id, location=location)
    reconciler = InsightsReconciler(insights_utils=utils)

    if args.diff or args.dry_run:
        print(
            f"Calculating diff for {args.config} in {project_id}/{location}..."
        )
        diff_res = reconciler.diff(args.config)
        print("\n==================================================")
        print("🔍 Declarative Insights Diff Preview")
        print("==================================================")
        print(json.dumps(diff_res, indent=2, default=str))
        result_data = diff_res
    else:
        print(
            f"Applying configuration {args.config} in {project_id}/{location}..."
        )
        apply_res = reconciler.apply(args.config, dry_run=False)
        print("\n==================================================")
        print("✅ Declarative Insights Reconciliation Summary")
        print("==================================================")
        print(json.dumps(apply_res, indent=2, default=str))
        result_data = apply_res

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2, default=str)
            print(f"\nSaved execution summary to: {args.output}")
        except Exception as e:
            print(f"Failed to write output file: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
