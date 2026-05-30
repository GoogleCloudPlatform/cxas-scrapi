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

import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from google.protobuf import json_format
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)


def expand_pb_struct(pb_struct: Any) -> Any:
    """Helper to recursively convert protobuf Struct/Map/Message to standard
    Python dicts/lists.
    """
    try:
        return json.loads(json_format.MessageToJson(pb_struct))
    except Exception:
        pass

    if hasattr(pb_struct, "items"):
        res = {}
        for k, v in pb_struct.items():
            res[k] = expand_pb_struct(v)
        return res
    elif hasattr(pb_struct, "__iter__") and not isinstance(pb_struct, str):
        return [expand_pb_struct(item) for item in pb_struct]
    else:
        return pb_struct


class ParsedGuardrailTrigger:
    """Represents a triggered guardrail."""

    def __init__(
        self,
        name: str,
        type_name: str,
        reason: Optional[str] = None,
        span_dict: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.type = type_name
        self.reason = reason
        self.span_dict = span_dict or {}

    def __repr__(self):
        return (
            f"ParsedGuardrailTrigger(name={self.name}, type={self.type}, "
            f"reason={self.reason})"
        )


class ToolSource(str, Enum):
    """Represents the source location of an extracted tool call."""

    TOP_LEVEL = "top_level"
    DIAGNOSTIC_INFO = "diagnostic_info"


class ParsedToolCall:
    """Represents a tool call made by the agent."""

    def __init__(self, name: str, args: Dict[str, Any], source: ToolSource):
        self.name = name
        self.args = args
        self.source = source

    def __repr__(self):
        return (
            f"ParsedToolCall(name={self.name}, args={self.args}, "
            f"source={self.source})"
        )


class ParsedToolResponse:
    """Represents a tool response returned to the agent."""

    def __init__(self, name: str, response: Any):
        self.name = name
        self.response = response

    def __repr__(self):
        return f"ParsedToolResponse(name={self.name}, response={self.response})"


class ParsedSessionResponse:
    """Unified parser for RunSessionResponse / BidiSessionServerMessage /
    SessionOutput lists.
    """

    def __init__(
        self, response: Any, tools_map: Optional[Dict[str, str]] = None
    ):
        self.tools_map = tools_map or {}
        self.outputs = []

        # Extract output list
        if hasattr(response, "outputs"):
            self.outputs = getattr(response, "outputs", []) or []
        elif isinstance(response, list):
            self.outputs = response
        elif hasattr(response, "session_output"):  # BidiSessionServerMessage
            self.outputs = [response.session_output]
        else:
            self.outputs = [response]

        self.agent_texts: List[str] = []
        self.user_texts: List[str] = []
        self.tool_calls: List[ParsedToolCall] = []
        self.tool_responses: List[ParsedToolResponse] = []
        self.agent_transfer: Optional[Any] = None
        self.custom_payloads: List[Dict[str, Any]] = []
        self.session_ended = False
        self.guardrail_trigger: Optional[ParsedGuardrailTrigger] = None
        self.detailed_trace: List[str] = []

        self._parse()

    def _resolve_tool_name(self, name: str) -> str:
        """Resolves tool name using tools_map if provided."""
        if name and "/tools/" in name:
            return self.tools_map.get(name, name)
        return name

    def _search_span_dict(
        self, span_dict: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Recursively searches a trace span dictionary for a guardrail
        trigger.
        """
        if not isinstance(span_dict, dict):
            return None

        attrs = span_dict.get("attributes", {})
        if "name" in attrs and any(
            k in attrs for k in ("type", "guardrailType", "guardrail_type")
        ):
            return span_dict

        child_spans = span_dict.get(
            "childSpans", span_dict.get("child_spans", [])
        )
        for child in child_spans:
            res = self._search_span_dict(child)
            if res:
                return res
        return None

    def _parse_guardrails(self, diagnostic_info: Any):
        """Extracts guardrail triggers from diagnostic_info.root_span."""
        root_span = getattr(diagnostic_info, "root_span", None)
        if root_span:
            try:
                span_dict = (
                    MessageToDict(root_span._pb)
                    if hasattr(root_span, "_pb")
                    else MessageToDict(root_span)
                )
            except Exception:
                span_dict = (
                    dict(root_span) if isinstance(root_span, dict) else {}
                )

            triggered_span = self._search_span_dict(span_dict)
            if triggered_span:
                attrs = triggered_span.get("attributes", {})
                g_name = attrs.get("name")
                g_type = attrs.get(
                    "type",
                    attrs.get("guardrailType", attrs.get("guardrail_type")),
                )
                g_reason = attrs.get("reason")
                self.guardrail_trigger = ParsedGuardrailTrigger(
                    name=g_name,
                    type_name=g_type,
                    reason=g_reason,
                    span_dict=triggered_span,
                )

    def _parse(self):
        for output in self.outputs:
            if output is None:
                continue

            # Top-level text
            text_val = getattr(output, "text", None)
            if text_val and isinstance(text_val, str):
                self.agent_texts.append(text_val)
                self.detailed_trace.append(f"Agent Text: {text_val}")

            # Top-level session ended flag
            end_sess = getattr(output, "end_session", None)
            if end_sess is not None:
                if hasattr(output, "_pb"):
                    if output._pb.WhichOneof("output_type") == "end_session":
                        self.session_ended = True
                else:
                    self.session_ended = True

            # Top-level tool calls
            tc_msg = getattr(output, "tool_calls", None)
            if tc_msg and hasattr(tc_msg, "tool_calls"):
                for tc in tc_msg.tool_calls:
                    tool_name = getattr(tc, "tool", "") or getattr(
                        tc, "display_name", ""
                    )
                    tool_name = self._resolve_tool_name(tool_name)
                    args = (
                        expand_pb_struct(tc.args) if hasattr(tc, "args") else {}
                    )
                    self.tool_calls.append(
                        ParsedToolCall(
                            name=tool_name,
                            args=args,
                            source=ToolSource.TOP_LEVEL,
                        )
                    )
                    self.detailed_trace.append(
                        f"Tool Call (Output): {tool_name} with args {args}"
                    )
                    if "end_session" in tool_name:
                        self.session_ended = True

            # Diagnostic Info (turn-by-turn trace chunks)
            diagnostic_info = getattr(output, "diagnostic_info", None)
            if diagnostic_info:
                # Parse guardrails
                self._parse_guardrails(diagnostic_info)

                # Parse messages
                messages = getattr(diagnostic_info, "messages", []) or []
                for message in messages:
                    role = getattr(message, "role", "")
                    chunks = getattr(message, "chunks", []) or []

                    for chunk in chunks:
                        # Detect chunk type
                        chunk_type = None
                        if hasattr(chunk, "_pb"):
                            chunk_type = chunk._pb.WhichOneof("data")

                        if not chunk_type:
                            # Fallback for custom mock chunks or dicts
                            for attr in [
                                "text",
                                "transcript",
                                "tool_call",
                                "function_call",
                                "tool_response",
                                "function_response",
                                "agent_transfer",
                                "payload",
                            ]:
                                if getattr(chunk, attr, None) is not None:
                                    chunk_type = attr
                                    break

                        # Process each type of chunk matching the active type
                        if chunk_type in ("text", "transcript"):
                            text_val = getattr(chunk, "text", None) or getattr(
                                chunk, "transcript", None
                            )
                            if text_val and isinstance(text_val, str):
                                if role.lower() == "user":
                                    self.user_texts.append(text_val)
                                    self.detailed_trace.append(
                                        f"User Query: {text_val}"
                                    )
                                else:
                                    self.agent_texts.append(text_val)
                                    self.detailed_trace.append(
                                        f"Agent Text (Diag): {text_val}"
                                    )

                        elif chunk_type in ("tool_call", "function_call"):
                            tc = getattr(chunk, "tool_call", None) or getattr(
                                chunk, "function_call", None
                            )
                            if tc:
                                tool_name = (
                                    getattr(tc, "display_name", "")
                                    or getattr(tc, "tool", "")
                                    or getattr(tc, "name", "")
                                )
                                tool_name = self._resolve_tool_name(tool_name)
                                args = (
                                    expand_pb_struct(tc.args)
                                    if hasattr(tc, "args")
                                    else {}
                                )
                                self.tool_calls.append(
                                    ParsedToolCall(
                                        name=tool_name,
                                        args=args,
                                        source=ToolSource.DIAGNOSTIC_INFO,
                                    )
                                )
                                self.detailed_trace.append(
                                    f"Tool Call: {tool_name} with args {args}"
                                )
                                if "end_session" in tool_name:
                                    self.session_ended = True

                        elif chunk_type in (
                            "tool_response",
                            "function_response",
                        ):
                            tr = getattr(
                                chunk, "tool_response", None
                            ) or getattr(chunk, "function_response", None)
                            if tr:
                                tool_name = (
                                    getattr(tr, "display_name", "")
                                    or getattr(tr, "tool", "")
                                    or getattr(tr, "name", "")
                                )
                                tool_name = self._resolve_tool_name(tool_name)
                                response_val = getattr(tr, "response", None)
                                if response_val is not None:
                                    response_val = expand_pb_struct(
                                        response_val
                                    )
                                self.tool_responses.append(
                                    ParsedToolResponse(
                                        name=tool_name, response=response_val
                                    )
                                )
                                self.detailed_trace.append(
                                    f"Tool Response: {tool_name} with result "
                                    f"{response_val}"
                                )

                        elif chunk_type == "agent_transfer":
                            at = getattr(chunk, "agent_transfer", None)
                            if at:
                                self.agent_transfer = at
                                display_name = getattr(
                                    at, "display_name", "unknown"
                                )
                                self.detailed_trace.append(
                                    "Agent Transfer: Transferred to "
                                    f"{display_name}"
                                )

                        elif chunk_type == "payload":
                            p = getattr(chunk, "payload", None)
                            if p:
                                payload_val = expand_pb_struct(p)
                                self.custom_payloads.append(payload_val)
                                self.detailed_trace.append(
                                    f"Custom Payload: {payload_val}"
                                )

                    # Also check action transfer at the message level
                    actions = getattr(message, "actions", None)
                    if (
                        actions
                        and hasattr(actions, "transfer_to_agent")
                        and actions.transfer_to_agent
                    ):
                        self.agent_transfer = actions.transfer_to_agent

        # Cleanup agent texts
        self.consolidated_agent_text = " ".join(self.agent_texts).strip()
        self.consolidated_user_text = " ".join(self.user_texts).strip()
