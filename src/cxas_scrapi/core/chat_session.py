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

"""Interactive chat session with turn tracking and trace integration."""

import logging
from typing import Any

from google.protobuf import json_format

from cxas_scrapi.core.sessions import Modality, Sessions
from cxas_scrapi.core.traces import Traces

logger = logging.getLogger(__name__)


class SessionEndedError(Exception):
    """Raised when trying to send to an ended session."""

    pass


class TurnRecord:
    """Immutable record of a single conversation turn."""

    def __init__(
        self, turn_index: int, user_text: str, response: dict[str, Any]
    ):
        self.turn_index = turn_index
        self.user_text = user_text
        self.agent_text: str = response.get("agent_text", "")
        self.tool_calls: list[dict] = response.get("tool_calls", [])
        self.tool_responses: list[dict] = response.get("tool_responses", [])
        self.agent_transfer: Any = response.get("agent_transfer")
        self.session_ended: bool = response.get("session_ended", False)
        self.payloads: list[dict] = response.get("payloads", [])
        self.raw_response: dict[str, Any] = response


class ChatSession:
    """Manages a live conversation with turn history and state tracking.

    Usage:
        session = ChatSession(app_name="projects/.../apps/...")
        result = session.send("Hello")
        result = session.send("I'd like a table for 4")
        trace = session.get_trace()  # fetch trace for this session
        session.close()
    """

    def __init__(
        self,
        app_name: str,
        channel: str | None = None,
        deployment_id: str | None = None,
        historical_contexts: list[dict] | str | None = None,
        turn_count: int | None = None,
        session_id: str | None = None,
        initial_turn_count: int = 0,
        initial_variable_state: dict[str, Any] | None = None,
        **session_kwargs: Any,
    ):
        self._app_name = app_name
        self._channel = channel
        self._deployment_id = deployment_id
        self._historical_contexts = historical_contexts
        self._turn_count = turn_count
        self._initial_turn_count = initial_turn_count
        self._session_kwargs = session_kwargs

        self._sessions = Sessions(
            app_name=app_name,
            deployment_id=deployment_id,
            **session_kwargs,
        )
        self._session_id = (
            session_id
            if session_id is not None
            else self._sessions.create_session_id()
        )
        self._turns: list[TurnRecord] = []
        self._variable_state: dict[str, Any] = dict(initial_variable_state) if initial_variable_state else {}
        self._closed = False

    @property
    def session_id(self) -> str:
        """The unique session ID for this conversation."""
        return self._session_id

    @property
    def turns(self) -> list[TurnRecord]:
        """All turns in this conversation so far."""
        return list(self._turns)

    @property
    def is_ended(self) -> bool:
        """Whether the session has ended (via agent or explicit close)."""
        if self._closed:
            return True
        if self._turns and self._turns[-1].session_ended:
            return True
        return False

    @property
    def current_turn_index(self) -> int:
        """The index that will be assigned to the next turn."""
        return self._initial_turn_count + len(self._turns)

    def send(self, text: str) -> TurnRecord:
        """Send a message and return the turn record.

        Raises SessionEndedError if session has already ended.
        Uses self._sessions.run() + get_structured_response().
        Tracks the turn in self._turns list.
        If channel was set, injects {"event_data": {"channel": channel}}
        as variables on the first turn only.
        """
        if self.is_ended:
            raise SessionEndedError(
                f"Session {self._session_id} has already ended."
            )

        turn_index = self.current_turn_index
        variables = None
        if self._channel and turn_index == 0:
            variables = {"event_data": {"channel": self._channel}}

        kwargs: dict[str, Any] = {
            "session_id": self._session_id,
            "text": text,
            "modality": Modality.TEXT,
        }
        if variables is not None:
            kwargs["variables"] = variables
        if self._historical_contexts is not None and turn_index == 0:
            kwargs["historical_contexts"] = self._historical_contexts
        if self._turn_count is not None and turn_index == 0:
            kwargs["turn_count"] = self._turn_count

        raw_response = self._sessions.run(**kwargs)
        structured = self._sessions.get_structured_response(raw_response)

        for var_dict in structured.get("variable_updates", []):
            if isinstance(var_dict, dict):
                self._variable_state.update(var_dict)

        turn = TurnRecord(
            turn_index=turn_index,
            user_text=text,
            response=structured,
        )
        self._turns.append(turn)
        return turn

    def send_event(
        self,
        event_name: str,
        event_vars: dict[str, Any] | None = None,
    ) -> TurnRecord:
        """Fire a CES event and return the turn record."""
        if self.is_ended:
            raise SessionEndedError(
                f"Session {self._session_id} has already ended."
            )

        turn_index = self.current_turn_index
        raw_response = self._sessions.run(
            session_id=self._session_id,
            event=event_name,
            event_vars=event_vars,
        )
        structured = self._sessions.get_structured_response(raw_response)

        for var_dict in structured.get("variable_updates", []):
            if isinstance(var_dict, dict):
                self._variable_state.update(var_dict)

        turn = TurnRecord(
            turn_index=turn_index,
            user_text=f"[event: {event_name}]",
            response=structured,
        )
        self._turns.append(turn)
        return turn

    def get_state(self) -> dict[str, Any]:
        """Extract current variable/slot state from the turn history.

        Returns dict with keys:
        - "active_agent": str | None (from last agent_transfer or initial)
        - "slot_machine": dict (from accumulated variable updates)
        - "filled_slots": dict (from sm.filled)
        - "session_ended": bool
        - "turn_count": int
        - "pending_transfer": str | None
        """
        state: dict[str, Any] = {
            "active_agent": None,
            "slot_machine": {},
            "filled_slots": {},
            "session_ended": False,
            "turn_count": len(self._turns),
            "pending_transfer": None,
        }

        for turn in self._turns:
            if turn.agent_transfer:
                target = turn.agent_transfer
                if hasattr(target, "display_name"):
                    state["active_agent"] = target.display_name
                elif isinstance(target, dict):
                    state["active_agent"] = target.get(
                        "display_name", target.get("target_agent")
                    )
                else:
                    state["active_agent"] = str(target)
                state["pending_transfer"] = state["active_agent"]

            if turn.session_ended:
                state["session_ended"] = True

        sm = self.get_slot_machine()
        if sm:
            state["slot_machine"] = sm
            filled = sm.get("filled", {})
            if isinstance(filled, dict):
                state["filled_slots"] = filled

        return state

    def get_trace(self, fmt: str = "json") -> str:
        """Fetch the full trace report for this session's conversation.

        Creates a Traces instance and calls get_report().
        The conversation_id is the session_id.
        """
        traces = Traces(
            app_name=self._app_name, **self._session_kwargs
        )
        return traces.get_report(self._session_id, fmt=fmt)

    def get_normalized_trace(self) -> dict[str, Any]:
        """Fetch the normalized trace dict for this session."""
        traces = Traces(
            app_name=self._app_name, **self._session_kwargs
        )
        return traces.get_normalized(self._session_id)

    def get_slot_machine(self) -> dict[str, Any]:
        """Get slot_machine state from accumulated session variables.

        Checks both 'sm' and 'slot_machine' keys since agents use
        either name for the slot machine variable.
        """
        for key in ("sm", "slot_machine"):
            val = self._variable_state.get(key)
            if isinstance(val, dict) and val:
                return val
        return {}

    def get_flow_context(self) -> dict[str, Any]:
        """Get multi-flow context from top-level session variables.

        Returns dict with:
        - active_config_id: currently active flow config
        - agent_config_map: {agent_name: config_id} for all flows
        - active_sm_key: which variable key holds the active sm
        """
        agent_map = self._variable_state.get("agent_config_map", "")
        if isinstance(agent_map, str) and agent_map:
            try:
                import json
                agent_map = json.loads(agent_map)
            except (ValueError, TypeError):
                agent_map = {}

        return {
            "active_config_id": self._variable_state.get(
                "_active_config_id"
            ),
            "agent_config_map": agent_map if isinstance(agent_map, dict) else {},
            "active_sm_key": self._variable_state.get("_active_sm_key"),
        }

    def export_turns_summary(self) -> list[dict[str, Any]]:
        """Export turns as a list of dicts for scripting/comparison.

        Each dict: {"turn": int, "user": str, "agent": str,
                     "tool_calls": [...], "transfer": str|None}
        """
        summaries = []
        for turn in self._turns:
            transfer = None
            if turn.agent_transfer:
                if hasattr(turn.agent_transfer, "display_name"):
                    transfer = turn.agent_transfer.display_name
                elif isinstance(turn.agent_transfer, dict):
                    transfer = turn.agent_transfer.get(
                        "display_name",
                        turn.agent_transfer.get("target_agent"),
                    )
                else:
                    transfer = str(turn.agent_transfer)

            summaries.append(
                {
                    "turn": turn.turn_index,
                    "user": turn.user_text,
                    "agent": turn.agent_text,
                    "tool_calls": turn.tool_calls,
                    "transfer": transfer,
                }
            )
        return summaries

    def close(self) -> None:
        """Mark session as closed. Idempotent."""
        self._closed = True
