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

"""CLI script for running CXAS SCRAPI evaluations."""

import argparse
import logging
import os
import subprocess
import sys
import time
import uuid
from typing import Dict, List

import pandas as pd

from cxas_scrapi.cli.app import (
    app_branch,
    app_create,
    app_delete,
    app_init,
    app_lint,
    app_pull,
    app_push,
    apps_get,
    apps_list,
)
from cxas_scrapi.cli.create_local import handle_local_create
from cxas_scrapi.cli.insights_cli import populate_insights_parser
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.evaluations import Evaluations, ExportFormat
from cxas_scrapi.core.github import init_github_action
from cxas_scrapi.evals.callback_evals import CallbackEvals
from cxas_scrapi.evals.tool_evals import ToolEvals
from cxas_scrapi.utils.eval_utils import EvalUtils

logger = logging.getLogger(__name__)


def export_eval(args: argparse.Namespace) -> None:
    """Handles the 'export' command."""

    print(f"Exporting evaluation: {args.evaluation_id}")
    # Use app_name to init client. Eval ID might be full resource name.
    eval_client = Evaluations(app_name=args.app_name)

    try:
        format_enum = (
            ExportFormat(args.format.lower())
            if args.format
            else ExportFormat.YAML
        )
        exported_eval = eval_client.export_evaluation(
            args.evaluation_id,
            output_format=format_enum,
            output_path=args.output,
        )
        if args.output:
            print(f"Evaluation exported to {args.output}")
        else:
            print(exported_eval)

    except Exception as e:
        print(f"Failed to export evaluation: {e}")
        sys.exit(1)


def push_eval(args: argparse.Namespace) -> None:
    """Handles the 'push' command."""
    print(f"Pushing evaluation(s) from {args.file} to App: {args.app_name}")

    eval_client = Evaluations(app_name=args.app_name)
    eval_utils = EvalUtils(app_name=args.app_name)

    try:
        evals = eval_utils.load_golden_evals_from_yaml(args.file)
        if not evals:
            print(f"No valid evaluations found in '{args.file}'.")
            sys.exit(1)

        print(f"Parsed {len(evals)} evaluation(s). Syncing...")
        for eval_dict in evals:
            res = eval_client.update_evaluation(
                evaluation=eval_dict, app_name=args.app_name
            )
            print(f"Pushed: '{res.display_name}' ({res.name})")

        print("\nPush complete.")

    except Exception as e:
        print(f"Failed to push evaluation(s): {e}")
        sys.exit(1)


def wait_for_evaluation_completion(
    eval_utils: EvalUtils,
    old_result_ids: List[str],
    app_name: str,
    expected_count: int = 1,
    timeout_seconds: int = 600,
) -> Dict[str, pd.DataFrame]:
    """Waits for all new evaluation results to appear."""
    print(f"Waiting for {expected_count} evaluation(s) to complete...")
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        # Fetch current evaluation results
        try:
            df_dict = eval_utils.evals_to_dataframe()
            df_current = df_dict.get("summary", pd.DataFrame())
            if df_current.empty:
                time.sleep(5)
                continue

            # Find new runs
            current_result_ids = set(df_current["eval_result_id"].unique())
            new_ids = current_result_ids - old_result_ids

            if new_ids and len(new_ids) >= expected_count:
                # Wait for ALL new runs to complete
                all_completed = True
                completed_results = []
                for run_id in new_ids:
                    df_new = df_current[df_current["eval_result_id"] == run_id]
                    exec_state = (
                        df_new["execution_state"].iloc[0]
                        if not df_new.empty
                        and "execution_state" in df_new.columns
                        else "COMPLETED"
                    )

                    if exec_state not in ("COMPLETED", "ERROR"):
                        all_completed = False
                        break

                    # Fetch trace
                    raw = eval_utils.eval_client.get_evaluation_result(run_id)
                    completed_results.append(raw)

                if all_completed:
                    print(f"All {len(new_ids)} evaluations completed.")
                    return eval_utils.evals_to_dataframe(
                        results=completed_results
                    )

        except Exception as e:
            print(f"Error checking evaluation status: {e}")

        time.sleep(5)

    print("Timeout waiting for evaluation to complete.")
    sys.exit(1)


