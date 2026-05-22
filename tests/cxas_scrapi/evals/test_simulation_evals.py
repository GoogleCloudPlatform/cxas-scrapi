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

"""Unit tests for the eval conversation utility."""

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cxas_scrapi.evals.simulation_evals import (
    LLMUserConversation,
    SimulationEvals,
    SimulationReport,
    Step,
    StepProgress,
    StepStatus,
    ToolCall,
    Turn,
)
from cxas_scrapi.utils.eval_utils import (
    ExpectationResult,
    ExpectationStatus,
)


def test_llm_user_conversation():
    mock_gemini_client = MagicMock()

    user_utterance_0 = "event: welcome"
    agent_response_1 = "Hi, how can I help you?"
    user_utterance_1 = "I want to book a flight."
    agent_response_2 = "Done"

    step_1 = Step(
        goal="Book a flight", success_criteria="Successfully booked a flight"
    )

    mock_gemini_client.generate.return_value = LLMUserConversation.Output(
        next_user_utterance=user_utterance_1,
        step_progresses=[
            StepProgress(
                step=step_1,
                status=StepStatus.COMPLETED,
                justification="User booked a flight.",
            )
        ],
    )

    test_case = {
        "name": "test_case_2",
        "user_utterances": [],
        "steps": [step_1.model_dump()],
    }

    llm_conv = LLMUserConversation(
        genai_client=mock_gemini_client,
        genai_model="gemini-1.5-flash",
        test_case=test_case,
    )

    assert llm_conv.steps_progress[0].status == StepStatus.NOT_STARTED

    got_user_utterance_0, _ = llm_conv.next_user_utterance("")
    assert got_user_utterance_0 == user_utterance_0
    assert llm_conv.get_num_turns() == 1
    assert llm_conv.get_transcript() == "\n".join([f"User: {user_utterance_0}"])

    got_user_utterance_1, _ = llm_conv.next_user_utterance(agent_response_1)
    assert got_user_utterance_1 == user_utterance_1
    assert llm_conv.get_num_turns() == 2
    assert llm_conv.get_transcript() == "\n".join(
        [
            f"User: {user_utterance_0}",
            f"Agent: {agent_response_1}",
            f"User: {user_utterance_1}",
        ]
    )

    assert llm_conv.steps_progress[0].status == StepStatus.COMPLETED

    got_user_utterance_2, _ = llm_conv.next_user_utterance(agent_response_2)
    assert got_user_utterance_2 == ""
    assert llm_conv.get_num_turns() == 3
    assert llm_conv.get_transcript() == "\n".join(
        [
            f"User: {user_utterance_0}",
            f"Agent: {agent_response_1}",
            f"User: {user_utterance_1}",
            f"Agent: {agent_response_2}",
            "User: ",
        ]
    )

    mock_gemini_client.generate.assert_called_once()


