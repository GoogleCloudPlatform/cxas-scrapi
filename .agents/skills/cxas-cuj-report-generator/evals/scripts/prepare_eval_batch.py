import argparse
import json
import os
import yaml

# Resolve directories dynamically
script_dir = os.path.dirname(os.path.abspath(__file__))
skill_dir = os.path.abspath(os.path.join(script_dir, "../.."))

cases_dir = os.path.join(skill_dir, "evals/cases")
main_skill_file = os.path.join(skill_dir, "SKILL.md")


def load_skill_content():
    with open(main_skill_file, "r") as f:
        return f.read()


def prepare_batch(batch_start, batch_size, iteration=1, replications=1):
    sources = [
        {"dir": os.path.join(skill_dir, "evals/cases"), "suffix": ""},
        {
            "dir": os.path.join(
                skill_dir, "ingestors/frameworks/cyara/evals/cases"
            ),
            "suffix": "_cyara",
        },
        {
            "dir": os.path.join(
                skill_dir, "ingestors/files/drawio/evals/cases"
            ),
            "suffix": "_drawio",
        },
    ]

    all_cases = []
    for src in sources:
        if os.path.exists(src["dir"]):
            for d in os.listdir(src["dir"]):
                src_path = os.path.join(src["dir"], d)
                if os.path.isdir(src_path):
                    all_cases.append({"case_name": d, "path": src_path})

    all_cases = sorted(all_cases, key=lambda x: x["case_name"])
    batch = all_cases[batch_start : batch_start + batch_size]

    print(
        f"Preparing batch: indices {batch_start} to"
        f" {batch_start + len(batch) - 1} (Unique cases: {len(batch)},"
        f" Replications: {replications})"
    )

    subagents_spec = []

    main_skill = load_skill_content()

    for case_data in batch:
        case_name = case_data["case_name"]
        case_path = case_data["path"]
        with open(os.path.join(case_path, "test.yaml"), "r") as f:
            case_config = yaml.safe_load(f)

        expectations = case_config.get("expectations", [])

        # Read raw test files
        testdir = os.path.join(case_path, "testdir")
        raw_files_content = ""
        for root, _, files in os.walk(testdir):
            for file in files:
                filepath = os.path.join(root, file)
                with open(filepath, "r", errors="ignore") as f:
                    raw_files_content += f"\n--- File: {file} ---\n"
                    raw_files_content += f.read() + "\n"

        # Determine subagent type and role prefix based on suffix
        type_name = "research-agent"

        if "frameworks/cyara" in case_path:
            role_prefix = f"Cyara Ingestion Evaluator-{case_name}"
            ingestor_skill_file = os.path.join(
                skill_dir, "ingestors/frameworks/cyara/SKILL.md"
            )
        elif "files/drawio" in case_path:
            role_prefix = f"DrawIO Ingestion Evaluator-{case_name}"
            ingestor_skill_file = os.path.join(
                skill_dir, "ingestors/files/drawio/SKILL.md"
            )
        else:
            role_prefix = f"General Evaluator-{case_name}"
            ingestor_skill_file = main_skill_file

        # Read individual ingestor skill guidelines if applicable
        ingestor_skill = ""
        if (
            os.path.exists(ingestor_skill_file)
            and ingestor_skill_file != main_skill_file
        ):
            with open(ingestor_skill_file, "r") as f:
                ingestor_skill = f"\nIngestor Skill Guidelines:\n{f.read()}\n"

        # Construct prompt pointing to guidelines in workspace
        prompt = f"""You are an expert AI testing subagent. You MUST use the skill instructions at these workspace files to parse the raw documents:
1. Main CUJ Skill: {main_skill_file}
2. Ingestor Skill: {ingestor_skill_file}

Your task is to read those skills first (using your view_file tool), analyze the raw requirements documents provided below, and output a parsed, end-to-end, voice-realistic transcript in YAML format matching the guidelines.

Raw Requirements Documents:
{raw_files_content}

Strict Expectations for Ingestion:
{chr(10).join([f"- {exp}" for exp in expectations])}

Please run the ingestion pipeline and output the generated YAML transcript matching the expected schemas directly in your response. Do not include any markdown wrappers or extra conversational text, just output the raw YAML transcript."""

        # Duplicate subagent launch entries programmatically in-memory
        for r in range(replications):
            role = role_prefix
            if replications > 1:
                role += f"-run_{r + 1}"

            subagent_entry = {
                "TypeName": type_name,
                "Role": role,
                "Prompt": prompt,
                "Workspace": "inherit",
            }
            subagents_spec.append(subagent_entry)

    # Save specs and map
    spec_path = "/tmp/eval_subagents_spec.json"
    with open(spec_path, "w") as f:
        json.dump({"Subagents": subagents_spec}, f, indent=2)

    map_path = "/tmp/active_eval_batch.json"
    active_batch_data = {
        "batch_start": batch_start,
        "iteration": iteration,
        "replications": replications,
        "cases": [],
    }
    for case_data in batch:
        case_name = case_data["case_name"]
        case_path = case_data["path"]
        with open(os.path.join(case_path, "test.yaml"), "r") as f:
            case_config = yaml.safe_load(f)

        for r in range(replications):
            unique_case_name = case_name
            if replications > 1:
                unique_case_name += f"-run_{r + 1}"

            active_batch_data["cases"].append(
                {
                    "case_name": unique_case_name,
                    "expectations": case_config.get("expectations", []),
                }
            )

    with open(map_path, "w") as f:
        json.dump(active_batch_data, f, indent=2)

    print(f"Saved subagents spec to {spec_path}")
    print(f"Saved active batch metadata to {map_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch-start",
        type=int,
        required=True,
        help="Batch start index (0-indexed)",
    )
    parser.add_argument("--batch-size", type=int, default=20, help="Batch size")
    parser.add_argument(
        "--iteration", type=int, default=1, help="Evaluation iteration sequence"
    )
    parser.add_argument(
        "--replications",
        type=int,
        default=1,
        help="Number of concurrent replications runs per test case",
    )
    args = parser.parse_args()
    prepare_batch(
        args.batch_start, args.batch_size, args.iteration, args.replications
    )
