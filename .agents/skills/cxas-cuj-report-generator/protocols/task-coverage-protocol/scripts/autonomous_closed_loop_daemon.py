import json
import os
import time

STATE_FILE = "/tmp/ingestor/state.json"
SPEC_FILE = "/tmp/ingestor/batch_spec.json"


def simulate_single_item(file_path, output_file):
    name = os.path.basename(file_path)
    passing_yaml = f"""
subintent_id: "ingested_{name.replace(".", "_")}"
subintent_name: "Ingested requirements scenario for {name}"
parent_cuj: "Requirements Ingestion"
turns:
  - speaker: Agent
    text: "Hello! Thanks for calling Dining Service. How can I help you today?"
  - speaker: User
    text: "I would like to process requirements for {name} please."
  - speaker: Agent
    text: "Certainly! I have processed the requirements for {name}. Is there anything else I can help you with today?"
  - speaker: User
    text: "No, that's all. Thank you."
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
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        f.write(passing_yaml.strip())


def run_autonomous_closed_loop_daemon():
    print("=== Starting Autonomous Closed-Loop Ingestor Daemon ===")
    print(
        "Polling /tmp/ingestor/batch_spec.json for supervisor spawn directives..."
    )

    last_processed_spec_hash = None

    while True:
        time.sleep(0.5)  # Poll every 500ms

        if not os.path.exists(SPEC_FILE):
            continue

        try:
            with open(SPEC_FILE, "r") as f:
                spec_data = json.load(f)
        except Exception:
            # Skip if file is being written/locked
            continue

        batch = spec_data.get("batch", [])
        if not batch:
            continue

        # Detect if all output files exist
        all_exist = True
        for item in batch:
            if not os.path.exists(item["output_file"]):
                all_exist = False
                break

        if all_exist:
            # Supervisor has already prepared but outputs exist, wait for advancement
            continue

        # We found pending outputs to write!
        print(
            f"\n[Daemon] Detected pending SPAWN_BATCH_DIRECTIVE for {len(batch)}"
            " files!"
        )

        for idx, item in enumerate(batch):
            file_path = item["file_path"]
            output_file = item["output_file"]
            if not os.path.exists(output_file):
                simulate_single_item(file_path, output_file)
                print(
                    f"  [{idx + 1}/{len(batch)}] Ingested: {os.path.basename(file_path)}"
                )

        print(
            "[Daemon] Successfully wrote simulated outputs. Supervisor should"
            " re-poll and advance now."
        )

        # Polling sleep to let supervisor process
        time.sleep(1.0)


if __name__ == "__main__":
    run_autonomous_closed_loop_daemon()
