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

"""Numeric voice-consistency metrics over per-turn agent recordings.

The Gemini analyses in `audio_analysis.py` answer perceptual questions
(same speaker? long pauses?) with PASS/FAIL and prose. This module is the
other half: signal measurements that do not know what produced the audio
and cannot be talked into anything. Per agent turn it measures

* integrated loudness (ITU-R BS.1770, LUFS) and within-turn loudness
  range (95th minus 10th percentile of the 3 s short-term track),
* articulation rate in syllables/second, from energy-peak syllable
  nuclei (de Jong & Wempe 2009 style: intensity peaks with >= 2 dB
  prominence, above a floor 25 dB under the 99th percentile, voiced at
  the peak),
* median fundamental frequency, in Hz and converted to semitones for
  cross-turn spread,
* a coarse timbre vector (mean MFCC 1..12 over phonated frames), used
  only for cosine-drift scores across turns.

Per call it aggregates turn-to-turn consistency: the loudness standard
deviation and the largest adjacent-turn jump, the articulation-rate
coefficient of variation, the largest within-turn loudness range, and
the spread of median pitch. `DEFAULT_THRESHOLDS` flags the values that
listeners reported as "the voice changed" in live reviews.

Caveat: pitch on whispered or otherwise aperiodic audio is unreliable —
with no glottal pulse the estimator latches onto formant structure and
reads roughly double the true value. When a turn's loudness collapses,
trust the loudness numbers and ignore its pitch.

Requires the optional audio-metrics dependencies::

    pip install "cxas-scrapi[audio-metrics]"
"""

from __future__ import annotations

import math
import os
import re
import statistics
import wave
from itertools import pairwise
from typing import Any

import numpy as np
from scipy.signal import find_peaks

try:  # optional heavy deps; everything else in the package works without them
    import parselmouth
    import pyloudnorm
except ImportError:  # pragma: no cover - exercised only without the extra
    parselmouth = None
    pyloudnorm = None

_MISSING_DEPS_MSG = (
    "audio metrics need the optional dependencies; install them with "
    'pip install "cxas-scrapi[audio-metrics]"'
)

#: Ignore fragments shorter than this (clicks / aborted turns).
MIN_TURN_S = 0.6

#: Turns shorter than this still get per-turn rows but are excluded from
#: call-level consistency scores, so truncated fragments don't fake a jump.
MIN_SCORED_TURN_S = 1.0

#: Call-level values above these read as "the voice changed" to listeners.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_adj_dlufs": 3.0,
    "lufs_sd": 1.5,
    "rate_cov_pct": 12.0,
    "max_lra_within_turn": 6.0,
    "f0_median_sd_st": 2.0,
}

_TURN_NUM_RE = re.compile(r"agent-turn-(\d+)")


def hz_to_semitones(f: float, ref: float = 100.0) -> float:
    return 12.0 * math.log2(f / ref)


