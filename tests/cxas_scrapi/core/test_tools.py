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

import pytest

from cxas_scrapi.core.tools import Tools


@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_list_tools(mock_client_cls):
    mock_client = mock_client_cls.return_value

    mock_tool = MagicMock()
    mock_tool.name = "projects/p/locations/l/apps/A/tools/t1"
    mock_client.list_tools.return_value = [mock_tool]

    mock_toolset = MagicMock()
    mock_toolset.name = "projects/p/locations/l/apps/A/toolsets/ts1"
    mock_client.list_toolsets.return_value = [mock_toolset]

    t = Tools("projects/p/locations/l/apps/A")
    res = t.list_tools()
    assert len(res) == 2
    assert res[0].name == "projects/p/locations/l/apps/A/tools/t1"
    assert res[1].name == "projects/p/locations/l/apps/A/toolsets/ts1"


# @patch("cxas_scrapi.core.tools.AgentServiceClient")
# def test_get_tools_map(mock_client_cls):
#     mock_client = mock_client_cls.return_value

#     mock_t1 = MagicMock()
#     mock_t1.name = "projects/p/locations/l/apps/A/tools/t1"
#     mock_t1.display_name = "n1"
#     mock_client.list_tools.return_value = [mock_t1]

#     mock_ts1 = MagicMock()
#     mock_ts1.name = "projects/p/locations/l/apps/A/toolsets/ts1"
#     mock_ts1.display_name = "ns1"
#     mock_client.list_toolsets.return_value = [mock_ts1]

#     t = Tools("projects/p/locations/l/apps/A")
#     res = t.get_tools_map()
#     assert res["projects/p/locations/l/apps/A/tools/t1"] == "n1"
#     assert res["projects/p/locations/l/apps/A/toolsets/ts1"] == "ns1"

#     res_rev = t.get_tools_map(reverse=True)
#     assert res_rev["n1"] == "projects/p/locations/l/apps/A/tools/t1"
#     assert res_rev["ns1"] == "projects/p/locations/l/apps/A/toolsets/ts1"


