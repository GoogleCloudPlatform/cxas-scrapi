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

"""Single-command iteration step: snapshot + push goldens + run evals + triage + iteration report.

Combines the boring parts of the debug iteration loop into one command so the
agent only needs to fix code and call this script.

Usage:
  python run-and-report.py --message "Fixed escalation logic"
  python run-and-report.py --message "Added timeout handling" --channel audio --runs 5
  python run-and-report.py --message "Refactored callbacks" --auto-revert
  python run-and-report.py --message "Edited agent only" --no-push-goldens
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


def _ensure_eval_reports_dir():
    """Create <project>/eval-reports/ if missing.

    Defensive — `setup-project.py` already creates this at bootstrap, but a
    user might delete it manually. The iteration loop's shell redirect to
    `<project>/eval-reports/last-run.log` (and the various result JSONs the
    sub-scripts write under this directory) all need the parent to exist.
    Note: this runs *after* the shell has already opened the redirect target,
    so it doesn't help the very first invocation on a fresh project — that's
    what `setup-project.py` covers.

    Returns silently when no project marker is found (e.g., `--help`
    invocation from a random directory) so we don't create stray dirs.
    """
    project = _resolve_project_dir()
    if project:
        os.makedirs(os.path.join(project, "eval-reports"), exist_ok=True)


def _resolve_project_dir():
    """Return the project root, or None when no project marker is found.

    Walks up from cwd looking for `.active-project` (whose contents name the
    active project folder) or a `gecx-config.json` in the cwd itself.
    """
    cwd = os.getcwd()
    for _ in range(10):
        active = os.path.join(cwd, ".active-project")
        if os.path.isfile(active):
            try:
                with open(active) as f:
                    name = f.read().strip()
                return os.path.join(cwd, name) if name else cwd
            except OSError:
                return cwd
        if os.path.isfile(os.path.join(cwd, "gecx-config.json")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            return None
        cwd = parent
    return None


def main():
    _ensure_eval_reports_dir()
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
        help="Trials per golden AND per sim (default: from run-all-evals.py = 5). Tool tests and callback tests are deterministic and always run once."
    )
    parser.add_argument(
        "--auto-revert", action="store_true", default=False,
        help="Revert cxas_app/ to previous snapshot if pass rate regressed"
    )
    parser.add_argument(
        "--no-push-goldens", action="store_true", default=False,
        help="Skip pushing local evals/goldens/ YAMLs to the platform before running. "
             "Use when you've only edited agent code and want to reuse the platform's existing goldens."
    )
    parser.add_argument(
        "--json-summary", default=None,
        help="Write a structured run summary to this path (forwarded to generate-iteration-report.py). Use this to read results without parsing stdout — the iteration loop reads this file."
    )
    parser.add_argument(
        "--priority", default=None,
        help="Sim priority filter (e.g., P0, or P0,P1,P2). Default: P0 (set in run-all-evals.py)."
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
    result = _run(snapshot_cmd, "Step 1/5: Snapshot agent state", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nFailed to take snapshot. Aborting.")
        sys.exit(1)

    # Step 2: Push local goldens to platform (so eval run sees latest YAML edits)
    if args.no_push_goldens:
        print(f"\n{'=' * 60}")
        print("  Step 2/5: Push goldens -- SKIPPED (--no-push-goldens)")
        print(f"{'=' * 60}")
    else:
        push_cmd = [
            python, os.path.join(SCRIPTS_DIR, "scrapi-eval-runner.py"),
            "push-goldens",
        ]
        result = _run(push_cmd, "Step 2/5: Push local goldens to platform", dry_run=args.dry_run)
        if result.returncode != 0:
            print("\n  WARNING: Golden push failed. Eval run will use the platform's existing goldens.")
            print("  If you edited golden YAMLs locally, those edits are NOT live on the platform.")

    # Step 3: Run all evals
    eval_cmd = [python, os.path.join(SCRIPTS_DIR, "run-all-evals.py")]
    if args.channel:
        eval_cmd.extend(["--channel", args.channel])
    if args.runs:
        eval_cmd.extend(["--runs", str(args.runs)])
    if args.priority:
        eval_cmd.extend(["--priority", args.priority])
    result = _run(eval_cmd, "Step 3/5: Run all evals", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nEval run failed. Continuing to triage and report with available results...")

    # Step 4: Triage results
    triage_cmd = [python, os.path.join(SCRIPTS_DIR, "triage-results.py")]
    _run(triage_cmd, "Step 4/5: Triage failures", dry_run=args.dry_run)

    # Step 5: Generate iteration report
    report_cmd = [
        python, os.path.join(SCRIPTS_DIR, "generate-iteration-report.py"),
        "report", "--message", args.message,
    ]
    if args.auto_revert:
        report_cmd.append("--auto-revert")
    if args.json_summary:
        report_cmd.extend(["--json-summary", args.json_summary])
    result = _run(report_cmd, "Step 5/5: Generate iteration report", dry_run=args.dry_run)
    if result.returncode != 0:
        print("\nReport generation failed.")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  Hillclimb iteration complete.")
    print(f"  Message: {args.message}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
