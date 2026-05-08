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

"""Non-interactive project setup for GECX agents.

Creates the project directory, pulls an existing app (if --app-id is provided),
detects modality from app.json, and writes gecx-config.json.

For new agents (no --app-id):
  python scripts/setup-project.py --project-id my-project --name my-agent --modality audio

For existing agents:
  python scripts/setup-project.py --project-id my-project --app-id 95bd3826-f40c-4430-9b34-fb824369eb39

  Modality, model, and app name are auto-detected from the pulled app.json.
"""

import argparse
import json
import os
import subprocess
import sys


def detect_modality_from_app(app_dir):
    """Read app.json and detect model/modality from modelSettings."""
    app_json_candidates = []
    for root, dirs, files in os.walk(app_dir):
        if "app.json" in files:
            app_json_candidates.append(os.path.join(root, "app.json"))

    if not app_json_candidates:
        return None, None, None

    app_json_path = app_json_candidates[0]
    with open(app_json_path) as f:
        app = json.load(f)

    model = ""
    model_settings = app.get("modelSettings", {})
    if isinstance(model_settings, dict):
        model = model_settings.get("model", "")

    app_name = app.get("displayName", app.get("name", ""))

    # Detect modality from model name
    if "live" in model.lower():
        modality = "audio"
    else:
        modality = "text"

    return app_name, model, modality


def main():
    parser = argparse.ArgumentParser(description="Set up a GECX project directory.")
    parser.add_argument("--project-id", required=True, help="GCP project ID")
    parser.add_argument("--app-id", help="Existing app ID (short name or UUID). If provided, pulls the app and auto-detects modality.")
    parser.add_argument("--name", help="Project folder name (defaults to app-id or must be provided for new agents)")
    parser.add_argument("--modality", choices=["audio", "text"], help="Agent modality (required for new agents, auto-detected for existing)")
    parser.add_argument("--location", default="us", help="GCP location (default: us)")

    args = parser.parse_args()

    # Determine project folder name
    folder_name = args.name or args.app_id
    if not folder_name:
        print("Error: --name is required for new agents (or provide --app-id for existing).")
        sys.exit(1)

    # Find workspace root
    workspace = os.getcwd()
    for _ in range(10):
        if os.path.isdir(os.path.join(workspace, ".agents")) or os.path.isdir(os.path.join(workspace, ".claude")):
            break
        parent = os.path.dirname(workspace)
        if parent == workspace:
            break
        workspace = parent

    project_dir = os.path.join(workspace, folder_name)
    os.makedirs(project_dir, exist_ok=True)
    # eval-reports/ holds iteration snapshots, sim/tool/callback result JSONs,
    # and the last-run.log shell-redirect target. Create it now so the iteration
    # loop's `> <project>/eval-reports/last-run.log 2>&1` works on first run.
    os.makedirs(os.path.join(project_dir, "eval-reports"), exist_ok=True)

    if args.app_id:
        # --- Existing agent ---
        app_resource = f"projects/{args.project_id}/locations/{args.location}/apps/{args.app_id}"
        app_dir = os.path.join(project_dir, "cxas_app")

        # Use cxas CLI from the same venv as the current Python
        cxas_bin = os.path.join(os.path.dirname(sys.executable), "cxas")
        print(f"Pulling app: {app_resource}")
        result = subprocess.run([
            cxas_bin, "pull", app_resource,
            "--project-id", args.project_id,
            "--location", args.location,
            "--target-dir", app_dir,
        ], cwd=workspace)

        if result.returncode != 0:
            print(f"Error: Failed to pull app. Exit code {result.returncode}")
            sys.exit(1)

        # Detect modality from pulled app.json
        app_name, model, modality = detect_modality_from_app(app_dir)

        if not model:
            print("Warning: Could not detect model from app.json. Using defaults.")
            model = "gemini-3.1-flash-live" if (args.modality == "audio") else "gemini-3-flash"
            modality = args.modality or "audio"

        if not app_name:
            app_name = args.app_id

        deployed_app_id = args.app_id
        print(f"Detected: model={model}, modality={modality}, app_name={app_name}")

    else:
        # --- New agent ---
        if not args.modality:
            print("Error: --modality is required for new agents.")
            sys.exit(1)

        modality = args.modality
        model = "gemini-3.1-flash-live" if modality == "audio" else "gemini-3-flash"
        app_name = folder_name
        deployed_app_id = None

    # Write gecx-config.json
    config = {
        "gcp_project_id": args.project_id,
        "location": args.location,
        "app_name": app_name,
        "deployed_app_id": deployed_app_id,
        "app_dir": "cxas_app/",
        "model": model,
        "modality": modality,
        "default_channel": modality,
    }

    config_path = os.path.join(project_dir, "gecx-config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Wrote: {config_path}")

    # Set active project
    pointer_path = os.path.join(workspace, ".active-project")
    with open(pointer_path, "w") as f:
        f.write(folder_name)
    print(f"Set active project: {folder_name}")

    # For existing apps, bootstrap eval files
    if deployed_app_id:
        print(f"\nBootstrapping eval files...")
        bootstrap_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bootstrap-evals.py")
        result = subprocess.run([sys.executable, bootstrap_script], cwd=workspace)
        if result.returncode != 0:
            print(f"Error: bootstrap-evals.py failed (exit code {result.returncode}). Project setup is incomplete.")
            sys.exit(result.returncode)

    print(f"\nProject ready at: {project_dir}")
    if deployed_app_id:
        print(f"Connected to existing app: {app_name} ({deployed_app_id})")
    else:
        print(f"New project: {app_name} (deploy with cxas push after building)")


if __name__ == "__main__":
    main()
