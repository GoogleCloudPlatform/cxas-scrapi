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
import re
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
HALLUCINATION = "HALLUCINATION"
TOOL_MISSING = "TOOL_MISSING"
TEXT_MISMATCH = "TEXT_MISMATCH"
EXPECTATION_FAIL = "EXPECTATION_FAIL"
EVAL_ERROR = "EVAL_ERROR"
UNKNOWN = "UNKNOWN"

# Foundation eval categories — deterministic tests on tools / callbacks.
TOOL_TEST_FAIL = "TOOL_TEST_FAIL"
CALLBACK_TEST_FAIL = "CALLBACK_TEST_FAIL"

# Sim-specific categories — failure modes that don't exist for goldens.
SIM_MAX_TURNS_EXCEEDED = "SIM_MAX_TURNS_EXCEEDED"  # conversation cut off mid-task
SIM_USER_OFF_SCRIPT = "SIM_USER_OFF_SCRIPT"        # sim user gave up / refused / diverged
SIM_TASK_INCOMPLETE = "SIM_TASK_INCOMPLETE"        # catch-all: passed=False but no other category fits


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

def categorize_failure(result_dict: dict) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Categorize a single failing result.

    Returns ``(category, detail_string, hint)``. ``hint`` is an optional dict
    carrying extra clustering context that doesn't fit cleanly into the detail
    string — currently ``{"responsible_agent": <name>}`` for HALLUCINATION;
    None for every other category.
    """
    golden = result_dict.get("golden_result", {}) or {}

    # Check for errors (timeout, invalid args, runtime errors)
    error_info = result_dict.get("error_info", {}) or {}
    error_msg = error_info.get("error_message", "") or ""
    error_code = error_info.get("error_code", "") or ""
    if error_msg or error_code:
        msg_lower = error_msg.lower()
        if "timed out" in msg_lower or "timeout" in msg_lower or "no user input" in msg_lower:
            return (TIMEOUT, error_msg[:80], None)
        # Any other error (INVALID_ARGUMENT, runtime errors, empty inputs) -- bad golden config or platform error
        return (EVAL_ERROR, f"{error_code}: {error_msg[:120]}", None)

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
        return (EXPECTATION_FAIL, "; ".join(parts), None)

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
            return (EXTRA_TURNS, detail, None)

        # Check hallucination results before assuming platform bug
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            hall_res = turn.get("hallucination_result", turn.get("hallucinationResult", {})) or {}
            hall_score = hall_res.get("score")
            if hall_score == 0:  # 0 = Not Justified (hallucination detected)
                explanation = hall_res.get("explanation", "")[:120]
                hint = _extract_hallucination_hint(turn)
                return (HALLUCINATION, f"Hallucination detected: {explanation}", hint)

        # SCORES_PASS_BUT_FAIL: genuinely all scores pass, no hallucination, no extra turns — platform scorer bug
        tool_str = f"{pass_tool_exp}/{total_tool_exp}" if total_tool_exp else "0/0"
        detail = f'tools={tool_str}, sem={overall_sem_score} -- all scores pass but platform marked FAIL'
        return (SCORES_PASS_BUT_FAIL, detail, None)

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
        return (TOOL_MISSING, detail, None)

    # TEXT_MISMATCH: semantic similarity failed
    if has_sem_fail:
        return (TEXT_MISMATCH, sem_detail, None)

    return (UNKNOWN, f"eval_status=FAIL, sem_pass={all_sem_pass}, tool_pass={all_tool_pass}", None)


# --- Per-type categorize functions (sim, tool_test, callback_test) ---
#
# Each returns the same `(category, detail, hint)` shape as `categorize_failure`
# so the existing `cluster_failures()` machinery handles them uniformly.

# Heuristic: if the sim ended with In-Progress steps and used at least this many
# turns, classify as SIM_MAX_TURNS_EXCEEDED rather than SIM_TASK_INCOMPLETE.
# Sim YAMLs we've seen typically configure max_turns 8-16.
_SIM_MAX_TURNS_HEURISTIC = 8


def categorize_sim_failure(sim_result: dict) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Categorize one sim_results.json entry.

    Result shape (from scrapi-sim-runner.py:613-642):
        {
            "name": str, "passed": bool,
            "goals": "N/M", "expectations": "N/M", "turns": int,
            "step_details": [{"goal": str, "status": "Not Started"|"In Progress"|"Completed",
                              "justification": str}, ...],
            "expectation_details": [{"expectation": str, "status": "Met"|"Not Met",
                                     "justification": str}, ...],
            "error": str (only on exception),
        }
    """
    # 1. Sim runner crashed -> EVAL_ERROR.
    err = sim_result.get("error")
    if err:
        return (EVAL_ERROR, f"sim runner error: {str(err)[:120]}", None)

    # 2. Failed expectations present -> EXPECTATION_FAIL (same detail format as
    #    goldens so the EXPECTATION_FAIL discriminator regex re-uses cleanly).
    exp_details = sim_result.get("expectation_details", []) or []
    failed_exps = [
        e for e in exp_details
        if isinstance(e, dict) and str(e.get("status", "")).lower() != "met"
    ]
    if failed_exps:
        parts = []
        for e in failed_exps:
            prompt = str(e.get("expectation", "?"))[:60]
            reason = str(e.get("justification", "")).strip().split(".")[0][:80] or "no justification"
            parts.append(f'"{prompt}" — {reason}')
        return (EXPECTATION_FAIL, "; ".join(parts), None)

    # 3. Step progress analysis. Detection priority: max-turns > off-script > incomplete.
    step_details = sim_result.get("step_details", []) or []
    in_progress = [s for s in step_details
                   if isinstance(s, dict) and str(s.get("status", "")) == "In Progress"]
    not_started = [s for s in step_details
                   if isinstance(s, dict) and str(s.get("status", "")) == "Not Started"]
    turns = sim_result.get("turns", 0) or 0

    if in_progress and turns >= _SIM_MAX_TURNS_HEURISTIC:
        # Conversation was actively working a step when it ran out of room.
        first = in_progress[0]
        goal = str(first.get("goal", "?"))[:60]
        return (
            SIM_MAX_TURNS_EXCEEDED,
            f'turns={turns}, in-progress: "{goal}" — bump max_turns or tighten sim user',
            {"step_goal": goal, "turns": turns},
        )

    if not_started and not in_progress:
        # Sim user never engaged with later steps — common when they refuse / give up early.
        first = not_started[0]
        goal = str(first.get("goal", "?"))[:60]
        just = str(first.get("justification", "")).strip().split(".")[0][:80]
        return (
            SIM_USER_OFF_SCRIPT,
            f'first un-attempted step: "{goal}" — {just or "sim user did not advance"}',
            {"step_goal": goal},
        )

    # 4. Fallback: passed=False but no specific signal -> generic incomplete.
    goals = sim_result.get("goals", "?")
    return (
        SIM_TASK_INCOMPLETE,
        f"goals={goals}, turns={turns}; check transcript for cause",
        None,
    )


