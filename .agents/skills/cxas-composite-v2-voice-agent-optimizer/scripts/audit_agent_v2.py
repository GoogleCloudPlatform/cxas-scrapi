#!/usr/bin/env python3
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

"""CXAS Voice Agent Auditor and Migrator for Gemini Composite V2 (gemini-composite-v2).

Audits, inspects, and remediates Google Cloud CX Agent Studio (CXAS) and
Customer Engagement Suite (CES) applications for Gemini Composite V2
(`gemini-composite-v2`):
- Upgrades `app.json` and agent overrides to `gemini-composite-v2` (temperature 1.0)
  and replaces legacy V1 Director's Notes with empty or ~12-word speaker profiles.
- Migrates inline `[tag]` syntax to `<tag>`, remaps low-performing tags to
  validated Composite V2 equivalents, deduplicates tag lists, inserts commas
  before mid-sentence pause tags, relocates mid-sentence style tags in quoted
  utterances to sentence start, and removes closing audio tags.
- Audits tool-scoped spoken text normalization (`Scope:` guard + `/IPA/` notation),
  ALL-CAPS lexical stress, `...` ellipses, contradictory legacy rules,
  `after_model_callback` safety nets, and cross-app hand-offs.
"""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import re
import sys
from typing import Any

# Auto-resolve src/ for cxas_scrapi when executed directly
_current_dir = pathlib.Path(__file__).resolve().parent
for _candidate in [_current_dir, *list(_current_dir.parents)]:
    _src = _candidate / "src"
    if _src.is_dir() and (_src / "cxas_scrapi").is_dir():
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        break
    _sub_src = _candidate / "cxas-scrapi" / "src"
    if _sub_src.is_dir() and (_sub_src / "cxas_scrapi").is_dir():
        if str(_sub_src) not in sys.path:
            sys.path.insert(0, str(_sub_src))
        break

try:
    from cxas_scrapi.utils.linter import (
        Discovery,
        LintConfig,
        LintReport,
        LintResult,
        Severity,
        build_context,
        build_registry,
        run_rules,
    )
except (ImportError, ModuleNotFoundError):
    Discovery = None  # type: ignore[assignment]
    LintReport = None  # type: ignore[assignment]
    LintResult = None  # type: ignore[assignment]
    Severity = None  # type: ignore[assignment]

TARGET_COMPOSITE_MODEL = "gemini-composite-v2"
COMPOSITE_MODEL_PREFIX = "gemini-composite"
DEFAULT_V2_STYLE = "warm, friendly, natural, professional"
MAX_RECOMMENDED_STYLE_WORDS = 25

PAUSE_TAGS: set[str] = {"short pause", "long pause"}

ALLOWED_V2_AUDIO_TAGS: set[str] = {
    # Breathing (6)
    "breath",
    "heavy breath",
    "exhales",
    "pant",
    "sigh",
    "sighs",
    # Laughter (6)
    "laugh",
    "chuckle",
    "chuckles",
    "giggle",
    "snicker",
    "cackle",
    # Reactions (7)
    "gasp",
    "cough",
    "sneeze",
    "throat-clearing",
    "snort",
    "tsk",
    "argh",
    # Sounds & soft delivery (7)
    "sob",
    "cry",
    "whimper",
    "groan",
    "yawn",
    "whispers",
    "whispering",
    # Pauses (2)
    "short pause",
    "long pause",
    # Multi-part sentence-opening tone & cadence tags (11 recommended + 8 supported)
    "calm",
    "empathetic",
    "warm",
    "positive",
    "enthusiasm",
    "clear",
    "determination",
    "interest",
    "relief",
    "fast",
    "faster",
    "sympathetic",
    "tension",
    "anxiety",
    "awe",
    "surprised",
    "fearful",
    "negative",
    "disgusted",
}

# Low-performing or legacy V1 tags automatically remapped during remediation
# ("" deletes the tag).
REMAP_V2: dict[str, tuple[str, str]] = {
    "reassuring": (
        "empathetic",
        "Frequently spoken aloud mid-sentence; <empathetic> is reliable.",
    ),
    "curiosity": (
        "interest",
        "Often ignored or spoken aloud; <interest> performs better.",
    ),
    "professional": (
        "clear",
        "No longer recommended; <clear> produces cleaner articulation.",
    ),
    "cautious": (
        "calm",
        "No longer recommended; <calm> steadies delivery reliably.",
    ),
    "alarm": (
        "calm",
        "Rarely changes delivery; <calm> is reliable.",
    ),
    "nervousness": (
        "calm",
        "No longer recommended; <calm> avoids vocal instability.",
    ),
    "happy": (
        "positive",
        "Changes delivery very little; <positive> is stronger.",
    ),
    "admiration": (
        "positive",
        "Acoustically indistinct from <warm> / <positive>.",
    ),
    "hope": (
        "warm",
        "Often ignored or spoken aloud; <warm> performs well.",
    ),
    "adoration": (
        "warm",
        "Unreliable in evaluations; <warm> performs well.",
    ),
    "crying": (
        "sympathetic",
        "Renders as unstable pitch; <sympathetic> conveys support.",
    ),
    "laughs": (
        "chuckles",
        "<chuckles> is more reliable and warmer on customer support calls.",
    ),
    "annoyance": (
        "determination",
        "No longer recommended; <determination> sounds firm, not irritated.",
    ),
    "aggression": (
        "determination",
        "Changes delivery very little; <determination> conveys resolve.",
    ),
    "medium pause": (
        "short pause",
        "Standardize mid-sentence pauses to <short pause> or <long pause>.",
    ),
    "neutral": (
        "",
        "No longer recommended as an inline tag; leave untagged.",
    ),
    "uhm": (
        "",
        "Bracketed filler tags cause mouth-click artifacts; use '...'.",
    ),
}

ALL_RECOGNIZED_AUDIO_TAGS: set[str] = ALLOWED_V2_AUDIO_TAGS | set(REMAP_V2)

DEFAULT_V2_VOICES: dict[str, str] = {
    "en": "en-US-Chirp3-HD-Zephyr",
    "en-US": "en-US-Chirp3-HD-Zephyr",
    "en-AU": "en-AU-Chirp3-HD-Zephyr",
    "en-GB": "en-GB-Chirp3-HD-Zephyr",
    "en-CA": "en-CA-Chirp3-HD-Zephyr",
    "en-IN": "en-IN-Chirp3-HD-Zephyr",
    "es": "es-US-Chirp3-HD-Zephyr",
    "es-US": "es-US-Chirp3-HD-Zephyr",
    "es-ES": "es-ES-Chirp3-HD-Zephyr",
    "es-MX": "es-US-Chirp3-HD-Zephyr",
    "cmn": "cmn-CN-Chirp3-HD-Zephyr",
    "cmn-CN": "cmn-CN-Chirp3-HD-Zephyr",
    "cmn-TW": "cmn-TW-Chirp3-HD-Zephyr",
    "zh": "cmn-CN-Chirp3-HD-Zephyr",
    "zh-CN": "cmn-CN-Chirp3-HD-Zephyr",
    "zh-TW": "cmn-TW-Chirp3-HD-Zephyr",
    "yue": "yue-CN-Chirp3-HD-Zephyr",
    "yue-CN": "yue-CN-Chirp3-HD-Zephyr",
    "hi": "hi-IN-Chirp3-HD-Zephyr",
    "hi-IN": "hi-IN-Chirp3-HD-Zephyr",
    "ko": "ko-KR-Chirp3-HD-Zephyr",
    "ko-KR": "ko-KR-Chirp3-HD-Zephyr",
    "ja": "ja-JP-Chirp3-HD-Zephyr",
    "ja-JP": "ja-JP-Chirp3-HD-Zephyr",
    "fr": "fr-FR-Chirp3-HD-Zephyr",
    "fr-FR": "fr-FR-Chirp3-HD-Zephyr",
    "fr-CA": "fr-CA-Chirp3-HD-Zephyr",
    "de": "de-DE-Chirp3-HD-Zephyr",
    "de-DE": "de-DE-Chirp3-HD-Zephyr",
    "pt": "pt-BR-Chirp3-HD-Zephyr",
    "pt-BR": "pt-BR-Chirp3-HD-Zephyr",
    "it": "it-IT-Chirp3-HD-Zephyr",
    "it-IT": "it-IT-Chirp3-HD-Zephyr",
}

