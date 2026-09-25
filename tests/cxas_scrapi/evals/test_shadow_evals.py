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

"""Unit tests for ShadowEvals and ShadowUserConversation."""

import io
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.evals.shadow_evals import (
    ShadowDecisionType,
    ShadowEvals,
    ShadowPastTurn,
    ShadowReport,
    ShadowTestCase,
    ShadowUserConversation,
    extract_pcm_bytes_from_wav,
)
from cxas_scrapi.evals.simulation_evals import Step, StepProgress, StepStatus
from cxas_scrapi.utils.eval_utils import ExpectationResult, ExpectationStatus
from cxas_scrapi.utils.lint_rules.evals import ShadowEvalStructure
from cxas_scrapi.utils.linter import LintContext


def _make_wav_bytes(pcm_payload: bytes = b"\x01\x02" * 800) -> bytes:
    """Helper to create a valid 16kHz, 1-channel, 16-bit WAV byte buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_payload)
    return buf.getvalue()


def test_shadow_test_case_validation() -> None:
    """ShadowTestCase requires conversation_id and non-empty expectations."""
    with pytest.raises(ValueError, match="conversation_id"):
        ShadowTestCase(
            conversation_id="   ",
            expectations=["Agent resolves the issue."],
        )

    with pytest.raises(ValueError, match="expectations"):
        ShadowTestCase(
            conversation_id="conv-123",
            expectations=[],
        )

    with pytest.raises(ValueError, match="expectations"):
        ShadowTestCase(
            conversation_id="conv-123",
            expectations=["   "],
        )

    tc = ShadowTestCase(
        conversation_id="conv-123",
        project_id="src-proj",
        location="us",
        app_id="src-app",
        expectations=["The agent checks order status and confirms delivery."],
    )
    assert tc.name == "shadow_conv-123"
    assert tc.conversation_id == "conv-123"
    assert tc.project_id == "src-proj"
    assert tc.app_id == "src-app"


def test_extract_pcm_bytes_from_wav() -> None:
    """Extracts raw PCM frames from a RIFF WAV buffer."""
    raw_pcm = b"\x10\x20" * 400
    wav_bytes = _make_wav_bytes(raw_pcm)
    extracted = extract_pcm_bytes_from_wav(wav_bytes)
    assert extracted == raw_pcm
    assert extract_pcm_bytes_from_wav(raw_pcm) == raw_pcm
    assert extract_pcm_bytes_from_wav(b"") == b""


def test_shadow_user_conversation_past_audio_and_tts_deviation() -> None:
    """ShadowUserConversation arbitrates between replaying past audio turns
    and generating new TTS responses when the new agent deviates.
    """
    mock_gemini = MagicMock()
    pcm_turn_1 = b"\xaa\xbb" * 160
    pcm_turn_2 = b"\xcc\xdd" * 160

    past_turns = [
        ShadowPastTurn(
            turn_index=1,
            user_transcript="I need a refund for order 12345.",
            preceding_agent_response="Hello, how can I help you?",
            audio_uri="gs://bucket/conv-1/user-turn-1.wav",
            has_audio=True,
            audio_bytes=pcm_turn_1,
        ),
        ShadowPastTurn(
            turn_index=2,
            user_transcript="The mug arrived broken.",
            preceding_agent_response="What is the reason for the refund?",
            audio_uri="gs://bucket/conv-1/user-turn-2.wav",
            has_audio=True,
            audio_bytes=pcm_turn_2,
        ),
    ]

    tc = ShadowTestCase(
        name="refund_shadow_test",
        conversation_id="conv-1",
        goal="Request refund for broken mug in order 12345",
        response_guide=(
            "If the agent asks for zip code verification, provide 94043."
        ),
        expectations=[
            "The agent verifies the caller's zip code and processes refund."
        ],
    )

    shadow_conv = ShadowUserConversation(
        genai_client=mock_gemini,
        genai_model="gemini-3.1-flash-lite",
        test_case=tc,
        past_turns=past_turns,
    )

    step = shadow_conv.steps_progress[0].step
    mock_gemini.generate.side_effect = [
        # Turn 1: Agent greeted -> use past turn 1 audio
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.USE_PAST_AUDIO,
            selected_past_turn_index=1,
            next_user_utterance="I need a refund for order 12345.",
            decision_justification="Matches opening request in past turn 1.",
            step_progresses=[
                StepProgress(
                    step=step,
                    status=StepStatus.IN_PROGRESS,
                    justification="Initiated refund request with past audio 1.",
                )
            ],
        ),
        # Turn 2: New agent deviates by asking for zip code -> generate TTS
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            selected_past_turn_index=None,
            next_user_utterance="My zip code is 94043.",
            decision_justification=(
                "New agent asked for zip code verification not in past call."
            ),
            step_progresses=[
                StepProgress(
                    step=step,
                    status=StepStatus.IN_PROGRESS,
                    justification="Provided zip code via TTS.",
                )
            ],
        ),
        # Turn 3: Agent asks reason for refund -> resume with past turn 2 audio
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.USE_PAST_AUDIO,
            selected_past_turn_index=2,
            next_user_utterance="The mug arrived broken.",
            decision_justification=(
                "Agent asked for refund reason, matching past turn 2 audio."
            ),
            step_progresses=[
                StepProgress(
                    step=step,
                    status=StepStatus.IN_PROGRESS,
                    justification="Replayed past turn 2 audio.",
                )
            ],
        ),
        # Turn 4: Agent confirms refund -> end conversation
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.END_CONVERSATION,
            selected_past_turn_index=None,
            next_user_utterance="",
            decision_justification="Refund confirmed; goal completed.",
            step_progresses=[
                StepProgress(
                    step=step,
                    status=StepStatus.COMPLETED,
                    justification="Refund processed.",
                )
            ],
        ),
    ]

    # Turn 0: initial welcome event
    u0, audio0, _, log0 = shadow_conv.next_user_turn()
    assert u0 == "event: welcome"
    assert audio0 is None
    assert log0 is not None
    assert log0.decision == "event"

    # Turn 1: replay past audio #1
    u1, audio1, _, log1 = shadow_conv.next_user_turn(
        "Hello, how can I help you?"
    )
    assert u1 == "I need a refund for order 12345."
    assert audio1 == pcm_turn_1
    assert log1 is not None
    assert log1.decision == "use_past_audio"
    assert log1.selected_past_turn_index == 1

    # Turn 2: deviate via TTS
    u2, audio2, _, log2 = shadow_conv.next_user_turn(
        "Before I look up order 12345, what is your billing zip code?"
    )
    assert u2 == "My zip code is 94043."
    assert audio2 is None
    assert log2 is not None
    assert log2.decision == "generate_tts"

    # Turn 3: resume past audio #2
    u3, audio3, _, log3 = shadow_conv.next_user_turn(
        "Thanks! What is the reason for the refund on order 12345?"
    )
    assert u3 == "The mug arrived broken."
    assert audio3 == pcm_turn_2
    assert log3 is not None
    assert log3.decision == "use_past_audio"
    assert log3.selected_past_turn_index == 2

    # Turn 4: end conversation
    u4, audio4, _, log4 = shadow_conv.next_user_turn(
        "I have processed your full refund for the broken mug."
    )
    assert u4 == ""
    assert audio4 is None
    assert log4 is None
    assert shadow_conv.steps_progress[0].status == StepStatus.COMPLETED

    report = shadow_conv.generate_report()
    assert isinstance(report, ShadowReport)
    assert len(report.turns_df) == 4
    assert "use_past_audio" in str(report)
    assert "generate_tts" in str(report)


@patch("cxas_scrapi.evals.shadow_evals.GCSUtils")
@patch("cxas_scrapi.evals.shadow_evals.Traces")
@patch("cxas_scrapi.evals.shadow_evals.Tools")
@patch("cxas_scrapi.evals.shadow_evals.Sessions")
@patch("cxas_scrapi.evals.shadow_evals.GeminiGenerate")
def test_shadow_evals_fetch_and_run_bidi(
    mock_gemini_cls: Any,
    mock_sessions_cls: Any,
    mock_tools_cls: Any,
    mock_traces_cls: Any,
    mock_gcs_cls: Any,
) -> None:
    """ShadowEvals downloads past turns/audio from GCS, streams past audio vs
    TTS on Bidi session, and evaluates natural-language expectations.
    """
    mock_tools_cls.return_value.get_tools_map.return_value = {}
    mock_traces = mock_traces_cls.return_value
    mock_traces.get_normalized.return_value = {
        "conversation_id": "past-conv-99",
        "start_time": "2026-09-20T10:00:00Z",
        "raw": {
            "turns": [
                {
                    "messages": [
                        {
                            "role": "root_agent",
                            "chunks": [{"text": "Welcome to Support!"}],
                        },
                        {
                            "role": "user",
                            "chunks": [
                                {"transcript": "Check my balance please."}
                            ],
                        },
                        {
                            "role": "root_agent",
                            "chunks": [{"text": "Your balance is $120."}],
                        },
                    ]
                }
            ]
        },
    }
    mock_traces.get_user_audio_uris.return_value = {
        1: "gs://my-audio-bucket/past-conv-99/user-turn-1.wav"
    }

    raw_pcm_1 = b"\x05\x06" * 300
    mock_gcs = mock_gcs_cls.return_value
    mock_gcs.download_blob.return_value = _make_wav_bytes(raw_pcm_1)

    # Setup mock agent responses on Sessions.run
    mock_sessions = mock_sessions_cls.return_value
    resp_welcome = MagicMock()
    out_welcome = MagicMock()
    out_welcome.text = "Welcome! How can I help?"
    out_welcome.diagnostic_info = None
    resp_welcome.outputs = [out_welcome]

    resp_turn1 = MagicMock()
    out_turn1 = MagicMock()
    out_turn1.text = "Please confirm your PIN first."
    out_turn1.diagnostic_info = None
    resp_turn1.outputs = [out_turn1]

    resp_turn2 = MagicMock()
    out_turn2 = MagicMock()
    out_turn2.text = "Thank you! Your account balance is $120."
    out_turn2.diagnostic_info = None
    resp_turn2.outputs = [out_turn2]

    mock_sessions.run.side_effect = [resp_welcome, resp_turn1, resp_turn2]

    # Setup GeminiGenerate for both simUser decisions and evaluate_expectations
    mock_gemini = mock_gemini_cls.return_value
    step_obj = Step(
        goal="Check account balance",
        success_criteria="Agent states the balance is $120",
    )

    def fake_generate(
        prompt: str,
        model_name: str,
        response_mime_type: str | None = None,
        response_schema: Any = None,
        **kwargs: Any,
    ) -> Any:
        if response_schema == ShadowUserConversation.Output:
            # First call: use past audio turn 1
            if "Please confirm your PIN first" not in prompt:
                return ShadowUserConversation.Output(
                    decision=ShadowDecisionType.USE_PAST_AUDIO,
                    selected_past_turn_index=1,
                    next_user_utterance="Check my balance please.",
                    decision_justification="Matches past turn 1 audio.",
                    step_progresses=[
                        StepProgress(
                            step=step_obj,
                            status=StepStatus.IN_PROGRESS,
                            justification="Replayed turn 1 audio.",
                        )
                    ],
                )
            # Second call: agent asked for PIN -> deviate with TTS
            if "Your account balance is $120" not in prompt:
                return ShadowUserConversation.Output(
                    decision=ShadowDecisionType.GENERATE_TTS,
                    selected_past_turn_index=None,
                    next_user_utterance="My PIN is 4321.",
                    decision_justification="Agent requested PIN; using TTS.",
                    step_progresses=[
                        StepProgress(
                            step=step_obj,
                            status=StepStatus.IN_PROGRESS,
                            justification="Sent PIN via TTS.",
                        )
                    ],
                )
            # Third call: balance provided -> end conversation
            return ShadowUserConversation.Output(
                decision=ShadowDecisionType.END_CONVERSATION,
                selected_past_turn_index=None,
                next_user_utterance="",
                decision_justification="Balance was provided.",
                step_progresses=[
                    StepProgress(
                        step=step_obj,
                        status=StepStatus.COMPLETED,
                        justification="Done.",
                    )
                ],
            )
        return None

    mock_gemini.generate.side_effect = fake_generate

    with patch(
        "cxas_scrapi.evals.shadow_evals.evaluate_expectations"
    ) as mock_eval_exp:
        mock_eval_exp.return_value = [
            ExpectationResult(
                expectation="The agent verifies the PIN and provides the $120 balance.",
                status=ExpectationStatus.MET,
                justification="Agent asked for PIN and stated $120 balance.",
            )
        ]

        shadow_evals = ShadowEvals(
            app_name="projects/new-proj/locations/us/apps/new-app",
            creds=MagicMock(),
        )

        yaml_str = """
