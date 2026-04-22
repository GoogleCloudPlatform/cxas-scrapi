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

"""Eval runner using cxas-scrapi SDK.

Usage:
  python scripts/scrapi-eval-runner.py push [--priority P0]
  python scripts/scrapi-eval-runner.py push-goldens <yaml_file_or_dir>
  python scripts/scrapi-eval-runner.py run [--priority P0] [--channel audio] [--runs 5]
  python scripts/scrapi-eval-runner.py run-goldens [--channel text] [--runs 1]
  python scripts/scrapi-eval-runner.py results <run_id>
  python scripts/scrapi-eval-runner.py status
  python scripts/scrapi-eval-runner.py report <run_id>
"""

import argparse
import sys
import time
import yaml
import json
import os
from datetime import datetime

import pandas as pd
from cxas_scrapi.core.evaluations import Evaluations
from cxas_scrapi.utils.eval_utils import EvalUtils
from config import load_app_name, get_project_path


EVALS_YAML = get_project_path("evals", "scenarios", "scenarios.yaml")
GOLDEN_EVALS_DIR = get_project_path("evals", "goldens")
REPORTS_DIR = get_project_path("eval-reports")


def load_yaml():
    if not os.path.exists(EVALS_YAML):
        return {"meta": {}, "evals": []}
    with open(EVALS_YAML, "r") as f:
        return yaml.safe_load(f) or {"meta": {}, "evals": []}


def save_yaml(data):
    os.makedirs(os.path.dirname(EVALS_YAML), exist_ok=True)
    with open(EVALS_YAML, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=200)


def get_app_name():
    return load_app_name()


def get_evals_client():
    return Evaluations(app_name=load_app_name())


def get_eval_utils():
    return EvalUtils(app_name=load_app_name())


def filter_evals(data, priority=None, tag=None):
    evals = data.get("evals", []) if isinstance(data, dict) else data
    if priority:
        evals = [e for e in evals if e.get("priority", "").upper() == priority.upper()]
    if tag:
        evals = [e for e in evals if tag in e.get("tags", [])]
    return evals


# --- Commands ---

def cmd_status(args):
    """Show sync status between YAML and platform."""
    data = load_yaml()
    client = get_evals_client()
    evals_map = client.get_evaluations_map(reverse=True)

    # Flatten platform evals (both goldens and scenarios)
    platform = {}
    for category in ["goldens", "scenarios"]:
        for name, resource in evals_map.get(category, {}).items():
            platform[name] = resource

    yaml_evals = filter_evals(data, args.priority, getattr(args, 'tag', None))

    print(f"{'Eval Name':45s} {'YAML':6s} {'Platform':10s} {'Synced':8s} {'Score':8s}")
    print("-" * 85)
    seen = set()
    for ev in yaml_evals:
        name = ev["name"]
        seen.add(name)
        on_platform = name in platform
        yaml_id = ev.get("eval_id", "")
        platform_id = platform.get(name, "").split("/")[-1] if on_platform else ""
        synced = "Yes" if yaml_id and platform_id and yaml_id == platform_id else "No"
        score = ev.get("last_run_score", "-") or "-"
        print(f"  {name:43s} {'Yes':6s} {'Yes' if on_platform else 'No':10s} {synced:8s} {score:8s}")

    # Platform-only evals
    for name in sorted(platform.keys()):
        if name not in seen:
            print(f"  {name:43s} {'No':6s} {'Yes':10s} {'-':8s} {'-':8s}")


