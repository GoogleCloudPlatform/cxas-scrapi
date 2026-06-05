import json
import os
import yaml

script_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.abspath(os.path.join(script_dir, "../.."))
evals_dir = os.path.join(base_dir, "evals")
results_dir = os.path.join(evals_dir, ".eval_results")


def compile_context():
    # 1. Read current SKILL.md guidelines
    main_skill_file = os.path.join(base_dir, "SKILL.md")
    main_skill = ""
    if os.path.exists(main_skill_file):
        with open(main_skill_file, "r") as f:
            main_skill = f.read()

    drawio_skill_file = os.path.join(
        base_dir, "ingestors/files/drawio/SKILL.md"
    )
    drawio_skill = ""
    if os.path.exists(drawio_skill_file):
        with open(drawio_skill_file, "r") as f:
            drawio_skill = f.read()

    cyara_skill_file = os.path.join(
        base_dir, "ingestors/frameworks/cyara/SKILL.md"
    )
    cyara_skill = ""
    if os.path.exists(cyara_skill_file):
        with open(cyara_skill_file, "r") as f:
            cyara_skill = f.read()

    # 2. Read active batch map to find iteration folder
    map_path = "/tmp/active_eval_batch.json"
    iteration = 1
    if os.path.exists(map_path):
        with open(map_path, "r") as f:
            active = json.load(f)
            iteration = active.get("iteration", 1)

    iteration_dir = os.path.join(results_dir, f"iteration_{iteration:03d}")
    progress_file = os.path.join(iteration_dir, "manual_evals_progress.yaml")

    failures = []
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            progress = yaml.safe_load(f)
            cases = progress.get("cases", [])
            for c in cases:
                if c.get("verdict") == "FAIL":
                    failures.append(
                        {
                            "case_name": c.get("name"),
                            "rationale": c.get("rationale"),
                        }
                    )

    # 3. Compile structured JSON
    context = {
        "iteration": iteration,
        "guidelines": {
            "main_skill": main_skill,
            "drawio_skill": drawio_skill,
            "cyara_skill": cyara_skill,
        },
        "failures": failures,
    }

    out_path = "/tmp/optimizer_context.json"
    with open(out_path, "w") as f:
        json.dump(context, f, indent=2)

    print(
        f"Successfully compiled optimization context inside {out_path} (Iteration"
        f" #{iteration})"
    )
    print(f"Total Failed cases found: {len(failures)}")


if __name__ == "__main__":
    compile_context()