def test_llm_user_conversation_max_turns():
    mock_gemini_client = MagicMock()

    user_utterance_0 = "event: welcome"
    agent_response_1 = "Hi, how can I help you?"

    step_1 = Step(
        goal="Book a flight", success_criteria="Successfully booked a flight"
    )

    test_case = {
        "name": "test_case_max_turns",
        "user_utterances": [],
        "steps": [step_1.model_dump()],
    }

    llm_conv = LLMUserConversation(
        genai_client=mock_gemini_client,
        genai_model="gemini-1.5-flash",
        test_case=test_case,
        max_turns=1,
    )

    got_user_utterance_0, _ = llm_conv.next_user_utterance("")
    assert got_user_utterance_0 == user_utterance_0
    assert llm_conv.get_num_turns() == 1
    assert llm_conv.get_transcript() == "\n".join([f"User: {user_utterance_0}"])

    # Last turn since we reached the max turns.
    got_user_utterance_1, _ = llm_conv.next_user_utterance(agent_response_1)
    assert got_user_utterance_1 == ""
    assert llm_conv.get_num_turns() == 2
    assert llm_conv.get_transcript() == "\n".join(
        [
            f"User: {user_utterance_0}",
            f"Agent: {agent_response_1}",
            "User: ",
        ]
    )

    # LLM call never gets made because we reached the max turns.
    mock_gemini_client.generate.assert_not_called()
    assert llm_conv.steps_progress[0].status == StepStatus.NOT_STARTED


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator(mock_llm_conv_class, mock_sessions_class):
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("I want to book a flight", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Setup mock agent responses
    mock_response_1 = MagicMock()
    mock_response_1.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output_1 = MagicMock()
    mock_output_1.text = "Where to?"
    mock_response_1.outputs = [mock_output_1]

    mock_response_2 = MagicMock()
    mock_response_2.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output_2 = MagicMock()
    mock_output_2.text = "Flight booked."
    mock_response_2.outputs = [mock_output_2]
    mock_sessions.run.side_effect = [mock_response_1, mock_response_2]

    # Initialize the SimulationEvals
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    # Run the simulation
    test_case = {"steps": []}
    result_conv = simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
    )

    # Assertions
    mock_sessions.run.assert_any_call(
        session_id="123", event="welcome", variables={}, modality="text"
    )
    mock_sessions.run.assert_any_call(
        session_id="123",
        text="I want to book a flight",
        variables={},
        modality="text",
    )
    mock_eval_conv.next_user_utterance.assert_any_call("Where to?")
    mock_eval_conv.next_user_utterance.assert_any_call("Flight booked.")
    assert result_conv == mock_eval_conv
    assert mock_sessions.run.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator_audio(mock_llm_conv_class, mock_sessions_class):
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("I want to book a flight", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Mock Response 1 (Diagnostic Info only, simulating audio response
    # text capture)
    mock_response_1 = MagicMock()
    mock_output_1 = MagicMock()
    mock_output_1.text = ""  # Empty high-level text

    mock_msg_1 = MagicMock()
    mock_msg_1.role = "model"
    mock_chunk_1 = MagicMock()
    mock_chunk_1._pb.WhichOneof.return_value = "text"
    mock_chunk_1.text = "Where to?"
    mock_msg_1.chunks = [mock_chunk_1]

    mock_diag_1 = MagicMock()
    mock_diag_1.messages = [mock_msg_1]
    mock_output_1.diagnostic_info = mock_diag_1
    mock_response_1.outputs = [mock_output_1]

    # Mock Response 2 (High-level text)
    mock_response_2 = MagicMock()
    mock_output_2 = MagicMock()
    mock_output_2.text = "Flight booked."
    mock_output_2.diagnostic_info = None
    mock_response_2.outputs = [mock_output_2]

    mock_sessions.run.side_effect = [mock_response_1, mock_response_2]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        modality="audio",
    )

    mock_sessions.run.assert_any_call(
        session_id="123", event="welcome", variables={}, modality="audio"
    )
    mock_sessions.run.assert_any_call(
        session_id="123",
        text="I want to book a flight",
        variables={},
        modality="audio",
    )

    # Verify text was extracted from Diagnostic Info
    # Note: text += chunk.text + " " so it should assert "Where to? "
    mock_eval_conv.next_user_utterance.assert_any_call("Where to?")
    mock_eval_conv.next_user_utterance.assert_any_call("Flight booked.")
    assert mock_sessions.run.call_count == 2


def test_parse_agent_response_standard():
    mock_response = MagicMock()
    mock_output = MagicMock()
    mock_output.text = "Hello there"

    # Mock tool calls
    mock_tc = MagicMock()
    mock_tc.tool = "some_tool"
    mock_tc.args = {"arg": "val"}
    mock_output.tool_calls.tool_calls = [mock_tc]

    mock_response.outputs = [mock_output]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    with patch(
        "cxas_scrapi.evals.simulation_evals.Sessions._expand_pb_struct",
        return_value={"arg": "val"},
    ):
        agent_text, trace_chunks, session_ended = (
            simulator._parse_agent_response(mock_response)
        )

    assert agent_text == "Hello there"
    assert any("Tool Call (Output): some_tool" in c for c in trace_chunks)
    assert not session_ended


def test_parse_agent_response_agent_transfer():
    mock_response = MagicMock()
    mock_output = MagicMock()
    mock_output.text = ""

    mock_msg = MagicMock()
    mock_msg.role = "model"
    mock_chunk = MagicMock()
    mock_chunk._pb.WhichOneof.return_value = "agent_transfer"
    mock_chunk.agent_transfer.display_name = "Billing Agent"
    mock_msg.chunks = [mock_chunk]

    mock_diag = MagicMock()
    mock_diag.messages = [mock_msg]
    mock_output.diagnostic_info = mock_diag
    mock_response.outputs = [mock_output]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    agent_text, trace_chunks, session_ended = simulator._parse_agent_response(
        mock_response
    )

    assert any(
        "Agent Transfer: Transferred to Billing Agent" in c
        for c in trace_chunks
    )
    assert not session_ended


def test_parse_agent_response_custom_payload():
    mock_response = MagicMock()
    mock_output = MagicMock()
    mock_output.text = ""

    mock_msg = MagicMock()
    mock_msg.role = "model"
    mock_chunk = MagicMock()
    mock_chunk._pb.WhichOneof.return_value = "payload"
    mock_chunk.payload = {"key": "value"}
    mock_msg.chunks = [mock_chunk]

    mock_diag = MagicMock()
    mock_diag.messages = [mock_msg]
    mock_output.diagnostic_info = mock_diag
    mock_response.outputs = [mock_output]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    with patch(
        "cxas_scrapi.evals.simulation_evals.Sessions._expand_pb_struct",
        return_value={"key": "value"},
    ):
        agent_text, trace_chunks, session_ended = (
            simulator._parse_agent_response(mock_response)
        )

    assert any("Custom Payload:" in c for c in trace_chunks)
    assert not session_ended


def test_parse_agent_response_diagnostic():
    mock_response = MagicMock()
    mock_output = MagicMock()
    mock_output.text = ""

    mock_msg = MagicMock()
    mock_msg.role = "model"
    mock_chunk = MagicMock()
    mock_chunk._pb.WhichOneof.return_value = "text"
    mock_chunk.text = "Hello from diag"
    mock_msg.chunks = [mock_chunk]

    mock_diag = MagicMock()
    mock_diag.messages = [mock_msg]
    mock_output.diagnostic_info = mock_diag
    mock_response.outputs = [mock_output]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    agent_text, trace_chunks, session_ended = simulator._parse_agent_response(
        mock_response
    )

    assert agent_text == "Hello from diag"
    assert any("Agent Text (Diag): Hello from diag" in c for c in trace_chunks)
    assert not session_ended


def test_evaluate_expectations():
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch(
        "cxas_scrapi.evals.simulation_evals.GeminiGenerate"
    ) as mock_gemini_client_class:
        mock_gemini_client = mock_gemini_client_class.return_value
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    # Setup mock output for Gemini
    mock_output = MagicMock()

    mock_output.results = [
        ExpectationResult(
            expectation="Exp 1",
            status=ExpectationStatus.MET,
            justification="Just 1",
        )
    ]
    mock_gemini_client.generate.return_value = mock_output

    eval_conv = MagicMock()
    eval_conv.expectations = ["Exp 1"]

    simulator._evaluate_expectations(eval_conv, ["Trace"], "model", False)

    assert eval_conv.expectation_results == mock_output.results


def test_simulation_report_rendering():
    goals_df = pd.DataFrame([{"goal": "Goal 1", "status": "Met"}])
    expectations_df = pd.DataFrame([{"expectation": "Exp 1", "status": "Met"}])

    report = SimulationReport(goals_df, expectations_df)

    # Test __str__
    str_report = str(report)
    assert "Goal Progress" in str_report
    assert "Expectations" in str_report

    # Test _repr_html_
    html_report = report._repr_html_()
    assert "<h3>Goal Progress</h3>" in html_report
    assert "<h3>Expectations</h3>" in html_report


# Granular unit tests for refactored components


def test_llm_user_check_conversation_status_continue():
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    assert conv._check_conversation_status() is True


def test_llm_user_check_conversation_status_max_turns():
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    conv = LLMUserConversation(
        mock_genai_client, "model", test_case, max_turns=2
    )
    conv.current_turn = 2
    assert conv._check_conversation_status() is False


def test_llm_user_handle_first_turn():
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet", "static_utterance": "Hello"}],
        "session_parameters": {"user_id": "123"},
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    utterance, params = conv._handle_first_turn()
    assert utterance == "Hello"
    assert params["user_id"] == "123"


def test_simulation_evals_add_agent_text():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    evals._add_agent_text(turn, "Hello")
    assert turn.agent == "Hello"
    evals._add_agent_text(turn, "World")
    assert turn.agent == ["Hello", "World"]


def test_simulation_evals_match_tool_response():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    tc = ToolCall(action="my_tool", args={})
    turn = Turn(tool_calls=[tc])
    evals._match_tool_response(turn, "my_tool", {"res": "ok"})
    assert tc.output == {"res": "ok"}


def test_simulation_evals_get_turns_from_local_trace():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    trace = [
        "User: Hi",
        "Agent Text: Hello there",
        "Agent Transfer: Transferred to live_agent",
        'Custom Payload: {"key": "value"}',
    ]
    turns = evals._get_turns_from_local_trace(trace)
    assert len(turns) == 1
    assert turns[0].user == "Hi"
    assert "Hello there" in turns[0].agent
    assert turns[0].tool_calls[0].action == "transfer_to_agent"
    assert turns[0].tool_calls[0].args["agent"] == "live_agent"
    assert any("[Custom Payload]" in text for text in turns[0].agent)


def test_simulation_evals_process_platform_chunk_text():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    evals._process_platform_chunk({"text": "Hello"}, turn)
    assert turn.agent == "Hello"


def test_simulation_evals_process_platform_chunk_tool_call():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"tool_call": {"display_name": "my_tool", "args": {"a": 1}}}
    evals._process_platform_chunk(chunk, turn)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].action == "my_tool"


