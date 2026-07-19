from __future__ import annotations

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

"""CLI command handlers for evaluations and testing."""


import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

from cxas_scrapi.cli.utils import LazyCallable, to_dataclass

ToolEvals = LazyCallable("cxas_scrapi.evals.tool_evals", "ToolEvals")
CallbackEvals = LazyCallable(
    "cxas_scrapi.evals.callback_evals", "CallbackEvals"
)


@dataclass(frozen=False)
class RunEvalConfig:
    """Configuration for running evaluations."""

    app_name: str | None = None
    evaluation_id: str | None = None
    display_name_prefix: str | None = None
    tags: list[str] | str | tuple[str, ...] | None = None
    modality: str = "text"
    golden_run_method: str = "STABLE"
    wait: bool = False
    filter_auto_metrics: bool = False


@dataclass(frozen=False)
class ExportEvalConfig:
    """Configuration for exporting evaluations."""

    app_name: str
    evaluation_id: str
    format: str = "yaml"
    output: str | None = None


@dataclass(frozen=False)
class PushEvalConfig:
    """Configuration for pushing evaluations."""

    app_name: str
    eval_file: str | None = None
    file: str | None = None


@dataclass(frozen=False)
class EvalsReportConfig:
    """Configuration for generating evals reports."""

    output_dir: str = "."
    input_dir: str | None = None
    gcs_path: str | None = None
    output: str | None = None
    include: list[str] | str | tuple[str, ...] | None = None
    golden_run: str | None = None
    app_name: str | None = None
    app_dir: str | None = None
    tool_test_file: str | None = None
    goldens_dir: str | None = None
    simulation_dir: str | None = None
    sim_user_model: str | None = None
    eval_model: str | None = None
    modality: str = "text"
    runs: int = 1
    filter_files: list[str] | str | tuple[str, ...] | None = None
    filter_tags: list[str] | str | tuple[str, ...] | None = None
    filter_names: list[str] | str | tuple[str, ...] | None = None
    run: bool = False
    sim_parallel: int = 5
    golden_timeout: float | int = 600.0
    json_progress: bool = False
    timestamped: bool = False
    bg_noise_file: str | None = None
    burst_noise_files: list[str] | str | tuple[str, ...] | None = None
    use_tool_fakes: bool = False
    expectations_only: bool = False
    deployment_id: str | None = None
    capture_agent_audio: bool = False


@dataclass(frozen=False)
class TestToolsConfig:
    """Configuration for testing tools."""

    app_name: str
    test_file: str
    debug: bool = False


@dataclass(frozen=False)
class TestCallbacksConfig:
    """Configuration for testing callbacks."""

    app_dir: str = "."
    app_name: str | None = None
    test_file: str | None = None
    agent_name: str | None = None
    callback_type: str | None = None
    callback_name: str | None = None
    log_file: str | None = None
    pytest_args: list[str] | str | tuple[str, ...] | None = None


@dataclass(frozen=False)
class TestSingleCallbackConfig:
    """Configuration for testing a single callback."""

    app_dir: str = "."
    app_name: str | None = None
    agent_name: str | None = None
    callback_type: str | None = None
    callback_name: str | None = None
    test_file_path: str | None = None
    log_file: str | None = None
    pytest_args: list[str] | str | tuple[str, ...] | None = None


@dataclass(frozen=False)
class CITestConfig:
    """Configuration for CI test lifecycle."""

    app_dir: str = "."
    project_id: str | None = None
    location: str | None = None
    display_name: str | None = None
    app_name: str | None = None


@dataclass(frozen=False)
class LocalTestConfig:
    """Configuration for local test lifecycle."""

    app_dir: str = "."
    project_id: str | None = None
    location: str | None = None
    env_file: str | None = None


def export_eval(config: ExportEvalConfig | Any) -> None:
    """Handles the 'export' command.

    Args:
        config: Export evaluation configuration object or arguments namespace.
    """
    args = to_dataclass(ExportEvalConfig, config)
    print(f"Exporting evaluation: {args.evaluation_id}")

    from cxas_scrapi.core.evaluations import Evaluations, ExportFormat

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


