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

"""Utility functions for loading evaluation results."""

import json
from typing import Any
from cxas_scrapi.utils import eval_utils
import pandas as pd
import yaml


def _outcome_str(val):
    if isinstance(val, int):
        return {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}.get(val, f"?{val}")
    return str(val) if val else "?"


def load_golden_results(
    run_id: str,
    app_name: str,
    include: list[str] | None = None,
    user_agent_extension: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch golden results and parse into report-friendly format.

    Args:
      run_id: The evaluation run ID to load results for.
      app_name: Vertex AI Agent Engine app resource name.
      include: Categories of evaluations to include (e.g. 'goldens', 'scenarios').

    Returns:
      A list of formatted evaluation result dictionaries.
    """
    if include is None:
        include = ["goldens", "scenarios"]

    utils = eval_utils.EvalUtils(app_name=app_name)
    full_run_id = (
        run_id
        if run_id.startswith("projects/")
        else f"{app_name}/evaluationRuns/{run_id}"
    )

    raw_results = utils.list_evaluation_results_by_run(full_run_id)

    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    results = []
    for r in raw_results:
        rd = type(r).to_dict(r)
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])

        is_golden = eval_resource in evals_map.get("goldens", {})
        is_scenario = eval_resource in evals_map.get("scenarios", {})

        if is_golden and "goldens" not in include:
            continue
        if is_scenario and "scenarios" not in include:
            continue

        display_name = name_lookup.get(
            eval_resource, eval_resource.split("/")[-1]
        )

        status_raw = rd.get("evaluation_status", 0)
        passed = (
            (status_raw == 1)
            if isinstance(status_raw, int)
            else str(status_raw).upper() == "PASS"
        )

        golden = rd.get("golden_result", {})

        turns = []
        for i, turn in enumerate(golden.get("turn_replay_results", [])):
            sem = turn.get("semantic_similarity_result", {})
            turn_data = {
                "index": i + 1,
                "semantic_score": sem.get("score"),
                "semantic_explanation": sem.get("explanation"),
                "comparisons": [],
            }
            for o in turn.get("expectation_outcome", []):
                exp = o.get("expectation", {})
                outcome = _outcome_str(o.get("outcome"))
                comp = {"outcome": outcome}

                if "agent_response" in exp:
                    chunks = exp["agent_response"].get("chunks", [])
                    comp["type"] = "text"
                    comp["expected"] = (
                        chunks[0].get("text", "") if chunks else ""
                    )
                    obs = o.get("observed_agent_response", {})
                    comp["actual"] = (
                        obs.get("chunks", [{}])[0].get("text", "")
                        if obs
                        else "(missed)"
                    )
                elif "tool_call" in exp:
                    tc = exp["tool_call"]
                    comp["type"] = "tool_call"
                    comp["expected"] = (
                        tc.get("display_name")
                        or tc.get("tool", "").split("/")[-1]
                    )
                    comp["expected_args"] = tc.get("args", {})
                    obs = o.get("observed_tool_call", {})
                    comp["actual"] = (
                        (
                            obs.get("display_name")
                            or obs.get("tool", "").split("/")[-1]
                        )
                        if obs
                        else "(missed)"
                    )
                    comp["actual_args"] = obs.get("args", {}) if obs else {}
                    tir = o.get("toolInvocationResult", {})
                    comp["tool_invocation_score"] = tir.get(
                        "parameterCorrectnessScore"
                    )
                    comp["tool_invocation_explanation"] = tir.get("explanation")
                elif "tool_response" in exp:
                    continue
                elif "agent_transfer" in exp:
                    at = exp["agent_transfer"]
                    comp["type"] = "transfer"
                    comp["expected"] = at.get(
                        "display_name",
                        at.get("target_agent", "").split("/")[-1],
                    )
                    obs = o.get("observed_agent_transfer", {})
                    if obs:
                        comp["actual"] = obs.get(
                            "display_name",
                            obs.get("target_agent", "").split("/")[-1],
                        )
                    else:
                        comp["actual"] = "(missed)"
                else:
                    continue

                turn_data["comparisons"].append(comp)
            turns.append(turn_data)

        expectations = []
        for ee in golden.get("evaluation_expectation_results", []):
            result_val = ee.get("outcome", ee.get("result"))
            exp_text = ee.get("prompt", ee.get("evaluation_expectation", ""))
            explanation = ee.get("explanation", "")
            met = (
                result_val == 1
                if isinstance(result_val, int)
                else str(result_val).upper() == "PASS"
            )
            expectations.append(
                {
                    "expectation": exp_text,
                    "status": "Met" if met else "Not Met",
                    "justification": explanation,
                }
            )

        session_id = ""
        if golden.get("turn_replay_results"):
            conv_path = golden["turn_replay_results"][0].get("conversation", "")
            if conv_path:
                # Extract the conversation ID (e.g. "evaluation-xxxx")
                session_id = conv_path.split("/")[-1]

        session_params = {}
        # One entry per golden turn: ("text", "...") or ("event", "...")
        turn_inputs = []
        try:
            ev_obj = utils.get_evaluation(eval_resource)
            evd = type(ev_obj).to_dict(ev_obj)
            golden_def = evd.get("golden", {})
            for turn_def in golden_def.get("turns", []):
                turn_input = None
                for step in turn_def.get("steps", []):
                    ui = step.get("user_input", {})
                    if "variables" in ui:
                        session_params.update(ui["variables"])
                    if "text" in ui:
                        turn_input = ("text", ui["text"])
                    elif "event" in ui:
                        turn_input = ("event", str(ui["event"]))
                if turn_input:
                    turn_inputs.append(turn_input)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        for i, turn in enumerate(turns):
            if i < len(turn_inputs):
                kind, text = turn_inputs[i]
                turn["user_input"] = text if kind == "text" else None
            else:
                turn["user_input"] = None

        total_latency_s = 0
        for turn_result in golden.get("turn_replay_results", []):
            lat = turn_result.get("turn_latency", "")
            if isinstance(lat, str) and lat.endswith("s"):
                try:
                    total_latency_s += float(lat.replace("s", ""))
                except ValueError:
                    pass
            elif isinstance(lat, dict):
                total_latency_s += lat.get("seconds", 0) + (
                    lat.get("nanos", 0) / 1e9
                )

        results.append(
            {
                "name": display_name,
                "passed": passed,
                "turns": turns,
                "expectations": expectations,
                "session_id": session_id,
                "session_parameters": session_params,
                "duration_s": (
                    round(total_latency_s, 1) if total_latency_s > 0 else None
                ),
            }
        )

    return results


def _load_sim_test_cases(yaml_path: str) -> list[dict[str, Any]]:
    """Loads sim files and merges common params and expectations.

    Args:
      yaml_path: Path to the YAML test cases file.

    Returns:
      List of merged evaluation test case dicts.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, list):
        return data

    common_params = data.get("common_session_parameters", {}) or {}
    common_expectations = data.get("common_expectations", []) or []
    cases = data.get("evals", [])
    if not isinstance(cases, list):
        return []

    merged_cases = []
    for c in cases:
        if isinstance(c, dict):
            case_copy = c.copy()
            # Merge session parameters
            case_params = case_copy.get("session_parameters", {}) or {}
            merged = common_params.copy()
            merged.update(case_params)
            case_copy["session_parameters"] = merged

            # Merge expectations
            case_expectations = case_copy.get("expectations", []) or []
            case_copy["expectations"] = common_expectations + case_expectations

            merged_cases.append(case_copy)
    return merged_cases


