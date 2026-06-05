import argparse
import json
import os
import pathlib
import re
import shutil
import time
from utils.grading import (
    grade_transcript_compliance,
    score_naturalness,
)
import yaml

STATE_FILE = "/tmp/evals/hill_climber_state.json"

# Paths setup dynamically
script_dir = os.path.dirname(os.path.abspath(__file__))
skill_base_dir = os.path.abspath(os.path.join(script_dir, "../.."))

skill_file = os.path.join(skill_base_dir, "SKILL.md")

cases_dir = os.path.join(skill_base_dir, "evals/cases")
results_dir = os.path.join(skill_base_dir, "evals/.eval_results")
progress_file = os.path.join(
    skill_base_dir, "evals/.eval_results/manual_evals_progress.yaml"
)
history_file = os.path.join(
    skill_base_dir, "evals/.eval_results/hill_climb_progress.json"
)

MAX_ITERATIONS = 100
TARGET_PASS_RATE = 1.0
BATCH_SIZE = 14


def load_skill_content():
    with open(skill_file, "r") as f:
        return f.read()


def write_skill_content(content):
    with open(skill_file, "w") as f:
        f.write(content)


def prepare_evaluation_prompt(case_name, iteration):
    case_path = os.path.join(cases_dir, case_name)
    case_results_dir = os.path.join(results_dir, case_name, str(iteration))
    testdir = os.path.join(case_results_dir, "testdir")

    # Setup execution directory
    os.makedirs(testdir, exist_ok=True)
    shutil.copytree(
        os.path.join(case_path, "testdir"), testdir, dirs_exist_ok=True
    )

    # Load expectations
    with open(os.path.join(case_path, "test.yaml"), "r") as f:
        case_yaml = yaml.safe_load(f)
    expectations = case_yaml.get("expectations", [])

    # Generalize prompt construction via test.yaml configuration.
    if "prompt" in case_yaml:
        prompt = (
            case_yaml["prompt"]
            .replace("{testdir}", testdir)
            .replace("{skill_file}", skill_file)
        )
    else:
        skill_name = case_yaml.get("skill_name", "cxas-cuj-report-generator")

        skill_path_conf = case_yaml.get("skill_path", "SKILL.md")
        if skill_path_conf.startswith("/"):
            skill_path = skill_path_conf
        else:
            skill_path = os.path.join(skill_base_dir, skill_path_conf)

        task_desc = case_yaml.get(
            "task",
            "Your task is to analyze the provided input files located in:"
            f" {testdir}",
        )
        output_instr = case_yaml.get(
            "output_instructions",
            "Output the final YAML content directly in your response.",
        )

        prompt = f"""Please apply the '{skill_name}' skill (located at {skill_path}).
{task_desc}
{output_instr}"""

    return prompt, expectations, case_results_dir


def process_evaluation_batch(batch, iteration):
    batch_spec = []
    expectations_map = {}
    out_dir_map = {}

    for case_name in batch:
        prompt, expectations, case_results_dir = prepare_evaluation_prompt(
            case_name, iteration
        )

        testdir = os.path.join(case_results_dir, "testdir")
        prompt_path = os.path.join(case_results_dir, "prompt.txt")
        batch_spec.append(
            {
                "case_name": case_name,
                "workspace_dir": case_results_dir,
                "testdir": testdir,
                "prompt_file": prompt_path,
            }
        )
        expectations_map[case_name] = expectations
        out_dir_map[case_name] = case_results_dir

    spec_file_path = "/tmp/evals/batch_spec.json"
    batch_resolved_path = "/tmp/evals/batch_resolved.txt"

    with open(spec_file_path, "w") as f:
        json.dump({"batch": batch_spec}, f, indent=2)

    if os.path.exists(batch_resolved_path):
        os.remove(batch_resolved_path)

    instructions = [
        f"A batch of {len(batch)} evaluation tasks has been prepared.",
        (
            f"Read the batch spec file at `{spec_file_path}` to get the required"
            " configuration data for each case."
        ),
        (
            "Please spawn a separate subagent for each case in the batch using"
            " `invoke_subagent`."
        ),
        (
            "**CRITICAL**: For each subagent, set its `Workspace` to point"
            " directly to its `workspace_dir`."
        ),
        (
            "Instruct all subagents with the exact same simple prompt: 'Read"
            " `prompt.txt` in your workspace. Follow its instructions exactly to"
            " analyze the files in `testdir/`.'"
        ),
        (
            "Wait for all subagents to finish their native execution naturally"
            " (they should be writing their final transcript YAML outputs via"
            " `append_turn.py` as defined in the SKILL.md constraints)."
        ),
        (
            "Once ALL subagents have completed, execute a bash command to"
            f" touch/create `{batch_resolved_path}`. This semaphore file will"
            " signal me to proceed with native script grading."
        ),
    ]

    formatted_instructions = "\n".join(
        f"{i + 1}. {instr}" for i, instr in enumerate(instructions)
    )

    print(
        "\n=======================\n🤖 AGENT ACTION REQUIRED: SPAWN EVALUATION"
        f" BATCH\n=======================\n{formatted_instructions}\n\nThis"
        " ensures each subagent reacts exactly as it would in reality without"
        " knowing it is in an evaluation.\n\nI am exiting execution to release"
        " the terminal lock. Once you finish steps 1-7, please run `python3"
        " hill_climber.py` again to resume grading!"
    )

    return batch_spec, expectations_map, out_dir_map