def _cosine_dist(a: Any, b: Any) -> float:
    a = np.asarray(a)
    b = np.asarray(b)
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    denom = np.linalg.norm(a) * np.linalg.norm(b) + 1e-9
    return float(1.0 - np.dot(a, b) / denom)


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    """Reads a PCM WAV (the shape CES writes) to mono float32 in [-1, 1]."""
    with wave.open(path, "rb") as fh:
        sr = fh.getframerate()
        width = fh.getsampwidth()
        n_ch = fh.getnchannels()
        raw = fh.readframes(fh.getnframes())
    if width == 2:
        y = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        y = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        y = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        y = (y - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported PCM sample width {width} in {path}")
    if n_ch > 1:
        y = y.reshape(-1, n_ch).mean(axis=1)
    return y, sr


def _short_term_loudness(
    meter: Any,
    y: np.ndarray,
    sr: int,
    win: float = 3.0,
    hop: float = 1.0,
) -> list[float]:
    """ITU-R BS.1770 short-term loudness track (3 s windows, 1 s hop)."""
    n = int(win * sr)
    h = int(hop * sr)
    vals = []
    for s in range(0, max(1, len(y) - n + 1), h):
        seg = y[s : s + n]
        if len(seg) < n:
            break
        loud = meter.integrated_loudness(seg)
        if np.isfinite(loud) and loud > -70:
            vals.append(loud)
    return vals


def _syllable_nuclei(intensity: Any, pitch: Any) -> tuple[int, float, float]:
    """Energy-peak syllable nuclei (de Jong & Wempe 2009 style).

    Peaks in the intensity contour with >= 2 dB prominence, above a
    threshold 25 dB below the 99th percentile, and voiced (F0 defined)
    at the peak. Returns (n_syllables, phonation_s, voiced_s).
    """

    vals = intensity.values[0]
    ts = intensity.xs()
    if len(vals) < 5:
        return 0, 0.0, 0.0
    p99 = np.percentile(vals, 99)
    thr = max(p99 - 25.0, np.min(vals) + 2.0)
    peaks, _ = find_peaks(vals, height=thr, prominence=2.0)
    n = 0
    for p in peaks:
        f0 = pitch.get_value_at_time(ts[p])
        if f0 is not None and not math.isnan(f0) and f0 > 0:
            n += 1
    dt = ts[1] - ts[0] if len(ts) > 1 else 0.01
    phonation = float(np.sum(vals > thr) * dt)
    f0s = pitch.selected_array["frequency"]
    voiced = float(np.sum(f0s > 0) * pitch.dt)
    return n, phonation, voiced


def turn_metrics(path: str, meter_cache: dict | None = None) -> dict:
    """Per-turn features for one agent recording. See the module docstring."""
    if parselmouth is None or pyloudnorm is None:
        raise ImportError(_MISSING_DEPS_MSG)
    y, sr = _read_wav(path)
    dur = len(y) / sr
    feat: dict = {"path": path, "dur_s": round(dur, 3), "sr": sr}
    if dur < MIN_TURN_S:
        feat["skipped"] = "too_short"
        return feat
    if meter_cache is None:
        meter_cache = {}
    if sr not in meter_cache:
        meter_cache[sr] = pyloudnorm.Meter(sr)
    meter = meter_cache[sr]

    # --- loudness
    lufs = meter.integrated_loudness(y)
    feat["lufs"] = round(float(lufs), 2) if np.isfinite(lufs) else float("nan")
    feat["peak_dbfs"] = round(
        20 * math.log10(float(np.max(np.abs(y))) + 1e-9), 2
    )
    stl = _short_term_loudness(meter, y, sr)
    if len(stl) >= 2:
        feat["lra_within_turn"] = round(
            float(np.percentile(stl, 95) - np.percentile(stl, 10)), 2
        )
    else:
        feat["lra_within_turn"] = float("nan")

    # --- prosody
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=400)
    intensity = snd.to_intensity(minimum_pitch=75, time_step=0.01)
    f0 = pitch.selected_array["frequency"]
    f0v = f0[f0 > 0]
    if len(f0v) > 5:
        feat["f0_mean_hz"] = round(float(np.mean(f0v)), 1)
        feat["f0_median_hz"] = round(float(np.median(f0v)), 1)
        feat["f0_sd_st"] = round(
            float(np.std([hz_to_semitones(f) for f in f0v])), 2
        )
    else:
        feat["f0_mean_hz"] = float("nan")
        feat["f0_median_hz"] = float("nan")
        feat["f0_sd_st"] = float("nan")

    n_syl, phonation_s, voiced_s = _syllable_nuclei(intensity, pitch)
    feat["n_syl"] = n_syl
    feat["phonation_s"] = round(phonation_s, 2)
    feat["voiced_s"] = round(voiced_s, 2)
    feat["artic_rate"] = (
        round(n_syl / phonation_s, 2) if phonation_s > 0.3 else float("nan")
    )
    feat["speak_rate"] = round(n_syl / dur, 2)
    feat["pause_frac"] = (
        round(1.0 - phonation_s / dur, 2) if dur > 0 else float("nan")
    )

    # --- coarse timbre vector: mean MFCC(1..12) over phonated frames
    mfcc = snd.to_mfcc(
        number_of_coefficients=12, window_length=0.025, time_step=0.01
    )
    m = mfcc.to_array()  # (13, nframes); row 0 is c0 (energy)
    ivals = np.interp(mfcc.xs(), intensity.xs(), intensity.values[0])
    thr = max(
        np.percentile(intensity.values[0], 99) - 25.0,
        np.min(intensity.values[0]) + 2.0,
    )
    mask = ivals > thr
    if mask.sum() > 10:
        feat["_mfcc"] = m[1:, mask].mean(axis=1).tolist()
    else:
        feat["_mfcc"] = [float("nan")] * 12
    return feat


