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

"""Drive a conversation with a Dialogflow CX (DFCX) agent and persist the
full trace (user turns, agent responses, tool calls, playbook/flow
invocations) as YAML.

This module talks to Dialogflow CX directly via the
`google-cloud-dialogflow-cx` SDK. It does not require dfcx-scrapi.
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import yaml
from google.cloud.dialogflowcx_v3beta1 import services as cx_services
from google.cloud.dialogflowcx_v3beta1 import types as cx_types
from google.protobuf.json_format import MessageToDict
from proto.marshal.collections import maps, repeated

from cxas_scrapi.core.common import Common
from cxas_scrapi.migration.dfcx_exporter import BaseDFCXClient

logger = logging.getLogger(__name__)

INLINE_TOOL_SENTINEL = "inline-action"


@dataclass
class ConversationTurn:
    """A single user/agent turn with its full trace."""

    turn: int
    user_query: str
    agent_responses: List[str] = field(default_factory=list)
    response_messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    playbook_invocations: List[Dict[str, Any]] = field(default_factory=list)
    flow_invocations: List[Dict[str, Any]] = field(default_factory=list)
    current_page: Optional[str] = None
    current_playbooks: List[str] = field(default_factory=list)
    intent: Optional[str] = None
    match_type: Optional[str] = None
    confidence: Optional[float] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationTrace:
    """All metadata + ordered turns for a single conversation run."""

    agent_id: str
    session_id: str
    language_code: str
    started_at: str
    turns: List[ConversationTurn] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "language_code": self.language_code,
            "started_at": self.started_at,
            "turns": [asdict(t) for t in self.turns],
        }


class DFCXConversationRunner(BaseDFCXClient):
    """Drives a conversation with a DFCX agent via google-cloud-dialogflow-cx.

    Each `send_message` call appends a fully traced `ConversationTurn` to
    the in-memory transcript, which can be exported with `save_to_yaml`.
    Inherits regional endpoint routing from `BaseDFCXClient` so behavior
    matches the rest of the migration package.
    """

    def __init__(
        self,
        agent_id: str,
        creds_path: Optional[str] = None,
        creds: Any = None,
        language_code: str = "en",
        session_id: Optional[str] = None,
        environment_id: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.language_code = language_code
        self.creds = Common(creds_path=creds_path, creds=creds).creds
        self._client_options = self._get_client_options(agent_id)

        # Lazy resource clients & display-name maps.
        self._sessions_client = None
        self._history_client = None
        self._tools_map: Optional[Dict[str, str]] = None
        self._playbooks_map: Optional[Dict[str, str]] = None
        self._flows_map: Optional[Dict[str, str]] = None

        self.session_id = session_id or self._build_session_id(
            agent_id, environment_id
        )

        self.trace = ConversationTrace(
            agent_id=agent_id,
            session_id=self.session_id,
            language_code=language_code,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def send_message(
        self,
        text: str,
        parameters: Optional[Dict[str, Any]] = None,
        end_user_metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationTurn:
        """Send a single user utterance and record the traced turn."""
        logger.info("Turn %d -> %s", len(self.trace.turns) + 1, text)

        request = self._build_detect_intent_request(
            text=text,
            parameters=parameters,
            end_user_metadata=end_user_metadata,
        )
        response = self._get_sessions_client().detect_intent(request=request)
        query_result = response.query_result

        turn = self._build_turn(text, query_result)
        self.trace.turns.append(turn)
        return turn

    def run_golden(self, utterances: Iterable[str]) -> ConversationTrace:
        """Replay a golden script of utterances against the agent and return
        the resulting trace.

        Each utterance is sent in order via `send_message`, so the produced
        trace can be diffed against a recorded baseline to detect regressions.
        """
        for utterance in utterances:
            self.send_message(utterance)
        return self.trace

    def list_conversations(self) -> List[Dict[str, Any]]:
        """List historical conversations stored for this agent.

        Returns a list of dicts: `{name, start_time, interaction_count}`.
        """
        client = self._get_history_client()
        request = cx_types.conversation_history.ListConversationsRequest(
            parent=self.agent_id
        )
        results = []
        for convo in client.list_conversations(request=request):
            start_time = getattr(convo, "start_time", None)
            results.append(
                {
                    "name": convo.name,
                    "start_time": start_time.isoformat()
                    if start_time
                    else None,
                    "interaction_count": len(
                        getattr(convo, "interactions", []) or []
                    ),
                }
            )
        return results

    def get_conversation(self, conversation_id: str) -> ConversationTrace:
        """Replace `self.trace` with a recorded conversation from history.

        The conversation's stored interactions are decoded through the same
        `_build_turn` logic used for live turns, so the YAML schema is
        identical regardless of source.

        Args:
          conversation_id: Full resource name
            `projects/.../agents/.../conversations/<id>`.
        """
        client = self._get_history_client()
        request = cx_types.conversation_history.GetConversationRequest(
            name=conversation_id
        )
        convo = client.get_conversation(request=request)

        start_time = getattr(convo, "start_time", None)
        started_at = (
            start_time.isoformat()
            if start_time
            else datetime.now(timezone.utc).isoformat()
        )

        # The conversation_id replaces session_id as the trace's source ref.
        self.session_id = convo.name
        self.trace = ConversationTrace(
            agent_id=self.agent_id,
            session_id=convo.name,
            language_code=self.language_code,
            started_at=started_at,
        )

        # Stored interactions are returned newest-first; reverse for
        # chronological replay.
        interactions = list(getattr(convo, "interactions", []) or [])
        for interaction in reversed(interactions):
            user_query = self._extract_user_input(
                getattr(interaction.request, "query_input", None)
            )
            query_result = getattr(interaction.response, "query_result", None)
            if query_result is None:
                continue
            turn = self._build_turn(user_query or "", query_result)
            self.trace.turns.append(turn)

        return self.trace

    @classmethod
    def from_conversation(
        cls,
        agent_id: str,
        conversation_id: str,
        creds_path: Optional[str] = None,
        creds: Any = None,
        language_code: str = "en",
    ) -> "DFCXConversationRunner":
        """Construct a runner pre-loaded with a recorded conversation."""
        runner = cls(
            agent_id=agent_id,
            creds_path=creds_path,
            creds=creds,
            language_code=language_code,
            session_id=conversation_id,  # avoid live-session UUID generation
        )
        runner.get_conversation(conversation_id)
        return runner

    @staticmethod
    def _extract_user_input(query_input) -> Optional[str]:
        """Pull the user-facing text out of a recorded QueryInput."""
        if query_input is None:
            return None
        text = getattr(query_input, "text", None)
        if text is not None and getattr(text, "text", ""):
            return text.text
        intent = getattr(query_input, "intent", None)
        if intent is not None and getattr(intent, "intent", ""):
            return f"<intent:{intent.intent}>"
        event = getattr(query_input, "event", None)
        if event is not None and getattr(event, "event", ""):
            return f"<event:{event.event}>"
        dtmf = getattr(query_input, "dtmf", None)
        if dtmf is not None and getattr(dtmf, "digits", ""):
            return f"<dtmf:{dtmf.digits}>"
        return None

    def save_to_yaml(self, output_path: str) -> str:
        """Serialize the conversation trace to YAML at output_path."""
        os.makedirs(
            os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True
        )
        content = yaml.dump(
            self.trace.to_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Saved conversation trace to %s", output_path)
        return content

    # ------------------------------------------------------------------ #
    # Session ID
    # ------------------------------------------------------------------ #

    def _build_session_id(
        self, agent_id: str, environment_id: Optional[str]
    ) -> str:
        sid = uuid.uuid4()
        if environment_id:
            return f"{agent_id}/environments/{environment_id}/sessions/{sid}"
        return f"{agent_id}/sessions/{sid}"

    # ------------------------------------------------------------------ #
    # Lazy CX clients
    # ------------------------------------------------------------------ #

    def _get_sessions_client(self):
        if self._sessions_client is None:
            self._sessions_client = cx_services.sessions.SessionsClient(
                credentials=self.creds, client_options=self._client_options
            )
        return self._sessions_client

    def _get_history_client(self):
        if self._history_client is None:
            self._history_client = (
                cx_services.conversation_history.ConversationHistoryClient(
                    credentials=self.creds,
                    client_options=self._client_options,
                )
            )
        return self._history_client

    def _build_detect_intent_request(
        self,
        text: str,
        parameters: Optional[Dict[str, Any]],
        end_user_metadata: Optional[Dict[str, Any]],
    ):
        text_input = cx_types.session.TextInput(text=text)
        query_input = cx_types.session.QueryInput(
            text=text_input, language_code=self.language_code
        )

        query_param_kwargs: Dict[str, Any] = {}
        if parameters:
            query_param_kwargs["parameters"] = parameters
        if end_user_metadata:
            query_param_kwargs["end_user_metadata"] = end_user_metadata

        request = cx_types.session.DetectIntentRequest(
            session=self.session_id,
            query_input=query_input,
        )
        if query_param_kwargs:
            request.query_params = cx_types.session.QueryParameters(
                **query_param_kwargs
            )
        return request

    # ------------------------------------------------------------------ #
    # Display-name maps (Tools / Playbooks / Flows)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_full_resource_name(value: str) -> bool:
        """True if `value` looks like projects/.../agents/.../<type>/<id>."""
        parts = value.split("/")
        return len(parts) >= 8 and parts[0] == "projects"

    def _get_tools_map(self) -> Dict[str, str]:
        if self._tools_map is not None:
            return self._tools_map
        client = cx_services.tools.ToolsClient(
            credentials=self.creds, client_options=self._client_options
        )
        self._tools_map = {
            t.name: t.display_name
            for t in client.list_tools(
                request=cx_types.ListToolsRequest(parent=self.agent_id)
            )
        }
        return self._tools_map

    def _get_playbooks_map(self) -> Dict[str, str]:
        if self._playbooks_map is not None:
            return self._playbooks_map
        client = cx_services.playbooks.PlaybooksClient(
            credentials=self.creds, client_options=self._client_options
        )
        self._playbooks_map = {
            pb.name: pb.display_name
            for pb in client.list_playbooks(
                request=cx_types.ListPlaybooksRequest(parent=self.agent_id)
            )
        }
        return self._playbooks_map

    def _get_flows_map(self) -> Dict[str, str]:
        if self._flows_map is not None:
            return self._flows_map
        client = cx_services.flows.FlowsClient(
            credentials=self.creds, client_options=self._client_options
        )
        self._flows_map = {
            f.name: f.display_name
            for f in client.list_flows(
                request=cx_types.ListFlowsRequest(parent=self.agent_id)
            )
        }
        return self._flows_map

    def _resolve_tool_name(self, tool_id: str) -> str:
        try:
            return self._get_tools_map().get(tool_id, tool_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to load tools map: %s", e)
            return tool_id

    def _resolve_playbook_name(self, playbook_id: str) -> str:
        try:
            return self._get_playbooks_map().get(playbook_id, playbook_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to load playbooks map: %s", e)
            return playbook_id

    def _resolve_flow_name(self, flow_id: str) -> str:
        try:
            return self._get_flows_map().get(flow_id, flow_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to load flows map: %s", e)
            return flow_id

    # ------------------------------------------------------------------ #
    # Trace extraction
    # ------------------------------------------------------------------ #

    def _build_turn(self, user_query: str, res) -> ConversationTurn:
        """Convert a DFCX QueryResult into a ConversationTurn."""
        turn = ConversationTurn(
            turn=len(self.trace.turns) + 1,
            user_query=user_query,
        )

        gen_info = getattr(res, "generative_info", None)
        if gen_info:
            actions = getattr(
                getattr(gen_info, "action_tracing_info", None), "actions", []
            )
            for action in actions:
                self._collect_action(action, turn)

            current_pbs = list(getattr(gen_info, "current_playbooks", []) or [])
            turn.current_playbooks = [
                self._resolve_playbook_name(pb_id) for pb_id in current_pbs
            ]

        # Fall back to flat response_messages when no generative trace.
        if not turn.agent_responses and getattr(res, "response_messages", None):
            for msg in res.response_messages:
                msg_dict = MessageToDict(msg._pb)  # noqa: SLF001
                turn.response_messages.append(msg_dict)
                text_block = msg_dict.get("text", {}).get("text")
                if text_block:
                    turn.agent_responses.extend(text_block)

        current_page = getattr(res, "current_page", None)
        if current_page is not None:
            turn.current_page = getattr(current_page, "display_name", None)

        intent = getattr(res, "intent", None)
        if intent is not None:
            turn.intent = getattr(intent, "display_name", None) or None

        match = getattr(res, "match", None)
        if match is not None:
            match_type = getattr(match, "match_type", None)
            if match_type is not None:
                turn.match_type = getattr(match_type, "_name_", str(match_type))
            turn.confidence = getattr(match, "confidence", None) or None

        params = getattr(res, "parameters", None)
        if params:
            turn.parameters = self._convert_parameters(params)

        return turn

    def _collect_action(self, action, turn: ConversationTurn) -> None:
        """Inspect a single action from action_tracing_info and route it."""
        agent_utterance = getattr(action, "agent_utterance", None)
        if agent_utterance and getattr(agent_utterance, "text", ""):
            turn.agent_responses.append(agent_utterance.text)

        tool_use = getattr(action, "tool_use", None)
        if tool_use and getattr(tool_use, "tool", ""):
            turn.tool_calls.append(self._build_tool_call(tool_use))

        playbook_invocation = getattr(action, "playbook_invocation", None)
        if playbook_invocation and getattr(playbook_invocation, "playbook", ""):
            turn.playbook_invocations.append(
                {
                    "playbook_id": playbook_invocation.playbook,
                    "playbook_name": self._resolve_playbook_name(
                        playbook_invocation.playbook
                    ),
                }
            )

        flow_invocation = getattr(action, "flow_invocation", None)
        if flow_invocation and getattr(flow_invocation, "flow", ""):
            turn.flow_invocations.append(
                {
                    "flow_id": flow_invocation.flow,
                    "flow_name": self._resolve_flow_name(flow_invocation.flow),
                }
            )

    def _build_tool_call(self, tool_use) -> Dict[str, Any]:
        """Normalize a tool_use action.

        Inline actions (no registered tool resource) are tagged
        `inline_action: True` and labeled with the INLINE_TOOL_SENTINEL.
        Registered tools resolve to their display name. The raw resource
        ID is always preserved under `tool_id`.
        """
        raw_tool_id = tool_use.tool
        is_inline = not self._is_full_resource_name(raw_tool_id)

        if is_inline:
            tool_name = INLINE_TOOL_SENTINEL
        else:
            tool_name = self._resolve_tool_name(raw_tool_id)

        return {
            "tool_id": raw_tool_id,
            "tool_name": tool_name,
            "tool_action": tool_use.action,
            "inline_action": is_inline,
            "input_params": self._extract_tool_params(
                tool_use.input_action_parameters
            ),
            "output_params": self._extract_tool_params(
                tool_use.output_action_parameters
            ),
        }

    def _extract_tool_params(self, params) -> Any:
        """Convert proto-marshal tool params to plain Python.

        DFCX wraps tool I/O under a single empty-string top-level key for
        Agent Builder tools; we unwrap that for readability while keeping
        the rest of the structure intact.
        """
        if params is None:
            return {}
        if isinstance(params, maps.MapComposite):
            param_map = self._recurse_marshal_to_dict(params)
        elif isinstance(params, dict):
            param_map = dict(params)
        else:
            try:
                param_map = json.loads(MessageToDict(params))
            except Exception:  # pylint: disable=broad-except
                return params

        empty_top_key = param_map.get("", None)
        if len(param_map) == 1 and empty_top_key is not None:
            return empty_top_key
        return param_map

    def _convert_parameters(self, params) -> Dict[str, Any]:
        """Recursively turn proto MapComposite/RepeatedComposite into plain
        Python types so they can be YAML-serialized cleanly."""
        out = {}
        for key in params:
            value = params[key]
            if isinstance(value, repeated.RepeatedComposite):
                value = self._recurse_repeated_composite(value)
            elif isinstance(value, maps.MapComposite):
                value = self._recurse_marshal_to_dict(value)
            out[key] = value
        return out

    def _recurse_marshal_to_dict(self, marshal_object) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in marshal_object.items():
            if isinstance(v, maps.MapComposite):
                converted = self._recurse_marshal_to_dict(v)
            elif isinstance(v, repeated.RepeatedComposite):
                converted = self._recurse_repeated_composite(v)
            else:
                converted = v
            out[k] = converted
        return out

    def _recurse_repeated_composite(self, repeated_object) -> List[Any]:
        out: List[Any] = []
        for item in repeated_object:
            if isinstance(item, maps.MapComposite):
                out.append(self._recurse_marshal_to_dict(item))
            elif isinstance(item, repeated.RepeatedComposite):
                out.append(self._recurse_repeated_composite(item))
            else:
                out.append(item)
        return out
