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

import typing
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cxas_scrapi.evals.naturalness import (
    NaturalnessConfig,
    NaturalnessFactor,
    NaturalnessLabel,
    NaturalnessResult,
    TurnNaturalness,
    parse_naturalness_config,
)
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


def test_llm_user_conversation() -> None:
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
        genai_model="gemini-3.1-flash-lite",
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


def test_llm_user_conversation_max_turns() -> None:
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
        genai_model="gemini-3.1-flash-lite",
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
def test_user_simulator(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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
        session_id="123",
        event="welcome",
        variables={},
        modality="text",
        turn_num=0,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
    )
    mock_sessions.run.assert_any_call(
        session_id="123",
        text="I want to book a flight",
        variables={},
        modality="text",
        turn_num=1,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
    )
    mock_eval_conv.next_user_utterance.assert_any_call("Where to?")
    mock_eval_conv.next_user_utterance.assert_any_call("Flight booked.")
    assert result_conv == mock_eval_conv
    assert mock_sessions.run.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator_audio_single_stream(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
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

    mock_interactive_session = MagicMock()
    mock_sessions.create_interactive_session.return_value = (
        mock_interactive_session
    )
    mock_interactive_session.send_turn.side_effect = [
        mock_response_1,
        mock_response_2,
    ]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        modality="audio",
        single_bidi_stream=True,
    )

    mock_sessions.run.assert_not_called()
    mock_sessions.create_interactive_session.assert_called_once_with(
        session_id="123",
        capture_agent_audio=False,
        background_noise_file=None,
        use_tool_fakes=False,
        skip_playback_wait=False,
        voice_config=None,
    )
    mock_interactive_session.start.assert_called_once()
    mock_interactive_session.send_turn.assert_any_call(
        "event: welcome",
        {},
    )
    mock_interactive_session.send_turn.assert_any_call(
        "I want to book a flight",
        {},
    )
    mock_interactive_session.close.assert_called_once()

    # Verify text was extracted from Diagnostic Info
    # Note: text += chunk.text + " " so it should assert "Where to? "
    mock_eval_conv.next_user_utterance.assert_any_call("Where to?")
    mock_eval_conv.next_user_utterance.assert_any_call("Flight booked.")
    assert mock_interactive_session.send_turn.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator_audio(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    """Default audio simulations use one bidi connection per turn."""
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        modality="audio",
    )

    mock_sessions.create_interactive_session.assert_not_called()
    mock_sessions.run.assert_any_call(
        session_id="123",
        event="welcome",
        variables={},
        modality="audio",
        turn_num=0,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
    )
    mock_sessions.run.assert_any_call(
        session_id="123",
        text="I want to book a flight",
        variables={},
        modality="audio",
        turn_num=1,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
    )

    # Verify text was extracted from Diagnostic Info
    # Note: text += chunk.text + " " so it should assert "Where to? "
    mock_eval_conv.next_user_utterance.assert_any_call("Where to?")
    mock_eval_conv.next_user_utterance.assert_any_call("Flight booked.")
    assert mock_sessions.run.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator_audio_with_tool_fakes(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("I want to book a flight", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Mock Response 1
    mock_response_1 = MagicMock()
    mock_output_1 = MagicMock()
    mock_output_1.text = ""

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

    # Mock Response 2
    mock_response_2 = MagicMock()
    mock_output_2 = MagicMock()
    mock_output_2.text = "Flight booked."
    mock_output_2.diagnostic_info = None
    mock_response_2.outputs = [mock_output_2]

    mock_interactive_session = MagicMock()
    mock_sessions.create_interactive_session.return_value = (
        mock_interactive_session
    )
    mock_interactive_session.send_turn.side_effect = [
        mock_response_1,
        mock_response_2,
    ]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        modality="audio",
        use_tool_fakes=True,
        single_bidi_stream=True,
    )

    mock_sessions.create_interactive_session.assert_called_once_with(
        session_id="123",
        capture_agent_audio=False,
        background_noise_file=None,
        use_tool_fakes=True,
        skip_playback_wait=False,
        voice_config=None,
    )
    mock_interactive_session.start.assert_called_once()
    mock_interactive_session.close.assert_called_once()


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_user_simulator_audio_with_eval_enabled(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("I want to book a flight", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    # Mock Response 1
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
    mock_response_1.agent_audio_paths = {
        0: "/tmp/scrapi_evals/123/turn_0_agent.wav"
    }

    # Mock Response 2
    mock_response_2 = MagicMock()
    mock_output_2 = MagicMock()
    mock_output_2.text = "Flight booked."
    mock_output_2.diagnostic_info = None
    mock_response_2.outputs = [mock_output_2]
    mock_response_2.agent_audio_paths = {
        0: "/tmp/scrapi_evals/123/turn_1_agent.wav"
    }

    mock_interactive_session = MagicMock()
    mock_sessions.create_interactive_session.return_value = (
        mock_interactive_session
    )
    mock_interactive_session.send_turn.side_effect = [
        mock_response_1,
        mock_response_2,
    ]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        modality="audio",
        capture_agent_audio=True,
        single_bidi_stream=True,
    )

    mock_sessions.create_interactive_session.assert_called_once_with(
        session_id="123",
        capture_agent_audio=True,
        background_noise_file=None,
        use_tool_fakes=False,
        skip_playback_wait=False,
        voice_config=None,
    )
    mock_interactive_session.start.assert_called_once()
    mock_interactive_session.send_turn.assert_any_call(
        "event: welcome",
        {},
    )
    mock_interactive_session.send_turn.assert_any_call(
        "I want to book a flight",
        {},
    )
    mock_interactive_session.close.assert_called_once()

    assert mock_interactive_session.send_turn.call_count == 2
    assert mock_eval_conv.agent_audio_paths == {
        0: "/tmp/scrapi_evals/123/turn_0_agent.wav",
        1: "/tmp/scrapi_evals/123/turn_1_agent.wav",
    }


def test_parse_agent_response_standard() -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    with patch(
        "cxas_scrapi.evals.simulation_evals.Sessions._expand_pb_struct",
        return_value={"arg": "val"},
    ):
        agent_text, trace_chunks, session_ended, _ = (
            simulator._parse_agent_response(mock_response)
        )

    assert agent_text == "Hello there"
    assert any("Tool Call (Output): some_tool" in c for c in trace_chunks)
    assert not session_ended


def test_parse_agent_response_agent_transfer() -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    (
        _agent_text,
        trace_chunks,
        session_ended,
        _,
    ) = simulator._parse_agent_response(mock_response)

    assert any(
        "Agent Transfer: Transferred to Billing Agent" in c
        for c in trace_chunks
    )
    assert not session_ended


def test_parse_agent_response_custom_payload() -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    with patch(
        "cxas_scrapi.evals.simulation_evals.Sessions._expand_pb_struct",
        return_value={"key": "value"},
    ):
        _agent_text, trace_chunks, session_ended, _ = (
            simulator._parse_agent_response(mock_response)
        )

    assert any("Custom Payload:" in c for c in trace_chunks)
    assert not session_ended


def test_parse_agent_response_diagnostic() -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    (
        agent_text,
        trace_chunks,
        session_ended,
        _,
    ) = simulator._parse_agent_response(mock_response)

    assert agent_text == "Hello from diag"
    assert any("Agent Text (Diag): Hello from diag" in c for c in trace_chunks)
    assert not session_ended


def test_evaluate_expectations() -> None:
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


def test_simulation_report_rendering() -> None:
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


def test_llm_user_check_conversation_status_continue() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    assert conv._check_conversation_status() is True


def test_llm_user_check_conversation_status_max_turns() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    conv = LLMUserConversation(
        mock_genai_client, "model", test_case, max_turns=2
    )
    conv.current_turn = 2
    assert conv._check_conversation_status() is False


def test_llm_user_conversation_default_max_turns() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    assert conv.max_turns == 30


def test_llm_user_conversation_max_turns_from_test_case() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
        "max_turns": 15,
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    assert conv.max_turns == 15


def test_llm_user_conversation_max_turns_override() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
        "max_turns": 15,
    }
    conv = LLMUserConversation(
        mock_genai_client, "model", test_case, max_turns=5
    )
    assert conv.max_turns == 5


def test_simulate_conversation_default_max_turns() -> None:
    mock_sessions = MagicMock()
    mock_response = MagicMock()
    mock_response.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output = MagicMock()
    mock_output.text = "Hello"
    mock_response.outputs = [mock_output]
    mock_sessions.run.return_value = mock_response

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)
    simulator.sessions_client = mock_sessions

    test_case = {
        "name": "test_tc_default",
        "steps": [{"goal": "greet"}],
    }
    with patch.object(
        LLMUserConversation, "next_user_utterance", return_value=("", {})
    ):
        conv = simulator.simulate_conversation(
            test_case=test_case, console_logging=False
        )
    assert conv.max_turns == 30


