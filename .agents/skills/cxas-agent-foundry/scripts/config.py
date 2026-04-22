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

"""Shared config loader for GECX eval scripts.

All scripts use gecx-config.json as the single source of truth for project
configuration. This module provides a common loader so each script doesn't
duplicate the same config-reading logic.

Projects live in named folders (e.g., tmobile/, humana/) with their own
gecx-config.json. The active project is resolved via:
1. GECX_PROJECT env var
2. CWD contains gecx-config.json (backward compat)
3. .active-project pointer file at workspace root
4. Single-project auto-detect

Usage:
    from config import load_app_name, load_config, get_project_path

    app_name = load_app_name()                    # "projects/P/locations/L/apps/A"
    config = load_config()                         # full config dict
    evals_dir = get_project_path("evals", "goldens")  # /workspace/tmobile/evals/goldens
"""

import json
import os
import sys


_project_dir = None


def _find_workspace_root():
    """Find the workspace root by looking for .agents/, .claude/, or .gemini/ in ancestors."""
    path = os.getcwd()
    for _ in range(10):
        if (os.path.isdir(os.path.join(path, ".agents"))
                or os.path.isdir(os.path.join(path, ".claude"))
                or os.path.isdir(os.path.join(path, ".gemini"))):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return os.getcwd()


def resolve_project_dir():
    """Find the active project directory.

    Search order:
    1. GECX_PROJECT env var → {workspace}/{GECX_PROJECT}
    2. .active-project pointer → {workspace}/{name}
    3. CWD has gecx-config.json → CWD (fallback)
    4. Single subdirectory with gecx-config.json → auto-detect
    """
    global _project_dir
    if _project_dir:
        return _project_dir

    workspace = _find_workspace_root()

    # 1. Env var
    env_project = os.environ.get("GECX_PROJECT")
    if env_project:
        candidate = os.path.join(workspace, env_project)
        if os.path.exists(os.path.join(candidate, "gecx-config.json")):
            _project_dir = candidate
            return _project_dir
        print(f"Error: GECX_PROJECT={env_project} but {candidate}/gecx-config.json not found.")
        sys.exit(1)

    # 2. .active-project pointer (takes priority over CWD)
    pointer = os.path.join(workspace, ".active-project")
    if os.path.exists(pointer):
        with open(pointer) as f:
            name = f.read().strip()
        if name:
            candidate = os.path.join(workspace, name)
            if os.path.exists(os.path.join(candidate, "gecx-config.json")):
                _project_dir = candidate
                return _project_dir
            print(f"Error: Active project '{name}' but {candidate}/gecx-config.json not found.")
            sys.exit(1)

    # 3. CWD has gecx-config.json (fallback for single-project setups)
    if os.path.exists(os.path.join(os.getcwd(), "gecx-config.json")):
        _project_dir = os.getcwd()
        return _project_dir

    # 4. Auto-detect single project
    projects = []
    for entry in os.listdir(workspace):
        full = os.path.join(workspace, entry)
        if os.path.isdir(full) and not entry.startswith(".") and os.path.exists(os.path.join(full, "gecx-config.json")):
            projects.append(full)

    if len(projects) == 1:
        _project_dir = projects[0]
        return _project_dir
    elif len(projects) > 1:
        names = [os.path.basename(p) for p in projects]
        print(f"Error: Multiple projects found ({', '.join(names)}). Set the active project:")
        print(f"  echo '{names[0]}' > .active-project")
        sys.exit(1)

    print("Error: No project found. Create a project folder with gecx-config.json:")
    print("  mkdir myproject && python .agents/skills/cxas-agent-foundry/scripts/configure.py")
    sys.exit(1)


def get_project_path(*parts):
    """Join parts relative to the active project directory.

    Usage: get_project_path("evals", "goldens") → /workspace/tmobile/evals/goldens
    """
    return os.path.join(resolve_project_dir(), *parts)


def load_config():
    """Load gecx-config.json from the active project and return the full config dict.

    Required keys: gcp_project_id, deployed_app_id
    Optional keys: location (default: "us"), modality, default_channel, app_dir
    """
    config_file = get_project_path("gecx-config.json")
    if not os.path.exists(config_file):
        print(f"Error: {config_file} not found. Create gecx-config.json with your project settings.")
        sys.exit(1)

    with open(config_file) as f:
        config = json.load(f)

    project = config.get("gcp_project_id")
    app_id = config.get("deployed_app_id")
    if not project or not app_id:
        print(f"Error: gecx-config.json missing 'gcp_project_id' or 'deployed_app_id'.")
        sys.exit(1)

    config.setdefault("location", "us")
    # Store the resolved project dir in config for convenience
    config["_project_dir"] = resolve_project_dir()
    return config


def load_app_name():
    """Load the full app resource name from gecx-config.json.

    Returns: "projects/{project}/locations/{location}/apps/{app_id}"
    """
    config = load_config()
    return (
        f"projects/{config['gcp_project_id']}"
        f"/locations/{config['location']}"
        f"/apps/{config['deployed_app_id']}"
    )
