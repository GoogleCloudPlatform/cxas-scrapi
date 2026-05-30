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

"""Tests for Traces.fork(), Traces.diff(), enhanced Traces.replay(),
and the _tool_calls_per_turn helper added in Stream 4 (cxas chat)."""

from unittest.mock import MagicMock

import pytest

from cxas_scrapi.core import traces as traces_mod
from cxas_scrapi.core.traces import Traces

APP = "projects/p/locations/l/apps/a"


# ----------------------------- helpers ----------------------------------------


def _multi_turn_conv(cid="c1"):
    """Returns a dict-shaped conversation with three turns."""
    return {
        "name": f"{APP}/conversations/{cid}",
        "source": "LIVE",
        "input_types": ["INPUT_TYPE_TEXT"],
        "start_time": "2026-05-01T00:00:00",
        "end_time": "2026-05-01T00:01:00",
        "turns": [
            {
                "messages": [
                    {"role": "user", "chunks": [{"text": "hello"}]},
                    {"role": "agent", "chunks": [{"text": "hi there"}]},
                ]
            },
            {
                "messages": [
                    {"role": "user", "chunks": [{"text": "book a table"}]},
                    {
                        "role": "agent",
                        "chunks": [
                            {"text": "sure, how many?"},
                            {
                                "tool_call": {
                                    "display_name": "set_flow",
                                    "args": {"flow": "reservation"},
                                }
                            },
                        ],
                    },
                ]
            },
            {
                "messages": [
                    {"role": "user", "chunks": [{"text": "4 people"}]},
                    {
                        "role": "agent",
                        "chunks": [
                            {"text": "party of 4, got it"},
                            {
                                "tool_call": {
                                    "display_name": "set_party_size",
                                    "args": {"size": 4},
                                }
                            },
                        ],
                    },
                ]
            },
        ],
    }


def _conv_dict_single(cid="c1"):
    """Single-turn conversation for simpler tests."""
    return {
        "name": f"{APP}/conversations/{cid}",
        "source": "LIVE",
        "input_types": ["INPUT_TYPE_TEXT"],
        "start_time": "2026-05-01T00:00:00",
        "end_time": "2026-05-01T00:01:00",
        "turns": [
            {
                "messages": [
                    {"role": "user", "chunks": [{"text": "hi"}]},
                    {
                        "role": "agent",
                        "chunks": [
                            {"text": "hello"},
                            {
                                "tool_call": {
                                    "display_name": "lookup",
                                    "args": {"q": 1},
                                }
                            },
                        ],
                    },
                ]
            }
        ],
    }


