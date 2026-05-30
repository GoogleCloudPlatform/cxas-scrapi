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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.chat_session import ChatSession
from cxas_scrapi.core.programmatic_chat import ProgrammaticChatDriver

APP = "projects/p/locations/l/apps/a"


def _structured_response(
    agent_text="Hello!",
    tool_calls=None,
    tool_responses=None,
    agent_transfer=None,
    session_ended=False,
    **extras,
):
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
    with patch("cxas_scrapi.core.chat_session.Sessions") as MockSessions:
        instance = MagicMock()
        instance.create_session_id.return_value = "test-session-id"
        instance.run.return_value = SimpleNamespace(outputs=[])
        instance.get_structured_response.return_value = _structured_response()
        MockSessions.return_value = instance
        yield instance


class TestProgrammaticChatDriverConstruction:
    def test_construction_default(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        assert driver.session_id == "test-session-id"
        assert driver.is_ended is False

    def test_construction_with_session_id(self, mock_sessions):
        driver = ProgrammaticChatDriver(
            app_name=APP, session_id="custom-session-123"
        )
        assert driver.session_id == "custom-session-123"
        mock_sessions.create_session_id.assert_not_called()

    def test_construction_with_initial_turn_count(self, mock_sessions):
        driver = ProgrammaticChatDriver(
            app_name=APP, initial_turn_count=5
        )
        result = driver.step("hello")
        assert result["turn_index"] == 5


class TestStep:
    def test_step_returns_dict(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        result = driver.step("Hello")

        assert isinstance(result, dict)
        expected_keys = {
            "turn_index", "user_text", "agent_text", "tool_calls",
            "tool_responses", "agent_transfer", "session_ended",
            "state", "session_id", "trace", "metrics",
        }
        assert set(result.keys()) == expected_keys

    def test_step_json_serializable(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        result = driver.step("Hello")
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["user_text"] == "Hello"

    def test_step_includes_state(self, mock_sessions):
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                agent_transfer={"display_name": "Reservation_Agent"}
            )
        )
        driver = ProgrammaticChatDriver(app_name=APP)
        result = driver.step("hello")

        state = result["state"]
        assert "active_agent" in state
        assert "filled_slots" in state
        assert "session_ended" in state
        assert state["active_agent"] == "Reservation_Agent"

    def test_step_with_trace(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Traces"
        ) as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_normalized.return_value = {
                "entries": [{"event": "start"}],
                "turn_metrics": [{"latency_ms": 100}],
                "totals": {"total_latency_ms": 100},
            }
            MockTraces.return_value = mock_traces_inst

            driver = ProgrammaticChatDriver(
                app_name=APP, include_trace=True
            )
            result = driver.step("Hello")

            assert result["trace"] is not None
            assert result["trace"]["entries"] == [{"event": "start"}]

    def test_step_with_metrics(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Traces"
        ) as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_normalized.return_value = {
                "entries": [],
                "turn_metrics": [{"latency_ms": 42}],
                "totals": {},
            }
            MockTraces.return_value = mock_traces_inst

            driver = ProgrammaticChatDriver(
                app_name=APP, include_metrics=True
            )
            result = driver.step("Hello")

            assert result["metrics"] == {"latency_ms": 42}

    def test_step_without_trace(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP, include_trace=False)
        result = driver.step("Hello")
        assert result["trace"] is None
        assert result["metrics"] is None

    def test_step_trace_error_handled(self, mock_sessions):
        with patch(
            "cxas_scrapi.core.chat_session.Traces"
        ) as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_normalized.side_effect = RuntimeError(
                "Trace fetch failed"
            )
            MockTraces.return_value = mock_traces_inst

            driver = ProgrammaticChatDriver(
                app_name=APP, include_trace=True
            )
            result = driver.step("Hello")

            assert result["trace"] == {"error": "Trace fetch failed"}

    def test_step_accumulates_turns(self, mock_sessions):
        mock_sessions.get_structured_response.side_effect = [
            _structured_response(agent_text="Reply 1"),
            _structured_response(agent_text="Reply 2"),
            _structured_response(agent_text="Reply 3"),
        ]

        driver = ProgrammaticChatDriver(app_name=APP)
        r1 = driver.step("msg1")
        r2 = driver.step("msg2")
        r3 = driver.step("msg3")

        assert r1["turn_index"] == 0
        assert r2["turn_index"] == 1
        assert r3["turn_index"] == 2
        assert r1["agent_text"] == "Reply 1"
        assert r2["agent_text"] == "Reply 2"
        assert r3["agent_text"] == "Reply 3"


class TestSerializeTransfer:
    def test_serialize_transfer_none(self):
        assert ProgrammaticChatDriver._serialize_transfer(None) is None

    def test_serialize_transfer_dict(self):
        transfer = {
            "display_name": "Res_Agent",
            "target_agent": "res",
        }
        result = ProgrammaticChatDriver._serialize_transfer(transfer)
        assert result == "Res_Agent"

    def test_serialize_transfer_dict_fallback(self):
        transfer = {"target_agent": "res_agent"}
        result = ProgrammaticChatDriver._serialize_transfer(transfer)
        assert result == "res_agent"

    def test_serialize_transfer_object(self):
        obj = SimpleNamespace(display_name="Object_Agent")
        assert ProgrammaticChatDriver._serialize_transfer(obj) == "Object_Agent"

    def test_serialize_transfer_string(self):
        assert ProgrammaticChatDriver._serialize_transfer(12345) == "12345"


class TestGetFullState:
    def test_get_full_state_without_slot_inspector(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        state = driver.get_full_state()
        assert "active_agent" in state
        assert "filled_slots" in state
        assert "slot_inspection" not in state

    def test_get_full_state_with_slot_machine(self, mock_sessions):
        mock_sessions.get_structured_response.return_value = (
            _structured_response(
                variable_updates=[
                    {
                        "slot_machine": {
                            "current": "party_size",
                            "filled": {"party_size": "4"},
                        }
                    }
                ],
            )
        )
        driver = ProgrammaticChatDriver(app_name=APP)
        driver.step("table for 4")
        state = driver.get_full_state()
        assert state["slot_machine"]["current"] == "party_size"
        assert state["filled_slots"] == {"party_size": "4"}


class TestClose:
    def test_close_ends_session(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        assert driver.is_ended is False
        driver.close()
        assert driver.is_ended is True


class TestChatSessionResumeSupport:
    def test_session_id_passthrough(self, mock_sessions):
        session = ChatSession(
            app_name=APP, session_id="custom-id-abc"
        )
        assert session.session_id == "custom-id-abc"
        mock_sessions.create_session_id.assert_not_called()

    def test_session_id_generated_when_none(self, mock_sessions):
        session = ChatSession(app_name=APP)
        assert session.session_id == "test-session-id"
        mock_sessions.create_session_id.assert_called_once()

    def test_initial_turn_count(self, mock_sessions):
        session = ChatSession(
            app_name=APP, initial_turn_count=5
        )
        assert session.current_turn_index == 5

    def test_initial_turn_count_accumulates(self, mock_sessions):
        mock_sessions.get_structured_response.side_effect = [
            _structured_response(agent_text="r1"),
            _structured_response(agent_text="r2"),
        ]
        session = ChatSession(
            app_name=APP, initial_turn_count=10
        )
        assert session.current_turn_index == 10
        session.send("hello")
        assert session.current_turn_index == 11
        session.send("world")
        assert session.current_turn_index == 12


class TestNewMethods:
    def test_get_sm_log_returns_filtered_entries(self, mock_sessions):
        mock_sessions.get_structured_response.return_value = _structured_response(
            variable_updates=[{
                "sm": {
                    "filled": {},
                    "_log": [
                        {"tag": "invoke", "level": "DEBUG", "data": {}},
                        {"tag": "config_loaded", "level": "INFO", "data": {}},
                        {"tag": "slot_error", "level": "ERROR", "data": {}},
                    ],
                }
            }],
        )
        driver = ProgrammaticChatDriver(app_name=APP)
        driver.step("hello")
        log = driver.get_sm_log("INFO")
        assert len(log) == 2
        assert log[0]["tag"] == "config_loaded"
        assert log[1]["tag"] == "slot_error"

    def test_get_sm_log_empty_when_no_sm(self, mock_sessions):
        driver = ProgrammaticChatDriver(app_name=APP)
        assert driver.get_sm_log() == []

    def test_get_sm_log_debug_returns_all(self, mock_sessions):
        mock_sessions.get_structured_response.return_value = _structured_response(
            variable_updates=[{
                "sm": {
                    "filled": {},
                    "_log": [
                        {"tag": "invoke", "level": "DEBUG", "data": {}},
                        {"tag": "config_loaded", "level": "INFO", "data": {}},
                    ],
                }
            }],
        )
        driver = ProgrammaticChatDriver(app_name=APP)
        driver.step("hello")
        log = driver.get_sm_log("DEBUG")
        assert len(log) == 2

    def test_get_flow_context_delegates(self, mock_sessions):
        mock_sessions.get_structured_response.return_value = _structured_response(
            variable_updates=[{
                "_active_config_id": "reservation",
                "agent_config_map": '{"Reservation_Agent": "reservation"}',
                "_active_sm_key": "sm",
            }],
        )
        driver = ProgrammaticChatDriver(app_name=APP)
        driver.step("hello")
        ctx = driver.get_flow_context()
        assert ctx["active_config_id"] == "reservation"
        assert ctx["agent_config_map"] == {"Reservation_Agent": "reservation"}

    def test_get_trace_report_delegates(self, mock_sessions):
        with patch("cxas_scrapi.core.chat_session.Traces") as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.get_report.return_value = "# Trace Report"
            MockTraces.return_value = mock_traces_inst
            driver = ProgrammaticChatDriver(app_name=APP)
            report = driver.get_trace_report(fmt="md")
            mock_traces_inst.get_report.assert_called_once()

    def test_get_turns_summary_delegates(self, mock_sessions):
        mock_sessions.get_structured_response.side_effect = [
            _structured_response(agent_text="Reply 1"),
            _structured_response(agent_text="Reply 2"),
        ]
        driver = ProgrammaticChatDriver(app_name=APP)
        driver.step("msg1")
        driver.step("msg2")
        summary = driver.get_turns_summary()
        assert len(summary) == 2
        assert summary[0]["user"] == "msg1"
        assert summary[1]["agent"] == "Reply 2"

    def test_report_bug_delegates(self, mock_sessions):
        with patch("cxas_scrapi.core.traces.Traces") as MockTraces:
            mock_traces_inst = MagicMock()
            mock_traces_inst.report_bug.return_value = {"status": "reported"}
            MockTraces.return_value = mock_traces_inst
            driver = ProgrammaticChatDriver(app_name=APP)
            result = driver.report_bug("something broke")
            mock_traces_inst.report_bug.assert_called_once_with(
                conversation_id="test-session-id",
                reason="something broke",
            )
            assert result["status"] == "reported"

    def test_initial_variable_state_seeded(self, mock_sessions):
        driver = ProgrammaticChatDriver(
            app_name=APP,
            initial_variable_state={"sm": {"filled": {"name": "Alice"}, "_log": [{"tag": "test", "level": "INFO"}]}},
        )
        log = driver.get_sm_log()
        assert len(log) == 1
        assert log[0]["tag"] == "test"
