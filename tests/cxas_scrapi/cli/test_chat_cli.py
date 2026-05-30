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

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli import chat_cli
from cxas_scrapi.utils.tracing.trace_config import (
    ChatConfig,
    TraceConfig,
)

APP = "projects/p/locations/l/apps/a"


def _ns(**overrides):
    """Build a minimal argparse.Namespace for chat commands."""
    base = dict(
        app_name=APP,
        channel=None,
        deployment_id=None,
        verbose=False,
        trace=False,
        trace_format="text",
        metrics=False,
        script=None,
        fork=None,
        fork_at_turn=None,
        config=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# -----------------------------------------------------------------------
# Registration / argparse tests
# -----------------------------------------------------------------------


def test_register_smoke():
    """Verify `cxas chat --app-name APP` parses correctly."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_cli.register(sub)
    args = parser.parse_args(["chat", "--app-name", APP])
    assert args.app_name == APP
    assert hasattr(args, "func")


def test_register_all_flags():
    """Verify all flags parse without errors."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_cli.register(sub)
    args = parser.parse_args(
        [
            "chat",
            "--app-name",
            APP,
            "--channel",
            "web",
            "--deployment-id",
            "dep-1",
            "--verbose",
            "--trace",
            "--trace-format",
            "json",
            "--metrics",
            "--script",
            "/tmp/script.yaml",
            "--fork",
            "conv-123",
            "--fork-at-turn",
            "3",
            "--config",
            "/tmp/trace.yaml",
        ]
    )
    assert args.app_name == APP
    assert args.channel == "web"
    assert args.deployment_id == "dep-1"
    assert args.verbose is True
    assert args.trace is True
    assert args.trace_format == "json"
    assert args.metrics is True
    assert args.script == "/tmp/script.yaml"
    assert args.fork == "conv-123"
    assert args.fork_at_turn == 3
    assert args.config == "/tmp/trace.yaml"


# -----------------------------------------------------------------------
# Slash command tests
# -----------------------------------------------------------------------


def test_slash_command_help():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/help", session, renderer, _ns())
    assert result is True
    renderer.render_slash_help.assert_called_once()


def test_slash_command_state():
    session = MagicMock()
    session.get_state.return_value = {"active_agent": "Agent1"}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/state", session, renderer, _ns())
    assert result is True
    session.get_state.assert_called_once()
    renderer.render_state.assert_called_once_with({"active_agent": "Agent1"})


def test_slash_command_trace():
    session = MagicMock()
    session.get_trace.return_value = "trace output"
    renderer = MagicMock()
    result = chat_cli._handle_slash_command(
        "/trace", session, renderer, _ns(trace_format="json")
    )
    assert result is True
    session.get_trace.assert_called_once_with(fmt="json")


def test_slash_command_quit():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/quit", session, renderer, _ns())
    assert result is False


def test_slash_command_save(tmp_path):
    session = MagicMock()
    session.export_turns_summary.return_value = [
        {"turn": 0, "user": "hi", "agent": "hello"}
    ]
    renderer = MagicMock()
    save_path = str(tmp_path / "conv.json")
    result = chat_cli._handle_slash_command(
        f"/save {save_path}", session, renderer, _ns()
    )
    assert result is True
    session.export_turns_summary.assert_called_once()
    with open(save_path) as f:
        data = json.load(f)
    assert data == [{"turn": 0, "user": "hi", "agent": "hello"}]


def test_slash_command_unknown():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command(
        "/unknown", session, renderer, _ns()
    )
    assert result is True
    renderer.render_error.assert_called_once()


def test_slash_command_bug_no_reason():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/bug", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()


def test_slash_command_save_no_path():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/save", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()


def test_slash_command_clear():
    session = MagicMock()
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/clear", session, renderer, _ns())
    assert result is True
    renderer.console.clear.assert_called_once()


@patch.object(chat_cli, "_paged")
def test_state_uses_pager(mock_paged):
    session = MagicMock()
    session.get_state.return_value = {"active_agent": "Agent1"}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/state", session, renderer, _ns())
    assert result is True
    mock_paged.assert_called_once()


@patch("shutil.which", return_value=None)
def test_paged_falls_back_without_less(mock_which):
    renderer = MagicMock()
    fn = MagicMock()
    chat_cli._paged(renderer, fn)
    fn.assert_called_once()


# -----------------------------------------------------------------------
# chat_interactive tests
# -----------------------------------------------------------------------


@patch.object(chat_cli, "ChatSession")
@patch.object(chat_cli, "ChatRenderer")
@patch("builtins.input")
def test_chat_interactive_basic(mock_input, mock_renderer_cls, mock_session_cls):
    """Mock input to return 'hello' then '/quit'; verify send called."""
    mock_input.side_effect = ["hello", "/quit"]

    mock_session = MagicMock()
    mock_session.session_id = "sess-123"
    mock_session.turns = []
    mock_session.is_ended = False
    mock_session_cls.return_value = mock_session

    mock_renderer = MagicMock()
    mock_renderer_cls.return_value = mock_renderer

    # After send(), add a turn to the list so len(turns) is correct
    def _send_side_effect(text):
        turn = MagicMock()
        turn.session_ended = False
        mock_session.turns.append(turn)
        return turn

    mock_session.send.side_effect = _send_side_effect

    chat_cli.chat_interactive(_ns())

    mock_session.send.assert_called_once_with("hello")
    mock_renderer.render_turn.assert_called_once()
    mock_renderer.render_session_end.assert_called_once()


@patch.object(chat_cli, "ChatSession")
@patch.object(chat_cli, "ChatRenderer")
@patch("builtins.input")
def test_chat_interactive_session_end(
    mock_input, mock_renderer_cls, mock_session_cls
):
    """Mock session that returns is_ended=True after first send."""
    mock_input.side_effect = ["hello", "should not reach this"]

    mock_session = MagicMock()
    mock_session.session_id = "sess-456"
    mock_session.turns = []

    def _send_side_effect(text):
        turn = MagicMock()
        turn.session_ended = True
        mock_session.turns.append(turn)
        mock_session.is_ended = True
        return turn

    mock_session.send.side_effect = _send_side_effect
    mock_session.is_ended = False
    mock_session_cls.return_value = mock_session

    mock_renderer = MagicMock()
    mock_renderer_cls.return_value = mock_renderer

    chat_cli.chat_interactive(_ns())

    # Only one send call because session ended
    mock_session.send.assert_called_once_with("hello")
    mock_renderer.render_session_end.assert_called_once()


@patch.object(chat_cli, "ChatSession")
@patch.object(chat_cli, "ChatRenderer")
@patch("builtins.input")
def test_chat_interactive_keyboard_interrupt(
    mock_input, mock_renderer_cls, mock_session_cls
):
    """Verify KeyboardInterrupt exits gracefully."""
    mock_input.side_effect = KeyboardInterrupt

    mock_session = MagicMock()
    mock_session.session_id = "sess-789"
    mock_session.turns = []
    mock_session_cls.return_value = mock_session

    mock_renderer = MagicMock()
    mock_renderer_cls.return_value = mock_renderer

    # Should not raise
    chat_cli.chat_interactive(_ns())
    mock_renderer.render_session_end.assert_called_once()


@patch.object(chat_cli, "ChatSession")
@patch.object(chat_cli, "ChatRenderer")
@patch("builtins.input")
def test_chat_interactive_auto_trace(
    mock_input, mock_renderer_cls, mock_session_cls
):
    """Verify --trace flag triggers get_trace on exit."""
    mock_input.side_effect = ["/quit"]

    mock_session = MagicMock()
    mock_session.session_id = "sess-trace"
    mock_session.turns = []
    mock_session.get_trace.return_value = "trace data"
    mock_session_cls.return_value = mock_session

    mock_renderer = MagicMock()
    mock_renderer_cls.return_value = mock_renderer

    chat_cli.chat_interactive(_ns(trace=True, trace_format="md"))

    mock_session.get_trace.assert_called_once_with(fmt="md")


# -----------------------------------------------------------------------
# ChatConfig / TraceConfig tests
# -----------------------------------------------------------------------


def test_chat_config_defaults():
    cfg = ChatConfig()
    assert cfg.auto_trace is False
    assert cfg.auto_trace_format == "text"
    assert cfg.verbose is False
    assert cfg.show_metrics is False
    assert cfg.save_session is True
    assert cfg.latency_warning_ms == 5000


def test_chat_config_in_trace_config():
    cfg = TraceConfig()
    assert isinstance(cfg.chat, ChatConfig)
    assert cfg.chat.auto_trace is False


def test_chat_config_override():
    cfg = ChatConfig(auto_trace=True, verbose=True, latency_warning_ms=3000)
    assert cfg.auto_trace is True
    assert cfg.verbose is True
    assert cfg.latency_warning_ms == 3000


# -----------------------------------------------------------------------
# SLASH_COMMANDS dict test
# -----------------------------------------------------------------------


def test_slash_commands_dict():
    assert "/help" in chat_cli.SLASH_COMMANDS
    assert "/trace" in chat_cli.SLASH_COMMANDS
    assert "/state" in chat_cli.SLASH_COMMANDS
    assert "/slots" in chat_cli.SLASH_COMMANDS
    assert "/clear" in chat_cli.SLASH_COMMANDS
    assert "/quit" in chat_cli.SLASH_COMMANDS
    assert "/save <path>" in chat_cli.SLASH_COMMANDS
    assert "/bug <reason>" in chat_cli.SLASH_COMMANDS


def test_slash_command_slots_no_turns():
    session = MagicMock()
    session.turns = []
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/slots", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()
    assert "Send at least one message" in renderer.render_error.call_args[0][0]


def test_slash_command_slots():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = {
        "filled": {"party_size": "4"},
        "pending": {"preferred_date": "Friday"},
        "status": "in_progress",
    }
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/slots", session, renderer, _ns())
    assert result is True
    session.get_slot_machine.assert_called_once()
    renderer.render_slots.assert_called_once()
    inspection = renderer.render_slots.call_args[0][0]
    assert inspection["summary"]["filled_count"] == 1
    assert inspection["summary"]["pending_count"] == 1


def test_slash_command_slots_with_category():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = {
        "filled": {"party_size": "4"},
        "pending": {},
        "status": "in_progress",
    }
    renderer = MagicMock()
    result = chat_cli._handle_slash_command(
        "/slots core_data", session, renderer, _ns()
    )
    assert result is True
    renderer.render_slots.assert_called_once()
    assert renderer.render_slots.call_args[1]["category"] == "core_data"


def test_slash_command_slots_no_sm():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = {}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/slots", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()


# -----------------------------------------------------------------------
# /log slash command tests
# -----------------------------------------------------------------------


def test_log_no_turns():
    session = MagicMock()
    session.turns = []
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/log", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()
    assert "Send at least one message" in renderer.render_error.call_args[0][0]


def test_log_no_slot_machine():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = None
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/log", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()
    assert "No slot machine state" in renderer.render_error.call_args[0][0]


def test_log_no_log_entries():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = {"_log": []}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/log", session, renderer, _ns())
    assert result is True
    renderer.render_error.assert_called_once()
    assert "No log entries" in renderer.render_error.call_args[0][0]


def test_log_dispatches_to_renderer():
    session = MagicMock()
    session.turns = [MagicMock()]
    log_entries = [
        {"level": "INFO", "message": "slot filled", "ts": "12:00"},
        {"level": "DEBUG", "message": "evaluating DAG", "ts": "12:01"},
    ]
    session.get_slot_machine.return_value = {"_log": log_entries}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command("/log", session, renderer, _ns())
    assert result is True
    renderer.render_log.assert_called_once_with(log_entries, min_level="INFO")


def test_log_invalid_level():
    session = MagicMock()
    session.turns = [MagicMock()]
    session.get_slot_machine.return_value = {
        "_log": [{"level": "INFO", "message": "x"}]
    }
    renderer = MagicMock()
    result = chat_cli._handle_slash_command(
        "/log foobar", session, renderer, _ns()
    )
    assert result is True
    renderer.render_error.assert_called_once()
    assert "Unknown level" in renderer.render_error.call_args[0][0]


@pytest.mark.parametrize("level_input,expected", [
    ("debug", "DEBUG"),
    ("WARN", "WARN"),
    ("info", "INFO"),
    ("Error", "ERROR"),
])
def test_log_valid_levels(level_input, expected):
    session = MagicMock()
    session.turns = [MagicMock()]
    log_entries = [{"level": "DEBUG", "message": "test"}]
    session.get_slot_machine.return_value = {"_log": log_entries}
    renderer = MagicMock()
    result = chat_cli._handle_slash_command(
        f"/log {level_input}", session, renderer, _ns()
    )
    assert result is True
    renderer.render_log.assert_called_once_with(log_entries, min_level=expected)


# -----------------------------------------------------------------------
# readline setup tests
# -----------------------------------------------------------------------


@patch("cxas_scrapi.cli.chat_cli.readline")
def test_setup_readline_reads_history(mock_readline):
    chat_cli._setup_readline()
    mock_readline.set_history_length.assert_called_once_with(1000)
    mock_readline.read_history_file.assert_called_once_with(
        chat_cli._HISTORY_FILE
    )


@patch("cxas_scrapi.cli.chat_cli.readline")
def test_setup_readline_handles_missing_file(mock_readline):
    mock_readline.read_history_file.side_effect = FileNotFoundError
    # Should not raise
    chat_cli._setup_readline()
    mock_readline.set_history_length.assert_called_once_with(1000)


@patch("cxas_scrapi.cli.chat_cli.readline")
def test_setup_readline_sets_completer(mock_readline):
    chat_cli._setup_readline()
    mock_readline.set_completer.assert_called_once_with(chat_cli._slash_completer)
    mock_readline.set_completer_delims.assert_called_once_with(" \t\n")
    mock_readline.parse_and_bind.assert_called_once_with("tab: complete")


def test_completable_commands_match_slash_commands():
    """Every completable command should derive from a SLASH_COMMANDS key."""
    base_commands = {k.split()[0] for k in chat_cli.SLASH_COMMANDS}
    for cmd in chat_cli._COMPLETABLE_COMMANDS:
        assert cmd in base_commands


def test_completable_commands_are_base_commands():
    """Completable commands are the unique base words from SLASH_COMMANDS."""
    for cmd in chat_cli._COMPLETABLE_COMMANDS:
        assert " " not in cmd
        assert cmd.startswith("/")


def test_slash_completer_returns_matches():
    assert chat_cli._slash_completer("/h", 0) == "/help"
    assert chat_cli._slash_completer("/h", 1) is None


def test_slash_completer_returns_none_without_slash():
    assert chat_cli._slash_completer("help", 0) is None


def test_slash_completer_returns_multiple_matches():
    results = []
    for i in range(10):
        r = chat_cli._slash_completer("/s", i)
        if r is None:
            break
        results.append(r)
    assert "/save" in results
    assert "/slots" in results
    assert "/state" in results