def cmd_push(args):
    """Push YAML evals to platform (create or delete-and-recreate)."""
    data = load_yaml()
    client = get_evals_client()
    meta = data.get("meta", {})
    app_name = get_app_name()

    # Get current platform state
    evals_map = client.get_evaluations_map(reverse=True)
    platform = {}
    for category in ["goldens", "scenarios"]:
        for name, resource in evals_map.get(category, {}).items():
            platform[name] = resource

    yaml_evals = filter_evals(data, args.priority, getattr(args, 'tag', None))
    print(f"Pushing {len(yaml_evals)} evals to platform...")

    for ev in yaml_evals:
        name = ev["name"]

        # Build scenario payload
        scenario = {
            "task": ev["task"].strip(),
            "maxTurns": ev["max_turns"],
            "variableOverrides": ev.get("variables", {}),
        }

        comp = ev.get("completion", "TASK_SATISFIED")
        if comp != "DEFAULT":
            scenario["taskCompletionBehavior"] = comp

        # Resolve tool expectations
        expect_tools = ev.get("expect_tools", [])
        meta_tools = meta.get("tools", {})
        if expect_tools and meta_tools:
            scenario["scenarioExpectations"] = [
                {"toolExpectation": {"expectedToolCall": {"tool": meta_tools[t]}}}
                for t in expect_tools if t in meta_tools
            ]

        # Resolve LLM criteria expectations
        expect_criteria = ev.get("expect_criteria", [])
        meta_expectations = meta.get("expectations", {})
        if expect_criteria and meta_expectations:
            scenario["evaluationExpectations"] = [
                meta_expectations[c] for c in expect_criteria if c in meta_expectations
            ]

        eval_payload = {"displayName": name, "scenario": scenario}

        # Add tags if present
        tags = ev.get("tags", [])
        if tags:
            eval_payload["tags"] = tags

        # Delete existing if present
        if name in platform:
            try:
                client.delete_evaluation(platform[name], force=True)
                print(f"  Deleted existing: {name}")
            except Exception as e:
                print(f"  Warning: Failed to delete {name}: {e}")

        # Create new
        try:
            result = client.create_evaluation(eval_payload, app_name=app_name)
            new_id = result.name.split("/")[-1]
            ev["eval_id"] = new_id
            ev["last_run_score"] = None
            ev["last_run_id"] = None
            print(f"  Created: {name} -> {new_id}")
        except Exception as e:
            print(f"  FAILED: {name}: {e}")

    save_yaml(data)
    print(f"\nDone. YAML updated with new eval_ids.")


def cmd_run(args):
    """Trigger an eval run."""
    data = load_yaml()
    client = get_evals_client()
    app_name = get_app_name()

    yaml_evals = filter_evals(data, args.priority, getattr(args, 'tag', None))
    eval_names = [ev["name"] for ev in yaml_evals if ev.get("eval_id")]

    if not eval_names:
        print("No evals with eval_id found. Run 'push' first.")
        return

    channel = args.channel or "text"
    runs = args.runs or 5

    print(f"Running {len(eval_names)} evals ({channel}, {runs} runs each)...")

    try:
        run_response = client.run_evaluation(
            evaluations=eval_names,
            app_name=app_name,
            modality=channel,
            run_count=runs,
        )
        print(f"Run triggered successfully.")
        # Try to extract run ID from response
        if hasattr(run_response, 'operation') and hasattr(run_response.operation, 'name'):
            print(f"Operation: {run_response.operation.name}")
        elif hasattr(run_response, 'name'):
            print(f"Run: {run_response.name}")
        else:
            print(f"Response: {run_response}")
    except Exception as e:
        print(f"Failed to trigger run: {e}")


def _score_result_audio(result) -> bool:
    """Score a single result using audio-correct method.

    In audio mode, taskCompleted is broken (always False).
    Use goalScore AND allExpectationsSatisfied instead.
    """
    res_dict = type(result).to_dict(result) if not isinstance(result, dict) else result
    sr = res_dict.get("scenario_result", {})
    goal = sr.get("user_goal_satisfaction_result", {}).get("score", 0)
    all_exp = sr.get("all_expectations_satisfied", False)
    return (goal == 1) and all_exp


def _is_error(result) -> bool:
    res_dict = type(result).to_dict(result) if not isinstance(result, dict) else result
    exec_state = res_dict.get("execution_state", 0)
    if isinstance(exec_state, int):
        return exec_state == 3  # ERROR
    return str(exec_state).upper() in ("ERROR", "ERRORED")


