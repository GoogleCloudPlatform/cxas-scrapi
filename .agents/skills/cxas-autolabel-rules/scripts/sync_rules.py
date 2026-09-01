#!/usr/bin/env python3
"""Helper script to sync declarative autolabel rules with CCAI Insights."""

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
import sys

from cxas_scrapi.core.autolabel_sync import (
    diff_autolabel_rules,
    dump_autolabel_rules_yaml,
    export_remote_rules_to_yaml_dict,
    load_autolabel_rules_yaml,
    sync_autolabel_rules,
)
from cxas_scrapi.core.insights import Insights


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync declarative autolabeling rules with CCAI Insights."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # pull
    pull_p = subparsers.add_parser(
        "pull", help="Pull rules from project to YAML."
    )
    pull_p.add_argument(
        "--project-id", required=True, help="Google Cloud project ID."
    )
    pull_p.add_argument(
        "--location", default="us-central1", help="GCP location."
    )
    pull_p.add_argument(
        "--out", default="autolabel_rules.yaml", help="Output YAML file path."
    )

    # diff
    diff_p = subparsers.add_parser(
        "diff", help="Diff local YAML against project."
    )
    diff_p.add_argument(
        "--file", default="autolabel_rules.yaml", help="Path to YAML file."
    )
    diff_p.add_argument(
        "--project-id", help="Optional GCP project ID override."
    )
    diff_p.add_argument("--location", help="Optional GCP location override.")

    # push
    push_p = subparsers.add_parser("push", help="Deploy local YAML to project.")
    push_p.add_argument(
        "--file", default="autolabel_rules.yaml", help="Path to YAML file."
    )
    push_p.add_argument(
        "--project-id", help="Optional GCP project ID override."
    )
    push_p.add_argument("--location", help="Optional GCP location override.")
    push_p.add_argument(
        "--dry-run", action="store_true", help="Preview changes only."
    )
    push_p.add_argument(
        "--force",
        action="store_true",
        help="Delete remote rules missing from local YAML.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "pull":
        client = Insights(project_id=args.project_id, location=args.location)
        parent = f"projects/{args.project_id}/locations/{args.location}"
        print(f"Fetching rules from {parent}...")
        rules = client.list_autolabeling_rules(parent=parent)
        data = export_remote_rules_to_yaml_dict(
            rules, args.project_id, args.location
        )
        dump_autolabel_rules_yaml(data, args.out)
        print(f"Successfully exported {len(rules)} rules to {args.out}")

    elif args.command == "diff":
        data = load_autolabel_rules_yaml(args.file)
        proj = args.project_id or data.get("project_id")
        loc = args.location or data.get("location")
        if not proj or not loc:
            print("Error: project_id and location must be specified.")
            sys.exit(1)
        client = Insights(project_id=proj, location=loc)
        parent = f"projects/{proj}/locations/{loc}"
        print(f"Comparing '{args.file}' against {parent}...")
        remote_rules = client.list_autolabeling_rules(parent=parent)
        diff_res = diff_autolabel_rules(data, remote_rules)
        print(diff_res["report"])

    elif args.command == "push":
        data = load_autolabel_rules_yaml(args.file)
        proj = args.project_id or data.get("project_id")
        loc = args.location or data.get("location")
        if not proj or not loc:
            print("Error: project_id and location must be specified.")
            sys.exit(1)
        client = Insights(project_id=proj, location=loc)
        parent = f"projects/{proj}/locations/{loc}"
        action = "Dry-run pushing" if args.dry_run else "Pushing"
        print(f"{action} '{args.file}' to {parent}...")
        summary = sync_autolabel_rules(
            client=client,
            file_path=args.file,
            parent=parent,
            force=args.force,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            print(
                f"Sync Complete. Created: {len(summary['created'])}, "
                f"Updated: {len(summary['updated'])}, "
                f"Deleted: {len(summary['deleted'])}"
            )
        else:
            print(summary["diff_report"])


if __name__ == "__main__":
    main()
