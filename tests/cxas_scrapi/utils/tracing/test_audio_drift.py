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

"""Tests for the drift-flag logic. Pure logic — no audio dependencies."""

from cxas_scrapi.utils.tracing import audio_drift as ad


def _row(
    turn: int,
    dur: float,
    lufs: float,
    n_syl: int = 30,
    text: str | None = None,
    tts: str | None = None,
) -> dict:
    r = {
        "turn": turn,
        "dur_s": dur,
        "lufs": lufs,
        "n_syl": n_syl,
        "agent_text": text
        if text is not None
        else "this line carries roughly thirty syllables of agent speech "
        "to keep the truncation ratio comfortable",
    }
    if tts is not None:
        r["tts"] = tts
    return r


def test_join_spans_matches_by_duration_and_leaves_multiclip_blank() -> None:
    rows = [_row(1, 3.68, -20), _row(2, 9.8, -19), _row(3, 7.5, -18)]
    spans = [
        {"start": "a", "cached": True, "ttfa_ms": 2, "audio_ms": 3680},
        {"start": "b", "cached": False, "ttfa_ms": 294, "audio_ms": 9800},
        # nothing within 0.3s of turn 3 (a multi-clip turn)
        {"start": "c", "cached": True, "ttfa_ms": 2, "audio_ms": 20920},
    ]
    ad.join_spans(rows, spans)
    assert rows[0]["tts"] == "cached"
    assert rows[1]["tts"] == "fresh"
    assert rows[2]["tts"] == ""


def test_seam_fires_on_cached_to_fresh_boundary_only() -> None:
    rows = [
        _row(1, 5, -19, tts="cached"),
        _row(2, 5, -19, tts="cached"),
        _row(3, 5, -19, tts="fresh"),
        _row(4, 5, -19, tts="fresh"),
    ]
    flagged = ad.flag_drift(rows)
    assert [r["turn"] for r in flagged] == [3]
    assert rows[2]["flags"] == ["SEAM"]


def test_unannotated_turns_do_not_break_the_seam_chain() -> None:
    rows = [
        _row(1, 5, -19, tts="cached"),
        _row(2, 5, -19, tts=""),  # multi-clip, unannotated
        _row(3, 5, -19, tts="fresh"),
    ]
    flagged = ad.flag_drift(rows)
    assert [r["turn"] for r in flagged] == [3]


def test_loud_flags_steps_beyond_threshold_with_signed_delta() -> None:
    rows = [_row(1, 5, -19.0), _row(2, 5, -23.4), _row(3, 5, -17.6)]
    flagged = ad.flag_drift(rows)
    assert [r["turn"] for r in flagged] == [2, 3]
    assert rows[1]["dlufs"] == -4.4
    assert rows[2]["dlufs"] == 5.8


def test_trunc_needs_a_real_deficit_and_enough_expected_syllables() -> None:
    long_text = (
        "yes I'm sure there is no fee to make a payment over the phone "
        "what day between now and your next due date works for you"
    )
    rows = [
        _row(1, 5, -19, n_syl=10, text=long_text),  # well under budget
        _row(2, 5, -19, n_syl=28, text=long_text),  # close enough
        _row(3, 5, -19, n_syl=1, text="Okay."),  # too short to judge
    ]
    flagged = ad.flag_drift(rows)
    assert [r["turn"] for r in flagged] == [1]
    assert rows[0]["flags"] == ["TRUNC"]


def test_describe_combines_reasons() -> None:
    row = {"flags": ["SEAM", "LOUD", "TRUNC"], "dlufs": 5.8}
    text = ad.describe(row)
    assert "seam" in text
    assert "+5.8" in text
    assert "truncated" in text


def test_syllable_counter_on_known_phrases() -> None:
    assert ad.syllables_in_text("hello") == 2
    assert ad.syllables_in_text("one moment please") == 4
    assert ad.syllables_in_text("") == 0
    assert ad.syllables_in_text("7 3 1") == 3  # bare digits count as spoken