def grade_evaluation_batch(batch_spec, expectations_map, out_dir_map):
    passes = 0
    failures = []

    for spec in batch_spec:
        case_name = spec["case_name"]
        case_dir = out_dir_map[case_name]

        # Authentically read whatever yaml logs the agent wrote out
        output = ""
        case_path = pathlib.Path(case_dir)
        for yaml_file in case_path.rglob("*.y*ml"):
            if yaml_file.name != "test.yaml":
                with open(yaml_file, "r") as f:
                    output += "\n---\n" + f.read()

        with open(os.path.join(case_dir, "raw_output.txt"), "w") as f:
            f.write(output)

        passed, rationale = grade_transcript_compliance(
            output, expectations_map[case_name]
        )
        naturalness_scores = score_naturalness(output)
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
        with open(os.path.join(out_dir_map[case_name], "grade.json"), "w") as f:
            json.dump(grade_res, f, indent=2)

        if passed:
            passes += 1
            print(f"  Case {case_name}: PASS (Naturalness: {case_avg:.1f}/3)")
        else:
            failures.append({"case": case_name, "rationale": rationale})
            print(
                f"  Case {case_name}: FAIL - {rationale} (Naturalness:"
                f" {case_avg:.1f}/3)"
            )

    return passes, failures


def save_iteration_results(
    iteration, passes, all_cases, failures, climb_history
):
    pass_rate = passes / len(all_cases)
    print(
        f"Iteration {iteration} Complete. Passes: {passes}/{len(all_cases)}"
        f" ({pass_rate * 100:.1f}%)"
    )

    iteration_log = {
        "iteration": iteration,
        "passes": passes,
        "total": len(all_cases),
        "pass_rate": pass_rate,
        "failures": failures,
    }
    climb_history.append(iteration_log)

    with open(history_file, "w") as f:
        json.dump(climb_history, f, indent=2)

    progress_yaml = {"cases": []}
    for log_item in failures:
        progress_yaml["cases"].append(
            {
                "name": log_item["case"],
                "verdict": "FAIL",
                "rationale": log_item["rationale"],
            }
        )
    passed_cases = [
        c for c in all_cases if c not in [f["case"] for f in failures]
    ]
    for pc in passed_cases:
        progress_yaml["cases"].append(
            {
                "name": pc,
                "verdict": "PASS",
                "rationale": "All expectations satisfied.",
            }
        )

    os.makedirs(
        os.path.join(results_dir, f"iteration_{iteration:03d}"), exist_ok=True
    )
    progress_path = os.path.join(
        results_dir, f"iteration_{iteration:03d}", "manual_evals_progress.yaml"
    )
    with open(progress_path, "w") as f:
        yaml.dump(progress_yaml, f, default_flow_style=False)

    return pass_rate


def load_all_cases():
    return sorted(
        [
            d
            for d in os.listdir(cases_dir)
            if os.path.isdir(os.path.join(cases_dir, d))
        ]
    )


def load_climb_history():
    climb_history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                climb_history = json.load(f)
            print(
                f"Loaded climb history containing {len(climb_history)} iterations."
            )
        except Exception:
            pass
    return climb_history


