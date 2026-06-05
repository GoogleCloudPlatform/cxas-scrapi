import argparse
import json
import os
import shutil
import time
import yaml

# Configuration Limits
BATCH_SIZE = 10
STATE_FILE = "/tmp/ingestor/state.json"
OUTPUT_DIR = "/tmp/ingestor/outputs"
PROMPTS_DIR = "/tmp/ingestor/prompts"


class IngestorSupervisor:
    def __init__(
        self,
        source_dir,
        target_skill_dir,
        include_extensions=None,
        exclude_patterns=None,
    ):
        self.source_dir = source_dir
        self.target_skill_dir = target_skill_dir
        self.drawio_skill = os.path.join(
            target_skill_dir, "ingestors/files/drawio/SKILL.md"
        )
        self.cyara_skill = os.path.join(
            target_skill_dir, "ingestors/frameworks/cyara/SKILL.md"
        )
        self.include_extensions = include_extensions or []
        self.exclude_patterns = exclude_patterns or []

    def scan_and_count_files(self):
        """Recursively lists all files in the source directory matching the filters."""
        import fnmatch

        files = []
        for root, _, filenames in os.walk(self.source_dir):
            for f in filenames:
                # Check allowlist extensions if provided
                if self.include_extensions:
                    ext = os.path.splitext(f)[1].lower().replace(".", "")
                    if ext not in self.include_extensions:
                        continue

                # Check denylist patterns if provided
                if self.exclude_patterns:
                    ignored = False
                    for pattern in self.exclude_patterns:
                        if fnmatch.fnmatch(f, pattern):
                            ignored = True
                            break
                    if ignored:
                        continue

                files.append(os.path.join(root, f))
        return sorted(files)

    def generate_case_prompt(self, file_path):
        """Synthesizes target prompt instructions for Phase 2 Ingestion."""
        is_drawio = file_path.endswith(".drawio")
        skill_path = self.drawio_skill if is_drawio else self.cyara_skill

        return f"""You are executing Phase 2 (Transcript Generation) of the Ingestion Protocol.
Structural Data Path: {file_path}
Ingestor Skill Path: {skill_path}

Read the structural digest of the conversation. Generate a high-fidelity, natural dialogue transcript in YAML format that adheres strictly to the following requirements:
1. You MUST output a single YAML object matching:
   parent_cuj: "<Parent Category>"
   subintent_id: "<Unique slug>"
   subintent_name: "<Human readable name>"
   description: "<Short scenario description>"
   turns:
     - speaker: User
       text: "<Dialogue utterance>"
     ...
2. The conversation MUST start with a User turn.
3. Speaker must be 'Agent' or 'User'.
4. Output ONLY the raw YAML content in your response. No markdown wrappers or explanations."""

    def validate_ingestion_output(self, output_content):
        """Deterministically validates the output YAML transcript schema and metadata."""
        try:
            data = yaml.safe_load(output_content)
            if not isinstance(data, (list, dict)):
                return False, "Invalid YAML root structure."

            transcripts = data if isinstance(data, list) else [data]
            required_keys = [
                "subintent_id",
                "subintent_name",
                "parent_cuj",
                "description",
                "turns",
            ]

            for t in transcripts:
                if not isinstance(t, dict):
                    return False, "Transcript element must be a dictionary."
                # Enforce transcript_schema.yml metadata requirements
                missing = [k for k in required_keys if k not in t]
                if missing:
                    return (
                        False,
                        f"Missing required schema metadata keys: {missing}",
                    )

                if not t["turns"]:
                    return False, "Turns list cannot be empty."

                # Validate turn schema
                for turn in t["turns"]:
                    if "speaker" not in turn or "text" not in turn:
                        return (
                            False,
                            "Each turn must contain 'speaker' and 'text'.",
                        )
                    if turn.get("speaker") not in ["Agent", "User"]:
                        return False, f"Invalid speaker: {turn.get('speaker')}"
            return True, "Valid"
        except Exception as e:
            return False, f"YAML Parsing Error: {e}"

    def load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        return None

    def save_state(self, state):
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def prepare(self, state, all_files):
        """Prepares the next batch prompts and writes the JSON batch spec."""
        batch_start = state["batch_start"]
        batch = all_files[batch_start : batch_start + BATCH_SIZE]

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(PROMPTS_DIR, exist_ok=True)

        batch_spec = []
        for f_path in batch:
            name = os.path.basename(f_path)
            prompt = self.generate_case_prompt(f_path)
            prompt_path = os.path.join(PROMPTS_DIR, f"{name}_prompt.txt")
            output_path = os.path.join(OUTPUT_DIR, f"{name}_output.txt")

            if os.path.exists(output_path):
                os.remove(output_path)

            with open(prompt_path, "w") as pf:
                pf.write(prompt)

            batch_spec.append(
                {
                    "file_path": f_path,
                    "prompt_file": prompt_path,
                    "output_file": output_path,
                }
            )

        state["batch_spec"] = batch_spec
        state["phase"] = "INGEST"
        self.save_state(state)

        spec_file = "/tmp/ingestor/batch_spec.json"
        with open(spec_file, "w") as f:
            json.dump({"batch": batch_spec}, f, indent=2)

        print(
            f'\nSPAWN_BATCH_DIRECTIVE: {{"spec_file": "{spec_file}", "output_dir":'
            f' "{OUTPUT_DIR}"}}'
        )
        print(
            f"AIS Supervisor is waiting for {len(batch)} subagents to ingest the"
            " files..."
        )

    def ingest(self, state, all_files):
        """Validates all completed batch output files, rebasing/resubmitting failures if needed."""
        batch_spec = state["batch_spec"]

        # Check if all outputs have been written by the subagents
        all_done = True
        for spec in batch_spec:
            if not os.path.exists(spec["output_file"]):
                all_done = False
                break

        if not all_done:
            print("Waiting for pending outputs...")
            return

        # Validate and save outputs
        failures = []
        for spec in batch_spec:
            with open(spec["output_file"], "r") as f:
                output = f.read()

            passed, error = self.validate_ingestion_output(output)
            if passed:
                state["completed_files"].append(spec["file_path"])
            else:
                failures.append({"file": spec["file_path"], "error": error})

        if failures:
            print(
                f"\n⚠️ Detected {len(failures)} ingestion failures! Initiating"
                " auto-recovery..."
            )
            state["phase"] = "PREPARE"
            state["batch_spec"] = []
            self.save_state(state)
            return

        state["batch_start"] += BATCH_SIZE
        if state["batch_start"] < len(all_files):
            state["phase"] = "PREPARE"
            self.save_state(state)
            print(
                "\nBatch successfully ingested. Run script again to queue the next"
                " batch."
            )
        else:
            state["phase"] = "COMPLETE"
            self.save_state(state)
            print("\n🎉 All files successfully ingested!")

    def run(self):
        all_files = self.scan_and_count_files()
        state = self.load_state()
        if not state:
            state = {
                "batch_start": 0,
                "completed_files": [],
                "phase": "PREPARE",
            }

        if state["phase"] == "PREPARE":
            self.prepare(state, all_files)
        elif state["phase"] == "INGEST":
            self.ingest(state, all_files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source_dir", required=True)
    parser.add_argument("--target_skill", required=True)
    parser.add_argument(
        "--include_extensions",
        help="Comma-separated list of file extensions to allowlist.",
    )
    parser.add_argument(
        "--exclude_patterns",
        help="Comma-separated glob patterns of files to ignore.",
    )
    args = parser.parse_args()

    include_exts = (
        [e.strip().lower() for e in args.include_extensions.split(",")]
        if args.include_extensions
        else None
    )
    exclude_patts = (
        [p.strip() for p in args.exclude_patterns.split(",")]
        if args.exclude_patterns
        else None
    )

    supervisor = IngestorSupervisor(
        args.source_dir,
        args.target_skill,
        include_extensions=include_exts,
        exclude_patterns=exclude_patts,
    )
    supervisor.run()
