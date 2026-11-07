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

"""Tests for the numeric voice-consistency metrics.

Skipped wholesale when the `audio-metrics` optional dependencies are not
installed; the module under test raises a clear ImportError in that case
and everything else in the package is unaffected.
"""

import math
import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyloudnorm")
pytest.importorskip("parselmouth")

from cxas_scrapi.utils.tracing import audio_metrics as am  # noqa: E402

SR = 16000


def _write_voice_wav(
    path: Path,
    seconds: float = 2.0,
    amplitude: float = 0.2,
    f0: float = 150.0,
) -> str:
    """A voiced-sounding test tone: an f0 pulse train with a 4 Hz
    syllable-like amplitude envelope, PCM16 mono."""
    t = np.arange(int(seconds * SR)) / SR
    # Harmonic-rich source so the pitch tracker locks on.
    y = (
        np.sin(2 * math.pi * f0 * t)
        + 0.5 * np.sin(2 * math.pi * 2 * f0 * t)
        + 0.25 * np.sin(2 * math.pi * 3 * f0 * t)
    )
    envelope = 0.55 + 0.45 * np.sign(np.sin(2 * math.pi * 4.0 * t))
    y = amplitude * y / np.max(np.abs(y)) * envelope
    pcm = (y * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())
    return str(path)


def test_turn_metrics_reports_loudness_pitch_and_rate(tmp_path: Path) -> None:
    p = _write_voice_wav(tmp_path / "agent-turn-1.wav")
    feat = am.turn_metrics(p)
    assert feat["dur_s"] == pytest.approx(2.0, abs=0.01)
    assert np.isfinite(feat["lufs"])
    # The source is a 150 Hz pulse train; the tracker should land near it.
    assert feat["f0_median_hz"] == pytest.approx(150.0, abs=15.0)
    assert feat["n_syl"] >= 1


def test_short_fragment_is_skipped(tmp_path: Path) -> None:
    p = _write_voice_wav(tmp_path / "agent-turn-1.wav", seconds=0.3)
    feat = am.turn_metrics(p)
    assert feat["skipped"] == "too_short"


def test_identical_turns_raise_no_flags(tmp_path: Path) -> None:
    paths = [
        _write_voice_wav(tmp_path / f"agent-turn-{i}.wav") for i in (1, 2, 3)
    ]
    call, turns = am.measure_call(paths)
    assert call["n_valid"] == 3
    assert call["lufs_sd"] == pytest.approx(0.0, abs=0.1)
    assert call["flags"] == []
    assert [t["turn"] for t in turns] == [1, 2, 3]


def test_loudness_jump_is_measured_and_flagged(tmp_path: Path) -> None:
    quiet = _write_voice_wav(tmp_path / "agent-turn-1.wav", amplitude=0.1)
    loud = _write_voice_wav(tmp_path / "agent-turn-2.wav", amplitude=0.4)
    call, _ = am.measure_call([loud, quiet])  # order-proof: sorted by turn
    # 4x amplitude is +12 dB; both the jump and the spread must flag.
    assert call["max_adj_dlufs"] == pytest.approx(12.0, abs=1.0)
    assert call["max_adj_dlufs_turns"] == "1->2"
    assert "max_adj_dlufs" in call["flags"]
    assert "lufs_sd" in call["flags"]


def test_missing_turn_numbers_sort_last_and_count(tmp_path: Path) -> None:
    a = _write_voice_wav(tmp_path / "agent-turn-2.wav")
    b = _write_voice_wav(tmp_path / "agent-turn-10.wav")
    call, turns = am.measure_call([b, a])
    assert [t["turn"] for t in turns] == [2, 10]
    assert call["n_turns"] == 2


def _write_two_voice_wav(
    path: Path,
    f0_a: float,
    f0_b: float,
    gap_s: float = 0.3,
    part_s: float = 1.8,
) -> str:
    """Two voiced parts at different pitches with a pause between them, the
    shape of a turn spliced from two renders."""

    def part(f0: float) -> np.ndarray:
        t = np.arange(int(part_s * SR)) / SR
        y = (
            np.sin(2 * math.pi * f0 * t)
            + 0.5 * np.sin(2 * math.pi * 2 * f0 * t)
            + 0.25 * np.sin(2 * math.pi * 3 * f0 * t)
        )
        envelope = 0.55 + 0.45 * np.sign(np.sin(2 * math.pi * 4.0 * t))
        return 0.2 * y / np.max(np.abs(y)) * envelope

    y = np.concatenate([part(f0_a), np.zeros(int(gap_s * SR)), part(f0_b)])
    pcm = (y * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())
    return str(path)