STATE_FILE = "/tmp/evals/state.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def prepare(state, iteration, all_cases, args):
    print(
        f"\n=== STARTING ITERATION {iteration}/{args.iterations} (Batch"
        f" starting at index {state['batch_start']}) ==="
    )

    batch = all_cases[state["batch_start"] : state["batch_start"] + BATCH_SIZE]
    batch_spec, expectations_map, out_dir_map = process_evaluation_batch(
        batch, iteration
    )

    state["expectations_map"] = expectations_map
    state["out_dir_map"] = out_dir_map
    state["batch_spec"] = batch_spec
    state["phase"] = "GRADE"
    save_state(state)


def grade(state, iteration, all_cases, climb_history, args):
    print("Resuming execution... Initiating batch grading pipeline.")
    batch_spec = state["batch_spec"]
    expectations_map = state["expectations_map"]
    out_dir_map = state["out_dir_map"]

    batch_passes, batch_failures = grade_evaluation_batch(
        batch_spec, expectations_map, out_dir_map
    )

    state["passes"] += batch_passes
    state["failures"].extend(batch_failures)
    state["batch_start"] += BATCH_SIZE

    # More batches remaining in this iteration
    if state["batch_start"] < len(all_cases):
        state["phase"] = "PREPARE"
        save_state(state)
        print(
            "Grading complete! Please run `python3 hill_climber.py` to queue up"
            " the next batch."
        )
        return

    # Iteration fully complete
    pass_rate = save_iteration_results(
        iteration, state["passes"], all_cases, state["failures"], climb_history
    )

    if pass_rate >= TARGET_PASS_RATE:
        print(
            "\n🎉 EXCELLENT! We have achieved the target pass rate"
            f" ({pass_rate * 100:.1f}%) in {iteration} iterations!"
        )
        print(
            "Agent, the skill has been successfully optimized. You may stop your"
            " operation now."
        )
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        return

    # Did not reach target, initiate optimization
    print(
        f"\n⚠️ Pass rate ({pass_rate * 100:.1f}%) is below our target"
        f" ({TARGET_PASS_RATE * 100}%). Proceeding to Optimizer phase."
    )

    state["phase"] = "OPTIMIZE"
    save_state(state)

    # We recursively call orchestrate again to naturally fall into OPTIMIZE branch
    orchestrate_evaluations()


def optimize(state):
    current_skill = load_skill_content()

    from utils.optimizer import request_skill_optimization

    # request_skill_optimization prints tasks and exits expecting optimizer_response.txt
    request_skill_optimization(current_skill, state["failures"])

    state["phase"] = "APPLY_OPTIMIZATION"
    save_state(state)

    print(
        "\nI am exiting execution to release the terminal lock. Once the"
        " Optimizer subagent has written to `/tmp/optimizer_response.txt`, run"
        " `python3 hill_climber.py` to finalize the guidelines update."
    )


def apply_optimization(state):
    print("Resuming execution... Processing Optimization updates.")
    response_path = "/tmp/optimizer_response.txt"
    if not os.path.exists(response_path):
        print(
            f"Error: {response_path} not found. Please ensure the Optimizer"
            " completed."
        )
        return

    with open(response_path, "r") as f:
        response = f.read()

    os.remove(response_path)

    optimized_skill = (
        response.replace("```markdown", "").replace("```", "").strip()
    )
    write_skill_content(optimized_skill)
    print("Skill guidelines gracefully updated.")

    state["iteration"] += 1
    state["batch_start"] = 0
    state["passes"] = 0
    state["failures"] = []
    state["phase"] = "PREPARE"
    save_state(state)

    print(
        "\nOptimization cycle concluded! Run `python3 hill_climber.py` to begin"
        " the next iteration."
    )


def orchestrate_evaluations():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Total optimization iterations",
    )
    args = parser.parse_args()

    all_cases = load_all_cases()
    climb_history = load_climb_history()

    state = load_state()

    if not state:
        print(
            "Initializing Evaluator Engine... I will act as your guide and"
            " validation layer."
        )
        print(f"Found {len(all_cases)} test cases.")
        state = {
            "iteration": len(climb_history) + 1,
            "batch_start": 0,
            "passes": 0,
            "failures": [],
            "phase": "PREPARE",
        }

    # Hard exit if we hit our max loops
    if state["iteration"] > args.iterations:
        print("Maximum iterations reached!")
        return

    iteration = state["iteration"]

    match state["phase"]:
        case "PREPARE":
            prepare(state, iteration, all_cases, args)
        case "GRADE":
            grade(state, iteration, all_cases, climb_history, args)
        case "OPTIMIZE":
            optimize(state)
        case "APPLY_OPTIMIZATION":
            apply_optimization(state)


if __name__ == "__main__":
    orchestrate_evaluations()
