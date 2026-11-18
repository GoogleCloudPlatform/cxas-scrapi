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
import typing
from enum import Enum
from typing import Any

from google.protobuf import json_format
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

# Transfer directives that hand the session off to another agent with no
# paired end_session (self-terminating vendors, flows >= 0.42.0): the directive
# IS the teardown. Callers waiting on a bidi stream must treat one as the end of
# the session, or they block for an end_session that never arrives and time out.
SELF_TERMINATING_TRANSFER_KEYS = ("transferToNga", "transferToDialogflow")


def payload_ends_session(payload_val: Any) -> bool:
    """True if a custom payload carries a self-terminating transfer."""
    return isinstance(payload_val, dict) and any(
        key in payload_val for key in SELF_TERMINATING_TRANSFER_KEYS
    )


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
        reason: str | None = None,
        span_dict: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.type = type_name
        self.reason = reason
        self.span_dict = span_dict or {}

    def __repr__(self) -> typing.Any:
        return (
            f"ParsedGuardrailTrigger(name={self.name}, type={self.type}, "
            f"reason={self.reason})"
        )


class ToolSource(str, Enum):
    """Represents the source location of an extracted tool call."""

    TOP_LEVEL = "top_level"
    DIAGNOSTIC_INFO = "diagnostic_info"
    CALLBACK = "callback"


class ParsedToolCall:
    """Represents a tool call made by the agent."""

    def __init__(
        self, name: str, args: dict[str, Any], source: ToolSource
    ) -> None:
        self.name = name
        self.args = args
        self.source = source

    def __repr__(self) -> typing.Any:
        return (
            f"ParsedToolCall(name={self.name}, args={self.args}, "
            f"source={self.source})"
        )


class ParsedToolResponse:
    """Represents a tool response returned to the agent."""

    def __init__(self, name: str, response: Any) -> None:
        self.name = name
        self.response = response

    def __repr__(self) -> typing.Any:
        return f"ParsedToolResponse(name={self.name}, response={self.response})"


