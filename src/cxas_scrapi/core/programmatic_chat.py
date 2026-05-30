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

"""Programmatic turn-by-turn chat driver with JSON-serializable output."""

from typing import Any

from cxas_scrapi.core.chat_session import ChatSession


class ProgrammaticChatDriver:
    """Drives a ChatSession programmatically.

    Returns JSON-serializable dicts per turn.

    Usage:
        driver = ProgrammaticChatDriver(
            app_name="projects/.../apps/..."
        )
        result = driver.step("Hello")
        print(json.dumps(result, indent=2))
        result = driver.step("Table for 4")
        driver.close()
    """

    def __init__(
        self,
        app_name: str,
        channel: str | None = None,
        deployment_id: str | None = None,
        include_trace: bool = False,
        include_metrics: bool = False,
        historical_contexts: list[dict] | str | None = None,
        session_id: str | None = None,
        initial_turn_count: int = 0,
        initial_variable_state: dict | None = None,
        **session_kwargs: Any,
    ):
        self._session = ChatSession(
            app_name=app_name,
            channel=channel,
            deployment_id=deployment_id,
            historical_contexts=historical_contexts,
            session_id=session_id,
            initial_turn_count=initial_turn_count,
            initial_variable_state=initial_variable_state,
            **session_kwargs,
        )
        self._include_raw_trace = include_trace
        self._include_metrics = include_metrics

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def is_ended(self) -> bool:
        return self._session.is_ended

    def step(self, text: str) -> dict[str, Any]:
        """Send one message, return a JSON-serializable dict."""
        turn = self._session.send(text)
        state = self._session.get_state()

        result: dict[str, Any] = {
            "turn_index": turn.turn_index,
            "user_text": turn.user_text,
            "agent_text": turn.agent_text,
            "tool_calls": turn.tool_calls,
            "tool_responses": turn.tool_responses,
            "agent_transfer": self._serialize_transfer(turn.agent_transfer),
            "session_ended": turn.session_ended,
            "state": state,
            "session_id": self._session.session_id,
            "trace": None,
            "metrics": None,
        }
        if turn.payloads:
            result["payloads"] = turn.payloads

        try:
            normalized = self._session.get_normalized_trace()
            self._enrich_from_trace(result, normalized, turn.turn_index)
            if self._include_raw_trace:
                result["trace"] = normalized
            if self._include_metrics:
                turn_metrics = normalized.get("turn_metrics", [])
                if turn.turn_index < len(turn_metrics):
                    result["metrics"] = turn_metrics[turn.turn_index]
        except Exception as e:
            if self._include_raw_trace:
                result["trace"] = {"error": str(e)}

        return result

    def get_full_state(self) -> dict[str, Any]:
        """Return current state with optional deep slot inspection."""
        state = self._session.get_state()
        try:
            from cxas_scrapi.utils.slot_inspector import SlotInspector
            sm = state.get("slot_machine")
            if sm and isinstance(sm, dict):
                state["slot_inspection"] = SlotInspector.inspect(sm)
        except ImportError:
            pass
        return state

    def get_sm_log(self, min_level: str = "INFO") -> list[dict[str, Any]]:
        """Return slot machine log entries at or above *min_level*."""
        sm = self._session.get_slot_machine()
        if not sm:
            return []
        log = sm.get("_log", [])
        level_order = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
        min_ord = level_order.get(min_level.upper(), 1)
        return [
            e for e in log
            if level_order.get(e.get("level", "INFO").upper(), 1) >= min_ord
        ]

    def get_flow_context(self) -> dict[str, Any]:
        """Return the multi-flow context from the session."""
        return self._session.get_flow_context()

    def get_trace_report(self, fmt: str = "json") -> str:
        """Return the trace report for this session."""
        return self._session.get_trace(fmt=fmt)

    def get_turns_summary(self) -> list[dict[str, Any]]:
        """Return a concise summary of every turn."""
        return self._session.export_turns_summary()

    def report_bug(self, reason: str) -> dict[str, Any]:
        """File a bug report against this conversation."""
        from cxas_scrapi.core.traces import Traces
        traces = Traces(app_name=self._session._app_name)
        return traces.report_bug(
            conversation_id=self._session.session_id,
            reason=reason,
        )

    @staticmethod
    def _enrich_from_trace(
        result: dict[str, Any],
        trace: dict[str, Any],
        turn_index: int,
    ) -> None:
        """Rebuild tool_calls and tool_responses from the trace.

        The CES API returns empty diagnostic_info chunks for tool calls
        during multi-agent turns, so the structured response misses them.
        The trace is the authoritative source.

        The trace numbers exchanges with its own cumulative ``turn`` field,
        which can drift from the session's ``turn_index`` (CES
        ``state.turn_count`` does not increment in lockstep across a
        multi-turn ``--session-file`` conversation, especially after agent
        transfers). A single ``send()`` always produces the latest trace
        turn, so we anchor on the maximum ``turn`` present rather than
        trusting the passed ``turn_index`` — otherwise enrichment surfaces
        tool calls from an earlier exchange.
        """
        entries = trace.get("entries", []) or []
        turns = [
            e.get("turn") for e in entries
            if isinstance(e, dict) and isinstance(e.get("turn"), int)
        ]
        target_turn = max(turns) if turns else turn_index
        tool_calls = []
        tool_responses = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("turn") != target_turn:
                continue
            kind = entry.get("kind")
            if kind == "tool_call":
                tool_calls.append({
                    "action": entry.get("tool", ""),
                    "args": entry.get("args", {}),
                    "agent": entry.get("agent", ""),
                })
            elif kind == "tool_response":
                tool_responses.append({
                    "action": entry.get("tool", ""),
                    "response": entry.get("response", {}),
                    "agent": entry.get("agent", ""),
                })
        if tool_calls:
            result["tool_calls"] = tool_calls
        if tool_responses:
            result["tool_responses"] = tool_responses

    @staticmethod
    def _serialize_transfer(agent_transfer: Any) -> str | None:
        """Convert agent_transfer to a JSON-serializable string."""
        if agent_transfer is None:
            return None
        if isinstance(agent_transfer, dict):
            return agent_transfer.get(
                "display_name",
                agent_transfer.get("target_agent"),
            )
        if hasattr(agent_transfer, "display_name"):
            return agent_transfer.display_name
        return str(agent_transfer)

    def close(self) -> None:
        """Close the underlying session."""
        self._session.close()
