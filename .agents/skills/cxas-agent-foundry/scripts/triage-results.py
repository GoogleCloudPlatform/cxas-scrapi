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

"""Triage golden eval results into failure categories for fast debugging.

Usage:
  python scripts/triage-results.py                                    # Latest run, all goldens
  python scripts/triage-results.py --eval golden_profanity_escalation # Single eval
  python scripts/triage-results.py --run-id abc12345                  # Specific run
  python scripts/triage-results.py --last 3                           # Average across last 3 runs
"""

import argparse
import json
import os
import sys
import yaml
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import load_app_name


# --- Failure categories ---

TIMEOUT = "TIMEOUT"
SCORES_PASS_BUT_FAIL = "SCORES_PASS_BUT_FAIL"
EXTRA_TURNS = "EXTRA_TURNS"
TOOL_MISSING = "TOOL_MISSING"
TEXT_MISMATCH = "TEXT_MISMATCH"
EXPECTATION_FAIL = "EXPECTATION_FAIL"
UNKNOWN = "UNKNOWN"


# --- Result parsing ---

def _status_str(val) -> str:
    if isinstance(val, int):
        return {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}.get(val, f"UNKNOWN_{val}")
    return str(val).upper() if val else "UNSPECIFIED"


def _outcome_int(val) -> int:
    """Normalize outcome to int (0=unspecified, 1=pass, 2=fail)."""
    if isinstance(val, int):
        return val
    s = str(val).upper() if val else ""
    if s == "PASS":
        return 1
    if s == "FAIL":
        return 2
    return 0


def get_golden_evals(client: "Evaluations") -> Dict[str, str]:
    """Return {display_name: resource_name} for all golden evals."""
    try:
        evals_map = client.get_evaluations_map(reverse=True)
    except Exception as e:
        print(f"Error: Failed to fetch evaluations map: {e}")
        return {}
    return evals_map.get("goldens", {})


def get_results_for_eval(client: "Evaluations", eval_display_name: str) -> list:
    """Fetch all results for a golden eval by display name."""
    try:
        return client.list_evaluation_results(eval_display_name)
    except Exception as e:
        print(f"  Warning: Failed to fetch results for '{eval_display_name}': {e}")
        return []


def group_results_by_run(results: list) -> Dict[str, list]:
    """Group results by evaluation_run, returning {run_id: [results]}."""
    groups = defaultdict(list)
    for r in results:
        rd = type(r).to_dict(r) if not isinstance(r, dict) else r
        run_id = rd.get("evaluation_run", "unknown")
        groups[run_id].append(r)
    return dict(groups)


def get_latest_run_results(results: list) -> Tuple[str, str, list]:
    """From all results, return (run_id_short, create_time_str, results) for the most recent run."""
    if not results:
        return ("", "", [])

    groups = group_results_by_run(results)

    # Find the run with the max create_time among its results
    best_run = None
    best_time = None
    for run_id, run_results in groups.items():
        for r in run_results:
            rd = type(r).to_dict(r) if not isinstance(r, dict) else r
            ct = rd.get("create_time", "")
            if ct and (best_time is None or str(ct) > str(best_time)):
                best_time = ct
                best_run = run_id

    if best_run is None:
        best_run = list(groups.keys())[0]

    run_short = best_run.split("/")[-1][:8] if best_run else "unknown"
    time_str = str(best_time)[:19].replace("T", " ") if best_time else "?"
    return (run_short, time_str, groups[best_run])


def get_run_results(client: "Evaluations", run_id: str, app_name: str) -> Tuple[str, str, list]:
    """Fetch results for a specific run ID."""
    full_run_id = run_id if run_id.startswith("projects/") else f"{app_name}/evaluationRuns/{run_id}"
    results = client.list_evaluation_results_by_run(full_run_id)
    run_short = run_id.split("/")[-1][:8] if "/" in run_id else run_id[:8]

    best_time = None
    for r in results:
        rd = type(r).to_dict(r) if not isinstance(r, dict) else r
        ct = rd.get("create_time", "")
        if ct and (best_time is None or str(ct) > str(best_time)):
            best_time = ct
    time_str = str(best_time)[:19].replace("T", " ") if best_time else "?"
    return (run_short, time_str, results)