def categorize_tool_test_failure(row: dict) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Categorize one tool_test_results row (DataFrame.to_dict()).

    Row shape (from tool_evals.py:617-721):
        {"test": str, "tool": str, "status": "PASSED"|"FAILED"|"ERROR",
         "errors": list[str], "response": dict|None, "latency (ms)": float, ...}
    """
    tool = str(row.get("tool", "?"))
    test = str(row.get("test", "?"))
    errors = row.get("errors") or []
    err_str = " | ".join(str(e) for e in errors)[:80] if errors else "no error message"
    detail = f"{tool}: {err_str}"
    return (TOOL_TEST_FAIL, detail, {"tool": tool, "test": test})


def categorize_callback_test_failure(row: dict) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Categorize one callback_test_results row.

    Row shape (from callback_evals.py:215-223):
        {"agent_name": str, "callback_type": str, "test_name": str,
         "status": "PASSED"|"FAILED"|"ERROR", "error_message": str|None}
    """
    agent = str(row.get("agent_name", "?"))
    cb_type = str(row.get("callback_type", "?"))
    test = str(row.get("test_name", "?"))
    err = str(row.get("error_message") or "no error message")[:80]
    detail = f"{agent}/{cb_type}/{test}: {err}"
    return (
        CALLBACK_TEST_FAIL,
        detail,
        {"agent_name": agent, "callback_type": cb_type, "test_name": test},
    )


