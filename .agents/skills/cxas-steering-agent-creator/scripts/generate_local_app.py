#!/usr/bin/env python3
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

import argparse
import json
import os
import re
import sys
import yaml

def sanitize_folder_name(name: str) -> str:
    """Sanitizes a display name to be a safe folder name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return re.sub(r"_+", "_", sanitized).strip("_")

def build_local_app(layout_path: str, output_dir: str) -> None:
    if not os.path.exists(layout_path):
        print(f"Error: Layout file not found at {layout_path}", file=sys.stderr)
        sys.exit(1)

    with open(layout_path, "r") as f:
        try:
            layout = yaml.safe_load(f)
        except Exception as e:
            print(f"Error: Failed to parse layout YAML: {e}", file=sys.stderr)
            sys.exit(1)

    app_name = layout.get("app_name")
    root_agent_def = layout.get("root_agent")
    subagents_def = layout.get("subagents", [])

    if not app_name or not root_agent_def:
        print("Error: Missing required root fields (app_name, root_agent) in layout YAML.", file=sys.stderr)
        sys.exit(1)

    # Ensure target directory exists (preserves pre-existing custom files/folders)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Write app.json metadata
    router_disp_name = root_agent_def.get("display_name")
    router_folder = sanitize_folder_name(router_disp_name)
    
    app_meta = {
        "displayName": app_name,
        "rootAgent": router_folder
    }
    with open(os.path.join(output_dir, "app.json"), "w") as f:
        json.dump(app_meta, f, indent=2)

    # Step 2: Create agents/ directory
    agents_dir = os.path.join(output_dir, "agents")
    os.makedirs(agents_dir, exist_ok=True)

    # Step 3: Process and Write Subagents folders
    child_agent_folders = []
    
    for sub_def in subagents_def:
        disp_name = sub_def.get("display_name")
        instruction_text = sub_def.get("instruction", "").strip()
        
        if not disp_name or not instruction_text:
            print("Error: Subagent is missing display_name or instruction text.", file=sys.stderr)
            sys.exit(1)
            
        folder_name = sanitize_folder_name(disp_name)
        child_agent_folders.append(folder_name)
        
        agent_path = os.path.join(agents_dir, folder_name)
        os.makedirs(agent_path, exist_ok=True)
        
        # Write agent metadata JSON
        agent_meta = {
            "displayName": disp_name,
            "instruction": f"agents/{folder_name}/instruction.txt"
        }
        if "model" in sub_def:
            agent_meta["modelSettings"] = {"model": sub_def["model"]}
            
        with open(os.path.join(agent_path, f"{folder_name}.json"), "w") as f:
            json.dump(agent_meta, f, indent=2)
            
        # Write XML instruction verbatim to instruction.txt
        with open(os.path.join(agent_path, "instruction.txt"), "w") as f:
            f.write(instruction_text + "\n")
            
        print(f"Generated local files for subagent: '{folder_name}'")

    # Step 4: Process and Write Root Steering Playbook folder
    router_path = os.path.join(agents_dir, router_folder)
    os.makedirs(router_path, exist_ok=True)
    
    root_instruction = root_agent_def.get("instruction", "").strip()
    if not root_instruction:
        print("Error: Root agent is missing instruction text.", file=sys.stderr)
        sys.exit(1)
        
    # Write router metadata JSON (include child playbooks list if there are subagents)
    router_meta = {
        "displayName": router_disp_name,
        "instruction": f"agents/{router_folder}/instruction.txt"
    }
    if child_agent_folders:
        router_meta["childAgents"] = child_agent_folders
        
    if "model" in root_agent_def:
        router_meta["modelSettings"] = {"model": root_agent_def["model"]}
        
    with open(os.path.join(router_path, f"{router_folder}.json"), "w") as f:
        json.dump(router_meta, f, indent=2)
        
    # Write Root XML instruction verbatim to instruction.txt
    with open(os.path.join(router_path, "instruction.txt"), "w") as f:
        f.write(root_instruction + "\n")

    print(f"Generated local files for root steering router: '{router_folder}'")
    print(f"\nSUCCESS: Standard CXAS local structure generated from YAML at: {output_dir}")

def main():
    parser = argparse.ArgumentParser(description="Generate standard local CXAS app structure from a YAML layout playbook specification.")
    parser.add_argument("--layout-path", required=True, help="Path to the YAML layout specification file.")
    parser.add_argument("--output-dir", required=True, help="Target local directory to write the CXAS app files.")
    args = parser.parse_args()

    build_local_app(args.layout_path, args.output_dir)

if __name__ == "__main__":
    main()
