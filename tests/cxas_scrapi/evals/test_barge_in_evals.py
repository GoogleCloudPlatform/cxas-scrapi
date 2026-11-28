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
import logging
import os
import queue
import time
import typing
import uuid
import wave
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.ces_v1beta import types

from cxas_scrapi.core.response_parser import ParsedSessionResponse
from cxas_scrapi.core.sessions import (
    AgentTurnManager,
    BargeInMetadata,
    BidiInteractiveSession,
    BidiSessionError,
    BidiSessionHandler,
    ScrapiRunSessionResponse,
)
from cxas_scrapi.evals.simulation_evals import (
    BargeInGenerationOutput,
    LLMUserConversation,
    SimulationEvals,
    Step,
    StepProgress,
    StepStatus,
    cleanup_session_dir,
    generate_model_barge_in,
)
from cxas_scrapi.evals.turn_evals import (
    TurnEvals,
    TurnExpectation,
    TurnOperator,
    TurnStep,
    TurnTestCase,
    _coerce_expected_bool,
    _resolve_step_input,
)
from cxas_scrapi.utils.eval_utils import EvalUtils
from cxas_scrapi.utils.reporting import _load_sim_test_cases


def test_agent_turn_manager_raw_and_filtered_audio_tracking() -> None:
    mgr = AgentTurnManager(skip_playback_wait=False)
    mgr.prepare_for_turn(expected_turn_index=1, barge_in_after_ms=200)

    # 16000 samples of 16-bit PCM = 32000 bytes = 1.0 second
    dummy_audio = b"\x00\x00" * 16000
    mgr.add_audio(dummy_audio)

    assert mgr.raw_audio_bytes_received == 32000
    assert mgr.first_audio_received_time is not None

    meta = mgr.get_barge_in_metadata()
    assert meta.total_audio_duration_ms == 1000.0

    # is_full_playback_done should return True immediately when interrupted
    assert not mgr.is_full_playback_done()
    mgr.mark_interrupted(barge_in=True)
    assert mgr.is_full_playback_done() is True


def test_agent_turn_manager_window_missed() -> None:
    mgr = AgentTurnManager(skip_playback_wait=False)
    mgr.prepare_for_turn(expected_turn_index=1, barge_in_after_ms=5000)
    mgr.mark_turn_completed()

    assert mgr.is_agent_done_talking() is True
    assert mgr.barge_in_window_missed is True

    metadata = mgr.get_barge_in_metadata()
    assert metadata.barge_in_attempted is True
    assert metadata.barge_in_window_missed is True
    assert metadata.interrupted is False


def test_agent_turn_manager_interrupted_metadata() -> None:
    mgr = AgentTurnManager(skip_playback_wait=False)
    mgr.prepare_for_turn(expected_turn_index=1, barge_in_after_ms=500)
    mgr.first_audio_received_time = time.time() - 1.0
    dummy_audio = b"\x00\x00" * 32000
    mgr.add_audio(dummy_audio)

    mgr.mark_interrupted(barge_in=True)
    assert mgr.interrupted_flag is True
    assert mgr.barge_in_signal_received is True

    agent_text = (
        "The quick brown fox jumps over the lazy dog and runs across the field"
    )
    metadata = mgr.get_barge_in_metadata(agent_text=agent_text)
    assert metadata.interrupted is True
    assert metadata.played_audio_duration_ms > 0
    assert metadata.total_audio_duration_ms == 2000.0
    assert metadata.estimated_heard_text != ""
    assert len(metadata.estimated_heard_text.split()) < len(agent_text.split())


def test_parsed_session_response_barge_in_metadata() -> None:
    metadata = BargeInMetadata(
        barge_in_attempted=True,
        barge_in_after_ms=300,
        interrupted=True,
        barge_in_signal_received=True,
        barge_in_ignored=False,
        barge_in_window_missed=False,
        played_audio_duration_ms=450.0,
        total_audio_duration_ms=1200.0,
        estimated_heard_text="Hello world",
    )
    mock_resp = types.RunSessionResponse(
        outputs=[types.SessionOutput(text="Hello world how can I help you")]
    )
    turn_resp = ScrapiRunSessionResponse(
        original_response=mock_resp,
        agent_audio_paths={},
        barge_in_metadata=metadata,
    )

    parsed = ParsedSessionResponse(turn_resp)
    assert parsed.interrupted is True
    assert parsed.barge_in_metadata == metadata
    assert any(
        "Barge-In Signal: interrupted=True" in t for t in parsed.detailed_trace
    )


def test_parsed_session_response_vad_barge_in_does_not_set_interrupted() -> (
    None
):
    diag_dict = {
        "rootSpan": {
            "name": "Session",
            "childSpans": [
                {
                    "name": "VAD",
                    "attributes": {
                        "barge-in": "true",
                        "skip": "disable_barge_in_control",
                    },
                }
            ],
        }
    }
    raw_dict = {
        "sessionOutput": {
            "text": "Greeting",
            "diagnosticInfo": diag_dict,
        }
    }
    parsed = ParsedSessionResponse(raw_dict)
    assert parsed.vad_barge_in is True
    assert parsed.interrupted is False
    assert parsed.vad_skip_reason == "disable_barge_in_control"
    assert any("VAD Barge-In: True" in t for t in parsed.detailed_trace)
    assert any(
        "VAD Skip Reason: disable_barge_in_control" in t
        for t in parsed.detailed_trace
    )


def test_parsed_session_response_barge_in_context() -> None:
    user_context = (
        "<context>agent speaking was interrupted by user "
        'at 400ms: "I can help with flight..."</context>What are store hours?'
    )
    mock_chunk = MagicMock()
    mock_chunk._pb.WhichOneof.return_value = "text"
    mock_chunk.text = user_context

    mock_msg = MagicMock()
    mock_msg.role = "user"
    mock_msg.chunks = [mock_chunk]

    mock_output = MagicMock()
    mock_output.text = "We are open 9 to 5"
    mock_output.diagnostic_info.messages = [mock_msg]

    mock_resp = MagicMock()
    mock_resp.outputs = [mock_output]

    parsed = ParsedSessionResponse(mock_resp)
    assert parsed.barge_in_context == user_context
    assert any("Barge-In Context:" in t for t in parsed.detailed_trace)