NORMALIZED_DEFAULT_VOICES: dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in DEFAULT_V2_VOICES.items()
}

PROSODY_RE = re.compile(
    r'^prosody\s+rate\s*=\s*"?(\d{1,3})\s*%"?$', re.IGNORECASE
)

# Regex matching legacy Composite V1 square-bracket tags
LEGACY_SQUARE_TAG_RE = re.compile(
    r"\[\s*(?:"
    r"prosody\s+rate=[^\]]+|"
    r"short\s+pause|medium\s+pause|long\s+pause|"
    r"breath|heavy\s+breath|pant|sighs?|exhales?|"
    r"laugh|laughs|laughing|chuckles?|giggle|snicker|cackle|"
    r"gasp|cough|sneeze|throat-clearing|clears\s+throat|snort|tsk|argh|"
    r"sob|cry|crying|whimper|groan|yawn|uhm|"
    r"whispers?|whispering|slow|slower|fast|faster|extremely\s+fast|"
    r"positive|happy|enthusiasm|amusement|celebratory|excited|excitement|"
    r"relief|neutral|seriousness|serious|cautious|alarm|confusion|"
    r"negative|frustration|anger|empathetic|warm|calm|reassuring|clear|"
    r"professional|curiosity|interest|determination|sympathetic|"
    r"hope|adoration|admiration|nervousness|annoyance|aggression"
    r")\s*\]",
    re.IGNORECASE,
)

# Regex matching stale angle-bracket tags that should be remapped in V2
STALE_ANGLE_TAG_RE = re.compile(
    r"<\s*("
    + "|".join(re.escape(k) for k in sorted(REMAP_V2, key=len, reverse=True))
    + r")\s*>",
    re.IGNORECASE,
)

# Regex matching forbidden closing audio tags (</prosody>, </warm>, etc.)
CLOSING_AUDIO_TAG_RE = re.compile(
    r"[ \t]*</\s*(?:"
    r"prosody|"
    + "|".join(
        re.escape(t)
        for t in sorted(ALL_RECOGNIZED_AUDIO_TAGS, key=len, reverse=True)
    )
    + r")\s*>",
    re.IGNORECASE,
)

# Regex matching contradictory leftover V1 tag instructions
CONTRADICTORY_TAG_RULE_RE = re.compile(
    r"(?:"
    r"wrap\s+tags\s+in\s+square\s+brackets|"
    r"use\s+square[- ]bracket(?:ed)?\s+(?:delivery|audio)\s+tags|"
    r"do\s+not\s+bunch\s+(?:all\s+)?tags\s+at\s+the\s+front"
    r")",
    re.IGNORECASE,
)

# Regex matching ellipsis stacked immediately before a V2 pause tag
ELLIPSIS_PAUSE_COLLISION_RE = re.compile(
    r"(?:\.\.\.|\u2026)\s*<(?:short|long)\s+pause>",
    re.IGNORECASE,
)

# Regex matching an ALL-CAPS word (>=2 letters) immediately after <exhales> or <sigh>
ALL_CAPS_AFTER_VOCAL_TAG_RE = re.compile(r"<(?:exhales|sigh)>\s+([A-Z]{2,})\b")

# Regex matching any V2 angle-bracket audio/tone tag in instructions
V2_ANGLE_TAG_RE = re.compile(
    r"<(?:"
    + "|".join(
        re.escape(t)
        for t in sorted(ALLOWED_V2_AUDIO_TAGS, key=len, reverse=True)
    )
    + r")>",
    re.IGNORECASE,
)

# Control-logic label contexts where [Positive] / [Negative] is NOT an audio tag
ATTRIBUTE_VALUE_RE = re.compile(
    r"<[A-Za-z_][\w:.\-]*\s[^<>\n]*=\s*(?:\"[^\"<>\n]*|'[^'<>\n]*)$"
)
LABEL_CONTEXT_RE = re.compile(
    r"\b(?:intents?|labels?|category|categories|classification|classified"
    r"\s+as|sentiment)\s*(?:is|was|are|=|==|:|of)?\s*$",
    re.IGNORECASE,
)

# Tag list deduplication helpers
TAG_LIST_ITEM_RE = re.compile(r"([`'\"]?)<([^<>\n]{1,40})>\1")
TAG_LIST_SEP_RE = re.compile(r"^\s*(?:,|/|,?\s+(?:or|and))\s*$", re.IGNORECASE)
TAG_LIST_CONJ_RE = re.compile(r"\b(?:or|and)\b", re.IGNORECASE)
TWO_ITEM_COMMA_RE = re.compile(r"^\s*,\s*(?=(?:or|and)\b)", re.IGNORECASE)

# Quoted utterance span and mid-sentence pause / placement helpers
QUOTED_SPAN_RE = re.compile(r'(?<!")"(?!")(?:[^"\n]|\n(?!\s*\n)){0,400}"(?!")')
NORMALIZATION_BLOCK_RE = re.compile(
    r'<instruction\s+id\s*=\s*"text normalization for speech"\s*>'
    r"(.*?)</instruction>",
    re.IGNORECASE | re.DOTALL,
)

# Cross-app hand-off payload targets & CES app resource references
HANDOFF_TARGETS: dict[str, str] = {
    "transferToNga": "another CES app (App2App)",
    "transferToDialogflow": "a Dialogflow CX agent",
    "telephonyTransferCall": "a phone number",
    "liveAgentHandoff": "a live agent",
}
HANDOFF_RE = re.compile(r"\b(" + "|".join(HANDOFF_TARGETS) + r")\b")
APP_RESOURCE_RE = re.compile(
    r"projects/[\w.:\-]+/locations/[\w\-]+/apps/[0-9a-f\-]{36}"
    r"(?:/deployments/[\w\-]+)?"
)


def get_default_v2_voice(locale: str) -> str:
    """Returns the default Chirp3-HD-Zephyr voice identifier for a locale."""
    norm = locale.strip().lower().replace("_", "-")
    if norm in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[norm]
    prefix = norm.split("-")[0]
    if prefix in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[prefix]
    return f"{locale}-Chirp3-HD-Zephyr"


def _has_legacy_v1_directors_note(inst: str) -> bool:
    """Returns True if instruction contains legacy V1 Director's Note headers."""
    lower = inst.lower()
    return (
        "# audio profile" in lower
        or "# the scene" in lower
        or "# director" in lower
        or "## transcript" in lower
    )


