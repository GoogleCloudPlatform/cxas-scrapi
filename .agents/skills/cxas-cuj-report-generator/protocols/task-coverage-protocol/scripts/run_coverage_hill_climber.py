import argparse
import json
import os
import re
import shutil
import subprocess
import time
import yaml

# Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
target_skill = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
skill_file = os.path.join(
    target_skill, "protocols/task-coverage-protocol/SKILL.md"
)
supervisor_script = os.path.join(script_dir, "ingestor_supervisor.py")
source_dir = os.path.join(
    target_skill,
    "protocols/task-coverage-protocol/evals/cases/list_and_count_173_files/testdir",
)

STATE_FILE = "/tmp/ingestor/state.json"
OUTPUT_DIR = "/tmp/ingestor/outputs"
PROMPTS_DIR = "/tmp/ingestor/prompts"
HISTORY_FILE = "/tmp/ingestor/hill_climb_progress.json"
OPTIMIZER_PROMPT_FILE = "/tmp/ingestor/optimizer_prompt.txt"
OPTIMIZER_RESPONSE_FILE = "/tmp/ingestor/optimizer_response.txt"

MAX_ITERATIONS = 100
TARGET_PASS_RATE = 1.0


def load_skill_content():
    with open(skill_file, "r") as f:
        return f.read()


def write_skill_content(content):
    with open(skill_file, "w") as f:
        f.write(content)


def load_climb_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_climb_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def request_optimization(current_skill, failures):
    failure_log = ""
    for f in failures:
        failure_log += f"- File: {f['file']}\n  Error: {f['error']}\n"

    prompt = f"""You are an expert AI Prompt Optimizer. Your task is to refine the 'task-coverage-protocol' skill guidelines (`SKILL.md`) to fix ingestion validation failures.

Current SKILL.md:
{current_skill}

Failed files and ingestion errors:
{failure_log}

Analyze why the subagents failed to satisfy ingestion standards, and rewrite `SKILL.md` adding concrete, highly specific rules to prevent these failures in the future. Preserve all unrelated rules.
Output the complete, optimized SKILL.md content directly in your response with absolutely no markdown wrappers or explanations."""

    if os.path.exists(OPTIMIZER_RESPONSE_FILE):
        os.remove(OPTIMIZER_RESPONSE_FILE)

    with open(OPTIMIZER_PROMPT_FILE, "w") as f:
        f.write(prompt)

    print(
        '\nSPAWN_OPTIMIZER_DIRECTIVE: {"role": "OwlNext", "prompt_file":'
        f' "{OPTIMIZER_PROMPT_FILE}", "response_file":'
        f' "{OPTIMIZER_RESPONSE_FILE}"}}'
    )
    print(
        "AIS Master Orchestrator is waiting for OwlNext Optimizer response"
        " file..."
    )

    while not os.path.exists(OPTIMIZER_RESPONSE_FILE):
        time.sleep(2)

    with open(OPTIMIZER_RESPONSE_FILE, "r") as f:
        response = f.read()

    new_content = response.replace("```markdown", "").replace("```", "").strip()
    return new_content


def run_hill_climb():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    print("=== Launching Asynchronous Ingestion Task Coverage Hill-Climber ===")
    history = load_climb_history()
    start_iteration = len(history) + 1

    for iteration in range(start_iteration, args.iterations + 1):
        print(
            f"\n=== STARTING COVERAGE ITERATION {iteration}/{args.iterations} ==="
        )

        # Clean up supervisor states for a fresh iteration run
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)
        if os.path.exists(PROMPTS_DIR):
            shutil.rmtree(PROMPTS_DIR)

        iteration_failures = []

        # Loop through all files in slices
        while True:
            # 1. Prepare next batch slice
            subprocess.run(
                [
                    "python3",
                    supervisor_script,
                    "--source_dir",
                    source_dir,
                    "--target_skill",
                    target_skill,
                ],
                capture_output=True,
                text=True,
            )

            # Load active supervisor state
            with open(STATE_FILE, "r") as sf:
                state = json.load(sf)

            if state["phase"] == "COMPLETE":
                break

            # 2. Retrieve batch spec and print SPAWN_BATCH_DIRECTIVE
            spec_file = "/tmp/ingestor/batch_spec.json"
            print(
                f'\nSPAWN_BATCH_DIRECTIVE: {{"spec_file": "{spec_file}",'
                f' "output_dir": "{OUTPUT_DIR}"}}'
            )
            print(f"AIS Orchestrator is waiting for batch outputs...")

            # 3. Wait for parallel subagent outputs in outputs folder
            batch_spec = state["batch_spec"]
            while True:
                all_done = True
                for spec in batch_spec:
                    if not os.path.exists(spec["output_file"]):
                        all_done = False
                        break
                if all_done:
                    break
                time.sleep(2)

            # 4. Run supervisor ingest to grade outputs
            result = subprocess.run(
                [
                    "python3",
                    supervisor_script,
                    "--source_dir",
                    source_dir,
                    "--target_skill",
                    target_skill,
                ],
                capture_output=True,
                text=True,
            )

            # Parse output to check for failures
            stdout = result.stdout
            if "Detected" in stdout:
                # Extract failures from supervisor state
                with open(STATE_FILE, "r") as sf:
                    fail_state = json.load(sf)
                # Ingest failed, state reverted to PREPARE
                # We need to gather what files were in this batch and log them
                # (For simple tracing, we log that the batch spec files failed)
                for spec in batch_spec:
                    iteration_failures.append(
                        {
                            "file": spec["file_path"],
                            "error": "Output YAML validation or speaker schema failed.",
                        }
                    )
                # break out of this iteration early to trigger prompt optimization!
                break

        total_files = len(state.get("completed_files", [])) + len(
            iteration_failures
        )
        passes = len(state.get("completed_files", []))
        pass_rate = passes / total_files if total_files > 0 else 0

        print(
            f"\nIteration {iteration} Complete. Passes: {passes}/{total_files}"
            f" ({pass_rate * 100:.1f}%)"
        )

        history.append(
            {
                "iteration": iteration,
                "passes": passes,
                "total": total_files,
                "pass_rate": pass_rate,
                "failures": iteration_failures,
            }
        )
        save_climb_history(history)

        if pass_rate >= TARGET_PASS_RATE:
            print(
                f"🎉 Iteration {iteration} met the target pass rate! Continuing to"
                " run for stability verification..."
            )

        if iteration < args.iterations and pass_rate < TARGET_PASS_RATE:
            print(
                f"Pass rate ({pass_rate * 100:.1f}%) below target. Initiating OwlNext"
                " Optimizer..."
            )
            current_skill = load_skill_content()
            optimized_skill = request_optimization(
                current_skill, iteration_failures
            )
            write_skill_content(optimized_skill)
            print("Skill guidelines updated. Proceeding to next iteration...")

    print("=== Hill-climbing completed successfully ===")


if __name__ == "__main__":
    run_hill_climb()