# --- Failure clustering ---
#
# Groups failures by `(category, discriminator)` extracted from the existing
# detail string (or hint dict) so the triage-failure subagent can be dispatched
# once per failure pattern instead of once per failing eval. Categories without
# a usable discriminator (TEXT_MISMATCH, TIMEOUT, EXTRA_TURNS, ...) produce one
# singleton cluster per failure — same as today's per-eval dispatch.

# TOOL_MISSING detail format (from categorize_failure): "expected <tool>, got <other>"
# or "expected <tool>, not found[. Called: [...]]" — comma always follows the tool.
_TOOL_MISSING_RE = re.compile(r"^expected\s+(\S+),")
# EXPECTATION_FAIL detail format: '"<prompt[:60]>" — <reason[:80]>[; ...]'
_EXPECTATION_FAIL_RE = re.compile(r'^"([^"]{1,60})"')
# EVAL_ERROR detail format: "<ERROR_CODE>: <message[:120]>"
_EVAL_ERROR_RE = re.compile(r"^([A-Z_]+):")


def _extract_hallucination_hint(turn: dict) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the responsible agent for a hallucinating turn.

    The platform's per-turn schema doesn't expose a guaranteed agent field, so
    we try several likely paths and degrade to None if none match (in which
    case the failure clusters as a singleton).
    """
    for key in ("responsible_agent", "agent_name", "agent"):
        val = turn.get(key)
        if isinstance(val, str) and val:
            return {"responsible_agent": val.split("/")[-1]}
        if isinstance(val, dict):
            name = val.get("display_name") or val.get("name")
            if isinstance(name, str) and name:
                return {"responsible_agent": name.split("/")[-1]}

    outcomes = turn.get("expectation_outcome", []) or []
    for outcome_obj in outcomes:
        if not isinstance(outcome_obj, dict):
            continue
        resp = outcome_obj.get("observed_agent_response", {}) or {}
        role = resp.get("role")
        if isinstance(role, str) and role and role.lower() not in ("user", "system"):
            return {"responsible_agent": role}
    return None


def _extract_discriminator(
    category: str, detail: str, hint: Optional[Dict[str, Any]]
) -> Tuple[str, Optional[str]]:
    """Return ``(kind, value)`` for clustering, or ``("none", None)`` if not groupable.

    For deterministic foundation categories (TOOL_TEST_FAIL, CALLBACK_TEST_FAIL)
    the discriminator is the category itself so all failures collapse into one
    super-cluster — the triage subagent batch-diagnoses the whole pile in one
    invocation instead of burning a dispatch slot per test (these diagnoses are
    cheap and self-contained; per-failure subagent overhead would dominate).
    """
    if category == TOOL_MISSING:
        m = _TOOL_MISSING_RE.match(detail)
        if m:
            tool = m.group(1)
            # Strip a leading "tools/" prefix if present so equivalent references cluster.
            if tool.startswith("tools/"):
                tool = tool[len("tools/"):]
            return ("tool", tool)
        print(f"# WARN: TOOL_MISSING detail did not match regex: {detail!r}", file=sys.stderr)
        return ("none", None)

    if category == HALLUCINATION:
        if hint and hint.get("responsible_agent"):
            return ("agent", hint["responsible_agent"])
        return ("none", None)

    if category == EXPECTATION_FAIL:
        m = _EXPECTATION_FAIL_RE.match(detail)
        if m:
            key = m.group(1).lower().strip(" .,!?;:'\"")
            if key:
                return ("prompt_prefix", key)
        return ("none", None)

    if category == EVAL_ERROR:
        m = _EVAL_ERROR_RE.match(detail)
        if m:
            return ("error_class", m.group(1))
        return ("error_class", detail[:30].lower().strip())

    # Foundation tests: collapse everything into one super-cluster per category.
    if category == TOOL_TEST_FAIL:
        return ("category", TOOL_TEST_FAIL)
    if category == CALLBACK_TEST_FAIL:
        return ("category", CALLBACK_TEST_FAIL)

    # Sim categories: each clusters by its own dimension.
    if category == SIM_MAX_TURNS_EXCEEDED:
        # All "ran out of turns" failures share roughly the same fix mechanic
        # (bump max_turns / tighten sim user). Cluster them together.
        return ("category", SIM_MAX_TURNS_EXCEEDED)
    if category == SIM_USER_OFF_SCRIPT:
        # Group by which step the sim user stalled on — failures stalling on
        # the same step usually share root cause (response_guide gap, persona).
        if hint and hint.get("step_goal"):
            return ("step_goal", hint["step_goal"])
        return ("none", None)
    if category == SIM_TASK_INCOMPLETE:
        # No reliable discriminator — fallback singletons.
        return ("none", None)

    return ("none", None)


def cluster_failures(
    raw_failures: List[Tuple[str, str, str, Optional[Dict[str, Any]]]],
    pass_rates: Optional[Dict[str, Tuple[int, int]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group failures by ``(category, discriminator)``.

    Args:
        raw_failures: list of ``(category, eval_name, detail, hint)`` tuples.
            One entry per failing run — the same eval failing across multiple
            runs of the same iteration appears multiple times. This function
            dedupes on ``(eval_name, category, discriminator)`` so flaky evals
            don't inflate cluster size.
        pass_rates: optional ``{eval_name: (pass_count, total_runs)}``. When
            provided, each cluster carries an ``eval_pass_rates`` field
            ``{eval_name: "N/M"}`` so the triage subagent can route to the
            ``flaky`` fix-type for evals with intermediate pass rates.

    Returns:
        ``{category: [cluster_dict, ...]}`` where each cluster dict has
        ``discriminator``, ``discriminator_kind``, ``eval_names`` (sorted
        alphabetically, deduped), ``details`` (one per eval_name — first
        observation wins), and optionally ``eval_pass_rates``.

        Categories without a usable discriminator (TEXT_MISMATCH, TIMEOUT,
        EXTRA_TURNS, ...) produce one singleton cluster per *unique* eval.
    """
    # First pass: group by (category, kind, value) AND dedupe by eval_name
    # within each group (so 3 runs of golden_a failing the same way → 1 entry).
    grouped: Dict[Tuple[str, str, Optional[str]], "OrderedDict[str, str]"] = defaultdict(
        _ordered_dict
    )
    singletons: Dict[Tuple[str, str], str] = {}  # (category, eval_name) -> detail

    for category, eval_name, detail, hint in raw_failures:
        kind, value = _extract_discriminator(category, detail, hint)
        if kind == "none":
            singletons.setdefault((category, eval_name), detail)
        else:
            members = grouped[(category, kind, value)]
            if eval_name not in members:
                members[eval_name] = detail

    clusters_by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for (category, kind, value), members in grouped.items():
        names_sorted = sorted(members.keys())
        cluster: Dict[str, Any] = {
            "discriminator": value,
            "discriminator_kind": kind,
            "eval_names": names_sorted,
            "details": [members[n] for n in names_sorted],
        }
        _attach_pass_rates(cluster, names_sorted, pass_rates)
        clusters_by_cat[category].append(cluster)

    for (category, eval_name), detail in singletons.items():
        cluster = {
            "discriminator": None,
            "discriminator_kind": "none",
            "eval_names": [eval_name],
            "details": [detail],
        }
        _attach_pass_rates(cluster, [eval_name], pass_rates)
        clusters_by_cat[category].append(cluster)

    # Deterministic ordering: largest cluster first, then by discriminator string.
    for category in clusters_by_cat:
        clusters_by_cat[category].sort(
            key=lambda c: (-len(c["eval_names"]), c["discriminator"] or "")
        )

    return dict(clusters_by_cat)


