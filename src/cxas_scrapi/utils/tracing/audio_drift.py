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

"""Voice-drift detection for one conversation's agent audio.

The platform synthesizes agent speech per clip and caches clips by text.
A cached clip replays a rendition frozen at its first synthesis — a
different sampling run, a different conversational context, and possibly
an older model build than today's fresh clips. Splicing vintages into one
call is what listeners report as "the agent's voice changed".

This module turns that into per-turn flags, from three independent
signal families:

  SEAM   provenance — this turn's TTS source (cached vs fresh, read from
         the platform's own TTS spans in Cloud Logging: `cache hit`,
         `time to first audio (ms)`) differs from the previous annotated
         turn. The cached<->fresh boundary is the audible vintage seam.
  LOUD   acoustics — the integrated loudness steps more than `loud_db`
         (default 3.0 dB LUFS) between adjacent measured turns. Loudness
         is the one acoustic dimension judged automatically: expressive
         prosody legitimately moves pitch, not volume. Pitch and timbre
         are reported, never flagged.
  TRUNC  content — the audio carries well under the syllables the turn's
         text calls for (ratio < 0.6 with at least 8 expected syllables):
         a playback or barge-in cutoff.

The flag logic here is dependency-light on purpose; the acoustic numbers
per turn come from `audio_metrics` (optional `audio-metrics` extra), and
`Traces.audio_drift` wires the two together for the
`cxas trace audio drift` command.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Any

try:
    from google.cloud import logging_v2
except ImportError:  # pragma: no cover - optional, matches cloud_logging.py
    logging_v2 = None

#: Adjacent-turn loudness step that reads as "the volume changed", in dB.
DEFAULT_LOUD_DB = 3.0

#: Audio/text syllable ratio under which a turn counts as truncated.
TRUNC_RATIO = 0.6
TRUNC_MIN_SYLLABLES = 8


def syllables_in_text(text: str) -> int:
    """Crude English syllable count — stable enough for a 0.6 ratio test."""
    total = 0
    for word in re.findall(r"[A-Za-z']+|\d", text or ""):
        w = re.sub(r"[^a-z]", "", word.lower())
        if not w:
            total += 1  # a bare digit is spoken as at least one syllable
            continue
        n = len(re.findall(r"[aeiouy]+", w))
        if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
            n -= 1
        total += max(1, n)
    return total


def fetch_tts_spans(
    project_id: str,
    conversation_id: str,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    credentials: Any = None,
    padding_s: int = 120,
) -> list[dict[str, Any]]:
    """TTS span attributes for a conversation, from Cloud Logging.

    Returns one dict per synthesis/replay:
    `{start, cached, ttfa_ms, audio_ms, speculative}`. `cached` comes from
    the span's own `cache hit` attribute; `ttfa_ms` (time to first audio)
    independently separates the populations (cache replays are a few ms,
    fresh synthesis is hundreds).
    """
    if logging_v2 is None:
        raise ImportError(
            "google-cloud-logging is required for TTS span fetching. "
            "Install with: pip install google-cloud-logging"
        )
    client = logging_v2.Client(project=project_id, credentials=credentials)
    parts = [f'"{conversation_id}"']
    pad = datetime.timedelta(seconds=padding_s)

    def _iso(t: datetime.datetime) -> str:
        return t.isoformat().replace("+00:00", "Z")

    if start_time:
        parts.append(f'timestamp>="{_iso(start_time - pad)}"')
    if end_time:
        parts.append(f'timestamp<="{_iso(end_time + pad)}"')
    spans: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            attrs = obj.get("attributes")
            if isinstance(attrs, dict) and "time to first audio (ms)" in attrs:
                spans.append(
                    {
                        "start": obj.get("startTime", ""),
                        "cached": bool(attrs.get("cache hit")),
                        "ttfa_ms": attrs.get("time to first audio (ms)"),
                        "audio_ms": attrs.get("audio duration (ms)"),
                        "speculative": bool(attrs.get("speculative")),
                    }
                )
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for entry in client.list_entries(
        filter_=" AND ".join(parts), order_by="timestamp asc"
    ):
        payload = getattr(entry, "payload", None)
        if isinstance(payload, dict):
            walk(payload)
    # a span can appear in several entries; keep one per (start, audio_ms)
    seen: set[tuple[str, Any]] = set()
    unique = []
    for s in spans:
        key = (s["start"], s["audio_ms"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return sorted(unique, key=lambda s: s["start"])


def join_spans(
    rows: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    tolerance_s: float = 0.3,
) -> list[dict[str, Any]]:
    """Annotates turn rows with `tts` = cached/fresh by audio-duration match.

    Per-turn recordings and TTS spans share no id, but a single-clip turn's
    recording duration matches its span's `audio duration (ms)` closely. A
    turn built from several clips matches no single span and stays
    un-annotated ("" — honest, rather than guessed).
    """
    for row in rows:
        dur = float(row.get("dur_s") or 0)
        best, best_d = None, tolerance_s
        for s in spans:
            if not s.get("audio_ms"):
                continue
            d = abs(s["audio_ms"] / 1000.0 - dur)
            if d < best_d:
                best, best_d = s, d
        row["tts"] = (
            "" if best is None else ("cached" if best["cached"] else "fresh")
        )
        if best is not None:
            row["ttfa_ms"] = best["ttfa_ms"]
    return rows


def flag_drift(
    rows: list[dict[str, Any]], loud_db: float = DEFAULT_LOUD_DB
) -> list[dict[str, Any]]:
    """Adds `flags` (SEAM / LOUD / TRUNC) per row; returns the flagged rows.

    Expects rows ordered by turn, each carrying what `audio_metrics` and
    `join_spans` produce: `dur_s`, `lufs`, `n_syl`, `tts`, and the turn's
    `agent_text`.
    """
    prev_src: str | None = None
    prev_lufs: float | None = None
    flagged = []
    for row in rows:
        flags = []
        src = row.get("tts") or ""
        if src:
            if prev_src and src != prev_src:
                flags.append("SEAM")
            prev_src = src
        lufs = row.get("lufs")
        if isinstance(lufs, (int, float)) and not math.isnan(lufs):
            if prev_lufs is not None and abs(lufs - prev_lufs) > loud_db:
                flags.append("LOUD")
                row["dlufs"] = round(lufs - prev_lufs, 2)
            prev_lufs = lufs
        expected = syllables_in_text(row.get("agent_text", ""))
        heard = row.get("n_syl") or 0
        if expected >= TRUNC_MIN_SYLLABLES and heard / expected < TRUNC_RATIO:
            flags.append("TRUNC")
        row["flags"] = flags
        if flags:
            flagged.append(row)
    return flagged


def describe(row: dict[str, Any]) -> str:
    """One-line human reason for a flagged row."""
    parts = []
    if "SEAM" in row.get("flags", ()):
        parts.append("vintage seam (cached<->fresh TTS boundary)")
    if "LOUD" in row.get("flags", ()):
        parts.append(
            f"loudness step {row.get('dlufs', 0):+.1f} dB vs previous turn"
        )
    if "TRUNC" in row.get("flags", ()):
        parts.append("audio truncated vs the turn's text")
    return " + ".join(parts)