def _normalize_tag_inner(inner: str) -> str:
    """Returns canonical spelling of the text inside a tag's brackets."""
    collapsed = " ".join(inner.split())
    match = PROSODY_RE.match(collapsed)
    if match:
        return f'prosody rate="{match.group(1)}%"'
    return collapsed.lower()


def _is_label_context(line_prefix: str) -> bool:
    """Returns True if a bracketed token sits in a control-logic label context."""
    return bool(
        ATTRIBUTE_VALUE_RE.search(line_prefix)
        or LABEL_CONTEXT_RE.search(line_prefix)
    )


def _is_meta_documentation_line(line: str) -> bool:
    """Returns True if `line` documents banned syntax or defines a tag regex."""
    lower = line.lower()
    return (
        "never write" in lower
        or "never use square brackets" in lower
        or "inline_tag" in lower
        or "audio_tag_regex" in lower
        or "legacy_square_tag_re" in lower
    )


def _rebuild_tag_list(
    line: str, items: list[re.Match[str]], drop: list[bool]
) -> str:
    """Rewrites one separator-joined tag list without items flagged in `drop`."""
    texts = [item.group(0) for item in items]
    seps = [
        line[prev.end() : nxt.start()]
        for prev, nxt in itertools.pairwise(items)
    ]
    remove = list(drop)
    i = 0
    while i < len(texts):
        if not remove[i] or len(texts) == 1:
            i += 1
            continue
        if i == len(texts) - 1 or (i > 0 and TAG_LIST_CONJ_RE.search(seps[i])):
            del seps[i - 1]
        else:
            del seps[i]
        del texts[i]
        del remove[i]
    if len(seps) == 1:
        seps[0] = TWO_ITEM_COMMA_RE.sub(" ", seps[0])
    rebuilt = texts[0]
    for sep, item_text in zip(seps, texts[1:], strict=True):
        rebuilt += sep + item_text
    return rebuilt


def _deduplicate_tag_lists_in_line(line: str) -> str:
    """Removes duplicate tags inside comma/conjunction-joined tag lists."""
    groups: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []
    for match in TAG_LIST_ITEM_RE.finditer(line):
        inner = _normalize_tag_inner(match.group(2))
        if inner not in ALL_RECOGNIZED_AUDIO_TAGS:
            continue
        if current and TAG_LIST_SEP_RE.match(
            line[current[-1].end() : match.start()]
        ):
            current.append(match)
            continue
        if len(current) > 1:
            groups.append(current)
        current = [match]
    if len(current) > 1:
        groups.append(current)

    if not groups:
        return line

    pieces: list[str] = []
    cursor = 0
    for items in groups:
        seen: set[str] = set()
        drop = [False] * len(items)
        for idx, item in enumerate(items):
            inner = _normalize_tag_inner(item.group(2))
            if inner in seen:
                drop[idx] = True
            else:
                seen.add(inner)
        if not any(drop):
            continue
        pieces.append(line[cursor : items[0].start()])
        pieces.append(_rebuild_tag_list(line, items, drop))
        cursor = items[-1].end()
    pieces.append(line[cursor:])
    return "".join(pieces)


def _relocate_utterance_tags_and_punctuate_pauses(
    utterance: str,
) -> tuple[str, int, int]:
    """Ensures mid-sentence pause punctuation and sentence-start style tags.

    Args:
        utterance: A double-quoted utterance span (including surrounding quotes).

    Returns:
        Tuple of (rewritten_utterance, pause_commas_added, style_tags_moved).
    """
    if len(utterance) < 4 or "<" not in utterance:
        return utterance, 0, 0

    inner = utterance[1:-1]
    pause_commas = 0
    style_moves = 0

    # 1. Insert comma before mid-sentence <short pause> / <long pause> when
    # preceded directly by an alphanumeric character without punctuation.
    def _punct_pause(m: re.Match[str]) -> str:
        nonlocal pause_commas
        pause_commas += 1
        return f"{m.group(1)}, {m.group(2)}"

    inner = re.sub(
        r"([A-Za-z0-9])[ \t]+(<(?:short|long)\s+pause>)",
        _punct_pause,
        inner,
        flags=re.IGNORECASE,
    )

    # 2. Move mid-sentence style/tone tags to the start of their sentence.
    sentences = re.split(r"([.!?]+(?:\s+|$))", inner)
    rebuilt_parts: list[str] = []
    for idx in range(0, len(sentences), 2):
        sent = sentences[idx]
        delim = sentences[idx + 1] if idx + 1 < len(sentences) else ""
        if "<" not in sent:
            rebuilt_parts.append(sent + delim)
            continue

        leading_ws_match = re.match(r"^(\s*(?:<[a-zA-Z][^>]*>\s*)*)", sent)
        leading_len = leading_ws_match.end() if leading_ws_match else 0
        leading_part = sent[:leading_len]
        rest_part = sent[leading_len:]

        existing_leading = {
            _normalize_tag_inner(m.group(1))
            for m in re.finditer(r"<([^>]+)>", leading_part)
        }
        lifted_tags: list[str] = []

        def _lift_style_tag(
            m: re.Match[str],
            existing_set: set[str] = existing_leading,
            lifted_list: list[str] = lifted_tags,
        ) -> str:
            nonlocal style_moves
            tag_name = _normalize_tag_inner(m.group(1))
            if tag_name not in ALLOWED_V2_AUDIO_TAGS or tag_name in PAUSE_TAGS:
                return m.group(0)
            style_moves += 1
            if tag_name not in existing_set:
                existing_set.add(tag_name)
                lifted_list.append(f"<{tag_name}>")
            return ""

        new_rest = re.sub(r"<([^>]+)>", _lift_style_tag, rest_part)
        if lifted_tags:
            new_rest = re.sub(r"[ \t]{2,}", " ", new_rest)
            new_rest = re.sub(r"[ \t]+([,.;:!?])", r"\1", new_rest).lstrip()
            prefix = leading_part.rstrip()
            if prefix:
                prefix += " "
            sent = prefix + " ".join(lifted_tags) + " " + new_rest
        rebuilt_parts.append(sent + delim)

    return f'"{"".join(rebuilt_parts)}"', pause_commas, style_moves


