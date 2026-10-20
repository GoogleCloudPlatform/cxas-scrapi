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
