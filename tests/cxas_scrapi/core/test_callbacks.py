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

from cxas_scrapi.core.callbacks import Callbacks


@patch("cxas_scrapi.core.callbacks.Agents.get_agent")
@patch("cxas_scrapi.core.agents.AgentServiceClient")
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


@patch("cxas_scrapi.core.callbacks.Agents.get_agent")
@patch("cxas_scrapi.core.agents.AgentServiceClient")
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


@patch("cxas_scrapi.core.callbacks.Agents.get_agent")
@patch("cxas_scrapi.core.agents.AgentServiceClient")
@patch("cxas_scrapi.core.callbacks.types.Callback")
def test_create_callback(mock_callback_cls, mock_client_cls, mock_get_agent):
    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_callback_cls.side_effect = side_effect

    """Test create_callback."""
    mock_agent = MagicMock()

    class MockRepeatedList(list):
        pass

    callbacks_list = MockRepeatedList()
    mock_agent.before_model_callbacks = callbacks_list
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    def my_cool_func(session):
        pass

    cb_client.create_callback("agent1", "before_model", my_cool_func)

    assert len(callbacks_list) == 1
    assert "def beforeModelCallback" in callbacks_list[0].python_code
    cb_client.client.update_agent.assert_called_once()


@patch("cxas_scrapi.core.callbacks.Agents.get_agent")
@patch("cxas_scrapi.core.agents.AgentServiceClient")
def test_update_callback(mock_client_cls, mock_get_agent):
    """Test update_callback."""
    mock_agent = MagicMock()
    mock_cb = MagicMock()
    mock_cb.python_code = "old"
    mock_cb.description = "old"
    mock_agent.before_model_callbacks = [mock_cb]
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    cb_client.update_callback(
        "agent1",
        "before_model",
        index=0,
        code="new_code",
        description="new_desc",
    )

    assert mock_cb.python_code == "new_code"
    assert mock_cb.description == "new_desc"
    cb_client.client.update_agent.assert_called_once()


@patch("cxas_scrapi.core.callbacks.Agents.get_agent")
@patch("cxas_scrapi.core.agents.AgentServiceClient")
def test_delete_callback(mock_client_cls, mock_get_agent):
    """Test delete_callback."""
    mock_agent = MagicMock()
    mock_cb1 = MagicMock()
    mock_cb2 = MagicMock()

    class MockRepeatedList(list):
        pass

    callbacks_list = MockRepeatedList([mock_cb1, mock_cb2])
    mock_agent.before_model_callbacks = callbacks_list
    mock_get_agent.return_value = mock_agent

    cb_client = Callbacks(app_name="projects/p/locations/l/apps/a")

    cb_client.delete_callback("agent1", "before_model", index=0)

    assert len(callbacks_list) == 1
    assert callbacks_list[0] == mock_cb2
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