def test_parsed_session_response_dict_support() -> None:
    dict_payload = {
        "outputs": [
            {
                "text": "Hello from dict output",
                "diagnosticInfo": {
                    "messages": [
                        {
                            "role": "agent",
                            "chunks": [{"text": "Hello from dict output"}],
                        }
                    ]
                },
            }
        ]
    }
    parsed = ParsedSessionResponse(dict_payload)
    assert parsed.consolidated_agent_text == "Hello from dict output"
    assert len(parsed.agent_texts) == 1


def test_bidi_session_handler_interruption_signal_extraction() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        inputs=[],
    )
    sig_msg = json.dumps({"interruptionSignal": {"bargeIn": True}})
    handler._on_message(None, sig_msg)
    assert handler.agent_turn_manager.interrupted_flag is True
    assert handler.agent_turn_manager.barge_in_signal_received is True


def test_bidi_session_handler_comfort_noise_rejection() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        input_queue=queue.Queue(),
    )
    handler._barge_in_pair_active = True
    handler.agent_turn_manager.prepare_for_turn(expected_turn_index=1)

    empty_noise_msg = json.dumps(
        {
            "sessionOutput": {
                "turnIndex": 1,
                "audio": "",
            }
        }
    )
    handler._on_message(None, empty_noise_msg)
    assert handler._turn_n_server_idx is None
    assert len(handler.outputs) == 0


def test_bidi_session_handler_max_server_turn_idx_tracking() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        inputs=[],
    )
    handler._barge_in_pair_active = True
    handler._turn_n_server_idx = 1
    handler.agent_turn_manager.prepare_for_turn(expected_turn_index=1)

    # Turn N+1 arrives with turnIndex 2
    turn_n1_msg = json.dumps(
        {
            "sessionOutput": {
                "turnIndex": 2,
                "text": "Interruption response",
                "turnCompleted": True,
            }
        }
    )
    handler._on_message(None, turn_n1_msg)
    assert handler.max_server_turn_idx == 2
    assert len(handler.next_turn_outputs) == 1

    # Turn N+2 arrives later with turnIndex 3
    handler._barge_in_pair_active = False
    handler.agent_turn_manager.prepare_for_turn(expected_turn_index=3)
    turn_n2_msg = json.dumps(
        {
            "sessionOutput": {
                "turnIndex": 3,
                "text": "Next regular turn",
                "turnCompleted": True,
            }
        }
    )
    handler._on_message(None, turn_n2_msg)
    assert handler.max_server_turn_idx == 3
    assert len(handler.current_turn_outputs) == 1


def test_bidi_session_handler_barge_in_phase_replay() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        inputs=[],
    )
    handler._barge_in_pair_active = True
    handler._turn_n_server_idx = 1
    handler._barge_in_phase = "turn_n"
    handler.agent_turn_manager.prepare_for_turn(expected_turn_index=1)

    # Deliver Turn N+1 during phase turn_n: should buffer without marking completed
    turn_n1_msg = json.dumps(
        {
            "sessionOutput": {
                "turnIndex": 2,
                "text": "Buffered response",
                "turnCompleted": True,
            }
        }
    )
    handler._on_message(None, turn_n1_msg)
    assert len(handler.next_turn_outputs) == 1
    assert handler.agent_turn_manager.turn_completed_flag is False

    # Transition to phase turn_n1 and replay
    with handler._barge_in_lock:
        handler._barge_in_phase = "turn_n1"
        handler.agent_turn_manager.prepare_for_turn(expected_turn_index=2)
        for out in handler.next_turn_outputs:
            if getattr(out, "turn_completed", False):
                handler.agent_turn_manager.mark_turn_completed()

    assert handler.agent_turn_manager.turn_completed_flag is True


def test_bidi_session_handler_session_ended_immediate_exit() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        inputs=[],
    )
    out = types.SessionOutput(end_session=types.EndSession())
    assert handler._output_ends_session(out) is False

    transfer_out = types.SessionOutput()
    assert handler._output_ends_session(transfer_out) is False


def test_bidi_interactive_session_send_turn_with_barge_in() -> None:
    mock_client = MagicMock()
    mock_client.location = "us"
    mock_client.token = "token"
    mock_client.rate_limiter = None
    mock_client.creds = None
    mock_client.project_id = "test-proj"

    session = BidiInteractiveSession(
        sessions_client=mock_client,
        session_id="s1",
        config={"session": "projects/p/locations/us/apps/a/sessions/s1"},
    )

    mock_resp_n = MagicMock()
    mock_resp_n1 = MagicMock()
    session.response_queue.put(mock_resp_n)
    session.response_queue.put(mock_resp_n1)

    res_n, res_n1 = session.send_turn_with_barge_in(
        text="Hello",
        barge_in_text="Stop",
        barge_in_after_ms=350,
        audio_bytes=b"dummy_user_audio_n",
        barge_in_audio_bytes=b"dummy_user_audio_n1",
    )
    assert res_n == mock_resp_n
    assert res_n1 == mock_resp_n1

    item = session.input_queue.get()
    assert "turn" in item
    assert "barge_in_next" in item
    assert item["barge_in_after_ms"] == 350


@patch("cxas_scrapi.core.sessions.AudioTransformer")
def test_bidi_interactive_session_connection_error_raises(
    mock_audio_transformer_cls: typing.Any,
) -> None:
    mock_transformer = mock_audio_transformer_cls.return_value
    mock_transformer.text_to_speech_bytes.return_value = {
        "audio_bytes": b"fake_audio",
        "text": "Hello",
    }
    mock_client = MagicMock()
    mock_client.location = "us"
    mock_client.token = "token"
    mock_client.rate_limiter = None
    mock_client.creds = None
    mock_client.project_id = "test-proj"

    session = BidiInteractiveSession(
        sessions_client=mock_client,
        session_id="s1",
        config={"session": "projects/p/locations/us/apps/a/sessions/s1"},
    )

    session.response_queue.put(
        {"connection_error": RuntimeError("Socket dropped")}
    )
    with pytest.raises(BidiSessionError):
        session.send_turn_with_barge_in(
            text="Hello",
            barge_in_text="Stop",
            barge_in_after_ms=300,
            audio_bytes=b"dummy_user_audio_n",
            barge_in_audio_bytes=b"dummy_user_audio_n1",
        )

    # If Turn N has session_ended, it should return (resp_n, None) immediately
    session.response_queue.put({"session_ended": True})
    resp_n, resp_n1 = session.send_turn_with_barge_in(
        text="Hello",
        barge_in_text="Stop",
        barge_in_after_ms=300,
        audio_bytes=b"dummy_user_audio_n",
        barge_in_audio_bytes=b"dummy_user_audio_n1",
    )
    assert resp_n == {"session_ended": True}
    assert resp_n1 is None