def get_last_n_run_results(results: list, n: int) -> List[Tuple[str, str, list]]:
    """Return the last N runs as a list of (run_short, time_str, results)."""
    groups = group_results_by_run(results)

    # Sort runs by max create_time descending
    def run_max_time(run_id):
        max_t = ""
        for r in groups[run_id]:
            rd = type(r).to_dict(r) if not isinstance(r, dict) else r
            ct = str(rd.get("create_time", ""))
            if ct > max_t:
                max_t = ct
        return max_t

    sorted_runs = sorted(groups.keys(), key=run_max_time, reverse=True)[:n]

    run_tuples = []
    for run_id in sorted_runs:
        max_t = run_max_time(run_id)
        run_short = run_id.split("/")[-1][:8] if run_id else "unknown"
        time_str = max_t[:19].replace("T", " ") if max_t else "?"
        run_tuples.append((run_short, time_str, groups[run_id]))

    return run_tuples


# --- Categorization ---

def categorize_failure(result_dict: dict) -> Tuple[str, str]:
    """Categorize a single failing result. Returns (category, detail_string)."""
    golden = result_dict.get("golden_result", {}) or {}

    # Check for timeout via error_info
    error_info = result_dict.get("error_info", {}) or {}
    error_msg = error_info.get("error_message", "") or ""
    if "no user input" in error_msg.lower() or "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
        return (TIMEOUT, error_msg[:80])

    # Check custom expectation results (LLM-judged expectations from the golden YAML)
    exp_results = golden.get("evaluation_expectation_results", []) or []
    failed_expectations = []
    for er in exp_results:
        if not isinstance(er, dict):
            continue
        if _outcome_int(er.get("outcome")) == 2:
            prompt = er.get("prompt", "?")
            explanation = er.get("explanation", "").strip()
            # Take the first sentence of the explanation as the reason
            reason = explanation.split(".")[0].strip() if explanation else "no reason given"
            failed_expectations.append((prompt, reason))
    if failed_expectations:
        parts = []
        for prompt, reason in failed_expectations:
            parts.append(f'"{prompt[:60]}" — {reason[:80]}')
        return (EXPECTATION_FAIL, "; ".join(parts))

    # Parse turn-level details
    turns = golden.get("turn_replay_results", []) or []

    has_sem_fail = False
    has_tool_fail = False
    all_sem_pass = True
    all_tool_pass = True
    tool_detail = ""
    sem_detail = ""

    for turn in turns:
        if not isinstance(turn, dict):
            continue

        # Semantic similarity
        sem_res = turn.get("semantic_similarity_result", {}) or {}
        sem_outcome = _outcome_int(sem_res.get("outcome"))
        if sem_outcome == 2:
            has_sem_fail = True
            all_sem_pass = False
            score = sem_res.get("score", "?")
            sem_detail = f"sem_score={score}"

        # Tool invocation (turn-level)
        tool_score = turn.get("tool_invocation_score")
        if not tool_score:
            overall_tool = turn.get("overall_tool_invocation_result", {}) or {}
            tool_score = overall_tool.get("tool_invocation_score")
        tool_outcome = _outcome_int(tool_score)
        if tool_outcome == 2:
            has_tool_fail = True
            all_tool_pass = False

        # Expectation outcomes (tool expectations, text expectations, etc.)
        outcomes = turn.get("expectation_outcome", []) or []
        for outcome_obj in outcomes:
            if not isinstance(outcome_obj, dict):
                continue
            exp_outcome = _outcome_int(outcome_obj.get("outcome"))

            # Check both "expectation" (actual key) and "expected_agent_action" (legacy)
            expected = outcome_obj.get("expectation", {}) or outcome_obj.get("expected_agent_action", {}) or {}

            if "tool_call" in expected:
                # Tool expectation
                tool_inv = outcome_obj.get("tool_invocation_result", {}) or {}
                tool_inv_outcome = _outcome_int(tool_inv.get("outcome"))
                if exp_outcome != 1 or tool_inv_outcome == 2:
                    has_tool_fail = True
                    all_tool_pass = False
                    expected_tool = expected["tool_call"].get(
                        "display_name",
                        expected["tool_call"].get("id", "?")
                    )
                    observed = outcome_obj.get("observed_tool_call", {}) or {}
                    actual_tool = observed.get("display_name", observed.get("id", ""))
                    if actual_tool:
                        tool_detail = f"expected {expected_tool}, got {actual_tool}"
                    else:
                        tool_detail = f"expected {expected_tool}, not found"
            elif "agent_response" in expected:
                # Text expectation
                if exp_outcome != 1:
                    score = sem_res.get("score", "?")
                    # If sem score is high (3-4) but outcome still fails, it's likely
                    # an extra-turn issue, not a real text mismatch
                    if isinstance(score, (int, float)) and score >= 3:
                        pass  # Text matches — failure is from extra turns, not text
                    else:
                        has_sem_fail = True
                        all_sem_pass = False
                        if not sem_detail:
                            sem_detail = f"sem_score={score}"

    # Build summary text/tool counts for SCORES_PASS_BUT_FAIL detection
    # Count total tool expectations and passes
    total_tool_exp = 0
    pass_tool_exp = 0
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        outcomes = turn.get("expectation_outcome", []) or []
        for outcome_obj in outcomes:
            if not isinstance(outcome_obj, dict):
                continue
            expected = outcome_obj.get("expectation", {}) or outcome_obj.get("expected_agent_action", {}) or {}
            if "tool_call" in expected:
                total_tool_exp += 1
                if _outcome_int(outcome_obj.get("outcome")) == 1:
                    pass_tool_exp += 1

    # Get overall semantic score
    overall_sem = golden.get("semantic_similarity_result", {}) or {}
    overall_sem_score = overall_sem.get("score", "?")

    # Get first agent response text for SCORES_PASS_BUT_FAIL detail
    first_text = ""
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        outcomes = turn.get("expectation_outcome", []) or []
        for outcome_obj in outcomes:
            if not isinstance(outcome_obj, dict):
                continue
            obs_resp = outcome_obj.get("observed_agent_response", {}) or {}
            chunks = obs_resp.get("chunks", []) or []
            if chunks:
                first_text = chunks[0].get("text", "")[:40]
                break
        if first_text:
            break

    # EXTRA_TURNS: all expected turns pass, but agent produced extra output
    # (transfers, sub-agent responses) that the golden doesn't cover
    if all_sem_pass and all_tool_pass and not has_sem_fail and not has_tool_fail:
        extra_turns = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            outcomes = turn.get("expectation_outcome", []) or []
            for outcome_obj in outcomes:
                if not isinstance(outcome_obj, dict):
                    continue
                exp = outcome_obj.get("expectation", {}) or {}
                # No expectation but agent produced something — extra turn
                if not exp:
                    transfer = outcome_obj.get("observed_agent_transfer", {})
                    resp = outcome_obj.get("observed_agent_response", {})
                    if transfer:
                        target = transfer.get("display_name", "?")
                        extra_turns.append(f"transfer→{target}")
                    elif resp:
                        role = resp.get("role", "?")
                        chunks = resp.get("chunks", [])
                        text = chunks[0].get("text", "")[:40] if chunks else ""
                        extra_turns.append(f'{role}: "{text}..."')

        if extra_turns:
            tool_str = f"{pass_tool_exp}/{total_tool_exp}" if total_tool_exp else "0/0"
            extras = ", ".join(extra_turns[:3])
            detail = f"all expected turns pass (tools={tool_str}), but agent produced extra: {extras}"
            return (EXTRA_TURNS, detail)

        # SCORES_PASS_BUT_FAIL: genuinely all scores pass, no extra turns — platform scorer bug
        tool_str = f"{pass_tool_exp}/{total_tool_exp}" if total_tool_exp else "0/0"
        detail = f'tools={tool_str}, sem={overall_sem_score} -- all scores pass but platform marked FAIL'
        return (SCORES_PASS_BUT_FAIL, detail)

    # TOOL_MISSING: a tool expectation failed
    if has_tool_fail:
        # Collect all actual tools called across turns
        called_tools = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            latencies = turn.get("tool_call_latencies", []) or []
            for tc in latencies:
                if isinstance(tc, dict):
                    tool_name = tc.get("tool", tc.get("display_name", ""))
                    if tool_name:
                        short = tool_name.split("/")[-1] if "/" in tool_name else tool_name
                        if short not in called_tools:
                            called_tools.append(short)
        detail = tool_detail
        if called_tools:
            detail += f". Called: [{', '.join(called_tools)}]"
        return (TOOL_MISSING, detail)

    # TEXT_MISMATCH: semantic similarity failed
    if has_sem_fail:
        return (TEXT_MISMATCH, sem_detail)

    return (UNKNOWN, f"eval_status=FAIL, sem_pass={all_sem_pass}, tool_pass={all_tool_pass}")


