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

"""Tests for the ScriptRunner module."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils.script_runner import (
    ConversationScript,
    ScriptResult,
    ScriptRunner,
    ScriptTurn,
    TurnExpectation,
    TurnResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn_record(
    turn_index: int = 0,
    user_text: str = "",
    agent_text: str = "",
    tool_calls: list[dict] | None = None,
    agent_transfer: str | None = None,
    session_ended: bool = False,
) -> MagicMock:
    """Create a mock TurnRecord with the expected attributes."""
    tr = MagicMock()
    tr.turn_index = turn_index
    tr.user_text = user_text
    tr.agent_text = agent_text
    tr.tool_calls = tool_calls or []
    tr.agent_transfer = agent_transfer
    tr.session_ended = session_ended
    return tr


def _mock_chat_session(turns_data: list[dict]) -> MagicMock:
    """Create a mock ChatSession that returns predefined TurnRecords.

    Args:
        turns_data: List of dicts with keys matching _make_turn_record params.
            Each dict can have: user, agent, tool_calls, transfer, session_ended.
    """
    mock_session = MagicMock()
    mock_session.session_id = "test-session-id"
    mock_session.is_ended = False

    turn_records = []
    for i, data in enumerate(turns_data):
        tr = _make_turn_record(
            turn_index=i,
            user_text=data.get("user", ""),
            agent_text=data.get("agent", ""),
            tool_calls=data.get("tool_calls", []),
            agent_transfer=data.get("transfer", None),
            session_ended=data.get("session_ended", False),
        )
        turn_records.append(tr)

    # Track call count so is_ended updates after a session_ended turn
    call_count = {"n": 0}
    original_side_effect = list(turn_records)

    def send_side_effect(text):
        idx = call_count["n"]
        call_count["n"] += 1
        record = original_side_effect[idx]
        if record.session_ended:
            mock_session.is_ended = True
        return record

    mock_session.send.side_effect = send_side_effect
    return mock_session


def _write_yaml(tmp_path: Path, filename: str, content: str) -> Path:
    """Write YAML content to a temp file and return the path."""
    filepath = tmp_path / filename
    filepath.write_text(textwrap.dedent(content))
    return filepath


# ===========================================================================
# YAML Loading Tests
# ===========================================================================


class TestLoadScript:
    """Tests for ScriptRunner.load_script()."""

    def test_load_script_basic(self, tmp_path):
        """Load a basic script with name, description, and turns."""
        p = _write_yaml(
            tmp_path,
            "basic.yaml",
            """\
            name: "Basic test"
            description: "A basic conversation"
            app_name: "projects/p/locations/l/apps/a"
            channel: "web"
            turns:
              - user: "Hello"
              - user: "How are you?"
            """,
        )
        script = ScriptRunner.load_script(p)

        assert script.name == "Basic test"
        assert script.description == "A basic conversation"
        assert script.app_name == "projects/p/locations/l/apps/a"
        assert script.channel == "web"
        assert len(script.turns) == 2
        assert script.turns[0].user == "Hello"
        assert script.turns[0].expect is None
        assert script.turns[1].user == "How are you?"

    def test_load_script_with_expectations(self, tmp_path):
        """Load a script with full expectation fields."""
        p = _write_yaml(
            tmp_path,
            "expect.yaml",
            """\
            name: "Expectation test"
            turns:
              - user: "Book a table"
                expect:
                  agent_contains: "party"
                  agent_not_contains: "error"
                  tools_called: ["set_party_size", "set_date"]
                  no_transfer: true
                  session_ended: false
            """,
        )
        script = ScriptRunner.load_script(p)

        assert len(script.turns) == 1
        exp = script.turns[0].expect
        assert exp is not None
        assert exp.agent_contains == "party"
        assert exp.agent_not_contains == "error"
        assert exp.tools_called == ["set_party_size", "set_date"]
        assert exp.no_transfer is True
        assert exp.session_ended is False

    def test_load_script_minimal(self, tmp_path):
        """Load a script with only required fields (name + turns)."""
        p = _write_yaml(
            tmp_path,
            "minimal.yaml",
            """\
            name: "Minimal"
            turns:
              - user: "Hi"
            """,
        )
        script = ScriptRunner.load_script(p)

        assert script.name == "Minimal"
        assert script.description == ""
        assert script.app_name is None
        assert script.channel is None
        assert len(script.turns) == 1

    def test_load_script_missing_file(self):
        """Verify FileNotFoundError for nonexistent file."""
        with pytest.raises(FileNotFoundError, match="Script file not found"):
            ScriptRunner.load_script("/nonexistent/path/script.yaml")

    def test_load_script_invalid_yaml(self, tmp_path):
        """Verify ValueError for malformed YAML."""
        p = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            - this is a list not a dict
            """,
        )
        with pytest.raises(ValueError, match="expected a YAML mapping"):
            ScriptRunner.load_script(p)

    def test_load_script_missing_name(self, tmp_path):
        """Verify ValueError when 'name' field is missing."""
        p = _write_yaml(
            tmp_path,
            "no_name.yaml",
            """\
            turns:
              - user: "Hi"
            """,
        )
        with pytest.raises(ValueError, match="missing 'name' field"):
            ScriptRunner.load_script(p)

    def test_load_script_missing_turns(self, tmp_path):
        """Verify ValueError when 'turns' field is missing."""
        p = _write_yaml(
            tmp_path,
            "no_turns.yaml",
            """\
            name: "No turns"
            """,
        )
        with pytest.raises(ValueError, match="missing or invalid 'turns' field"):
            ScriptRunner.load_script(p)

    def test_load_script_turn_missing_user(self, tmp_path):
        """Verify ValueError when a turn lacks 'user' field."""
        p = _write_yaml(
            tmp_path,
            "no_user.yaml",
            """\
            name: "Bad turn"
            turns:
              - expect:
                  agent_contains: "hello"
            """,
        )
        with pytest.raises(ValueError, match="must have a 'user' field"):
            ScriptRunner.load_script(p)