class ParsedSessionResponse:
    """Unified parser for RunSessionResponse / BidiSessionServerMessage /
    SessionOutput lists.
    """

    def __init__(
        self,
        response: Any,
        tools_map: dict[str, str] | None = None,
        include_callback_tools: bool = False,
    ) -> None:
        self.tools_map = tools_map or {}
        self.include_callback_tools = include_callback_tools
        self._raw_response = response
        self.outputs = []

        # Extract output list
        if hasattr(response, "outputs"):
            self.outputs = getattr(response, "outputs", []) or []
        elif isinstance(response, dict):
            if "outputs" in response:
                self.outputs = response["outputs"] or []
            elif "session_output" in response or "sessionOutput" in response:
                self.outputs = [
                    response.get("session_output")
                    or response.get("sessionOutput")
                ]
            else:
                self.outputs = [response]
        elif isinstance(response, list):
            self.outputs = response
        elif hasattr(response, "session_output"):  # BidiSessionServerMessage
            self.outputs = [response.session_output]
        else:
            self.outputs = [response]

        self.agent_texts: list[str] = []
        self.user_texts: list[str] = []
        self.tool_calls: list[ParsedToolCall] = []
        self.tool_responses: list[ParsedToolResponse] = []
        self.callback_tool_calls: list[ParsedToolCall] = []
        self.callback_tool_responses: list[ParsedToolResponse] = []
        self.agent_transfer: Any | None = None
        self.custom_payloads: list[dict[str, Any]] = []
        self.citations: list[dict[str, Any]] = []
        self.session_ended = False
        self.guardrail_trigger: ParsedGuardrailTrigger | None = None
        self.detailed_trace: list[str] = []
        self.root_spans: list[dict[str, Any]] = []
        self.root_span: dict[str, Any] | None = None

        from cxas_scrapi.core.sessions import (  # noqa: PLC0415
            BargeInMetadata,
        )

        meta = (
            getattr(response, "barge_in_metadata", None)
            if not isinstance(response, dict)
            else response.get("barge_in_metadata")
        )
        if isinstance(meta, BargeInMetadata):
            self.barge_in_metadata = meta
        else:
            self.barge_in_metadata = None
        self.interrupted = bool(
            getattr(self.barge_in_metadata, "interrupted", False)
        )
        self.vad_barge_in = False
        self.vad_skip_reason: str | None = None
        self.barge_in_context: str | None = None

        self._parse()

    @classmethod
    def extract_root_span(cls, response: Any) -> dict[str, Any] | None:
        """Extracts the primary root_span dictionary from a session response."""
        if response is None or type(response).__name__ == "MagicMock":
            return None
        try:
            return cls(response).root_span
        except Exception:
            return None

    def _resolve_tool_name(self, name: str) -> str:
        """Resolves tool name using tools_map if provided."""
        if name and "/tools/" in name:
            return self.tools_map.get(name, name)
        return name

    def _search_span_dict(
        self, span_dict: dict[str, Any]
    ) -> dict[str, Any] | None:
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

    def _search_vad_spans(self, span_dict: dict[str, Any]) -> None:
        """Recursively inspects spans for VAD barge-in signals."""
        if not isinstance(span_dict, dict):
            return

        name = span_dict.get("name", "")
        attrs = span_dict.get("attributes", {})
        if name == "VAD":
            barge_in_attr = attrs.get("barge-in")
            if barge_in_attr is True or str(barge_in_attr).lower() == "true":
                self.vad_barge_in = True
            if "skip" in attrs:
                self.vad_skip_reason = str(attrs["skip"])

        child_spans = span_dict.get(
            "childSpans", span_dict.get("child_spans", [])
        )
        for child in child_spans:
            self._search_vad_spans(child)

    def _parse_guardrails(self, diagnostic_info: Any) -> None:
        """Extracts root_span and guardrail triggers from diagnostic_info."""
        if diagnostic_info is None:
            return
        root_span = getattr(diagnostic_info, "root_span", None)
        if root_span is None and isinstance(diagnostic_info, dict):
            root_span = diagnostic_info.get(
                "root_span", diagnostic_info.get("rootSpan")
            )
        if root_span:
            try:
                if isinstance(root_span, dict):
                    span_dict = dict(root_span)
                elif hasattr(root_span, "_pb"):
                    span_dict = MessageToDict(root_span._pb)
                else:
                    span_dict = MessageToDict(root_span)
            except Exception:
                span_dict = (
                    dict(root_span) if isinstance(root_span, dict) else {}
                )

            if span_dict and isinstance(span_dict, dict):
                self.root_spans.append(span_dict)
                self._search_vad_spans(span_dict)
                if self.root_span is None or len(
                    span_dict.get(
                        "childSpans", span_dict.get("child_spans", [])
                    )
                    or []
                ) >= len(
                    self.root_span.get(
                        "childSpans", self.root_span.get("child_spans", [])
                    )
                    or []
                ):
                    self.root_span = span_dict

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

    def _extract_callback_tool_spans(
        self,
        span_dict: dict[str, Any],
        existing_llm_tools: set[str],
    ) -> None:
        """Extracts Callback-invoked Tool and sibling Fake Tool spans."""
        if not isinstance(span_dict, dict):
            return

        def _record_span(tool_span: dict[str, Any], span_kind: str) -> None:
            attrs = tool_span.get("attributes") or {}
            if not isinstance(attrs, dict):
                return
            raw_name = (
                attrs.get("display_name")
                or attrs.get("displayName")
                or attrs.get("tool")
                or attrs.get("name")
                or ""
            )
            if not raw_name:
                for grandchild in (
                    tool_span.get("childSpans")
                    or tool_span.get("child_spans")
                    or []
                ):
                    if isinstance(grandchild, dict) and str(
                        grandchild.get("name", "")
                    ).startswith("Tool: "):
                        raw_name = str(grandchild["name"])[
                            len("Tool: ") :
                        ].strip()
                        break
            tool_name = self._resolve_tool_name(str(raw_name))
            if not tool_name or tool_name in ("Tool", "Fake Tool"):
                return
            if span_kind == "Fake Tool" and tool_name in existing_llm_tools:
                return
            raw_in = (
                attrs.get("args")
                if "args" in attrs
                else (
                    attrs.get("input")
                    if "input" in attrs
                    else attrs.get("parameters")
                )
            )
            args = expand_pb_struct(raw_in) if raw_in is not None else {}
            if not isinstance(args, dict):
                args = {} if args in (None, "") else {"value": args}
            parsed_call = ParsedToolCall(
                name=tool_name,
                args=args,
                source=ToolSource.CALLBACK,
            )
            self.callback_tool_calls.append(parsed_call)
            if self.include_callback_tools:
                self.tool_calls.append(parsed_call)
            for out_key in ("response", "output", "result"):
                if out_key in attrs and attrs[out_key] is not None:
                    resp_val = expand_pb_struct(attrs[out_key])
                    parsed_resp = ParsedToolResponse(
                        name=tool_name, response=resp_val
                    )
                    self.callback_tool_responses.append(parsed_resp)
                    if self.include_callback_tools:
                        self.tool_responses.append(parsed_resp)
                    break

        def _walk(node: dict[str, Any]) -> None:
            children = node.get("childSpans") or node.get("child_spans") or []
            if not isinstance(children, list):
                return
            in_after_callback = False
            for child in children:
                if not isinstance(child, dict):
                    continue
                cname = str(child.get("name") or "")
                if cname == "Callback":
                    in_after_callback = True
                    for grandchild in (
                        child.get("childSpans")
                        or child.get("child_spans")
                        or []
                    ):
                        if not isinstance(grandchild, dict):
                            continue
                        gname = str(grandchild.get("name") or "")
                        if gname in ("Tool", "Fake Tool"):
                            _record_span(grandchild, gname)
                elif cname == "Fake Tool" and in_after_callback:
                    _record_span(child, "Fake Tool")
                elif cname != "Guardrail":
                    in_after_callback = False
                    _walk(child)

        _walk(span_dict)

    def _parse(self) -> None:
        top_dim = getattr(self._raw_response, "diagnostic_info", None)
        if top_dim is not None and self._raw_response not in self.outputs:
            self._parse_guardrails(top_dim)
        top_level_agent_text_found = False
        for output in self.outputs:
            if output is None:
                continue

            # Top-level text
            text_val = (
                output.get("text")
                if isinstance(output, dict)
                else getattr(output, "text", None)
            )
            if text_val and isinstance(text_val, str):
                self.agent_texts.append(text_val)
                self.detailed_trace.append(f"Agent Text: {text_val}")
                top_level_agent_text_found = True

            # Top-level session ended flag
            end_sess = (
                output.get("end_session") or output.get("endSession")
                if isinstance(output, dict)
                else getattr(output, "end_session", None)
            )
            if end_sess is not None:
                if hasattr(output, "_pb"):
                    if output._pb.WhichOneof("output_type") == "end_session":
                        self.session_ended = True
                else:
                    self.session_ended = True

            # Top-level Citations
            cit_msg = (
                output.get("citations")
                if isinstance(output, dict)
                else getattr(output, "citations", None)
            )
            if cit_msg:
                cited_chunks = (
                    cit_msg.get("cited_chunks", cit_msg.get("citedChunks", []))
                    if isinstance(cit_msg, dict)
                    else getattr(cit_msg, "cited_chunks", [])
                )
                for chunk in cited_chunks:
                    self.citations.append(
                        {
                            "uri": (
                                chunk.get("uri", "")
                                if isinstance(chunk, dict)
                                else getattr(chunk, "uri", "")
                            ),
                            "title": (
                                chunk.get("title", "")
                                if isinstance(chunk, dict)
                                else getattr(chunk, "title", "")
                            ),
                            "text": (
                                chunk.get("text", "")
                                if isinstance(chunk, dict)
                                else getattr(chunk, "text", "")
                            ),
                        }
                    )

            # Top-level custom payload Struct
            payload_msg = (
                output.get("payload")
                if isinstance(output, dict)
                else getattr(output, "payload", None)
            )
            if payload_msg:
                payload_val = (
                    payload_msg
                    if isinstance(payload_msg, dict)
                    else expand_pb_struct(payload_msg)
                )
                self.custom_payloads.append(payload_val)
                self.detailed_trace.append(
                    f"Custom Payload (Output): {payload_val}"
                )
                if payload_ends_session(payload_val):
                    self.session_ended = True

            # Top-level tool calls
            tc_msg = (
                output.get("tool_calls") or output.get("toolCalls")
                if isinstance(output, dict)
                else getattr(output, "tool_calls", None)
            )
            if tc_msg:
                tc_list = (
                    tc_msg.get("tool_calls", tc_msg.get("toolCalls", []))
                    if isinstance(tc_msg, dict)
                    else getattr(tc_msg, "tool_calls", [])
                )
                for tc in tc_list:
                    tool_name = (
                        (
                            tc.get("tool")
                            or tc.get("display_name")
                            or tc.get("displayName")
                            or ""
                        )
                        if isinstance(tc, dict)
                        else (
                            getattr(tc, "tool", "")
                            or getattr(tc, "display_name", "")
                        )
                    )
                    tool_name = self._resolve_tool_name(tool_name)
                    if isinstance(tc, dict):
                        args = tc.get("args", {})
                    else:
                        args = (
                            expand_pb_struct(tc.args)
                            if hasattr(tc, "args")
                            else {}
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
            if diagnostic_info is None and isinstance(output, dict):
                diagnostic_info = output.get("diagnostic_info") or output.get(
                    "diagnosticInfo"
                )
            if diagnostic_info:
                # Parse guardrails
                self._parse_guardrails(diagnostic_info)

                # Parse messages
                messages = (
                    diagnostic_info.get("messages", [])
                    if isinstance(diagnostic_info, dict)
                    else getattr(diagnostic_info, "messages", []) or []
                )
                for message in messages:
                    role = (
                        message.get("role", "")
                        if isinstance(message, dict)
                        else getattr(message, "role", "")
                    )
                    chunks = (
                        message.get("chunks", [])
                        if isinstance(message, dict)
                        else getattr(message, "chunks", []) or []
                    )

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
                                val = (
                                    chunk.get(attr)
                                    if isinstance(chunk, dict)
                                    else getattr(chunk, attr, None)
                                )
                                if val is not None:
                                    chunk_type = attr
                                    break

                        # Process each type of chunk matching the active type
                        if chunk_type in ("text", "transcript"):
                            text_val = (
                                chunk.get("text") or chunk.get("transcript")
                                if isinstance(chunk, dict)
                                else (
                                    getattr(chunk, "text", None)
                                    or getattr(chunk, "transcript", None)
                                )
                            )
                            if text_val and isinstance(text_val, str):
                                if role.lower() == "user":
                                    self.user_texts.append(text_val)
                                    self.detailed_trace.append(
                                        f"User Query: {text_val}"
                                    )
                                    if (
                                        "<context>agent speaking was"
                                        " interrupted" in text_val.lower()
                                    ):
                                        self.barge_in_context = text_val
                                        self.detailed_trace.append(
                                            f"Barge-In Context: {text_val}"
                                        )
                                elif not top_level_agent_text_found:
                                    self.agent_texts.append(text_val)
                                    self.detailed_trace.append(
                                        f"Agent Text (Diag): {text_val}"
                                    )

                        elif chunk_type in ("tool_call", "function_call"):
                            tc = (
                                (
                                    chunk.get("tool_call")
                                    or chunk.get("toolCall")
                                    or chunk.get("function_call")
                                    or chunk.get("functionCall")
                                )
                                if isinstance(chunk, dict)
                                else (
                                    getattr(chunk, "tool_call", None)
                                    or getattr(chunk, "function_call", None)
                                )
                            )
                            if tc:
                                tool_name = (
                                    (
                                        tc.get("display_name")
                                        or tc.get("displayName")
                                        or tc.get("tool")
                                        or tc.get("name")
                                        or ""
                                    )
                                    if isinstance(tc, dict)
                                    else (
                                        getattr(tc, "display_name", "")
                                        or getattr(tc, "tool", "")
                                        or getattr(tc, "name", "")
                                    )
                                )
                                tool_name = self._resolve_tool_name(tool_name)
                                if isinstance(tc, dict):
                                    args = tc.get("args", {})
                                else:
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
                            tr = (
                                (
                                    chunk.get("tool_response")
                                    or chunk.get("toolResponse")
                                    or chunk.get("function_response")
                                    or chunk.get("functionResponse")
                                )
                                if isinstance(chunk, dict)
                                else (
                                    getattr(chunk, "tool_response", None)
                                    or getattr(chunk, "function_response", None)
                                )
                            )
                            if tr:
                                tool_name = (
                                    (
                                        tr.get("display_name")
                                        or tr.get("displayName")
                                        or tr.get("tool")
                                        or tr.get("name")
                                        or ""
                                    )
                                    if isinstance(tr, dict)
                                    else (
                                        getattr(tr, "display_name", "")
                                        or getattr(tr, "tool", "")
                                        or getattr(tr, "name", "")
                                    )
                                )
                                tool_name = self._resolve_tool_name(tool_name)
                                if isinstance(tr, dict):
                                    response_val = tr.get("response")
                                else:
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
                            at = (
                                chunk.get("agent_transfer")
                                or chunk.get("agentTransfer")
                                if isinstance(chunk, dict)
                                else getattr(chunk, "agent_transfer", None)
                            )
                            if at:
                                self.agent_transfer = at
                                display_name = (
                                    at.get("display_name")
                                    or at.get("displayName", "unknown")
                                    if isinstance(at, dict)
                                    else getattr(at, "display_name", "unknown")
                                )
                                self.detailed_trace.append(
                                    "Agent Transfer: Transferred to "
                                    f"{display_name}"
                                )

                        elif chunk_type == "payload":
                            p = (
                                chunk.get("payload")
                                if isinstance(chunk, dict)
                                else getattr(chunk, "payload", None)
                            )
                            if p:
                                payload_val = (
                                    p
                                    if isinstance(p, dict)
                                    else expand_pb_struct(p)
                                )
                                self.custom_payloads.append(payload_val)
                                self.detailed_trace.append(
                                    f"Custom Payload: {payload_val}"
                                )
                                if payload_ends_session(payload_val):
                                    self.session_ended = True

                    # Also check action transfer at the message level
                    actions = (
                        message.get("actions")
                        if isinstance(message, dict)
                        else getattr(message, "actions", None)
                    )
                    if actions:
                        transfer = (
                            actions.get("transfer_to_agent")
                            or actions.get("transferToAgent")
                            if isinstance(actions, dict)
                            else (
                                getattr(actions, "transfer_to_agent", None)
                                if hasattr(actions, "transfer_to_agent")
                                else None
                            )
                        )
                        if transfer:
                            self.agent_transfer = transfer

        existing_llm_tools = {tc.name for tc in self.tool_calls}
        for span_dict in self.root_spans:
            self._extract_callback_tool_spans(span_dict, existing_llm_tools)

        # Cleanup agent texts
        self.consolidated_agent_text = " ".join(self.agent_texts).strip()
        self.consolidated_user_text = " ".join(self.user_texts).strip()

        if (
            self.barge_in_metadata
            and type(self.barge_in_metadata).__name__ != "MagicMock"
            and getattr(self.barge_in_metadata, "barge_in_attempted", False)
        ):
            played_ms = getattr(
                self.barge_in_metadata, "played_audio_duration_ms", 0.0
            )
            total_ms = getattr(
                self.barge_in_metadata, "total_audio_duration_ms", 0.0
            )
            played_str = (
                f"{played_ms:.0f}"
                if isinstance(played_ms, (int, float))
                else str(played_ms)
            )
            total_str = (
                f"{total_ms:.0f}"
                if isinstance(total_ms, (int, float))
                else str(total_ms)
            )
            self.detailed_trace.append(
                f"Barge-In Signal: interrupted="
                f"{self.barge_in_metadata.interrupted}, "
                f"played_ms={played_str}, "
                f"total_ms={total_str}"
            )
        if self.vad_barge_in:
            self.detailed_trace.append(f"VAD Barge-In: {self.vad_barge_in}")
        if self.vad_skip_reason:
            self.detailed_trace.append(
                f"VAD Skip Reason: {self.vad_skip_reason}"
            )