def test_seam_reports_an_apparent_speaker_change(tmp_path: Path) -> None:
    # +12.7 st: the calibrated male->female greeting splice.
    p = _write_two_voice_wav(tmp_path / "agent-turn-1.wav", 120.0, 250.0)
    feat = am.turn_metrics(p)
    assert len(feat["seams"]) == 1
    seam = feat["seams"][0]
    assert seam["tier"] == "speaker_change"
    assert seam["shift_st"] == pytest.approx(12.7, abs=0.6)
    assert seam["t_s"] == pytest.approx(1.8, abs=0.1)
    assert feat["max_seam_st"] == pytest.approx(12.7, abs=0.6)


def test_seam_reports_drift_between_seven_and_eleven_semitones(
    tmp_path: Path,
) -> None:
    p = _write_two_voice_wav(
        tmp_path / "agent-turn-1.wav", 120.0, 185.0
    )  # +7.5 st
    seams = am.turn_metrics(p)["seams"]
    assert [s["tier"] for s in seams] == ["drift"]


def test_ordinary_intonation_is_not_a_seam(tmp_path: Path) -> None:
    p = _write_two_voice_wav(
        tmp_path / "agent-turn-1.wav", 120.0, 130.0
    )  # +1.4 st
    feat = am.turn_metrics(p)
    assert feat["seams"] == []
    assert feat["max_seam_st"] == 0.0


def test_a_long_pause_is_not_a_splice(tmp_path: Path) -> None:
    p = _write_two_voice_wav(
        tmp_path / "agent-turn-1.wav", 120.0, 250.0, gap_s=2.0
    )
    assert am.turn_metrics(p)["seams"] == []


def test_call_flags_name_the_turns_with_seams(tmp_path: Path) -> None:
    paths = [
        _write_voice_wav(tmp_path / "agent-turn-1.wav", seconds=3.0, f0=120.0),
        _write_two_voice_wav(tmp_path / "agent-turn-2.wav", 120.0, 250.0),
        _write_two_voice_wav(tmp_path / "agent-turn-3.wav", 120.0, 185.0),
    ]
    call, _ = am.measure_call(paths)
    assert call["seam_speaker_change_turns"] == [2]
    assert call["seam_drift_turns"] == [3]
    assert "intra_turn_speaker_change" in call["flags"]
    assert "intra_turn_voice_drift" in call["flags"]
    assert call["max_seam_st"] == pytest.approx(12.7, abs=0.6)


def _write_render_join_wav(
    path: Path, f0_b: float, gain_b: float, f0_a: float = 150.0
) -> str:
    """Two rendered parts: the second at pitch `f0_b` and `gain_b` times
    the first part's level, after a 0.3 s pause."""

    def part(f0: float, gain: float) -> np.ndarray:
        t = np.arange(int(1.8 * SR)) / SR
        y = sum(np.sin(2 * math.pi * k * f0 * t) / k for k in range(1, 6))
        envelope = 0.6 + 0.4 * np.sin(2 * math.pi * 4.0 * t)
        return 0.15 * gain * y / np.max(np.abs(y)) * envelope

    y = np.concatenate(
        [part(f0_a, 1.0), np.zeros(int(0.3 * SR)), part(f0_b, gain_b)]
    )
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())
    return str(path)


PARTS = [
    "One two three four five six seven.",
    "Eight nine ten eleven twelve thirteen fourteen.",
]


def test_a_render_join_rising_in_pitch_and_loudness_is_a_jump(
    tmp_path: Path,
) -> None:
    # +5.8 st and about +6 dB where the second part starts.
    p = _write_render_join_wav(tmp_path / "agent-turn-1.wav", 210.0, 2.0)
    (join,) = am.render_join_steps(p, PARTS)
    assert join["jump"]
    assert join["part"] == 2
    assert join["d_st"] == pytest.approx(5.8, abs=0.6)
    assert join["d_db"] == pytest.approx(6.0, abs=1.0)


def test_pitch_or_loudness_alone_at_a_join_is_not_a_jump(
    tmp_path: Path,
) -> None:
    (pitch,) = am.render_join_steps(
        _write_render_join_wav(tmp_path / "p.wav", 230.0, 1.0), PARTS
    )
    (loud,) = am.render_join_steps(
        _write_render_join_wav(tmp_path / "l.wav", 150.0, 2.5), PARTS
    )
    assert pitch["d_st"] > 6
    assert not pitch["jump"]
    assert loud["d_db"] > 6
    assert not loud["jump"]


def test_a_single_part_turn_has_no_joins(tmp_path: Path) -> None:
    p = _write_render_join_wav(tmp_path / "agent-turn-1.wav", 210.0, 2.0)
    assert am.render_join_steps(p, [" ".join(PARTS)]) == []
    assert am.render_join_shares(["Hello.", "", "Goodbye now."]) == [
        pytest.approx(2 / 5)
    ]
