import json
import os
import time


def simulate_batch_ingestion():
    spec_path = "/tmp/ingestor/batch_spec.json"
    if not os.path.exists(spec_path):
        print("Error: batch_spec.json not found!")
        return False

    with open(spec_path, "r") as f:
        spec_data = json.load(f)

    batch = spec_data.get("batch", [])
    print(
        f"[Simulator] Consuming SPAWN_BATCH_DIRECTIVE for {len(batch)} files..."
    )

    for idx, item in enumerate(batch):
        file_path = item["file_path"]
        output_file = item["output_file"]
        name = os.path.basename(file_path)

        # Simulate processing time (50ms per file)
        time.sleep(0.05)

        # Synthesize clean, passing YAML transcript
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
        with open(output_file, "w") as out_f:
            out_f.write(passing_yaml.strip())

        print(
            f"  [{idx + 1}/{len(batch)}] Ingested and saved output for {name}"
        )

    print(
        f"[Simulator] Successfully completed batch ingestion for {len(batch)}"
        " files."
    )
    return True


if __name__ == "__main__":
    simulate_batch_ingestion()