def push_eval(config: PushEvalConfig | Any) -> None:
    """Handles the 'push-eval' command.

    Args:
        config: Push evaluation configuration object or arguments namespace.
    """
    args = to_dataclass(PushEvalConfig, config)
    target_file = args.eval_file or args.file
    print(f"Pushing evaluation(s) from {target_file} to App: {args.app_name}")

    from cxas_scrapi.core.evaluations import Evaluations
    from cxas_scrapi.utils.eval_utils import EvalUtils

    eval_client = Evaluations(app_name=args.app_name)
    eval_utils = EvalUtils(app_name=args.app_name)

    try:
        evals = eval_utils.load_golden_evals_from_yaml(target_file)
        if not evals:
            print(f"No valid evaluations found in '{target_file}'.")
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
    eval_utils: Any,
    old_result_ids: list[str],
    app_name: str,
    expected_count: int = 1,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Waits for all new evaluation results to appear."""
    import pandas as pd

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


def filter_metrics_and_assess(
    df_dict_new_run: dict[str, Any],
    filter_auto_metrics: bool,
) -> bool:
    """Assesses the evaluation run and returns True if passed,
    False otherwise."""
    import pandas as pd

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


def run_eval(config: RunEvalConfig | Any) -> None:
    """Handles the 'run' command.

    Args:
        config: Run evaluation configuration object or arguments namespace.
    """
    args = to_dataclass(RunEvalConfig, config)
    import pandas as pd

    from cxas_scrapi.core.evaluations import Evaluations
    from cxas_scrapi.utils.eval_utils import EvalUtils

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
            evaluations=evaluations_to_run,
            app_name=args.app_name,
            modality=getattr(args, "modality", "text"),
            golden_run_method=getattr(args, "golden_run_method", "STABLE"),
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


def _to_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return []


def combined_evals_report_cmd(config: EvalsReportConfig | Any) -> None:
    """Handles the 'evals report' command.

    Args:
        config: Evals report configuration object or arguments namespace.
    """
    args = to_dataclass(EvalsReportConfig, config)
    from cxas_scrapi.utils.reporting import (  # noqa: PLC0415
        generate_combined_report_from_dir,
    )

    output_dir = args.output_dir
    timestamp = None
    if getattr(args, "timestamped", False):
        import datetime  # noqa: PLC0415

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    output_path = args.gcs_path or args.output

    include_list = _to_list(args.include)
    filter_files_list = _to_list(args.filter_files)
    filter_tags_list = _to_list(args.filter_tags)
    filter_names_list = _to_list(args.filter_names)

    if getattr(args, "input_dir", None):
        if args.tool_test_file == "evals/tool_tests/":
            args.tool_test_file = os.path.join(args.input_dir, "tool_tests/")
        if args.goldens_dir == "evals/goldens/":
            args.goldens_dir = os.path.join(args.input_dir, "goldens/")
        if args.simulation_dir == "evals/simulations/":
            args.simulation_dir = os.path.join(args.input_dir, "simulations/")

    sim_parallel = getattr(args, "sim_parallel", 5)
    golden_timeout = getattr(args, "golden_timeout", 600)

    progress_callback = None
    if getattr(args, "json_progress", False):

        def progress_callback(stage: str, current: int, total: int):
            import json  # noqa: PLC0415

            sys.stderr.write(
                json.dumps(
                    {
                        "progress": {
                            "stage": stage,
                            "current": current,
                            "total": total,
                        }
                    }
                )
                + "\n"
            )
            sys.stderr.flush()

    actual_output_path = generate_combined_report_from_dir(
        output_dir=output_dir,
        golden_run=args.golden_run,
        app_name=args.app_name,
        output_path=output_path,
        run=args.run,
        app_dir=args.app_dir,
        tool_test_file=args.tool_test_file,
        goldens_dir=args.goldens_dir,
        simulation_dir=args.simulation_dir,
        include=include_list,
        modality=args.modality,
        sim_user_model=args.sim_user_model,
        eval_model=args.eval_model,
        runs=args.runs,
        filter_files=filter_files_list,
        filter_tags=filter_tags_list,
        filter_names=filter_names_list,
        parallel=sim_parallel,
        golden_timeout=golden_timeout,
        bg_noise_file=getattr(args, "bg_noise_file", None),
        burst_noise_files=_to_list(args.burst_noise_files)
        if args.burst_noise_files
        else None,
        use_tool_fakes=getattr(args, "use_tool_fakes", False),
        timestamp=timestamp,
        expectations_only=getattr(args, "expectations_only", False),
        deployment_id=getattr(args, "deployment_id", None),
        progress_callback=progress_callback,
        capture_agent_audio=getattr(args, "capture_agent_audio", False),
    )
    print(f"Combined report generated at {actual_output_path}")


def test_tools(config: TestToolsConfig | Any) -> None:
    """Handles the 'test-tools' command.

    Args:
        config: Test tools configuration object or arguments namespace.
    """
    args = to_dataclass(TestToolsConfig, config)
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


def test_callbacks(config: TestCallbacksConfig | Any) -> None:
    """Handles the 'test-callbacks' command.

    Args:
        config: Test callbacks configuration object or arguments namespace.
    """
    args = to_dataclass(TestCallbacksConfig, config)
    print(f"Running callback tests in App directory: {args.app_dir}")
    callback_evals = CallbackEvals()

    try:
        results = callback_evals.test_all_callbacks_in_app_dir(
            app_dir=args.app_dir,
            agent_name=args.agent_name,
            callback_type=args.callback_type,
            callback_name=args.callback_name,
            log_file=args.log_file,
            pytest_args=_to_list(args.pytest_args)
            if args.pytest_args
            else None,
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


def test_single_callback(config: TestSingleCallbackConfig | Any) -> None:
    """Handles the 'test-single-callback' command.

    Args:
        config: Test single callback configuration object or arguments namespace.
    """
    args = to_dataclass(TestSingleCallbackConfig, config)
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
            pytest_args=_to_list(args.pytest_args)
            if args.pytest_args
            else None,
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


def ci_test(config: CITestConfig | Any) -> None:
    """Handles the 'ci-test' command.

    Args:
        config: CI test configuration object or arguments namespace.
    """
    args = to_dataclass(CITestConfig, config)
    from cxas_scrapi.cli.app import AppPushConfig, PushResult, app_push

    print("Starting CI Test Lifecycle...")

    if hasattr(args, "display_name") and args.display_name:
        temp_display_name = args.display_name
    else:
        temp_display_name = f"[CI] PR Test {uuid.uuid4().hex[:8]}"

    push_args = AppPushConfig(
        app_dir=args.app_dir,
        display_name=temp_display_name,
        project_id=args.project_id,
        location=args.location,
        app_name=args.app_name,
    )
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.evaluations import Evaluations

    apps_client = Apps(project_id=args.project_id, location=args.location)
    existing_app = apps_client.get_app_by_display_name(temp_display_name)
    if existing_app:
        print(f"Found existing temp agent: {existing_app.name}. Updating...")
        push_args.app_name = existing_app.name

    push_res = app_push(push_args)
    if isinstance(push_res, PushResult):
        temp_app_name = push_res.app_name
    else:
        temp_app_name = push_res

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


def local_test(config: LocalTestConfig | Any) -> None:
    """Handles the 'local-test' command.

    Args:
        config: Local test configuration object or arguments namespace.
    """
    args = to_dataclass(LocalTestConfig, config)
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


import click


@click.group(name="evals")
def evals_group() -> None:
    """Manage and report on evaluations."""


@evals_group.command(name="report")
@click.option("--output-dir", help="Output directory path.")
@click.option("--sim-user-model", help="Simulated user Gemini model.")
@click.option("--eval-model", help="Evaluator Gemini model.")
@click.option("--run", is_flag=True, help="Run evals before generating report.")
@click.option(
    "--timestamped", is_flag=True, help="Create timestamped report output."
)
@click.pass_context
def evals_report_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Generate evaluations report."""
    cfg = to_dataclass(EvalsReportConfig, ctx, **kwargs)
    combined_evals_report_cmd(cfg)
