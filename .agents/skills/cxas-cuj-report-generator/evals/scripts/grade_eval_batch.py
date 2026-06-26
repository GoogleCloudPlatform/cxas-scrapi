import json
import os
import pathlib
import sys
import yaml

# Add scripts directory to path to import grading helpers
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from hill_climber import results_dir, history_file
from utils.grading import grade_transcript_compliance, score_naturalness


def load_active_batch(map_path="/tmp/active_eval_batch.json"):
    """Loads the active evaluation batch spec metadata."""
    if not os.path.exists(map_path):
        print(f"Active batch mapping {map_path} not found.")
        return None
    with open(map_path, "r") as f:
        return json.load(f)


def load_climb_history(history_path):
    """Loads the existing optimization hill-climb history json."""
    if os.path.exists(history_path):
        try:
            with open(history_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def load_model_output(name, case_results_dir):
    """Locates case output strictly from authentic generator artifacts written by the agent."""
    output = ""
    case_path = pathlib.Path(case_results_dir)
    for yaml_file in case_path.rglob("*.y*ml"):
        if yaml_file.name != "test.yaml":
            with open(yaml_file, "r") as f:
                output += "\n---\n" + f.read()
    return output


def grade_single_case(
    case_spec, iteration_results_dir, case_index, total_cases
):
    """Grades a single evaluation case, writing its grade and raw results."""
    name = case_spec["case_name"]
    conv_id = case_spec.get("conv_id")
    expectations = case_spec["expectations"]
    case_results_dir = os.path.join(iteration_results_dir, name)

    print(f"[{case_index}/{total_cases}] Grading {name}...")

    model_output = load_model_output(name, case_results_dir)
    if not model_output:
        print(
            "  Verdict: FAIL - No model response found in transcript or fallback."
        )
        return False, "No model response found.", [], 0

    # Save raw model output
    os.makedirs(case_results_dir, exist_ok=True)
    with open(os.path.join(case_results_dir, "raw_output.txt"), "w") as out_f:
        out_f.write(model_output)

    # Grade rules and score naturalness
    passed, rationale = grade_transcript_compliance(model_output, expectations)
    naturalness_scores = score_naturalness(model_output)
    case_avg = (
        sum(naturalness_scores) / len(naturalness_scores)
        if len(naturalness_scores) > 0
        else 0
    )

    grade_res = {
        "passed": passed,
        "rationale": rationale,
        "naturalness_scores": naturalness_scores,
        "average_naturalness": case_avg,
    }
    with open(os.path.join(case_results_dir, "grade.json"), "w") as gf:
        json.dump(grade_res, gf, indent=2)

    scores_str = ", ".join(f"{s}/3" for s in naturalness_scores)
    if passed:
        print(
            f"  Verdict: PASS (Naturalness: [{scores_str}] - Avg: {case_avg:.1f}/3)"
        )
    else:
        print(
            f"  Verdict: FAIL - {rationale} (Naturalness: [{scores_str}] - Avg:"
            f" {case_avg:.1f}/3)"
        )

    return passed, rationale, naturalness_scores, case_avg


def save_iteration_progress(
    climb_history,
    history_path,
    iteration_results_dir,
    cases,
    failures,
    passes,
    total_naturalness,
    scored_cases,
):
    """Computes averages, writes the JSON history, and outputs the progress yaml."""
    current_iteration = len(climb_history) + 1
    avg_naturalness = (
        total_naturalness / scored_cases if scored_cases > 0 else 0
    )

    iteration_log = {
        "iteration": current_iteration,
        "passes": passes,
        "total": len(cases),
        "pass_rate": passes / len(cases) if len(cases) > 0 else 0,
        "avg_naturalness": avg_naturalness,
        "failures": failures,
    }
    climb_history.append(iteration_log)

    # 1. Save the updated hill climber JSON history
    with open(history_path, "w") as hf:
        json.dump(climb_history, hf, indent=2)

    # 2. Synthesize manual_evals_progress.yaml for HTML reports
    progress_yaml = {"cases": []}
    for log_item in failures:
        progress_yaml["cases"].append(
            {
                "name": log_item["case"],
                "verdict": "FAIL",
                "rationale": log_item["rationale"],
            }
        )

    passed_names = [
        c["case_name"]
        for c in cases
        if c["case_name"] not in [f["case"] for f in failures]
    ]
    for pn in passed_names:
        progress_yaml["cases"].append(
            {
                "name": pn,
                "verdict": "PASS",
                "rationale": "All expectations satisfied.",
            }
        )

    progress_file = os.path.join(
        iteration_results_dir, "manual_evals_progress.yaml"
    )
    with open(progress_file, "w") as pf:
        yaml.dump(progress_yaml, pf, default_flow_style=False)

    print("\n=== BATCH GRADING COMPLETE ===")
    print(
        f"Passes: {passes}/{len(cases)} ({iteration_log['pass_rate'] * 100:.1f}%)"
    )
    print(f"Average Naturalness: {avg_naturalness:.1f}/3")


def grade_eval_batch():
    """Coordinates active batch specs extraction, grading execution and reports compilation."""
    active_batch = load_active_batch()
    if not active_batch:
        return

    cases = active_batch.get("cases", [])
    iteration = active_batch.get("iteration", 1)
    iteration_results_dir = os.path.join(
        results_dir, f"iteration_{iteration:03d}"
    )
    os.makedirs(iteration_results_dir, exist_ok=True)

    print(
        f"Grading {len(cases)} cases in this batch (Iteration #{iteration})..."
    )

    climb_history = load_climb_history(history_file)

    passes = 0
    failures = []
    total_naturalness = 0
    scored_cases = 0

    for idx, c in enumerate(cases):
        passed, rationale, _, case_avg = grade_single_case(
            c, iteration_results_dir, idx + 1, len(cases)
        )
        if passed:
            passes += 1
        else:
            failures.append({"case": c["case_name"], "rationale": rationale})

        if case_avg > 0:
            total_naturalness += case_avg
            scored_cases += 1

    save_iteration_progress(
        climb_history,
        history_file,
        iteration_results_dir,
        cases,
        failures,
        passes,
        total_naturalness,
        scored_cases,
    )


if __name__ == "__main__":
    grade_eval_batch()
