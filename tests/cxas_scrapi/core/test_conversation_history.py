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

from cxas_scrapi.core.conversation_history import ConversationHistory


@patch("cxas_scrapi.core.conversation_history.AgentServiceClient")
def test_conversation_list(mock_client_cls):
    """Test ConversationHistory.list_conversations."""
    mock_client = mock_client_cls.return_value
    mock_conv = MagicMock()
    mock_conv.name = "projects/p/locations/l/apps/a/conversations/c1"

    mock_response = [mock_conv]
    mock_client.list_conversations.return_value = mock_response

    conv_client = ConversationHistory(app_name="projects/p/locations/l/apps/a")
    res = conv_client.list_conversations()

    assert len(res) == 1
    assert res[0].name == "projects/p/locations/l/apps/a/conversations/c1"
    mock_client.list_conversations.assert_called_once()


@patch("cxas_scrapi.core.conversation_history.AgentServiceClient")
def test_conversation_get(mock_client_cls):
    """Test ConversationHistory.get_conversation."""
    mock_client = mock_client_cls.return_value
    mock_conv = MagicMock()
    mock_conv.name = "projects/p/locations/l/apps/a/conversations/c1"
    mock_client.get_conversation.return_value = mock_conv

    conv_client = ConversationHistory(app_name="projects/p/locations/l/apps/a")
    res = conv_client.get_conversation("c1")

    assert res.name == "projects/p/locations/l/apps/a/conversations/c1"
    # Should prefix with app_name if not present
    mock_client.get_conversation.assert_called_once()


def test_conversation_dict_to_yaml():
    """Test static method conversation_dict_to_yaml."""
    conv_dict = {
        "turns": [
            {"messages": [{"role": "user", "chunks": [{"text": "hi"}]}]},
            {
                "messages": [
                    {"role": r"root_agent", "chunks": [{"text": "hello"}]}
                ]
            },
            {
                "messages": [
                    {
                        "role": "root_agent",
                        "chunks": [
                            {
                                "tool_call": {
                                    "display_name": "my_tool",
                                    "args": {"param1": "val1"},
                                }
                            }
                        ],
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "user",
                        "chunks": [
                            {
                                "tool_response": {
                                    "display_name": "my_tool",
                                    "response": {"result": "success"},
                                }
                            }
                        ],
                    }
                ]
            },
        ]
    }

    res = ConversationHistory.conversation_dict_to_yaml(conv_dict)
    assert res["name"] == "Converted_Conversation"
    assert len(res["turns"]) == 3
    assert res["turns"][0] == {"user": "hi"}
    assert res["turns"][1] == {"root_agent": "hello"}
    assert res["turns"][2] == {
        "tool_call": {"tool": "my_tool", "args": {"param1": "val1"}}
    }
    assert len(res["mocks"]) == 1
    assert res["mocks"][0] == {
        "tool_response": {"tool": "my_tool", "response": {"result": "success"}}
    }


@patch(
    "cxas_scrapi.core.conversation_history.ConversationHistory.get_conversation"
)
def test_export_conversation_to_yaml(mock_get_conv):
    """Test ConversationHistory.export_conversation_to_yaml."""

    # Mock the to_dict method
    with patch("cxas_scrapi.core.conversation_history.type") as mock_type:
        mock_to_dict = MagicMock(return_value={"turns": []})
        mock_type.return_value.to_dict = mock_to_dict

        with patch("cxas_scrapi.core.conversation_history.AgentServiceClient"):
            conv_client = ConversationHistory(
                app_name="projects/p/locations/l/apps/a"
            )
            yaml_str = conv_client.export_conversation_to_yaml("c1")
            assert "name: Converted_Conversation" in yaml_str


@patch("cxas_scrapi.core.conversation_history.types.DeleteConversationRequest")
@patch("cxas_scrapi.core.conversation_history.AgentServiceClient")
def test_delete_conversation(mock_client_cls, mock_req_cls):
    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect
    """Test delete_conversation."""
    mock_client = mock_client_cls.return_value

    conv_client = ConversationHistory(app_name="projects/p/locations/l/apps/a")
    conv_client.delete_conversation("c1")

    mock_client.delete_conversation.assert_called_once()

    # Verify the requested name
    called_request = mock_client.delete_conversation.call_args[1]["request"]
    assert (
        called_request.name == "projects/p/locations/l/apps/a/conversations/c1"
    )


@patch("cxas_scrapi.core.conversation_history.AgentServiceClient")
@patch("cxas_scrapi.utils.latency_parser.LatencyParser.extract_trace_metrics")
@patch(
    "cxas_scrapi.utils.latency_parser.LatencyParser.fetch_conversation_traces"
)
def test_get_latency_metrics_dfs_limit(
    mock_fetch, mock_extract, mock_client_cls
):
    """Test get_latency_metrics_dfs with integer and string limits."""
    conv_client = ConversationHistory(app_name="projects/p/locations/l/apps/a")

    # Create 5 mock conversations
    mock_convs = []
    for i in range(5):
        m = MagicMock()
        m.name = f"projects/p/locations/l/apps/a/conversations/c{i}"
        mock_convs.append(m)

    with patch.object(
        conv_client, "list_conversations", return_value=mock_convs
    ):
        # Test with limit as int 2
        conv_client.get_latency_metrics_dfs(limit=2)
        mock_fetch.assert_called_with(
            ["c0", "c1"], conv_client.get_conversation
        )

        # Test with limit as string "3"
        conv_client.get_latency_metrics_dfs(limit="3")
        mock_fetch.assert_called_with(
            ["c0", "c1", "c2"], conv_client.get_conversation
        )