def cmd_results(args):
    """Fetch and score results for a specific run."""
    data = load_yaml()
    utils = get_eval_utils()
    app_name = get_app_name()
    run_id = args.run_id
    audio = getattr(args, "audio", False)

    # Build full run resource name if just an ID
    if not run_id.startswith("projects/"):
        run_id = f"{app_name}/evaluationRuns/{run_id}"

    print(f"Fetching results for run: {run_id.split('/')[-1]}")

    try:
        results = utils.wait_for_run_and_get_results(run_id, timeout_seconds=1800)
        print(f"Got {len(results)} results.")
    except TimeoutError:
        print("Run timed out after 30 minutes.")
        return
    except Exception as e:
        print(f"Error fetching results: {e}")
        return

    # Get eval display names
    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for category in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(category, {}).items():
            name_lookup[resource] = display

    # Score results
    eval_scores = {}
    errors = 0

    for result in results:
        res_dict = type(result).to_dict(result) if not isinstance(result, dict) else result

        if _is_error(result):
            errors += 1
            continue

        # Get eval name
        result_name = res_dict.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])
        display_name = name_lookup.get(eval_resource, eval_resource.split("/")[-1])

        if audio:
            passed = _score_result_audio(result)
        else:
            # Text mode: use platform's evaluation_status
            status = res_dict.get("evaluation_status", 0)
            if isinstance(status, int):
                passed = (status == 1)  # 1 = PASS
            else:
                passed = str(status).upper() == "PASS"

        if display_name not in eval_scores:
            eval_scores[display_name] = {"pass": 0, "total": 0}
        eval_scores[display_name]["total"] += 1
        if passed:
            eval_scores[display_name]["pass"] += 1

    # Print results
    total_pass = sum(s["pass"] for s in eval_scores.values())
    total_scored = sum(s["total"] for s in eval_scores.values())
    pct = 100 * total_pass / total_scored if total_scored else 0

    scoring_method = "audio (goal+expectations)" if audio else "platform (evaluation_status)"
    print(f"\n=== Results ({scoring_method}) ===")
    print(f"Overall: {total_pass}/{total_scored} ({pct:.1f}%) | Errors: {errors}\n")

    sorted_evals = sorted(eval_scores.items(),
                          key=lambda x: x[1]["pass"] / max(x[1]["total"], 1))
    for name, s in sorted_evals:
        score = f"{s['pass']}/{s['total']}"
        marker = " <<<" if s["pass"] < s["total"] else ""
        print(f"  {score:>5}  {name}{marker}")

    # Update YAML scores
    yaml_evals = data.get("evals", [])
    run_short_id = args.run_id if not args.run_id.startswith("projects/") else args.run_id.split("/")[-1]

    for ev in yaml_evals:
        if ev["name"] in eval_scores:
            s = eval_scores[ev["name"]]
            ev["last_run_score"] = f"{s['pass']}/{s['total']}"
            ev["last_run_id"] = run_short_id

    save_yaml(data)
    print(f"\nYAML updated with scores.")