def filter_metrics_and_assess(  # noqa: C901
    df_dict_new_run: Dict[str, pd.DataFrame],
    filter_auto_metrics: bool,
) -> bool:
    """Assesses the evaluation run and returns True if passed,
    False otherwise."""
    passed = True

    df_new_run = df_dict_new_run.get("summary", pd.DataFrame())
    df_expectations = df_dict_new_run.get("expectations", pd.DataFrame())

    # Standard assessment: check standard status first
    # This might encompass semantic and hallucination metrics

    num_passed = 0
    num_failed = 0
    num_error = 0
    if not df_new_run.empty:
        for _, row in df_new_run.iterrows():
            eval_stat = str(row.get("evaluation_status", "")).upper()
            exec_stat = str(row.get("execution_state", "")).upper()

            if exec_stat in ("ERROR", "ERRORED") or eval_stat in (
                "ERROR",
                "ERRORED",
            ):
                num_error += 1
            elif eval_stat in ("PASS", "PASSED", "✅ PASSED"):
                num_passed += 1
            else:
                num_failed += 1

    overall_status = (
        "PASS"
        if num_failed == 0 and num_error == 0 and num_passed > 0
        else "FAIL"
        if (num_failed > 0 or num_error > 0)
        else "UNKNOWN"
    )

    print(f"\n--- Evaluation Status: {overall_status} ---")
    print(f"Passed: {num_passed}")
    print(f"Failed: {num_failed}")
    print(f"Errored: {num_error}")

    if filter_auto_metrics:
        print(
            "\n[Targeted Assessment] Filtering out automated LLM metrics "
            "(semantic similarity, hallucination)."
        )
        print("Focusing strictly on custom expectations and tool invocation.")

        if (
            not df_expectations.empty
            and "record_type" in df_expectations.columns
        ):
            expectation_rows = df_expectations[
                df_expectations["record_type"] == "summary_expectation"
            ]
        else:
            expectation_rows = pd.DataFrame()

        if not expectation_rows.empty:
            failed_expectations = expectation_rows[
                expectation_rows["not_met_count"] > 0
            ]
            if not failed_expectations.empty:
                print(
                    f"FAILED: {len(failed_expectations)} custom expectations "
                    "not met."
                )
                for _, row in failed_expectations.iterrows():
                    print(
                        f"  - Expectation: {row['expectation']} "
                        f"(Met: {row['met_count']}, "
                        f"Not Met: {row['not_met_count']})"
                    )
                passed = False
            else:
                print(
                    f"PASSED: All {len(expectation_rows)} custom expectations "
                    "met."
                )
        else:
            print("WARNING: No custom expectations found in this evaluation.")
            # Fallback: check basic tool execution result limit

    # Strict overall pass/fail based on the server constraints
    elif overall_status != "PASS":
        passed = False

    return passed


