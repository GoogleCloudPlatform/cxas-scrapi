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

"""Unit tests for the CallbackEvals testing utility."""

from unittest.mock import MagicMock, patch

import pandas as pd

from cxas_scrapi.evals.callback_evals import CallbackEvals


def test_run_callback_tests_no_files(tmp_path):
    utils = CallbackEvals()
    result = utils.test_all_callbacks_in_app_dir(app_dir=str(tmp_path))
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0
    assert list(result.columns) == [
        "agent_name",
        "callback_type",
        "test_name",
        "status",
        "error_message",
    ]


def test_run_callback_tests_missing_python_code(tmp_path):
    utils = CallbackEvals()

    agent_dir = tmp_path / "agents" / "agentA" / "my_callbacks" / "cb1"
    agent_dir.mkdir(parents=True)
    test_file = agent_dir / "test.py"
    test_file.write_text("def test_dummy(): pass")

    result = utils.test_all_callbacks_in_app_dir(app_dir=str(tmp_path))
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_run_callback_tests_success(tmp_path):
    utils = CallbackEvals()

    agent_dir = tmp_path / "agents" / "agentA" / "my_callbacks" / "cb1"
    agent_dir.mkdir(parents=True)
    test_file = agent_dir / "test.py"
    test_file.write_text("""def test_dummy():
        assert True
""")

    python_code_file = agent_dir / "python_code.py"
    python_code_file.write_text("def my_func(): pass\n")

    result = utils.test_all_callbacks_in_app_dir(app_dir=str(tmp_path))
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert result.iloc[0]["status"] == "PASSED"
    assert result.iloc[0]["test_name"] == "test_dummy"
    assert result.iloc[0]["agent_name"] == "agentA"
    assert result.iloc[0]["callback_type"] == "my_callbacks"


def test_run_callback_tests_failure(tmp_path):
    utils = CallbackEvals()

    agent_dir = tmp_path / "agents" / "agentA" / "my_callbacks" / "cb1"
    agent_dir.mkdir(parents=True)
    test_file = agent_dir / "test.py"
    test_file.write_text("""def test_dummy_fail():
        assert False, 'Failed purposely'
""")

    python_code_file = agent_dir / "python_code.py"
    python_code_file.write_text("def my_func(): pass\n")

    result = utils.test_all_callbacks_in_app_dir(app_dir=str(tmp_path))
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert result.iloc[0]["status"] == "FAILED"
    assert result.iloc[0]["test_name"] == "test_dummy_fail"
    assert result.iloc[0]["agent_name"] == "agentA"
    assert result.iloc[0]["callback_type"] == "my_callbacks"


def test_test_single_callback_for_agent(tmp_path):
    utils = CallbackEvals()

    test_file = tmp_path / "test_cb.py"
    test_file.write_text("""def test_dummy():
        assert True
""")

    mock_callback = MagicMock()
    mock_callback.python_code = "def my_func(): pass\n"

    # Create mock agent
    mock_agent = MagicMock()
    # Mock the field the user is accessing in their if/elif structure
    mock_agent.before_model_callbacks = [mock_callback]

    with patch("cxas_scrapi.evals.callback_evals.Agents") as MockAgents:
        mock_client = MockAgents.return_value
        mock_client.get_agents_map.return_value = {
            "agentA": "projects/P/locations/L/apps/A/agents/agentA"
        }
        mock_client.get_agent.return_value = mock_agent

        result = utils.test_single_callback_for_agent(
            app_name="projects/P/locations/L/apps/A",
            agent_name="agentA",
            callback_type="before_model_callback",
            test_file_path=str(test_file),
        )

    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert result.iloc[0]["status"] == "PASSED"
    assert result.iloc[0]["test_name"] == "test_dummy"
    assert result.iloc[0]["agent_name"] == "agentA"
    assert result.iloc[0]["callback_type"] == "before_model_callback"
