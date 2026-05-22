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

"""Tests for dfcx_conversation_runner module."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cxas_scrapi.migration.dfcx_conversation_runner import (
    INLINE_TOOL_SENTINEL,
    ConversationTrace,
    ConversationTurn,
    DFCXConversationRunner,
)

LIVE_AGENT_ID = (
    "projects/ccai-platform-project/locations/global/agents/"
    "406b66f8-43d0-494d-8a2d-05c1273f9265"
)

AGENT_BASE = "projects/p/locations/us-central1/agents/a"
TOOL_ID = f"{AGENT_BASE}/tools/t1"
PLAYBOOK_ID = f"{AGENT_BASE}/playbooks/pb1"
FLOW_ID = f"{AGENT_BASE}/flows/f1"


def _make_query_result(
    *,
    agent_text="Hello!",
    tool_id=TOOL_ID,
    tool_action="lookup",
    playbook_id=PLAYBOOK_ID,
    flow_id=FLOW_ID,
    page_display_name="Start Page",
    intent_display_name="welcome",
    confidence=0.92,
):
    """Build a fake QueryResult mirroring the shape of a real one."""
    tool_use = SimpleNamespace(
        tool=tool_id,
        action=tool_action,
        input_action_parameters={"q": "weather"},
        output_action_parameters={"result": "sunny"},
    )

    actions = [
        SimpleNamespace(
            agent_utterance=SimpleNamespace(text=agent_text),
            tool_use=None,
            playbook_invocation=None,
            flow_invocation=None,
        ),
        SimpleNamespace(
            agent_utterance=SimpleNamespace(text=""),
            tool_use=tool_use,
            playbook_invocation=None,
            flow_invocation=None,
        ),
        SimpleNamespace(
            agent_utterance=SimpleNamespace(text=""),
            tool_use=None,
            playbook_invocation=SimpleNamespace(playbook=playbook_id),
            flow_invocation=None,
        ),
        SimpleNamespace(
            agent_utterance=SimpleNamespace(text=""),
            tool_use=None,
            playbook_invocation=None,
            flow_invocation=SimpleNamespace(flow=flow_id),
        ),
    ]

    gen_info = SimpleNamespace(
        action_tracing_info=SimpleNamespace(actions=actions),
        current_playbooks=[playbook_id],
    )

    match_type = SimpleNamespace(_name_="INTENT")
    match = SimpleNamespace(match_type=match_type, confidence=confidence)

    return SimpleNamespace(
        generative_info=gen_info,
        response_messages=[],
        current_page=SimpleNamespace(display_name=page_display_name),
        intent=SimpleNamespace(display_name=intent_display_name),
        match=match,
        parameters={},
        text="user input",
    )


def _build_runner_with_mock_session(query_result_factory=_make_query_result):
    """Construct a DFCXConversationRunner whose CX clients are mocked
    so the tests never touch the network."""
    fake_response = MagicMock()
    fake_response.query_result = query_result_factory()
    fake_sessions_client = MagicMock()
    fake_sessions_client.detect_intent.return_value = fake_response

    runner = DFCXConversationRunner(
        agent_id=AGENT_BASE,
        creds=MagicMock(),
        language_code="en",
    )

    # Inject the mock SessionsClient and pre-populate display-name caches
    # so no network calls happen.
    runner._sessions_client = fake_sessions_client
    runner._tools_map = {TOOL_ID: "my_tool"}
    runner._playbooks_map = {PLAYBOOK_ID: "primary_playbook"}
    runner._flows_map = {FLOW_ID: "main_flow"}

    return runner, fake_sessions_client


def test_dataclass_round_trip():
    trace = ConversationTrace(
        agent_id="a",
        session_id="s",
        language_code="en",
        started_at="2026-05-14T00:00:00+00:00",
        turns=[ConversationTurn(turn=1, user_query="hi")],
    )
    d = trace.to_dict()
    assert d["agent_id"] == "a"
    assert d["turns"][0]["user_query"] == "hi"
    assert d["turns"][0]["agent_responses"] == []


def test_session_id_built_with_correct_prefix():
    runner = DFCXConversationRunner(agent_id=AGENT_BASE, creds=MagicMock())
    assert runner.session_id.startswith(f"{AGENT_BASE}/sessions/")


def test_client_options_routes_regional_endpoint():
    """The runner inherits BaseDFCXClient's regional endpoint routing."""
    runner = DFCXConversationRunner.__new__(DFCXConversationRunner)
    assert runner._get_client_options(AGENT_BASE) == {
        "api_endpoint": "us-central1-dialogflow.googleapis.com"
    }
    assert runner._get_client_options(
        "projects/p/locations/global/agents/a"
    ) == {"api_endpoint": "dialogflow.googleapis.com"}