def run_eval(args: argparse.Namespace) -> None:  # noqa: C901
    """Handles the 'run' command."""

    print(f"Triggering evaluation for App: {args.app_name}")
    eval_client = Evaluations(app_name=args.app_name)
    eval_utils = EvalUtils(app_name=args.app_name)

    # Determine which evaluations to run
    evaluations_to_run = []
    if args.evaluation_id:
        evaluations_to_run.append(args.evaluation_id)
    else:
        # Require prefix or tags if no specific ID is given
        if not args.display_name_prefix and not args.tags:
            print(
                "Error: You must provide either --evaluation-id, "
                "--display-name-prefix, or --tags to "
                "specify which tests to run."
            )
            sys.exit(1)

        if args.display_name_prefix:
            print(
                "Fetching tests matching prefix: "
                f"'{args.display_name_prefix}'..."
            )
        elif args.tags:
            print(f"Fetching tests matching tags: {args.tags}...")
        all_evals = eval_client.list_evaluations(app_name=args.app_name)

        for eval_obj in all_evals:
            match = False

            if args.display_name_prefix and eval_obj.display_name.startswith(
                args.display_name_prefix
            ):
                match = True

            # Assuming tags are accessible as a
            # list/repeated field on the Evaluation
            # object
            if args.tags and hasattr(eval_obj, "tags"):
                # intersection of CLI tags and agent tags
                if any(t in eval_obj.tags for t in args.tags):
                    match = True

            if match:
                evaluations_to_run.append(eval_obj.name)

        if not evaluations_to_run:
            print(
                "No matching tests found for the "
                "given prefix or tags. Aborting run."
            )
            sys.exit(0)

        print(f"Found {len(evaluations_to_run)} matching test(s) to run.")

    # Determine which evaluations to run
    evaluations_to_run = []
    if args.evaluation_id:
        evaluations_to_run.append(args.evaluation_id)
    else:
        # Require prefix or tags if no specific ID is given
        if not args.display_name_prefix and not args.tags:
            print(
                "Error: You must provide either --evaluation-id, "
                "--display-name-prefix, or --tags to "
                "specify which tests to run."
            )
            sys.exit(1)

        if args.display_name_prefix:
            print(
                "Fetching tests matching prefix: "
                f"'{args.display_name_prefix}'..."
            )
        elif args.tags:
            print(f"Fetching tests matching tags: {args.tags}...")
        all_evals = eval_client.list_evaluations(app_name=args.app_name)

        for eval_obj in all_evals:
            match = False

            if args.display_name_prefix and eval_obj.display_name.startswith(
                args.display_name_prefix
            ):
                match = True

            # Assuming tags are accessible as a
            # list/repeated field on the Evaluation
            # object
            if args.tags and hasattr(eval_obj, "tags"):
                # intersection of CLI tags and agent tags
                if any(t in eval_obj.tags for t in args.tags):
                    match = True

            if match:
                evaluations_to_run.append(eval_obj.name)

        if not evaluations_to_run:
            print(
                "No matching tests found for the "
                "given prefix or tags. Aborting run."
            )
            sys.exit(0)

        print(f"Found {len(evaluations_to_run)} matching test(s) to run.")

    try:
        # Step 1: Capture existing evaluation runs to diff against later
        df_initial = eval_utils.evals_to_dataframe().get(
            "summary", pd.DataFrame()
        )
        old_result_ids = set()
        if not df_initial.empty and "eval_result_id" in df_initial.columns:
            old_result_ids = set(df_initial["eval_result_id"].unique())

        # Step 2: Trigger evaluation
        eval_client.run_evaluation(
            evaluations=evaluations_to_run, app_name=args.app_name
        )
        print("Evaluation triggered successfully based on CLI call.")

        # Step 3: Wait and backoff on pending evaluations.
        if args.wait:
            df_new_run = wait_for_evaluation_completion(
                eval_utils,
                old_result_ids,
                args.app_name,
                expected_count=len(evaluations_to_run),
            )
            pass_status = filter_metrics_and_assess(
                df_new_run, args.filter_auto_metrics
            )

            if pass_status:
                print("\nFINAL RESULT: PASS")
                sys.exit(0)
            else:
                df_failures = df_new_run.get("failures", pd.DataFrame())
                if not df_failures.empty:
                    print("\n--- Failure Details ---")
                    grouped = df_failures.groupby("display_name", sort=False)
                    for disp, group_df in grouped:
                        is_err = any(
                            row.get("failure_type") == "System Engine Error"
                            for _, row in group_df.iterrows()
                        )
                        title_str = "Errored" if is_err else "Failed"
                        print(f"\n{disp} {title_str}")

                        sys_errors = group_df[
                            group_df["failure_type"] == "System Engine Error"
                        ]
                        normal_fails = group_df[
                            group_df["failure_type"] != "System Engine Error"
                        ]

                        for _, row in sys_errors.iterrows():
                            print(f"- {row.get('actual')}\n")

                        for _, row in normal_fails.iterrows():
                            idx = row.get("turn_index")
                            tba = f" (Turn {idx})" if pd.notnull(idx) else ""

                            print(f"- Type    : {row.get('failure_type')}{tba}")
                            print(f"- Expected: {row.get('expected')}")
                            print(f"- Actual  : {row.get('actual')}")

                            score = row.get("score")
                            if pd.notnull(score):
                                print(f"- Score   : {score}")
                            print()

                print("\nFINAL RESULT: FAIL")
                sys.exit(1)

    except Exception as e:
        print(f"Failed to run evaluation: {e}")
        sys.exit(1)


def test_tools(args: argparse.Namespace) -> None:
    """Handles the 'test-tools' command."""

    print(
        f"Running tool tests for App: {args.app_name} "
        f"using file: {args.test_file}"
    )
    tool_evals = ToolEvals(app_name=args.app_name)

    try:
        test_cases = tool_evals.load_tool_test_cases_from_file(args.test_file)
        if not test_cases:
            print(f"No valid test cases found in {args.test_file}")
            sys.exit(1)

        results = tool_evals.run_tool_tests(test_cases, debug=args.debug)

        # Check overall status
        failed_count = sum(1 for r in results["status"] if r != "PASSED")

        if failed_count > 0:
            print(f"\nFINAL RESULT: FAIL ({failed_count} tools failed)")
            sys.exit(1)
        else:
            print(f"\nFINAL RESULT: PASS (All {len(results)} tools passed)")
            sys.exit(0)

    except Exception as e:
        print(f"Failed to run tool tests: {e}")
        sys.exit(1)


