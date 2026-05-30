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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.chat_session import (
    ChatSession,
    SessionEndedError,
    TurnRecord,
)

APP = "projects/p/locations/l/apps/a"


def _structured_response(
    agent_text="Hello!",
    tool_calls=None,
    tool_responses=None,
    agent_transfer=None,
    session_ended=False,
    **extras,
):
    """Build a dict matching Sessions.get_structured_response() output."""
    result = {
        "agent_text": agent_text,
        "tool_calls": tool_calls or [],
        "tool_responses": tool_responses or [],
        "agent_transfer": agent_transfer,
        "session_ended": session_ended,
    }
    result.update(extras)
    return result


@pytest.fixture
def mock_sessions():
    """Patch Sessions so construction doesn't hit real auth."""
    with patch(
        "cxas_scrapi.core.chat_session.Sessions"
    ) as MockSessions:
        instance = MagicMock()
        instance.create_session_id.return_value = "test-session-id"
        instance.run.return_value = SimpleNamespace(outputs=[])
        instance.get_structured_response.return_value = (
            _structured_response()
        )
        MockSessions.return_value = instance
        yield instance


class TestTurnRecord:
    def test_fields_from_response(self):
        resp = _structured_response(
            agent_text="Hi there",
            tool_calls=[{"action": "lookup", "args": {"q": "x"}}],
            session_ended=True,
        )
        record = TurnRecord(turn_index=0, user_text="hello", response=resp)
        assert record.turn_index == 0
        assert record.user_text == "hello"
        assert record.agent_text == "Hi there"
        assert len(record.tool_calls) == 1
        assert record.session_ended is True
        assert record.raw_response is resp

    def test_defaults_for_missing_keys(self):
        record = TurnRecord(turn_index=1, user_text="hi", response={})
        assert record.agent_text == ""
        assert record.tool_calls == []
        assert record.tool_responses == []
        assert record.agent_transfer is None
        assert record.session_ended is False


class TestChatSessionConstruction:
    def test_construction(self, mock_sessions):
        session = ChatSession(app_name=APP)
        assert session.session_id == "test-session-id"
        assert session.turns == []
        assert session.is_ended is False
        assert session.current_turn_index == 0

    def test_construction_passes_deployment_id(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Sessions"
        ) as MockSessions:
            MockSessions.return_value = mock_sessions
            ChatSession(app_name=APP, deployment_id="dep-1")
            MockSessions.assert_called_once_with(
                app_name=APP, deployment_id="dep-1"
            )


class TestChatSessionSend:
    def test_send_basic(self, mock_sessions):
        session = ChatSession(app_name=APP)
        turn = session.send("Hello")

        assert isinstance(turn, TurnRecord)
        assert turn.turn_index == 0
        assert turn.user_text == "Hello"
        assert turn.agent_text == "Hello!"
        mock_sessions.run.assert_called_once()
        mock_sessions.get_structured_response.assert_called_once()

    def test_send_accumulates_turns(self, mock_sessions):
        session = ChatSession(app_name=APP)

        responses = [
            _structured_response(agent_text="Reply 1"),
            _structured_response(agent_text="Reply 2"),
            _structured_response(agent_text="Reply 3"),
        ]
        mock_sessions.get_structured_response.side_effect = responses

        session.send("msg1")
        session.send("msg2")
        session.send("msg3")

        assert len(session.turns) == 3
        assert session.turns[0].agent_text == "Reply 1"
        assert session.turns[1].agent_text == "Reply 2"
        assert session.turns[2].agent_text == "Reply 3"
        assert session.current_turn_index == 3

    def test_send_raises_after_session_end(self, mock_sessions):
        session = ChatSession(app_name=APP)

        mock_sessions.get_structured_response.return_value = (
            _structured_response(session_ended=True)
        )
        session.send("bye")

        assert session.is_ended is True
        with pytest.raises(SessionEndedError):
            session.send("more")

    def test_send_raises_after_close(self, mock_sessions):
        session = ChatSession(app_name=APP)
        session.close()
        assert session.is_ended is True
        with pytest.raises(SessionEndedError):
            session.send("after close")

    def test_channel_injection_first_turn_only(self, mock_sessions):
        session = ChatSession(app_name=APP, channel="web")

        mock_sessions.get_structured_response.side_effect = [
            _structured_response(agent_text="r1"),
            _structured_response(agent_text="r2"),
        ]

        session.send("first")
        session.send("second")

        # First call should include variables with channel
        first_call_kwargs = mock_sessions.run.call_args_list[0]
        assert first_call_kwargs.kwargs.get("variables") == {
            "event_data": {"channel": "web"}
        }

        # Second call should NOT include variables
        second_call_kwargs = mock_sessions.run.call_args_list[1]
        assert "variables" not in second_call_kwargs.kwargs

    def test_historical_contexts_passthrough(self, mock_sessions):
        contexts = [
            {"role": "user", "chunks": [{"text": "old msg"}]},
            {"role": "model", "chunks": [{"text": "old reply"}]},
        ]
        session = ChatSession(
            app_name=APP, historical_contexts=contexts
        )
        session.send("new msg")

        call_kwargs = mock_sessions.run.call_args_list[0].kwargs
        assert call_kwargs["historical_contexts"] == contexts

    def test_historical_contexts_not_sent_on_second_turn(
        self, mock_sessions
    ):
        contexts = [
            {"role": "user", "chunks": [{"text": "old"}]}
        ]
        session = ChatSession(
            app_name=APP, historical_contexts=contexts
        )

        mock_sessions.get_structured_response.side_effect = [
            _structured_response(agent_text="r1"),
            _structured_response(agent_text="r2"),
        ]

        session.send("first")
        session.send("second")

        second_call_kwargs = mock_sessions.run.call_args_list[1].kwargs
        assert "historical_contexts" not in second_call_kwargs