def test_simulate_conversation_uses_test_case_max_turns() -> None:
    mock_sessions = MagicMock()
    mock_response = MagicMock()
    mock_response.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output = MagicMock()
    mock_output.text = "Hello"
    mock_response.outputs = [mock_output]
    mock_sessions.run.return_value = mock_response

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)
    simulator.sessions_client = mock_sessions

    test_case = {
        "name": "test_tc_max_turns",
        "max_turns": 8,
        "steps": [{"goal": "greet"}],
    }
    with patch.object(
        LLMUserConversation, "next_user_utterance", return_value=("", {})
    ):
        conv = simulator.simulate_conversation(
            test_case=test_case, console_logging=False
        )
    assert conv.max_turns == 8


def test_simulate_conversation_uses_explicit_max_turns() -> None:
    mock_sessions = MagicMock()
    mock_response = MagicMock()
    mock_response.session.name = (
        "projects/test/locations/us/apps/123-abc/sessions/123"
    )
    mock_output = MagicMock()
    mock_output.text = "Hello"
    mock_response.outputs = [mock_output]
    mock_sessions.run.return_value = mock_response

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)
    simulator.sessions_client = mock_sessions

    test_case = {
        "name": "test_tc_max_turns",
        "max_turns": 8,
        "steps": [{"goal": "greet"}],
    }
    with patch.object(
        LLMUserConversation, "next_user_utterance", return_value=("", {})
    ):
        conv = simulator.simulate_conversation(
            test_case=test_case, max_turns=12, console_logging=False
        )
    assert conv.max_turns == 12