def test_turn_evals_operators_and_coercion() -> None:
    evals = TurnEvals(app_name="projects/p/locations/us/apps/a")

    metadata = BargeInMetadata(
        barge_in_attempted=True,
        barge_in_after_ms=400,
        interrupted=True,
        barge_in_signal_received=True,
        barge_in_ignored=False,
        barge_in_window_missed=False,
        played_audio_duration_ms=400.0,
        total_audio_duration_ms=1000.0,
        estimated_heard_text="Hi",
    )
    mock_output = types.SessionOutput(text="Hi there")
    resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(outputs=[mock_output]),
        agent_audio_paths={},
        barge_in_metadata=metadata,
    )

    # Boolean coercion tests for AGENT_INTERRUPTED
    for val in (True, "true", "True", "1", "yes"):
        case = TurnStep(
            turn="1",
            expectations=[
                TurnExpectation(type=TurnOperator.AGENT_INTERRUPTED, value=val)
            ],
        )
        res = evals.validate_turn_test(case, resp)
        assert res[0]["status"] == "SUCCESS"

    for val in (False, "false", "False", "0", "no"):
        case = TurnStep(
            turn="1",
            expectations=[
                TurnExpectation(type=TurnOperator.AGENT_INTERRUPTED, value=val)
            ],
        )
        res = evals.validate_turn_test(case, resp)
        assert res[0]["status"] == "FAILURE"

    # None defaults to True
    case_none = TurnStep(
        turn="1",
        expectations=[
            TurnExpectation(type=TurnOperator.AGENT_INTERRUPTED, value=None)
        ],
    )
    assert evals.validate_turn_test(case_none, resp)[0]["status"] == "SUCCESS"

    # Helper function check
    assert _coerce_expected_bool(None) is True
    assert _coerce_expected_bool("yes") is True
    assert _coerce_expected_bool("no") is False
    with pytest.raises(ValueError, match="Cannot coerce"):
        _coerce_expected_bool("unrecognized")

    # Helper function _resolve_step_input check
    step_event = TurnStep(turn="1", user="<event>custom_event</event>")
    _, event_name, text = _resolve_step_input(step_event)
    assert event_name == "custom_event"
    assert text == "event:custom_event"


@patch("cxas_scrapi.evals.turn_evals.track", side_effect=lambda x, **kwargs: x)
def test_turn_evals_multi_turn_barge_in_sequence(
    mock_track: typing.Any,
) -> None:
    evals = TurnEvals(app_name="projects/p/locations/us/apps/a")
    mock_interactive_session = MagicMock()

    resp_0 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[types.SessionOutput(text="Welcome to our store")]
        ),
        barge_in_metadata=BargeInMetadata(
            barge_in_attempted=True,
            barge_in_after_ms=500,
            interrupted=True,
            barge_in_signal_received=True,
            barge_in_ignored=False,
            barge_in_window_missed=False,
            played_audio_duration_ms=500.0,
            total_audio_duration_ms=1500.0,
            estimated_heard_text="Welcome",
        ),
    )
    resp_1 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[types.SessionOutput(text="We are open until 9 PM")]
        ),
    )

    mock_interactive_session.send_turn_with_barge_in.return_value = (
        resp_0,
        resp_1,
    )
    evals.sessions_client.create_interactive_session = MagicMock(
        return_value=mock_interactive_session
    )

    case = TurnTestCase(
        name="test_barge_in_flow",
        config={
            "use_tool_fakes": True,
            "capture_agent_audio": True,
        },
        turns=[
            TurnStep(
                turn="1",
                user="Hello",
                expectations=[
                    TurnExpectation(
                        type=TurnOperator.AGENT_INTERRUPTED, value=True
                    )
                ],
            ),
            TurnStep(
                turn="2",
                user="What time do you close?",
                barge_in_after_ms=500,
                expectations=[
                    TurnExpectation(type=TurnOperator.CONTAINS, value="9 PM")
                ],
            ),
        ],
    )

    df = evals.run_turn_tests([case])
    assert len(df) == 2
    assert all(df["status"] == "SUCCESS")
    mock_interactive_session.send_turn_with_barge_in.assert_called_once()
    mock_interactive_session.close.assert_called_once()


def test_turn_evals_invalid_barge_in_config() -> None:
    evals = TurnEvals(app_name="projects/p/locations/us/apps/a")

    # Turn 0 cannot have barge_in_after_ms
    case_invalid_0 = TurnTestCase(
        name="invalid_turn_0",
        turns=[
            TurnStep(turn="1", user="Hello", barge_in_after_ms=500),
            TurnStep(turn="2", user="World"),
        ],
    )
    with pytest.raises(ValueError, match="turn 0"):
        evals.run_turn_tests([case_invalid_0])

    # Consecutive barge-in turns are invalid
    case_consecutive = TurnTestCase(
        name="consecutive_barge_in",
        turns=[
            TurnStep(turn="1", user="Turn 1"),
            TurnStep(turn="2", user="Turn 2", barge_in_after_ms=300),
            TurnStep(turn="3", user="Turn 3", barge_in_after_ms=400),
        ],
    )
    with pytest.raises(ValueError, match="Consecutive barge-in turns"):
        evals.run_turn_tests([case_consecutive])


def test_simulation_evals_barge_in_chronological_transcript() -> None:
    test_case = {
        "steps": [
            {"goal": "Start", "static_utterance": "Hello"},
            {
                "goal": "Interrupt agent",
                "barge_in_after_ms": 750,
                "static_utterance": "Wait!",
            },
        ]
    }
    conv = LLMUserConversation(
        genai_client=None,
        genai_model="test-model",
        test_case=test_case,
        initial_utterance="event: welcome",
    )

    # Initial turn
    u0, _ = conv.next_user_utterance()
    assert u0 == "event: welcome"

    u1, _ = conv.next_user_utterance(last_agent_response="Welcome")
    assert u1 == "Hello"

    # Generate barge-in utterance without adding to transcript yet
    barge_utterance, _ = conv.generate_barge_in_utterance()
    assert barge_utterance == "Wait!"
    # Transcript should not contain "Wait!" yet
    assert "Wait!" not in conv.get_transcript()

    # Agent responds to Turn N
    conv._add_agent_response("I can help you with...")
    # Now record barge-in utterance
    conv.record_user_utterance(barge_utterance)

    transcript = conv.get_transcript()
    # Confirm chronological ordering: Turn N agent response precedes barge-in utterance
    pos_agent = transcript.find("I can help you with...")
    pos_barge = transcript.find("Wait!")
    assert pos_agent != -1
    assert pos_barge != -1
    assert pos_agent < pos_barge