def test_callbacks(args: argparse.Namespace) -> None:
    """Handles the 'test-callbacks' command."""

    print(f"Running callback tests in App directory: {args.app_dir}")
    callback_evals = CallbackEvals()

    try:
        results = callback_evals.test_all_callbacks_in_app_dir(
            app_dir=args.app_dir,
            agent_name=args.agent_name,
            callback_type=args.callback_type,
            callback_name=args.callback_name,
            log_file=args.log_file,
            pytest_args=args.pytest_args,
        )
        if results.empty:
            print(f"No valid callback tests found in {args.app_dir}")
            sys.exit(1)

        # Check overall status
        failed_count = sum(1 for r in results["status"] if r != "PASSED")

        if failed_count > 0:
            print(f"\nFINAL RESULT: FAIL ({failed_count} callbacks failed)")
            sys.exit(1)
        else:
            print(f"\nFINAL RESULT: PASS (All {len(results)} callbacks passed)")
            sys.exit(0)

    except Exception as e:
        print(f"Failed to run callback tests: {e}")
        sys.exit(1)


def test_single_callback(args: argparse.Namespace) -> None:
    """Handles the 'test-single-callback' command."""

    print(
        f"Running single callback test for "
        f"Agent: {args.agent_name}, "
        f"Type: {args.callback_type}"
    )
    callback_evals = CallbackEvals()

    try:
        results = callback_evals.test_single_callback_for_agent(
            app_name=args.app_name,
            agent_name=args.agent_name,
            callback_type=args.callback_type,
            test_file_path=args.test_file_path,
            log_file=args.log_file,
            pytest_args=args.pytest_args,
        )
        if results.empty:
            print(f"No valid callback tests found at {args.test_file_path}")
            sys.exit(1)

        # Check overall status
        failed_count = sum(1 for r in results["status"] if r != "PASSED")

        if failed_count > 0:
            print(f"\nFINAL RESULT: FAIL ({failed_count} callbacks failed)")
            sys.exit(1)
        else:
            print(f"\nFINAL RESULT: PASS (All {len(results)} callbacks passed)")
            sys.exit(0)

    except Exception as e:
        print(f"Failed to run callback tests: {e}")
        sys.exit(1)


def ci_test(args: argparse.Namespace) -> None:
    """Handles the 'ci-test' command."""

    print("Starting CI Test Lifecycle...")

    if hasattr(args, "display_name") and args.display_name:
        temp_display_name = args.display_name
    else:
        temp_display_name = f"[CI] PR Test {uuid.uuid4().hex[:8]}"

    args.display_name = temp_display_name
    args.app_name = None  # Force create by default

    apps_client = Apps(project_id=args.project_id, location=args.location)

    existing_app = apps_client.get_app_by_display_name(temp_display_name)
    if existing_app:
        print(f"Found existing temp agent: {existing_app.name}. Updating...")
        args.app_name = existing_app.name

    temp_app_name = app_push(args)

    if not temp_app_name:
        print("Failed to get deployed temp app name. CI Test aborting.")
        sys.exit(1)

    try:
        # Run test-tools

        test_file = os.path.join(args.app_dir, "tests", "tool_tests.yaml")
        if os.path.exists(test_file):
            print(f"\\n--- Running Tool Tests on {temp_app_name} ---")
            cmd = [
                "cxas",
                "test-tools",
                "--app-name",
                temp_app_name,
                "--test-file",
                test_file,
            ]
            print(f"Executing: {' '.join(cmd)}")
            res = subprocess.run(cmd, check=False)
            if res.returncode != 0:
                print("Tool tests failed.")
                sys.exit(1)

        # We must evaluate using the API or SDK
        print(f"\\n--- Running Evaluations on {temp_app_name} ---")

        evals_client = Evaluations(app_name=temp_app_name)
        evals_map = evals_client.get_evaluations_map()

        if not evals_map or (
            not evals_map.get("goldens") and not evals_map.get("scenarios")
        ):
            print("No evaluations found in the temp app. Skipping run_eval.")
        else:
            all_eval_ids = list(evals_map.get("goldens", {}).values()) + list(
                evals_map.get("scenarios", {}).values()
            )
            for eval_id in all_eval_ids:
                cmd = [
                    "cxas",
                    "run",
                    "--app-name",
                    temp_app_name,
                    "--evaluation-id",
                    eval_id,
                    "--wait",
                    "--filter-auto-metrics",
                ]
                print(f"Executing: {' '.join(cmd)}")
                res = subprocess.run(cmd, check=False)
                if res.returncode != 0:
                    print(f"Evaluation '{eval_id}' failed.")
                    sys.exit(1)

        print(
            "\\nCI Test Lifecycle Completed Successfully! "
            "Temp agent persists for review."
        )

    except Exception as e:
        print(f"Failed to execute CI Tests: {e}")
        sys.exit(1)


