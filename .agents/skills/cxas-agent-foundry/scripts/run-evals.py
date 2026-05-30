#!/usr/bin/env python3
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

"""Run all 4 eval types and generate a combined report in one command (SCRAPI)."""

import argparse
import os
import sys
import time
from datetime import datetime

from config import load_config as _load_shared_config, get_project_path

# --- Paths ---
REPORTS_DIR = get_project_path("eval-reports")


def load_config():
    """Load app config from gecx-config.json via shared config loader."""
    raw = _load_shared_config()
    config = {
        "project": raw["gcp_project_id"],
        "location": raw.get("location", "us"),
        "app_id": raw["deployed_app_id"],
        "app_name_short": raw.get("app_name", ""),
        "default_channel": raw.get(
            "default_channel", raw.get("modality", "text")
        ),
        "modality": raw.get("modality", "text"),
    }
    config["app_resource"] = (
        f"projects/{config['project']}/locations/{config['location']}/apps/{config['app_id']}"
    )
    print(
        f"Config loaded from gecx-config.json (app: {config['app_name_short']})"
    )
    return config


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print(
            "Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first."
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Run all 4 eval types and generate a combined report"
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Modality: text or audio (default: from gecx-config.json)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Trials per golden AND per sim (default: 5). Tool tests and callback tests are deterministic and always run once.",
    )
    parser.add_argument(
        "--skip-sims", action="store_true", help="Skip simulation evals"
    )
    parser.add_argument(
        "--skip-goldens",
        action="store_true",
        help="Skip golden evals (just run local tests + sims)",
    )
    parser.add_argument(
        "--priority",
        default="P0",
        help="Sim priority filter forwarded to scrapi-sim-runner (e.g., P0, or P0,P1,P2). Default: P0.",
    )
    parser.add_argument(
        "--sim-parallel",
        type=int,
        default=5,
        help="Number of parallel worker sessions for simulations. Defaults to 5.",
    )
    args = parser.parse_args()

    # Load config
    config = load_config()

    if args.channel and args.channel != config.get("modality", "text"):
        print(
            f"ERROR: Cannot run evals in '{args.channel}' mode. gecx-config.json specifies modality '{config.get('modality', 'text')}'."
        )
        print(
            "To fix: Remove the --channel flag or ensure it matches the app's configured modality."
        )
        sys.exit(1)

    channel = args.channel or config.get("default_channel", "text")

    print(f"\nApp: {config['app_resource']}")
    print(f"Channel: {channel}")
    print(f"Golden runs: {args.runs}")
    print(f"Skip goldens: {args.skip_goldens}")
    print(f"Skip sims: {args.skip_sims}")

    overall_start = time.time()

    # Map skips to include list
    include = ["tools", "callbacks"]
    if not args.skip_sims:
        include.append("sims")
    if not args.skip_goldens:
        include.append("goldens")

    filter_tags = []
    if args.priority:
        filter_tags.extend(
            [p.strip().upper() for p in args.priority.split(",")]
        )

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = os.path.join(REPORTS_DIR, f"combined_report_{ts}.html")

    from cxas_scrapi.utils.reporting import generate_combined_report_from_dir

    print("\nRunning all evaluations in-process...")
    try:
        generate_combined_report_from_dir(
            output_dir=REPORTS_DIR,
            app_name=config["app_resource"],
            output_path=report_path,
            run=True,
            app_dir=get_project_path("evals", "callback_tests"),
            tool_test_file=get_project_path("evals", "tool_tests"),
            goldens_dir=get_project_path("evals", "goldens"),
            simulation_dir=get_project_path("evals", "simulations"),
            include=include,
            modality=channel,
            runs=args.runs,
            filter_tags=filter_tags,
            parallel=args.sim_parallel,
        )
    except Exception as e:
        print(f"\n  ERROR: Evaluation run failed: {e}")
        sys.exit(1)

    elapsed = time.time() - overall_start
    elapsed_str = f"{elapsed / 60:.1f}m" if elapsed >= 60 else f"{elapsed:.0f}s"

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Total time: {elapsed_str}")
    print(f"  Channel:    {channel}")
    print(f"  Combined report: {report_path}")
    print()


if __name__ == "__main__":
    main()