def test_simulation_evals_process_platform_chunk_agent_transfer():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"agent_transfer": {"display_name": "live_agent"}}
    evals._process_platform_chunk(chunk, turn)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].action == "transfer_to_agent"
    assert turn.tool_calls[0].args["agent"] == "live_agent"


def test_simulation_evals_process_platform_chunk_payload():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"payload": {"key": "value"}}
    evals._process_platform_chunk(chunk, turn)
    assert "[Custom Payload]" in turn.agent


def test_simulation_evals_parse_platform_messages():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    messages = [
        {"role": "user", "chunks": [{"text": "Hello"}]},
        {"role": "agent", "chunks": [{"text": "Hi! How can I help?"}]},
    ]
    turns = []
    evals._parse_platform_messages(messages, turns)
    assert len(turns) == 1
    assert turns[0].user == "Hello"
    assert turns[0].agent == "Hi! How can I help?"


def test_simulation_evals_get_turns_fallback():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    res = {
        "session_id": "sid",
        "detailed_trace": ["User: Hi", "Agent Text: Hello"],
    }
    with patch.object(
        evals, "_get_turns_from_platform", side_effect=Exception("Failed")
    ):
        turns = evals._get_turns(res)
        assert len(turns) == 1
        assert turns[0].user == "Hi"
        assert turns[0].agent == "Hello"


