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

"""Single-command iteration step: snapshot + run evals + triage + iteration report.

Combines the boring parts of the debug iteration loop into one command so the
agent only needs to fix code and call this script.

Usage:
  python run-and-report.py --message "Fixed escalation logic"
  python run-and-report.py --message "Added timeout handling" --channel audio --runs 5
  python run-and-report.py --message "Refactored callbacks" --auto-revert
  python run-and-report.py --message "Testing" --dry-run
"""

import argparse
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(cmd: list[str], description: str, dry_run: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess with clear status output."""
    print(f"\n{'=' * 60}")
    print(f"  {description}")
    print(f"{'=' * 60}")
    print(f"  $ {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] Skipped.")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = subprocess.run(cmd, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"\n  ERROR: {description} failed (exit code {result.returncode})")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Single-command iteration step: snapshot + evals + triage + report"
    )
    parser.add_argument(
        "--message", required=True,
        help="Description of what changed in this iteration"
    )
    parser.add_argument(
        "--channel", default=None,
        help="Eval channel: text or audio (default: from gecx-config.json)"
    )
    parser.add_argument(
        "--runs", type=int, default=None,
        help="Number of golden eval runs (default: from run-all-evals.py)"
    )
    parser.add_argument(
        "--auto-revert", action="store_true", default=False,
        help="Revert cxas_app/ to previous snapshot if pass rate regressed"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print what would be done without running anything"
    )

    args = parser.parse_args()
    python = sys.executable

    # Check cxas_scrapi is available
    if not args.dry_run:
        try:
            import cxas_scrapi  # noqa: F401
        except ImportError:
            print("Error: cxas-scrapi is not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
            sys.exit(1)

    print(f"\nHillclimb iteration: {args.message}")
    print(f"{'—' * 60}")

    # Step 1: Snapshot
    snapshot_cmd = [python, os.path.join(SCRIPTS_DIR, "generate-iteration-report.py"), "snapshot"]
    result = _run(snapshot_cmd, "Step 1/4: Snapshot agent state", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nFailed to take snapshot. Aborting.")
        sys.exit(1)

    # Step 2: Run all evals
    eval_cmd = [python, os.path.join(SCRIPTS_DIR, "run-all-evals.py")]
    if args.channel:
        eval_cmd.extend(["--channel", args.channel])
    if args.runs:
        eval_cmd.extend(["--runs", str(args.runs)])
    result = _run(eval_cmd, "Step 2/4: Run all evals", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nEval run failed. Continuing to triage and report with available results...")

    # Step 3: Triage results
    triage_cmd = [python, os.path.join(SCRIPTS_DIR, "triage-results.py")]
    _run(triage_cmd, "Step 3/4: Triage failures", dry_run=args.dry_run)

    # Step 4: Generate iteration report
    report_cmd = [
        python, os.path.join(SCRIPTS_DIR, "generate-iteration-report.py"),
        "report", "--message", args.message,
    ]
    if args.auto_revert:
        report_cmd.append("--auto-revert")
    result = _run(report_cmd, "Step 4/4: Generate iteration report", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nReport generation failed.")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  Hillclimb iteration complete.")
    print(f"  Message: {args.message}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