def _turn_number(path: str) -> int:
    m = _TURN_NUM_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else 0


def measure_call(
    wav_paths: list[str],
    meter_cache: dict | None = None,
    thresholds: dict[str, float] | None = None,
) -> tuple[dict, list[dict]]:
    """Per-call consistency scores over a conversation's agent turns.

    Args:
        wav_paths: local paths of the `agent-turn-N.wav` files. Sorted by
            turn number internally.
        meter_cache: optional dict reused across calls so the loudness
            meter is built once per sample rate.
        thresholds: flag levels; defaults to `DEFAULT_THRESHOLDS`.

    Returns:
        (call, turns): `call` holds the aggregate scores plus a `flags`
        list naming every threshold exceeded; `turns` holds one row per
        file (rows for skipped fragments carry `skipped`).
    """
    if parselmouth is None or pyloudnorm is None:
        raise ImportError(_MISSING_DEPS_MSG)
    if meter_cache is None:
        meter_cache = {}
    thresholds = dict(thresholds or DEFAULT_THRESHOLDS)

    turns = []
    for path in sorted(wav_paths, key=_turn_number):
        feat = turn_metrics(path, meter_cache)
        feat["turn"] = _turn_number(path)
        turns.append(feat)

    valid = [
        t
        for t in turns
        if "skipped" not in t
        and t["dur_s"] >= MIN_SCORED_TURN_S
        and np.isfinite(t.get("lufs", float("nan")))
    ]
    call: dict = {"n_turns": len(turns), "n_valid": len(valid)}
    if len(valid) >= 2:
        loud = [t["lufs"] for t in valid]
        rates = [t["artic_rate"] for t in valid if np.isfinite(t["artic_rate"])]
        pitches = [
            hz_to_semitones(t["f0_median_hz"])
            for t in valid
            if np.isfinite(t["f0_median_hz"])
        ]
        call["lufs_mean"] = round(statistics.mean(loud), 2)
        call["lufs_sd"] = round(statistics.pstdev(loud), 2)
        call["lufs_range"] = round(max(loud) - min(loud), 2)
        jumps = [abs(b - a) for a, b in pairwise(loud)]
        i = int(np.argmax(jumps))
        call["max_adj_dlufs"] = round(max(jumps), 2)
        call["max_adj_dlufs_turns"] = (
            f"{valid[i]['turn']}->{valid[i + 1]['turn']}"
        )
        lra = [
            t["lra_within_turn"]
            for t in valid
            if np.isfinite(t["lra_within_turn"])
        ]
        call["max_lra_within_turn"] = (
            round(max(lra), 2) if lra else float("nan")
        )
        if len(rates) >= 2:
            call["rate_mean"] = round(statistics.mean(rates), 2)
            call["rate_cov_pct"] = round(
                100 * statistics.pstdev(rates) / statistics.mean(rates), 1
            )
            drate = [
                abs(b - a) / ((a + b) / 2) * 100 for a, b in pairwise(rates)
            ]
            call["max_adj_drate_pct"] = round(max(drate), 1)
        if len(pitches) >= 2:
            call["f0_median_sd_st"] = round(statistics.pstdev(pitches), 2)
            call["f0_median_range_st"] = round(max(pitches) - min(pitches), 2)
        first = valid[0]["_mfcc"]
        vs_first = [
            d
            for d in (_cosine_dist(first, t["_mfcc"]) for t in valid[1:])
            if np.isfinite(d)
        ]
        vs_prev = [
            d
            for d in (
                _cosine_dist(a["_mfcc"], b["_mfcc"]) for a, b in pairwise(valid)
            )
            if np.isfinite(d)
        ]
        if vs_first:
            call["timbre_drift_vs_t1_mean"] = round(
                statistics.mean(vs_first), 4
            )
            call["timbre_drift_vs_t1_max"] = round(max(vs_first), 4)
        if vs_prev:
            call["timbre_drift_vs_prev_max"] = round(max(vs_prev), 4)

    call["flags"] = sorted(
        k
        for k, limit in thresholds.items()
        if np.isfinite(call.get(k, float("nan"))) and call[k] > limit
    )
    for t in turns:
        t.pop("_mfcc", None)
    return call, turns