def _ordered_dict():
    """Factory for a dict that preserves insertion order — used by defaultdict."""
    from collections import OrderedDict
    return OrderedDict()


def _attach_pass_rates(
    cluster: Dict[str, Any],
    eval_names: List[str],
    pass_rates: Optional[Dict[str, Tuple[int, int]]],
) -> None:
    """Add ``eval_pass_rates: {eval_name: "N/M"}`` to ``cluster`` when available."""
    if not pass_rates:
        return
    rates: Dict[str, str] = {}
    for name in eval_names:
        rate = pass_rates.get(name)
        if rate is not None:
            p, t = rate
            rates[name] = f"{p}/{t}"
    if rates:
        cluster["eval_pass_rates"] = rates


def triage_results(results: list, eval_name_lookup: Dict[str, str]) -> Dict[str, Any]:
    """Triage a list of results into categories.

    Returns:
        {
            "total": int,
            "passed": int,
            "failures": {category: [(eval_name, detail)]},
            "per_eval": {eval_name: {"pass": int, "total": int, "failures": [(category, detail)]}},
            "failure_clusters": {category: [cluster_dict, ...]},
        }
    """
    total = 0
    passed = 0
    failures = defaultdict(list)  # category -> [(eval_name, detail)]
    per_eval = defaultdict(lambda: {"pass": 0, "total": 0, "failures": []})
    raw_failures: List[Tuple[str, str, str, Optional[Dict[str, Any]]]] = []  # for clustering

    for r in results:
        rd = type(r).to_dict(r) if not isinstance(r, dict) else r

        # Resolve eval display name
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])
        display_name = eval_name_lookup.get(eval_resource, eval_resource.split("/")[-1])

        # Detect errored executions — these still count as runs (and as failures).
        # The platform marks exec_state=ERROR for "agent produced unexpected response,
        # eval aborted before completing" — that's a real failure of the agent, not
        # an infrastructure issue. Silently skipping these (the prior behavior)
        # caused the JSON summary to report 0/0 PASS instead of 0/N PASS for
        # whole batches of failed goldens, inflating apparent pass rates.
        exec_state = rd.get("execution_state", 0)
        is_errored = (
            (isinstance(exec_state, int) and exec_state == 3)
            or (isinstance(exec_state, str) and exec_state.upper() in ("ERROR", "ERRORED"))
        )

        total += 1
        per_eval[display_name]["total"] += 1

        # Check pass/fail. Errored executions are always failures (regardless of
        # whatever evaluation_status the platform set), and route through
        # categorize_failure which already handles error_info.error_message.
        status = rd.get("evaluation_status", 0)
        status_s = _status_str(status)

        if status_s == "PASS" and not is_errored:
            passed += 1
            per_eval[display_name]["pass"] += 1
        else:
            category, detail, hint = categorize_failure(rd)
            # If categorize_failure returned UNKNOWN for an errored execution
            # (no error_info populated), promote it to EVAL_ERROR so the user
            # has a meaningful category to act on.
            if is_errored and category == UNKNOWN:
                category = EVAL_ERROR
                detail = f"execution_state=ERROR (no error_info available); check transcript for run {rd.get('name', '?').split('/')[-1]}"
                hint = None
            failures[category].append((display_name, detail))
            per_eval[display_name]["failures"].append((category, detail))
            raw_failures.append((category, display_name, detail, hint))

    pass_rates = {name: (info["pass"], info["total"]) for name, info in per_eval.items()}
    return {
        "total": total,
        "passed": passed,
        "failures": dict(failures),
        "per_eval": dict(per_eval),
        "failure_clusters": cluster_failures(raw_failures, pass_rates=pass_rates),
    }