def migrate_text_to_v2(text: str) -> tuple[str, list[str]]:
    """Migrates `[tag]` -> `<tag>`, remaps stale tags, and fixes placement."""
    notes: list[str] = []
    syntax_count = 0
    remap_count = 0
    closing_count = 0

    lines_out: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        if _is_meta_documentation_line(raw_line):
            lines_out.append(raw_line)
            continue

        # 1. Strip forbidden closing audio tags (</prosody>, </warm>, etc.)
        def _strip_closing(m: re.Match[str]) -> str:
            nonlocal closing_count
            closing_count += 1
            return ""

        cur_line = CLOSING_AUDIO_TAG_RE.sub(_strip_closing, raw_line)

        # 2. Convert [tag] -> <tag> (skipping markdown links, ALL-CAPS
        # placeholders, and control-logic labels).
        cursor = 0
        pieces: list[str] = []
        for match in LEGACY_SQUARE_TAG_RE.finditer(cur_line):
            if cur_line[match.end() : match.end() + 1] == "(":
                continue
            raw_inner = match.group(0)[1:-1]
            if raw_inner.isupper():
                continue
            if _is_label_context(cur_line[: match.start()]):
                continue
            norm_inner = _normalize_tag_inner(raw_inner)
            pieces.append(cur_line[cursor : match.start()])
            if norm_inner in REMAP_V2:
                target, _ = REMAP_V2[norm_inner]
                remap_count += 1
                if target:
                    pieces.append(f"<{target}>")
                elif cur_line[match.end() : match.end() + 1] == " ":
                    cursor = match.end() + 1
                    continue
            else:
                syntax_count += 1
                pieces.append(f"<{norm_inner}>")
            cursor = match.end()
        pieces.append(cur_line[cursor:])
        cur_line = "".join(pieces)

        # 3. Remap stale angle-bracket tags (<reassuring> -> <empathetic>, etc.)
        cursor = 0
        pieces = []
        for match in STALE_ANGLE_TAG_RE.finditer(cur_line):
            norm_inner = _normalize_tag_inner(match.group(1))
            target, _ = REMAP_V2[norm_inner]
            remap_count += 1
            pieces.append(cur_line[cursor : match.start()])
            if target:
                pieces.append(f"<{target}>")
            elif cur_line[match.end() : match.end() + 1] == " ":
                cursor = match.end() + 1
                continue
            cursor = match.end()
        pieces.append(cur_line[cursor:])
        cur_line = "".join(pieces)

        # 4. Deduplicate tag lists on the line
        cur_line = _deduplicate_tag_lists_in_line(cur_line)
        lines_out.append(cur_line)

    migrated = "".join(lines_out)

    # 5. Inside quoted utterances, insert commas before mid-sentence pause tags
    # and move mid-sentence style tags to the start of their sentence.
    pause_commas_total = 0
    style_moves_total = 0

    def _rewrite_utterance(m: re.Match[str]) -> str:
        nonlocal pause_commas_total, style_moves_total
        rewritten, pc, sm = _relocate_utterance_tags_and_punctuate_pauses(
            m.group(0)
        )
        pause_commas_total += pc
        style_moves_total += sm
        return rewritten

    migrated = QUOTED_SPAN_RE.sub(_rewrite_utterance, migrated)

    if syntax_count:
        notes.append(f"converted {syntax_count} [tag] -> <tag>")
    if remap_count:
        notes.append(f"remapped {remap_count} low-performing tag(s)")
    if closing_count:
        notes.append(f"removed {closing_count} closing audio tag(s)")
    if pause_commas_total:
        notes.append(
            f"inserted {pause_commas_total} comma(s) before mid-sentence pause tag(s)"
        )
    if style_moves_total:
        notes.append(
            f"moved {style_moves_total} mid-sentence style tag(s) to sentence start"
        )
    return migrated, notes