@pytest.fixture
def traces_obj(tmp_path, monkeypatch):
    """Traces with no app dir; mocks TraceConfig path resolution."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(traces_mod.TraceConfig, "_pick_path", lambda *_: None)
    return Traces(app_name=APP, app_dir=str(tmp_path / "missing"))


# ========================== _tool_calls_per_turn ==============================


class TestToolCallsPerTurn:
    def test_basic(self):
        n = {
            "entries": [
                {"kind": "tool_call", "turn": 0, "tool": "lookup"},
                {"kind": "tool_call", "turn": 1, "tool": "set_flow"},
                {"kind": "tool_call", "turn": 1, "tool": "set_party_size"},
            ]
        }
        result = traces_mod._tool_calls_per_turn(n)
        assert result == [["lookup"], ["set_flow", "set_party_size"]]

    def test_empty_entries(self):
        assert traces_mod._tool_calls_per_turn({"entries": []}) == []

    def test_no_tool_calls(self):
        n = {"entries": [{"kind": "agent", "turn": 0, "text": "hi"}]}
        assert traces_mod._tool_calls_per_turn(n) == []

    def test_gap_in_turns(self):
        n = {
            "entries": [
                {"kind": "tool_call", "turn": 0, "tool": "a"},
                {"kind": "tool_call", "turn": 2, "tool": "b"},
            ]
        }
        result = traces_mod._tool_calls_per_turn(n)
        assert result == [["a"], [], ["b"]]


# ============================== fork() ========================================


class TestFork:
    def test_fork_basic(self, traces_obj):
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _multi_turn_conv()

        result = traces_obj.fork("c1")

        assert result["original_conversation_id"] == "c1"
        assert result["turn_count"] == 3
        assert result["forked_at_turn"] is None
        # 3 turns x 2 messages each (user + agent) = 6 context messages
        assert len(result["historical_contexts"]) == 6
        assert result["historical_contexts"][0]["role"] == "user"
        assert result["historical_contexts"][1]["role"] == "agent"

    def test_fork_at_turn(self, traces_obj):
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _multi_turn_conv()

        result = traces_obj.fork("c1", at_turn=1)

        assert result["turn_count"] == 2
        assert result["forked_at_turn"] == 1
        # 2 turns x 2 messages each = 4 context messages
        assert len(result["historical_contexts"]) == 4

    def test_fork_at_turn_zero(self, traces_obj):
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _multi_turn_conv()

        result = traces_obj.fork("c1", at_turn=0)

        assert result["turn_count"] == 1
        assert result["forked_at_turn"] == 0
        # 1 turn x 2 messages = 2 context messages
        assert len(result["historical_contexts"]) == 2

    def test_fork_return_structure(self, traces_obj):
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _multi_turn_conv()

        result = traces_obj.fork("c1", at_turn=1)

        assert set(result.keys()) == {
            "historical_contexts",
            "turn_count",
            "original_conversation_id",
            "forked_at_turn",
        }
        # Verify each context message has role and chunks
        for ctx in result["historical_contexts"]:
            assert "role" in ctx
            assert "chunks" in ctx

    def test_fork_filters_messages_without_role_or_chunks(self, traces_obj):
        """Messages lacking role or chunks should be skipped."""
        traces_obj.history = MagicMock()
        conv = {
            "name": f"{APP}/conversations/c1",
            "turns": [
                {
                    "messages": [
                        {"role": "user", "chunks": [{"text": "hi"}]},
                        {"no_role": True},  # missing role
                        {"role": "agent"},  # missing chunks
                    ]
                }
            ],
        }
        traces_obj.history.get_conversation.return_value = conv

        result = traces_obj.fork("c1")
        assert len(result["historical_contexts"]) == 1


# ============================== diff() ========================================


class TestDiff:
    def _normalized_conv(self, cid, agent_texts, tool_entries=None):
        """Build a normalized-style dict for diff testing."""
        entries = []
        for i, text in enumerate(agent_texts):
            entries.append(
                {"kind": "user", "turn": i, "text": f"user msg {i}"}
            )
            entries.append({"kind": "agent", "turn": i, "text": text})
        for te in tool_entries or []:
            entries.append(te)
        return {
            "conversation_id": cid,
            "entries": entries,
        }

    def test_diff_identical(self, traces_obj):
        norm = self._normalized_conv("c1", ["hi there", "sure thing"])
        traces_obj.get_normalized = MagicMock(return_value=norm)

        result = traces_obj.diff("c1", "c1")

        assert result["summary"]["matching_turns"] == 2
        assert result["summary"]["differing_turns"] == 0
        assert result["agent_text_diff"] == ""
        assert result["tool_call_diff"] == ""

    def test_diff_different_text(self, traces_obj):
        norm_a = self._normalized_conv("ca", ["hello", "yes"])
        norm_b = self._normalized_conv("cb", ["hello", "no"])
        traces_obj.get_normalized = MagicMock(
            side_effect=lambda cid: norm_a if cid == "ca" else norm_b
        )

        result = traces_obj.diff("ca", "cb")

        assert result["summary"]["differing_turns"] > 0
        assert result["agent_text_diff"] != ""
        assert result["conversation_a"] == "ca"
        assert result["conversation_b"] == "cb"

    def test_diff_different_tools(self, traces_obj):
        norm_a = self._normalized_conv(
            "ca",
            ["hello"],
            tool_entries=[
                {"kind": "tool_call", "turn": 0, "tool": "lookup"},
            ],
        )
        norm_b = self._normalized_conv(
            "cb",
            ["hello"],
            tool_entries=[
                {"kind": "tool_call", "turn": 0, "tool": "search"},
            ],
        )
        traces_obj.get_normalized = MagicMock(
            side_effect=lambda cid: norm_a if cid == "ca" else norm_b
        )

        result = traces_obj.diff("ca", "cb")

        # Text matches but tools differ
        assert result["turn_comparison"][0]["text_match"] is True
        assert result["turn_comparison"][0]["tools_match"] is False
        assert result["tool_call_diff"] != ""

    def test_diff_different_turn_counts(self, traces_obj):
        norm_a = self._normalized_conv("ca", ["hello", "sure", "bye"])
        norm_b = self._normalized_conv("cb", ["hello"])
        traces_obj.get_normalized = MagicMock(
            side_effect=lambda cid: norm_a if cid == "ca" else norm_b
        )

        result = traces_obj.diff("ca", "cb")

        s = result["summary"]
        assert s["total_turns_a"] == 3
        assert s["total_turns_b"] == 1
        # Turn 0 matches, turns 1-2 are only in A
        assert s["matching_turns"] == 1
        assert s["differing_turns"] == 2

    def test_diff_summary_counts(self, traces_obj):
        norm_a = self._normalized_conv("ca", ["a", "b", "c"])
        norm_b = self._normalized_conv("cb", ["a", "x", "c"])
        traces_obj.get_normalized = MagicMock(
            side_effect=lambda cid: norm_a if cid == "ca" else norm_b
        )

        result = traces_obj.diff("ca", "cb")

        s = result["summary"]
        assert s["total_turns_a"] == 3
        assert s["total_turns_b"] == 3
        assert s["matching_turns"] == 2  # turns 0 and 2 match
        assert s["differing_turns"] == 1  # turn 1 differs
        assert s["matching_turns"] + s["differing_turns"] == max(
            s["total_turns_a"], s["total_turns_b"]
        )


# ======================== enhanced replay() ===================================


class TestEnhancedReplay:
    def test_replay_backward_compat(self, traces_obj, monkeypatch):
        """Calling replay() without new params gives same structure as before,
        plus the new diverged_at key."""
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _conv_dict_single()

        fake_sess = MagicMock()
        fake_sess.create_session_id.return_value = "sid"
        fake_sess.run.return_value = "raw"
        fake_sess.get_structured_response.return_value = {
            "agent_text": "hello back",
        }
        monkeypatch.setattr(
            traces_mod, "Sessions", MagicMock(return_value=fake_sess)
        )

        out = traces_obj.replay("c1")
        assert "original" in out
        assert "replay" in out
        assert "diff" in out
        assert "diverged_at" in out
        assert out["diverged_at"] is None

    def test_replay_interactive_callback(self, traces_obj, monkeypatch):
        """on_turn callback is invoked with correct arguments."""
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _conv_dict_single()

        fake_sess = MagicMock()
        fake_sess.create_session_id.return_value = "sid"
        fake_sess.run.return_value = "raw"
        fake_sess.get_structured_response.return_value = {
            "agent_text": "replayed",
        }
        monkeypatch.setattr(
            traces_mod, "Sessions", MagicMock(return_value=fake_sess)
        )

        callback = MagicMock(return_value=None)
        out = traces_obj.replay(
            "c1", interactive=True, on_turn=callback
        )

        callback.assert_called_once()
        call_args = callback.call_args[0]
        assert call_args[0] == 0  # turn index
        assert isinstance(call_args[1], str)  # original text
        assert isinstance(call_args[2], str)  # replayed text
        assert out["diverged_at"] is None  # callback returned None

    def test_replay_diverged_at(self, traces_obj, monkeypatch):
        """When on_turn returns a non-None value, diverged_at is set."""
        traces_obj.history = MagicMock()
        traces_obj.history.get_conversation.return_value = _conv_dict_single()

        fake_sess = MagicMock()
        fake_sess.create_session_id.return_value = "sid"
        fake_sess.run.return_value = "raw"
        fake_sess.get_structured_response.return_value = {
            "agent_text": "replayed",
        }
        monkeypatch.setattr(
            traces_mod, "Sessions", MagicMock(return_value=fake_sess)
        )

        # Return a string to signal divergence
        callback = MagicMock(return_value="I want something else")
        out = traces_obj.replay(
            "c1", interactive=True, on_turn=callback
        )

        assert out["diverged_at"] == 0