def _triage_typed(
    rows: list,
    name_key: str,
    categorize_fn,
    is_pass_fn,
) -> Dict[str, Any]:
    """Shared per-type triage builder for sim / tool_test / callback_test results.

    Same return shape as :func:`triage_results` so the merge in
    ``_build_run_summary`` can iterate uniformly across all 4 eval types.
    """
    total = 0
    passed = 0
    failures: "defaultdict[str, list]" = defaultdict(list)
    per_eval: "defaultdict[str, Dict[str, Any]]" = defaultdict(
        lambda: {"pass": 0, "total": 0, "failures": []}
    )
    raw_failures: List[Tuple[str, str, str, Optional[Dict[str, Any]]]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        eval_name = str(row.get(name_key, "<unnamed>"))
        total += 1
        per_eval[eval_name]["total"] += 1

        if is_pass_fn(row):
            passed += 1
            per_eval[eval_name]["pass"] += 1
        else:
            category, detail, hint = categorize_fn(row)
            failures[category].append((eval_name, detail))
            per_eval[eval_name]["failures"].append((category, detail))
            raw_failures.append((category, eval_name, detail, hint))

    pass_rates = {name: (info["pass"], info["total"]) for name, info in per_eval.items()}
    return {
        "total": total,
        "passed": passed,
        "failures": dict(failures),
        "per_eval": dict(per_eval),
        "failure_clusters": cluster_failures(raw_failures, pass_rates=pass_rates),
    }


def triage_sim_results(sim_results: list) -> Dict[str, Any]:
    """Triage sim runner output into the standard triage shape.

    ``sim_results`` is the ``results`` array from ``sim_results_*.json``
    (each entry is one run of one sim).
    """
    return _triage_typed(
        sim_results,
        name_key="name",
        categorize_fn=categorize_sim_failure,
        is_pass_fn=lambda r: bool(r.get("passed")),
    )


def triage_tool_test_results(rows: list) -> Dict[str, Any]:
    """Triage tool test rows (from ``tool_test_results.json``)."""
    return _triage_typed(
        rows,
        name_key="test",
        categorize_fn=categorize_tool_test_failure,
        is_pass_fn=lambda r: str(r.get("status", "")).upper() == "PASSED",
    )


def triage_callback_test_results(rows: list) -> Dict[str, Any]:
    """Triage callback test rows (from ``callback_test_results.json``)."""
    return _triage_typed(
        rows,
        name_key="test_name",
        categorize_fn=categorize_callback_test_failure,
        is_pass_fn=lambda r: str(r.get("status", "")).upper() == "PASSED",
    )


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
    for cat in [TIMEOUT, EVAL_ERROR, SCORES_PASS_BUT_FAIL, EXTRA_TURNS, HALLUCINATION, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
        n = counts.get(cat, 0)
        if n:
            parts.append(f"{n} {cat}")
    print(f"SUMMARY: {' | '.join(parts)}")

    # Adjusted score (exclude platform issues: timeouts + scorer bugs)
    timeout_n = counts.get(TIMEOUT, 0)
    scorer_n = counts.get(SCORES_PASS_BUT_FAIL, 0)
    error_n = counts.get(EVAL_ERROR, 0)
    adjusted_total = total - timeout_n - scorer_n - error_n
    adjusted_pass = passed
    if adjusted_total > 0:
        adj_pct = 100 * adjusted_pass / adjusted_total
        print(f"Adjusted (excl platform/config issues): {adjusted_pass}/{adjusted_total} ({adj_pct:.1f}%)")

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
        for cat in [TIMEOUT, EVAL_ERROR, SCORES_PASS_BUT_FAIL, HALLUCINATION, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
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
    for cat in [TIMEOUT, EVAL_ERROR, SCORES_PASS_BUT_FAIL, EXTRA_TURNS, HALLUCINATION, EXPECTATION_FAIL, TOOL_MISSING, TEXT_MISMATCH, UNKNOWN]:
        c = all_category_counts.get(cat, 0)
        if c:
            parts.append(f"{c} {cat}")
    print(f"AGGREGATE: {' | '.join(parts)}")

    # Adjusted (exclude platform issues: timeouts + scorer bugs)
    timeout_n = all_category_counts.get(TIMEOUT, 0)
    scorer_n = all_category_counts.get(SCORES_PASS_BUT_FAIL, 0)
    error_n = all_category_counts.get(EVAL_ERROR, 0)
    adjusted_total = total_total - timeout_n - scorer_n - error_n
    adjusted_pass = total_passed
    if adjusted_total > 0:
        adj_pct = 100 * adjusted_pass / adjusted_total
        print(f"Adjusted (excl platform/config issues): {adjusted_pass}/{adjusted_total} ({adj_pct:.1f}%)")

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