@patch("cxas_scrapi.core.tools.types.GetToolRequest")
@patch("cxas_scrapi.core.tools.types.GetToolsetRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_get_tool(mock_client_cls, mock_ts_req_cls, mock_t_req_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_ts_req_cls.side_effect = side_effect
    mock_t_req_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")

    # Test tool
    mock_client.get_tool.return_value = MagicMock(name="t1")
    t.get_tool("projects/p/locations/l/apps/A/tools/T")
    mock_client.get_tool.assert_called_once()
    assert (
        mock_client.get_tool.call_args[1]["request"].name
        == "projects/p/locations/l/apps/A/tools/T"
    )

    # Test toolset
    mock_client.get_toolset.return_value = MagicMock(name="ts1")
    t.get_tool("projects/p/locations/l/apps/A/toolsets/TS")
    mock_client.get_toolset.assert_called_once()
    assert (
        mock_client.get_toolset.call_args[1]["request"].name
        == "projects/p/locations/l/apps/A/toolsets/TS"
    )


@patch("cxas_scrapi.core.tools.types.Tool")
@patch("cxas_scrapi.core.tools.types.CreateToolRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_create_tool(mock_client_cls, mock_req_cls, mock_tool_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect
    mock_tool_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")

    t.create_tool(
        tool_id="t1",
        display_name="my_tool",
        payload={"python_code": "print(1)"},
        tool_type="python_function",
        description="desc",
    )

    mock_client.create_tool.assert_called_once()
    args = mock_client.create_tool.call_args[1]["request"]
    assert args.parent == "projects/p/locations/l/apps/A"
    assert args.tool_id == "t1"
    assert args.tool.display_name == "my_tool"


@patch("cxas_scrapi.core.tools.types.Toolset")
@patch("cxas_scrapi.core.tools.types.CreateToolsetRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_create_toolset(mock_client_cls, mock_req_cls, mock_tool_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect
    mock_tool_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")

    t.create_tool(
        tool_id="ts1",
        display_name="my_toolset",
        payload={"open_api_schema": "yaml"},
        tool_type="open_api_toolset",
        description="desc",
    )

    mock_client.create_toolset.assert_called_once()
    args = mock_client.create_toolset.call_args[1]["request"]
    assert args.parent == "projects/p/locations/l/apps/A"
    assert args.toolset_id == "ts1"
    assert args.toolset.display_name == "my_toolset"
    assert args.toolset.description == "desc"


@patch("cxas_scrapi.core.tools.types.Tool")
@patch("cxas_scrapi.core.tools.types.UpdateToolRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_update_tool(mock_client_cls, mock_req_cls, mock_tool_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect
    mock_tool_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")
    t.update_tool(
        "projects/p/locations/l/apps/A/tools/T", display_name="new_name"
    )

    mock_client.update_tool.assert_called_once()
    args = mock_client.update_tool.call_args[1]["request"]
    assert args.tool.name == "projects/p/locations/l/apps/A/tools/T"
    assert args.tool.display_name == "new_name"


@patch("cxas_scrapi.core.tools.types.Toolset")
@patch("cxas_scrapi.core.tools.types.UpdateToolsetRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_update_toolset(mock_client_cls, mock_req_cls, mock_ts_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect
    mock_ts_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")
    t.update_tool(
        "projects/p/locations/l/apps/A/toolsets/TS", display_name="new_name"
    )

    mock_client.update_toolset.assert_called_once()
    args = mock_client.update_toolset.call_args[1]["request"]
    assert args.toolset.name == "projects/p/locations/l/apps/A/toolsets/TS"
    assert args.toolset.display_name == "new_name"


@patch("cxas_scrapi.core.tools.types.DeleteToolRequest")
@patch("cxas_scrapi.core.tools.types.DeleteToolsetRequest")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_delete_tool(mock_client_cls, mock_ts_req_cls, mock_t_req_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_ts_req_cls.side_effect = side_effect
    mock_t_req_cls.side_effect = side_effect

    t = Tools("projects/p/locations/l/apps/A")

    t.delete_tool("projects/p/locations/l/apps/A/tools/T")
    mock_client.delete_tool.assert_called_once()
    args = mock_client.delete_tool.call_args[1]["request"]
    assert args.name == "projects/p/locations/l/apps/A/tools/T"

    t.delete_tool("projects/p/locations/l/apps/A/toolsets/TS")
    mock_client.delete_toolset.assert_called_once()
    args2 = mock_client.delete_toolset.call_args[1]["request"]
    assert args2.name == "projects/p/locations/l/apps/A/toolsets/TS"


@patch("requests.post")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_execute_tool(mock_client_cls, mock_post):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": "fake", "variables": {"var1": "val1"}}

    mock_post.return_value = FakeResponse()

    t = Tools("projects/p/locations/l/apps/A")
    t.creds = MagicMock()
    t.creds.token = "token"

    with patch.object(
        t,
        "get_tools_map",
        return_value={"my_tool": "projects/p/locations/l/apps/A/tools/t1"},
    ):
        res = t.execute_tool(
            tool_display_name="my_tool",
            args={"query": "test"},
            variables={"var1": "val1"},
        )

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert (
        args[0]
        == "https://ces.googleapis.com/v1beta/projects/p/locations/l/apps/A:executeTool"
    )
    assert kwargs["json"]["tool"] == "projects/p/locations/l/apps/A/tools/t1"
    assert kwargs["json"]["args"] == {"query": "test"}
    assert kwargs["json"]["variables"] == {"var1": "val1"}

    # Verify response formatting
    assert res["result"] == "fake"
    assert res["variables"]["var1"] == "val1"


@patch("requests.post")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_execute_toolset(mock_client_cls, mock_post):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": "fake"}

    mock_post.return_value = FakeResponse()

    t = Tools("projects/p/locations/l/apps/A")
    t.creds = MagicMock()
    t.creds.token = "token"

    with patch.object(
        t,
        "get_tools_map",
        return_value={
            "my_tool_in_toolset": (
                "projects/p/locations/l/apps/A/toolsets/ts1/tools/"
                "my_tool_in_toolset"
            )
        },
    ):
        res = t.execute_tool(
            tool_display_name="my_tool_in_toolset",
            args={"query": "test"},
        )

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert (
        args[0]
        == "https://ces.googleapis.com/v1beta/projects/p/locations/l/apps/A:executeTool"
    )
    assert (
        kwargs["json"]["toolsetTool"]["toolset"]
        == "projects/p/locations/l/apps/A/toolsets/ts1"
    )
    assert kwargs["json"]["toolsetTool"]["toolId"] == "my_tool_in_toolset"
    assert kwargs["json"]["args"] == {"query": "test"}
    assert "variables" not in res


@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_execute_tool_not_found(mock_client_cls):
    t = Tools("projects/p/locations/l/apps/A")
    t.creds = MagicMock()

    with patch.object(
        t,
        "get_tools_map",
        return_value={
            "existing_tool": "projects/p/locations/l/apps/A/tools/o1"
        },
    ):
        with pytest.raises(ValueError, match="Tool 'missing_tool' not found"):
            t.execute_tool(tool_display_name="missing_tool", args={})


@patch("requests.post")
@patch("cxas_scrapi.core.tools.AgentServiceClient")
def test_execute_tool_with_context(mock_client_cls, mock_post):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": "fake"}

    mock_post.return_value = FakeResponse()

    t = Tools("projects/p/locations/l/apps/A")
    t.creds = MagicMock()
    t.creds.token = "token"

    with patch.object(
        t,
        "get_tools_map",
        return_value={"my_tool": "projects/p/locations/l/apps/A/tools/t1"},
    ):
        res = t.execute_tool(
            tool_display_name="my_tool",
            args={"query": "test"},
            variables={"var2": "var2"},
            context={
                "state": {"var1": "val1"},
                "events": [
                    {
                        "author": "user",
                        "content": {
                            "parts": [{"text": "Hello"}],
                            "role": "user",
                        },
                    }
                ],
            },
        )

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert (
        args[0]
        == "https://ces.googleapis.com/v1beta/projects/p/locations/l/apps/A:executeTool"
    )
    assert kwargs["json"]["tool"] == "projects/p/locations/l/apps/A/tools/t1"
    assert kwargs["json"]["args"] == {"query": "test"}
    assert kwargs["json"]["context"] == {
        "state": {"var1": "val1"},
        "events": [
            {
                "author": "user",
                "content": {"parts": [{"text": "Hello"}], "role": "user"},
            }
        ],
    }
    assert "variables" not in kwargs["json"]

    assert res["result"] == "fake"