class TestLoadScripts:
    """Tests for ScriptRunner.load_scripts()."""

    def test_load_scripts_multiple(self, tmp_path):
        """Load two scripts from explicit paths."""
        p1 = _write_yaml(
            tmp_path,
            "script1.yaml",
            """\
            name: "Script 1"
            turns:
              - user: "Hello"
            """,
        )
        p2 = _write_yaml(
            tmp_path,
            "script2.yaml",
            """\
            name: "Script 2"
            turns:
              - user: "Goodbye"
            """,
        )
        scripts = ScriptRunner.load_scripts([p1, p2])

        assert len(scripts) == 2
        assert scripts[0].name == "Script 1"
        assert scripts[1].name == "Script 2"

    def test_load_scripts_glob_pattern(self, tmp_path):
        """Load scripts via glob pattern."""
        for i in range(3):
            _write_yaml(
                tmp_path,
                f"test_{i}.yaml",
                f"""\
                name: "Test {i}"
                turns:
                  - user: "Turn {i}"
                """,
            )
        # Also create a non-matching file
        _write_yaml(
            tmp_path,
            "readme.txt",
            "not a script",
        )

        scripts = ScriptRunner.load_scripts([tmp_path / "test_*.yaml"])
        assert len(scripts) == 3


# ===========================================================================
# Expectation Checking Tests
# ===========================================================================