def triage_results(results: list, eval_name_lookup: Dict[str, str]) -> Dict[str, Any]:
    """Triage a list of results into categories.

    Returns:
        {
            "total": int,
            "passed": int,
            "failures": {category: [(eval_name, detail)]},
            "per_eval": {eval_name: {"pass": int, "total": int, "failures": [(category, detail)]}},
        }
    """
    total = 0
    passed = 0
    failures = defaultdict(list)  # category -> [(eval_name, detail)]
    per_eval = defaultdict(lambda: {"pass": 0, "total": 0, "failures": []})

    for r in results:
        rd = type(r).to_dict(r) if not isinstance(r, dict) else r

        # Skip errored executions
        exec_state = rd.get("execution_state", 0)
        if isinstance(exec_state, int) and exec_state == 3:
            continue
        if isinstance(exec_state, str) and exec_state.upper() in ("ERROR", "ERRORED"):
            continue

        # Resolve eval display name
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])
        display_name = eval_name_lookup.get(eval_resource, eval_resource.split("/")[-1])

        total += 1
        per_eval[display_name]["total"] += 1

        # Check pass/fail
        status = rd.get("evaluation_status", 0)
        status_s = _status_str(status)

        if status_s == "PASS":
            passed += 1
            per_eval[display_name]["pass"] += 1
        else:
            category, detail = categorize_failure(rd)
            failures[category].append((display_name, detail))
            per_eval[display_name]["failures"].append((category, detail))

    return {
        "total": total,
        "passed": passed,
        "failures": dict(failures),
        "per_eval": dict(per_eval),
    }


