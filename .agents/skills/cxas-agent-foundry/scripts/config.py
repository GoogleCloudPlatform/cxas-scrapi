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

Delegates project resolution and configuration loading to the core SCRAPI
workspace module.
"""

from cxas_scrapi import workspace as ws


def resolve_project_dir():
    """Find the active project directory."""
    return ws.resolve_project_dir()


def get_project_path(*parts):
    """Join parts relative to the active project directory."""
    return ws._project_path(*parts)


def get_output_dir() -> str:
    """Get the output directory path from GECX workspace configuration."""
    return ws.get_output_dir()


def load_config():
    """Load configuration from the active project.

    Returns the configuration dictionary in snake_case format for backward
    compatibility.
    """
    config = ws.load_workspace_config()
    # Add the private _project_dir key expected by some older scripts
    config["_project_dir"] = ws.resolve_project_dir()
    return config


def load_app_name():
    """Load the full app resource name from the active configuration.

    Returns: "projects/{project}/locations/{location}/apps/{app_id}"
    """
    config = load_config()
    return (
        f"projects/{config['gcp_project_id']}"
        f"/locations/{config['location']}"
        f"/apps/{config['deployed_app_id']}"
    )