def test_send_message_extracts_full_trace():
    runner, fake_client = _build_runner_with_mock_session()

    turn = runner.send_message("hello", parameters={"x": 1})

    fake_client.detect_intent.assert_called_once()
    # The conftest mocks google.cloud.dialogflowcx_v3beta1, so the
    # DetectIntentRequest returned to the runner is itself a MagicMock —
    # introspecting its fields would only assert on auto-generated mocks.
    # Behavioral correctness is covered by the turn-data assertions below.
    assert "request" in fake_client.detect_intent.call_args.kwargs

    assert turn.turn == 1
    assert turn.user_query == "hello"
    assert turn.agent_responses == ["Hello!"]
    assert turn.current_page == "Start Page"
    assert turn.intent == "welcome"
    assert turn.match_type == "INTENT"
    assert turn.confidence == pytest.approx(0.92)

    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc["tool_id"] == TOOL_ID
    assert tc["tool_name"] == "my_tool"
    assert tc["tool_action"] == "lookup"
    assert tc["inline_action"] is False
    assert tc["input_params"] == {"q": "weather"}
    assert tc["output_params"] == {"result": "sunny"}

    assert turn.playbook_invocations == [
        {"playbook_id": PLAYBOOK_ID, "playbook_name": "primary_playbook"}
    ]
    assert turn.flow_invocations == [
        {"flow_id": FLOW_ID, "flow_name": "main_flow"}
    ]
    assert turn.current_playbooks == ["primary_playbook"]


def test_inline_tool_action_is_tagged_and_id_preserved():
    """Inline actions don't have a registered tools/<id> resource."""

    def factory():
        return _make_query_result(tool_id="inline-action")

    runner, _ = _build_runner_with_mock_session(query_result_factory=factory)
    turn = runner.send_message("hi")

    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc["tool_id"] == "inline-action"
    assert tc["tool_name"] == INLINE_TOOL_SENTINEL
    assert tc["inline_action"] is True
    # We must NOT have attempted to resolve the inline ID against the map.
    assert "inline-action" not in runner._tools_map


def test_run_golden_appends_turns():
    runner, fake_client = _build_runner_with_mock_session()
    fake_client.detect_intent.side_effect = [
        SimpleNamespace(query_result=_make_query_result(agent_text="hi 1")),
        SimpleNamespace(query_result=_make_query_result(agent_text="hi 2")),
        SimpleNamespace(query_result=_make_query_result(agent_text="hi 3")),
    ]

    trace = runner.run_golden(["a", "b", "c"])

    assert len(trace.turns) == 3
    assert [t.user_query for t in trace.turns] == ["a", "b", "c"]
    assert trace.turns[1].agent_responses == ["hi 2"]
    assert trace.turns[2].turn == 3


def test_save_to_yaml_writes_expected_structure(tmp_path):
    runner, _ = _build_runner_with_mock_session()
    runner.send_message("hello")

    out_path = tmp_path / "conv.yaml"
    runner.save_to_yaml(str(out_path))

    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["agent_id"] == AGENT_BASE
    assert loaded["session_id"].startswith(f"{AGENT_BASE}/sessions/")
    assert loaded["language_code"] == "en"
    assert "started_at" in loaded
    assert len(loaded["turns"]) == 1
    turn = loaded["turns"][0]
    assert turn["user_query"] == "hello"
    assert turn["agent_responses"] == ["Hello!"]
    tc = turn["tool_calls"][0]
    assert tc["tool_id"] == TOOL_ID
    assert tc["tool_name"] == "my_tool"
    assert tc["inline_action"] is False
    assert turn["playbook_invocations"][0]["playbook_name"] == (
        "primary_playbook"
    )
    assert turn["flow_invocations"][0]["flow_name"] == "main_flow"