class VoiceAgentV2Auditor:
    """Audits and remediates CXAS voice configurations for Gemini Composite V2."""

    def __init__(self, workspace_dir: pathlib.Path | str = "."):
        self.workspace_path = pathlib.Path(workspace_dir).resolve()
        self.app_json_path = self._find_app_json()
        self.app_root = (
            self.app_json_path.parent
            if self.app_json_path.is_file()
            else self.workspace_path
        )

    def _find_app_json(self) -> pathlib.Path:
        direct = self.workspace_path / "app.json"
        if direct.is_file():
            return direct
        for p in sorted(self.workspace_path.glob("**/app.json")):
            if p.is_file() and not any(
                part.startswith(".") or part == "build"
                for part in p.relative_to(self.workspace_path).parts
            ):
                return p
        return direct

    def _read_app_json(self) -> dict[str, Any]:
        if not self.app_json_path.is_file():
            return {}
        try:
            return json.loads(self.app_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_app_json(self, data: dict[str, Any]) -> None:
        self.app_json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _instruction_files(self) -> list[pathlib.Path]:
        files: list[pathlib.Path] = []
        global_inst = self.app_root / "global_instruction.txt"
        if global_inst.is_file():
            files.append(global_inst)
        for p in sorted(self.app_root.glob("agents/*/instruction.txt")):
            if p.is_file():
                files.append(p)
        return files

    def _agent_json_files(self) -> list[pathlib.Path]:
        return [
            p
            for p in sorted(self.app_root.glob("agents/*/*.json"))
            if p.is_file()
        ]

    def _tool_and_callback_files(self) -> list[pathlib.Path]:
        files: list[pathlib.Path] = []
        for pattern in (
            "tools/*/python_function/python_code.py",
            "tools/*/*.json",
            "agents/*/*_callbacks/*/*.py",
        ):
            for p in sorted(self.app_root.glob(pattern)):
                if p.is_file():
                    files.append(p)
        return files

    def find_handoffs(self) -> list[dict[str, Any]]:
        """Finds cross-app and external call hand-offs across the app."""
        handoffs: list[dict[str, Any]] = []
        seen_resources: set[str] = set()
        candidates: list[pathlib.Path] = []
        if self.app_json_path.is_file():
            candidates.append(self.app_json_path)
        candidates.extend(self._instruction_files())
        candidates.extend(self._agent_json_files())
        candidates.extend(self._tool_and_callback_files())

        for path in candidates:
            rel = str(path.relative_to(self.app_root))
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in HANDOFF_RE.finditer(line):
                    key = match.group(1)
                    handoffs.append(
                        {
                            "type": "handoff",
                            "target_kind": key,
                            "file": rel,
                            "line": lineno,
                            "message": (
                                f"Hands the call to {HANDOFF_TARGETS[key]}. "
                                "The destination runs its own session with its own "
                                "voice and TTS model, not this app's."
                            ),
                        }
                    )
                for match in APP_RESOURCE_RE.finditer(line):
                    resource = match.group(0)
                    if resource in seen_resources:
                        continue
                    seen_resources.add(resource)
                    handoffs.append(
                        {
                            "type": "app_reference",
                            "target_kind": resource,
                            "file": rel,
                            "line": lineno,
                            "message": (
                                f"References external CES app '{resource}'. "
                                "Check whether that destination app needs its own "
                                "Composite V2 migration."
                            ),
                        }
                    )
        return handoffs

    def audit_app_speech_style_v2(
        self, app_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Audits app.json and agent overrides for gemini-composite-v2 and V2 styles."""
        issues: list[dict[str, Any]] = []
        rel_app = str(self.app_json_path.relative_to(self.app_root))

        model_settings = app_data.get("modelSettings") or {}
        current_model = model_settings.get("model")
        if current_model != TARGET_COMPOSITE_MODEL:
            issues.append(
                {
                    "code": "MODEL_NOT_COMPOSITE_V2",
                    "file": rel_app,
                    "priority": "P0",
                    "message": (
                        f"modelSettings.model is '{current_model}'; "
                        f"must be '{TARGET_COMPOSITE_MODEL}' for Gemini Composite V2."
                    ),
                    "recommended": f"Set modelSettings.model to '{TARGET_COMPOSITE_MODEL}'.",
                }
            )
        if model_settings.get("temperature") != 1.0:
            issues.append(
                {
                    "code": "TEMPERATURE_NOT_1_0",
                    "file": rel_app,
                    "priority": "P0",
                    "message": (
                        f"modelSettings.temperature is {model_settings.get('temperature')}; "
                        "must be 1.0 for natural prosody in Gemini Composite V2."
                    ),
                    "recommended": "Set modelSettings.temperature to 1.0.",
                }
            )

        # Check agent-level modelSettings overrides
        for agent_json in self._agent_json_files():
            rel_agent = str(agent_json.relative_to(self.app_root))
            try:
                agent_data = json.loads(agent_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            agent_model = (agent_data.get("modelSettings") or {}).get("model")
            if agent_model and agent_model != TARGET_COMPOSITE_MODEL:
                issues.append(
                    {
                        "code": "AGENT_MODEL_NOT_COMPOSITE_V2",
                        "file": rel_agent,
                        "priority": "P0",
                        "message": (
                            f"Agent config {rel_agent} overrides modelSettings.model "
                            f"with '{agent_model}'; must be '{TARGET_COMPOSITE_MODEL}'."
                        ),
                        "recommended": (
                            f"Set modelSettings.model in {rel_agent} to "
                            f"'{TARGET_COMPOSITE_MODEL}'."
                        ),
                    }
                )

        lang_settings = app_data.get("languageSettings") or {}
        default_lang = lang_settings.get("defaultLanguageCode") or "en-US"
        supported = lang_settings.get("supportedLanguageCodes") or []
        declared_langs = [
            default_lang,
            *[lang for lang in supported if isinstance(lang, str)],
        ]

        audio_cfg = app_data.get("audioProcessingConfig") or {}
        speech_cfgs = audio_cfg.get("synthesizeSpeechConfigs") or {}
        custom_voice_names = {
            str(s.get("name")).strip()
            for s in (audio_cfg.get("customVoiceSamples") or [])
            if isinstance(s, dict) and s.get("name")
        }
        if not speech_cfgs:
            issues.append(
                {
                    "code": "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
                    "file": rel_app,
                    "priority": "P0",
                    "message": "audioProcessingConfig.synthesizeSpeechConfigs is missing or empty.",
                    "recommended": (
                        f"Configure synthesizeSpeechConfigs for {declared_langs} with "
                        "an empty style, a ~12-word speaker profile, or "
                        f"instruction='{DEFAULT_V2_STYLE}'."
                    ),
                }
            )
            return {"passed": False, "issues": issues}

        for lang in declared_langs:
            cfg = speech_cfgs.get(lang)
            if not isinstance(cfg, dict):
                issues.append(
                    {
                        "code": "MISSING_LOCALE_SPEECH_CONFIG",
                        "file": rel_app,
                        "priority": "P0",
                        "message": f"Declared language '{lang}' is missing from synthesizeSpeechConfigs.",
                        "recommended": (
                            f"Add '{lang}' with voice='{get_default_v2_voice(lang)}', "
                            f"speakingRate=1.0, instruction='{DEFAULT_V2_STYLE}' (or ~12-word speaker profile)."
                        ),
                    }
                )
                continue

            inst = str(cfg.get("instruction") or "").strip()
            if _has_legacy_v1_directors_note(inst):
                issues.append(
                    {
                        "code": "LEGACY_V1_DIRECTORS_NOTE_IN_V2_STYLE",
                        "file": rel_app,
                        "priority": "P0",
                        "message": (
                            f"Locale '{lang}' uses a legacy V1 '# Audio Profile' / '# The Scene' / "
                            "'# Director's note' in synthesizeSpeechConfigs. Long V1 Director's Notes "
                            "make the voice drift over a call and conflict with inline tags in Composite V2."
                        ),
                        "recommended": (
                            f"Remove the V1 Director's Note for '{lang}'. Leave the style empty (''), "
                            "replace with a ~12-word speaker description (age, vocal timbre, pitch, "
                            f"accent, cadence), or use '{DEFAULT_V2_STYLE}'."
                        ),
                    }
                )
            elif len(inst.split()) > MAX_RECOMMENDED_STYLE_WORDS:
                issues.append(
                    {
                        "code": "SPEAKING_STYLE_TOO_LONG",
                        "file": rel_app,
                        "priority": "P1",
                        "message": (
                            f"Locale '{lang}' has a {len(inst.split())}-word speaking style instruction. "
                            "Composite V2 recommends a short description of ~12 words (<=25 words) "
                            "describing who the speaker is, or an empty speaking style."
                        ),
                        "recommended": (
                            f"Shorten '{lang}' instruction to ~12 words focused on speaker age, timbre, "
                            "pitch, regional accent, and baseline cadence, or leave empty."
                        ),
                    }
                )

            voice = str(cfg.get("voice") or "").strip()
            if not voice:
                issues.append(
                    {
                        "code": "MISSING_VOICE_ID",
                        "file": rel_app,
                        "priority": "P0",
                        "message": f"Locale '{lang}' is missing a voice identifier in synthesizeSpeechConfigs.",
                        "recommended": f"Set '{lang}' voice to '{get_default_v2_voice(lang)}' (or your custom voice key).",
                    }
                )
            elif "Chirp3-HD" not in voice and voice not in custom_voice_names:
                issues.append(
                    {
                        "code": "NON_CHIRP3_HD_VOICE",
                        "file": rel_app,
                        "priority": "P1",
                        "message": f"Locale '{lang}' uses voice='{voice}'; expected a *-Chirp3-HD-* or customVoiceSamples voice.",
                        "recommended": f"Set '{lang}' voice to '{get_default_v2_voice(lang)}' (or a customVoiceSamples name).",
                    }
                )

        return {"passed": len(issues) == 0, "issues": issues}

    def audit_audio_tags_v2(self) -> dict[str, Any]:
        """Audits instructions, tools, and callbacks for Composite V2 tag compliance."""
        issues: list[dict[str, Any]] = []
        inst_files = self._instruction_files()
        combined_inst = "\n".join(
            p.read_text(encoding="utf-8") for p in inst_files if p.is_file()
        )

        if inst_files and not V2_ANGLE_TAG_RE.search(combined_inst):
            issues.append(
                {
                    "code": "MISSING_V2_AUDIO_TAG_GUIDANCE",
                    "file": "global_instruction.txt",
                    "priority": "P1",
                    "message": (
                        "Instructions do not define or reference any Composite V2 angle-bracket "
                        "non-verbal sound/pause tags (<breath>, <exhales>, <sigh>, <short pause>, "
                        "<long pause>) or sentence-opening tone transition tags (<calm>, <empathetic>, "
                        "<warm>, <positive>, <enthusiasm>)."
                    ),
                    "recommended": (
                        'Add <instruction id="audio tag format"> to global_instruction.txt '
                        "defining Composite V2 angle-bracket sound, pause, and tone tags."
                    ),
                }
            )

        for path in [*inst_files, *self._tool_and_callback_files()]:
            rel = str(path.relative_to(self.app_root))
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                lower_line = line.lower()
                if _is_meta_documentation_line(line):
                    continue

                for match in LEGACY_SQUARE_TAG_RE.finditer(line):
                    if line[match.end() : match.end() + 1] == "(":
                        continue
                    if match.group(0)[1:-1].isupper():
                        continue
                    if _is_label_context(line[: match.start()]):
                        continue
                    issues.append(
                        {
                            "code": "LEGACY_V1_SQUARE_BRACKET_TAG",
                            "file": rel,
                            "line": lineno,
                            "priority": "P0",
                            "message": (
                                f"Found legacy Composite V1 square-bracket tag '{match.group(0)}' "
                                f"in {rel}:{lineno}. Composite V2 requires angle-bracket (<...>) tags."
                            ),
                            "recommended": (
                                "Run `audit_agent_v2.py --remediate` or replace with a Composite V2 "
                                "angle-bracket tag (<breath>, <exhales>, <sigh>, <chuckles>, <gasp>, "
                                "<short pause>, <long pause>, <calm>, <empathetic>, <warm>, <positive>)."
                            ),
                        }
                    )

                for match in STALE_ANGLE_TAG_RE.finditer(line):
                    raw_tag = _normalize_tag_inner(match.group(1))
                    target, reason = REMAP_V2[raw_tag]
                    replacement_desc = (
                        f"<{target}>" if target else "removing the tag"
                    )
                    issues.append(
                        {
                            "code": "STALE_V2_AUDIO_TAG",
                            "file": rel,
                            "line": lineno,
                            "priority": "P1",
                            "message": (
                                f"Found low-performing tag '{match.group(0)}' in {rel}:{lineno}. "
                                f"{reason}"
                            ),
                            "recommended": (
                                f"Run `audit_agent_v2.py --remediate` to replace '{match.group(0)}' "
                                f"with {replacement_desc}."
                            ),
                        }
                    )

                if "never" not in lower_line:
                    for match in CLOSING_AUDIO_TAG_RE.finditer(line):
                        issues.append(
                            {
                                "code": "CLOSING_AUDIO_TAG_FORBIDDEN",
                                "file": rel,
                                "line": lineno,
                                "priority": "P0",
                                "message": (
                                    f"Found closing audio tag '{match.group(0)}' in {rel}:{lineno}. "
                                    "Composite V2 inline tags are point markers and must never be closed."
                                ),
                                "recommended": (
                                    f"Remove '{match.group(0)}'; open a new tone tag when delivery "
                                    "shifts instead of closing the previous tag."
                                ),
                            }
                        )

                if CONTRADICTORY_TAG_RULE_RE.search(line):
                    issues.append(
                        {
                            "code": "CONTRADICTORY_LEGACY_TAG_RULE",
                            "file": rel,
                            "line": lineno,
                            "priority": "P1",
                            "message": (
                                f"Found contradictory V1 tag instruction in {rel}:{lineno} "
                                "that conflicts with Composite V2 angle-bracket sentence-start tags."
                            ),
                            "recommended": (
                                "Rewrite the rule to specify angle-bracket tags (<...>) placed "
                                "at the start of a sentence (stacking allowed there) and pause "
                                "tags after punctuation."
                            ),
                        }
                    )

                if (
                    "never stack" not in lower_line
                    and "avoiding" not in lower_line
                    and "rather than after" not in lower_line
                    and ELLIPSIS_PAUSE_COLLISION_RE.search(line)
                ):
                    issues.append(
                        {
                            "code": "ELLIPSIS_PAUSE_TAG_COLLISION",
                            "file": rel,
                            "line": lineno,
                            "priority": "P1",
                            "message": (
                                f"Found ellipsis stacked immediately before pause tag in "
                                f"{rel}:{lineno}. This causes an unnatural ~900ms double-stutter."
                            ),
                            "recommended": (
                                "Use a comma or full stop before <short pause> "
                                "(', <short pause>' or '. <short pause>'), never '... <short pause>'."
                            ),
                        }
                    )

                if "never" not in lower_line:
                    cap_match = ALL_CAPS_AFTER_VOCAL_TAG_RE.search(line)
                    if cap_match and cap_match.group(1) not in {
                        "SIM",
                        "OK",
                        "ID",
                        "TV",
                        "UK",
                        "US",
                        "AI",
                    }:
                        issues.append(
                            {
                                "code": "ALL_CAPS_IMMEDIATELY_AFTER_VOCAL_TAG",
                                "file": rel,
                                "line": lineno,
                                "priority": "P1",
                                "message": (
                                    f"Found ALL-CAPS stress word '{cap_match.group(1)}' "
                                    f"immediately after <exhales>/<sigh> in {rel}:{lineno}, "
                                    "which causes a barked opening syllable in Composite V2."
                                ),
                                "recommended": (
                                    "Keep the first word after <exhales> or <sigh> in normal "
                                    "case and place the ALL-CAPS stress word later in the clause."
                                ),
                            }
                        )

        return {"passed": len(issues) == 0, "issues": issues}

    def audit_text_normalization_and_pacing_v2(self) -> dict[str, Any]:
        """Audits tool-scoped text normalization (/IPA/, Scope: guard) and ALL-CAPS stress."""
        issues: list[dict[str, Any]] = []
        inst_files = self._instruction_files()
        if not inst_files:
            return {"passed": True, "issues": []}

        combined_inst = "\n".join(
            p.read_text(encoding="utf-8") for p in inst_files if p.is_file()
        )
        lower_inst = combined_inst.lower()

        norm_match = NORMALIZATION_BLOCK_RE.search(combined_inst)
        if not norm_match:
            issues.append(
                {
                    "code": "MISSING_TEXT_NORMALIZATION_BLOCK",
                    "file": "global_instruction.txt",
                    "priority": "P1",
                    "message": (
                        "Instructions are missing '<instruction id=\"text normalization for speech\">'."
                    ),
                    "recommended": (
                        'Add <instruction id="text normalization for speech"> with a '
                        "spoken-only Scope: guard, currency/date/ID examples, and /IPA/ notation."
                    ),
                }
            )
        elif not re.search(r"\btool", norm_match.group(1), re.IGNORECASE):
            issues.append(
                {
                    "code": "UNSCOPED_TEXT_NORMALIZATION",
                    "file": "global_instruction.txt",
                    "priority": "P1",
                    "message": (
                        '<instruction id="text normalization for speech"> does not explicitly '
                        "scope normalization to spoken replies only (excluding tool calls/arguments)."
                    ),
                    "recommended": (
                        'Add the `Scope:` paragraph inside `<instruction id="text normalization '
                        'for speech">` stating that normalization applies ONLY to words spoken '
                        'to the user and NEVER to tool calls or tool arguments (pass "1234", '
                        'not "one two three four").'
                    ),
                }
            )

        if (
            "capital" not in lower_inst
            and "all caps" not in lower_inst
            and "all-caps" not in lower_inst
        ):
            issues.append(
                {
                    "code": "MISSING_ALL_CAPS_LEXICAL_STRESS_RULE",
                    "file": "global_instruction.txt",
                    "priority": "P1",
                    "message": (
                        "Instructions do not guide the model to put 1-2 key words per turn "
                        "in CAPITAL letters (ALL CAPS) for natural Composite V2 lexical stress."
                    ),
                    "recommended": (
                        "Instruct the model to put CAPITAL letters on 1-2 key words per turn."
                    ),
                }
            )

        return {"passed": len(issues) == 0, "issues": issues}

    def audit_structural_rules(self) -> dict[str, Any]:
        """Runs cxas_scrapi structural lint rules (I015, V104, A010, T014)."""
        issues: list[dict[str, Any]] = []
        if Discovery is not None:
            discovery = Discovery(
                app_dir=self.app_root,
                evals_dir=self.app_root / "evals",
            )
            config = LintConfig.load(self.app_root)
            context = build_context(
                project_root=self.app_root,
                config=config,
                discovery=discovery,
                model_override="gemini-composite-v1",
            )
            registry = build_registry()
            report = LintReport()
            run_rules(
                registry=registry,
                config=config,
                context=context,
                discovery=discovery,
                report=report,
                specific_rules={"A010", "I015", "V104", "T014"},
            )
            for r in report.results:
                issues.append(
                    {
                        "code": r.rule_id,
                        "file": r.file,
                        "line": r.line,
                        "priority": "P0"
                        if r.rule_id in {"A010", "I015", "V104"}
                        else "P2",
                        "message": r.message,
                        "recommended": r.fix_suggestion,
                    }
                )

        return {"passed": len(issues) == 0, "issues": issues}

    def audit_after_model_callback_v2(self) -> dict[str, Any]:
        """Checks after_model_callbacks for Composite V2 angle-bracket tag handling."""
        issues: list[dict[str, Any]] = []
        cb_files = sorted(
            self.app_root.glob("agents/*/after_model_callbacks/*/*.py")
        )
        for cb in cb_files:
            rel = str(cb.relative_to(self.app_root))
            text = cb.read_text(encoding="utf-8")
            if "INLINE_TAG" in text and (
                "short pause" not in text
                or "empathetic" not in text
                or "breath" not in text
            ):
                issues.append(
                    {
                        "code": "CALLBACK_MISSING_V2_TAG_REGEX",
                        "file": rel,
                        "priority": "P2",
                        "message": (
                            f"{rel} defines INLINE_TAG without the full Composite V2 "
                            "angle-bracket sound, pause, and multi-part tone tag set "
                            "(e.g., <breath>, <chuckle>, <calm>, <empathetic>, <warm>, <positive>)."
                        ),
                        "recommended": (
                            "Update INLINE_TAG to preserve all Composite V2 angle-bracket "
                            "sound, pause, and tone tags."
                        ),
                    }
                )
        return {"passed": len(issues) == 0, "issues": issues}

    def audit(self) -> dict[str, Any]:
        """Runs all Composite V2 audit passes and collects cross-app hand-offs."""
        app_data = self._read_app_json()
        passes = {
            "app_speech_style_v2": self.audit_app_speech_style_v2(app_data),
            "audio_tags_v2": self.audit_audio_tags_v2(),
            "text_normalization_and_pacing_v2": (
                self.audit_text_normalization_and_pacing_v2()
            ),
            "structural_rules": self.audit_structural_rules(),
            "after_model_callback_v2": self.audit_after_model_callback_v2(),
        }
        total_issues = sum(len(p["issues"]) for p in passes.values())
        return {
            "workspace": str(self.app_root),
            "app_json": str(self.app_json_path),
            "overall_status": "PASSED" if total_issues == 0 else "FAILED",
            "total_issues": total_issues,
            "passes": passes,
            "handoffs": self.find_handoffs(),
        }

    def to_lint_report(self) -> Any:
        """Converts Composite V2 audit issues to a cxas-scrapi standard LintReport."""
        audit_res = self.audit()
        if LintReport is None or LintResult is None:
            return audit_res

        report = LintReport()
        for pass_data in audit_res.get("passes", {}).values():
            for issue in pass_data.get("issues", []):
                priority = issue.get("priority", "P1")
                sev = (
                    getattr(Severity, "ERROR", "error")
                    if priority in ("P0", "P1")
                    else getattr(Severity, "WARNING", "warning")
                )
                report.add(
                    LintResult(
                        file=issue.get("file", str(self.app_json_path)),
                        rule_id=issue.get("code", "VOICE_V2_RULE"),
                        severity=sev,
                        message=issue.get("message", ""),
                        line=issue.get("line"),
                        fix_suggestion=issue.get("recommended", ""),
                    )
                )
        return report

    def generate_prioritized_report(self) -> dict[str, Any]:
        """Generates a P0/P1/P2 Markdown and JSON readiness report for Composite V2."""
        audit_res = self.audit()
        p0: list[dict[str, Any]] = []
        p1: list[dict[str, Any]] = []
        p2: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = audit_res.get("handoffs", [])

        for pass_name, pass_data in audit_res["passes"].items():
            for issue in pass_data["issues"]:
                item = {
                    "pass": pass_name,
                    "code": issue["code"],
                    "file": issue.get("file"),
                    "line": issue.get("line"),
                    "message": issue["message"],
                    "recommended": issue.get("recommended"),
                    "priority": issue.get("priority", "P1"),
                }
                if item["priority"] == "P0":
                    p0.append(item)
                elif item["priority"] == "P1":
                    p1.append(item)
                else:
                    p2.append(item)

        lines = [
            "# CXAS Gemini Composite V2 (`gemini-composite-v2`) Voice Optimization Report\n",
            f"**Workspace:** `{self.app_root}`",
            (
                f"**Overall Compliance Status:** `{audit_res['overall_status']}` "
                f"({audit_res['total_issues']} Issues Detected)\n"
            ),
            "### Summary of Prioritized Checks",
            f"- 🔴 **Priority P0 (Critical Composite V2 Synthesis & Migration Blockers):** {len(p0)}",
            f"- 🟡 **Priority P1 (Speaking Style, Tag Placement, Multi-Part Tone & Normalization):** {len(p1)}",
            f"- 🟢 **Priority P2 (Callbacks & Tool Pacing Hygiene):** {len(p2)}",
            f"- ℹ️ **Cross-App & External Hand-Offs (Informational):** {len(handoffs)}\n",
            "---",
        ]

        for label, group in (
            ("🔴 Priority P0: Critical Composite V2 Blockers", p0),
            (
                "🟡 Priority P1: Speaking Style, Tag Placement & Normalization",
                p1,
            ),
            ("🟢 Priority P2: Callbacks & Tool Hygiene", p2),
        ):
            if group:
                lines.append(f"\n## {label}\n")
                for idx, item in enumerate(group, start=1):
                    loc = (
                        f"{item['file']}:{item['line']}"
                        if item.get("line")
                        else item.get("file")
                    )
                    lines.append(
                        f"### {item['priority']}.{idx} `{item['code']}` ({loc})"
                    )
                    lines.append(f"- **Issue:** {item['message']}")
                    if item.get("recommended"):
                        lines.append(
                            f"- **Recommended Fix:** {item['recommended']}"
                        )
                    lines.append("")

        if handoffs:
            lines.append(
                "\n## ℹ️ Cross-App & External Hand-Offs (Informational)\n"
            )
            for h in handoffs:
                loc = f"{h['file']}:{h['line']}"
                lines.append(
                    f"- **`{h['target_kind']}`** (`{loc}`): {h['message']}"
                )
            lines.append("")

        if not (p0 or p1 or p2):
            lines.append(
                "\n🎉 **No issues detected! Agent is 100% compliant with "
                "Gemini Composite V2 (`gemini-composite-v2`) best practices.**"
            )

        return {
            "workspace": str(self.app_root),
            "overall_status": audit_res["overall_status"],
            "total_issues": audit_res["total_issues"],
            "priority_counts": {"P0": len(p0), "P1": len(p1), "P2": len(p2)},
            "p0_issues": p0,
            "p1_issues": p1,
            "p2_issues": p2,
            "handoffs": handoffs,
            "markdown_report": "\n".join(lines),
        }

    def remediate(self) -> dict[str, Any]:
        """Applies automated Composite V2 remediation to app.json, agents, and prompts."""
        changes: list[str] = []
        app_data = self._read_app_json()
        if app_data:
            app_changed = False
            model_settings = app_data.setdefault("modelSettings", {})
            if model_settings.get("model") != TARGET_COMPOSITE_MODEL:
                model_settings["model"] = TARGET_COMPOSITE_MODEL
                changes.append(
                    f"Set modelSettings.model to '{TARGET_COMPOSITE_MODEL}'."
                )
                app_changed = True
            if model_settings.get("temperature") != 1.0:
                model_settings["temperature"] = 1.0
                changes.append("Set modelSettings.temperature to 1.0.")
                app_changed = True

            lang_settings = app_data.setdefault("languageSettings", {})
            default_lang = lang_settings.get("defaultLanguageCode") or "en-US"
            lang_settings["defaultLanguageCode"] = default_lang
            supported = lang_settings.get("supportedLanguageCodes") or []
            declared_langs = [
                default_lang,
                *[lang for lang in supported if isinstance(lang, str)],
            ]

            audio_cfg = app_data.setdefault("audioProcessingConfig", {})
            speech_cfgs = audio_cfg.setdefault("synthesizeSpeechConfigs", {})
            custom_voice_names = {
                str(s.get("name")).strip()
                for s in (audio_cfg.get("customVoiceSamples") or [])
                if isinstance(s, dict) and s.get("name")
            }
            for lang in declared_langs:
                cfg = speech_cfgs.setdefault(lang, {})
                voice = str(cfg.get("voice") or "").strip()
                if not voice or (
                    "Chirp3-HD" not in voice and voice not in custom_voice_names
                ):
                    cfg["voice"] = get_default_v2_voice(lang)
                    changes.append(
                        f"Set Composite V2 voice '{cfg['voice']}' for '{lang}'."
                    )
                    app_changed = True
                if cfg.get("speakingRate") != 1.0:
                    cfg["speakingRate"] = 1.0
                    changes.append(f"Set speakingRate=1.0 for '{lang}'.")
                    app_changed = True
                inst = cfg.get("instruction")
                if (
                    inst is None
                    or _has_legacy_v1_directors_note(str(inst))
                    or len(str(inst).split()) > MAX_RECOMMENDED_STYLE_WORDS
                ):
                    cfg["instruction"] = DEFAULT_V2_STYLE
                    changes.append(
                        f"Replaced legacy/long instruction with Composite V2 style '{DEFAULT_V2_STYLE}' for '{lang}'."
                    )
                    app_changed = True
            if app_changed:
                self._write_app_json(app_data)

        # Upgrade any agent-level Composite model overrides
        for agent_json in self._agent_json_files():
            rel_agent = str(agent_json.relative_to(self.app_root))
            try:
                agent_data = json.loads(agent_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            agent_ms = agent_data.get("modelSettings")
            if isinstance(agent_ms, dict):
                agent_model = str(agent_ms.get("model") or "")
                if (
                    agent_model.startswith(COMPOSITE_MODEL_PREFIX)
                    and agent_model != TARGET_COMPOSITE_MODEL
                ):
                    agent_ms["model"] = TARGET_COMPOSITE_MODEL
                    agent_json.write_text(
                        json.dumps(agent_data, indent=2, ensure_ascii=False)
                        + "\n",
                        encoding="utf-8",
                    )
                    changes.append(
                        f"Upgraded {rel_agent} modelSettings.model from "
                        f"'{agent_model}' to '{TARGET_COMPOSITE_MODEL}'."
                    )

        # Run in-place tag migration ([tag] -> <tag>, remap, deduplicate,
        # pause punctuation, and quoted-utterance style tag placement) across
        # instructions and tool files.
        text_targets = [
            *self._instruction_files(),
            *sorted(
                self.app_root.glob("tools/*/python_function/python_code.py")
            ),
            *sorted(self.app_root.glob("tools/*/*.json")),
        ]
        for path in text_targets:
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.app_root))
            orig = path.read_text(encoding="utf-8")
            migrated, notes = migrate_text_to_v2(orig)
            if migrated != orig:
                path.write_text(migrated, encoding="utf-8")
                changes.append(f"{rel}: {', '.join(notes)}.")

        return {
            "status": "REMEDIATED" if changes else "NO_CHANGES_NEEDED",
            "changes_applied": changes,
            "post_remediation_audit": self.audit(),
        }


# Backwards-compatible aliases
CXASVoiceV2Auditor = VoiceAgentV2Auditor
AuditAgentV2 = VoiceAgentV2Auditor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CXAS Gemini Composite V2 Voice Configuration Auditor & Migrator."
    )
    parser.add_argument(
        "--workspace",
        "--app-dir",
        dest="workspace",
        type=str,
        default=".",
        help="Path to the CXAS app directory.",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Perform audit without modifying files.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate a prioritized P0/P1/P2 Markdown readiness report.",
    )
    parser.add_argument(
        "--remediate",
        "--fix",
        dest="remediate",
        action="store_true",
        help="Apply automated in-place Composite V2 migration and remediation.",
    )
    parser.add_argument(
        "--json",
        "--json-output",
        dest="json_output",
        action="store_true",
        help="Print output as JSON.",
    )
    parser.add_argument(
        "--lint-report",
        action="store_true",
        help="Output in standard cxas lint format.",
    )

    args = parser.parse_args()
    auditor = VoiceAgentV2Auditor(workspace_dir=args.workspace)

    if args.lint_report:
        report = auditor.to_lint_report()
        if hasattr(report, "to_json") and args.json_output:
            print(report.to_json())
        elif hasattr(report, "print_and_exit"):
            report.print_and_exit(json_output=args.json_output)
        else:
            print(json.dumps(report, indent=2))
    elif args.report:
        rep = auditor.generate_prioritized_report()
        if args.json_output:
            print(json.dumps(rep, indent=2))
        else:
            print(rep["markdown_report"])
        if rep["overall_status"] != "PASSED":
            sys.exit(1)
    elif args.remediate:
        res = auditor.remediate()
        if args.json_output:
            print(json.dumps(res, indent=2))
        else:
            print(f"=== Composite V2 Remediation Status: {res['status']} ===")
            for ch in res["changes_applied"]:
                print(f" - {ch}")
            print(
                f"Post-remediation audit status: {res['post_remediation_audit']['overall_status']}"
            )
        if res["post_remediation_audit"]["overall_status"] != "PASSED":
            sys.exit(1)
    else:
        res = auditor.audit()
        if args.json_output:
            print(json.dumps(res, indent=2))
        else:
            print(f"=== CXAS Composite V2 Audit: {res['overall_status']} ===")
            print(f"Total Issues: {res['total_issues']}")
            for name, pdata in res["passes"].items():
                status = "PASS" if pdata["passed"] else "FAIL"
                print(f" [{status}] {name}")
                for issue in pdata["issues"]:
                    print(
                        f"   * [{issue['priority']}] {issue['code']}: {issue['message']}"
                    )
            if res.get("handoffs"):
                print(
                    f" [INFO] handoffs ({len(res['handoffs'])} cross-app/external hand-off(s) detected)"
                )
        if res["overall_status"] != "PASSED":
            sys.exit(1)


if __name__ == "__main__":
    main()