config:
  project_id: "old-proj"
  location: "us"
  app_id: "old-app"
  gcs_bucket: "gs://my-audio-bucket"

shadow_evals:
  - name: "balance_check_shadow"
    conversation_id: "past-conv-99"
    goal: "Check account balance"
    response_guide: "If asked for a PIN, say 4321."
    expectations:
      - "The agent verifies the PIN and provides the $120 balance."
"""
        cases = shadow_evals.load_shadow_test_cases_from_yaml(yaml_str)
        assert len(cases) == 1
        assert cases[0].project_id == "old-proj"
        assert cases[0].app_id == "old-app"
        assert cases[0].gcs_bucket == "gs://my-audio-bucket"

        results = shadow_evals.run_shadow_evals(
            cases, runs=1, modality="audio", verbose=False
        )
        assert len(results) == 1
        res = results[0]
        assert res["passed"] is True
        assert res["conversation_id"] == "past-conv-99"
        assert res["past_audio_turns_used"] == 1
        assert res["tts_turns_generated"] == 1
        assert res["expectations"] == "1/1"

        # Verify Sessions.run calls:
        # Call 0: event="welcome"
        # Call 1: audio=raw_pcm_1 (streamed from GCS recording)
        # Call 2: text="My PIN is 4321." (synthesized via TTS on Bidi)
        assert mock_sessions.run.call_count == 3
        assert (
            mock_sessions.run.call_args_list[0].kwargs.get("event") == "welcome"
        )
        assert (
            mock_sessions.run.call_args_list[1].kwargs.get("audio") == raw_pcm_1
        )
        assert (
            mock_sessions.run.call_args_list[2].kwargs.get("text")
            == "My PIN is 4321."
        )


def test_shadow_eval_lint_rule_e012(tmp_path: Path) -> None:
    """Rule E012 flags shadow eval entries missing conversation_id or
    natural-language expectations.
    """
    shadow_dir = tmp_path / "evals" / "shadows"
    shadow_dir.mkdir(parents=True)
    bad_file = shadow_dir / "bad_shadow.yaml"
    bad_content = """