def local_test(args: argparse.Namespace) -> None:
    """Handles the 'local-test' command."""

    agent_dir = os.path.abspath(args.app_dir)
    agent_name = (
        os.path.basename(agent_dir.rstrip(os.sep)).lower().replace(" ", "-")
    )
    tag = f"{agent_name}-local-test"

    print(f"Building Docker image for {agent_name}...")
    # Compilation requires executing from the root agent directory
    build_cmd = ["docker", "build", "-t", tag, agent_dir]
    if subprocess.call(build_cmd) != 0:
        print("Docker build failed.")
        sys.exit(1)

    print("Running tests in Docker container...")

    # Detect ADC
    home = os.path.expanduser("~")
    # Default gcloud location
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not adc_path:
        adc_path = os.path.join(
            home, ".config/gcloud/application_default_credentials.json"
        )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{agent_dir}:/workspace",
        "-w",
        "/workspace",
        "-e",
        f"PROJECT_ID={args.project_id}",
        "-e",
        f"LOCATION={args.location}",
    ]

    oauth_token = os.environ.get("CXAS_OAUTH_TOKEN")

    if oauth_token:
        print("Using provided CXAS_OAUTH_TOKEN.")
        docker_cmd.extend(["-e", "CXAS_OAUTH_TOKEN"])
    elif os.path.exists(adc_path):
        print(f"Mounting credentials from {adc_path}")
        docker_cmd.extend(
            [
                "-e",
                "GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/adc.json",
                "-v",
                f"{adc_path}:/tmp/keys/adc.json:ro",
            ]
        )
    else:
        print(
            "Warning: Application Default Credentials not found. "
            "Authentication may fail."
        )

    display_name = f"[Local] {agent_name}"

    # The command passed to the container
    inner_cmd = [
        tag,
        "ci-test",
        "--app-dir",
        "/workspace",
        "--project-id",
        args.project_id,
        "--location",
        args.location,
        "--display-name",
        display_name,
    ]

    env_file = getattr(args, "env_file", None)
    if env_file:
        inner_cmd.extend(["--env-file", env_file])

    docker_cmd.extend(inner_cmd)

    print(f"Executing: {' '.join(docker_cmd)}")
    sys.exit(subprocess.call(docker_cmd))


