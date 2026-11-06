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

"""Normalizes a SCRAPI simulation result into a `conversation.v1` run trace.

SCRAPI's `SimulationEvals` returns, per run, `detailed_trace` (one string per
user turn and one multi-line string per agent turn, rendered by
`cxas_scrapi.core.response_parser.ParsedSessionResponse`) and `turn_traces`
(per-turn diagnostic root spans). This module turns them into a structured
`conversation.v1` event list:

  user_message, agent_message, tool_call, tool_result, handoff, payload

Why convert SCRAPI's output into `conversation.v1`:
1. Recover callback-invoked and fake tools omitted from `detailed_trace`:
   callback-invoked tools (for example lookups executed inside a `before_agent`
   callback) never appear as chunks in `detailed_trace`; they are recovered from
   `Tool` and `Fake Tool` spans under or following `Callback` spans in
   `turn_traces` and emitted with `via: "callback"` before the turn's other
   events.
2. Unify simulated and production traces under one schema: `conversation.v1` is
   shared between simulated SCRAPI runs and production BigQuery
   `conversation_logs`, allowing trace contract checks, completion rubrics, and
   sim-vs-real calibration to run identically on both without separate parsers.
"""

import ast
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

FORMAT = "conversation.v1"
SOURCE = "fde_eval_runner"
DEFAULT_ESCALATION_TOOLS = frozenset({"escalate_to_agent", "escalate"})
_MAX_SESSION_CHANGES = 40

_LINE_PATTERNS = (
    ("agent_text", re.compile(r"^Agent Text: (.*)$", re.S)),
    ("agent_text_diag", re.compile(r"^Agent Text \(Diag\): (.*)$", re.S)),
    ("user_query", re.compile(r"^User Query: (.*)$", re.S)),
    (
        "tool_call",
        re.compile(r"^Tool Call(?: \(Output\))?: (\S+) with args (.*)$", re.S),
    ),
    (
        "tool_response",
        re.compile(r"^Tool Response: (\S+) with result (.*)$", re.S),
    ),
    ("transfer", re.compile(r"^Agent Transfer: Transferred to (.*)$", re.S)),
    ("payload", re.compile(r"^Custom Payload(?: \(Output\))?: (.*)$", re.S)),
    ("barge_in", re.compile(r"^Barge-In Context: (.*)$", re.S)),
)
_PATTERNS = dict(_LINE_PATTERNS)
_TOOL_SPANS = ("Tool", "Fake Tool")
_FAKE_TOOL_SPAN = "Fake Tool"
_USER_PREFIX = "User: "
_BARGE_IN_PREFIX = "[Barge-In] "


def as_obj(value: Any) -> Any:
    """Parses JSON-encoded strings (up to two levels); returns others as is."""
    for _ in range(2):
        if not isinstance(value, str) or value.strip()[:1] not in ("{", "["):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