evals:
  - name: "missing_conv_and_exp"
    goal: "Test goal"
"""
    bad_file.write_text(bad_content, encoding="utf-8")

    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
    )
    rule = ShadowEvalStructure()
    findings = rule.check(bad_file, bad_content, ctx)
    assert len(findings) == 2
    assert any("conversation_id" in f.message for f in findings)
    assert any("expectations" in f.message for f in findings)

    good_content = """
evals:
  - name: "valid_shadow"
    conversation_id: "conv-abc-123"
    expectations:
      - "The agent greets the user and completes the booking."
"""
    good_findings = rule.check(bad_file, good_content, ctx)
    assert len(good_findings) == 0


@patch("cxas_scrapi.evals.shadow_evals.GCSUtils")
@patch("cxas_scrapi.evals.shadow_evals.Traces")
@patch("cxas_scrapi.evals.shadow_evals.Tools")
@patch("cxas_scrapi.evals.shadow_evals.Sessions")
@patch("cxas_scrapi.evals.shadow_evals.GeminiGenerate")
def test_shadow_evals_single_bidi_stream_interactive(
    mock_gemini_cls: Any,
    mock_sessions_cls: Any,
    mock_tools_cls: Any,
    mock_traces_cls: Any,
    mock_gcs_cls: Any,
) -> None:
    """In single_bidi_stream=True mode, BidiInteractiveSession.send_turn
    receives raw audio_bytes for past audio turns and audio_bytes=None for TTS.
    """
    mock_tools_cls.return_value.get_tools_map.return_value = {}
    mock_traces = mock_traces_cls.return_value
    mock_traces.get_normalized.return_value = {
        "conversation_id": "conv-stream-1",
        "start_time": "2026-09-20T12:00:00Z",
        "entries": [
            {"kind": "agent", "turn": 0, "text": "Hi there!"},
            {"kind": "user", "turn": 0, "text": "Book a hotel room."},
        ],
    }
    mock_traces.get_user_audio_uris.return_value = {
        1: "gs://bucket/conv-stream-1/user-turn-1.wav"
    }

    pcm_1 = b"\x11\x22" * 200
    mock_gcs_cls.return_value.download_blob.return_value = _make_wav_bytes(
        pcm_1
    )

    mock_interactive = MagicMock()
    mock_sessions_cls.return_value.create_interactive_session.return_value = (
        mock_interactive
    )

    resp_0 = MagicMock()
    out_0 = MagicMock()
    out_0.text = "Hi there!"
    out_0.diagnostic_info = None
    resp_0.outputs = [out_0]

    resp_1 = MagicMock()
    out_1 = MagicMock()
    out_1.text = "Which city?"
    out_1.diagnostic_info = None
    resp_1.outputs = [out_1]

    resp_2 = MagicMock()
    out_2 = MagicMock()
    out_2.text = "Booked your hotel in Seattle!"
    out_2.diagnostic_info = None
    resp_2.outputs = [out_2]

    mock_interactive.send_turn.side_effect = [resp_0, resp_1, resp_2]

    step_obj = Step(
        goal="Book a hotel in Seattle",
        success_criteria="Hotel booked in Seattle",
    )
    mock_gemini_cls.return_value.generate.side_effect = [
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.USE_PAST_AUDIO,
            selected_past_turn_index=1,
            next_user_utterance="Book a hotel room.",
            decision_justification="Matches turn 1 audio.",
            step_progresses=[
                StepProgress(step=step_obj, status=StepStatus.IN_PROGRESS)
            ],
        ),
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            selected_past_turn_index=None,
            next_user_utterance="Seattle, please.",
            decision_justification="City was not in past audio.",
            step_progresses=[
                StepProgress(step=step_obj, status=StepStatus.IN_PROGRESS)
            ],
        ),
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.END_CONVERSATION,
            selected_past_turn_index=None,
            next_user_utterance="",
            decision_justification="Hotel booked.",
            step_progresses=[
                StepProgress(step=step_obj, status=StepStatus.COMPLETED)
            ],
        ),
    ]

    with patch(
        "cxas_scrapi.evals.shadow_evals.evaluate_expectations"
    ) as mock_eval_exp:
        mock_eval_exp.return_value = [
            ExpectationResult(
                expectation="Agent books a hotel in Seattle.",
                status=ExpectationStatus.MET,
                justification="Booked.",
            )
        ]

        shadow_evals = ShadowEvals(
            app_name="projects/p/locations/us/apps/a",
            creds=MagicMock(),
        )
        tc = ShadowTestCase(
            conversation_id="conv-stream-1",
            expectations=["Agent books a hotel in Seattle."],
        )
        conv = shadow_evals.run_shadow_conversation(
            tc,
            modality="audio",
            single_bidi_stream=True,
            console_logging=False,
        )
        assert len(conv.turn_logs) == 3
        assert mock_interactive.send_turn.call_count == 3
        # Turn 0: welcome event -> audio_bytes=None
        assert (
            mock_interactive.send_turn.call_args_list[0].kwargs["audio_bytes"]
            is None
        )
        # Turn 1: past audio -> audio_bytes=pcm_1
        assert (
            mock_interactive.send_turn.call_args_list[1].kwargs["audio_bytes"]
            == pcm_1
        )
        # Turn 2: TTS deviation -> audio_bytes=None
        assert (
            mock_interactive.send_turn.call_args_list[2].kwargs["audio_bytes"]
            is None
        )
        mock_interactive.close.assert_called_once()


def test_shadow_user_conversation_exact_replay() -> None:
    """When replay_mode='exact', ShadowUserConversation replays past audio
    turns sequentially with authentic audio bytes without calling Gemini.
    """
    mock_gemini = MagicMock()
    pcm_turn_1 = b"\xaa\xbb" * 160
    pcm_turn_2 = b"\xcc\xdd" * 160

    past_turns = [
        ShadowPastTurn(
            turn_index=1,
            user_transcript="First question from caller.",
            audio_uri="gs://bucket/user-turn-1.wav",
            has_audio=True,
            audio_bytes=pcm_turn_1,
        ),
        ShadowPastTurn(
            turn_index=2,
            user_transcript="Second confirmation from caller.",
            audio_uri="gs://bucket/user-turn-2.wav",
            has_audio=True,
            audio_bytes=pcm_turn_2,
        ),
    ]

    tc = ShadowTestCase(
        name="exact_replay_test",
        conversation_id="conv-exact",
        replay_mode="exact",
        expectations=["Agent succeeds."],
        initial_utterance="event: session start",
    )

    conv = ShadowUserConversation(
        genai_client=mock_gemini,
        genai_model="gemini-3.1-flash-lite",
        test_case=tc,
        past_turns=past_turns,
    )

    # Turn 0: event
    u0, a0, _, l0 = conv.next_user_turn()
    assert u0 == "event: session start"
    assert a0 is None
    assert l0.decision == "event"

    # Turn 1: exact replay of past turn 1
    u1, a1, _, l1 = conv.next_user_turn("Agent greeting.")
    assert u1 == "First question from caller."
    assert a1 == pcm_turn_1
    assert l1.decision == ShadowDecisionType.USE_PAST_AUDIO.value
    assert l1.selected_past_turn_index == 1

    # Turn 2: exact replay of past turn 2
    u2, a2, _, l2 = conv.next_user_turn("Agent ask confirmation.")
    assert u2 == "Second confirmation from caller."
    assert a2 == pcm_turn_2
    assert l2.decision == ShadowDecisionType.USE_PAST_AUDIO.value
    assert l2.selected_past_turn_index == 2

    # Turn 3: all turns replayed -> conversation ends
    u3, a3, _, l3 = conv.next_user_turn("Agent goodbye.")
    assert u3 == ""
    assert a3 is None
    assert l3 is None

    # Verify Gemini was never invoked for simulated turn decisions
    mock_gemini.generate.assert_not_called()


def _hybrid_conv(mock_gemini: MagicMock) -> ShadowUserConversation:
    past_turns = [
        ShadowPastTurn(
            turn_index=2,
            user_transcript="I'm calling about my order. The package never "
            "arrived.",
            has_audio=True,
            audio_bytes=b"\x01\x02" * 160,
            audio_path="/tmp/past_user_turn_2.wav",
        ),
        ShadowPastTurn(
            turn_index=3,
            user_transcript="Yes.",
            has_audio=True,
            audio_bytes=b"\x03\x04" * 160,
        ),
    ]
    tc = ShadowTestCase(
        conversation_id="conv-guard",
        expectations=["Agent resolves the delivery issue."],
        initial_utterance="event: session start",
    )
    conv = ShadowUserConversation(
        genai_client=mock_gemini,
        genai_model="gemini-3.1-flash-lite",
        test_case=tc,
        past_turns=past_turns,
    )
    conv.next_user_turn()  # consume initial event
    return conv


def test_shadow_user_tts_paraphrase_overridden_to_past_audio() -> None:
    """A generate_tts decision that restates an unused past turn replays the
    recorded audio instead of synthesizing TTS."""
    mock_gemini = MagicMock()
    mock_gemini.generate.return_value = ShadowUserConversation.Output(
        decision=ShadowDecisionType.GENERATE_TTS,
        next_user_utterance="Yes, I'm calling about my order. The package "
        "never arrived.",
        decision_justification="Agent asked if caller is an existing customer.",
    )
    conv = _hybrid_conv(mock_gemini)

    utt, audio, _, log = conv.next_user_turn("Are you an existing customer?")

    assert audio == b"\x01\x02" * 160
    assert utt.startswith("I'm calling about my order")
    assert log.decision == ShadowDecisionType.USE_PAST_AUDIO.value
    assert log.selected_past_turn_index == 2
    assert log.user_audio_path == "/tmp/past_user_turn_2.wav"
    assert "Overridden" in log.decision_justification
    assert conv.past_turns[0].used


def test_shadow_user_genuine_tts_not_overridden() -> None:
    """New information (and short non-identical answers) stays on TTS."""
    mock_gemini = MagicMock()
    mock_gemini.generate.side_effect = [
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            next_user_utterance="My zip code is 94043.",
        ),
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            next_user_utterance="No.",
        ),
    ]
    conv = _hybrid_conv(mock_gemini)

    _, audio1, _, log1 = conv.next_user_turn("What's your zip code?")
    _, audio2, _, log2 = conv.next_user_turn("Anything else?")

    assert audio1 is None
    assert audio2 is None
    assert log1.decision == ShadowDecisionType.GENERATE_TTS.value
    assert log2.decision == ShadowDecisionType.GENERATE_TTS.value
    assert not any(pt.used for pt in conv.past_turns)


def test_shadow_user_tts_with_new_info_kept_and_verbatim_repeat_reused() -> (
    None
):
    """TTS that adds new info stays TTS; a verbatim repeat of an already-used
    past turn re-replays its recording."""
    mock_gemini = MagicMock()
    mock_gemini.generate.side_effect = [
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.USE_PAST_AUDIO,
            selected_past_turn_index=2,
        ),
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            next_user_utterance="I'm calling about my order from last Tuesday "
            "afternoon. The package never arrived.",
        ),
        ShadowUserConversation.Output(
            decision=ShadowDecisionType.GENERATE_TTS,
            next_user_utterance="I'm calling about my order. The package "
            "never arrived.",
        ),
    ]
    conv = _hybrid_conv(mock_gemini)

    _, _, _, l1 = conv.next_user_turn("Are you an existing customer?")
    _, a2, _, l2 = conv.next_user_turn("Which order is this about?")
    _, a3, _, l3 = conv.next_user_turn("What are you calling about?")

    assert l1.decision == ShadowDecisionType.USE_PAST_AUDIO.value
    assert a2 is None
    assert l2.decision == ShadowDecisionType.GENERATE_TTS.value
    assert a3 == b"\x01\x02" * 160
    assert l3.decision == ShadowDecisionType.USE_PAST_AUDIO.value
    assert l3.selected_past_turn_index == 2


def test_load_shadow_yaml_inherits_tool_fakes_and_session_params() -> None:
    """`use_tool_fakes` and `session_parameters` in the global config block
    seed every shadow case (case-level values win)."""
    yaml_text = """
