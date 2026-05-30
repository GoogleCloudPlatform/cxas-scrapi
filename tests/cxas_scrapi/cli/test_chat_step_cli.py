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

"""Tests for the chat-step CLI module."""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli import chat_step_cli

APP = "projects/p/locations/l/apps/a"


def _ns(**overrides):
    """Build a minimal argparse.Namespace for chat-step commands."""
    base = dict(
        app_name=APP,
        message="Hello",
        session_file=None,
        channel=None,
        deployment_id=None,
        with_trace=False,
        with_metrics=False,
        with_slots=False,
        with_log=None,
        with_flow_context=False,
        with_trace_report=None,
        with_turns=False,
        bug=None,
        inspect_only=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _step_result(**overrides):
    """Build a mock step() return value."""
    base = {
        "turn_index": 0,
        "user_text": "Hello",
        "agent_text": "Hi there!",
        "tool_calls": [],
        "tool_responses": [],
        "agent_transfer": None,
        "session_ended": False,
        "state": {
            "active_agent": None,
            "slot_machine": {},
            "filled_slots": {},
            "session_ended": False,
            "turn_count": 1,
            "pending_transfer": None,
        },
        "session_id": "test-session-id",
        "trace": None,
        "metrics": None,
    }
    base.update(overrides)
    return base


# -----------------------------------------------------------------------
# Registration / argparse tests
# -----------------------------------------------------------------------


def test_register_smoke():
    """Verify `cxas chat-step --app-name APP -m "hi"` parses correctly."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_step_cli.register(sub)
    args = parser.parse_args(["chat-step", "--app-name", APP, "-m", "hi"])
    assert args.app_name == APP
    assert args.message == "hi"
    assert hasattr(args, "func")


def test_register_all_flags():
    """Verify all flags parse without errors."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_step_cli.register(sub)
    args = parser.parse_args(
        [
            "chat-step",
            "--app-name",
            APP,
            "--message",
            "test message",
            "--session-file",
            "/tmp/session.json",
            "--channel",
            "web",
            "--deployment-id",
            "dep-1",
            "--with-trace",
            "--with-metrics",
            "--with-slots",
        ]
    )
    assert args.app_name == APP
    assert args.message == "test message"
    assert args.session_file == "/tmp/session.json"
    assert args.channel == "web"
    assert args.deployment_id == "dep-1"
    assert args.with_trace is True
    assert args.with_metrics is True
    assert args.with_slots is True


# -----------------------------------------------------------------------
# chat_step() handler tests
# -----------------------------------------------------------------------


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_basic(MockDriver, capsys):
    """New session sends message and prints JSON to stdout."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.is_ended = False
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns())

    mock_driver.step.assert_called_once_with("Hello")
    mock_driver.close.assert_called_once()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["agent_text"] == "Hi there!"


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_with_session_file_new(MockDriver, capsys, tmp_path):
    """When session file doesn't exist yet, creates it after the step."""
    mock_driver = MagicMock()
    mock_driver.session_id = "new-session-id"
    mock_driver.step.return_value = _step_result(session_id="new-session-id")
    MockDriver.return_value = mock_driver

    session_file = str(tmp_path / "session.json")
    chat_step_cli.chat_step(_ns(session_file=session_file))

    with open(session_file) as f:
        saved = json.load(f)
    assert saved["session_id"] == "new-session-id"
    assert saved["turn_count"] == 1
    assert saved["app_name"] == APP


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_with_session_file_resume(MockDriver, capsys, tmp_path):
    """Existing session file passes session_id and turn_count to driver."""
    session_file = str(tmp_path / "session.json")
    with open(session_file, "w") as f:
        json.dump({"session_id": "existing-session", "turn_count": 3, "app_name": APP}, f)

    mock_driver = MagicMock()
    mock_driver.session_id = "existing-session"
    mock_driver.step.return_value = _step_result(
        turn_index=3,
        state={
            "active_agent": None,
            "slot_machine": {},
            "filled_slots": {},
            "session_ended": False,
            "turn_count": 4,
            "pending_transfer": None,
        },
    )
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(session_file=session_file))

    MockDriver.assert_called_once_with(
        app_name=APP,
        channel=None,
        deployment_id=None,
        include_trace=False,
        include_metrics=False,
        session_id="existing-session",
        initial_turn_count=3,
        initial_variable_state=None,
    )


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_with_trace(MockDriver, capsys):
    """--with-trace passes include_trace=True to driver."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result(trace={"spans": []})
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_trace=True))

    MockDriver.assert_called_once_with(
        app_name=APP,
        channel=None,
        deployment_id=None,
        include_trace=True,
        include_metrics=False,
        session_id=None,
        initial_turn_count=0,
        initial_variable_state=None,
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["trace"] == {"spans": []}


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_with_metrics(MockDriver, capsys):
    """--with-metrics passes include_metrics=True to driver."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result(metrics={"latency_ms": 150})
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_metrics=True))

    MockDriver.assert_called_once_with(
        app_name=APP,
        channel=None,
        deployment_id=None,
        include_trace=False,
        include_metrics=True,
        session_id=None,
        initial_turn_count=0,
        initial_variable_state=None,
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["metrics"] == {"latency_ms": 150}


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_with_slots(MockDriver, capsys):
    """--with-slots calls get_full_state and adds slot_inspection to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.get_full_state.return_value = {
        "active_agent": None,
        "slot_machine": {},
        "filled_slots": {"name": "Alice"},
        "session_ended": False,
        "turn_count": 1,
        "pending_transfer": None,
        "slot_inspection": {"name": {"value": "Alice", "source": "user"}},
    }
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_slots=True))

    mock_driver.get_full_state.assert_called_once()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["slot_inspection"] == {"name": {"value": "Alice", "source": "user"}}


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_session_file_updated(MockDriver, capsys, tmp_path):
    """Session file is written with correct turn_count after step."""
    session_file = str(tmp_path / "session.json")

    mock_driver = MagicMock()
    mock_driver.session_id = "sess-42"
    mock_driver.step.return_value = _step_result(
        state={
            "active_agent": None,
            "slot_machine": {},
            "filled_slots": {},
            "session_ended": False,
            "turn_count": 5,
            "pending_transfer": None,
        },
    )
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(session_file=session_file))

    with open(session_file) as f:
        saved = json.load(f)
    assert saved["session_id"] == "sess-42"
    assert saved["turn_count"] == 5
    assert saved["app_name"] == APP


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_driver_error(MockDriver, capsys):
    """driver.step() raising prints error to stderr and exits with 1."""
    mock_driver = MagicMock()
    mock_driver.step.side_effect = RuntimeError("connection lost")
    MockDriver.return_value = mock_driver

    with pytest.raises(SystemExit) as exc_info:
        chat_step_cli.chat_step(_ns())

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Chat step failed: connection lost" in captured.err
    mock_driver.close.assert_called_once()


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_session_file_corrupt(MockDriver, capsys, tmp_path):
    """Corrupt session file prints error to stderr and exits with 1."""
    session_file = str(tmp_path / "session.json")
    with open(session_file, "w") as f:
        f.write("{not valid json")

    with pytest.raises(SystemExit) as exc_info:
        chat_step_cli.chat_step(_ns(session_file=session_file))

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Failed to load session file" in captured.err
    MockDriver.assert_not_called()


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_json_output_valid(MockDriver, capsys):
    """Stdout is valid JSON with all expected keys."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns())

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    expected_keys = {
        "turn_index", "user_text", "agent_text", "tool_calls",
        "tool_responses", "agent_transfer", "session_ended",
        "state", "session_id", "trace", "metrics",
    }
    assert expected_keys == set(output.keys())
    assert isinstance(output["state"], dict)
    assert "turn_count" in output["state"]


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_chat_step_driver_creation_error(MockDriver, capsys):
    """Driver constructor raising prints error and exits with 1."""
    MockDriver.side_effect = ValueError("invalid app name")

    with pytest.raises(SystemExit) as exc_info:
        chat_step_cli.chat_step(_ns())

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Failed to create session: invalid app name" in captured.err


# -----------------------------------------------------------------------
# New flag tests
# -----------------------------------------------------------------------


def test_register_new_flags():
    """All new flags parse without errors."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_step_cli.register(sub)
    args = parser.parse_args([
        "chat-step", "--app-name", APP, "-m", "test",
        "--with-log", "debug",
        "--with-flow-context",
        "--with-trace-report", "md",
        "--with-turns",
        "--bug", "something broke",
    ])
    assert args.with_log == "debug"
    assert args.with_flow_context is True
    assert args.with_trace_report == "md"
    assert args.with_turns is True
    assert args.bug == "something broke"


def test_register_with_log_default_level():
    """--with-log alone defaults to INFO."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_step_cli.register(sub)
    args = parser.parse_args([
        "chat-step", "--app-name", APP, "-m", "test", "--with-log",
    ])
    assert args.with_log == "INFO"


def test_register_inspect_only():
    """--inspect-only makes --message optional."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_step_cli.register(sub)
    args = parser.parse_args([
        "chat-step", "--app-name", APP, "--inspect-only",
    ])
    assert args.inspect_only is True
    assert args.message is None


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_with_log_enriches(MockDriver, capsys):
    """--with-log adds sm_log to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.get_sm_log.return_value = [{"tag": "config_loaded", "level": "INFO"}]
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_log="INFO"))

    mock_driver.get_sm_log.assert_called_once_with("INFO")
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["sm_log"] == [{"tag": "config_loaded", "level": "INFO"}]


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_with_flow_context_enriches(MockDriver, capsys):
    """--with-flow-context adds flow_context to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.get_flow_context.return_value = {
        "active_config_id": "reservation",
        "agent_config_map": {},
        "active_sm_key": "sm",
    }
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_flow_context=True))

    mock_driver.get_flow_context.assert_called_once()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["flow_context"]["active_config_id"] == "reservation"


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_with_trace_report_enriches(MockDriver, capsys):
    """--with-trace-report adds trace_report to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.get_trace_report.return_value = "# Trace Report\n..."
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_trace_report="md"))

    mock_driver.get_trace_report.assert_called_once_with("md")
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["trace_report"] == "# Trace Report\n..."


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_with_turns_enriches(MockDriver, capsys):
    """--with-turns adds turns_summary to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.get_turns_summary.return_value = [
        {"turn": 0, "user": "Hello", "agent": "Hi!"},
    ]
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(with_turns=True))

    mock_driver.get_turns_summary.assert_called_once()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert len(output["turns_summary"]) == 1


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_bug_flag_enriches(MockDriver, capsys):
    """--bug adds bug_report to output."""
    mock_driver = MagicMock()
    mock_driver.session_id = "test-session-id"
    mock_driver.step.return_value = _step_result()
    mock_driver.report_bug.return_value = {"status": "reported", "id": "bug-123"}
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(bug="something broke"))

    mock_driver.report_bug.assert_called_once_with("something broke")
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["bug_report"]["status"] == "reported"


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_inspect_only_no_step(MockDriver, capsys, tmp_path):
    """--inspect-only doesn't call driver.step()."""
    session_file = str(tmp_path / "session.json")
    with open(session_file, "w") as f:
        json.dump({"session_id": "existing-sess", "turn_count": 2, "app_name": APP}, f)

    mock_driver = MagicMock()
    mock_driver.session_id = "existing-sess"
    mock_driver.get_full_state.return_value = {
        "active_agent": None,
        "slot_machine": {},
        "filled_slots": {},
        "session_ended": False,
        "turn_count": 2,
        "pending_transfer": None,
    }
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(
        inspect_only=True,
        message=None,
        session_file=session_file,
    ))

    mock_driver.step.assert_not_called()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["inspect_only"] is True
    assert output["session_id"] == "existing-sess"


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_inspect_only_requires_session_file(MockDriver, capsys):
    """--inspect-only without session file errors."""
    with pytest.raises(SystemExit) as exc_info:
        chat_step_cli.chat_step(_ns(inspect_only=True, message=None))
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--inspect-only requires --session-file" in captured.err


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_inspect_only_with_enrichments(MockDriver, capsys, tmp_path):
    """--inspect-only works with --with-* flags."""
    session_file = str(tmp_path / "session.json")
    with open(session_file, "w") as f:
        json.dump({"session_id": "existing-sess", "turn_count": 2, "app_name": APP}, f)

    mock_driver = MagicMock()
    mock_driver.session_id = "existing-sess"
    mock_driver.get_full_state.return_value = {"slot_machine": {}, "filled_slots": {}}
    mock_driver.get_sm_log.return_value = [{"tag": "test"}]
    mock_driver.get_flow_context.return_value = {"active_config_id": "test"}
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(
        inspect_only=True,
        message=None,
        session_file=session_file,
        with_log="INFO",
        with_flow_context=True,
    ))

    mock_driver.step.assert_not_called()
    mock_driver.get_sm_log.assert_called_once()
    mock_driver.get_flow_context.assert_called_once()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "sm_log" in output
    assert "flow_context" in output


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_variable_state_persisted(MockDriver, capsys, tmp_path):
    """Session file includes variable_state after a step."""
    session_file = str(tmp_path / "session.json")
    mock_driver = MagicMock()
    mock_driver.session_id = "sess-42"
    mock_driver.step.return_value = _step_result(
        state={"turn_count": 1, "active_agent": None, "slot_machine": {}, "filled_slots": {}, "session_ended": False, "pending_transfer": None},
    )
    mock_driver._session._variable_state = {"sm": {"filled": {"name": "Alice"}}}
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(session_file=session_file))

    with open(session_file) as f:
        saved = json.load(f)
    assert "variable_state" in saved
    assert saved["variable_state"]["sm"]["filled"]["name"] == "Alice"


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_variable_state_loaded(MockDriver, capsys, tmp_path):
    """variable_state from session file is passed to driver."""
    session_file = str(tmp_path / "session.json")
    var_state = {"sm": {"filled": {"name": "Bob"}, "_log": []}}
    with open(session_file, "w") as f:
        json.dump({
            "session_id": "existing-sess",
            "turn_count": 1,
            "app_name": APP,
            "variable_state": var_state,
        }, f)

    mock_driver = MagicMock()
    mock_driver.session_id = "existing-sess"
    mock_driver.step.return_value = _step_result(
        state={"turn_count": 2, "active_agent": None, "slot_machine": {}, "filled_slots": {}, "session_ended": False, "pending_transfer": None},
    )
    mock_driver._session._variable_state = var_state
    MockDriver.return_value = mock_driver

    chat_step_cli.chat_step(_ns(session_file=session_file))

    MockDriver.assert_called_once_with(
        app_name=APP,
        channel=None,
        deployment_id=None,
        include_trace=False,
        include_metrics=False,
        session_id="existing-sess",
        initial_turn_count=1,
        initial_variable_state=var_state,
    )


@patch.object(chat_step_cli, "ProgrammaticChatDriver")
def test_message_required_without_inspect_only(MockDriver, capsys):
    """Without --inspect-only, missing --message is an error."""
    with pytest.raises(SystemExit) as exc_info:
        chat_step_cli.chat_step(_ns(message=None))
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--message is required" in captured.err