def truthy(value: Any) -> bool:
    """Interprets common string/bool/numeric flags as booleans."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _is_escalation_tool(tool: str, args: Mapping[str, Any]) -> bool:
    """Returns True when a standard CES tool call signals human escalation."""
    if tool in DEFAULT_ESCALATION_TOOLS:
        return True
    return tool == "end_session" and truthy(args.get("session_escalated"))


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(value, list):
        if value and all(not isinstance(i, (Mapping, list)) for i in value):
            out[prefix or "result"] = value
        elif value:
            _flatten(value[0], prefix + "[0]", out)
    elif value is not None and not (isinstance(value, str) and not value):
        out[prefix or "result"] = value


def normalize_response(response: Any, failed: bool) -> dict[str, Any]:
    """Maps a CES tool response to `{status_code, session_changes, output_keys}`."""
    body = as_obj(response)
    if isinstance(body, Mapping) and set(body) == {"result"}:
        body = as_obj(body["result"])
    flat: dict[str, Any] = {}
    _flatten(
        body if isinstance(body, (Mapping, list)) else {"result": body},
        "",
        flat,
    )
    items = list(flat.items())[:_MAX_SESSION_CHANGES]
    return {
        "status_code": 1 if failed else 0,
        "session_changes": dict(items),
        "output_keys": [k for k, _ in items],
    }


def parse_value(text: str) -> Any:
    """Parses a rendered dict / list (Python repr or JSON); else the string."""
    text = text.strip()
    for parser in (ast.literal_eval, json.loads):
        try:
            return parser(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    return text


def _short_name(name: Any) -> str:
    return str(name or "").rsplit("/", 1)[-1]


def _has_error(mapping: Mapping[str, Any]) -> bool:
    return bool(
        mapping.get("error")
        or mapping.get("errorMessage")
        or str(mapping.get("status") or "").strip().lower() == "error"
    )


def tool_failed(response: Any) -> bool:
    """True for error-shaped tool responses (CES `{"result": ...}` or bare)."""
    body = as_obj(response)
    if not isinstance(body, Mapping):
        return False
    if _has_error(body):
        return True
    result = as_obj(body.get("result"))
    return isinstance(result, Mapping) and _has_error(result)


def callback_tools(
    root_span: Any,
    tools_map: Mapping[str, str] | None = None,
) -> list[tuple[str, str, Any, Any, str]]:
    """(agent, tool, args, response, span_kind) for callback-invoked tools.

    Two shapes are recognized: `Tool` spans nested under a `Callback` span (real
    tools and the BigQuery export), and `Fake Tool` spans (fake mode) that CES
    emits as the next siblings of the `Callback` span that invoked them. Accepts
    the API's `MessageToDict` spans (`childSpans`) and the BigQuery export shape
    (`child_spans`, possibly JSON-encoded).

    Args:
      root_span: One turn's root span.
      tools_map: Optional resource-name to display-name map.

    Returns:
      Callback-invoked tool calls in span order, with span kind (`Tool` or
      `Fake Tool`).
    """
    out: list[tuple[str, str, Any, Any, str]] = []

    def attrs_of(span: Mapping[str, Any]) -> Mapping[str, Any]:
        attrs = span.get("attributes") or {}
        return attrs if isinstance(attrs, Mapping) else {}

    def children(span: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        kids = as_obj(span.get("childSpans") or span.get("child_spans") or [])
        if not isinstance(kids, list):
            return []
        kids = [as_obj(k) for k in kids]
        return [k for k in kids if isinstance(k, Mapping)]

    def add(agent: str, span: Mapping[str, Any], kind: str) -> None:
        attrs = attrs_of(span)
        raw_name = (
            attrs.get("display_name")
            or attrs.get("displayName")
            or attrs.get("tool")
            or attrs.get("name")
            or ""
        )
        if not raw_name:
            for grandchild in children(span):
                g_label = str(grandchild.get("name") or "")
                if g_label.startswith("Tool: "):
                    raw_name = g_label[len("Tool: ") :].strip()
                    break
        raw_str = str(raw_name or "")
        if tools_map and "/tools/" in raw_str and raw_str in tools_map:
            tool = str(tools_map[raw_str])
        else:
            tool = _short_name(raw_str)
        if not tool or tool in _TOOL_SPANS:
            return
        if "args" in attrs:
            raw_in = attrs.get("args")
        elif "input" in attrs:
            raw_in = attrs.get("input")
        else:
            raw_in = attrs.get("parameters")
        raw_out = None
        for out_key in ("response", "output", "result"):
            if out_key in attrs and attrs[out_key] is not None:
                raw_out = attrs[out_key]
                break
        out.append((
            agent,
            tool,
            as_obj(raw_in),
            as_obj(raw_out),
            kind,
        ))

    def walk(span: Mapping[str, Any]) -> None:
        last_callback: str | None = None
        for child in children(span):
            name = str(child.get("name") or "")
            if name == "Callback":
                attrs = attrs_of(child)
                agent = str(attrs.get("agent") or attrs.get("agent_name") or "")
                last_callback = agent or None
                for grandchild in children(child):
                    gname = str(grandchild.get("name") or "")
                    if agent and gname in _TOOL_SPANS:
                        add(agent, grandchild, gname)
            elif name == _FAKE_TOOL_SPAN and last_callback:
                add(last_callback, child, name)
            elif name != "Guardrail":
                last_callback = None
                walk(child)

    root = as_obj(root_span)
    for r in root if isinstance(root, list) else [root]:
        r = as_obj(r)
        if isinstance(r, Mapping):
            walk(r)
    return out


def _lines(entry: str) -> Iterable[tuple[str, str]]:
    """(kind, text) per rendered line; continuation lines join the previous."""
    current: list[str] | None = None
    kind = ""
    for line in entry.split("\n"):
        match = None
        for k, pattern in _LINE_PATTERNS:
            match = pattern.match(line)
            if match:
                if current is not None:
                    yield kind, "\n".join(current)
                kind, current = k, [line]
                break
        if not match and current is not None:
            current.append(line)
    if current is not None:
        yield kind, "\n".join(current)


class _Builder:
    """Accumulates events and standard CES escalation signals."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.escalated = False
        self.tools: list[str] = []

    def add(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def tool_call(
        self, turn: int, actor: str, tool: str, args: Any, via: str = "llm"
    ) -> None:
        """Adds a tool_call event and updates the escalation signal."""
        if not isinstance(args, Mapping):
            args = {} if args in (None, "") else {"value": args}
        self.tools.append(tool)
        self.escalated |= _is_escalation_tool(tool, args)
        event = {
            "turn": turn,
            "type": "tool_call",
            "actor": actor,
            "tool": tool,
            "args": dict(args),
        }
        if via != "llm":
            event["via"] = via
        self.add(event)

    def tool_result(self, turn: int, actor: str, tool: str, raw: Any) -> None:
        failed = tool_failed(raw)
        event = {
            "turn": turn,
            "type": "tool_result",
            "actor": actor,
            "tool": tool,
            "response": normalize_response(raw, failed),
        }
        if failed:
            event["status"] = "error"
        self.add(event)

    def payload(self, turn: int, actor: str, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        self.add({
            "turn": turn,
            "type": "payload",
            "actor": actor,
            "payload": dict(payload),
        })


def to_conversation(
    result: Mapping[str, Any],
    conversation_id: str | None = None,
    tools_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Converts one SimulationEvals result row into a `conversation.v1` trace.

    Args:
      result: A row returned by `SimulationEvals.run_simulations` (needs
        `detailed_trace`; `turn_traces` adds callback-invoked tools).
      conversation_id: Id for the trace; defaults to `<name>#<session_id>`.
      tools_map: Optional resource-name to display-name map.

    Returns:
      The run trace.
    """
    b = _Builder()
    raw_map = tools_map or result.get("tools_map")
    resolved_tools_map = raw_map if isinstance(raw_map, Mapping) else None
    spans_by_turn: dict[int, Any] = {}
    for t in result.get("turn_traces") or []:
        if isinstance(t, Mapping) and isinstance(t.get("turn_index"), int):
            spans_by_turn[t["turn_index"]] = t.get("root_span")
    turn = -1
    actor = "agent"
    emitted_callbacks: set[int] = set()

    def emit_callbacks(t: int, llm_tools: set[str]) -> None:
        if t in emitted_callbacks or t not in spans_by_turn:
            return
        emitted_callbacks.add(t)
        for agent, tool, args, response, kind in callback_tools(
            spans_by_turn[t], tools_map=resolved_tools_map
        ):
            if kind == _FAKE_TOOL_SPAN and tool in llm_tools:
                continue  # Sibling Fake Tool already rendered as an LLM tool call.
            b.tool_call(t, agent, tool, args, via="callback")
            b.tool_result(t, agent, tool, response)

    for entry in result.get("detailed_trace") or []:
        if not isinstance(entry, str):
            continue
        if entry.startswith(_USER_PREFIX):
            turn += 1
            text = entry[len(_USER_PREFIX) :]
            if text.startswith(_BARGE_IN_PREFIX):
                text = text[len(_BARGE_IN_PREFIX) :]
            b.add({
                "turn": turn,
                "type": "user_message",
                "actor": "user",
                "text": text,
            })
            continue
        turn = max(turn, 0)
        lines = []
        for kind, line in _lines(entry):
            match = _PATTERNS[kind].match(line)
            if match is not None:
                lines.append((kind, match))
        emit_callbacks(turn, {m.group(1) for k, m in lines if k == "tool_call"})
        # Top-level agent text is rendered first but spoken after the turn's tools
        # and transfers, so it is emitted last (with the final speaking agent).
        spoken = []
        for kind, match in lines:
            if kind == "agent_text":
                spoken.append(match.group(1))
            elif kind == "agent_text_diag":
                b.add({
                    "turn": turn,
                    "type": "agent_message",
                    "actor": actor,
                    "text": match.group(1),
                })
            elif kind == "tool_call":
                b.tool_call(
                    turn, actor, match.group(1), parse_value(match.group(2))
                )
            elif kind == "tool_response":
                b.tool_result(
                    turn, actor, match.group(1), parse_value(match.group(2))
                )
            elif kind == "transfer":
                target = _short_name(match.group(1).strip())
                b.add({
                    "turn": turn,
                    "type": "handoff",
                    "actor": actor,
                    "target": target,
                })
                actor = target
            elif kind == "payload":
                b.payload(turn, actor, parse_value(match.group(1)))
        for text in spoken:
            b.add({
                "turn": turn,
                "type": "agent_message",
                "actor": actor,
                "text": text,
            })
    for t in sorted(spans_by_turn):
        emit_callbacks(
            t, set()
        )  # Turns with no agent entry (e.g. ended sessions).
    b.events.sort(key=lambda e: e["turn"])  # Stable: keeps in-turn order.
    name = str(result.get("name") or "run")
    return {
        "format": FORMAT,
        "conversation_id": (
            conversation_id
            or f"{name}#{result.get('session_id') or result.get('run') or 0}"
        ),
        "source": SOURCE,
        "attributes": {
            "session_id": result.get("session_id"),
            "tools": b.tools,
        },
        "signals": {"session_escalated": b.escalated},
        "events": b.events,
    }