def test_llm_user_get_active_step_index() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}, {"goal": "ask_hours"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    # Initially first step is active (index 0)
    assert conv._get_active_step_index() == 0

    # Mark first step as completed
    conv.steps_progress[0].status = StepStatus.COMPLETED
    assert conv._get_active_step_index() == 1

    # Mark second step as completed
    conv.steps_progress[1].status = StepStatus.COMPLETED
    assert conv._get_active_step_index() is None


def test_llm_user_next_user_utterance_static_utterance_bypass() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [
            {
                "goal": "greet",
                "static_utterance": "Hello First Step",
                "inject_variables": {"var1": "val1"},
            },
            {"goal": "ask_hours", "static_utterance": "What are the hours?"},
        ],
        "session_parameters": {"user_id": "123"},
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)

    # 1. Turn 0: Returns initial utterance
    utterance, variables = conv.next_user_utterance()
    assert utterance == "event: welcome"
    assert variables == {"user_id": "123"}
    mock_genai_client.generate.assert_not_called()

    # 2. Turn 1: Bypasses LLM and returns first step's static utterance
    utterance, variables = conv.next_user_utterance("Agent response to welcome")
    assert utterance == "Hello First Step"
    assert variables == {"user_id": "123", "var1": "val1"}
    assert conv.steps_progress[0].status == StepStatus.COMPLETED
    assert conv.steps_progress[0].justification == (
        "Static utterance sent (bypassed LLM)."
    )
    mock_genai_client.generate.assert_not_called()

    # 3. Turn 2: Active step is now index 1 which is also static
    utterance, variables = conv.next_user_utterance(
        "Agent response to first step"
    )
    assert utterance == "What are the hours?"
    assert variables == {"user_id": "123"}
    assert conv.steps_progress[1].status == StepStatus.COMPLETED
    assert conv.steps_progress[1].justification == (
        "Static utterance sent (bypassed LLM)."
    )
    mock_genai_client.generate.assert_not_called()


def test_simulation_evals_retains_app_name() -> None:
    """`Common.__init__` assigns `self.app_name` unconditionally, so an
    assignment made before `super().__init__()` is silently replaced with
    `None`. That left every `self.app_name` consumer resolving conversation
    and trace resources under the name `"None/..."`."""
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    assert evals.app_name == app_name


def test_simulation_evals_add_agent_text() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    evals._add_agent_text(turn, "Hello")
    assert turn.agent == "Hello"
    evals._add_agent_text(turn, "World")
    assert turn.agent == ["Hello", "World"]


def test_simulation_evals_match_tool_response() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    tc = ToolCall(action="my_tool", args={})
    turn = Turn(tool_calls=[tc])
    evals._match_tool_response(turn, "my_tool", {"res": "ok"})
    assert tc.output == {"res": "ok"}


def test_simulation_evals_get_turns_from_local_trace() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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


def test_simulation_evals_process_platform_chunk_text() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    evals._process_platform_chunk({"text": "Hello"}, turn)
    assert turn.agent == "Hello"


def test_simulation_evals_process_platform_chunk_tool_call() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"tool_call": {"display_name": "my_tool", "args": {"a": 1}}}
    evals._process_platform_chunk(chunk, turn)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].action == "my_tool"


def test_simulation_evals_process_platform_chunk_agent_transfer() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"agent_transfer": {"display_name": "live_agent"}}
    evals._process_platform_chunk(chunk, turn)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].action == "transfer_to_agent"
    assert turn.tool_calls[0].args["agent"] == "live_agent"


def test_simulation_evals_process_platform_chunk_payload() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)
    turn = Turn(tool_calls=[])
    chunk = {"payload": {"key": "value"}}
    evals._process_platform_chunk(chunk, turn)
    assert "[Custom Payload]" in turn.agent


def test_simulation_evals_parse_platform_messages() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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


def test_simulation_evals_get_turns_fallback() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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


def test_simulation_evals_send_request_with_retry_success() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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


def test_simulation_evals_send_request_with_retry_failure() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals.sessions_client = MagicMock()
    evals.sessions_client.run.side_effect = Exception("Permanent")
    evals.max_retries = 2

    with patch("time.sleep"), pytest.raises(Exception, match="Permanent"):
        evals._send_request_with_retry("sid", "hi", {}, "text", False)

    assert evals.sessions_client.run.call_count == 2


def test_llm_user_prepare_llm_prompt() -> None:
    mock_genai_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet", "success_criteria": "hi"}],
    }
    conv = LLMUserConversation(mock_genai_client, "model", test_case)
    prompt = conv._prepare_llm_prompt()

    assert "greet" in prompt
    assert "hi" in prompt
    assert "Conversation History" in prompt


def test_simulation_evals_prepare_simulation_jobs() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    test_cases = [{"name": "tc1"}, {"name": "tc2"}]
    jobs = evals._prepare_simulation_jobs(test_cases, runs=2)

    assert len(jobs) == 4
    assert jobs[0] == (test_cases[0], 0)
    assert jobs[1] == (test_cases[0], 1)
    assert jobs[2] == (test_cases[1], 0)