class TestChatSessionGetState:
    def test_get_state_empty(self, mock_sessions):
        session = ChatSession(app_name=APP)
        state = session.get_state()
        assert state == {
            "active_agent": None,
            "slot_machine": {},
            "filled_slots": {},
            "session_ended": False,
            "turn_count": 0,
            "pending_transfer": None,
        }

    def test_get_state_with_transfer(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                agent_transfer={"display_name": "Reservation_Agent"}
            )
        )
        session.send("hello")
        state = session.get_state()
        assert state["active_agent"] == "Reservation_Agent"
        assert state["pending_transfer"] == "Reservation_Agent"
        assert state["turn_count"] == 1

    def test_get_state_with_slot_machine(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {
                        "slot_machine": {
                            "current": "party_size",
                            "filled": {"party_size": "4", "date": "Friday"},
                        }
                    }
                ],
            )
        )
        session.send("table for 4 on Friday")
        state = session.get_state()
        assert state["slot_machine"]["current"] == "party_size"
        assert state["filled_slots"] == {
            "party_size": "4",
            "date": "Friday",
        }

    def test_get_state_with_sm_key(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {
                        "sm": {
                            "filled": {"party_size": "4"},
                            "pending": {"date": None},
                        }
                    }
                ],
            )
        )
        session.send("table for 4")
        state = session.get_state()
        assert state["slot_machine"]["filled"] == {"party_size": "4"}
        assert state["filled_slots"] == {"party_size": "4"}

    def test_variable_state_accumulates_across_turns(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.side_effect = [
            _structured_response(
                variable_updates=[
                    {"slot_machine": {"filled": {"party_size": "4"}, "pending": {}}}
                ],
            ),
            _structured_response(
                variable_updates=[
                    {"slot_machine": {"filled": {"party_size": "4", "date": "Fri"}, "pending": {}}}
                ],
            ),
        ]
        session.send("table for 4")
        session.send("on Friday")
        state = session.get_state()
        assert state["filled_slots"] == {"party_size": "4", "date": "Fri"}

    def test_get_state_session_ended(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(session_ended=True)
        )
        session.send("bye")
        state = session.get_state()
        assert state["session_ended"] is True


class TestChatSessionExport:
    def test_export_turns_summary(self, mock_sessions):
        session = ChatSession(app_name=APP)

        mock_sessions.get_structured_response.side_effect = [
            _structured_response(
                agent_text="Hi",
                tool_calls=[{"action": "greet", "args": {}}],
            ),
            _structured_response(
                agent_text="Transferring",
                agent_transfer={"display_name": "Other_Agent"},
            ),
        ]

        session.send("hello")
        session.send("transfer me")

        summary = session.export_turns_summary()
        assert len(summary) == 2
        assert summary[0] == {
            "turn": 0,
            "user": "hello",
            "agent": "Hi",
            "tool_calls": [{"action": "greet", "args": {}}],
            "transfer": None,
        }
        assert summary[1]["turn"] == 1
        assert summary[1]["transfer"] == "Other_Agent"

    def test_export_turns_summary_empty(self, mock_sessions):
        session = ChatSession(app_name=APP)
        assert session.export_turns_summary() == []


class TestChatSessionTrace:
    def test_get_trace(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Traces"
        ) as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_report.return_value = '{"trace": "data"}'
            MockTraces.return_value = mock_traces_inst

            session = ChatSession(app_name=APP)
            result = session.get_trace(fmt="json")

            assert result == '{"trace": "data"}'
            mock_traces_inst.get_report.assert_called_once_with(
                "test-session-id", fmt="json"
            )

    def test_get_normalized_trace(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Traces"
        ) as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_normalized.return_value = {
                "entries": []
            }
            MockTraces.return_value = mock_traces_inst

            session = ChatSession(app_name=APP)
            result = session.get_normalized_trace()

            assert result == {"entries": []}
            mock_traces_inst.get_normalized.assert_called_once_with(
                "test-session-id"
            )


class TestGetSlotMachine:
    def test_returns_slot_machine_key(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {"slot_machine": {"filled": {"party_size": "4"}, "status": "ok"}}
                ],
            )
        )
        session.send("table for 4")
        sm = session.get_slot_machine()
        assert sm["filled"] == {"party_size": "4"}

    def test_returns_sm_key(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {"sm": {"filled": {"date": "Friday"}, "pending": {}}}
                ],
            )
        )
        session.send("Friday")
        sm = session.get_slot_machine()
        assert sm["filled"] == {"date": "Friday"}

    def test_prefers_sm_over_slot_machine(self, mock_sessions):
        session = ChatSession(app_name=APP)
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {
                        "sm": {"filled": {"date": "Friday"}},
                        "slot_machine": {"filled": {"old": "data"}},
                    }
                ],
            )
        )
        session.send("Friday")
        sm = session.get_slot_machine()
        assert sm["filled"] == {"date": "Friday"}

    def test_returns_empty_dict_when_no_data(self, mock_sessions):
        session = ChatSession(app_name=APP)
        session.send("hello")
        sm = session.get_slot_machine()
        assert sm == {}


class TestChatSessionClose:
    def test_close_is_idempotent(self, mock_sessions):
        session = ChatSession(app_name=APP)
        session.close()
        session.close()  # should not raise
        assert session.is_ended is True
