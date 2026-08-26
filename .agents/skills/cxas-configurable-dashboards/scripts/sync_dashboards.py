#!/usr/bin/env python3
"""Sync declarative configurable dashboards with CCAI Insights."""

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

from cxas_scrapi.core.dashboard_sync import (
    diff_dashboards,
    dump_dashboards_yaml,
    export_remote_dashboards_to_yaml_dict,
    load_dashboards_yaml,
    sync_dashboards,
)
from cxas_scrapi.core.insights import Insights


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync configurable dashboards with CCAI Insights."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # pull
    pull_p = subparsers.add_parser(
        "pull", help="Pull dashboards from project to YAML."
    )
    pull_p.add_argument(
        "--project-id", required=True, help="Google Cloud project ID."
    )
    pull_p.add_argument(
        "--location", default="us-central1", help="GCP location."
    )
    pull_p.add_argument(
        "--out", default="dashboards.yaml", help="Output YAML file path."
    )

    # diff
    diff_p = subparsers.add_parser(
        "diff", help="Diff local YAML against project."
    )
    diff_p.add_argument(
        "--file", default="dashboards.yaml", help="Path to YAML file."
    )
    diff_p.add_argument(
        "--project-id", help="Optional GCP project ID override."
    )
    diff_p.add_argument("--location", help="Optional GCP location override.")

    # push
    push_p = subparsers.add_parser("push", help="Deploy local YAML to project.")
    push_p.add_argument(
        "--file", default="dashboards.yaml", help="Path to YAML file."
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
        help="Delete remote dashboards missing from local YAML.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "pull":
        client = Insights(project_id=args.project_id, location=args.location)
        parent = f"projects/{args.project_id}/locations/{args.location}"
        print(f"Fetching dashboards from {parent}...")
        dashboards = client.list_dashboards(parent=parent)
        data = export_remote_dashboards_to_yaml_dict(
            dashboards, args.project_id, args.location
        )
        dump_dashboards_yaml(data, args.out)
        count = len(data.get("dashboards", []))
        print(f"Successfully exported {count} custom dashboards to {args.out}")

    elif args.command == "diff":
        data = load_dashboards_yaml(args.file)
        proj = args.project_id or data.get("project_id")
        loc = args.location or data.get("location", "us-central1")
        if not proj:
            print("Error: Specify --project-id or set project_id in YAML.")
            sys.exit(1)
        client = Insights(project_id=proj, location=loc)
        parent = f"projects/{proj}/locations/{loc}"
        print(f"Diffing '{args.file}' against {parent}...")
        remote = client.list_dashboards(parent=parent)
        res = diff_dashboards(data, remote)
        print(res["report"])

    elif args.command == "push":
        data = load_dashboards_yaml(args.file)
        proj = args.project_id or data.get("project_id")
        loc = args.location or data.get("location", "us-central1")
        if not proj:
            print("Error: Specify --project-id or set project_id in YAML.")
            sys.exit(1)
        client = Insights(project_id=proj, location=loc)
        parent = f"projects/{proj}/locations/{loc}"
        summary = sync_dashboards(
            client=client,
            file_path=args.file,
            parent=parent,
            force=args.force,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            print("\nSync Complete:")
            print(f"Created : {len(summary['created'])}")
            print(f"Updated : {len(summary['updated'])}")
            print(f"Deleted : {len(summary['deleted'])}")
            if summary.get("skipped_delete"):
                skip_count = len(summary["skipped_delete"])
                print(f"Skipped Deletions (use --force): {skip_count}")


if __name__ == "__main__":
    main()