def test_simulation_evals_step_progress_property_preservation() -> None:
    test_case = {
        "steps": [
            {
                "goal": "First step",
                "barge_in_after_ms": 400,
                "static_utterance": "First",
            }
        ]
    }
    conv = LLMUserConversation(
        genai_client=None,
        genai_model="test-model",
        test_case=test_case,
    )
    assert conv.peek_barge_in_after_ms() == 400


def test_simulation_evals_trace_format_and_naturalness_alignment() -> None:
    sim = SimulationEvals(app_name="projects/p/locations/us/apps/a")
    test_case = {
        "steps": [
            {"goal": "Greeting", "static_utterance": "Hello"},
            {
                "goal": "Interrupt",
                "barge_in_after_ms": 500,
                "static_utterance": "Stop now",
            },
        ]
    }
    mock_interactive_session = MagicMock()
    resp_0 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[types.SessionOutput(text="Welcome to support")]
        ),
        barge_in_metadata=BargeInMetadata(
            barge_in_attempted=True,
            barge_in_after_ms=500,
            interrupted=True,
            barge_in_signal_received=True,
            barge_in_ignored=False,
            barge_in_window_missed=False,
            played_audio_duration_ms=500.0,
            total_audio_duration_ms=1200.0,
            estimated_heard_text="Welcome",
        ),
    )
    resp_1 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[types.SessionOutput(text="Stopped.")]
        ),
    )
    mock_interactive_session.send_turn_with_barge_in.return_value = (
        resp_0,
        resp_1,
    )
    sim.sessions_client.create_interactive_session = MagicMock(
        return_value=mock_interactive_session
    )

    with patch(
        "cxas_scrapi.evals.simulation_evals.evaluate_expectations",
        return_value=[],
    ):
        result_conv = sim.simulate_conversation(
            test_case=test_case,
            modality="audio",
            console_logging=False,
        )

    # Trace line must format as 'User: [Barge-In] ...' so naturalness regex can extract turns
    assert any(
        "User: [Barge-In] Stop now" in t for t in result_conv.detailed_trace
    )


def test_simulation_evals_cleanup_session_dir(tmp_path: typing.Any) -> None:
    output_dir = str(tmp_path / "out")
    test_session_id = f"test_sess_{uuid.uuid4().hex[:8]}"

    class DummyRunner:
        @cleanup_session_dir
        def run(
            self,
            session_id: str,
            capture_agent_audio: bool = False,
            output_dir: str | None = None,
        ) -> str:
            src_dir = f"/tmp/scrapi_evals/{session_id}"
            os.makedirs(src_dir, exist_ok=True)
            with wave.open(f"{src_dir}/turn_0_agent.wav", "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 800)
            return session_id

    runner = DummyRunner()
    runner.run(
        session_id=test_session_id,
        capture_agent_audio=True,
        output_dir=output_dir,
    )

    # Check WAV was copied into output_dir/session_id
    expected_wav = os.path.join(output_dir, test_session_id, "turn_0_agent.wav")
    assert os.path.exists(expected_wav)
    # Check /tmp directory was cleaned up
    assert not os.path.exists(f"/tmp/scrapi_evals/{test_session_id}")


def test_simulation_evals_barge_in_warning_on_text_modality(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sim = SimulationEvals(app_name="projects/p/locations/us/apps/a")
    test_case = {
        "steps": [
            {
                "goal": "Interrupt on text",
                "barge_in_after_ms": 500,
                "static_utterance": "Stop",
            }
        ]
    }
    mock_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(text="ok", end_session=types.EndSession())
            ]
        )
    )
    with (
        caplog.at_level(logging.WARNING),
        patch.object(sim.sessions_client, "run", return_value=mock_resp),
        patch(
            "cxas_scrapi.evals.simulation_evals.evaluate_expectations",
            return_value=[],
        ),
    ):
        sim.simulate_conversation(
            test_case=test_case,
            modality="text",
            console_logging=False,
        )
    assert any(
        "Barge-in evaluation requires audio modality" in r.message
        for r in caplog.records
    )


def test_save_agent_audio_truncated_on_barge_in() -> None:
    handler = BidiSessionHandler(
        location="us",
        token="fake-token",
        config={
            "session": "projects/p/locations/us/apps/a/sessions/test-trunc-1"
        },
        capture_agent_audio=True,
    )
    # 2 seconds of audio at 16kHz 16-bit mono = 64000 bytes
    sample_rate = 16000
    full_audio = b"\x01\x00" * (sample_rate * 2)
    handler.turn_audio_buffers[0] = bytearray(full_audio)

    # Save with max_duration_ms=500.0 (should produce 8000 frames = 500ms)
    res = handler._save_agent_audio_for_index(0, max_duration_ms=500.0)
    assert 0 in res
    wav_path = res[0]

    with wave.open(wav_path, "rb") as wf:
        frames = wf.getnframes()
        assert frames == 8000
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000


def test_barge_in_example_yaml_validates_turn_and_simulation_schemas() -> None:
    example_path = "examples/evals/barge_in_eval_example.yaml"
    assert os.path.exists(example_path), (
        f"Example file {example_path} must exist"
    )

    turn_evals = TurnEvals(app_name="projects/p/locations/us/apps/a")
    turn_cases = turn_evals.load_turn_test_cases_from_file(example_path)
    assert len(turn_cases) >= 3

    case1 = turn_cases[0]
    assert case1.turns is not None
    assert len(case1.turns) == 2
    assert case1.turns[0].barge_in_after_ms is None
    assert case1.turns[1].barge_in_after_ms == 1200

    op_types = [
        exp.type if isinstance(exp, TurnExpectation) else None
        for exp in case1.turns[1].expectations
    ]
    assert TurnOperator.AGENT_INTERRUPTED in op_types
    assert TurnOperator.VAD_BARGE_IN in op_types
    assert TurnOperator.BARGE_IN_AWARENESS_INJECTED in op_types

    case3 = turn_cases[2]
    assert case3.turns is not None
    op_types_c3 = [
        exp.type if isinstance(exp, TurnExpectation) else None
        for exp in case3.turns[1].expectations
    ]
    assert TurnOperator.VAD_SKIP_REASON in op_types_c3

    sim_cases = _load_sim_test_cases(example_path)
    assert len(sim_cases) >= 2

    barge_in_steps_found = 0
    for sc in sim_cases:
        assert "steps" in sc
        for step_dict in sc["steps"]:
            step_obj = Step(**step_dict)
            if step_obj.barge_in_after_ms is not None:
                assert step_obj.barge_in_after_ms > 0
                barge_in_steps_found += 1
    assert barge_in_steps_found >= 2