def get_parser() -> argparse.ArgumentParser:
    """Sets up the argument parser."""
    parser = argparse.ArgumentParser(
        description="CXAS SCRAPI Evaluation Runner for CI/CD.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--oauth-token",
        help=(
            "Optional: OAuth token string for CES API authentication. "
            "Alternatively, set CXAS_OAUTH_TOKEN env var."
        ),
        required=False,
    )

    def _add_project_location_args(
        subparser: argparse.ArgumentParser, required: bool = True
    ) -> None:
        """Helper to add standard GCP args to subparsers."""
        help_suffix = "" if required else " (Optional if using Display Name)"
        subparser.add_argument(
            "--project-id",
            required=required,
            help=f"The GCP Project ID.{help_suffix}",
        )
        subparser.add_argument(
            "--location",
            required=required,
            help=f"The GCP Location (e.g., global, us-central1).{help_suffix}",
        )

    subparsers = parser.add_subparsers(
        title="Commands", dest="command", required=True
    )

    # Parser for 'init-github-action'
    parser_init_gh = subparsers.add_parser(
        "init-github-action",
        help="Generate a GitHub Actions workflow file for testing the agent.",
    )
    parser_init_gh.add_argument(
        "--app-dir",
        help=(
            "Optional: The path to the app directory (e.g., 'pilot') "
            "to extract app_name and agent_name from app.yaml."
        ),
    )
    parser_init_gh.add_argument(
        "--app-name",
        help=(
            "Optional: The CXAS App ID (projects/.../apps/...). "
            "If missing, extracts from app_dir/app.yaml."
        ),
    )
    parser_init_gh.add_argument(
        "--agent-name",
        help=(
            "Optional: The name of the agent directory to scope the workflow "
            "to (e.g., 'pilot')."
        ),
    )

    parser_init_gh.add_argument(
        "--workload-identity-provider",
        help="Optional: GCP Workload Identity Provider string.",
    )
    parser_init_gh.add_argument(
        "--service-account",
        help="Optional: GCP Service Account email.",
    )
    parser_init_gh.add_argument(
        "--output",
        help=(
            "Optional: Override path where the workflow file will be saved. "
            "Defaults to .github/workflows/test_{agent_name}.yml"
        ),
    )

    _add_project_location_args(parser_init_gh, required=False)

    parser_init_gh.add_argument(
        "--branch",
        default="main",
        help=(
            "Optional: Target branch for deploy trigger (e.g. main). "
            "Defaults to 'main'."
        ),
    )
    parser_init_gh.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Optional: Skip generation of the cleanup workflow.",
    )
    parser_init_gh.add_argument(
        "--install-hook",
        action="store_true",
        help=(
            "Optional: Install a git pre-push hook to run local-test "
            "automatically."
        ),
    )
    parser_init_gh.add_argument(
        "--auto-create-wif",
        action="store_true",
        help=(
            "Optional: Automatically create Workload "
            "Identity Pool, Provider, and Service "
            "Account on Google Cloud."
        ),
    )
    parser_init_gh.add_argument(
        "--wif-pool-name",
        default="github-actions-pool-scrapi",
        help="Optional: The name of the Workload Identity Pool to create/use.",
    )
    parser_init_gh.add_argument(
        "--github-repo",
        help=(
            "Optional: Override inferred GitHub repository (e.g., owner/repo)."
        ),
    )

    parser_init_gh.set_defaults(func=init_github_action)

    parser_test_tools = subparsers.add_parser(
        "test-tools",
        help="Run local tool unit tests against the deployed agent.",
    )
    parser_test_tools.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_test_tools.add_argument(
        "--test-file",
        required=True,
        help="Path to the YAML/JSON file containing tool test definitions.",
    )
    parser_test_tools.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for tool executions.",
    )

    parser_test_tools.set_defaults(func=test_tools)

    # Parser for 'test-callbacks'
    parser_test_callbacks = subparsers.add_parser(
        "test-callbacks",
        help="Run local callback unit tests against the deployed agent.",
    )
    parser_test_callbacks.add_argument(
        "--app-dir",
        required=True,
        help="The path to the app directory.",
    )
    parser_test_callbacks.add_argument(
        "--agent-name",
        required=False,
        help="Optional: The name of the agent to run callback tests for.",
    )
    parser_test_callbacks.add_argument(
        "--callback-type",
        required=False,
        help="Optional: The type of callback to run tests for.",
    )
    parser_test_callbacks.add_argument(
        "--callback-name",
        required=False,
        help="Optional: The name of the callback to run tests for.",
    )
    parser_test_callbacks.add_argument(
        "--log-file",
        required=False,
        help="Optional: Path to a file to log pytest output to.",
    )
    parser_test_callbacks.add_argument(
        "--pytest-args",
        type=lambda s: [item for item in s.split(",")],
        help='Comma-separated list (e.g., "-v,-s")',
    )

    parser_test_callbacks.set_defaults(func=test_callbacks)

    # Parser for 'test-single-callback'
    parser_test_single_callback = subparsers.add_parser(
        "test-single-callback",
        help="Run local callback unit tests against the deployed agent.",
    )
    parser_test_single_callback.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_test_single_callback.add_argument(
        "--agent-name",
        required=True,
        help="Optional: The name of the agent to run callback tests for.",
    )
    parser_test_single_callback.add_argument(
        "--callback-type",
        required=True,
        help="Optional: The type of callback to run tests for.",
    )
    parser_test_single_callback.add_argument(
        "--test-file-path",
        required=True,
        help="Path to the test python file to run.",
    )
    parser_test_single_callback.add_argument(
        "--log-file",
        required=False,
        help="Optional: Path to a file to log pytest output to.",
    )
    parser_test_single_callback.add_argument(
        "--pytest-args",
        type=lambda s: [item for item in s.split(",")],
        help='Comma-separated list (e.g., "-v,-s")',
    )

    parser_test_single_callback.set_defaults(func=test_single_callback)

    # Parser for 'export'
    parser_export = subparsers.add_parser(
        "export", help="Export an evaluation to YAML or JSON format."
    )
    parser_export.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_export.add_argument(
        "--evaluation-id",
        required=True,
        help=(
            "The evaluation resource name "
            "(projects/.../locations/.../apps/.../evaluations/...)."
        ),
    )
    parser_export.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Export format (yaml or json). Defaults to yaml.",
    )
    parser_export.add_argument(
        "--output",
        help=(
            "Path to save the exported evaluation. "
            "If not provided, prints to stdout."
        ),
    )

    parser_export.set_defaults(func=export_eval)

    # Parser for 'push'
    parser_push_eval = subparsers.add_parser(
        "push-eval", help="Push evaluation(s) from a YAML file to the app."
    )
    parser_push_eval.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_push_eval.add_argument(
        "--file",
        required=True,
        help="Path to the YAML file containing evaluation definitions.",
    )
    parser_push_eval.set_defaults(func=push_eval)

    # Parser for 'run'
    parser_run = subparsers.add_parser(
        "run", help="Run an evaluation and assert results."
    )
    parser_run.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_run.add_argument(
        "--evaluation-id",
        required=False,
        help=(
            "The evaluation resource name "
            "(projects/.../locations/.../apps/.../evaluations/...)."
        ),
    )
    parser_run.add_argument(
        "--modality",
        choices=["text", "audio"],
        default="text",
        help="Evaluation execution modality (text or audio). Defaults to text.",
    )
    parser_run.add_argument(
        "--display-name-prefix",
        required=False,
        help="Run all tests whose display name starts with this string.",
    )
    parser_run.add_argument(
        "--tags",
        nargs="+",
        default=[],
        help=(
            "Space-separated list of tags. Runs tests "
            "containing any of these tags."
        ),
    )
    parser_run.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Wait for evaluation to complete and return exit code 0 "
            "on pass or 1 on fail."
        ),
    )
    parser_run.add_argument(
        "--filter-auto-metrics",
        action="store_true",
        help=(
            "Filter out automated metrics (semantic similarity, "
            "hallucination) and only evaluate custom expectations."
        ),
    )

    parser_run.set_defaults(func=run_eval)

    # Parser for 'ci-test'
    parser_ci_test = subparsers.add_parser(
        "ci-test", help="Runs standard integration tests on a temporary agent."
    )
    parser_ci_test.add_argument(
        "--app-dir",
        default=".",
        help=(
            "Path to the app directory to test. Defaults to current directory."
        ),
    )
    parser_ci_test.add_argument(
        "--display-name",
        help=(
            "Optional: Deterministic display name for the temp agent "
            "(e.g. [CI] PR-123). Overwrites existing."
        ),
    )
    parser_ci_test.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    _add_project_location_args(parser_ci_test)
    parser_ci_test.set_defaults(func=ci_test)

    # Parser for 'delete'
    parser_delete = subparsers.add_parser(
        "delete", help="Deletes a specified agent/app."
    )
    parser_delete.add_argument(
        "--app-name",
        help=(
            "The CXAS App ID (projects/.../locations/.../apps/...). "
            "Required if --display-name not provided."
        ),
    )
    parser_delete.add_argument(
        "--display-name",
        help=(
            "The Display Name of the app to delete. "
            "Required if --app-name not provided."
        ),
    )
    _add_project_location_args(parser_delete, required=False)
    parser_delete.add_argument(
        "--force",
        action="store_true",
        help="Force delete even if there are child resources.",
    )
    parser_delete.set_defaults(func=app_delete)

    # Parser for 'local-test'
    parser_local_test = subparsers.add_parser(
        "local-test", help="Runs the agent tests locally using Docker."
    )
    parser_local_test.add_argument(
        "--app-dir",
        default=".",
        help="Path to the app directory. Defaults to current directory.",
    )
    parser_local_test.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    _add_project_location_args(parser_local_test)
    parser_local_test.set_defaults(func=local_test)

    # Parser for 'pull'
    parser_pull = subparsers.add_parser(
        "pull", help="Export an app to a local directory."
    )
    parser_pull.add_argument("app", help="App Resource Name or Display Name.")
    parser_pull.add_argument(
        "--target-dir", default=".", help="Directory to extract to."
    )
    _add_project_location_args(parser_pull, required=False)
    parser_pull.set_defaults(func=app_pull)

    # Parser for 'push'
    parser_push = subparsers.add_parser(
        "push", help="Import local files back to CXAS."
    )
    parser_push.add_argument(
        "--app-dir", default=".", help="Local app directory."
    )
    parser_push.add_argument(
        "--to", help="Target App Resource Name or Display Name."
    )
    parser_push.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    parser_push.add_argument(
        "--app-name",
        help="Target App ID to explicitly push to (v1beta API).",
    )
    parser_push.add_argument(
        "--display-name",
        help="Display name for a new App if --to is not provided.",
    )
    _add_project_location_args(parser_push, required=False)
    parser_push.set_defaults(func=app_push)

    # Parser for 'lint'
    parser_lint = subparsers.add_parser(
        "lint",
        help="Lint an app directory for best practices and structural issues.",
    )
    parser_lint.add_argument(
        "--app-dir",
        default=".",
        help="Path to the app directory to lint (default: current directory).",
    )
    parser_lint.add_argument(
        "--fix",
        action="store_true",
        help="Show fix suggestions for each issue.",
    )
    parser_lint.add_argument(
        "--only",
        choices=[
            "instructions",
            "callbacks",
            "tools",
            "evals",
            "config",
            "structure",
            "schema",
        ],
        help="Only run a specific linter category.",
    )
    parser_lint.add_argument(
        "--rule",
        type=str,
        help="Run specific rules only (comma-separated IDs, e.g. I003,C005).",
    )
    parser_lint.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON.",
    )
    parser_lint.add_argument(
        "--list-rules",
        action="store_true",
        help="List all available lint rules.",
    )
    parser_lint.add_argument(
        "--validate-only",
        action="store_true",
        help="Run only structure and config rules.",
    )
    parser_lint.add_argument(
        "--agent",
        help="Validate a single agent directory against CES schema.",
    )
    parser_lint.add_argument(
        "--tool",
        help="Validate a single tool directory against CES schema.",
    )
    parser_lint.add_argument(
        "--toolset",
        help="Validate a single toolset directory against CES schema.",
    )
    parser_lint.add_argument(
        "--guardrail",
        help="Validate a single guardrail directory against CES schema.",
    )
    parser_lint.add_argument(
        "--evaluation",
        help="Validate a single evaluation directory against CES schema.",
    )
    parser_lint.add_argument(
        "--evaluation-expectations",
        help=(
            "Validate a single evaluation expectations"
            " directory against CES schema."
        ),
    )
    parser_lint.set_defaults(func=app_lint)

    # Parser for 'init'
    parser_init = subparsers.add_parser(
        "init",
        help="Initialize a project with CXAS agent development skills "
        "(.agents, .claude, .gemini, AGENTS.md, etc.).",
    )
    parser_init.add_argument(
        "--target-dir",
        default=".",
        help="Directory to install skills into (default: current directory).",
    )
    parser_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files without prompting.",
    )
    parser_init.set_defaults(func=app_init)

    # Parser for 'create'
    parser_create = subparsers.add_parser("create", help="Create a new app.")
    parser_create.add_argument("name", help="Display name of the new app.")
    parser_create.add_argument(
        "--description", help="Description for the new app."
    )
    parser_create.add_argument(
        "--app-name", help="Optional specific app_name to use."
    )
    _add_project_location_args(parser_create)
    parser_create.set_defaults(func=app_create)

    # Parser for 'branch'
    parser_branch = subparsers.add_parser(
        "branch", help="Branch an app (pull -> create -> push)."
    )
    parser_branch.add_argument(
        "source", help="Source App Resource Name or Display Name."
    )
    parser_branch.add_argument(
        "--new-name", required=True, help="Display name of the new branch app."
    )
    _add_project_location_args(parser_branch)
    parser_branch.set_defaults(func=app_branch)

    # Subparsers for 'apps'
    parser_apps = subparsers.add_parser("apps", help="Manage apps (list, get).")
    apps_subparsers = parser_apps.add_subparsers(
        title="Apps Commands", dest="apps_command", required=True
    )

    parser_apps_list = apps_subparsers.add_parser("list", help="List all apps.")
    _add_project_location_args(parser_apps_list)
    parser_apps_list.set_defaults(func=apps_list)

    parser_apps_get = apps_subparsers.add_parser("get", help="Get app details.")
    parser_apps_get.add_argument(
        "app",
        help="App Resource Name or Display Name.",
    )
    _add_project_location_args(parser_apps_get, required=False)
    parser_apps_get.set_defaults(func=apps_get)

    # Subparsers for 'local'
    parser_local = subparsers.add_parser(
        "local", help="Local workspace operations."
    )
    local_subparsers = parser_local.add_subparsers(
        title="Local Commands", dest="local_command", required=True
    )

    parser_local_create = local_subparsers.add_parser(
        "create", help="Create local templates for CXAS components."
    )
    local_create_subparsers = parser_local_create.add_subparsers(
        title="Create Local Commands",
        dest="create_local_command",
        required=True,
    )

    parser_local_create_agent = local_create_subparsers.add_parser(
        "agent", help="Create local agent template."
    )
    parser_local_create_agent.add_argument(
        "name", help="Display name of the agent."
    )
    parser_local_create_agent.add_argument(
        "--app-dir", default=".", help="App directory."
    )
    parser_local_create_agent.set_defaults(func=handle_local_create)

    parser_local_create_tool = local_create_subparsers.add_parser(
        "tool", help="Create local tool template."
    )
    parser_local_create_tool.add_argument(
        "name", help="Display name of the tool."
    )
    parser_local_create_tool.add_argument(
        "tool_type", nargs="?", help="Type of tool (e.g., PYTHON)."
    )
    parser_local_create_tool.add_argument(
        "--add-to-agent", nargs="?", help="Agent to add the tool to."
    )
    parser_local_create_tool.add_argument(
        "--app-dir", default=".", help="App directory."
    )
    parser_local_create_tool.set_defaults(func=handle_local_create)

    # Subparsers for 'insights'
    parser_insights = subparsers.add_parser(
        "insights",
        help="Perform high-level CXAS Insights and Quality AI operations.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    populate_insights_parser(parser_insights)

    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    if getattr(args, "oauth_token", None):
        os.environ["CXAS_OAUTH_TOKEN"] = args.oauth_token

    # Configure logging
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
