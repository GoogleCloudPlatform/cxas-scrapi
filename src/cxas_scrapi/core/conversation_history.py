"""ConversationHistory class for CXAS Scrapi."""

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

import datetime
import logging
from typing import Any, Dict, List

import pandas as pd
import yaml
from google.cloud.ces_v1beta import AgentServiceClient, types

from cxas_scrapi.core.common import Common
from cxas_scrapi.utils.latency_parser import LatencyParser

logger = logging.getLogger(__name__)


class ConversationHistory(Common):
    """Core Class for managing Conversation History."""

    def __init__(
        self,
        app_name: str = None,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )

        self.app_name = app_name
        self.client_options = self._get_client_options(self.app_name)
        self.client = AgentServiceClient(
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )

    @staticmethod
    def parse_conversation_to_yaml(filepath):
        """Parses a direct CXAS Conversation History textproto into the
        target FDE YAML format."""
        with open(filepath, "r") as f:
            text = f.read()

        parsed = Common.parse_textproto(text)
        return ConversationHistory.conversation_dict_to_yaml(parsed)

    @staticmethod
    def conversation_dict_to_yaml(conv_dict):
        """Parses a direct CXAS Conversation History dictionary into the
        target FDE YAML format."""

        turns = conv_dict.get("turns", [])
        if not isinstance(turns, list):
            turns = [turns]

        out_yaml = {
            "name": "Converted_Conversation",
            "turns": [],
            "expectations": [],
            "mocks": [],
        }

        for turn in turns:
            messages = turn.get("messages", [])
            for message in messages:
                role = message.get("role", "")
                chunks = message.get("chunks", [])
                text = " ".join(
                    [c.get("text", "") for c in chunks if "text" in c]
                )
                if text:
                    # role = "agent name" for agent responses and tool calls
                    out_yaml["turns"].append({role: text})

                for chunk in chunks:
                    if "tool_call" in chunk:
                        tool_call = chunk["tool_call"]
                        tool_name = tool_call.get(
                            "display_name",
                            tool_call.get("name", tool_call.get("tool", "")),
                        )
                        tool_args = Common.unwrap_struct(
                            tool_call.get("args", {})
                        )
                        out_yaml["turns"].append(
                            {
                                "tool_call": {
                                    "tool": tool_name,
                                    "args": tool_args,
                                }
                            }
                        )
                    elif "tool_response" in chunk:
                        tool_response = chunk["tool_response"]
                        tool_name = tool_response.get(
                            "display_name",
                            tool_response.get(
                                "name", tool_response.get("tool", "")
                            ),
                        )
                        tool_response = Common.unwrap_struct(
                            tool_response.get("response", {})
                        )
                        out_yaml["mocks"].append(
                            {
                                "tool_response": {
                                    "tool": tool_name,
                                    "response": tool_response,
                                }
                            }
                        )

        return out_yaml

    def list_conversations(
        self,
        time_filter: str = None,
        source_filter: str = None,
    ) -> Any:
        """Lists conversations in the configured app.

        Args:
            time_filter: An optional relative time filter (e.g. '7d',
                '24h', '1m').
            source_filter: An optional enum string filter (e.g. 'LIVE',
                'SIMULATOR', 'EVAL').
        """
        filter_str = None
        if time_filter:
            now = datetime.datetime.utcnow()
            valid = False
            if time_filter.endswith("d"):
                days = int(time_filter[:-1])
                past = now - datetime.timedelta(days=days)
                valid = True
            elif time_filter.endswith("h"):
                hours = int(time_filter[:-1])
                past = now - datetime.timedelta(hours=hours)
                valid = True
            elif time_filter.endswith("m"):
                minutes = int(time_filter[:-1])
                past = now - datetime.timedelta(minutes=minutes)
                valid = True
            if valid:
                formatted_time = past.strftime("%Y-%m-%dT%H:%M:%SZ")
                filter_str = f'start_time > "{formatted_time}"'
            else:
                logger.warning(
                    f"Unrecognized time_filter format: {time_filter}. Ignoring."
                )

        request_kwargs = {"parent": self.app_name, "filter": filter_str}

        if source_filter:
            source_enum_val = getattr(
                types.Conversation.Source, source_filter.upper(), None
            )
            if source_enum_val is not None:
                request_kwargs["source"] = source_enum_val
            else:
                logger.warning(
                    f"Unrecognized source_filter format: {source_filter}. "
                    f"Ignoring."
                )

        request = types.ListConversationsRequest(**request_kwargs)

        # Return the response iterator directly to allow auto-pagination
        return list(self.client.list_conversations(request=request))

    def get_latency_metrics_dfs(
        self,
        time_filter: str = "7d",
        source_filter: str = None,
        limit: int = 50,
    ) -> Dict[str, pd.DataFrame]:
        """Generates latency metrics DataFrames from recent conversation traces.

        Args:
            time_filter: Relative timeframe to fetch (e.g. '7d', '24h').
            source_filter: Optional source environment to filter by (e.g.
                'LIVE', 'SIMULATOR').
            limit: Maximum number of conversations to retrieve and parse.

        Returns:
            Dictionary containing DataFrames: tool_summary, tool_details,
            callback_summary, callback_details, guardrail_summary,
            guardrail_details
        """
        limit = int(limit) if limit is not None else 50

        convs = self.list_conversations(
            time_filter=time_filter,
            source_filter=source_filter,
        )
        if not convs:
            logger.warning(
                f"No conversations found for time_filter: {time_filter} "
                f"and source_filter: {source_filter}"
            )
            return {
                "tool_summary": pd.DataFrame(),
                "tool_details": pd.DataFrame(),
                "callback_summary": pd.DataFrame(),
                "callback_details": pd.DataFrame(),
                "guardrail_summary": pd.DataFrame(),
                "guardrail_details": pd.DataFrame(),
                "llm_summary": pd.DataFrame(),
                "llm_details": pd.DataFrame(),
            }

        # Extract the string IDs, limiting to the requested amount
        conv_ids = [c.name.split("/")[-1] for c in convs[:limit]]

        traces = LatencyParser.fetch_conversation_traces(
            conv_ids, self.get_conversation
        )
        return LatencyParser.extract_trace_metrics(
            traces, context_type="conversation"
        )

    def get_conversation(self, conversation_id: str) -> types.Conversation:
        """Gets a specific conversation by its ID or full resource name."""
        if conversation_id.startswith("projects/"):
            name = conversation_id
        else:
            name = f"{self.app_name}/conversations/{conversation_id}"

        request = types.GetConversationRequest(name=name)
        return self.client.get_conversation(request=request)

    def delete_conversation(self, conversation_id: str) -> None:
        request = types.DeleteConversationRequest(
            name=f"{self.app_name}/conversations/{conversation_id}"
        )
        self.client.delete_conversation(request=request)

    def export_conversation_to_yaml(self, conversation_id: str) -> str:
        """
        Fetches a specific conversation and exports it to the FDE YAML format.

        Args:
            conversation_id: Full resource name or ID of the conversation.

        Returns:
            A string containing the formatted YAML.
        """
        conv_obj = self.get_conversation(conversation_id=conversation_id)
        # Convert to dictionary
        conv_dict = type(conv_obj).to_dict(conv_obj)
        out_yaml_dict = ConversationHistory.conversation_dict_to_yaml(conv_dict)
        return yaml.dump(out_yaml_dict, sort_keys=False, allow_unicode=True)