def test_simulation_evals_send_request_with_retry_success():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals.sessions_client = MagicMock()
    evals.sessions_client.run.side_effect = [
        Exception("Transient"),
        MagicMock(),
    ]

    with patch("time.sleep"):  # Avoid slowing down tests
        res = evals._send_request_with_retry("sid", "hi", {}, "text", False)

    assert evals.sessions_client.run.call_count == 2
    assert res is not None


def test_simulation_evals_send_request_with_retry_failure():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals.sessions_client = MagicMock()
    evals.sessions_client.run.side_effect = Exception("Permanent")
    evals.max_retries = 2

    with patch("time.sleep"):
        with pytest.raises(Exception, match="Permanent"):
            evals._send_request_with_retry("sid", "hi", {}, "text", False)

    assert evals.sessions_client.run.call_count == 2


def test_llm_user_prepare_llm_prompt():
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet", "success_criteria": "hi"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    prompt = conv._prepare_llm_prompt()

    assert "greet" in prompt
    assert "hi" in prompt
    assert "Conversation History" in prompt


def test_simulation_evals_prepare_simulation_jobs():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    test_cases = [{"name": "tc1"}, {"name": "tc2"}]
    jobs = evals._prepare_simulation_jobs(test_cases, runs=2)

    assert len(jobs) == 4
    assert jobs[0] == (test_cases[0], 0)
    assert jobs[1] == (test_cases[0], 1)
    assert jobs[2] == (test_cases[1], 0)