def _make_recorded_conversation(
    name="projects/p/locations/us-central1/agents/a/conversations/c1",
    interactions=None,
    start_time=None,
):
    """Build a fake stored Conversation that mirrors the SDK shape."""
    if interactions is None:
        interactions = [
            SimpleNamespace(
                request=SimpleNamespace(
                    query_input=SimpleNamespace(
                        text=SimpleNamespace(text="hello"),
                        intent=None,
                        event=None,
                        dtmf=None,
                    )
                ),
                response=SimpleNamespace(
                    query_result=_make_query_result(agent_text="hi back")
                ),
            ),
            SimpleNamespace(
                request=SimpleNamespace(
                    query_input=SimpleNamespace(
                        text=SimpleNamespace(text="bye"),
                        intent=None,
                        event=None,
                        dtmf=None,
                    )
                ),
                response=SimpleNamespace(
                    query_result=_make_query_result(agent_text="goodbye")
                ),
            ),
        ]

    if start_time is None:
        start_time = SimpleNamespace(
            isoformat=lambda: "2026-05-14T22:00:00+00:00"
        )

    # Stored conversations come back newest-first; mirror that.
    return SimpleNamespace(
        name=name,
        start_time=start_time,
        interactions=list(reversed(interactions)),
    )


def _build_runner_with_mock_history(convo):
    """Construct a runner whose history client returns the supplied
    Conversation, with all maps pre-populated to avoid network calls."""
    fake_history_client = MagicMock()
    fake_history_client.get_conversation.return_value = convo
    fake_history_client.list_conversations.return_value = iter([convo])

    runner = DFCXConversationRunner(
        agent_id=AGENT_BASE,
        creds=MagicMock(),
        language_code="en",
    )

    runner._history_client = fake_history_client
    runner._tools_map = {TOOL_ID: "my_tool"}
    runner._playbooks_map = {PLAYBOOK_ID: "primary_playbook"}
    runner._flows_map = {FLOW_ID: "main_flow"}
    return runner, fake_history_client


def test_list_conversations_returns_summary():
    convo = _make_recorded_conversation()
    runner, fake_client = _build_runner_with_mock_history(convo)

    listing = runner.list_conversations()

    fake_client.list_conversations.assert_called_once()
    assert len(listing) == 1
    assert listing[0]["name"] == convo.name
    assert listing[0]["start_time"] == "2026-05-14T22:00:00+00:00"
    assert listing[0]["interaction_count"] == 2


def test_get_conversation_replays_into_trace():
    convo = _make_recorded_conversation()
    runner, fake_client = _build_runner_with_mock_history(convo)

    trace = runner.get_conversation(convo.name)

    fake_client.get_conversation.assert_called_once()
    # The historical conversation_id replaces session_id as the source ref.
    assert trace.session_id == convo.name
    assert runner.session_id == convo.name
    assert trace.started_at == "2026-05-14T22:00:00+00:00"

    assert len(trace.turns) == 2
    # Replay reverses the SDK's newest-first ordering, so chronological.
    assert [t.user_query for t in trace.turns] == ["hello", "bye"]
    assert [t.agent_responses[0] for t in trace.turns] == ["hi back", "goodbye"]
    # Same trace extraction means tool/playbook/flow data is captured too.
    assert trace.turns[0].tool_calls[0]["tool_name"] == "my_tool"
    assert trace.turns[0].playbook_invocations[0]["playbook_name"] == (
        "primary_playbook"
    )


def test_get_conversation_handles_non_text_inputs():
    interactions = [
        SimpleNamespace(
            request=SimpleNamespace(
                query_input=SimpleNamespace(
                    text=None,
                    intent=SimpleNamespace(intent="welcome"),
                    event=None,
                    dtmf=None,
                )
            ),
            response=SimpleNamespace(
                query_result=_make_query_result(agent_text="hi")
            ),
        ),
        SimpleNamespace(
            request=SimpleNamespace(
                query_input=SimpleNamespace(
                    text=None,
                    intent=None,
                    event=SimpleNamespace(event="WELCOME"),
                    dtmf=None,
                )
            ),
            response=SimpleNamespace(
                query_result=_make_query_result(agent_text="welcome event")
            ),
        ),
    ]
    convo = _make_recorded_conversation(interactions=interactions)
    runner, _ = _build_runner_with_mock_history(convo)

    trace = runner.get_conversation(convo.name)
    queries = [t.user_query for t in trace.turns]
    assert "<intent:welcome>" in queries
    assert "<event:WELCOME>" in queries