def test_simulation_evals_aggregate_simulation_results_parallel() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    jobs = [({"name": "tc1"}, 0), ({"name": "tc1"}, 1)]

    results = evals._aggregate_simulation_results(
        jobs,
        runs=2,
        parallel=2,
        sim_user_model="m_sim",
        eval_model="m_eval",
        modality="text",
        verbose=False,
    )

    assert len(results) == 2
    assert all(r["status"] == "ok" for r in results)
    assert evals._run_single_simulation_job.call_count == 2


@patch("cxas_scrapi.evals.simulation_evals.ConversationHistory")
def test_simulation_evals_get_turns_from_platform(
    mock_ch_class: typing.Any,
) -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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
    def __init__(self, data: typing.Any) -> None:
        self.data = data

    @staticmethod
    def to_dict(obj: typing.Any) -> typing.Any:
        return obj.data


class TestSimToGolden(unittest.TestCase):
    def setUp(self) -> None:
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
    def test_export_results_to_golden(self, mock_ch_class: typing.Any) -> None:
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
            assert "user: hello" in yaml_output
            assert "agent: hi there" in yaml_output
            assert "user: how are you?" in yaml_output
            assert "action: get_weather" in yaml_output
            assert "city: London" in yaml_output
            assert "output:" in yaml_output
            assert "temp: 20" in yaml_output
            assert "- It is 20 degrees." in yaml_output
            assert "Must say hi" in yaml_output
            assert "key: val" in yaml_output


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_accumulates_vars(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
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

    def mock_run_side_effect(
        *args: typing.Any, **kwargs: typing.Any
    ) -> typing.Any:
        vars_arg = kwargs.get("variables")
        captured_variables.append(
            dict(vars_arg) if vars_arg is not None else None
        )
        if len(captured_variables) == 1:
            return mock_response_1
        return mock_response_2

    mock_sessions.run.side_effect = mock_run_side_effect

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
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
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
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
    # Verify that final agent response triggers evaluation
    mock_eval_conv._next_user_utterance.assert_called_once()


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
def test_simulation_evals_init_with_rate_limiter_and_deployment_id(
    mock_sessions: typing.Any,
) -> None:
    mock_rate_limiter = MagicMock()
    app_name = "projects/test/locations/us/apps/123-abc"
    deployment_id = "xyz"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            _ = SimulationEvals(
                app_name=app_name,
                deployment_id=deployment_id,
                rate_limiter=mock_rate_limiter,
            )

    mock_sessions.assert_called_once_with(
        app_name,
        deployment_id=deployment_id,
        rate_limiter=mock_rate_limiter,
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_simulate_conversation_use_tool_fakes(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    mock_response = MagicMock()
    mock_response.outputs = []
    mock_sessions.run.return_value = mock_response

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    test_case = {"steps": []}
    simulator.simulate_conversation(
        test_case=test_case,
        session_id="123",
        console_logging=False,
        use_tool_fakes=True,
    )

    mock_sessions.run.assert_called_once_with(
        session_id="123",
        event="welcome",
        variables={},
        modality="text",
        turn_num=0,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=True,
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_voice_config(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("Bonjour", {}),
        ("", {}),
    ]
    mock_eval_conv.steps_progress = []

    mock_response_1 = MagicMock()
    mock_response_1.outputs = [MagicMock(text="Bonjour!")]
    mock_response_2 = MagicMock()
    mock_response_2.outputs = [MagicMock(text="Au revoir!")]
    mock_sessions.run.side_effect = [mock_response_1, mock_response_2]

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    # Custom voice_config passed via simulate_conversation
    custom_voice = {"language_code": "fr-FR", "voice_name": "fr-FR-Standard-G"}

    # Test 1: Passed as function argument
    simulator.simulate_conversation(
        test_case={"steps": []},
        session_id="123",
        console_logging=False,
        voice_config=custom_voice,
    )

    # Verify that Sessions.run was called with the custom voice_config
    mock_sessions.run.assert_any_call(
        session_id="123",
        event="welcome",
        variables={},
        modality="text",
        turn_num=0,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
        voice_config=custom_voice,
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
def test_simulation_evals_run_simulations_use_tool_fakes(
    mock_sessions: typing.Any,
) -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    test_cases = [{"name": "tc1"}]

    results = evals.run_simulations(
        test_cases=test_cases,
        runs=1,
        parallel=1,
        sim_user_model="gemini-3.1-flash-lite",
        eval_model="gemini-3.1-pro-preview",
        use_tool_fakes=True,
    )

    assert len(results) == 1
    evals._run_single_simulation_job.assert_called_once_with(
        test_cases[0],
        0,
        1,
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
        "text",
        False,
        1,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=True,
        skip_playback_wait=False,
        single_bidi_stream=False,
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
def test_simulation_evals_run_simulations_use_tool_fakes_parallel(
    mock_sessions: typing.Any,
) -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    test_cases = [{"name": "tc1"}, {"name": "tc2"}]

    results = evals.run_simulations(
        test_cases=test_cases,
        runs=1,
        parallel=2,
        sim_user_model="gemini-3.1-flash-lite",
        eval_model="gemini-3.1-pro-preview",
        use_tool_fakes=True,
    )

    assert len(results) == 2
    assert evals._run_single_simulation_job.call_count == 2
    evals._run_single_simulation_job.assert_any_call(
        test_cases[0],
        0,
        1,
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
        "text",
        False,
        2,
        capture_agent_audio=False,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=True,
        skip_playback_wait=False,
        single_bidi_stream=False,
    )


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
def test_simulation_evals_run_simulations_capture_agent_audio(
    mock_sessions: typing.Any,
) -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    test_cases = [
        {
            "name": "tc1",
            "expectations": [
                {
                    "title": "Audio Audit",
                    "expectation": "Audio matches text",
                    "requires_audio_paths": True,
                }
            ],
        }
    ]

    results = evals.run_simulations(
        test_cases=test_cases,
        runs=1,
        parallel=1,
        sim_user_model="gemini-1.5-flash",
        eval_model="gemini-1.5-flash",
        modality="audio",
        capture_agent_audio=True,
    )

    assert len(results) == 1
    evals._run_single_simulation_job.assert_called_once_with(
        test_cases[0],
        0,
        1,
        "gemini-1.5-flash",
        "gemini-1.5-flash",
        "audio",
        False,
        1,
        capture_agent_audio=True,
        background_noise_file=None,
        burst_noise_files=None,
        use_tool_fakes=False,
        skip_playback_wait=False,
        single_bidi_stream=False,
    )


def test_simulation_evals_with_audio_expectations() -> None:
    test_case = {
        "steps": [],
        "expectations": ["text expectation"],
        "audio_expectations": ["audio expectation"],
    }

    patch_path = "cxas_scrapi.evals.simulation_evals.GeminiGenerate"
    with patch(patch_path) as mock_genai:
        conv = LLMUserConversation(
            genai_client=mock_genai,
            genai_model="gemini-1.5-flash",
            test_case=test_case,
        )

    assert conv.expectations == ["text expectation"]
    assert conv.audio_expectations == [
        {"expectation": "audio expectation", "requires_audio_paths": True}
    ]


def test_simulation_evals_expectations_only_passing() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name, expectations_only=True)

    mock_conv = MagicMock()
    # 0/2 goals completed
    mock_conv.steps_progress = [
        MagicMock(status=StepStatus.IN_PROGRESS),
        MagicMock(status=StepStatus.IN_PROGRESS),
    ]
    # 2/2 expectations met
    mock_conv.expectation_results = [
        MagicMock(status=ExpectationStatus.MET),
        MagicMock(status=ExpectationStatus.MET),
    ]
    mock_conv.current_turn = 1
    mock_conv.get_transcript.return_value = "transcript"
    mock_conv.naturalness_result = None

    with patch.object(evals, "simulate_conversation", return_value=mock_conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )
        assert res["passed"] is True


def test_simulation_evals_expectations_only_failing() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name, expectations_only=True)

    mock_conv = MagicMock()
    # 2/2 goals completed
    mock_conv.steps_progress = [
        MagicMock(status=StepStatus.COMPLETED),
        MagicMock(status=StepStatus.COMPLETED),
    ]
    # 1/2 expectations met
    mock_conv.expectation_results = [
        MagicMock(status=ExpectationStatus.MET),
        MagicMock(status=ExpectationStatus.NOT_MET),
    ]
    mock_conv.current_turn = 1
    mock_conv.get_transcript.return_value = "transcript"
    mock_conv.naturalness_result = None

    with patch.object(evals, "simulate_conversation", return_value=mock_conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )
        assert res["passed"] is False


def test_simulation_evals_expectations_only_fallback() -> None:
    app_name = "projects/p/locations/l/apps/a"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name, expectations_only=True)

    mock_conv = MagicMock()
    # 2/2 goals completed
    mock_conv.steps_progress = [
        MagicMock(status=StepStatus.COMPLETED),
        MagicMock(status=StepStatus.COMPLETED),
    ]
    # 0 expectations
    mock_conv.expectation_results = []
    mock_conv.current_turn = 1
    mock_conv.get_transcript.return_value = "transcript"
    mock_conv.naturalness_result = None

    with patch.object(evals, "simulate_conversation", return_value=mock_conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )
        assert res["passed"] is True


def test_llm_user_conversation_custom_initial_utterance() -> None:
    mock_gemini_client = MagicMock()
    test_case = {
        "steps": [{"goal": "greet"}],
    }
    llm_conv = LLMUserConversation(
        genai_client=mock_gemini_client,
        genai_model="gemini-3.1-flash-lite",
        test_case=test_case,
        initial_utterance="Hello Agent",
    )
    got_user_utterance_0, _ = llm_conv.next_user_utterance("")
    assert got_user_utterance_0 == "Hello Agent"


def test_simulation_evals_run_simulations_progress_callback() -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            evals = SimulationEvals(app_name=app_name)

    evals._run_single_simulation_job = MagicMock(return_value={"status": "ok"})
    test_cases = [{"name": "tc1"}, {"name": "tc2"}]

    progress_calls = []

    def callback(current: typing.Any, total: typing.Any) -> None:
        progress_calls.append((current, total))

    evals.run_simulations(
        test_cases=test_cases,
        runs=2,
        parallel=1,
        progress_callback=callback,
    )

    assert len(progress_calls) == 4
    assert progress_calls == [(1, 4), (2, 4), (3, 4), (4, 4)]


@patch("cxas_scrapi.evals.simulation_evals.Sessions")
@patch("cxas_scrapi.evals.simulation_evals.LLMUserConversation")
def test_simulation_evals_escalation_transfer_handling(
    mock_llm_conv_class: typing.Any, mock_sessions_class: typing.Any
) -> None:
    mock_sessions = mock_sessions_class.return_value
    mock_eval_conv = mock_llm_conv_class.return_value

    mock_eval_conv.next_user_utterance.side_effect = [
        ("event: welcome", {}),
        ("", {}),
    ]
    step = Step(goal="escalate to human", success_criteria="escalate to agent")
    step_prog = StepProgress(step=step, status=StepStatus.NOT_STARTED)
    mock_eval_conv.steps_progress = [step_prog]
    mock_eval_conv.expectations = []

    mock_response = MagicMock()

    app_name = "projects/test/locations/us/apps/123-abc"
    with patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"):  # noqa: SIM117
        with patch("cxas_scrapi.core.apps.AgentServiceClient"):
            simulator = SimulationEvals(app_name=app_name)

    with patch.object(
        simulator,
        "_parse_agent_response",
        return_value=("Transferring you.", [], True, []),
    ):
        mock_sessions.run.return_value = mock_response

        test_case = {"steps": [step.model_dump()]}
        simulator.simulate_conversation(
            test_case=test_case,
            session_id="123",
            console_logging=False,
        )

    assert step_prog.status == StepStatus.COMPLETED
    assert (
        "Agent ended session via escalation/transfer" in step_prog.justification
    )


def test_simulation_evals_default_vertex_location() -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with (
        patch(
            "cxas_scrapi.evals.simulation_evals.GeminiGenerate"
        ) as mock_gemini,
        patch("cxas_scrapi.core.apps.AgentServiceClient"),
    ):
        simulator = SimulationEvals(app_name=app_name)
    assert simulator.vertex_location == "global"
    mock_gemini.assert_called_once_with(
        project_id="test",
        location="global",
        credentials=simulator.creds,
    )


def test_simulation_evals_custom_vertex_location() -> None:
    app_name = "projects/test/locations/us/apps/123-abc"
    with (
        patch(
            "cxas_scrapi.evals.simulation_evals.GeminiGenerate"
        ) as mock_gemini,
        patch("cxas_scrapi.core.apps.AgentServiceClient"),
    ):
        simulator = SimulationEvals(
            app_name=app_name, vertex_location="us-central1"
        )
    assert simulator.vertex_location == "us-central1"
    mock_gemini.assert_called_once_with(
        project_id="test",
        location="us-central1",
        credentials=simulator.creds,
    )


def _naturalness_evals(**kwargs: typing.Any) -> SimulationEvals:
    app_name = "projects/p/locations/l/apps/a"
    with (
        patch("cxas_scrapi.evals.simulation_evals.GeminiGenerate"),
        patch("cxas_scrapi.core.apps.AgentServiceClient"),
    ):
        return SimulationEvals(app_name=app_name, **kwargs)


def _passing_conv(naturalness_result: typing.Any = None) -> MagicMock:
    conv = MagicMock()
    conv.steps_progress = [MagicMock(status=StepStatus.COMPLETED)]
    conv.expectation_results = []
    conv.current_turn = 1
    conv.get_transcript.return_value = "transcript"
    conv.naturalness_result = naturalness_result
    return conv


def test_simulation_naturalness_absent_leaves_results_unchanged() -> None:
    """Backward compatibility: no config means no naturalness keys."""
    evals = _naturalness_evals()
    conv = _passing_conv(naturalness_result=None)

    with patch.object(evals, "simulate_conversation", return_value=conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )

    assert res["passed"] is True
    assert "naturalness" not in res
    assert "naturalness_label" not in res
    assert "naturalness_details" not in res


def test_simulation_naturalness_adds_result_keys() -> None:
    evals = _naturalness_evals()
    result = NaturalnessResult(
        overall_score=4.1,
        overall_label=NaturalnessLabel.HUMAN_LIKE,
        turn_count=2,
    )
    conv = _passing_conv(naturalness_result=result)

    with patch.object(evals, "simulate_conversation", return_value=conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test", "naturalness_metric": True},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )

    assert res["naturalness"] == "4.1/5"
    assert res["naturalness_label"] == "Human-like"
    assert res["naturalness_details"]["overall_score"] == 4.1
    # Informational only: the simulation still passes.
    assert res["passed"] is True


def test_simulation_naturalness_threshold_can_fail_a_run() -> None:
    evals = _naturalness_evals()
    result = NaturalnessResult(
        overall_score=2.0,
        overall_label=NaturalnessLabel.BOT_LIKE,
        pass_threshold=3.5,
        passed=False,
    )
    conv = _passing_conv(naturalness_result=result)

    with patch.object(evals, "simulate_conversation", return_value=conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="text",
            verbose=False,
            parallel=1,
        )

    assert res["passed"] is False


def test_simulation_naturalness_run_level_override_is_persisted() -> None:
    """run_simulations must stash the override for monkeypatched runners."""
    evals = _naturalness_evals()
    assert evals.naturalness is None

    with patch.object(
        evals, "_run_single_simulation_job", return_value={"passed": True}
    ):
        evals.run_simulations(
            [{"name": "test"}], runs=1, parallel=1, naturalness=True
        )

    assert evals.naturalness is True


def test_simulation_naturalness_run_level_false_force_disables() -> None:
    """`False` must survive as `False`, not be treated as "unset".

    This is the hill-climbing switch: iterate on correctness with grading
    off, even for test cases that declare the metric, then turn it back on.
    """
    evals = _naturalness_evals()

    with patch.object(
        evals, "_run_single_simulation_job", return_value={"passed": True}
    ):
        evals.run_simulations(
            [{"name": "test"}], runs=1, parallel=1, naturalness=False
        )

    assert evals.naturalness is False
    # A test case that declared the metric is still switched off by it.
    assert (
        parse_naturalness_config(
            {"naturalness_metric": {"pass_threshold": 3.5}}, evals.naturalness
        )
        is None
    )


def test_simulate_conversation_skips_naturalness_when_unconfigured() -> None:
    evals = _naturalness_evals()
    conv = MagicMock()
    conv.naturalness_result = None

    with patch(
        "cxas_scrapi.evals.simulation_evals.evaluate_naturalness"
    ) as mock_eval:
        evals._evaluate_naturalness(conv, ["User: hi"], "model", False, None)

    mock_eval.assert_not_called()
    assert conv.naturalness_result is None


def test_simulation_naturalness_latency_failure_fails_a_run() -> None:
    """A hard failure sinks the run even without a pass_threshold."""
    evals = _naturalness_evals()
    result = NaturalnessResult(
        overall_score=4.6,
        overall_label=NaturalnessLabel.HUMAN_LIKE,
        passed=False,
        failure_reasons=["perceived latency reached 5.2s on turn 2"],
    )
    conv = _passing_conv(naturalness_result=result)

    with patch.object(evals, "simulate_conversation", return_value=conv):
        res = evals._run_single_simulation_job(
            tc={"name": "test"},
            run_idx=0,
            runs=1,
            sim_user_model="fake",
            eval_model="fake",
            modality="audio",
            verbose=False,
            parallel=1,
        )

    assert res["passed"] is False
    assert res["naturalness_details"]["failure_reasons"]


def test_evaluate_naturalness_auto_enables_audio_in_audio_modality() -> None:
    """Audio modality is the case the metric exists for, so listen by default."""
    evals = _naturalness_evals()
    conv = MagicMock()

    with (
        patch.object(
            evals, "_conversation_latency", return_value=({0: 900.0}, None)
        ),
        patch.object(
            evals, "_resolve_agent_audio", return_value={0: "gs://b/a.wav"}
        ) as mock_audio,
        patch(
            "cxas_scrapi.evals.simulation_evals.evaluate_naturalness"
        ) as mock_eval,
    ):
        evals._evaluate_naturalness(
            conv,
            ["User: hi"],
            "model",
            False,
            NaturalnessConfig(),
            session_id="sess-1",
            modality="audio",
        )

    mock_audio.assert_called_once()
    kwargs = mock_eval.call_args.kwargs
    assert kwargs["config"].use_audio is True
    assert kwargs["audio_paths"] == {0: "gs://b/a.wav"}
    assert kwargs["latency_ms_by_turn"] == {0: 900.0}


def test_evaluate_naturalness_text_modality_skips_audio_lookup() -> None:
    evals = _naturalness_evals()
    conv = MagicMock()

    with (
        patch.object(evals, "_conversation_latency", return_value=({}, None)),
        patch.object(evals, "_resolve_agent_audio") as mock_audio,
        patch(
            "cxas_scrapi.evals.simulation_evals.evaluate_naturalness"
        ) as mock_eval,
    ):
        evals._evaluate_naturalness(
            conv,
            ["User: hi"],
            "model",
            False,
            NaturalnessConfig(),
            session_id="sess-1",
            modality="text",
        )

    mock_audio.assert_not_called()
    assert mock_eval.call_args.kwargs["audio_paths"] is None


def test_conversation_latency_reads_perceived_latency_per_turn() -> None:
    evals = _naturalness_evals()
    normalized = {
        "start_time": "2024-01-01T00:00:00Z",
        "turn_metrics": [
            {"turn": 0, "perceived_latency_ms": 1200},
            {"turn": 1, "perceived_latency_ms": None},
            {"turn": 2, "perceived_latency_ms": 4100.5},
        ],
    }

    with (
        patch("cxas_scrapi.evals.simulation_evals.ConversationHistory"),
        patch(
            "cxas_scrapi.evals.simulation_evals.trace_report.normalize",
            return_value=normalized,
        ),
    ):
        latencies, start_time = evals._conversation_latency("sess-1")

    # The turn with no measurement is omitted rather than defaulted.
    assert latencies == {0: 1200.0, 2: 4100.5}
    assert start_time == "2024-01-01T00:00:00Z"


def test_conversation_latency_tolerates_an_unreachable_trace() -> None:
    """Latency is a nice-to-have; a failed fetch must not break the run."""
    evals = _naturalness_evals()

    with patch(
        "cxas_scrapi.evals.simulation_evals.ConversationHistory",
        side_effect=RuntimeError("no trace"),
    ):
        latencies, start_time = evals._conversation_latency("sess-1")

    assert latencies == {}
    assert start_time is None


def test_resolve_agent_audio_maps_one_based_recordings_to_turns() -> None:
    """agent-turn-N.wav is turn N-1, so the map has to be shifted."""
    evals = _naturalness_evals()
    conv = MagicMock()

    with patch("cxas_scrapi.evals.simulation_evals.Traces") as mock_traces:
        mock_traces.return_value.get_agent_audio_uris.return_value = {
            1: "gs://b/agent-turn-1.wav",
            2: "gs://b/agent-turn-2.wav",
        }
        paths = evals._resolve_agent_audio(
            conv, "sess-1", None, NaturalnessConfig()
        )

    assert paths == {
        0: "gs://b/agent-turn-1.wav",
        1: "gs://b/agent-turn-2.wav",
    }


def test_resolve_agent_audio_falls_back_to_local_capture() -> None:
    evals = _naturalness_evals()
    conv = MagicMock()
    conv.agent_audio_paths = {0: "/tmp/turn0.wav"}

    with patch("cxas_scrapi.evals.simulation_evals.Traces") as mock_traces:
        mock_traces.return_value.get_agent_audio_uris.return_value = {}
        paths = evals._resolve_agent_audio(
            conv, "sess-1", None, NaturalnessConfig()
        )

    assert paths == {0: "/tmp/turn0.wav"}


def test_resolve_agent_audio_respects_source_none() -> None:
    evals = _naturalness_evals()
    conv = MagicMock()
    conv.agent_audio_paths = {0: "/tmp/turn0.wav"}

    with patch("cxas_scrapi.evals.simulation_evals.Traces") as mock_traces:
        paths = evals._resolve_agent_audio(
            conv, "sess-1", None, NaturalnessConfig(audio_source="none")
        )

    mock_traces.assert_not_called()
    assert paths == {}


def test_naturalness_report_shows_latency_and_failure() -> None:
    conv = LLMUserConversation(
        genai_client=MagicMock(),
        genai_model="fake",
        test_case={"name": "t", "steps": []},
    )
    conv.naturalness_result = NaturalnessResult(
        overall_score=4.5,
        overall_label=NaturalnessLabel.HUMAN_LIKE,
        turns=[
            TurnNaturalness(
                turn_index=0,
                score=2.5,
                factors=[
                    NaturalnessFactor(
                        quality="perceivedLatency", score=1, value="5.2s"
                    )
                ],
            )
        ],
        latency_ms_by_turn={0: 5200.0},
        failure_reasons=["perceived latency reached 5.2s on turn 0"],
    )

    report = conv.generate_report()

    assert report.naturalness_df is not None
    assert report.naturalness_df.loc[0, "latency_s"] == 5.2
    assert report.naturalness_df.loc[0, "perceivedLatency"] == 1
    assert "FAILED" in report.naturalness_headline
    assert "5.2s" in report.naturalness_headline


def test_conversation_latency_polls_until_the_trace_is_published() -> None:
    """The platform publishes a conversation some time after the call ends."""
    evals = _naturalness_evals()
    normalized = {
        "start_time": None,
        "turn_metrics": [{"turn": 0, "perceived_latency_ms": 900}],
    }
    history = MagicMock()
    history.get_conversation.side_effect = [
        RuntimeError("404 not found"),
        RuntimeError("404 not found"),
        "conversation",
    ]

    with (
        patch(
            "cxas_scrapi.evals.simulation_evals.ConversationHistory",
            return_value=history,
        ),
        patch(
            "cxas_scrapi.evals.simulation_evals.trace_report.normalize",
            return_value=normalized,
        ),
        patch("cxas_scrapi.evals.simulation_evals.time.sleep") as mock_sleep,
    ):
        latencies, _ = evals._conversation_latency("sess-1", timeout_s=120.0)

    assert latencies == {0: 900.0}
    assert history.get_conversation.call_count == 3
    # A steady poll, so the trace is picked up as soon as it lands.
    assert [c.args[0] for c in mock_sleep.call_args_list] == [5.0, 5.0]


def test_conversation_latency_gives_up_within_its_budget() -> None:
    evals = _naturalness_evals()
    history = MagicMock()
    history.get_conversation.side_effect = RuntimeError("404 not found")

    with (
        patch(
            "cxas_scrapi.evals.simulation_evals.ConversationHistory",
            return_value=history,
        ),
        patch("cxas_scrapi.evals.simulation_evals.time.sleep") as mock_sleep,
    ):
        latencies, start_time = evals._conversation_latency(
            "sess-1", timeout_s=0.0
        )

    assert latencies == {}
    assert start_time is None
    # A zero budget must not sleep at all.
    mock_sleep.assert_not_called()


def test_conversation_latency_fetches_over_rest() -> None:
    """A conversation with audio exceeds gRPC's 4 MB response limit."""
    evals = _naturalness_evals()

    with (
        patch(
            "cxas_scrapi.evals.simulation_evals.ConversationHistory"
        ) as mock_history,
        patch(
            "cxas_scrapi.evals.simulation_evals.trace_report.normalize",
            return_value={"turn_metrics": []},
        ),
    ):
        evals._conversation_latency("sess-1")

    assert mock_history.call_args.kwargs["transport"] == "rest"
