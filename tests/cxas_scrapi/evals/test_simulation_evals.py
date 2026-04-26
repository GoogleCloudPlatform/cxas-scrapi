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

from unittest.mock import MagicMock, patch

import pandas as pd
from cxas_scrapi.evals.simulation_evals import (
    LLMUserConversation,
    SimulationEvals,
    SimulationReport,
    Step,
    StepProgress,
    StepStatus,
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