# --- Output ---

def print_triage(triage: Dict[str, Any], run_short: str, time_str: str):
    """Print triage summary in the standard format."""
    total = triage["total"]
    passed = triage["passed"]
    failures = triage["failures"]
    per_eval = triage["per_eval"]

    counts = {cat: len(items) for cat, items in failures.items()}

    print(f"\n=== Golden Triage (run {run_short}, {time_str}) ===\n")

    parts = [f"{passed}/{total} PASS"]
    for cat in [TIMEOUT, SCORES_PASS_BUT_FAIL, EXTRA_TURNS, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
        n = counts.get(cat, 0)
        if n:
            parts.append(f"{n} {cat}")
    print(f"SUMMARY: {' | '.join(parts)}")

    # Adjusted score (exclude platform issues: timeouts + scorer bugs)
    timeout_n = counts.get(TIMEOUT, 0)
    scorer_n = counts.get(SCORES_PASS_BUT_FAIL, 0)
    adjusted_total = total - timeout_n - scorer_n
    adjusted_pass = passed
    if adjusted_total > 0:
        adj_pct = 100 * adjusted_pass / adjusted_total
        print(f"Adjusted (excl platform issues): {adjusted_pass}/{adjusted_total} ({adj_pct:.1f}%)")

    # Per-eval breakdown
    print(f"\nPER-EVAL:")
    for name in sorted(per_eval.keys()):
        info = per_eval[name]
        p, t = info["pass"], info["total"]
        if p == t:
            print(f"  \u2713 {name}: {p}/{t}")
        else:
            print(f"  ~ {name}: {p}/{t}")
            for cat, detail in info["failures"]:
                print(f"      {cat}: {detail}")

    # Failures by category
    if failures:
        print(f"\nFAILURES BY CATEGORY:")
        for cat in [TIMEOUT, SCORES_PASS_BUT_FAIL, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
            if cat not in failures:
                continue
            items = failures[cat]
            # Count per eval
            eval_counts = defaultdict(int)
            for eval_name, _ in items:
                eval_counts[eval_name] += 1
            detail_parts = [f"{name} x{count}" if count > 1 else name for name, count in eval_counts.items()]
            print(f"  {cat} ({len(items)}): {', '.join(detail_parts)}")


def print_multi_run_triage(run_triages: List[Tuple[str, str, Dict[str, Any]]]):
    """Print aggregated triage across multiple runs."""
    n = len(run_triages)
    print(f"\n=== Golden Triage (last {n} runs) ===\n")

    total_total = 0
    total_passed = 0
    all_category_counts = defaultdict(int)
    eval_agg = defaultdict(lambda: {"pass": 0, "total": 0})

    for run_short, time_str, triage in run_triages:
        total_total += triage["total"]
        total_passed += triage["passed"]
        for cat, items in triage["failures"].items():
            all_category_counts[cat] += len(items)
        for name, info in triage["per_eval"].items():
            eval_agg[name]["pass"] += info["pass"]
            eval_agg[name]["total"] += info["total"]

    avg_pct = 100 * total_passed / total_total if total_total else 0

    parts = [f"{total_passed}/{total_total} PASS ({avg_pct:.1f}%)"]
    for cat in [TIMEOUT, SCORES_PASS_BUT_FAIL, EXTRA_TURNS, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
        c = all_category_counts.get(cat, 0)
        if c:
            parts.append(f"{c} {cat}")
    print(f"AGGREGATE: {' | '.join(parts)}")

    # Adjusted (exclude platform issues: timeouts + scorer bugs)
    timeout_n = all_category_counts.get(TIMEOUT, 0)
    scorer_n = all_category_counts.get(SCORES_PASS_BUT_FAIL, 0)
    adjusted_total = total_total - timeout_n - scorer_n
    adjusted_pass = total_passed
    if adjusted_total > 0:
        adj_pct = 100 * adjusted_pass / adjusted_total
        print(f"Adjusted (excl platform issues): {adjusted_pass}/{adjusted_total} ({adj_pct:.1f}%)")

    # Per-eval averages
    print(f"\nPER-EVAL (across {n} runs):")
    for name in sorted(eval_agg.keys()):
        info = eval_agg[name]
        p, t = info["pass"], info["total"]
        pct = 100 * p / t if t else 0
        marker = "\u2713" if p == t else "~"
        print(f"  {marker} {name}: {p}/{t} ({pct:.0f}%)")

    # Per-run summaries
    print(f"\nPER-RUN:")
    for run_short, time_str, triage in run_triages:
        pct = 100 * triage["passed"] / triage["total"] if triage["total"] else 0
        print(f"  {run_short} ({time_str}): {triage['passed']}/{triage['total']} ({pct:.1f}%)")


def main():
    try:
        import cxas_scrapi
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Triage golden eval results into failure categories"
    )
    parser.add_argument(
        "--eval", default=None,
        help="Triage a specific golden eval by display name"
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Triage a specific run ID instead of latest"
    )
    parser.add_argument(
        "--last", type=int, default=None,
        help="Aggregate triage across last N runs"
    )
    args = parser.parse_args()

    app_name = load_app_name()

    from cxas_scrapi.core.evaluations import Evaluations
    client = Evaluations(app_name=app_name)

    # Build eval name lookup (resource -> display_name)
    try:
        evals_map = client.get_evaluations_map(reverse=False)
    except Exception as e:
        print(f"Error: Failed to fetch evaluations map: {e}")
        sys.exit(1)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    # Determine which golden evals to triage
    golden_evals = get_golden_evals(client)  # display_name -> resource_name
    if args.eval:
        if args.eval not in golden_evals:
            print(f"Error: Golden eval '{args.eval}' not found. Available: {', '.join(sorted(golden_evals.keys()))}")
            sys.exit(1)
        golden_evals = {args.eval: golden_evals[args.eval]}

    print(f"Fetching results for {len(golden_evals)} golden eval(s)...")

    if args.run_id:
        # Fetch results for a specific run
        run_short, time_str, results = get_run_results(client, args.run_id, app_name)
        triage = triage_results(results, name_lookup)
        print_triage(triage, run_short, time_str)

    elif args.last:
        # Fetch all results, group by run, take last N
        all_results = []
        for display_name in golden_evals:
            try:
                all_results.extend(get_results_for_eval(client, display_name))
            except Exception as e:
                print(f"  Warning: Failed to fetch {display_name}: {e}")

        run_tuples = get_last_n_run_results(all_results, args.last)
        if not run_tuples:
            print("No runs found.")
            return

        triaged_runs = []
        for run_short, time_str, run_results in run_tuples:
            triage = triage_results(run_results, name_lookup)
            triaged_runs.append((run_short, time_str, triage))

        print_multi_run_triage(triaged_runs)

    else:
        # Default: fetch latest run for each golden, combine
        all_results = []
        run_short = ""
        time_str = ""

        for display_name in golden_evals:
            try:
                results = get_results_for_eval(client, display_name)
                rs, ts, latest = get_latest_run_results(results)
                all_results.extend(latest)
                # Track the most recent run overall
                if ts > time_str:
                    time_str = ts
                    run_short = rs
            except Exception as e:
                print(f"  Warning: Failed to fetch {display_name}: {e}")

        if not all_results:
            print("No results found.")
            return

        triage = triage_results(all_results, name_lookup)
        print_triage(triage, run_short, time_str)


if __name__ == "__main__":
    main()