def test_existing_example_yamls_validate_with_barge_in() -> None:
    turn_example_path = "examples/evals/turn_eval_example.yaml"
    sim_example_path = "examples/evals/simulation_eval_example.yaml"

    assert os.path.exists(turn_example_path)
    assert os.path.exists(sim_example_path)

    turn_evals = TurnEvals(app_name="projects/p/locations/us/apps/a")
    turn_cases = turn_evals.load_turn_test_cases_from_file(turn_example_path)
    barge_in_turn_cases = [
        c
        for c in turn_cases
        if c.turns and any(t.barge_in_after_ms is not None for t in c.turns)
    ]
    assert len(barge_in_turn_cases) >= 1
    assert barge_in_turn_cases[0].turns is not None
    barge_turn = next(
        t
        for t in barge_in_turn_cases[0].turns
        if t.barge_in_after_ms is not None
    )
    assert barge_turn.barge_in_after_ms == 1200

    sim_cases = _load_sim_test_cases(sim_example_path)
    barge_in_sim_cases = [
        c
        for c in sim_cases
        if any(
            Step(**s).barge_in_after_ms is not None for s in c.get("steps", [])
        )
    ]
    assert len(barge_in_sim_cases) >= 1
    matching_step = next(
        Step(**s)
        for s in barge_in_sim_cases[0]["steps"]
        if Step(**s).barge_in_after_ms is not None
    )
    assert matching_step.barge_in_after_ms == 1500


def test_step_barge_in_normalization_and_timing_bounds() -> None:
    # Sub-object range [min_ms, max_ms] and list of styles
    step_range = Step(
        goal="Interrupt with order change",
        barge_in={
            "after_ms": [700, 1900],
            "style": ["urgent_pivot", "impatient_cutoff"],
        },
    )
    assert step_range.is_barge_in_step() is True
    assert step_range.barge_in is not None
    assert step_range.barge_in.enabled is True
    assert step_range.barge_in.min_ms == 700
    assert step_range.barge_in.max_ms == 1900
    assert step_range.barge_in.after_ms == 1300
    assert step_range.barge_in_after_ms == 1300
    assert step_range.barge_in.style == "urgent_pivot,impatient_cutoff"
    assert step_range.resolve_timing_bounds() == (700, 1900)

    # Sub-object with "auto"
    step_auto = Step(
        goal="Interrupt automatically",
        barge_in={
            "after_ms": "auto",
            "min_ms": 600,
            "max_ms": 1800,
        },
    )
    assert step_auto.is_barge_in_step() is True
    assert step_auto.resolve_timing_bounds() == (600, 1800)
    assert step_auto.barge_in_after_ms == 1200

    # Sub-object fixed timestamp with jitter
    step_jitter = Step(
        goal="Interrupt with jitter",
        barge_in={
            "after_ms": 1000,
            "jitter_ms": 200,
        },
    )
    assert step_jitter.resolve_timing_bounds() == (800, 1200)


def test_generate_model_barge_in_utterance_and_timing_variance_across_runs() -> (
    None
):
    # 1. Offline variance across 5 runs (rotates across all 5 styles and timing windows)
    offline_utterances = set()
    offline_styles = []
    offline_timings = set()
    for run_idx in range(5):
        out = generate_model_barge_in(
            genai_client=None,
            genai_model="test-model",
            goal="Cancel order ORD-98765 immediately",
            response_guide='Say "Wait, I need to cancel order ORD-98765 right now!"',
            barge_in_style="auto",
            barge_in_min_ms=600,
            barge_in_max_ms=2000,
            run_idx=run_idx,
            turn_idx=1,
        )
        assert 600 <= out.barge_in_after_ms <= 2000
        assert "ORD-98765" in out.barge_in_utterance
        offline_utterances.add(out.barge_in_utterance)
        offline_styles.append(out.barge_in_style)
        offline_timings.add(out.barge_in_after_ms)

    assert len(set(offline_styles)) == 5
    assert len(offline_utterances) >= 4
    assert len(offline_timings) >= 3

    # 2. Model-driven generation with prompt verification & timing clamping
    mock_genai = MagicMock()
    captured_prompts: list[str] = []

    def _fake_generate(
        prompt: str,
        model_name: str,
        response_mime_type: str,
        response_schema: typing.Any,
    ) -> BargeInGenerationOutput:
        captured_prompts.append(prompt)
        idx = len(captured_prompts)
        return BargeInGenerationOutput(
            barge_in_utterance=f"Hold everything, cancel order ORD-98765 variant {idx}!",
            barge_in_after_ms=300
            if idx == 1
            else 2500,  # out of bounds to test clamping
            barge_in_style="urgent_pivot" if idx == 1 else "impatient_cutoff",
            should_barge_in=True,
            justification="Urgent order cancellation",
        )

    mock_genai.generate.side_effect = _fake_generate

    history: list[str] = []
    out1 = generate_model_barge_in(
        genai_client=mock_genai,
        genai_model="gemini-3.5-flash-lite",
        goal="Cancel order ORD-98765",
        response_guide="Interrupt to cancel order ORD-98765",
        barge_in_style=["urgent_pivot", "impatient_cutoff"],
        barge_in_min_ms=700,
        barge_in_max_ms=1800,
        previous_barge_in_utterances=history,
        run_idx=0,
        turn_idx=1,
    )
    history.append(out1.barge_in_utterance)
    out2 = generate_model_barge_in(
        genai_client=mock_genai,
        genai_model="gemini-3.5-flash-lite",
        goal="Cancel order ORD-98765",
        response_guide="Interrupt to cancel order ORD-98765",
        barge_in_style=["urgent_pivot", "impatient_cutoff"],
        barge_in_min_ms=700,
        barge_in_max_ms=1800,
        previous_barge_in_utterances=history,
        run_idx=1,
        turn_idx=1,
    )

    assert out1.barge_in_after_ms == 700  # clamped to min_ms
    assert out2.barge_in_after_ms == 1800  # clamped to max_ms
    assert out1.barge_in_utterance != out2.barge_in_utterance
    assert "urgent_pivot" in captured_prompts[0]
    assert "impatient_cutoff" in captured_prompts[1]
    assert out1.barge_in_utterance in captured_prompts[1]


