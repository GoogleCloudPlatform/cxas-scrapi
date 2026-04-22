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

"""Run all 4 eval types and generate a combined report in one command.

Usage:
  python run-all-evals.py                          # Run everything, default channel from gecx-config
  python run-all-evals.py --channel audio          # Override channel
  python run-all-evals.py --runs 5                 # Number of golden runs (default: 5)
  python run-all-evals.py --skip-sims              # Skip sims
  python run-all-evals.py --skip-goldens           # Skip goldens (just run local tests + sims)
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from config import load_config as _load_shared_config, get_project_path, resolve_project_dir


# --- Paths ---
TOOL_TESTS_DIR = get_project_path("evals", "tool_tests")
CALLBACK_TESTS_DIR = get_project_path("evals", "callback_tests")
REPORTS_DIR = get_project_path("eval-reports")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

GOLDEN_TIMEOUT = 1200  # 20 minutes


def load_config():
    """Load app config from gecx-config.json via shared config loader."""
    raw = _load_shared_config()
    config = {
        "project": raw["gcp_project_id"],
        "location": raw.get("location", "us"),
        "app_id": raw["deployed_app_id"],
        "app_name_short": raw.get("app_name", ""),
        "default_channel": raw.get("default_channel", raw.get("modality", "text")),
        "modality": raw.get("modality", "text"),
    }
    config["app_resource"] = (
        f"projects/{config['project']}/locations/{config['location']}/apps/{config['app_id']}"
    )
    print(f"Config loaded from gecx-config.json (app: {config['app_name_short']})")
    return config


def run_callback_tests():
    """Run callback tests using CallbackEvals and save results."""
    print("\n" + "=" * 60)
    print("PHASE 1: Callback Tests")
    print("=" * 60)

    if not os.path.isdir(CALLBACK_TESTS_DIR):
        print(f"  Skipped: {CALLBACK_TESTS_DIR} not found")
        return None

    try:
        from cxas_scrapi.evals.callback_evals import CallbackEvals

        cb = CallbackEvals()
        results_df = cb.test_all_callbacks_in_app_dir(app_dir=CALLBACK_TESTS_DIR)

        os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, "callback_test_results.json")
        results_df.to_json(output_path, orient="records", indent=2)

        total = len(results_df)
        passed = len(results_df[results_df["status"].str.upper() == "PASSED"]) if "status" in results_df.columns else 0
        print(f"  Callback tests: {passed}/{total} passed")
        print(f"  Results saved to: {output_path}")
        return output_path

    except ImportError:
        print("  Skipped: cxas_scrapi.evals.callback_evals not available")
        return None
    except Exception as e:
        print(f"  ERROR: Callback tests failed: {e}")
        return None


def run_tool_tests(config):
    """Run tool tests using ToolEvals and save results."""
    print("\n" + "=" * 60)
    print("PHASE 2: Tool Tests")
    print("=" * 60)

    yaml_files = list(Path(TOOL_TESTS_DIR).glob("*.yaml")) if os.path.isdir(TOOL_TESTS_DIR) else []
    if not yaml_files:
        print(f"  Skipped: No YAML files in {TOOL_TESTS_DIR}")
        return None

    try:
        from cxas_scrapi.evals.tool_evals import ToolEvals

        te = ToolEvals(app_name=config["app_resource"])
        test_cases = te.load_tool_tests_from_dir(TOOL_TESTS_DIR)

        if not test_cases:
            print("  Skipped: No test cases loaded from YAML files")
            return None

        print(f"  Running {len(test_cases)} tool tests...")
        results_df = te.run_tool_tests(test_cases)

        os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, "tool_test_results.json")
        results_df.to_json(output_path, orient="records", indent=2)

        total = len(results_df)
        passed = len(results_df[results_df["status"].str.upper() == "PASSED"]) if "status" in results_df.columns else 0
        print(f"  Tool tests: {passed}/{total} passed")
        print(f"  Results saved to: {output_path}")
        return output_path

    except ImportError:
        print("  Skipped: cxas_scrapi.evals.tool_evals not available")
        return None
    except Exception as e:
        print(f"  ERROR: Tool tests failed: {e}")
        return None


def trigger_goldens(config, channel, runs):
    """Trigger golden eval run and return the evaluationRun resource name.

    Extracts the run name from the operation metadata — the platform
    populates the evaluation_run field shortly after the operation starts.
    """
    print("\n" + "=" * 60)
    print("PHASE 3: Platform Goldens (trigger)")
    print("=" * 60)

    from cxas_scrapi.core.evaluations import Evaluations
    from google.cloud.ces_v1beta.types import RunEvaluationOperationMetadata
    import time

    app_name = config.get("app_resource")
    if not app_name:
        print("  ERROR: No app_resource in config")
        return None

    try:
        client = Evaluations(app_name=app_name)
        response = client.run_evaluation(
            eval_type="goldens",
            app_name=app_name,
            modality=channel,
            run_count=runs,
        )
        print(f"  Golden eval run triggered ({channel}, {runs} runs)")

        # Poll operation metadata for the evaluation_run field
        for i in range(12):
            time.sleep(10)
            refreshed = response._refresh(None)
            meta = RunEvaluationOperationMetadata()
            meta._pb.ParseFromString(refreshed.metadata.value)
            if meta.evaluation_run:
                print(f"  Run: {meta.evaluation_run.split('/')[-1]}")
                return meta.evaluation_run
            print(f"  Waiting for run to appear... ({(i+1)*10}s)")

        print("  WARNING: Could not extract run name from operation metadata")
        return None
    except Exception as e:
        print(f"  ERROR: Failed to trigger golden run: {e}")
        return None


def _wait_for_run(app_name, run_name, timeout=GOLDEN_TIMEOUT):
    """Poll until a specific evaluation run completes."""
    import time
    from cxas_scrapi.core.evaluations import Evaluations

    client = Evaluations(app_name=app_name)
    start = time.time()
    poll_interval = 15

    while time.time() - start < timeout:
        try:
            run = client.client.get_evaluation_run(name=run_name)
            state = run.state if isinstance(run.state, int) else run.state.value
            if state in (2, 3):  # COMPLETED or ERROR
                return run.name
            print(f"  Waiting for run to complete... ({int(time.time() - start)}s)")
        except Exception as e:
            print(f"  Poll error: {e}")

        time.sleep(poll_interval)

    return None


def poll_golden_results(config, run_name, timeout=GOLDEN_TIMEOUT):
    """Wait for a specific golden run to complete and return run ID."""
    print("\n" + "=" * 60)
    print("PHASE 3: Platform Goldens (waiting for results)")
    print("=" * 60)

    if not run_name:
        print("  WARNING: No run name to poll — skipping golden results")
        return None

    from cxas_scrapi.utils.eval_utils import EvalUtils

    app_name = config.get("app_resource")

    # Wait for the specific run to complete
    completed_run = _wait_for_run(app_name, run_name, timeout=timeout)
    if not completed_run:
        print(f"  WARNING: Golden run timed out after {timeout}s")
        return None

    run_id_short = completed_run.split("/")[-1]
    print(f"  Golden run completed: {run_id_short}")

    # Fetch results
    try:
        utils = EvalUtils(app_name=app_name)
        results = utils.wait_for_run_and_get_results(
            run_name=completed_run,
            timeout_seconds=60,
        )
        if results:
            print(f"  Got {len(results)} golden results")
    except Exception as e:
        print(f"  WARNING: Failed to fetch results: {e}")

    return run_id_short


def run_sims(channel):
    """Run local sims via scrapi-sim-runner.py."""
    print("\n" + "=" * 60)
    print("PHASE 4: Local Simulations")
    print("=" * 60)

    runner_script = os.path.join(SCRIPTS_DIR, "scrapi-sim-runner.py")
    cmd = [
        sys.executable, runner_script, "run",
        "--priority", "P0",
        "--parallel", "3",
        "--channel", channel,
    ]

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True, cwd=resolve_project_dir())

    if result.returncode != 0:
        print(f"  WARNING: Sims exited with code {result.returncode}")
        return None

    # Find the most recent sim results JSON
    if os.path.isdir(REPORTS_DIR):
        sim_files = sorted(
            Path(REPORTS_DIR).glob("sim_results_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if sim_files:
            print(f"  Sim results: {sim_files[0]}")
            return str(sim_files[0])

    print("  WARNING: No sim results file found")
    return None


def generate_combined_report(golden_run_id, sim_results_path, tool_results_path,
                              callback_results_path, channel):
    """Generate combined HTML report from all result sources."""
    print("\n" + "=" * 60)
    print("GENERATING COMBINED REPORT")
    print("=" * 60)

    report_script = os.path.join(SCRIPTS_DIR, "generate-combined-report.py")
    cmd = [sys.executable, report_script]

    if golden_run_id:
        cmd.extend(["--golden-run", golden_run_id, "--golden-modality", channel])
    if sim_results_path:
        cmd.extend(["--sim-results", sim_results_path, "--sim-modality", channel])
    if tool_results_path:
        cmd.extend(["--tool-results", tool_results_path])
    if callback_results_path:
        cmd.extend(["--callback-results", callback_results_path])

    if len(cmd) <= 2:
        print("  Skipped: No results to combine")
        return None

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True, cwd=resolve_project_dir())

    if result.returncode != 0:
        print(f"  WARNING: Report generation exited with code {result.returncode}")

    # Find the most recent combined report
    if os.path.isdir(REPORTS_DIR):
        report_files = sorted(
            Path(REPORTS_DIR).glob("combined_report_*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if report_files:
            return str(report_files[0])

    return None


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Run all 4 eval types and generate a combined report"
    )
    parser.add_argument(
        "--channel", default=None,
        help="Modality: text or audio (default: from gecx-config.json)"
    )
    parser.add_argument(
        "--runs", type=int, default=5,
        help="Number of golden runs (default: 5)"
    )
    parser.add_argument(
        "--skip-sims", action="store_true",
        help="Skip simulation evals"
    )
    parser.add_argument(
        "--skip-goldens", action="store_true",
        help="Skip golden evals (just run local tests + sims)"
    )
    args = parser.parse_args()

    # Load config
    config = load_config()

    if args.channel and args.channel != config.get("modality", "text"):
        print(f"ERROR: Cannot run evals in '{args.channel}' mode. gecx-config.json specifies modality '{config.get('modality', 'text')}'.")
        print("To fix: Remove the --channel flag or ensure it matches the app's configured modality.")
        sys.exit(1)

    channel = args.channel or config.get("default_channel", "text")

    print(f"\nApp: {config['app_resource']}")
    print(f"Channel: {channel}")
    print(f"Golden runs: {args.runs}")
    print(f"Skip goldens: {args.skip_goldens}")
    print(f"Skip sims: {args.skip_sims}")

    overall_start = time.time()
    golden_run_name = None
    golden_run_id = None

    # --- Step 1: Trigger goldens early (they run async on the platform) ---
    if not args.skip_goldens:
        golden_run_name = trigger_goldens(config, channel, args.runs)

    # --- Step 2: While goldens are running, execute local tests ---
    callback_results_path = run_callback_tests()
    tool_results_path = run_tool_tests(config)

    # --- Step 3: Wait for goldens to complete ---
    if golden_run_name is not None:
        golden_run_id = poll_golden_results(config, golden_run_name)

    # --- Step 4: Run sims ---
    sim_results_path = None
    if not args.skip_sims:
        sim_results_path = run_sims(channel)

    # --- Step 5: Generate combined report ---
    report_path = generate_combined_report(
        golden_run_id=golden_run_id,
        sim_results_path=sim_results_path,
        tool_results_path=tool_results_path,
        callback_results_path=callback_results_path,
        channel=channel,
    )

    # --- Final summary ---
    elapsed = time.time() - overall_start
    elapsed_str = f"{elapsed / 60:.1f}m" if elapsed >= 60 else f"{elapsed:.0f}s"

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Total time: {elapsed_str}")
    print(f"  Channel:    {channel}")
    print()

    status_lines = [
        ("Callback tests", callback_results_path),
        ("Tool tests", tool_results_path),
        ("Goldens", golden_run_id if golden_run_id else None),
        ("Sims", sim_results_path),
    ]
    for label, result in status_lines:
        if args.skip_goldens and label == "Goldens":
            status = "SKIPPED"
        elif args.skip_sims and label == "Sims":
            status = "SKIPPED"
        elif result:
            status = f"DONE ({result})" if isinstance(result, str) else "DONE"
        else:
            status = "FAILED / NOT RUN"
        print(f"  {label:20s} {status}")

    if report_path:
        print(f"\n  Combined report: {report_path}")
    else:
        print("\n  No combined report generated.")

    print()


if __name__ == "__main__":
    main()