def cmd_report(args):
    """Generate a markdown report for a run."""
    data = load_yaml()
    utils = get_eval_utils()
    app_name = get_app_name()
    run_id = args.run_id

    if not run_id.startswith("projects/"):
        run_id = f"{app_name}/evaluationRuns/{run_id}"

    print(f"Generating report for run: {run_id.split('/')[-1]}")

    try:
        results = utils.wait_for_run_and_get_results(run_id, timeout_seconds=1800)
    except Exception as e:
        print(f"Error: {e}")
        return

    dfs = utils.evals_to_dataframe(results=results)
    summary_df = dfs["summary"]
    failures_df = dfs["failures"]
    metadata_df = dfs.get("metadata", pd.DataFrame())

    total = len(summary_df)
    passed = len(summary_df[summary_df["evaluation_status"] == "PASS"])
    pct = 100 * passed / total if total else 0

    # Also get latency metrics
    try:
        latency_dfs = utils.get_latency_metrics_dfs(results=results)
        latency_summary = latency_dfs.get("eval_summary", pd.DataFrame())
    except Exception:
        latency_summary = pd.DataFrame()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_short = run_id.split("/")[-1][:8]

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"scrapi_report_{timestamp}.md")

    # Build per-eval scores
    eval_scores = {}
    for _, row in summary_df.iterrows():
        name = row["display_name"]
        status = row["evaluation_status"]
        if name not in eval_scores:
            eval_scores[name] = {"pass": 0, "total": 0}
        eval_scores[name]["total"] += 1
        if status == "PASS":
            eval_scores[name]["pass"] += 1

    # Match with YAML metadata
    yaml_lookup = {}
    for ev in data.get("evals", []):
        yaml_lookup[ev["name"]] = ev

    with open(report_path, "w") as f:
        f.write(f"# Eval Report (SCRAPI)\n\n")
        f.write(f"**Run ID:** `{run_id.split('/')[-1]}`\n")
        f.write(f"**Date:** {timestamp}\n")
        f.write(f"**Pass Rate:** {pct:.1f}% ({passed}/{total})\n\n")
        f.write(f"---\n\n")

        # Results table
        f.write(f"## Results by Eval\n\n")
        f.write(f"| Score | Eval | PRD | Severity |\n")
        f.write(f"|-------|------|-----|----------|\n")

        sorted_evals = sorted(eval_scores.items(),
                              key=lambda x: x[1]["pass"] / max(x[1]["total"], 1))
        for name, s in sorted_evals:
            score = f"{s['pass']}/{s['total']}"
            ev_meta = yaml_lookup.get(name, {})
            prd = ev_meta.get("prd_id", "-")
            severity = ev_meta.get("severity", "-")
            f.write(f"| {score} | {name} | {prd} | {severity} |\n")

        # Failures
        if not failures_df.empty:
            f.write(f"\n## Failure Details\n\n")
            for name in failures_df["display_name"].unique():
                name_failures = failures_df[failures_df["display_name"] == name]
                f.write(f"\n### {name}\n\n")
                for _, row in name_failures.iterrows():
                    ftype = row.get("failure_type", "?")
                    expected = str(row.get("expected", ""))[:150]
                    actual = str(row.get("actual", ""))[:150]
                    f.write(f"- **{ftype}**: expected `{expected}` | actual `{actual}`\n")

        # Custom expectations (metadata)
        if not metadata_df.empty:
            exp_df = metadata_df[metadata_df["type"] == "Custom Expectation"]
            if not exp_df.empty:
                f.write(f"\n## Custom Expectation Results\n\n")
                f.write(f"| Eval | Expectation | Outcome | Score |\n")
                f.write(f"|------|-------------|---------|-------|\n")
                for _, row in exp_df.iterrows():
                    exp_text = str(row.get("expected", ""))[:60]
                    f.write(f"| {row['display_name']} | {exp_text}... | {row.get('outcome', '-')} | {row.get('score', '-')} |\n")

        # Latency
        if not latency_summary.empty:
            f.write(f"\n## Latency Summary\n\n")
            f.write(f"| Eval | Avg Turn | p50/p90/p99 Turn |\n")
            f.write(f"|------|----------|------------------|\n")
            for _, row in latency_summary.iterrows():
                f.write(f"| {row['display_name']} | {row.get('Average (Turn)', '-')} | {row.get('p50 | p90 | p99 (Turn)', '-')} |\n")

        f.write(f"\n---\n_Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} via scrapi-eval-runner_\n")

    print(f"\nReport written to: {report_path}")