def test_llm_user_conversation_multi_step_llm_goal_to_model_barge_in() -> None:
    sim = SimulationEvals(app_name="projects/p/locations/us/apps/a")
    test_case = {
        "name": "multi_step_goal_then_model_barge_in",
        "steps": [
            {
                "goal": "Inquire about general store return policies",
                "success_criteria": "Agent begins explaining the store return policies",
                "response_guide": "Ask the agent to explain return policies.",
                "max_turns": 6,
            },
            {
                "goal": "Barge in with an urgent order cancellation request",
                "success_criteria": "Agent ceases policy explanation and cancels order 98765",
                "response_guide": "Interrupt to cancel order 98765 right now.",
                "max_turns": 8,
                "barge_in": {
                    "style": "urgent_pivot",
                    "after_ms": [800, 1600],
                },
            },
        ],
    }

    def _mock_generate(
        prompt: str,
        model_name: str,
        response_mime_type: str,
        response_schema: typing.Any,
    ) -> typing.Any:
        if response_schema is BargeInGenerationOutput:
            return BargeInGenerationOutput(
                barge_in_utterance="Wait, hold on—I need to cancel order 98765 right away!",
                barge_in_after_ms=1150,
                barge_in_style="urgent_pivot",
                should_barge_in=True,
                justification="Caller urgently pivots to cancelling order 98765.",
            )
        # Standard LLMUserConversation.Output for Step 0 initiation
        return LLMUserConversation.Output(
            next_user_utterance="Can you explain your return and refund policies?",
            step_progresses=[
                StepProgress(
                    step=Step(
                        goal="Inquire about general store return policies",
                        success_criteria="Agent begins explaining the store return policies",
                        response_guide="Ask the agent to explain return policies.",
                        max_turns=6,
                    ),
                    status=StepStatus.IN_PROGRESS,
                    justification="User is initiating this goal by describing the problem.",
                ),
                StepProgress(
                    step=Step(
                        goal="Barge in with an urgent order cancellation request",
                        success_criteria="Agent ceases policy explanation and cancels order 98765",
                        response_guide="Interrupt to cancel order 98765 right now.",
                        max_turns=8,
                    ),
                    status=StepStatus.NOT_STARTED,
                    justification="",
                ),
            ],
        )

    sim.genai_client.generate = MagicMock(side_effect=_mock_generate)

    mock_interactive_session = MagicMock()
    welcome_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="Welcome to Cymbal Support! How can I help?"
                )
            ]
        )
    )
    interrupted_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="Our return policy allows returns within 30 days of purchase with a receipt."
                )
            ]
        ),
        barge_in_metadata=BargeInMetadata(
            barge_in_attempted=True,
            barge_in_after_ms=1150,
            interrupted=True,
            barge_in_signal_received=True,
            barge_in_ignored=False,
            barge_in_window_missed=False,
            played_audio_duration_ms=1150.0,
            total_audio_duration_ms=3500.0,
            estimated_heard_text="Our return policy allows",
            barge_in_style="urgent_pivot",
            barge_in_mode="pre_turn",
            model_generated=True,
        ),
    )
    post_barge_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="I have cancelled order 98765 for you right away."
                )
            ]
        )
    )

    mock_interactive_session.send_turn.return_value = welcome_resp
    mock_interactive_session.send_turn_with_barge_in.return_value = (
        interrupted_resp,
        post_barge_resp,
    )
    sim.sessions_client.create_interactive_session = MagicMock(
        return_value=mock_interactive_session
    )

    with patch(
        "cxas_scrapi.evals.simulation_evals.evaluate_expectations",
        return_value=[],
    ):
        conv = sim.simulate_conversation(
            test_case=test_case,
            modality="audio",
            console_logging=False,
        )

    # Turn 0 (welcome) must be sent via send_turn, NOT send_turn_with_barge_in
    mock_interactive_session.send_turn.assert_called_once_with(
        "event: welcome", {}
    )
    # Turn 1 (Step 0 utterance) must be paired with Step 1's model-generated barge-in
    mock_interactive_session.send_turn_with_barge_in.assert_called_once()
    call_kwargs = (
        mock_interactive_session.send_turn_with_barge_in.call_args.kwargs
    )
    assert (
        call_kwargs["text"]
        == "Can you explain your return and refund policies?"
    )
    assert (
        call_kwargs["barge_in_text"]
        == "Wait, hold on—I need to cancel order 98765 right away!"
    )
    assert call_kwargs["barge_in_after_ms"] == 1150
    assert call_kwargs["barge_in_style"] == "urgent_pivot"
    assert call_kwargs["model_generated"] is True

    # Both Step 0 and Step 1 must be marked COMPLETED
    assert all(p.status == StepStatus.COMPLETED for p in conv.steps_progress)
    assert any(
        "User: [Barge-In] Wait, hold on—I need to cancel order 98765 right away!"
        in t
        for t in conv.detailed_trace
    )