def test_save_to_yaml_works_for_loaded_history(tmp_path):
    convo = _make_recorded_conversation()
    runner, _ = _build_runner_with_mock_history(convo)
    runner.get_conversation(convo.name)

    out_path = tmp_path / "history.yaml"
    runner.save_to_yaml(str(out_path))

    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["agent_id"] == AGENT_BASE
    assert loaded["session_id"] == convo.name
    assert loaded["started_at"] == "2026-05-14T22:00:00+00:00"
    assert len(loaded["turns"]) == 2
    # Identical schema to live runs.
    assert loaded["turns"][0]["agent_responses"] == ["hi back"]
    assert loaded["turns"][0]["tool_calls"][0]["tool_id"] == TOOL_ID


def test_from_conversation_classmethod():
    convo = _make_recorded_conversation()
    fake_history_client = MagicMock()
    fake_history_client.get_conversation.return_value = convo

    # Patch _get_history_client at class level so it survives __init__.
    with patch.object(
        DFCXConversationRunner,
        "_get_history_client",
        return_value=fake_history_client,
    ):
        runner = DFCXConversationRunner.from_conversation(
            agent_id=AGENT_BASE,
            conversation_id=convo.name,
            creds=MagicMock(),
        )

    assert runner.session_id == convo.name
    assert len(runner.trace.turns) == 2


@pytest.mark.online
def test_live_history_pull_against_test_agent(tmp_path):
    """List past conversations from the configured DFCX test agent, then
    pull the most recent one and persist it as YAML.

    Skipped unless --run-online is passed. Skipped at runtime if the agent
    has no recorded conversations.
    """
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    runner = DFCXConversationRunner(
        agent_id=LIVE_AGENT_ID,
        creds_path=creds_path,
        language_code="en",
    )

    listing = runner.list_conversations()
    if not listing:
        pytest.skip("No recorded conversations on the test agent")

    convo_id = listing[0]["name"]
    trace = runner.get_conversation(convo_id)

    assert trace.session_id == convo_id
    assert trace.agent_id == LIVE_AGENT_ID
    # A non-empty conversation must have at least one turn.
    assert len(trace.turns) >= 1

    out_path = tmp_path / "live_history_trace.yaml"
    runner.save_to_yaml(str(out_path))
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["session_id"] == convo_id
    assert len(loaded["turns"]) == len(trace.turns)


@pytest.mark.online
def test_live_conversation_against_test_agent(tmp_path):
    """Drive a real conversation against the configured DFCX test agent and
    persist the trace. Skipped unless --run-online is passed.

    Authentication is picked up from Application Default Credentials, or
    the GOOGLE_APPLICATION_CREDENTIALS env var if set.
    """
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    runner = DFCXConversationRunner(
        agent_id=LIVE_AGENT_ID,
        creds_path=creds_path,
        language_code="en",
    )

    utterances = ["hi", "I need help", "thanks"]
    trace = runner.run_golden(utterances)

    assert trace.agent_id == LIVE_AGENT_ID
    assert trace.session_id.startswith(f"{LIVE_AGENT_ID}/sessions/")
    assert len(trace.turns) == len(utterances)
    for utterance, turn in zip(utterances, trace.turns, strict=True):
        assert turn.user_query == utterance
        assert turn.agent_responses or turn.response_messages

    out_path = tmp_path / "live_conversation_trace.yaml"
    runner.save_to_yaml(str(out_path))
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["agent_id"] == LIVE_AGENT_ID
    assert len(loaded["turns"]) == len(utterances)
    # Every tool_call dict must carry tool_id and inline_action keys.
    for turn in loaded["turns"]:
        for tc in turn["tool_calls"]:
            assert "tool_id" in tc
            assert "inline_action" in tc
