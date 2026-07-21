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

from unittest.mock import patch

import cxas_scrapi.workspace as ws


@patch("cxas_scrapi.workspace.resolve_project_dir")
@patch("cxas_scrapi.workspace.load_workspace_config")
def test_callback_tests_path_resolves_correctly(
    mock_load_config, mock_resolve_dir
):
    mock_resolve_dir.return_value = "/mock/project"
    mock_load_config.return_value = {"evals_dir": "custom_evals"}

    assert (
        ws.callback_tests_path() == "/mock/project/custom_evals/callback_tests"
    )


@patch("cxas_scrapi.workspace.resolve_project_dir")
@patch("cxas_scrapi.workspace.load_workspace_config")
def test_callback_tests_path_default_evals_dir(
    mock_load_config, mock_resolve_dir
):
    mock_resolve_dir.return_value = "/mock/project"
    mock_load_config.return_value = {}  # No evals_dir specified

    assert ws.callback_tests_path() == "/mock/project/evals/callback_tests"


@patch("cxas_scrapi.workspace.resolve_project_dir")
@patch("cxas_scrapi.workspace.load_workspace_config")
def test_tool_tests_path_resolves_correctly(mock_load_config, mock_resolve_dir):
    mock_resolve_dir.return_value = "/mock/project"
    mock_load_config.return_value = {"evals_dir": "custom_evals"}

    assert ws.tool_tests_path() == "/mock/project/custom_evals/tool_tests"


@patch("cxas_scrapi.workspace.resolve_project_dir")
@patch("cxas_scrapi.workspace.load_workspace_config")
def test_goldens_path_resolves_correctly(mock_load_config, mock_resolve_dir):
    mock_resolve_dir.return_value = "/mock/project"
    mock_load_config.return_value = {}

    assert ws.goldens_path() == "/mock/project/evals/goldens"


@patch("cxas_scrapi.workspace.resolve_project_dir")
@patch("cxas_scrapi.workspace.load_workspace_config")
def test_simulations_path_resolves_correctly(
    mock_load_config, mock_resolve_dir
):
    mock_resolve_dir.return_value = "/mock/project"
    mock_load_config.return_value = {}

    assert ws.simulations_path() == "/mock/project/evals/simulations"


def test_migrate_config_to_toml_nesting_under_default(tmp_path):
    json_file = tmp_path / "gecx-config.json"
    json_file.write_text('{"gcp-project-id": "my-project", "location": "us"}')

    ws._migrate_config_to_toml(tmp_path)

    toml_file = tmp_path / "gecx-config.toml"
    assert toml_file.exists()
    assert not json_file.exists()

    # toml is imported at module level in tests, or we can import here
    import toml

    config = toml.loads(toml_file.read_text())
    assert "default" in config
    assert config["default"]["gcp-project-id"] == "my-project"
    assert config["default"]["location"] == "us"