def test_simulation_evals_reactive_barge_in_defers_then_interrupts() -> None:
    sim = SimulationEvals(app_name="projects/p/locations/us/apps/a")
    test_case = {
        "name": "reactive_barge_in_defer_and_trigger",
        "steps": [
            {
                "goal": "Ask about roaming plans and answer verification if asked",
                "success_criteria": "Agent starts explaining roaming plans",
                "response_guide": "Ask about roaming plans. If asked for ZIP, say 94043.",
                "max_turns": 6,
            },
            {
                "goal": "Reactively barge in to report locked SIM on account 445566",
                "success_criteria": "Agent switches to unlocking SIM on account 445566",
                "response_guide": "Interrupt roaming explanation to report locked SIM on account 445566.",
                "max_turns": 8,
                "barge_in": {
                    "mode": "reactive",
                    "style": "urgent_pivot",
                    "after_ms": [700, 1700],
                },
            },
        ],
    }

    llm_turn_calls = 0
    barge_in_calls: list[str] = []

    def _mock_generate(
        prompt: str,
        model_name: str,
        response_mime_type: str,
        response_schema: typing.Any,
    ) -> typing.Any:
        nonlocal llm_turn_calls
        if response_schema is BargeInGenerationOutput:
            barge_in_calls.append(prompt)
            if "What is your billing ZIP code?" in prompt:
                # Agent asked a short prerequisite question -> defer barge-in!
                return BargeInGenerationOutput(
                    barge_in_utterance="",
                    barge_in_after_ms=900,
                    barge_in_style="urgent_pivot",
                    should_barge_in=False,
                    justification="Agent asked prerequisite ZIP question; deferring barge-in.",
                )
            return BargeInGenerationOutput(
                barge_in_utterance="Hold on, forget roaming—my SIM on account 445566 is PUK-locked!",
                barge_in_after_ms=950,
                barge_in_style="urgent_pivot",
                should_barge_in=True,
                justification="Agent is now explaining roaming plans; interrupting reactively.",
            )

        llm_turn_calls += 1
        utterance = (
            "What international roaming plans do you have?"
            if llm_turn_calls == 1
            else "My ZIP code is 94043."
        )
        return LLMUserConversation.Output(
            next_user_utterance=utterance,
            step_progresses=[
                StepProgress(
                    step=Step(**test_case["steps"][0]),
                    status=StepStatus.IN_PROGRESS,
                    justification="Working on step 0.",
                ),
                StepProgress(
                    step=Step(**test_case["steps"][1]),
                    status=StepStatus.NOT_STARTED,
                    justification="",
                ),
            ],
        )

    sim.genai_client.generate = MagicMock(side_effect=_mock_generate)

    mock_interactive_session = MagicMock()
    welcome_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[types.SessionOutput(text="Hello, how can I help you?")]
        )
    )
    zip_question_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="Sure! Before I check roaming plans, what is your billing ZIP code?"
                )
            ]
        )
    )
    roaming_interrupted_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="Thank you. We offer three international roaming passes starting at ten dollars..."
                )
            ]
        ),
        barge_in_metadata=BargeInMetadata(
            barge_in_attempted=True,
            barge_in_after_ms=950,
            interrupted=True,
            barge_in_signal_received=True,
            barge_in_ignored=False,
            barge_in_window_missed=False,
            played_audio_duration_ms=950.0,
            total_audio_duration_ms=3200.0,
            estimated_heard_text="Thank you. We offer three",
            barge_in_style="urgent_pivot",
            barge_in_mode="reactive",
            model_generated=True,
        ),
    )
    sim_unlocked_resp = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="I can help unlock your SIM card on account 445566 right now."
                )
            ]
        )
    )

    mock_interactive_session.send_turn.return_value = welcome_resp

    barge_send_count = 0

    def _fake_send_turn_with_barge_in(
        **kwargs: typing.Any,
    ) -> tuple[typing.Any, typing.Any]:
        nonlocal barge_send_count
        barge_send_count += 1
        cb = kwargs.get("barge_in_callback")
        assert cb is not None
        if barge_send_count == 1:
            res = cb("What is your billing ZIP code?")
            assert res["should_barge_in"] is False
            return zip_question_resp, None
        res = cb(
            "Thank you. We offer three international roaming passes starting at ten dollars..."
        )
        assert res["should_barge_in"] is True
        assert "445566" in res["barge_in_text"]
        assert res["barge_in_after_ms"] == 950
        return roaming_interrupted_resp, sim_unlocked_resp

    mock_interactive_session.send_turn_with_barge_in.side_effect = (
        _fake_send_turn_with_barge_in
    )
    sim.sessions_client.create_interactive_session = MagicMock(
        return_value=mock_interactive_session
    )

    with patch(
        "cxas_scrapi.evals.simulation_evals.evaluate_expectations",
        return_value=[],
    ):
        conv = sim.simulate_conversation(
            test_case=test_case,
            modality="audio",
            console_logging=False,
        )

    assert barge_send_count == 2
    assert len(barge_in_calls) == 2
    assert all(p.status == StepStatus.COMPLETED for p in conv.steps_progress)
    assert any("User: My ZIP code is 94043." in t for t in conv.detailed_trace)
    assert any(
        "User: [Barge-In] Hold on, forget roaming—my SIM on account 445566 is PUK-locked!"
        in t
        for t in conv.detailed_trace
    )


def test_bidi_session_handler_reactive_barge_in_callback_and_defer() -> None:
    # 1. Test reactive callback deferring barge-in (should_barge_in=False)
    resp_q_defer: queue.Queue = queue.Queue()
    handler_defer = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s1"},
        input_queue=queue.Queue(),
        response_queue=resp_q_defer,
        skip_playback_wait=True,
    )
    handler_defer.current_turn_outputs = [
        types.SessionOutput(text="Can I get your account PIN first?")
    ]
    handler_defer.agent_turn_manager.mark_turn_completed()

    seen_live_texts: list[str] = []

    def _defer_cb(live_text: str) -> dict[str, typing.Any]:
        seen_live_texts.append(live_text)
        return {"should_barge_in": False, "model_generated": True}

    with patch.object(handler_defer, "_stream_input_payload"):
        handler_defer._send_barge_in_pair(
            {
                "turn": [{"text": "Hi"}],
                "barge_in_next": None,
                "barge_in_after_ms": 500,
                "barge_in_callback": _defer_cb,
                "barge_in_mode": "reactive",
                "model_generated": True,
            },
            idx=0,
        )

    assert seen_live_texts == ["Can I get your account PIN first?"]
    r_n = resp_q_defer.get_nowait()
    r_n1 = resp_q_defer.get_nowait()
    assert isinstance(r_n, ScrapiRunSessionResponse)
    assert r_n.barge_in_metadata is not None
    assert r_n.barge_in_metadata.barge_in_attempted is False
    assert r_n.barge_in_metadata.barge_in_mode == "reactive"
    assert r_n.barge_in_metadata.model_generated is True
    assert r_n1 is None
    assert handler_defer.current_agent_turn_idx == 1

    # 2. Test reactive callback triggering barge-in (should_barge_in=True)
    resp_q_trig: queue.Queue = queue.Queue()
    handler_trig = BidiSessionHandler(
        location="us",
        token="token",
        config={"session": "sessions/s2"},
        input_queue=queue.Queue(),
        response_queue=resp_q_trig,
        skip_playback_wait=True,
    )
    handler_trig.current_turn_outputs = [
        types.SessionOutput(
            text="Here is our complete 12-part policy breakdown..."
        )
    ]
    handler_trig.agent_turn_manager.mark_interrupted(barge_in=True)
    handler_trig.agent_turn_manager.mark_turn_completed()
    handler_trig.next_turn_outputs = [
        types.SessionOutput(
            text="Got it, skipping to cancellation.", turn_completed=True
        )
    ]
    handler_trig.next_turn_completed_flag = True

    streamed_payloads: list[typing.Any] = []

    def _trig_cb(live_text: str) -> dict[str, typing.Any]:
        return {
            "should_barge_in": True,
            "barge_in_next": [{"text": "Skip the breakdown, cancel now!"}],
            "barge_in_after_ms": 650,
            "barge_in_style": "impatient_cutoff",
            "model_generated": True,
        }

    with patch.object(
        handler_trig,
        "_stream_input_payload",
        side_effect=lambda payload, *a, **kw: streamed_payloads.append(payload),
    ):
        handler_trig._send_barge_in_pair(
            {
                "turn": [{"text": "Tell me about the policy"}],
                "barge_in_next": None,
                "barge_in_after_ms": 800,
                "barge_in_callback": _trig_cb,
                "barge_in_mode": "reactive",
                "model_generated": True,
            },
            idx=0,
        )

    assert len(streamed_payloads) == 2
    assert streamed_payloads[1] == [{"text": "Skip the breakdown, cancel now!"}]
    resp_0 = resp_q_trig.get_nowait()
    resp_1 = resp_q_trig.get_nowait()
    assert resp_0.barge_in_metadata is not None
    assert resp_0.barge_in_metadata.barge_in_after_ms == 650
    assert resp_0.barge_in_metadata.barge_in_style == "impatient_cutoff"
    assert resp_0.barge_in_metadata.barge_in_mode == "reactive"
    assert resp_0.barge_in_metadata.model_generated is True
    assert resp_1 is not None