def load_sim_results(
    json_path: str, sim_evals_yaml: str | None = None
) -> tuple[list[dict[str, Any]], float | None]:
    """Load sim results from JSON file.

    Handles both old (list) and new (envelope) formats.

    Args:
      json_path: The JSON file path containing evaluation results.
      sim_evals_yaml: Optional path to simulation evals YAML definition file.

    Returns:
      A tuple containing the list of simulation results and the wall clock time.
    """
    with open(json_path) as f:
        data = json.load(f)

    wall_clock_s = None
    # New envelope format: {"wall_clock_s": N, "results": [...]}
    # Old format: [...]
    if isinstance(data, dict):
        wall_clock_s = data.get("wall_clock_s")
        results = data.get("results", [])
    else:
        results = data

    # Backfill session_parameters if missing
    if sim_evals_yaml:
        try:
            eval_list = _load_sim_test_cases(sim_evals_yaml)
            templates = {
                e["name"]: e
                for e in eval_list
                if isinstance(e, dict) and "name" in e
            }
            for r in results:
                if "session_parameters" not in r and r.get("name") in templates:
                    r["session_parameters"] = templates[r["name"]].get(
                        "session_parameters", {}
                    )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return results, wall_clock_s


def load_tool_test_results(csv_or_json_path: str) -> list[dict[str, Any]]:
    """Load tool test results from a CSV or JSON file.

    Args:
      csv_or_json_path: Path to the CSV or JSON tool test results file.

    Returns:
      A list of formatted tool test result dictionaries.
    """
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", row.get("test", "?")),
                "tool": row.get("tool", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "latency_ms": row.get("latency (ms)", 0),
                "errors": row.get("errors", ""),
            }
        )
    return results


def load_callback_test_results(csv_or_json_path: str) -> list[dict[str, Any]]:
    """Load callback test results from a CSV or JSON file.

    Args:
      csv_or_json_path: Path to the CSV or JSON callback test results file.

    Returns:
      A list of formatted callback test result dictionaries.
    """
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", "?"),
                "agent": row.get("agent_name", "?"),
                "callback_type": row.get("callback_type", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "error": row.get("error_message", ""),
            }
        )
    return results
