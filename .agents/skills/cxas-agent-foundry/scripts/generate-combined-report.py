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

"""Generate a combined HTML report for golden + simulation eval results.

Usage:
  python scripts/generate-combined-report.py --golden-run <RUN_ID> --sim-results <JSON_PATH>
  python scripts/generate-combined-report.py --golden-run <RUN_ID>
  python scripts/generate-combined-report.py --sim-results <JSON_PATH>
"""

import argparse
import os
import sys
from datetime import datetime

from config import get_project_path
from cxas_scrapi.utils.reporting import (
    generate_combined_html_report,
    load_callback_test_results,
    load_golden_results,
    load_sim_results,
    load_tool_test_results,
)

REPORTS_DIR = get_project_path("eval-reports")
SIM_EVALS_YAML = get_project_path("evals", "simulations", "simulations.yaml")


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print(
            "Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first."
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Generate combined eval report"
    )
    parser.add_argument("--golden-run", help="Golden eval run ID")
    parser.add_argument("--sim-results", help="Path to sim results JSON")
    parser.add_argument(
        "--tool-results", help="Path to tool test results CSV/JSON"
    )
    parser.add_argument(
        "--callback-results", help="Path to callback test results CSV/JSON"
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="App resource name. If not provided, reads from gecx-config.json via config.py.",
    )
    parser.add_argument(
        "--golden-modality",
        default="text",
        help="Modality for golden run (text/audio)",
    )
    parser.add_argument(
        "--sim-modality",
        default="text",
        help="Modality for sim run (text/audio)",
    )
    parser.add_argument("--output", help="Output HTML path")
    args = parser.parse_args()

    if not any(
        [
            args.golden_run,
            args.sim_results,
            args.tool_results,
            args.callback_results,
        ]
    ):
        parser.print_help()
        return

    # Resolve app_name from gecx-config.json if not provided
    if not args.app_name and args.golden_run:
        try:
            from config import load_app_name

            args.app_name = load_app_name()
        except Exception:
            print(
                "Error: --app-name required when gecx-config.json not found"
            )
            return

    golden_results = None
    sim_results = None
    tool_results = None
    callback_results = None

    if args.golden_run:
        print(f"Loading golden results for run {args.golden_run}...")
        golden_results = load_golden_results(args.golden_run, args.app_name)
        print(f"  {len(golden_results)} golden results")

    sim_wall_clock_s = None
    if args.sim_results:
        print(f"Loading sim results from {args.sim_results}...")
        sim_results, sim_wall_clock_s = load_sim_results(
            args.sim_results, sim_evals_yaml=SIM_EVALS_YAML
        )
        wc_str = (
            f" (wall clock: {sim_wall_clock_s:.0f}s)"
            if sim_wall_clock_s
            else ""
        )
        print(f"  {len(sim_results)} sim results{wc_str}")

    if args.tool_results:
        print(f"Loading tool test results from {args.tool_results}...")
        tool_results = load_tool_test_results(args.tool_results)
        print(f"  {len(tool_results)} tool test results")

    if args.callback_results:
        print(
            f"Loading callback test results from {args.callback_results}..."
        )
        callback_results = load_callback_test_results(args.callback_results)
        print(f"  {len(callback_results)} callback test results")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    output_path = args.output or os.path.join(
        REPORTS_DIR, f"combined_report_{ts}.html"
    )

    generate_combined_html_report(
        golden_results=golden_results or [],
        sim_results=sim_results or [],
        tool_results=tool_results or [],
        callback_results=callback_results or [],
        output_path=output_path,
        app_name=args.app_name or "",
        golden_modality=args.golden_modality,
        sim_modality=args.sim_modality,
        sim_wall_clock_s=sim_wall_clock_s,
    )
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