class TestCheckExpectations:
    """Tests for ScriptRunner.check_expectations()."""

    def test_check_agent_contains_pass(self):
        """Text contains expected substring -- no failures."""
        turn = _make_turn_record(agent_text="How many in your party?")
        expect = TurnExpectation(agent_contains="party")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_agent_contains_fail(self):
        """Text missing expected substring -- failure reported."""
        turn = _make_turn_record(agent_text="What time would you like?")
        expect = TurnExpectation(agent_contains="party")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1
        assert "agent_contains" in failures[0]
        assert "party" in failures[0]

    def test_check_agent_contains_case_insensitive(self):
        """Case-insensitive matching for agent_contains."""
        turn = _make_turn_record(agent_text="How many in your PARTY?")
        expect = TurnExpectation(agent_contains="party")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_agent_not_contains_pass(self):
        """Text doesn't contain forbidden substring -- no failures."""
        turn = _make_turn_record(agent_text="How many in your party?")
        expect = TurnExpectation(agent_not_contains="error")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_agent_not_contains_fail(self):
        """Text contains forbidden substring -- failure reported."""
        turn = _make_turn_record(agent_text="An error occurred")
        expect = TurnExpectation(agent_not_contains="error")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1
        assert "agent_not_contains" in failures[0]

    def test_check_agent_not_contains_case_insensitive(self):
        """Case-insensitive matching for agent_not_contains."""
        turn = _make_turn_record(agent_text="An ERROR occurred")
        expect = TurnExpectation(agent_not_contains="error")
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1

    def test_check_tools_called_pass(self):
        """All expected tools are present."""
        turn = _make_turn_record(
            tool_calls=[
                {"action": "set_party_size", "args": {"size": 4}},
                {"action": "set_date", "args": {"date": "2026-06-01"}},
            ]
        )
        expect = TurnExpectation(tools_called=["set_party_size", "set_date"])
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_tools_called_fail(self):
        """Expected tool is missing."""
        turn = _make_turn_record(
            tool_calls=[{"action": "set_party_size", "args": {}}]
        )
        expect = TurnExpectation(tools_called=["set_party_size", "set_date"])
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1
        assert "set_date" in failures[0]

    def test_check_tools_called_partial(self):
        """Some tools match, others don't -- only missing ones reported."""
        turn = _make_turn_record(
            tool_calls=[
                {"action": "set_party_size", "args": {}},
                {"action": "other_tool", "args": {}},
            ]
        )
        expect = TurnExpectation(
            tools_called=["set_party_size", "set_date", "set_time"]
        )
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 2
        failure_text = " ".join(failures)
        assert "set_date" in failure_text
        assert "set_time" in failure_text

    def test_check_no_transfer_pass(self):
        """No transfer occurred -- no failure."""
        turn = _make_turn_record(agent_transfer=None)
        expect = TurnExpectation(no_transfer=True)
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_no_transfer_fail(self):
        """Transfer occurred when not expected -- failure."""
        turn = _make_turn_record(agent_transfer="Takeout_Agent")
        expect = TurnExpectation(no_transfer=True)
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1
        assert "no_transfer" in failures[0]
        assert "Takeout_Agent" in failures[0]

    def test_check_session_ended_pass(self):
        """Session ended status matches expectation."""
        turn = _make_turn_record(session_ended=True)
        expect = TurnExpectation(session_ended=True)
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_session_ended_pass_false(self):
        """Session not ended matches expectation of False."""
        turn = _make_turn_record(session_ended=False)
        expect = TurnExpectation(session_ended=False)
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []

    def test_check_session_ended_fail(self):
        """Session ended status doesn't match expectation."""
        turn = _make_turn_record(session_ended=False)
        expect = TurnExpectation(session_ended=True)
        failures = ScriptRunner.check_expectations(turn, expect)
        assert len(failures) == 1
        assert "session_ended" in failures[0]

    def test_check_multiple_expectations(self):
        """Combine several expectations -- all are checked."""
        turn = _make_turn_record(
            agent_text="Here is your reservation",
            tool_calls=[{"action": "BookReservation", "args": {}}],
            agent_transfer="Farewell_Agent",
            session_ended=False,
        )
        expect = TurnExpectation(
            agent_contains="reservation",
            agent_not_contains="error",
            tools_called=["BookReservation"],
            no_transfer=True,  # will fail
            session_ended=True,  # will fail
        )
        failures = ScriptRunner.check_expectations(turn, expect)
        # agent_contains passes, agent_not_contains passes,
        # tools_called passes, no_transfer fails, session_ended fails
        assert len(failures) == 2
        failure_text = " ".join(failures)
        assert "no_transfer" in failure_text
        assert "session_ended" in failure_text

    def test_check_no_expectations(self):
        """Empty expectation -- no failures."""
        turn = _make_turn_record(agent_text="Anything")
        expect = TurnExpectation()
        failures = ScriptRunner.check_expectations(turn, expect)
        assert failures == []


# ===========================================================================
# Script Execution Tests
# ===========================================================================