def test_simulation_evals_aggregate_simulation_results_parallel():
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    jobs = [({"name": "tc1"}, 0), ({"name": "tc1"}, 1)]

    results = evals._aggregate_simulation_results(
        jobs, runs=2, parallel=2, model="m", modality="text", verbose=False
    )

    assert len(results) == 2
    assert all(r["status"] == "ok" for r in results)
    assert evals._run_single_simulation_job.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.ConversationHistory")
def test_simulation_evals_get_turns_from_platform(mock_ch_class):
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    mock_ch = mock_ch_class.return_value
    mock_conv = MagicMock()
    # Mocking the dictionary conversion behavior
    mock_conv_dict = {
        "turns": [
            {"messages": [{"role": "user", "chunks": [{"text": "hello"}]}]}
        ]
    }
    # SimulationEvals uses type(conv_obj).to_dict(conv_obj)
    with patch("cxas_scrapi.evals.simulation_evals.type") as mock_type:
        mock_type.return_value.to_dict.return_value = mock_conv_dict
        mock_ch.get_conversation.return_value = mock_conv

        turns = evals._get_turns_from_platform("sid")

    assert len(turns) == 1
    assert turns[0].user == "hello"


class MockProto:
    def __init__(self, data):
        self.data = data

    @staticmethod
    def to_dict(obj):
        return obj.data


