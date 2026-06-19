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

from unittest.mock import MagicMock, patch

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.callbacks import Callbacks


@patch("cxas_scrapi.core.callbacks.Callbacks.get_agent")
@patch("cxas_scrapi.core.callbacks.AgentServiceClient")
def test_list_callbacks(mock_client_cls, mock_get_agent):
    """Test list_callbacks."""
    mock_agent = MagicMock()
    mock_agent.before_agent_callbacks = []
    mock_agent.after_agent_callbacks = [MagicMock()]
    mock_agent.before_model_callbacks = []
    mock_agent.after_model_callbacks = []
    mock_agent.before_tool_callbacks = []
    mock_agent.after_tool_callbacks = []
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")
    res = cb_client.list_callbacks("agent1")

    assert len(res["before_agent_callbacks"]) == 0
    assert len(res["after_agent_callbacks"]) == 1
    mock_get_agent.assert_called_once_with("agent1")


@patch("cxas_scrapi.core.callbacks.Callbacks.get_agent")
@patch("cxas_scrapi.core.callbacks.AgentServiceClient")
def test_get_callback(mock_client_cls, mock_get_agent):
    """Test get_callback."""
    mock_agent = MagicMock()
    mock_cb = MagicMock()
    mock_cb.python_code = "print('hi')"
    mock_agent.before_model_callbacks = [mock_cb]
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")
    res = cb_client.get_callback("agent1", "before_model")

    assert res.python_code == "print('hi')"

    # Test out of bounds
    assert cb_client.get_callback("agent1", "before_model", index=5) is None


@patch("cxas_scrapi.core.callbacks.Callbacks.get_agent")
@patch("cxas_scrapi.core.callbacks.AgentServiceClient")
def test_create_callback(mock_client_cls, mock_get_agent):
    """Test create_callback."""
    mock_agent = types.Agent()
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    def my_cool_func(session):
        pass

    cb_client.create_callback("agent1", "before_model", my_cool_func)

    assert len(mock_agent.before_model_callbacks) == 1
    cb_code = mock_agent.before_model_callbacks[0].python_code
    assert "def beforeModelCallback" in cb_code
    cb_client.client.update_agent.assert_called_once()


@patch("cxas_scrapi.core.callbacks.Callbacks.get_agent")
@patch("cxas_scrapi.core.callbacks.AgentServiceClient")
def test_update_callback(mock_client_cls, mock_get_agent):
    """Test update_callback."""
    mock_agent = types.Agent()
    cb = types.Callback(python_code="old", description="old")
    mock_agent.before_model_callbacks.append(cb)
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    cb_client.update_callback(
        "agent1",
        "before_model",
        index=0,
        code="new_code",
        description="new_desc",
    )

    assert mock_agent.before_model_callbacks[0].python_code == "new_code"
    assert mock_agent.before_model_callbacks[0].description == "new_desc"
    cb_client.client.update_agent.assert_called_once()


@patch("cxas_scrapi.core.callbacks.Callbacks.get_agent")
@patch("cxas_scrapi.core.callbacks.AgentServiceClient")
def test_delete_callback(mock_client_cls, mock_get_agent):
    """Test delete_callback."""
    mock_agent = types.Agent()
    cb1 = types.Callback(python_code="c1")
    cb2 = types.Callback(python_code="c2")
    mock_agent.before_model_callbacks.extend([cb1, cb2])
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    cb_client.delete_callback("agent1", "before_model", index=0)

    assert len(mock_agent.before_model_callbacks) == 1
    assert mock_agent.before_model_callbacks[0].python_code == "c2"
    cb_client.client.update_agent.assert_called_once()


def test_execute_callback_string():
    """Test execute_callback with a string."""
    code = """
def beforeModelCallback(session):
    session['new_var'] = 123
    return session
"""
    res = Callbacks.execute_callback(code, {"some_var": "abc"})
    assert res["success"] is True
    assert res["result"]["new_var"] == 123


def test_execute_callback_callable():
    """Test execute_callback with a Callable."""

    def my_callable(session):
        session["added_by_callable"] = True
        return session

    res = Callbacks.execute_callback(my_callable, {})
    assert res["success"] is True
    assert res["result"]["added_by_callable"] is True
