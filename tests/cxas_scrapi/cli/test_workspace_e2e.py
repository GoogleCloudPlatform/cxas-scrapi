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

"""E2E lifecycle tests for GECX Workspace & Stateful Configuration Profiles."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import toml


@pytest.fixture
def sandbox_dir():
    """Fixture to create a completely isolated workspace directory structure."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cxas_e2e_")).resolve()
    # Create a dummy WORKSPACE marker to define the workspace root
    (temp_dir / "WORKSPACE").touch()

    # Create a project folder structure
    project_dir = temp_dir / "my_project"
    project_dir.mkdir()

    yield temp_dir, project_dir

    # Cleanup temp directory
    shutil.rmtree(temp_dir)


def run_cxas(args, cwd):
    """Run the cxas CLI as a subprocess."""
    cmd = [sys.executable, "-m", "cxas_scrapi.cli.main"] + args
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return res


def test_workspace_profiles_e2e_lifecycle(sandbox_dir):
    temp_dir, project_dir = sandbox_dir

    # Step 1: Initialize GECX workspace config
    res = run_cxas(
        ["workspace", "create", "--target-dir", "my_project"], cwd=temp_dir
    )
    assert res.returncode == 0, f"workspace create failed: {res.stderr}"

    config_toml = project_dir / "gecx-config.toml"
    assert config_toml.exists()
    with open(config_toml, encoding="utf-8") as f:
        created_cfg = toml.load(f)
    assert "default" in created_cfg
    assert created_cfg["default"]["gcp-project-id"] == "YOUR_PROJECT_ID"

    # Step 2: Write cascading configuration structure into gecx-config.toml
    cascading_config = {
        "default": {
            "gcp-project-id": "test-proj-dev",
            "deployed-app-id": "test-app-dev",
            "location": "us",
            "model": "gemini-3-flash",
            "modality": "text",
        },
        "profiles": {
            "prod": {
                "gcp-project-id": "test-proj-prod",
                "deployed-app-id": "test-app-prod",
                "us-central1": {
                    "location": "us-central1",
                },
            }
        },
    }
    with open(config_toml, "w", encoding="utf-8") as f:
        toml.dump(cascading_config, f)

    # Step 2.5: Verify workspace set with no params prints help
    res = run_cxas(["workspace", "set"], cwd=temp_dir)
    assert res.returncode == 1
    assert "usage:" in res.stdout.lower()
    assert "workspace set" in res.stdout

    # Step 3: Set the active workspace with a specific profile

    res = run_cxas(
        ["workspace", "set", "my_project", "--profile", "prod.us-central1"],
        cwd=temp_dir,
    )
    assert res.returncode == 0, f"workspace set failed: {res.stderr}"
    assert "Successfully set active project to" in res.stdout

    pointer_file = temp_dir / ".scrapi" / "active-project"
    assert pointer_file.exists()
    with open(pointer_file) as f:
        pointer_data = toml.load(f)
    assert pointer_data["base-dir"] == str(project_dir)
    assert pointer_data["active-profile"] == "prod.us-central1"

    # Step 4: Verify workspace show returns cascading resolved configuration
    res = run_cxas(["workspace", "show"], cwd=temp_dir)
    assert res.returncode == 0, f"workspace show failed: {res.stderr}"
    assert f"Project Path: {project_dir}" in res.stdout
    assert f"Configuration File: {config_toml}" in res.stdout

    # Decode effective output dictionary
    effective_config = json.loads(res.stdout.split("-" * 40)[1].strip())

    # Verify Overridden keys
    assert effective_config["gcp_project_id"] == "test-proj-prod"
    assert effective_config["deployed_app_id"] == "test-app-prod"
    assert effective_config["location"] == "us-central1"

    # Verify Inherited keys (the rest)
    assert effective_config["model"] == "gemini-3-flash"
    assert effective_config["modality"] == "text"

    # Step 5: Reset workspace without --profile to clear the active profile
    res = run_cxas(["workspace", "set", "my_project"], cwd=temp_dir)
    assert res.returncode == 0

    with open(pointer_file) as f:
        pointer_data = toml.load(f)
    assert "active-profile" not in pointer_data

    # Step 6: Verify workspace show resolves back to default baseline
    res = run_cxas(["workspace", "show"], cwd=temp_dir)
    assert res.returncode == 0
    effective_config = json.loads(res.stdout.split("-" * 40)[1].strip())
    assert effective_config["gcp_project_id"] == "test-proj-dev"
    assert effective_config["deployed_app_id"] == "test-app-dev"
    assert effective_config["location"] == "us"
    assert effective_config["model"] == "gemini-3-flash"
    assert effective_config["modality"] == "text"

    # Step 6.5: Verify setting profile only (no target_dir) updates the
    # profile inside the active workspace
    res = run_cxas(["workspace", "set", "--profile", "prod"], cwd=temp_dir)
    assert res.returncode == 0, f"workspace set --profile failed: {res.stderr}"

    with open(pointer_file) as f:
        pointer_data = toml.load(f)
    assert pointer_data["active-profile"] == "prod"

    res = run_cxas(["workspace", "show"], cwd=temp_dir)
    assert res.returncode == 0
    effective_config = json.loads(res.stdout.split("-" * 40)[1].strip())
    assert effective_config["gcp_project_id"] == "test-proj-prod"
    assert effective_config["location"] == "us"  # inherited

    # Step 6.6: Verify setting invalid profile causes workspace show to fail
    # with validation error
    res = run_cxas(
        ["workspace", "set", "--profile", "invalid_profile"], cwd=temp_dir
    )
    assert res.returncode == 0
    res = run_cxas(["workspace", "show"], cwd=temp_dir)
    assert res.returncode == 1
    assert (
        "Profile resolution error: Profile 'invalid_profile' is not"
        " defined under [profiles]"
        in res.stderr
        or (
            "Profile resolution error: Profile 'invalid_profile' is not"
            " defined under [profiles]"
        )
        in res.stdout
    )

    # Step 7: Unset active workspace

    res = run_cxas(["workspace", "unset"], cwd=temp_dir)
    assert res.returncode == 0, f"workspace unset failed: {res.stderr}"
    assert not pointer_file.exists()