config:
  use_tool_fakes: true
  session_parameters:
    customer_tier: gold
shadow_evals:
  - conversation_id: conv-1
    expectations: ["Agent greets the returning customer."]
  - conversation_id: conv-2
    use_tool_fakes: false
    session_parameters:
      customer_tier: silver
    expectations: ["Agent offers the silver-tier options."]
"""
    cases = ShadowEvals.load_shadow_test_cases_from_yaml(yaml_text)

    assert cases[0].use_tool_fakes is True
    assert cases[0].session_parameters == {"customer_tier": "gold"}
    assert cases[1].use_tool_fakes is False
    assert cases[1].session_parameters == {"customer_tier": "silver"}


def test_load_shadow_yaml_warns_on_unknown_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Typos are dropped by pydantic, so the loader must warn about them."""
    yaml_text = """
config:
  use_toolfakes: true
shadow_evals:
  - conversation_id: conv-1
    session_params:
      customer_tier: gold
    expectations: ["Agent greets the returning customer."]
"""
    with caplog.at_level("WARNING"):
        cases = ShadowEvals.load_shadow_test_cases_from_yaml(yaml_text)

    assert cases[0].session_parameters == {}
    assert "Did you mean 'use_tool_fakes'?" in caplog.text
    assert "Did you mean 'session_parameters'?" in caplog.text


def test_shadow_test_case_rejects_invalid_replay_mode() -> None:
    with pytest.raises(ValueError, match="replay_mode"):
        ShadowTestCase(
            conversation_id="conv-1",
            expectations=["Agent helps."],
            replay_mode="exactly",
        )


def test_infer_recording_bucket_prefers_conversation_uri() -> None:
    normalized = {
        "raw": {
            "tool_payload": "gs://unrelated-bucket/some/file.json",
            "audio": "gs://rec-bucket/proj/us/app/2026-09-25/conv-1/x.wav",
        }
    }
    assert (
        ShadowEvals._infer_recording_bucket(normalized, "conv-1")
        == "gs://rec-bucket"
    )
    assert (
        ShadowEvals._infer_recording_bucket(normalized, "conv-2")
        == "gs://unrelated-bucket"
    )
    assert ShadowEvals._infer_recording_bucket({}, "conv-1") is None
