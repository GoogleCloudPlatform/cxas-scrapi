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

from io import StringIO

import pytest
from rich.console import Console

from cxas_scrapi.core.chat_session import TurnRecord
from cxas_scrapi.utils.chat_renderer import SLASH_COMMANDS, ChatRenderer


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO for capture."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    return console, buf


def _make_turn(
    turn_index=0,
    user_text="hi",
    agent_text="hello",
    tool_calls=None,
    tool_responses=None,
    agent_transfer=None,
    session_ended=False,
) -> TurnRecord:
    return TurnRecord(
        turn_index=turn_index,
        user_text=user_text,
        response={
            "agent_text": agent_text,
            "tool_calls": tool_calls or [],
            "tool_responses": tool_responses or [],
            "agent_transfer": agent_transfer,
            "session_ended": session_ended,
        },
    )


class TestRenderSessionStart:
    def test_render_session_start(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_session_start("sid-123", "projects/p/apps/a")
        output = buf.getvalue()
        assert "sid-123" in output
        assert "projects/p/apps/a" in output
        assert "Chat Session Started" in output

    def test_render_session_start_with_display_name(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_session_start(
            "sid-123", "projects/p/apps/a", display_name="Bella Notte",
        )
        output = buf.getvalue()
        assert "Bella Notte" in output
        assert "projects/p/apps/a" in output


class TestRenderUserMessage:
    def test_render_user_message(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_user_message(0, "Hello agent!")
        output = buf.getvalue()
        assert "Turn 0" in output
        assert "Hello agent!" in output


class TestRenderTurn:
    def test_render_turn_basic(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        turn = _make_turn(agent_text="I can help with that!")
        renderer.render_turn(turn)
        output = buf.getvalue()
        assert "I can help with that!" in output

    def test_render_turn_verbose_shows_tool_calls(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console, verbose=True)
        turn = _make_turn(
            agent_text="Let me look that up.",
            tool_calls=[
                {"action": "lookup_info", "args": {"query": "test"}}
            ],
            tool_responses=[
                {
                    "action": "_response:lookup_info",
                    "response": {"result": "found"},
                }
            ],
        )
        renderer.render_turn(turn)
        output = buf.getvalue()
        assert "lookup_info" in output
        assert "Let me look that up." in output

    def test_render_turn_non_verbose_hides_tool_calls(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console, verbose=False)
        turn = _make_turn(
            agent_text="Let me check.",
            tool_calls=[
                {"action": "secret_tool", "args": {"key": "value"}}
            ],
        )
        renderer.render_turn(turn)
        output = buf.getvalue()
        assert "Let me check." in output
        # Tool call details should not appear in non-verbose mode
        assert "Tool Call" not in output

    def test_render_turn_with_transfer(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        turn = _make_turn(
            agent_text="Transferring now.",
            agent_transfer={"display_name": "Reservation_Agent"},
        )
        renderer.render_turn(turn)
        output = buf.getvalue()
        assert "Reservation_Agent" in output
        assert "Transferred to" in output

    def test_render_turn_empty_agent_text(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        turn = _make_turn(agent_text="")
        renderer.render_turn(turn)
        output = buf.getvalue()
        # Should not render an empty agent panel
        assert "Agent" not in output


class TestRenderAgentText:
    def test_render_agent_text(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_agent_text(2, "Response text here")
        output = buf.getvalue()
        assert "Turn 2" in output
        assert "Response text here" in output

    def test_render_agent_text_with_role(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_agent_text(0, "text", role="Greeter")
        output = buf.getvalue()
        assert "Greeter" in output


class TestRenderToolCall:
    def test_render_tool_call(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_tool_call(
            "set_party_size", {"party_size": 4}
        )
        output = buf.getvalue()
        assert "set_party_size" in output
        assert "party_size" in output


class TestRenderToolResponse:
    def test_render_tool_response(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_tool_response(
            "set_party_size", {"status": "ok"}
        )
        output = buf.getvalue()
        assert "set_party_size" in output
        assert "status" in output


class TestRenderTransfer:
    def test_render_transfer(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_transfer("Takeout_Agent")
        output = buf.getvalue()
        assert "Transferred to" in output
        assert "Takeout_Agent" in output


class TestRenderState:
    def test_render_state(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        state = {
            "active_agent": "Reservation_Agent",
            "slot_machine": {"current": "party_size"},
            "filled_slots": {"party_size": "4"},
            "session_ended": False,
            "turn_count": 2,
            "pending_transfer": None,
        }
        renderer.render_state(state)
        output = buf.getvalue()
        assert "Session State" in output
        assert "active_agent" in output
        assert "Reservation_Agent" in output
        assert "party_size" in output

    def test_render_state_empty(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_state({})
        output = buf.getvalue()
        assert "Session State" in output


class TestRenderSessionEnd:
    def test_render_session_end(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_session_end(
            "sid-456",
            turn_count=5,
            trace_command="cxas trace get sid-456",
        )
        output = buf.getvalue()
        assert "sid-456" in output
        assert "Session Ended" in output
        assert "5" in output
        assert "cxas trace get sid-456" in output


class TestRenderMetrics:
    def test_render_metrics(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_metrics(
            duration_ms=1234.5,
            tokens={"input": 100, "output": 50},
            tool_count=3,
        )
        output = buf.getvalue()
        assert "1234ms" in output
        assert "tools=3" in output

    def test_render_metrics_none_duration(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_metrics(
            duration_ms=None, tokens=None, tool_count=0
        )
        output = buf.getvalue()
        assert "tools=0" in output


class TestRenderError:
    def test_render_error(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_error("Something went wrong")
        output = buf.getvalue()
        assert "Error" in output
        assert "Something went wrong" in output


class TestRenderSlashHelp:
    def test_render_slash_help(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_slash_help()
        output = buf.getvalue()
        assert "Available Commands" in output
        # Verify all slash commands appear
        for cmd in SLASH_COMMANDS:
            assert cmd in output


class TestRenderLog:
    def test_render_log_basic(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "bella_notte", "n_slots": 5, "n_tasks": 2}},
            {"tag": "invoke", "level": "INFO",
             "data": {"n": 1, "phase": "collection", "filled": 0, "asking": "party_size"}},
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "party_size", "value": "4"}},
            {"tag": "task_completed", "level": "INFO",
             "data": {"task": "lookup", "success": True}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "Conversation Timeline" in output
        assert "FLOW" in output
        assert "SET" in output
        assert "TASK" in output
        assert "bella_notte" in output
        assert "party_size" in output

    def test_render_log_empty(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        renderer.render_log([])
        output = buf.getvalue()
        assert "No events at this level" in output

    def test_render_log_internal_tags_hidden(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "invoke", "level": "INFO",
             "data": {"n": 1, "phase": "collection", "filled": 0, "asking": "x"}},
            {"tag": "gate_active", "level": "INFO",
             "data": {"config_id": "takeout"}},
            {"tag": "announce_stored", "level": "INFO",
             "data": {"slot": "welcome", "value": "hi"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "No events at this level" in output

    def test_render_log_hidden_count_footer(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "party_size", "value": "4"}},
            {"tag": "invoke", "level": "INFO",
             "data": {"n": 1, "phase": "collection", "filled": 0, "asking": "x"}},
            {"tag": "announce_stored", "level": "INFO",
             "data": {"slot": "welcome", "value": "hi"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "1 events shown" in output
        assert "2 internal events hidden" in output
        assert "/log debug" in output

    def test_render_log_level_filter_warn(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "x", "value": "1"}},
            {"tag": "steer_back_soft", "level": "WARN",
             "data": {"turns": 3}},
        ]
        renderer.render_log(entries, min_level="WARN")
        output = buf.getvalue()
        assert "STEER" in output
        assert "SET" not in output

    def test_render_log_debug_fallback(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "invoke", "level": "DEBUG",
             "data": {"n": 1, "phase": "collection", "filled": 0, "asking": "x"}},
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "x", "value": "1"}},
        ]
        renderer.render_log(entries, min_level="DEBUG")
        output = buf.getvalue()
        # DEBUG fallback uses _render_log_debug which shows "Slot Filling Timeline"
        assert "Slot Filling Timeline" in output
        assert "Conversation Timeline" not in output

    def test_render_log_task_completed_format(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "task_completed", "level": "INFO",
             "data": {"task": "lookup_reservation", "success": True}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "✓" in output

        console2, buf2 = _make_console()
        renderer2 = ChatRenderer(console=console2)
        entries2 = [
            {"tag": "task_completed", "level": "INFO",
             "data": {"task": "submit_order", "success": False}},
        ]
        renderer2.render_log(entries2)
        output2 = buf2.getvalue()
        assert "✗" in output2

    def test_render_log_setter_format(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "party_size", "value": "4"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "party_size" in output
        assert '= "4"' in output

    def test_render_log_steer_back_format(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "steer_back_soft", "level": "INFO",
             "data": {"turns": 3}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "Soft redirect" in output
        assert "STEER" in output

    def test_render_log_correction_format(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "correction_applied", "level": "INFO",
             "data": {"slot": "date", "old": "Friday", "new": "Saturday"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "date" in output
        assert "Friday" in output
        assert "Saturday" in output
        assert "→" in output  # → arrow

    def test_render_log_flow_events(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "bootstrap_transfer", "level": "INFO",
             "data": {"agent": "Reservation_Agent"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "Transfer" in output
        assert "Reservation_Agent" in output
        assert "FLOW" in output

    def test_render_log_error_styling(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "slot_error", "level": "WARN",
             "data": {"slot": "date", "code": "INVALID", "retries": 2}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "ERROR" in output

    def test_render_log_invalid_level_defaults_info(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "x", "value": "1"}},
        ]
        # Invalid level string should default to INFO behavior (no crash)
        renderer.render_log(entries, min_level="BOGUS")
        output = buf.getvalue()
        assert "Conversation Timeline" in output
        assert "SET" in output

    def test_render_log_dedup_repeated_config_loaded(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "bella_notte", "n_slots": 6, "n_tasks": 2}},
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "party_size", "value": "4"}},
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "bella_notte", "n_slots": 6, "n_tasks": 2}},
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "bella_notte", "n_slots": 6, "n_tasks": 2}},
            {"tag": "setter_stored", "level": "INFO",
             "data": {"slot": "date", "value": "Friday"}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert output.count("bella_notte") == 1
        assert "party_size" in output
        assert "date" in output

    def test_render_log_dedup_different_config_shown(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        entries = [
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "reservation", "n_slots": 6, "n_tasks": 2}},
            {"tag": "config_loaded", "level": "INFO",
             "data": {"config_id": "takeout", "n_slots": 4, "n_tasks": 1}},
        ]
        renderer.render_log(entries)
        output = buf.getvalue()
        assert "reservation" in output
        assert "takeout" in output


class TestRenderSlots:
    def test_render_slots_full(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {
                "phase": "collection",
                "status": "in_progress",
                "filled_count": 2,
                "pending_count": 1,
                "deferred_count": 0,
                "retries": {},
                "steer_back_turns": 0,
                "config_id": "bella_notte",
            },
            "categories": {
                "core_data": {
                    "label": "Core Data",
                    "fields": {
                        "filled": {"party_size": "4", "date": "Friday"},
                        "pending": {"time": "7pm"},
                        "status": "in_progress",
                    },
                },
            },
            "slot_dag": {
                "filled": ["date", "party_size"],
                "pending": ["time"],
                "deferred": [],
                "blocked": [],
            },
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "collection" in output
        assert "Slots" in output
        assert "party_size" in output
        assert "date" in output
        assert "filled" in output
        assert "pending" in output

    def test_render_slots_with_blocked(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": None,
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": None},
            "categories": {},
            "slot_dag": {
                "filled": [],
                "pending": [],
                "deferred": [],
                "blocked": [{"slot": "phone", "blocked_by": ["party_size"]}],
            },
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "phone" in output
        assert "party_size" in output
        assert "blocked" in output

    def test_render_slots_category_filter(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": None,
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": None},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {"filled": {}}},
                "engine_control": {"label": "Engine Control", "fields": {"_invoke_n": 3}},
            },
            "slot_dag": {"filled": [], "pending": [], "deferred": [], "blocked": []},
        }
        renderer.render_slots(inspection, category="core_data")
        output = buf.getvalue()
        assert "Core Data" in output
        assert "Engine Control" not in output

    def test_render_slots_invalid_category(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": None,
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": None},
            "categories": {"core_data": {"label": "Core Data", "fields": {}}},
            "slot_dag": {"filled": [], "pending": [], "deferred": [], "blocked": []},
        }
        renderer.render_slots(inspection, category="nonexistent")
        output = buf.getvalue()
        assert "Unknown category" in output

    def test_render_slots_collapsed_categories_shown_as_hint(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": None,
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": None},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {"filled": {}}},
                "configuration": {"label": "Configuration", "fields": {"_config_id": "res"}},
                "engine_control": {"label": "Engine Control", "fields": {"_invoke_n": 3}},
            },
            "slot_dag": {"filled": [], "pending": [], "deferred": [], "blocked": []},
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "/slots configuration" in output
        assert "/slots engine_control" in output
        assert "Configuration" not in output or "Collapsed" in output

    def test_render_slots_flags_shown(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "correction", "status": "in_progress",
                        "filled_count": 1, "pending_count": 0,
                        "deferred_count": 0, "retries": {"set_date": 2},
                        "steer_back_turns": 3, "config_id": "res"},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {
                    "filled": {"party_size": "4"}, "pending": {},
                }},
                "confirmation": {"label": "Confirmation", "fields": {
                    "_correction_pending": True,
                }},
            },
            "slot_dag": {"filled": ["party_size"], "pending": [], "deferred": [], "blocked": []},
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "steer_back=3" in output
        assert "retry(set_date)=2" in output
        assert "correction_pending" in output

    def test_render_slots_suspended_flows(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 1, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "takeout",
                        "suspended_flows": [
                            {"flow": "reservation",
                             "slots": ["date", "time"],
                             "deferred": ["guest_name"]},
                        ],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {
                    "filled": {"pickup_time": "6pm"}, "pending": {},
                }},
            },
            "slot_dag": {"filled": ["pickup_time"], "pending": [],
                         "deferred": [], "blocked": [],
                         "shared_slots": []},
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "Suspended Flows" in output
        assert "reservation" in output
        assert "date" in output
        assert "suspended" in output.lower()

    def test_render_slots_shared_annotation(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 2, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "res",
                        "suspended_flows": [],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {
                    "filled": {"party_size": "4", "guest_name": "Alice"},
                    "pending": {},
                }},
            },
            "slot_dag": {"filled": ["guest_name", "party_size"],
                         "pending": [], "deferred": [], "blocked": [],
                         "meta_slots": [], "flow_slots": [],
                         "shared_slots": ["guest_name"]},
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "shared across flows" in output
        assert "*" in output

    def test_render_slots_restored_flow_header(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 0, "pending_count": 1,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "res",
                        "suspended_flows": [],
                        "restored_flow": True,
                        "auto_resume_deferred": False},
            "categories": {
                "core_data": {"label": "Core Data", "fields": {
                    "filled": {}, "pending": {"date": "Friday"},
                }},
            },
            "slot_dag": {"filled": [], "pending": ["date"],
                         "deferred": [], "blocked": [],
                         "shared_slots": []},
        }
        renderer.render_slots(inspection)
        output = buf.getvalue()
        assert "restored" in output.lower()

    def test_render_slots_flows_list(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 2, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "bella_notte",
                        "suspended_flows": [
                            {"flow": "takeout", "slots": ["pickup_time"],
                             "pending": [], "deferred": [],
                             "slot_values": {"pickup_time": "6pm"},
                             "pending_values": {}, "deferred_values": {}},
                        ],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {},
            "slot_dag": {"filled": [], "pending": [], "deferred": [],
                         "blocked": [], "shared_slots": []},
        }
        flow_context = {
            "active_config_id": "bella_notte",
            "agent_config_map": {
                "Reservation_Agent": "bella_notte",
                "Takeout_Agent": "takeout",
            },
        }
        renderer.render_slots(inspection, category="flows",
                              flow_context=flow_context)
        output = buf.getvalue()
        assert "Flows" in output
        assert "Reservation_Agent" in output
        assert "Takeout_Agent" in output
        assert "active" in output
        assert "suspended" in output

    def test_render_slots_flow_detail(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 1, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "takeout",
                        "suspended_flows": [
                            {"flow": "bella_notte", "slots": ["date", "time"],
                             "pending": ["guest_name"], "deferred": [],
                             "slot_values": {"date": "Friday", "time": "7pm"},
                             "pending_values": {"guest_name": "Alice"},
                             "deferred_values": {}},
                        ],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {},
            "slot_dag": {"filled": [], "pending": [], "deferred": [],
                         "blocked": [], "shared_slots": ["guest_name"]},
        }
        renderer.render_slots(inspection, category="flow:bella_notte")
        output = buf.getvalue()
        assert "bella_notte" in output
        assert "Friday" in output
        assert "7pm" in output
        assert "Alice" in output
        assert "saved" in output
        assert "pending" in output

    def test_render_slots_flow_detail_not_found(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "res",
                        "suspended_flows": [],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {},
            "slot_dag": {"filled": [], "pending": [], "deferred": [],
                         "blocked": [], "shared_slots": []},
        }
        renderer.render_slots(inspection, category="flow:nonexistent")
        output = buf.getvalue()
        assert "No suspended flow" in output

    def test_render_slots_flow_detail_shared_annotation(self):
        console, buf = _make_console()
        renderer = ChatRenderer(console=console)
        inspection = {
            "summary": {"phase": "collection", "status": "in_progress",
                        "filled_count": 0, "pending_count": 0,
                        "deferred_count": 0, "retries": {},
                        "steer_back_turns": 0, "config_id": "takeout",
                        "suspended_flows": [
                            {"flow": "bella_notte",
                             "slots": ["date", "guest_name"],
                             "pending": [], "deferred": [],
                             "slot_values": {"date": "Fri", "guest_name": "Al"},
                             "pending_values": {}, "deferred_values": {}},
                        ],
                        "restored_flow": False,
                        "auto_resume_deferred": False},
            "categories": {},
            "slot_dag": {"filled": [], "pending": [], "deferred": [],
                         "blocked": [], "shared_slots": ["guest_name"]},
        }
        renderer.render_slots(inspection, category="flow:bella_notte")
        output = buf.getvalue()
        assert "shared across flows" in output