def cmd_push_goldens(args):
    """Push golden evals from YAML files to platform."""
    utils = get_eval_utils()
    app_name = get_app_name()

    source = args.source or GOLDEN_EVALS_DIR

    # Collect YAML files
    if os.path.isfile(source):
        yaml_files = [source]
    elif os.path.isdir(source):
        yaml_files = sorted(
            os.path.join(source, f)
            for f in os.listdir(source)
            if f.endswith((".yaml", ".yml"))
        )
    else:
        print(f"Not found: {source}")
        return

    print(f"Pushing golden evals from {len(yaml_files)} file(s)...")

    total_created = 0
    total_updated = 0

    for yf in yaml_files:
        print(f"\n--- {os.path.basename(yf)} ---")
        try:
            evals = utils.load_golden_evals_from_yaml(yf)
        except Exception as e:
            print(f"  Failed to parse: {e}")
            continue

        for eval_dict in evals:
            name = eval_dict.get("displayName", "?")
            try:
                result = utils.update_evaluation(eval_dict, app_name=app_name)
                new_id = result.name.split("/")[-1]
                # Check if it was created or updated based on output
                print(f"  Synced: {name} -> {new_id}")
                total_created += 1
            except Exception as e:
                print(f"  FAILED: {name}: {e}")

    print(f"\nDone. Synced {total_created} golden evals.")


def cmd_run_goldens(args):
    """Run all golden evals on the platform."""
    client = get_evals_client()
    app_name = get_app_name()

    from config import load_config as _load_shared_config
    raw = _load_shared_config()
    modality = raw.get("modality", "text")

    if args.channel and args.channel != modality:
        print(f"ERROR: Cannot run evals in '{args.channel}' mode. gecx-config.json specifies modality '{modality}'.")
        print("To fix: Remove the --channel flag or ensure it matches the app's configured modality.")
        sys.exit(1)

    channel = args.channel or raw.get("default_channel", modality)
    runs = args.runs or 1

    try:
        run_response = client.run_evaluation(
            eval_type="goldens",
            app_name=app_name,
            modality=channel,
            run_count=runs,
        )
        print(f"Golden eval run triggered ({channel}, {runs} runs).")
        if hasattr(run_response, 'operation') and hasattr(run_response.operation, 'name'):
            print(f"Operation: {run_response.operation.name}")
        else:
            print(f"Response: {run_response}")
    except Exception as e:
        print(f"Failed: {e}")


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Eval runner using cxas-scrapi")
    sub = parser.add_subparsers(dest="command")

    # status
    p_status = sub.add_parser("status", help="Show sync status")
    p_status.add_argument("--priority", default=None)
    p_status.add_argument("--tag", default=None, help="Filter by tag (e.g. outage, escalation)")

    # push
    p_push = sub.add_parser("push", help="Push evals to platform")
    p_push.add_argument("--priority", default=None)
    p_push.add_argument("--tag", default=None, help="Filter by tag")

    # run
    p_run = sub.add_parser("run", help="Trigger eval run")
    p_run.add_argument("--priority", default=None)
    p_run.add_argument("--tag", default=None, help="Filter by tag")
    p_run.add_argument("--channel", default="text", choices=["text", "audio"])
    p_run.add_argument("--runs", type=int, default=5)

    # push-goldens
    p_push_g = sub.add_parser("push-goldens", help="Push golden evals from YAML")
    p_push_g.add_argument("source", nargs="?", default=None,
                           help="YAML file or directory (default: golden-evals/)")

    # run-goldens
    p_run_g = sub.add_parser("run-goldens", help="Run all golden evals")
    p_run_g.add_argument("--channel", default="text", choices=["text", "audio"])
    p_run_g.add_argument("--runs", type=int, default=1)

    # results
    p_results = sub.add_parser("results", help="Fetch and score results")
    p_results.add_argument("run_id", help="Run ID or full resource name")
    p_results.add_argument("--audio", action="store_true",
                           help="Use audio scoring (goal+expectations, skip taskCompleted)")

    # report
    p_report = sub.add_parser("report", help="Generate markdown report")
    p_report.add_argument("run_id", help="Run ID or full resource name")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "push":
        cmd_push(args)
    elif args.command == "push-goldens":
        cmd_push_goldens(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "run-goldens":
        cmd_run_goldens(args)
    elif args.command == "results":
        cmd_results(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