class TestRunScript:
    """Tests for ScriptRunner.run_script() with mocked ChatSession."""

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_all_pass(self, mock_sleep, MockChatSession):
        """3-turn script where all expectations pass."""
        mock_session = _mock_chat_session(
            [
                {"agent": "How many in your party?", "tool_calls": []},
                {
                    "agent": "What time would you like?",
                    "tool_calls": [{"action": "set_party_size", "args": {"size": 4}}],
                },
                {"agent": "Confirmed for 6 PM", "tool_calls": []},
            ]
        )
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Happy path",
            turns=[
                ScriptTurn(
                    user="Table for 4",
                    expect=TurnExpectation(agent_contains="party"),
                ),
                ScriptTurn(
                    user="4 people",
                    expect=TurnExpectation(tools_called=["set_party_size"]),
                ),
                ScriptTurn(
                    user="6 PM",
                    expect=TurnExpectation(agent_contains="6"),
                ),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is True
        assert result.total_turns == 3
        assert result.passed_turns == 3
        assert result.failed_turns == 0
        assert result.error is None
        assert len(result.turns) == 3
        for tr in result.turns:
            assert tr.passed is True
            assert tr.expectation_failures == []

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_with_failures(self, mock_sleep, MockChatSession):
        """Expectations fail but execution continues to collect all failures."""
        mock_session = _mock_chat_session(
            [
                {"agent": "Hello!", "tool_calls": []},
                {"agent": "I don't understand", "tool_calls": []},
            ]
        )
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Failure test",
            turns=[
                ScriptTurn(
                    user="Hi",
                    expect=TurnExpectation(agent_contains="party"),  # will fail
                ),
                ScriptTurn(
                    user="Book a table",
                    expect=TurnExpectation(
                        tools_called=["set_party_size"]
                    ),  # will fail
                ),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is False
        assert result.total_turns == 2
        assert result.passed_turns == 0
        assert result.failed_turns == 2
        assert result.turns[0].passed is False
        assert len(result.turns[0].expectation_failures) == 1
        assert result.turns[1].passed is False
        assert len(result.turns[1].expectation_failures) == 1

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_session_ends_early(self, mock_sleep, MockChatSession):
        """Session ends at turn 2 of 5 -- remaining turns skipped."""
        mock_session = _mock_chat_session(
            [
                {"agent": "Hello!", "session_ended": False},
                {"agent": "Goodbye!", "session_ended": True},
            ]
        )
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Early end",
            turns=[
                ScriptTurn(user="Hi"),
                ScriptTurn(user="Bye"),
                ScriptTurn(user="Still here?"),
                ScriptTurn(user="Hello?"),
                ScriptTurn(user="Anyone?"),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is False
        assert result.total_turns == 5
        # 2 executed (pass) + 3 skipped (fail)
        assert result.passed_turns == 2
        assert result.failed_turns == 3
        assert len(result.turns) == 5
        # First two turns executed normally
        assert result.turns[0].passed is True
        assert result.turns[1].passed is True
        # Remaining turns are skipped
        for tr in result.turns[2:]:
            assert tr.passed is False
            assert "Skipped: session ended early" in tr.expectation_failures

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_no_expectations(self, mock_sleep, MockChatSession):
        """Turns without expect field all pass."""
        mock_session = _mock_chat_session(
            [
                {"agent": "Hello!"},
                {"agent": "Sure thing!"},
            ]
        )
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="No expectations",
            turns=[
                ScriptTurn(user="Hi"),
                ScriptTurn(user="Do something"),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is True
        assert result.passed_turns == 2
        assert result.failed_turns == 0

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_exception(self, mock_sleep, MockChatSession):
        """ChatSession.send() throws -- error captured in result."""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-id"
        mock_session.is_ended = False
        mock_session.send.side_effect = RuntimeError("API unavailable")
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Error test",
            turns=[
                ScriptTurn(user="Hi"),
                ScriptTurn(user="Will not reach"),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is False
        # Only 1 turn attempted before exception
        assert len(result.turns) == 1
        assert result.turns[0].passed is False
        assert "API unavailable" in result.turns[0].expectation_failures[0]

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_uses_script_app_name(self, mock_sleep, MockChatSession):
        """Script-level app_name overrides runner-level app_name."""
        mock_session = _mock_chat_session([{"agent": "Hi"}])
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Override test",
            app_name="script-level-app",
            channel="phone",
            turns=[ScriptTurn(user="Hi")],
        )

        runner = ScriptRunner(app_name="runner-level-app", channel="web", delay=0)
        runner.run_script(script)

        MockChatSession.assert_called_once_with(
            app_name="script-level-app",
            channel="phone",
        )

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_falls_back_to_runner_app_name(
        self, mock_sleep, MockChatSession
    ):
        """When script has no app_name, runner-level app_name is used."""
        mock_session = _mock_chat_session([{"agent": "Hi"}])
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Fallback test",
            turns=[ScriptTurn(user="Hi")],
        )

        runner = ScriptRunner(app_name="runner-level-app", channel="web", delay=0)
        runner.run_script(script)

        MockChatSession.assert_called_once_with(
            app_name="runner-level-app",
            channel="web",
        )

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_script_delay_between_turns(self, mock_sleep, MockChatSession):
        """Verifies delay is applied between turns but not after the last."""
        mock_session = _mock_chat_session(
            [
                {"agent": "Turn 1"},
                {"agent": "Turn 2"},
                {"agent": "Turn 3"},
            ]
        )
        MockChatSession.return_value = mock_session

        script = ConversationScript(
            name="Delay test",
            turns=[
                ScriptTurn(user="A"),
                ScriptTurn(user="B"),
                ScriptTurn(user="C"),
            ],
        )

        runner = ScriptRunner(app_name="test-app", delay=1.5)
        runner.run_script(script)

        # Sleep should be called between turns (2 times for 3 turns)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(1.5)

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    def test_run_script_session_creation_failure(self, MockChatSession):
        """ChatSession constructor throws -- error captured in ScriptResult."""
        MockChatSession.side_effect = RuntimeError("Connection refused")

        script = ConversationScript(
            name="Creation failure",
            turns=[ScriptTurn(user="Hi")],
        )

        runner = ScriptRunner(app_name="test-app", delay=0)
        result = runner.run_script(script)

        assert result.passed is False
        assert result.error is not None
        assert "Connection refused" in result.error
        assert result.total_turns == 1
        assert len(result.turns) == 0


# ===========================================================================
# Batch Tests
# ===========================================================================


class TestRunBatch:
    """Tests for ScriptRunner.run_batch()."""

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_batch_sequential(self, mock_sleep, MockChatSession):
        """Two scripts, both executed sequentially."""
        call_count = {"n": 0}

        def session_factory(*args, **kwargs):
            call_count["n"] += 1
            return _mock_chat_session(
                [{"agent": f"Response from session {call_count['n']}"}]
            )

        MockChatSession.side_effect = session_factory

        scripts = [
            ConversationScript(
                name="Script A", turns=[ScriptTurn(user="Hello A")]
            ),
            ConversationScript(
                name="Script B", turns=[ScriptTurn(user="Hello B")]
            ),
        ]

        runner = ScriptRunner(app_name="test-app", delay=0)
        results = runner.run_batch(scripts, max_workers=1)

        assert len(results) == 2
        assert results[0].script_name == "Script A"
        assert results[1].script_name == "Script B"
        assert all(r.passed for r in results)

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_batch_results_count(self, mock_sleep, MockChatSession):
        """Verify all results returned for a batch of 3 scripts."""
        MockChatSession.side_effect = lambda *a, **kw: _mock_chat_session(
            [{"agent": "Response"}]
        )

        scripts = [
            ConversationScript(name=f"Script {i}", turns=[ScriptTurn(user="Hi")])
            for i in range(3)
        ]

        runner = ScriptRunner(app_name="test-app", delay=0)
        results = runner.run_batch(scripts)

        assert len(results) == 3

    @patch("cxas_scrapi.utils.script_runner.ChatSession")
    @patch("cxas_scrapi.utils.script_runner.time.sleep")
    def test_run_batch_parallel(self, mock_sleep, MockChatSession):
        """Run batch with max_workers > 1 to exercise ThreadPoolExecutor path."""
        MockChatSession.side_effect = lambda *a, **kw: _mock_chat_session(
            [{"agent": "Response"}]
        )

        scripts = [
            ConversationScript(name=f"Script {i}", turns=[ScriptTurn(user="Hi")])
            for i in range(3)
        ]

        runner = ScriptRunner(app_name="test-app", delay=0)
        results = runner.run_batch(scripts, max_workers=2)

        assert len(results) == 3


# ===========================================================================
# Output Format Tests
# ===========================================================================


class TestOutputFormats:
    """Tests for results_to_json() and results_to_table()."""

    def _sample_results(self) -> list[ScriptResult]:
        """Create sample ScriptResults for output tests."""
        return [
            ScriptResult(
                script_name="Happy Path",
                turns=[
                    TurnResult(
                        turn_index=0,
                        user_text="Hi",
                        agent_text="Hello!",
                        tool_calls=[],
                        transfer=None,
                        session_ended=False,
                        expectation_failures=[],
                        passed=True,
                    ),
                ],
                passed=True,
                total_turns=1,
                passed_turns=1,
                failed_turns=0,
            ),
            ScriptResult(
                script_name="Sad Path",
                turns=[
                    TurnResult(
                        turn_index=0,
                        user_text="Break",
                        agent_text="Error",
                        tool_calls=[],
                        transfer=None,
                        session_ended=False,
                        expectation_failures=["agent_contains: expected 'hello'"],
                        passed=False,
                    ),
                ],
                passed=False,
                total_turns=1,
                passed_turns=0,
                failed_turns=1,
            ),
        ]

    def test_results_to_json(self):
        """Verify JSON output structure and content."""
        results = self._sample_results()
        json_str = ScriptRunner.results_to_json(results)
        parsed = json.loads(json_str)

        assert len(parsed) == 2
        assert parsed[0]["script_name"] == "Happy Path"
        assert parsed[0]["passed"] is True
        assert parsed[0]["total_turns"] == 1
        assert parsed[0]["passed_turns"] == 1
        assert parsed[0]["failed_turns"] == 0
        assert parsed[0]["error"] is None
        assert len(parsed[0]["turns"]) == 1
        assert parsed[0]["turns"][0]["user_text"] == "Hi"
        assert parsed[0]["turns"][0]["agent_text"] == "Hello!"
        assert parsed[0]["turns"][0]["passed"] is True

        assert parsed[1]["script_name"] == "Sad Path"
        assert parsed[1]["passed"] is False
        assert len(parsed[1]["turns"][0]["expectation_failures"]) == 1

    def test_results_to_json_empty(self):
        """Empty results list produces valid JSON array."""
        json_str = ScriptRunner.results_to_json([])
        parsed = json.loads(json_str)
        assert parsed == []

    def test_results_to_table(self):
        """Verify table output contains script names and statuses."""
        results = self._sample_results()
        table_str = ScriptRunner.results_to_table(results)

        assert "Happy Path" in table_str
        assert "Sad Path" in table_str
        assert "PASS" in table_str
        assert "FAIL" in table_str

    def test_results_to_table_with_error(self):
        """Verify table shows ERROR status for scripts with errors."""
        results = [
            ScriptResult(
                script_name="Error Script",
                turns=[],
                passed=False,
                total_turns=3,
                passed_turns=0,
                failed_turns=0,
                error="Connection refused",
            ),
        ]
        table_str = ScriptRunner.results_to_table(results)

        assert "Error Script" in table_str
        assert "ERROR" in table_str


# ===========================================================================
# Data Class Tests
# ===========================================================================


class TestDataClasses:
    """Tests for the data classes themselves."""

    def test_turn_expectation_defaults(self):
        """TurnExpectation has sensible defaults."""
        exp = TurnExpectation()
        assert exp.agent_contains is None
        assert exp.agent_not_contains is None
        assert exp.tools_called is None
        assert exp.no_transfer is False
        assert exp.session_ended is None

    def test_script_turn_defaults(self):
        """ScriptTurn expect defaults to None."""
        st = ScriptTurn(user="Hello")
        assert st.user == "Hello"
        assert st.expect is None

    def test_conversation_script_defaults(self):
        """ConversationScript optional fields have defaults."""
        cs = ConversationScript(name="Test", turns=[])
        assert cs.description == ""
        assert cs.app_name is None
        assert cs.channel is None

    def test_script_result_defaults(self):
        """ScriptResult error defaults to None."""
        sr = ScriptResult(
            script_name="Test",
            turns=[],
            passed=True,
            total_turns=0,
            passed_turns=0,
            failed_turns=0,
        )
        assert sr.error is None