class TestSimToGolden(unittest.TestCase):
    def setUp(self):
        self.app_name = "projects/p/locations/l/apps/a"

        # Create instance without calling __init__ to avoid complex dependency
        # mocking
        self.sim_evals = MagicMock(spec=SimulationEvals)
        self.sim_evals.app_name = self.app_name
        self.sim_evals.creds = MagicMock()

        # Bind refactored methods to the mock instance so they can be called
        # internally
        methods_to_bind = [
            "export_results_to_golden",
            "_get_turns",
            "_get_turns_from_platform",
            "_get_turns_from_local_trace",
            "_parse_platform_messages",
            "_process_platform_chunk",
            "_handle_text_chunk",
            "_handle_tool_call_chunk",
            "_handle_tool_response_chunk",
            "_handle_agent_transfer_chunk",
            "_handle_payload_chunk",
            "_match_tool_response",
            "_add_agent_text",
            "_parse_trace_line",
        ]
        for method_name in methods_to_bind:
            method = getattr(SimulationEvals, method_name)
            setattr(
                self.sim_evals,
                method_name,
                method.__get__(self.sim_evals, SimulationEvals),
            )

    @patch("cxas_scrapi.evals.simulation_evals.ConversationHistory")
    def test_export_results_to_golden(self, mock_ch_class):
        mock_ch = mock_ch_class.return_value

        # Mock conversation data
        mock_conv_data = {
            "turns": [
                {
                    "messages": [
                        {"role": "user", "chunks": [{"text": "hello"}]},
                        {"role": "agent", "chunks": [{"text": "hi there"}]},
                    ]
                },
                {
                    "messages": [
                        {"role": "user", "chunks": [{"text": "how are you?"}]},
                        {
                            "role": "agent",
                            "chunks": [
                                {"text": "I am good,"},
                                {
                                    "tool_call": {
                                        "display_name": "get_weather",
                                        "args": {"city": "London"},
                                    }
                                },
                            ],
                        },
                    ]
                },
                {
                    "messages": [
                        {
                            "role": "get_weather",
                            "chunks": [
                                {
                                    "tool_response": {
                                        "display_name": "get_weather",
                                        "response": {"temp": 20},
                                    }
                                }
                            ],
                        },
                        {
                            "role": "agent",
                            "chunks": [{"text": "It is 20 degrees."}],
                        },
                    ]
                },
            ]
        }

        mock_ch.get_conversation.return_value = MockProto(mock_conv_data)

        results = [
            {
                "session_id": "session1",
                "name": "Test Conv",
                "expectation_details": [{"expectation": "Must say hi"}],
                "session_parameters": {"key": "val"},
            }
        ]

        # We need to mock Sessions._expand_pb_struct as it's called in the
        # method
        with patch(
            "cxas_scrapi.core.sessions.Sessions._expand_pb_struct",
            side_effect=lambda x: x,
        ):
            yaml_output = self.sim_evals.export_results_to_golden(results)

            # Basic checks on generated YAML
            self.assertIn("user: hello", yaml_output)
            self.assertIn("agent: hi there", yaml_output)
            self.assertIn("user: how are you?", yaml_output)
            self.assertIn("action: get_weather", yaml_output)
            self.assertIn("city: London", yaml_output)
            self.assertIn("output:", yaml_output)
            self.assertIn("temp: 20", yaml_output)
            self.assertIn("- It is 20 degrees.", yaml_output)
            self.assertIn("Must say hi", yaml_output)
            self.assertIn("key: val", yaml_output)


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_accumulates_vars(
    mock_llm_conv_class, mock_sessions_class
):
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    # Multi-turn conversational flow with session vars on Turn 1 and Turn 2
    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {"disclaimer_accepted": True}),
        ("I want to order a hammer", {"product_brand": "DEWALT"}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Mock agent responses
    mock_response_1 = MagicMock()
    mock_response_1.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output_1 = MagicMock()
    mock_output_1.text = "Sure, what brand?"
    mock_response_1.outputs = [mock_output_1]

    mock_response_2 = MagicMock()
    mock_response_2.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output_2 = MagicMock()
    mock_output_2.text = "Hammer ordered."
    mock_response_2.outputs = [mock_output_2]

    captured_variables = []

    def mock_run_side_effect(*args, **kwargs):
        vars_arg = kwargs.get("variables")
        captured_variables.append(
            dict(vars_arg) if vars_arg is not None else None
        )
        if len(captured_variables) == 1:
            return mock_response_1
        return mock_response_2

    mock_sessions.run.side_effect = mock_run_side_effect

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
    )

    # Verify that the variables accumulated sequentially across turns
    assert len(captured_variables) == 2
    # Turn 1: Should pass only Turn 1's variables
    assert captured_variables[0] == {"disclaimer_accepted": True}
    # Turn 2: Should pass accumulated variables with Turn 1 and 2
    assert captured_variables[1] == {
        "disclaimer_accepted": True,
        "product_brand": "DEWALT",
    }
    assert mock_sessions.run.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_adds_final_agent_response_on_session_ended(
    mock_llm_conv_class, mock_sessions_class
):
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    # Setup direct single-turn call that ends session immediately
    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Mock agent response containing a clean end_session tool call
    mock_response = MagicMock()
    mock_output = MagicMock()
    mock_output.text = "Transferring to associate now."

    mock_tc = MagicMock()
    mock_tc.tool = "escalate_human"

    mock_tc_end = MagicMock()
    mock_tc_end.tool = "end_session"

    mock_output.tool_calls.tool_calls = [mock_tc, mock_tc_end]
    mock_response.outputs = [mock_output]
    mock_sessions.run.side_effect = [mock_response]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    with patch(
        "cxas_scrapi.core.sessions.Sessions._expand_pb_struct",
        return_value={},
    ):
        simulator.simulate_conversation(
            test_case=test_case,
            session_id="123",
            console_logging=False,
        )

    # Verify that final agent text is appended to transcript on session end
    mock_eval_conv._add_agent_response.assert_any_call(
        "Transferring to associate now."
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
def test_simulation_evals_init_with_rate_limiter(mock_sessions):
    mock_rate_limiter = MagicMock()
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            _ = SimulationEvals(
                app_name=app_name, rate_limiter=mock_rate_limiter
            )

    mock_sessions.assert_called_once_with(
        app_name,
        rate_limiter=mock_rate_limiter,
    )
