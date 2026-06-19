import json
import os
import shutil
from ingestor_supervisor import (
    IngestorSupervisor,
    OUTPUT_DIR,
    PROMPTS_DIR,
    STATE_FILE,
)
import yaml


def test_ingestor_supervisor_dry_run():
    print("=== Starting Ingestor Supervisor Dry-Run Verification ===")

    # 1. Cleanup previous state
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    if os.path.exists(PROMPTS_DIR):
        shutil.rmtree(PROMPTS_DIR)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_skill = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
    source_dir = os.path.join(
        target_skill,
        "protocols/task-coverage-protocol/evals/cases/list_and_count_173_files/testdir",
    )

    # Programmatically bootstrap exactly 173 mock files
    os.makedirs(source_dir, exist_ok=True)
    for i in range(173):
        file_path = os.path.join(source_dir, f"file_{i}.txt")
        with open(file_path, "w") as f:
            f.write(f"Mock requirements spec content for file {i}")

    supervisor = IngestorSupervisor(
        source_dir,
        target_skill,
        include_extensions=["txt"],
        exclude_patterns=["*.zip"],
    )

    # 2. Scan and prepare first batch
    print("\n--- Step 1: Prepare Batch Slice #1 ---")
    supervisor.run()

    state = supervisor.load_state()
    assert state is not None, "State file not created!"
    assert state["phase"] == "INGEST", (
        f"Incorrect state phase: {state['phase']}"
    )
    assert state["batch_start"] == 0, (
        f"Incorrect batch start index: {state['batch_start']}"
    )
    assert len(state["batch_spec"]) == 10, (
        f"Incorrect batch spec size: {len(state['batch_spec'])}"
    )

    spec_file = "/tmp/ingestor/batch_spec.json"
    assert os.path.exists(spec_file), (
        "Specification file batch_spec.json not created!"
    )
    with open(spec_file, "r") as f:
        spec_data = json.load(f)

    print("✅ Step 1: Preparation completed successfully. Sliced 10 prompts.")

    # 3. Simulate Subagents Processing (Write passing mock outputs)
    print("\n--- Step 2: Simulate subagent outputs writing ---")
    for spec in spec_data["batch"]:
        dummy_yaml = f"""
subintent_id: dummy_intent
subintent_name: "Dummy Ingested Scenario"
parent_cuj: "Dummy CUJ"
description: "A high-fidelity dummy requirements transcript."
turns:
  - speaker: Agent
    text: "Hello! Thanks for calling Dining Service. How can I help you today?"
  - speaker: User
    text: "I want to place an order."
  - speaker: Agent
    text: "Thank you for calling Dining Service! Goodbye."
    tool_call:
      name: end_session
      payload:
        session_escalated: false
        reason: "Conversation completed successfully"
      response:
        result: "success"
"""
        with open(spec["output_file"], "w") as f:
            f.write(dummy_yaml)

    print("✅ Step 2: Successfully wrote 10 simulated passing outputs.")

    # 4. Run supervisor to ingest
    print("\n--- Step 3: Ingest Batch Slice #1 ---")
    supervisor.run()

    # Verify state has advanced to PREPARE
    state = supervisor.load_state()
    assert state["phase"] == "PREPARE", (
        f"Incorrect phase after ingestion: {state['phase']}"
    )
    assert state["batch_start"] == 10, (
        f"Incorrect batch start index after ingestion: {state['batch_start']}"
    )

    print("✅ Step 3: Successfully ingested slice #1 and set phase to PREPARE.")

    # 5. Run supervisor again to prepare next slice
    print("\n--- Step 4: Prepare Batch Slice #2 ---")
    supervisor.run()

    # Verify state is now INGEST and advanced batch_spec loaded
    state = supervisor.load_state()
    assert state["phase"] == "INGEST", (
        f"Incorrect phase after preparing slice #2: {state['phase']}"
    )
    assert len(state["batch_spec"]) == 10, (
        f"Incorrect advanced batch spec size: {len(state['batch_spec'])}"
    )
    assert state["batch_spec"][0]["file_path"].endswith("file_107.txt"), (
        "Incorrect starting file in slice 2:"
        f" {state['batch_spec'][0]['file_path']}"
    )

    # Teardown: Clean up programmatically bootstrapped mock files
    for i in range(173):
        file_path = os.path.join(source_dir, f"file_{i}.txt")
        if os.path.exists(file_path):
            os.remove(file_path)

    print(
        "✅ Step 4: Successfully prepared slice #2 (files alphabetically 10-19)"
        " and re-entered INGEST phase."
    )
    print(
        "\n=== INGESTOR SUPERVISOR DRY-RUN VERIFICATION COMPLETELY PASSED! ==="
    )


if __name__ == "__main__":
    test_ingestor_supervisor_dry_run()
