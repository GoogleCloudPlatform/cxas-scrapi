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

"""Unit tests for simulation audio persistence, concatenation, and report rendering."""

import os
import shutil
import struct
import typing
import uuid
import wave
from unittest.mock import patch

from cxas_scrapi.evals.simulation_evals import (
    SimulationEvals,
    cleanup_session_dir,
)

try:
    from cxas_scrapi.evals.simulation_evals import persist_simulation_audio
except ImportError:
    persist_simulation_audio = None

from cxas_scrapi.utils.reporting import generate_combined_html_report


def _create_synthetic_wav(
    filepath: str,
    num_frames: int = 1600,
    framerate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
    amplitude: int = 500,
) -> str:
    """Creates a valid RIFF WAV file with deterministic PCM audio frames."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        sample = struct.pack("<h", amplitude)
        wf.writeframes(sample * num_frames)
    return filepath


def test_simulation_audio_persistence_leaves_files_intact(
    tmp_path: typing.Any,
) -> None:
    """Verifies turn audio is preserved and not deleted by cleanup_session_dir."""
    session_id = f"test-sess-{uuid.uuid4().hex[:8]}"
    temp_dir = f"/tmp/scrapi_evals/{session_id}"
    persistent_dir = f"/tmp/scrapi_audio/{session_id}"

    class DummyConversation:
        def __init__(self) -> None:
            self.agent_audio_paths: dict[int, str] = {}

    class DummySimWrapper:
        @cleanup_session_dir
        def simulate_conversation(
            self, session_id: str | None = None
        ) -> DummyConversation:
            os.makedirs(temp_dir, exist_ok=True)
            turn0 = os.path.join(temp_dir, "turn_0_agent.wav")
            turn1 = os.path.join(temp_dir, "turn_1_agent.wav")
            _create_synthetic_wav(turn0, num_frames=800, framerate=16000)
            _create_synthetic_wav(turn1, num_frames=1200, framerate=16000)

            conv = DummyConversation()
            conv.agent_audio_paths = {0: turn0, 1: turn1}
            return conv

    runner = DummySimWrapper()
    conv = runner.simulate_conversation(session_id=session_id)

    try:
        # 1. Verify cleanup_session_dir wiped temp_dir
        assert not os.path.exists(temp_dir), (
            f"Temporary directory {temp_dir} was not cleaned up"
        )

        # 2. Verify audio files were preserved and NOT deleted
        assert os.path.isdir(persistent_dir), (
            f"Persistent audio store {persistent_dir} was not created"
        )
        persisted_turn0 = os.path.join(persistent_dir, "turn_0_agent.wav")
        persisted_turn1 = os.path.join(persistent_dir, "turn_1_agent.wav")
        assert os.path.isfile(persisted_turn0), (
            f"{persisted_turn0} was lost during session cleanup"
        )
        assert os.path.isfile(persisted_turn1), (
            f"{persisted_turn1} was lost during session cleanup"
        )

        # 3. Verify conv.agent_audio_paths was updated to persistent store
        assert conv.agent_audio_paths[0] == persisted_turn0
        assert conv.agent_audio_paths[1] == persisted_turn1

        # 4. Verify persist_simulation_audio creates report audio tree
        assert persist_simulation_audio is not None, (
            "persist_simulation_audio must be available"
        )
        report_output_dir = str(tmp_path / "eval_run")
        persist_res = persist_simulation_audio(
            session_id=session_id,
            audio_paths=conv.agent_audio_paths,
            output_dir=report_output_dir,
        )

        target_audio_dir = os.path.join(report_output_dir, "audio", session_id)
        assert os.path.isdir(target_audio_dir)
        full_wav = os.path.join(target_audio_dir, "full-scenario.wav")
        assert os.path.isfile(full_wav), (
            "full-scenario.wav was not created by persist_simulation_audio"
        )
        assert os.path.isfile(
            os.path.join(target_audio_dir, "turn_0_agent.wav")
        )
        assert os.path.isfile(
            os.path.join(target_audio_dir, "agent-turn-1.wav")
        )
        assert os.path.isfile(
            os.path.join(target_audio_dir, "turn_1_agent.wav")
        )
        assert os.path.isfile(
            os.path.join(target_audio_dir, "agent-turn-2.wav")
        )
        assert persist_res.get("full_audio_path") == full_wav
    finally:
        shutil.rmtree(persistent_dir, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_wave_concatenation_accurate_riff_header_and_frame_count(
    tmp_path: typing.Any,
) -> None:
    """Verifies WAV chunks concatenate into a valid RIFF file with expected frames."""
    assert persist_simulation_audio is not None, (
        "persist_simulation_audio must be available"
    )

    session_id = f"sess-concat-{uuid.uuid4().hex[:8]}"
    source_dir = tmp_path / "raw_audio"
    source_dir.mkdir(parents=True, exist_ok=True)

    framerate = 24000
    turn0_wav = _create_synthetic_wav(
        str(source_dir / "chunk0.wav"), num_frames=1000, framerate=framerate
    )
    turn1_wav = _create_synthetic_wav(
        str(source_dir / "chunk1.wav"), num_frames=1500, framerate=framerate
    )
    turn2_wav = _create_synthetic_wav(
        str(source_dir / "chunk2.wav"), num_frames=500, framerate=framerate
    )

    audio_paths = {0: turn0_wav, 1: turn1_wav, 2: turn2_wav}
    out_dir = str(tmp_path / "persisted")

    result = persist_simulation_audio(
        session_id=session_id,
        audio_paths=audio_paths,
        output_dir=out_dir,
    )

    full_path = result.get("full_audio_path")
    assert full_path is not None, "full_audio_path was not returned"
    assert os.path.isfile(full_path), f"File {full_path} does not exist"

    # Verify standard RIFF WAV header
    with open(full_path, "rb") as f:
        header = f.read(12)
        assert header[:4] == b"RIFF", (
            f"Expected RIFF header, got {header[:4]!r}"
        )
        assert header[8:12] == b"WAVE", (
            f"Expected WAVE format, got {header[8:12]!r}"
        )

    # Verify frame count and audio parameters
    with wave.open(full_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == framerate
        assert wf.getnframes() == 3000, (
            f"Expected 3000 total frames, got {wf.getnframes()}"
        )
        frames_bytes = wf.readframes(3000)
        assert len(frames_bytes) == 3000 * 2


def test_generate_combined_html_report_auto_infers_audio_modality_and_renders_player(
    tmp_path: typing.Any,
) -> None:
    """Verifies HTML report generation auto-infers audio modality and renders player."""
    report_dir = tmp_path / "reports_test"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = str(report_dir / "combined_report.html")

    session_id = f"sim-sess-{uuid.uuid4().hex[:8]}"
    raw_dir = tmp_path / "source_wavs"
    raw_dir.mkdir(parents=True, exist_ok=True)

    turn0_wav = _create_synthetic_wav(
        str(raw_dir / "turn0.wav"), num_frames=1200, framerate=16000
    )
    turn1_wav = _create_synthetic_wav(
        str(raw_dir / "turn1.wav"), num_frames=1600, framerate=16000
    )

    # Note: sim_modality is NOT passed, defaulting to "text".
    sim_results = [
        {
            "name": "voice_ordering_cuj",
            "passed": True,
            "run": 1,
            "goals": "1/1",
            "expectations": "0/0",
            "turns": 2,
            "duration_s": 3.5,
            "session_id": session_id,
            "session_parameters": {},
            "agent_audio_paths": {0: turn0_wav, 1: turn1_wav},
            "step_details": [
                {
                    "goal": "Order package delivery",
                    "success_criteria": "Booking confirmed",
                    "status": "Completed",
                    "justification": "All parameters gathered",
                }
            ],
            "expectation_details": [],
            "detailed_trace": [
                "User: Hello, I would like to schedule a pickup",
                "Agent Text: I can help with that. What is your address?",
                "User: 123 Main St",
                "Agent Text: Pickup scheduled for tomorrow.",
            ],
            "_processed_trace": [
                ("user", "Hello, I would like to schedule a pickup"),
                ("agent", "I can help with that. What is your address?"),
                ("user", "123 Main St"),
                ("agent", "Pickup scheduled for tomorrow."),
            ],
        }
    ]

    out_path = generate_combined_html_report(
        sim_results=sim_results,
        output_path=report_file,
        app_name="projects/test-proj/locations/global/apps/voice-agent",
    )

    assert os.path.isfile(out_path), "HTML report was not generated"

    with open(out_path, encoding="utf-8") as f:
        html_content = f.read()

    # 1. Verify audio player was rendered in HTML
    player_src = f"audio/{session_id}/full-scenario.wav"
    assert f'src="{player_src}"' in html_content, (
        f"Audio player with src='{player_src}' was not rendered in HTML report. "
        "Likely sim_modality was not auto-inferred as 'audio'."
    )
    assert '<div class="audio-player">' in html_content
    assert "<audio controls" in html_content

    # 2. Verify resolved audio file is physically present at the expected path relative to the report
    resolved_audio_file = os.path.join(
        str(report_dir), "audio", session_id, "full-scenario.wav"
    )
    assert os.path.isfile(resolved_audio_file), (
        f"Target audio file {resolved_audio_file} does not exist. "
        "The report references this file but it was not copied or generated."
    )
    assert os.path.getsize(resolved_audio_file) > 0, "Audio file is empty"

    # Verify per-turn audio files are also copied for individual turn playback
    assert os.path.isfile(
        os.path.join(str(report_dir), "audio", session_id, "turn_0_agent.wav")
    )
    assert os.path.isfile(
        os.path.join(str(report_dir), "audio", session_id, "agent-turn-1.wav")
    )


def test_simulation_job_enriches_audio_paths(tmp_path: typing.Any) -> None:
    """Verifies _run_single_simulation_job populates audio paths in row dict."""
    raw_wav = _create_synthetic_wav(
        str(tmp_path / "agent_turn.wav"), num_frames=800, framerate=16000
    )

    class MockConv:
        def __init__(self) -> None:
            self.agent_audio_paths = {0: raw_wav}
            self.detailed_trace = ["User: hi", "Agent Text: hello"]
            self.steps_progress = []
            self.expectation_results = []
            self.naturalness_result = None
            self.current_turn = 1

        def get_transcript(self) -> str:
            return "User: hi\nAgent: hello"

    with patch.object(
        SimulationEvals, "__init__", lambda self, *args, **kwargs: None
    ):
        sim = SimulationEvals("test-app")
        sim.single_bidi_stream = False
        sim.progress = None
        sim.task_id = None
        sim.deployment_id = None
        sim.expectations_only = False
        sim.naturalness = None

        with patch.object(
            sim, "simulate_conversation", return_value=MockConv()
        ):
            tc = {"name": "test_audio_tc"}
            res = sim._run_single_simulation_job(
                tc=tc,
                run_idx=0,
                runs=1,
                sim_user_model="gemini-1.5-flash",
                eval_model="gemini-1.5-flash",
                modality="audio",
                verbose=False,
                parallel=1,
                capture_agent_audio=True,
                output_dir=str(tmp_path / "out"),
            )

            assert "agent_audio_paths" in res, (
                "agent_audio_paths missing from simulation job result"
            )
            assert "full_audio_path" in res, (
                "full_audio_path missing from simulation job result"
            )
            assert res["agent_audio_paths"], (
                "agent_audio_paths should not be empty"
            )
            assert res["full_audio_path"] is not None, (
                "full_audio_path should not be None"
            )
            assert os.path.isfile(res["full_audio_path"])


def test_persist_simulation_audio_no_wav_files_does_not_create_empty_dir(
    tmp_path: typing.Any,
) -> None:
    """Verifies persist_simulation_audio does not create audio/<session_id> when no WAVs exist."""
    assert persist_simulation_audio is not None, (
        "persist_simulation_audio must be available"
    )
    session_id = f"text-sess-{uuid.uuid4().hex[:8]}"
    out_dir = str(tmp_path / "text_eval_out")
    res = persist_simulation_audio(
        session_id=session_id,
        audio_paths={},
        output_dir=out_dir,
    )
    assert not res.get("agent_audio_paths")
    assert not res.get("full_audio_path")
    assert not os.path.exists(os.path.join(out_dir, "audio", session_id)), (
        "Text simulation with no WAV files must not create empty audio/<session_id> directory"
    )