@patch("cxas_scrapi.evals.turn_evals.track", side_effect=lambda x, **kwargs: x)
def test_turn_evals_model_generated_barge_in(mock_track: typing.Any) -> None:
    evals = TurnEvals(app_name="projects/p/locations/us/apps/a")
    evals.genai_client.generate = MagicMock(
        return_value=BargeInGenerationOutput(
            barge_in_utterance="Actually, cut the shipping overview—update address on ORD-5512!",
            barge_in_after_ms=920,
            barge_in_style="impatient_cutoff",
            should_barge_in=True,
            justification="Impatient cutoff preserving ORD-5512.",
        )
    )

    mock_interactive_session = MagicMock()
    resp_0 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="We offer standard, expedited, and overnight shipping..."
                )
            ]
        ),
        barge_in_metadata=BargeInMetadata(
            barge_in_attempted=True,
            barge_in_after_ms=920,
            interrupted=True,
            barge_in_signal_received=True,
            barge_in_ignored=False,
            barge_in_window_missed=False,
            played_audio_duration_ms=920.0,
            total_audio_duration_ms=2500.0,
            estimated_heard_text="We offer standard",
            barge_in_style="impatient_cutoff",
            barge_in_mode="pre_turn",
            model_generated=True,
        ),
    )
    resp_1 = ScrapiRunSessionResponse(
        original_response=types.RunSessionResponse(
            outputs=[
                types.SessionOutput(
                    text="Sure, I can update the delivery address for ORD-5512."
                )
            ]
        )
    )
    mock_interactive_session.send_turn_with_barge_in.return_value = (
        resp_0,
        resp_1,
    )
    evals.sessions_client.create_interactive_session = MagicMock(
        return_value=mock_interactive_session
    )

    case = TurnTestCase(
        name="model_gen_turn_barge_in",
        turns=[
            TurnStep(
                turn="1",
                user="Can you list all shipping tiers?",
                expectations=[
                    TurnExpectation(
                        type=TurnOperator.AGENT_INTERRUPTED, value=True
                    )
                ],
            ),
            TurnStep(
                turn="2",
                barge_in={
                    "goal": "Interrupt to update shipping address on ORD-5512",
                    "guide": "Cut in and ask to change address for ORD-5512",
                    "style": ["urgent_pivot", "impatient_cutoff"],
                    "after_ms": [700, 1800],
                },
                expectations=[
                    TurnExpectation(
                        type=TurnOperator.CONTAINS, value="ORD-5512"
                    )
                ],
            ),
        ],
    )

    df = evals.run_turn_tests([case])
    assert len(df) == 2
    assert all(df["status"] == "SUCCESS")
    call_kwargs = (
        mock_interactive_session.send_turn_with_barge_in.call_args.kwargs
    )
    assert (
        call_kwargs["barge_in_text"]
        == "Actually, cut the shipping overview—update address on ORD-5512!"
    )
    assert call_kwargs["barge_in_after_ms"] == 920
    assert call_kwargs["barge_in_style"] == "impatient_cutoff"
    assert call_kwargs["model_generated"] is True


def test_barge_in_requires_local_execution_and_milestone_sanitization() -> None:
    req_reactive, reason_reactive = SimulationEvals.requires_local_execution(
        {
            "name": "cymbal_reactive_barge_in",
            "steps": [
                {"goal": "Ask about plans"},
                {
                    "goal": "Interrupt reactively",
                    "barge_in": {"mode": "reactive", "after_ms": "auto"},
                },
            ],
        }
    )
    assert req_reactive is True
    assert "barge_in" in reason_reactive

    req_disabled, _ = SimulationEvals.requires_local_execution(
        {
            "name": "cymbal_disabled_barge_in",
            "steps": [
                {
                    "goal": "Ask about plans without barge-in",
                    "barge_in": {"enabled": False},
                }
            ],
        }
    )
    assert req_disabled is False

    milestone_yaml = EvalUtils.convert_sim_evals_to_milestone_yaml(
        {
            "evals": [
                {
                    "name": "cymbal_sanitized_barge_in_sim",
                    "steps": [
                        {
                            "goal": "Ask about roaming",
                            "success_criteria": "Agent explains roaming",
                        },
                        {
                            "goal": "Interrupt to unlock SIM",
                            "success_criteria": "Agent unlocks SIM",
                            "barge_in": {
                                "mode": "reactive",
                                "style": "urgent_pivot",
                                "after_ms": [600, 1800],
                            },
                        },
                    ],
                }
            ]
        }
    )
    assert "barge_in:" not in milestone_yaml
    assert "cymbal_sanitized_barge_in_sim" in milestone_yaml
