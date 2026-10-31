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
- Upgrades older Composite models in `app.json` and agent overrides to
  `gemini-composite-v2` (other models are only reported), clears legacy V1
  Director's Notes and over-long speaking styles to an empty style, enables
  `languageSettings.enableMultilingualSupport` for multi-locale apps, and adds
  missing locales with the default locale's Chirp 3 HD voice persona (defaulting
  to `Aoede`, `<locale>-Chirp3-HD-Aoede`, when prebuilt voices are used).
- Migrates inline `[tag]` syntax to `<tag>`, converts translated non-English
  audio tags (`<pausa corta>`, `<短暂停顿>`, etc.) to canonical English `<...>`
  tags, remaps low-performing tags to validated Composite V2 equivalents
  (`<slow>` -> `<prosody rate="65%">`), deduplicates tag lists, rewrites
  `</prosody>` as a `<prosody rate="100%">` reset, puts prosody first in tag
  stacks, inserts commas before mid-sentence pause tags, relocates mid-sentence
  style tags in quoted utterances to sentence start, and removes closing audio
  tags.
- Audits unevaluated tags, misspelled bracket tags, `<sigh>`/`<exhales>` on
  ending or transfer turns, tool-scoped spoken text normalization (`Scope:`
  guard), ALL-CAPS lexical stress, cross-locale speaker profile accent
  mismatches, mixed Latin words in Devanagari utterances, contradictory legacy
  rules, `after_model_callback` tag coverage, and cross-app hand-offs.

Run with `uv run python` so the cxas_scrapi structural lint pass is included;
use `--remediate --dry-run` to preview changes and `--backup` to keep copies.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import itertools
import json
import pathlib
import re
import shutil
import sys
import tokenize
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
DEFAULT_PREBUILT_PERSONA = "Aoede"
EMPTY_V2_STYLE = ""
MAX_RECOMMENDED_STYLE_WORDS = 25
PROSODY_RESET_TAG = '<prosody rate="100%">'

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
    "slow": (
        'prosody rate="65%"',
        "Read aloud as a word; use a slower prosody rate instead.",
    ),
    "slower": (
        'prosody rate="65%"',
        "Read aloud as a word; use a slower prosody rate instead.",
    ),
    "exhale": ("exhales", "Use the canonical V2 spelling <exhales>."),
    "whisper": ("whispers", "Use the canonical V2 spelling <whispers>."),
    "clears throat": (
        "throat-clearing",
        "Use the canonical V2 tag <throat-clearing>.",
    ),
}

ALL_RECOGNIZED_AUDIO_TAGS: set[str] = ALLOWED_V2_AUDIO_TAGS | set(REMAP_V2)

# Common non-English translations of Composite V2 tags (Spanish, Mandarin,
# Hindi, Korean, Japanese, French, German, Portuguese). Composite V2 requires
# English tag keywords in every spoken language; translated tags are read aloud.
TRANSLATED_TAG_MAP: dict[str, str] = {
    # Spanish
    "pausa corta": "short pause",
    "pausa breve": "short pause",
    "pausa larga": "long pause",
    "pausa": "short pause",
    "suspiro": "sigh",
    "suspira": "sighs",
    "exhala": "exhales",
    "aliento": "breath",
    "respiración": "breath",
    "respiracion": "breath",
    "risa": "laugh",
    "risas": "chuckles",
    "risita": "chuckle",
    "jadeo": "gasp",
    "calma": "calm",
    "calmado": "calm",
    "calmada": "calm",
    "empático": "empathetic",
    "empatica": "empathetic",
    "empática": "empathetic",
    "empatico": "empathetic",
    "cálido": "warm",
    "calido": "warm",
    "cálida": "warm",
    "calida": "warm",
    "positivo": "positive",
    "positiva": "positive",
    "entusiasmo": "enthusiasm",
    "claro": "clear",
    "clara": "clear",
    "alivio": "relief",
    "susurro": "whispers",
    "susurros": "whispers",
    "susurrando": "whispering",
    # Mandarin Chinese
    "短暂停顿": "short pause",
    "短停顿": "short pause",
    "长停顿": "long pause",
    "停顿": "short pause",
    "叹气": "sigh",
    "呼吸": "breath",
    "吸气": "breath",
    "呼气": "exhales",
    "轻笑": "chuckles",
    "笑": "laugh",
    "倒吸一口气": "gasp",
    "平静": "calm",
    "冷静": "calm",
    "共情": "empathetic",
    "同情": "sympathetic",
    "温暖": "warm",
    "温和": "warm",
    "积极": "positive",
    "热情": "enthusiasm",
    "清晰": "clear",
    "坚定": "determination",
    "耳语": "whispers",
    "低语": "whispers",
    # Hindi
    "छोटा विराम": "short pause",
    "लंबा विराम": "long pause",
    "विराम": "short pause",
    "आह": "sigh",
    "साँस": "breath",
    "सांस": "breath",
    "हँसी": "chuckles",
    "शांत": "calm",
    "सहानुभूति": "empathetic",
    "गर्मजोशी": "warm",
    "सकारात्मक": "positive",
    "स्पष्ट": "clear",
    "राहत": "relief",
    # Korean
    "짧은 휴지": "short pause",
    "짧은 멈춤": "short pause",
    "긴 휴지": "long pause",
    "긴 멈춤": "long pause",
    "일시중지": "short pause",
    "한숨": "sigh",
    "숨": "breath",
    "웃음": "chuckles",
    "차분하게": "calm",
    "차분함": "calm",
    "공감": "empathetic",
    "따뜻하게": "warm",
    "따뜻함": "warm",
    "긍정적": "positive",
    # Japanese
    "短い間": "short pause",
    "長い間": "long pause",
    "ポーズ": "short pause",
    "ため息": "sigh",
    "息": "breath",
    "笑い": "chuckles",
    "穏やか": "calm",
    "共感": "empathetic",
    "温かい": "warm",
    "ポジティブ": "positive",
    # French / German / Portuguese
    "courte pause": "short pause",
    "longue pause": "long pause",
    "soupir": "sigh",
    "calme": "calm",
    "empathique": "empathetic",
    "chaleureux": "warm",
    "positif": "positive",
    "kurze pause": "short pause",
    "lange pause": "long pause",
    "seufzer": "sigh",
    "ruhig": "calm",
    "empathisch": "empathetic",
    "pausa curta": "short pause",
    "pausa longa": "long pause",
    "calmo": "calm",
}

TRANSLATED_AUDIO_TAG_RE = re.compile(
    r"(?:<\s*|\[\s*)("
    + "|".join(
        re.escape(k) for k in sorted(TRANSLATED_TAG_MAP, key=len, reverse=True)
    )
    + r")(?:\s*>|\s*\])",
    re.IGNORECASE,
)

# English-locale nationality/accent descriptors that should not be copied into
# non-English synthesizeSpeechConfigs.<locale>.instruction speaker profiles.
ENGLISH_ACCENT_IN_STYLE_RE = re.compile(
    r"\b(?:american|australian|aussie|british|scottish|irish|canadian|"
    r"new\s+zealand|kiwi|cockney|english\s+accent|us\s+accent|uk\s+accent|"
    r"au\s+accent)\b",
    re.IGNORECASE,
)

# Quoted utterance spans (single or double quotes) for script-purity checks.
# Single quotes require non-word boundaries so English apostrophes (caller's,
# don't) never pair with single-quoted phrases.
UTTERANCE_SPAN_RE = re.compile(
    r'"[^"\n]{3,400}"|(?<![\w])\'[^\'\n]{3,400}\'(?![\w])'
)
DEVANAGARI_CHAR_RE = re.compile(r"[\u0900-\u097F]")
LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")

# Closest evaluated tag to suggest for common unevaluated tags. Suggestions
# are report-only: swap a tag only when the user agrees.
UNEVALUATED_TAG_SUGGESTIONS: dict[str, str] = {
    "excited": "enthusiasm",
    "excitement": "enthusiasm",
    "celebratory": "enthusiasm",
    "cheerful": "positive",
    "amusement": "chuckles",
    "laughing": "chuckles",
    "seriousness": "calm",
    "serious": "calm",
    "confusion": "clear",
    "frustration": "determination",
    "anger": "determination",
    "sad": "sympathetic",
    "apologetic": "empathetic",
    "curious": "interest",
    "extremely fast": "faster",
    "pause": "short pause",
}

# Markup element names that are never audio tags.
NON_AUDIO_ELEMENT_NAMES: set[str] = {
    "b",
    "br",
    "code",
    "div",
    "em",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "td",
    "th",
    "tr",
    "u",
    "ul",
}

KNOWN_TAG_WORDS: list[str] = sorted(
    ALL_RECOGNIZED_AUDIO_TAGS | set(UNEVALUATED_TAG_SUGGESTIONS)
)

# Bare lowercase angle/square tokens that may be audio tags.
ANGLE_TOKEN_RE = re.compile(r"<([a-z][a-z \-]{0,30})>")
BRACKET_TOKEN_RE = re.compile(r"\[([a-z]+(?: [a-z]+){0,2})\]")

# Prosody tags and stacks where prosody is not first.
PROSODY_TAG_RE = re.compile(
    r'<\s*prosody\s+rate\s*=\s*"?\d{1,3}\s*%"?\s*>', re.IGNORECASE
)
PROSODY_NOT_FIRST_RE = re.compile(
    r"(?P<before>(?:<(?!/|prosody\b)[a-z][a-z \-]{0,30}>[ \t]*)+)"
    r'(?P<pros><\s*prosody\s+rate\s*=\s*"?\d{1,3}\s*%"?\s*>)',
    re.IGNORECASE,
)
CLOSING_PROSODY_RE = re.compile(r"[ \t]*</\s*prosody\s*>", re.IGNORECASE)

# Quotes inside tag attributes (<prosody rate="70%">) are masked while
# quoted utterances are processed, then restored.
_TAG_QUOTE_SENTINEL = "\ue000"
TAG_WITH_QUOTES_RE = re.compile(r"<[^<>\n]*\"[^<>\n]*>")

# Turns that end the session or transfer/escalate the call.
ENDING_SOUND_TAG_RE = re.compile(r"<\s*(?:sighs?|exhales)\s*>", re.IGNORECASE)
TOOL_REF_RE = re.compile(r"\{@TOOL:\s*([\w.\-]+)\s*\}")
ENDING_TOOL_NAME_RE = re.compile(
    r"end_session|transfer|escalat|hand_?off|live_?agent|human",
    re.IGNORECASE,
)
ENDING_PHRASE_RE = re.compile(
    r"\b(?:end(?:s|ing)?\s+the\s+(?:call|session)"
    r"|transfer(?:s|ring)?\s+(?:the\s+call|you)"
    r"|put(?:ting)?\s+you\s+(?:straight\s+)?through"
    r"|escalat\w*"
    r"|hand(?:ing)?\s+you\s+(?:over|off)"
    r"|connect(?:ing)?\s+you\s+(?:to|with)\s+(?:a\s+|an\s+|our\s+)?"
    r"(?:human|live|specialist))",
    re.IGNORECASE,
)
ENDING_CONST_NAME_RE = re.compile(
    r"transfer|escalat|hand_?off|goodbye|farewell|end_?session|closing",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:never|do\s+not|don't|avoid|must\s+not)\b", re.IGNORECASE
)
CLAUSE_BOUNDARY_RE = re.compile(r"[.;:()!?\n]")
CHIRP3_VOICE_RE = re.compile(
    r"^(?P<locale>[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)-Chirp3-HD-(?P<name>\w+)$"
)

DEFAULT_V2_VOICES: dict[str, str] = {
    # Base 2/3-letter language codes
    "en": f"en-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr": f"fr-FR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "de": f"de-DE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ja": f"ja-JP-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pt": f"pt-BR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "it": f"it-IT-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "yue": f"yue-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ko": f"ko-KR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl": f"nl-NL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "hi": f"hi-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "sv": f"sv-SE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "da": f"da-DK-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fi": f"fi-FI-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pl": f"pl-PL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "tr": f"tr-TR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "id": f"id-ID-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "mr": f"mr-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ro": f"ro-RO-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ta": f"ta-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "te": f"te-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "vi": f"vi-VN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    # Specific locale codes
    "en-US": f"en-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-AU": f"en-AU-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-GB": f"en-GB-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-CA": f"en-CA-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-IN": f"en-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-hi": f"en-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-es": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-US": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-ES": f"es-ES-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-MX": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn-CN": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn-TW": f"cmn-TW-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh-CN": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh-TW": f"cmn-TW-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "yue-CN": f"yue-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "hi-IN": f"hi-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ko-KR": f"ko-KR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ja-JP": f"ja-JP-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr-FR": f"fr-FR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr-CA": f"fr-CA-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "de-DE": f"de-DE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pt-BR": f"pt-BR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "it-IT": f"it-IT-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl-NL": f"nl-NL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl-BE": f"nl-BE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "sv-SE": f"sv-SE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "da-DK": f"da-DK-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fi-FI": f"fi-FI-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pl-PL": f"pl-PL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "tr-TR": f"tr-TR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "id-ID": f"id-ID-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "mr-IN": f"mr-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ro-RO": f"ro-RO-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ta-IN": f"ta-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "te-IN": f"te-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "vi-VN": f"vi-VN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
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

# Regex matching an ALL-CAPS word (>=2 letters) right after <exhales>/<sigh(s)>
ALL_CAPS_AFTER_VOCAL_TAG_RE = re.compile(
    r"<(?:exhales|sighs?)>\s+([A-Z]{2,})\b"
)

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
    "transferToNga": "another CX Agent Studio app",
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
    """Returns the default Chirp3-HD-Aoede voice identifier for a locale."""
    norm = locale.strip().lower().replace("_", "-")
    if norm in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[norm]
    prefix = norm.split("-")[0]
    if prefix in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[prefix]
    return f"{locale}-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}"


def derive_locale_voice(
    locale: str,
    persona_voice: str | None,
    custom_voice_names: set[str] | None = None,
) -> str:
    """Returns a voice for `locale` that keeps the app's prebuilt or custom persona.

    If the default locale uses `en-AU-Chirp3-HD-Aoede`, a new `es-US` locale
    gets `es-US-Chirp3-HD-Aoede`, so every language sounds like the same
    speaker. If the default locale uses a custom voice from `customVoiceSamples`,
    reuses that custom voice key. Falls back to the Aoede default when no
    Chirp 3 HD or custom persona is configured.
    """
    clean_persona = str(persona_voice or "").strip()
    match = CHIRP3_VOICE_RE.match(clean_persona)
    if match:
        fallback_locale = get_default_v2_voice(locale).split("-Chirp3-HD-")[0]
        return f"{fallback_locale}-Chirp3-HD-{match.group('name')}"
    if custom_voice_names and clean_persona in custom_voice_names:
        return clean_persona
    return get_default_v2_voice(locale)


def _is_negated(text: str, pos: int, window: int = 120) -> bool:
    """Returns True if the clause before `pos` negates what follows."""
    prefix = text[max(0, pos - window) : pos]
    cut = max((m.end() for m in CLAUSE_BOUNDARY_RE.finditer(prefix)), default=0)
    return bool(NEGATION_RE.search(prefix[cut:]))


def _has_ending_context(line: str) -> bool:
    """True if `line` ends the session or transfers/escalates the call."""
    for match in TOOL_REF_RE.finditer(line):
        if ENDING_TOOL_NAME_RE.search(match.group(1)) and not _is_negated(
            line, match.start()
        ):
            return True
    return any(
        not _is_negated(line, m.start())
        for m in ENDING_PHRASE_RE.finditer(line)
    )


def _non_negated_ending_sounds(line: str) -> list[str]:
    """Returns <sigh>/<sighs>/<exhales> tags in `line` that aren't negated."""
    return [
        m.group(0)
        for m in ENDING_SOUND_TAG_RE.finditer(line)
        if not _is_negated(line, m.start())
    ]


def _mask_tag_quotes(text: str) -> str:
    """Hides quotes inside tags (<prosody rate="70%">) from quote matching."""
    if _TAG_QUOTE_SENTINEL in text:
        return text
    return TAG_WITH_QUOTES_RE.sub(
        lambda m: m.group(0).replace('"', _TAG_QUOTE_SENTINEL), text
    )


def _unmask_tag_quotes(text: str) -> str:
    return text.replace(_TAG_QUOTE_SENTINEL, '"')


JSON_ANGLE_ESCAPE_RE = re.compile(r"\\u00(3[cC]|3[eE])")


def _decode_json_angle_escapes(text: str) -> str:
    """Turns JSON-escaped angle brackets (\\u003c, \\u003e) into < and >.

    Exported app JSON often escapes angle brackets, which hides audio tags
    such as \\u003csigh\\u003e from line-based checks.
    """
    return JSON_ANGLE_ESCAPE_RE.sub(
        lambda m: "<" if m.group(1).lower() == "3c" else ">", text
    )


def _encode_json_angle_escapes(text: str) -> str:
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def _suggest_tag(name: str) -> str | None:
    """Returns the closest evaluated V2 tag for an unevaluated tag name."""
    if name in UNEVALUATED_TAG_SUGGESTIONS:
        return UNEVALUATED_TAG_SUGGESTIONS[name]
    close = difflib.get_close_matches(
        name, sorted(ALLOWED_V2_AUDIO_TAGS), n=1, cutoff=0.75
    )
    return close[0] if close else None


def _python_string_segments(source: str) -> list[tuple[int, str]]:
    """Returns (start_line, text) for every string literal in Python source."""
    segments: list[tuple[int, str]] = []
    string_types = {tokenize.STRING}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        string_types.add(fstring_middle)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in string_types:
                segments.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [(1, source)]
    return segments


def _has_legacy_v1_directors_note(inst: str) -> bool:
    """Returns True if instruction contains legacy V1 Director's Note headers or <agent_voice> DSP block."""
    lower = inst.lower()
    return (
        "# audio profile" in lower
        or "# the scene" in lower
        or "# director" in lower
        or "## transcript" in lower
        or "<agent_voice" in lower
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
    # preceded directly by a word character (including accented Latin,
    # Devanagari, Hangul, Kana, or Hanzi) without punctuation.
    def _punct_pause(m: re.Match[str]) -> str:
        nonlocal pause_commas
        pause_commas += 1
        return f"{m.group(1)}, {m.group(2)}"

    inner = re.sub(
        r"([A-Za-z0-9\u00C0-\u024F\u0900-\u0963\u0966-\u097F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF])[ \t]+(<(?:short|long)\s+pause>)",
        _punct_pause,
        inner,
        flags=re.IGNORECASE,
    )

    # 2. Move mid-sentence style/tone tags to the start of their sentence.
    # Recognize ASCII (.!?), Devanagari danda, and CJK sentence boundaries.
    sentences = re.split(
        r"([.!?]+(?:\s+|$)|[\u0964\u0965]+(?:\s+|$)|[\u3002\uff01\uff1f]+\s*)",
        inner,
    )
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
            # Stack lifted tags directly onto existing opening tags
            # (<calm><empathetic>), otherwise start the sentence with them.
            joiner = "" if prefix.endswith(">") else (" " if prefix else "")
            sent = prefix + joiner + "".join(lifted_tags) + " " + new_rest
        rebuilt_parts.append(sent + delim)

    return f'"{"".join(rebuilt_parts)}"', pause_commas, style_moves


def _reorder_prosody_first(line: str) -> tuple[str, int]:
    """Moves <prosody rate="N%"> to the front of a tag stack."""
    moved = 0

    def _swap(m: re.Match[str]) -> str:
        nonlocal moved
        before_tags = re.findall(r"<([^<>]+)>", m.group("before"))
        if not all(
            _normalize_tag_inner(t) in ALL_RECOGNIZED_AUDIO_TAGS
            for t in before_tags
        ):
            return m.group(0)
        moved += 1
        return m.group("pros") + m.group("before").rstrip()

    return PROSODY_NOT_FIRST_RE.sub(_swap, line), moved


def _replace_closing_prosody(line: str) -> tuple[str, int, int]:
    """Rewrites </prosody> as a reset when speech continues after it.

    Returns:
        Tuple of (rewritten_line, resets_inserted, closings_removed).
    """
    resets = 0
    removed = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal resets, removed
        remainder = line[m.end() :]
        spoken_after = re.sub(r"<[^<>]*>", "", remainder)
        if re.search(r"[A-Za-z0-9\u00C0-\uFFFF]", spoken_after.split('"')[0]):
            resets += 1
            return " " + PROSODY_RESET_TAG
        removed += 1
        return ""

    return CLOSING_PROSODY_RE.sub(_sub, line), resets, removed


def migrate_text_to_v2(
    text: str, fix_placement: bool = True
) -> tuple[str, list[str]]:
    """Migrates `[tag]` -> `<tag>`, remaps stale tags, and fixes placement.

    Args:
        text: Prompt, docstring, or tool text to migrate.
        fix_placement: Whether to fix tag placement inside double-quoted
            utterances. Disable it for raw JSON, where every string value
            is double-quoted and prose would be mistaken for utterances.
    """
    notes: list[str] = []
    syntax_count = 0
    remap_count = 0
    translated_count = 0
    closing_count = 0
    reset_count = 0
    prosody_moves = 0

    lines_out: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        if _is_meta_documentation_line(raw_line):
            lines_out.append(raw_line)
            continue

        # 1. </prosody> becomes a <prosody rate="100%"> reset when speech
        # continues after it; other closing audio tags are removed.
        cur_line, resets, removed = _replace_closing_prosody(raw_line)
        reset_count += resets
        closing_count += removed

        def _strip_closing(m: re.Match[str]) -> str:
            nonlocal closing_count
            closing_count += 1
            return ""

        cur_line = CLOSING_AUDIO_TAG_RE.sub(_strip_closing, cur_line)

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

        # 3b. Convert translated non-English audio tags (<pausa corta>, <短暂停顿>)
        # to their canonical English Composite V2 tag unless negated.
        cursor = 0
        pieces = []
        for match in TRANSLATED_AUDIO_TAG_RE.finditer(cur_line):
            if cur_line[match.end() : match.end() + 1] == "(":
                continue
            if match.group(0).startswith("[") and (
                match.group(1).isupper()
                or _is_label_context(cur_line[: match.start()])
            ):
                continue
            if _is_negated(cur_line, match.start()):
                continue
            norm_inner = " ".join(match.group(1).split()).lower()
            target = TRANSLATED_TAG_MAP.get(norm_inner)
            if not target:
                continue
            translated_count += 1
            pieces.append(cur_line[cursor : match.start()])
            pieces.append(f"<{target}>")
            cursor = match.end()
        pieces.append(cur_line[cursor:])
        cur_line = "".join(pieces)

        # 4. Put <prosody rate="N%"> first in tag stacks, then deduplicate
        # tag lists on the line
        cur_line, moves = _reorder_prosody_first(cur_line)
        prosody_moves += moves
        cur_line = _deduplicate_tag_lists_in_line(cur_line)
        lines_out.append(cur_line)

    migrated = "".join(lines_out)

    # 5. Inside quoted utterances, insert commas before mid-sentence pause tags
    # and move mid-sentence style tags to the start of their sentence. Quotes
    # inside tags (<prosody rate="70%">) are masked so they never end a span.
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

    if fix_placement:
        migrated = _unmask_tag_quotes(
            QUOTED_SPAN_RE.sub(_rewrite_utterance, _mask_tag_quotes(migrated))
        )

    if syntax_count:
        notes.append(f"converted {syntax_count} [tag] -> <tag>")
    if remap_count:
        notes.append(f"remapped {remap_count} low-performing tag(s)")
    if translated_count:
        notes.append(
            f"converted {translated_count} translated audio tag(s) to English"
        )
    if closing_count:
        notes.append(f"removed {closing_count} closing audio tag(s)")
    if reset_count:
        notes.append(
            f'rewrote {reset_count} </prosody> as a <prosody rate="100%"> reset'
        )
    if prosody_moves:
        notes.append(
            f"moved {prosody_moves} prosody tag(s) to the front of a stack"
        )
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

    def __init__(
        self,
        workspace_dir: pathlib.Path | str = ".",
        dry_run: bool = False,
        backup: bool = False,
        bind_custom_voice: str | None = None,
        prebuilt_voice: str | None = None,
    ):
        self.workspace_path = pathlib.Path(workspace_dir).resolve()
        self.app_json_path = self._find_app_json()
        self.app_root = (
            self.app_json_path.parent
            if self.app_json_path.is_file()
            else self.workspace_path
        )
        self.dry_run = dry_run
        self.backup = backup
        self.bind_custom_voice = (
            bind_custom_voice.strip() if bind_custom_voice else None
        )
        self.prebuilt_voice = self._normalize_prebuilt_persona(prebuilt_voice)
        if self.bind_custom_voice and self.prebuilt_voice:
            raise ValueError(
                "Cannot specify both bind_custom_voice and prebuilt_voice."
            )
        self.planned_diffs: list[str] = []

    @staticmethod
    def _normalize_prebuilt_persona(prebuilt_voice: str | None) -> str | None:
        if prebuilt_voice is None:
            return None
        cleaned = prebuilt_voice.strip()
        if not cleaned or cleaned.lower() == "auto":
            return DEFAULT_PREBUILT_PERSONA
        match = CHIRP3_VOICE_RE.match(cleaned)
        if match:
            cleaned = match.group("name")
        if cleaned.islower():
            return cleaned[:1].upper() + cleaned[1:]
        return cleaned

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

    def _rel(self, path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(self.app_root))
        except ValueError:
            return str(path)

    def _write_text(self, path: pathlib.Path, new_text: str) -> None:
        """Writes `new_text`, honoring --dry-run (diff only) and --backup."""
        old_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old_text == new_text:
            return
        if self.dry_run:
            rel = self._rel(path)
            self.planned_diffs.append(
                "".join(
                    difflib.unified_diff(
                        old_text.splitlines(keepends=True),
                        new_text.splitlines(keepends=True),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )
                )
            )
            return
        if self.backup and path.is_file():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        path.write_text(new_text, encoding="utf-8")

    @staticmethod
    def _json_indent(raw: str) -> int:
        match = re.search(r"\n([ \t]+)\"", raw)
        return len(match.group(1).expandtabs(4)) if match else 2

    def _write_json(self, path: pathlib.Path, data: dict[str, Any]) -> None:
        raw = path.read_text(encoding="utf-8") if path.is_file() else ""
        self._write_text(
            path,
            json.dumps(data, indent=self._json_indent(raw), ensure_ascii=False)
            + "\n",
        )

    def _write_app_json(self, data: dict[str, Any]) -> None:
        self._write_json(self.app_json_path, data)

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        return bool(
            "\n" not in value
            and len(value) < 260
            and re.fullmatch(r"[\w./\- ]+\.(?:txt|md)", value.strip())
        )

    def _instruction_sources(self) -> list[dict[str, Any]]:
        """Resolves the global and agent instructions, files or inline text.

        Each source is a dict with `label`, `text`, and either `path` (a file)
        or `json_path` + `json_key` (inline text inside a JSON config).
        Pointer problems are recorded in `self.pointer_issues`.
        """
        sources: list[dict[str, Any]] = []
        self.pointer_issues: list[dict[str, Any]] = []
        seen: set[pathlib.Path] = set()

        def _add_file(path: pathlib.Path) -> None:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                return
            seen.add(resolved)
            sources.append(
                {
                    "label": self._rel(path),
                    "path": path,
                    "text": path.read_text(encoding="utf-8"),
                }
            )

        def _resolve(
            owner: pathlib.Path, value: Any, fallback: pathlib.Path | None
        ) -> bool:
            if isinstance(value, str) and value.strip():
                if self._looks_like_path(value):
                    for base in (self.app_root, owner.parent):
                        candidate = base / value.strip()
                        if candidate.is_file():
                            _add_file(candidate)
                            return True
                    self.pointer_issues.append(
                        {
                            "code": "MISSING_INSTRUCTION_FILE",
                            "file": self._rel(owner),
                            "priority": "P0",
                            "message": (
                                f"{self._rel(owner)} points to instruction file "
                                f"'{value.strip()}', which does not exist."
                            ),
                            "recommended": "Fix the pointer or restore the file.",
                        }
                    )
                    return True
                sources.append(
                    {
                        "label": f"{self._rel(owner)}:instruction",
                        "json_path": owner,
                        "text": value,
                    }
                )
                return True
            if fallback is not None and fallback.is_file():
                _add_file(fallback)
                return True
            return False

        app_data = self._read_app_json()
        has_global = _resolve(
            self.app_json_path,
            app_data.get("globalInstruction"),
            self.app_root / "global_instruction.txt",
        )
        if sources and "json_path" in sources[-1]:
            sources[-1]["json_key"] = "globalInstruction"
            sources[-1]["label"] = "app.json:globalInstruction"
        if not has_global:
            self.pointer_issues.append(
                {
                    "code": "MISSING_GLOBAL_INSTRUCTION",
                    "file": "app.json",
                    "priority": "P1",
                    "message": (
                        "The app has no global instruction, so the Composite V2 "
                        "audio tag and text normalization blocks have no "
                        "app-wide home."
                    ),
                    "recommended": (
                        "Create global_instruction.txt beside app.json and set "
                        '"globalInstruction": "global_instruction.txt".'
                    ),
                }
            )

        for agent_json in self._agent_json_files():
            try:
                agent_data = json.loads(agent_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            count_before = len(sources)
            _resolve(
                agent_json,
                agent_data.get("instruction"),
                agent_json.parent / "instruction.txt",
            )
            if len(sources) > count_before and "json_path" in sources[-1]:
                sources[-1]["json_key"] = "instruction"

        for p in sorted(self.app_root.glob("agents/*/instruction.txt")):
            _add_file(p)
        return sources

    def _instruction_files(self) -> list[pathlib.Path]:
        return [s["path"] for s in self._instruction_sources() if "path" in s]

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
        own_app = str(self._read_app_json().get("name") or "")
        candidates: list[tuple[str, str]] = []
        file_paths: list[pathlib.Path] = []
        if self.app_json_path.is_file():
            file_paths.append(self.app_json_path)
        file_paths.extend(self._agent_json_files())
        file_paths.extend(self._tool_and_callback_files())
        for source in self._instruction_sources():
            if "path" in source:
                candidates.append((source["label"], source["text"]))
        for path in file_paths:
            try:
                candidates.append(
                    (self._rel(path), path.read_text(encoding="utf-8"))
                )
            except OSError:
                continue

        for rel, text in candidates:
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
                    if resource in seen_resources or (
                        own_app and resource.startswith(own_app)
                    ):
                        continue
                    seen_resources.add(resource)
                    handoffs.append(
                        {
                            "type": "app_reference",
                            "target_kind": resource,
                            "file": rel,
                            "line": lineno,
                            "message": (
                                f"References another app '{resource}'. "
                                "Check whether that destination app needs its own "
                                "Composite V2 migration."
                            ),
                        }
                    )
        return handoffs

    def _persona_voice(
        self,
        speech_cfgs: dict[str, Any],
        default_lang: str,
        custom_voice_names: set[str] | None = None,
    ) -> str:
        """Returns the default locale's voice (the app's voice persona)."""
        default_cfg = speech_cfgs.get(default_lang)
        if isinstance(default_cfg, dict) and default_cfg.get("voice"):
            return str(default_cfg["voice"])
        for cfg in speech_cfgs.values():
            if not isinstance(cfg, dict):
                continue
            voice = str(cfg.get("voice") or "").strip()
            if CHIRP3_VOICE_RE.match(voice) or (
                custom_voice_names and voice in custom_voice_names
            ):
                return voice
        return ""

    def audit_app_speech_style_v2(
        self, app_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Audits app.json and agent overrides for gemini-composite-v2 and V2 styles."""
        issues: list[dict[str, Any]] = []
        rel_app = self._rel(self.app_json_path)

        model_settings = app_data.get("modelSettings") or {}
        current_model = model_settings.get("model")
        if current_model != TARGET_COMPOSITE_MODEL:
            older_composite = not current_model or str(
                current_model
            ).startswith(COMPOSITE_MODEL_PREFIX)
            issues.append(
                {
                    "code": "MODEL_NOT_COMPOSITE_V2",
                    "file": rel_app,
                    "priority": "P0",
                    "message": (
                        f"modelSettings.model is '{current_model}'; Composite V2 "
                        f"needs '{TARGET_COMPOSITE_MODEL}'."
                        + (
                            ""
                            if older_composite
                            else " This is not a Composite model, so --remediate "
                            "leaves it unchanged."
                        )
                    ),
                    "recommended": (
                        f"Set modelSettings.model to '{TARGET_COMPOSITE_MODEL}'."
                        if older_composite
                        else "Confirm with the app owner before switching a "
                        "non-Composite app to Composite V2."
                    ),
                }
            )
        temperature = model_settings.get("temperature")
        if temperature is not None and temperature != 1.0:
            issues.append(
                {
                    "code": "TEMPERATURE_ADVISORY",
                    "file": rel_app,
                    "priority": "P2",
                    "message": (
                        f"modelSettings.temperature is {temperature}. Composite "
                        "V2 has no documented temperature requirement; 1.0 was "
                        "the Composite V1 recommendation against repetitive "
                        "phrasing on long calls."
                    ),
                    "recommended": (
                        "Advisory only. Keep the current value unless you see "
                        "repetitive phrasing; --remediate never changes it."
                    ),
                }
            )

        # Check agent-level modelSettings overrides
        for agent_json in self._agent_json_files():
            rel_agent = self._rel(agent_json)
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
                            f"with '{agent_model}'; Composite V2 needs "
                            f"'{TARGET_COMPOSITE_MODEL}'."
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
        if any(
            isinstance(lang, str) and lang != default_lang for lang in supported
        ) and not lang_settings.get("enableMultilingualSupport"):
            issues.append(
                {
                    "code": "MULTILINGUAL_SUPPORT_NOT_ENABLED",
                    "file": rel_app,
                    "priority": "P1",
                    "message": (
                        "languageSettings.supportedLanguageCodes declares "
                        f"additional locales ({supported}), but "
                        "languageSettings.enableMultilingualSupport is not true."
                    ),
                    "recommended": (
                        "Set languageSettings.enableMultilingualSupport to true "
                        "(--remediate does this automatically)."
                    ),
                }
            )

        audio_cfg = app_data.get("audioProcessingConfig") or {}
        speech_cfgs = audio_cfg.get("synthesizeSpeechConfigs") or {}
        custom_samples = [
            s
            for s in (audio_cfg.get("customVoiceSamples") or [])
            if isinstance(s, dict)
        ]
        custom_voice_names = {
            str(s.get("name")).strip() for s in custom_samples if s.get("name")
        }
        referenced_voices = {
            str(cfg.get("voice")).strip()
            for cfg in speech_cfgs.values()
            if isinstance(cfg, dict) and cfg.get("voice")
        }
        for sample in custom_samples:
            sample_name = str(sample.get("name") or "").strip()
            label = sample_name or "customVoiceSamples[]"
            sample_uri = str(sample.get("voiceSampleGcsUri") or "").strip()
            consent_uri = str(sample.get("consentAudioGcsUri") or "").strip()
            missing_uris: list[str] = []
            if not sample_uri.startswith("gs://"):
                missing_uris.append("voiceSampleGcsUri")
            if not consent_uri.startswith("gs://"):
                missing_uris.append("consentAudioGcsUri")
            if missing_uris:
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_MISSING_GCS_URI",
                        "file": rel_app,
                        "priority": "P0",
                        "message": (
                            f"Custom voice '{label}' is missing a valid 'gs://' URI "
                            f"for {', '.join(missing_uris)}. At runtime, CES silently "
                            "falls back to a prebuilt voice when either URI is missing."
                        ),
                        "recommended": (
                            f"Set valid 'gs://...' URIs for {', '.join(missing_uris)} "
                            f"on custom voice '{label}' in "
                            "audioProcessingConfig.customVoiceSamples."
                        ),
                    }
                )

            voice_inst = str(sample.get("voiceInstruction") or "").strip()
            if _has_legacy_v1_directors_note(voice_inst):
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_DIRECTORS_NOTE",
                        "file": rel_app,
                        "priority": "P0",
                        "message": (
                            f"Custom voice '{label}' has a V1 Director's Note or "
                            "<agent_voice> DSP block in voiceInstruction. Long voice "
                            "instructions fight the reference audio embedding and "
                            "make the voice drift and conflict with inline tags."
                        ),
                        "recommended": (
                            'Clear voiceInstruction to "" and set '
                            '"voiceInstructionMode": "NO_INSTRUCTION" (--remediate '
                            "does this automatically) and steer delivery with "
                            "inline <...> tags."
                        ),
                    }
                )
            elif voice_inst:
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_INSTRUCTION_TOO_LONG",
                        "file": rel_app,
                        "priority": "P1",
                        "message": (
                            f"Custom voice '{label}' has a non-empty "
                            f"({len(voice_inst.split())}-word) voiceInstruction. "
                            "Because the reference WAV already defines the speaker's "
                            "vocal identity, text descriptions in voiceInstruction "
                            "compete with the voice embedding and cause drift on "
                            "long calls."
                        ),
                        "recommended": (
                            'Clear voiceInstruction to "" and set '
                            '"voiceInstructionMode": "NO_INSTRUCTION" (--remediate '
                            "does this automatically); steer emotion with inline "
                            "<...> tags."
                        ),
                    }
                )

            mode = str(sample.get("voiceInstructionMode") or "").strip()
            if mode != "NO_INSTRUCTION":
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_INSTRUCTION_MODE_NOT_DISABLED",
                        "file": rel_app,
                        "priority": "P0",
                        "message": (
                            f"Custom voice '{label}' has voiceInstructionMode="
                            f"'{mode or 'unspecified'}' instead of 'NO_INSTRUCTION'. "
                            "On cxas push (UpdateApp), CES either auto-generates an "
                            "<agent_voice> DSP block (GENERATE_INSTRUCTION) or "
                            "restores a cached priorVoiceInstruction from Spanner "
                            "when voiceInstructionMode is not 'NO_INSTRUCTION'."
                        ),
                        "recommended": (
                            'Set "voiceInstructionMode": "NO_INSTRUCTION" and '
                            '"voiceInstruction": "" on customVoiceSamples[] '
                            "(--remediate does this automatically)."
                        ),
                    }
                )

            if (
                sample_name
                and speech_cfgs
                and sample_name not in referenced_voices
            ):
                issues.append(
                    {
                        "code": "UNBOUND_CUSTOM_VOICE_SAMPLE",
                        "file": rel_app,
                        "priority": "P2",
                        "message": (
                            f"Custom voice '{sample_name}' in customVoiceSamples is "
                            "not referenced by any "
                            "synthesizeSpeechConfigs.<locale>.voice."
                        ),
                        "recommended": (
                            "Bind synthesizeSpeechConfigs.<locale>.voice to "
                            f"'{sample_name}' (--remediate --bind-custom-voice "
                            f"'{sample_name}' or 'auto' does this automatically), "
                            "or if using a prebuilt voice instead, default to "
                            f"'{DEFAULT_PREBUILT_PERSONA}' "
                            f"('<locale>-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}') "
                            "and remove the unused entry from customVoiceSamples."
                        ),
                    }
                )

            if str(sample.get("previewAudioContent") or "").strip():
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_STALE_PREVIEW_AUDIO_BLOB",
                        "file": rel_app,
                        "priority": "P2",
                        "message": (
                            f"Custom voice '{label}' embeds a base64 "
                            "previewAudioContent WAV blob in app.json, bloating "
                            "version control with a transient console preview."
                        ),
                        "recommended": (
                            "Remove previewAudioContent from customVoiceSamples[] "
                            "(--remediate strips this automatically)."
                        ),
                    }
                )

        if not speech_cfgs:
            default_suggested = get_default_v2_voice(default_lang)
            issues.append(
                {
                    "code": "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
                    "file": rel_app,
                    "priority": "P0",
                    "message": "audioProcessingConfig.synthesizeSpeechConfigs is missing or empty.",
                    "recommended": (
                        f"Configure synthesizeSpeechConfigs for {declared_langs} "
                        f"with default prebuilt '{DEFAULT_PREBUILT_PERSONA}' "
                        f"(e.g. '{default_suggested}'; --remediate scaffolds "
                        "declared locales automatically) or your "
                        "customVoiceSamples key, plus a ~12-word speaker "
                        "profile for prebuilt voices (or an empty speaking style)."
                    ),
                }
            )
            return {"passed": False, "issues": issues}

        persona_voice = self._persona_voice(
            speech_cfgs, default_lang, custom_voice_names
        )
        for lang in declared_langs:
            cfg = speech_cfgs.get(lang)
            suggested_voice = derive_locale_voice(
                lang, persona_voice, custom_voice_names
            )
            if not isinstance(cfg, dict):
                issues.append(
                    {
                        "code": "MISSING_LOCALE_SPEECH_CONFIG",
                        "file": rel_app,
                        "priority": "P0",
                        "message": f"Declared language '{lang}' is missing from synthesizeSpeechConfigs.",
                        "recommended": (
                            f"Add '{lang}' with voice='{suggested_voice}' (same "
                            "persona as the default locale; verify it exists for "
                            "this locale) and a ~12-word speaker profile (or an "
                            "empty speaking style)."
                        ),
                    }
                )
                continue

            if str(cfg.get("voiceSampleGcsUri") or "").strip():
                issues.append(
                    {
                        "code": "DEPRECATED_INLINE_VOICE_SAMPLE_URI",
                        "file": rel_app,
                        "priority": "P0",
                        "message": (
                            f"Locale '{lang}' sets deprecated inline "
                            "synthesizeSpeechConfigs.voiceSampleGcsUri. CES "
                            "runtime ignores inline voiceSampleGcsUri and falls "
                            "back to a prebuilt voice."
                        ),
                        "recommended": (
                            "Move the custom voice into "
                            "audioProcessingConfig.customVoiceSamples[] with name, "
                            "voiceSampleGcsUri, consentAudioGcsUri, and "
                            '"voiceInstructionMode": "NO_INSTRUCTION", and '
                            "reference its name in "
                            "synthesizeSpeechConfigs.<locale>.voice."
                        ),
                    }
                )

            voice = str(cfg.get("voice") or "").strip()
            is_custom_voice = bool(voice and voice in custom_voice_names)
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
                            "Replace with a ~12-word speaker profile describing who the "
                            "speaker is (age, vocal timbre, pitch, accent, pace — see "
                            "vertical examples in references/natural_speech_patterns_v2.md), "
                            'or clear to "" as a secondary alternative (--remediate clears '
                            'the legacy block to "" and prints the removed text).'
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
                            "Composite V2 recommends a ~12-word speaker profile describing who "
                            "the speaker is (or an empty style as a secondary alternative)."
                        ),
                        "recommended": (
                            f"Shorten to a ~12-word speaker profile, or clear the '{lang}' "
                            'style to "" (--remediate clears it and prints the removed text).'
                        ),
                    }
                )
            elif is_custom_voice and inst:
                issues.append(
                    {
                        "code": "CUSTOM_VOICE_LOCALE_STYLE_CONFLICT",
                        "file": rel_app,
                        "priority": "P1",
                        "message": (
                            f"Locale '{lang}' uses custom cloned voice '{voice}' "
                            "with a non-empty speaking style instruction "
                            f"('{inst}'). TextToSpeechProcessor concatenates this "
                            "instruction into speechMetadata.style, competing with "
                            "the reference WAV embedding and causing "
                            "speaker-similarity drift over long calls."
                        ),
                        "recommended": (
                            f"Set synthesizeSpeechConfigs['{lang}'].instruction "
                            'to "" when using a custom cloned voice (--remediate '
                            "does this automatically), and steer emotion with "
                            "inline <...> tags."
                        ),
                    }
                )
            elif (
                inst
                and not lang.lower().startswith("en")
                and ENGLISH_ACCENT_IN_STYLE_RE.search(inst)
            ):
                accent_match = ENGLISH_ACCENT_IN_STYLE_RE.search(inst)
                accent_term = (
                    accent_match.group(0) if accent_match else "English"
                )
                issues.append(
                    {
                        "code": "LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH",
                        "file": rel_app,
                        "priority": "P1",
                        "message": (
                            f"Non-English locale '{lang}' speaking style instruction "
                            f"contains an English-locale descriptor ('{accent_term}'), "
                            f"which forces a foreign English accent onto '{lang}' speech."
                        ),
                        "recommended": (
                            f"Use a locale-neutral or native ~12-word speaker profile for "
                            f"'{lang}', or set synthesizeSpeechConfigs['{lang}'].instruction "
                            'to "" as a secondary alternative.'
                        ),
                    }
                )

            if not voice:
                issues.append(
                    {
                        "code": "MISSING_VOICE_ID",
                        "file": rel_app,
                        "priority": "P0",
                        "message": f"Locale '{lang}' is missing a voice identifier in synthesizeSpeechConfigs.",
                        "recommended": (
                            f"Set '{lang}' voice to '{suggested_voice}' (verify it "
                            "exists for this locale) or your custom voice key."
                        ),
                    }
                )
            elif "Chirp3-HD" not in voice and voice not in custom_voice_names:
                issues.append(
                    {
                        "code": "NON_CHIRP3_HD_VOICE",
                        "file": rel_app,
                        "priority": "P1",
                        "message": (
                            f"Locale '{lang}' uses voice='{voice}'; expected a "
                            "*-Chirp3-HD-* or customVoiceSamples voice. --remediate "
                            "reports this without replacing it unless --prebuilt-voice "
                            "is passed."
                        ),
                        "recommended": (
                            f"Use '{suggested_voice}' (default prebuilt voice is "
                            f"'{DEFAULT_PREBUILT_PERSONA}'; run --remediate "
                            "--prebuilt-voice to apply across locales, and verify "
                            "it exists for this locale) or bind a custom voice."
                        ),
                    }
                )

        return {"passed": len(issues) == 0, "issues": issues}

    def _text_units(self) -> list[dict[str, Any]]:
        """Returns instruction, tool, and callback text units to scan.

        Each unit has `label`, `kind` ("instruction", "tool", or "callback"),
        `text`, and `segments`: [(start_line, text)] limited to string
        literals for Python files so code is never mistaken for prose.
        """
        units: list[dict[str, Any]] = []
        for source in self._instruction_sources():
            units.append(
                {
                    "label": source["label"],
                    "kind": "instruction",
                    "text": source["text"],
                    "segments": [(1, source["text"])],
                }
            )
        for path in self._tool_and_callback_files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = self._rel(path)
            if path.suffix == ".json":
                text = _decode_json_angle_escapes(text)
            units.append(
                {
                    "label": rel,
                    "kind": "callback" if "_callbacks/" in rel else "tool",
                    "text": text,
                    "segments": (
                        _python_string_segments(text)
                        if path.suffix == ".py"
                        else [(1, text)]
                    ),
                }
            )
        return units

    @staticmethod
    def _placement_fix_counts(text: str) -> tuple[int, int]:
        """Counts pause commas and style-tag moves --remediate would make."""
        pause_commas = 0
        style_moves = 0
        for m in QUOTED_SPAN_RE.finditer(_mask_tag_quotes(text)):
            _, pc, sm = _relocate_utterance_tags_and_punctuate_pauses(
                m.group(0)
            )
            pause_commas += pc
            style_moves += sm
        return pause_commas, style_moves

    @staticmethod
    def _token_issues(unit: dict[str, Any]) -> list[dict[str, Any]]:
        """Finds unevaluated angle tags and unrecognized bracket tokens."""
        issues: list[dict[str, Any]] = []
        rel = unit["label"]
        closed = {
            " ".join(n.split()).lower()
            for n in re.findall(
                r"</\s*([A-Za-z][\w \-]{0,30})\s*>", unit["text"]
            )
        }
        seen_angle: dict[str, dict[str, Any]] = {}
        seen_bracket: dict[str, dict[str, Any]] = {}
        for start_line, seg in unit["segments"]:
            for m in ANGLE_TOKEN_RE.finditer(seg):
                name = _normalize_tag_inner(m.group(1))
                if (
                    name in ALL_RECOGNIZED_AUDIO_TAGS
                    or name in TRANSLATED_TAG_MAP
                    or name in NON_AUDIO_ELEMENT_NAMES
                    or name in closed
                    or _is_negated(seg, m.start())
                ):
                    continue
                line_start = seg.rfind("\n", 0, m.start()) + 1
                line_end = seg.find("\n", m.end())
                line = seg[line_start : line_end if line_end != -1 else None]
                if _is_meta_documentation_line(line):
                    continue
                lineno = start_line + seg.count("\n", 0, m.start())
                if name in seen_angle:
                    seen_angle[name]["count"] += 1
                    continue
                suggestion = _suggest_tag(name)
                seen_angle[name] = {
                    "code": "UNEVALUATED_V2_TAG",
                    "file": rel,
                    "line": lineno,
                    "priority": "P1",
                    "count": 1,
                    "message": (
                        f"'<{name}>' in {rel}:{lineno} is not an evaluated "
                        "Composite V2 tag (or it is a placeholder); its "
                        "delivery is unknown and it may be read aloud."
                    ),
                    "recommended": (
                        f"Swap it for '<{suggestion}>' if the user agrees, or "
                        "keep it and note it as a remaining finding."
                        if suggestion
                        else "Replace it with an evaluated tag if the user "
                        "agrees, or keep it and note it as a remaining finding."
                    ),
                }
            for m in BRACKET_TOKEN_RE.finditer(seg):
                inner = m.group(1)
                line_start = seg.rfind("\n", 0, m.start()) + 1
                line_end = seg.find("\n", m.end())
                line = seg[line_start : line_end if line_end != -1 else None]
                if (
                    LEGACY_SQUARE_TAG_RE.fullmatch(f"[{inner}]")
                    or inner.lower() in TRANSLATED_TAG_MAP
                    or seg[m.end() : m.end() + 1] == "("
                    or _is_label_context(seg[line_start : m.start()])
                    or _is_meta_documentation_line(line)
                    or _is_negated(seg, m.start())
                ):
                    continue
                close = difflib.get_close_matches(
                    inner, KNOWN_TAG_WORDS, n=1, cutoff=0.7
                )
                if not close or inner in seen_bracket:
                    continue
                target = close[0]
                if target in REMAP_V2:
                    target = REMAP_V2[target][0] or target
                elif target in UNEVALUATED_TAG_SUGGESTIONS:
                    target = UNEVALUATED_TAG_SUGGESTIONS[target]
                lineno = start_line + seg.count("\n", 0, m.start())
                seen_bracket[inner] = {
                    "code": "UNRECOGNIZED_BRACKET_TOKEN",
                    "file": rel,
                    "line": lineno,
                    "priority": "P1",
                    "message": (
                        f"'[{inner}]' in {rel}:{lineno} looks like a misspelled "
                        f"audio tag (closest: <{target}>). Square brackets are "
                        "read aloud far more often in Composite V2."
                    ),
                    "recommended": (
                        f"If it is an audio tag, write '<{target}>'. If it is "
                        "a placeholder, leave it as is."
                    ),
                }
        for item in seen_angle.values():
            if item["count"] > 1:
                item["message"] += f" ({item['count']} occurrences)"
            issues.append(item)
        issues.extend(seen_bracket.values())
        return issues

    @staticmethod
    def _ending_turn_issue(
        rel: str, lineno: int, sounds: list[str], detail: str
    ) -> dict[str, Any]:
        tags = ", ".join(sorted(set(sounds)))
        return {
            "code": "SIGH_ON_ENDING_OR_TRANSFER_TURN",
            "file": rel,
            "line": lineno,
            "priority": "P1",
            "message": (
                f"{tags} in {rel}:{lineno} is used on a turn that ends the "
                f"session or transfers/escalates the call ({detail}). Composite "
                "V2 guidance: <sigh>, <sighs>, and <exhales> are fine in an "
                "empathetic moment, but never on ending or transfer turns."
            ),
            "recommended": (
                "Open that turn with a tone tag such as <calm><empathetic> "
                "instead. This is a product decision: confirm with the owner, "
                "and update callbacks and tests that assert the old line."
            ),
        }

    def _ending_turn_sound_issues(
        self, unit: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Finds <sigh>/<sighs>/<exhales> on ending or transfer turns."""
        issues: list[dict[str, Any]] = []
        rel = unit["label"]
        if unit["kind"] == "instruction":
            for lineno, line in enumerate(unit["text"].splitlines(), start=1):
                if _is_meta_documentation_line(line):
                    continue
                sounds = _non_negated_ending_sounds(line)
                if sounds and _has_ending_context(line):
                    issues.append(
                        self._ending_turn_issue(
                            rel, lineno, sounds, "same step as an ending tool"
                        )
                    )
        elif unit["kind"] == "tool":
            parts = rel.split("/")
            tool_name = parts[1] if len(parts) > 1 else rel
            if not ENDING_TOOL_NAME_RE.search(tool_name):
                return issues
            for start_line, seg in unit["segments"]:
                for offset, line in enumerate(seg.splitlines()):
                    sounds = _non_negated_ending_sounds(line)
                    if sounds:
                        issues.append(
                            self._ending_turn_issue(
                                rel,
                                start_line + offset,
                                sounds,
                                f"example for the '{tool_name}' tool",
                            )
                        )
        else:
            try:
                tree = ast.parse(unit["text"])
            except SyntaxError:
                return issues
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    continue
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if not any(ENDING_CONST_NAME_RE.search(n) for n in names):
                    continue
                hits = [
                    (c.lineno, c.value)
                    for c in ast.walk(node.value)
                    if isinstance(c, ast.Constant)
                    and isinstance(c.value, str)
                    and _non_negated_ending_sounds(c.value)
                ]
                if hits:
                    sounds = [
                        s
                        for _, value in hits
                        for s in _non_negated_ending_sounds(value)
                    ]
                    issues.append(
                        self._ending_turn_issue(
                            rel,
                            hits[0][0],
                            sounds,
                            f"{len(hits)} line(s) in {names[0]}",
                        )
                    )
        return issues

    def audit_audio_tags_v2(self) -> dict[str, Any]:
        """Audits instructions, tools, and callbacks for Composite V2 tag compliance."""
        issues: list[dict[str, Any]] = []
        units = self._text_units()
        instruction_text = "\n".join(
            u["text"] for u in units if u["kind"] == "instruction"
        )

        if any(
            u["kind"] == "instruction" for u in units
        ) and not V2_ANGLE_TAG_RE.search(instruction_text):
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

        for unit in units:
            rel = unit["label"]
            text = unit["text"]
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _is_meta_documentation_line(line):
                    continue
                lower_line = line.lower()

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

                for match in TRANSLATED_AUDIO_TAG_RE.finditer(line):
                    if line[match.end() : match.end() + 1] == "(":
                        continue
                    if match.group(0).startswith("[") and (
                        match.group(1).isupper()
                        or _is_label_context(line[: match.start()])
                    ):
                        continue
                    if _is_negated(line, match.start()):
                        continue
                    norm_inner = " ".join(match.group(1).split()).lower()
                    target = TRANSLATED_TAG_MAP.get(norm_inner, "short pause")
                    issues.append(
                        {
                            "code": "TRANSLATED_AUDIO_TAG",
                            "file": rel,
                            "line": lineno,
                            "priority": "P0",
                            "message": (
                                f"Found translated non-English audio tag '{match.group(0)}' "
                                f"in {rel}:{lineno}. Composite V2 only recognizes English tag "
                                "keywords and reads translated tags aloud."
                            ),
                            "recommended": (
                                f"Run `audit_agent_v2.py --remediate` or replace "
                                f"'{match.group(0)}' with '<{target}>'."
                            ),
                        }
                    )

                if (
                    "instead of" not in lower_line
                    and "rather than" not in lower_line
                ):
                    masked_line = _mask_tag_quotes(line)
                    for span_match in UTTERANCE_SPAN_RE.finditer(masked_line):
                        if _is_negated(line, span_match.start()):
                            continue
                        span_inner = _unmask_tag_quotes(
                            span_match.group(0)[1:-1]
                        )
                        cleaned_span = re.sub(r"<[^<>]+>", " ", span_inner)
                        cleaned_span = re.sub(r"\{[^{}]+\}", " ", cleaned_span)
                        cleaned_span = re.sub(r"/[^/\s]+/", " ", cleaned_span)
                        if len(DEVANAGARI_CHAR_RE.findall(cleaned_span)) >= 3:
                            latin_words = LATIN_WORD_RE.findall(cleaned_span)
                            if latin_words:
                                words_str = ", ".join(sorted(set(latin_words)))
                                issues.append(
                                    {
                                        "code": "MIXED_LATIN_IN_DEVANAGARI_UTTERANCE",
                                        "file": rel,
                                        "line": lineno,
                                        "priority": "P1",
                                        "message": (
                                            f"Quoted Devanagari (Hindi) utterance in {rel}:{lineno} "
                                            f"mixes Latin script word(s) ({words_str}) with Devanagari. "
                                            "In Composite V2, Latin acronyms or words inside Devanagari "
                                            "sentences can trigger mid-sentence English code-switching "
                                            "when the session starts in an English locale."
                                        ),
                                        "recommended": (
                                            "Write brand names and acronyms phonetically in Devanagari "
                                            "script (e.g., 'वाई-फाई' instead of 'Wi-Fi', 'सिम' instead "
                                            "of 'SIM') so the spoken clause stays in one script."
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
                                f"{rel}:{lineno}. This causes an unnatural double pause."
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

                if (
                    PROSODY_TAG_RE.search(line)
                    and _reorder_prosody_first(line)[1]
                ):
                    issues.append(
                        {
                            "code": "PROSODY_NOT_FIRST_IN_STACK",
                            "file": rel,
                            "line": lineno,
                            "priority": "P1",
                            "message": (
                                f"A <prosody rate> tag follows another tag in {rel}:{lineno}. "
                                'Put prosody first in a stack: <prosody rate="70%"><empathetic>.'
                            ),
                            "recommended": "Run --remediate to move prosody to the front.",
                        }
                    )

            issues.extend(self._token_issues(unit))
            if unit["kind"] == "instruction" or (
                unit["kind"] == "tool" and rel.endswith(".py")
            ):
                pause_commas, style_moves = self._placement_fix_counts(text)
                if pause_commas or style_moves:
                    issues.append(
                        {
                            "code": "MID_SENTENCE_TAG_PLACEMENT",
                            "file": rel,
                            "priority": "P1",
                            "message": (
                                f"{rel} has {pause_commas} mid-sentence pause tag(s) "
                                f"without punctuation and {style_moves} mid-sentence "
                                "style tag(s) inside quoted example replies. Tags "
                                "between clauses are read aloud more often."
                            ),
                            "recommended": (
                                "Run --remediate to add the commas and move style "
                                "tags to the start of their sentence."
                            ),
                        }
                    )
            issues.extend(self._ending_turn_sound_issues(unit))

        return {"passed": len(issues) == 0, "issues": issues}

    def audit_text_normalization_and_pacing_v2(self) -> dict[str, Any]:
        """Audits tool-scoped text normalization (/IPA/, Scope: guard) and ALL-CAPS stress."""
        issues: list[dict[str, Any]] = []
        sources = self._instruction_sources()
        if not sources:
            return {"passed": True, "issues": []}

        combined_inst = "\n".join(s["text"] for s in sources)
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

    def audit_instruction_coverage(self) -> dict[str, Any]:
        """Lists the instructions audited and flags missing or broken pointers."""
        sources = self._instruction_sources()
        issues = list(self.pointer_issues)
        if not sources:
            issues.append(
                {
                    "code": "NO_INSTRUCTIONS_FOUND",
                    "file": self._rel(self.app_json_path),
                    "priority": "P0",
                    "message": (
                        "No global or agent instructions were found, so no "
                        "prompt checks could run."
                    ),
                    "recommended": (
                        "Point --workspace at the app directory that contains "
                        "app.json, global_instruction.txt, and agents/."
                    ),
                }
            )
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "covered": [s["label"] for s in sources],
        }

    def audit_structural_rules(self) -> dict[str, Any]:
        """Runs cxas_scrapi structural lint rules I015 and V104."""
        if Discovery is None:
            return {
                "passed": True,
                "skipped": True,
                "issues": [],
                "note": (
                    "cxas_scrapi is not importable, so I015/V104 were not "
                    "checked. Run this script with `uv run python`."
                ),
            }
        issues: list[dict[str, Any]] = []
        discovery = Discovery(
            app_dir=self.app_root,
            evals_dir=self.app_root / "evals",
        )
        config = LintConfig.load(self.app_root)
        context = build_context(
            project_root=self.app_root,
            config=config,
            discovery=discovery,
        )
        registry = build_registry()
        report = LintReport()
        run_rules(
            registry=registry,
            config=config,
            context=context,
            discovery=discovery,
            report=report,
            specific_rules={"I015", "V104"},
        )
        for r in report.results:
            issues.append(
                {
                    "code": r.rule_id,
                    "file": r.file,
                    "line": r.line,
                    "priority": "P0",
                    "message": r.message,
                    "recommended": r.fix_suggestion,
                }
            )
        return {"passed": len(issues) == 0, "issues": issues}

    @staticmethod
    def _inline_tag_pattern(source: str) -> re.Pattern[str] | None:
        """Compiles the callback's INLINE_TAG regex without running the code."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "INLINE_TAG"
                    for t in node.targets
                )
                and isinstance(node.value, ast.Call)
                and node.value.args
                and isinstance(node.value.args[0], ast.Constant)
                and isinstance(node.value.args[0].value, str)
            ):
                continue
            flags = 0
            flag_src = " ".join(ast.unparse(a) for a in node.value.args[1:])
            flag_src += " ".join(
                ast.unparse(k.value) for k in node.value.keywords
            )
            if "IGNORECASE" in flag_src or re.search(r"\bre\.I\b", flag_src):
                flags |= re.IGNORECASE
            try:
                return re.compile(node.value.args[0].value, flags)
            except re.error:
                return None
        return None

    def audit_after_model_callback_v2(self) -> dict[str, Any]:
        """Checks that callback INLINE_TAG regexes cover every tag in use."""
        issues: list[dict[str, Any]] = []
        used: set[str] = set()
        for source in self._instruction_sources():
            for m in re.finditer(r"<([^<>\n]{1,40})>", source["text"]):
                name = _normalize_tag_inner(m.group(1))
                if name in ALLOWED_V2_AUDIO_TAGS:
                    used.add(name)
        for cb in sorted(
            self.app_root.glob("agents/*/after_model_callbacks/*/*.py")
        ):
            rel = self._rel(cb)
            text = cb.read_text(encoding="utf-8")
            if "INLINE_TAG" not in text:
                continue
            pattern = self._inline_tag_pattern(text)
            if pattern is None:
                missing = [
                    t
                    for t in ("short pause", "empathetic", "breath")
                    if t not in text
                ]
            else:
                missing = sorted(
                    t for t in used if not pattern.search(f"<{t}>")
                )
            if missing:
                issues.append(
                    {
                        "code": "CALLBACK_MISSING_V2_TAG_REGEX",
                        "file": rel,
                        "priority": "P2",
                        "message": (
                            f"{rel} INLINE_TAG does not match tag(s) your "
                            f"instructions use: {', '.join('<' + t + '>' for t in missing)}."
                        ),
                        "recommended": (
                            "Add the missing tags to INLINE_TAG so text "
                            "normalization treats them as tags."
                        ),
                    }
                )
        return {"passed": len(issues) == 0, "issues": issues}

    def audit(self) -> dict[str, Any]:
        """Runs all Composite V2 audit passes and collects cross-app hand-offs."""
        app_data = self._read_app_json()
        passes = {
            "instruction_coverage": self.audit_instruction_coverage(),
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
            "skipped_passes": {
                name: p.get("note", "")
                for name, p in passes.items()
                if p.get("skipped")
            },
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

        coverage = audit_res["passes"].get("instruction_coverage", {})
        if coverage.get("covered"):
            lines.append(
                "\n**Instructions audited:** "
                + ", ".join(f"`{c}`" for c in coverage["covered"])
            )
        for name, note in audit_res.get("skipped_passes", {}).items():
            lines.append(f"\n> [!WARNING]\n> Pass `{name}` was SKIPPED: {note}")

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
                "\n✅ **No issues found by the automated checks.** Still run "
                "the manual contradiction check and a turn-by-turn audio ear-audit."
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
        """Applies automated Composite V2 remediation to app.json, agents, and prompts.

        With `dry_run`, nothing is written; unified diffs are collected in
        `planned_diffs`. With `backup`, each changed file is copied to
        `<name>.bak` first.
        """
        changes: list[str] = []
        removed_styles: list[str] = []
        app_data = self._read_app_json()
        sources = self._instruction_sources()
        if app_data:
            app_changed = False
            model_settings = app_data.setdefault("modelSettings", {})
            current_model = str(model_settings.get("model") or "")
            if current_model != TARGET_COMPOSITE_MODEL and (
                not current_model
                or current_model.startswith(COMPOSITE_MODEL_PREFIX)
            ):
                model_settings["model"] = TARGET_COMPOSITE_MODEL
                changes.append(
                    f"Set modelSettings.model to '{TARGET_COMPOSITE_MODEL}'"
                    + (f" (was '{current_model}')." if current_model else ".")
                )
                app_changed = True
            elif current_model != TARGET_COMPOSITE_MODEL:
                changes.append(
                    f"Left modelSettings.model '{current_model}' unchanged: it is "
                    "not a Composite model. Confirm with the owner before switching."
                )
            if not model_settings:
                app_data.pop("modelSettings")

            lang_settings = app_data.get("languageSettings") or {}
            default_lang = lang_settings.get("defaultLanguageCode") or "en-US"
            supported = lang_settings.get("supportedLanguageCodes") or []
            declared_langs = [
                default_lang,
                *[lang for lang in supported if isinstance(lang, str)],
            ]
            if any(
                isinstance(lang, str) and lang != default_lang
                for lang in supported
            ) and not lang_settings.get("enableMultilingualSupport"):
                app_data.setdefault("languageSettings", {})[
                    "enableMultilingualSupport"
                ] = True
                changes.append(
                    "Enabled languageSettings.enableMultilingualSupport for multi-locale app."
                )
                app_changed = True

            audio_cfg = app_data.setdefault("audioProcessingConfig", {})
            speech_cfgs = audio_cfg.setdefault("synthesizeSpeechConfigs", {})
            custom_samples = [
                s
                for s in (audio_cfg.get("customVoiceSamples") or [])
                if isinstance(s, dict)
            ]
            custom_voice_names = {
                str(s.get("name")).strip()
                for s in custom_samples
                if s.get("name")
            }
            target_custom_voice: str | None = None
            if self.bind_custom_voice:
                if (
                    self.bind_custom_voice.lower() == "auto"
                    and len(custom_voice_names) == 1
                ):
                    target_custom_voice = next(iter(custom_voice_names))
                elif self.bind_custom_voice in custom_voice_names:
                    target_custom_voice = self.bind_custom_voice
            target_prebuilt_seed = (
                f"en-US-Chirp3-HD-{self.prebuilt_voice}"
                if self.prebuilt_voice
                else None
            )
            persona_voice = (
                target_custom_voice
                or target_prebuilt_seed
                or self._persona_voice(
                    speech_cfgs, default_lang, custom_voice_names
                )
            )
            default_cfg = speech_cfgs.get(default_lang)
            default_rate = (
                default_cfg.get("speakingRate")
                if isinstance(default_cfg, dict)
                else None
            )
            for lang in declared_langs:
                if not isinstance(speech_cfgs.get(lang), dict):
                    new_cfg: dict[str, Any] = {
                        "voice": derive_locale_voice(
                            lang, persona_voice, custom_voice_names
                        )
                    }
                    if default_rate is not None:
                        new_cfg["speakingRate"] = default_rate
                    new_cfg["instruction"] = EMPTY_V2_STYLE
                    speech_cfgs[lang] = new_cfg
                    changes.append(
                        f"Added '{lang}' with voice '{new_cfg['voice']}' (same "
                        "persona as the default locale; verify it exists) and an "
                        "empty speaking style."
                    )
                    app_changed = True
                    continue
                cfg = speech_cfgs[lang]
                desired_prebuilt = (
                    derive_locale_voice(
                        lang, target_prebuilt_seed, custom_voice_names
                    )
                    if target_prebuilt_seed
                    else None
                )
                if (
                    target_custom_voice
                    and str(cfg.get("voice") or "").strip()
                    != target_custom_voice
                ):
                    old_v = str(cfg.get("voice") or "").strip()
                    cfg["voice"] = target_custom_voice
                    changes.append(
                        f"Bound '{lang}' voice to custom voice "
                        f"'{target_custom_voice}'"
                        + (f" (was '{old_v}')." if old_v else ".")
                    )
                    app_changed = True
                elif (
                    desired_prebuilt
                    and str(cfg.get("voice") or "").strip() != desired_prebuilt
                ):
                    old_v = str(cfg.get("voice") or "").strip()
                    cfg["voice"] = desired_prebuilt
                    changes.append(
                        f"Set '{lang}' voice to prebuilt voice "
                        f"'{desired_prebuilt}'"
                        + (f" (was '{old_v}')." if old_v else ".")
                    )
                    app_changed = True
                elif not str(cfg.get("voice") or "").strip():
                    cfg["voice"] = derive_locale_voice(
                        lang, persona_voice, custom_voice_names
                    )
                    changes.append(
                        f"Set missing voice for '{lang}' to '{cfg['voice']}'."
                    )
                    app_changed = True
                inst = str(cfg.get("instruction") or "")
                voice_val = str(cfg.get("voice") or "").strip()
                if inst.strip() and (
                    _has_legacy_v1_directors_note(inst)
                    or len(inst.split()) > MAX_RECOMMENDED_STYLE_WORDS
                    or voice_val in custom_voice_names
                ):
                    cfg["instruction"] = EMPTY_V2_STYLE
                    removed_styles.append(f"[{lang}] {inst.strip()}")
                    changes.append(
                        f"Cleared the speaking style for '{lang}' "
                        "(removed text is listed under removed_styles)."
                    )
                    app_changed = True

            for sample in audio_cfg.get("customVoiceSamples") or []:
                if not isinstance(sample, dict):
                    continue
                label = sample.get("name") or "customVoiceSamples[]"
                voice_inst = str(sample.get("voiceInstruction") or "")
                if voice_inst.strip():
                    sample["voiceInstruction"] = EMPTY_V2_STYLE
                    removed_styles.append(f"[{label}] {voice_inst.strip()}")
                    changes.append(
                        f"Cleared voiceInstruction in custom voice '{label}' "
                        "(removed text is listed under removed_styles)."
                    )
                    app_changed = True
                if sample.get("voiceInstructionMode") != "NO_INSTRUCTION":
                    sample["voiceInstructionMode"] = "NO_INSTRUCTION"
                    changes.append(
                        "Set voiceInstructionMode to 'NO_INSTRUCTION' on "
                        f"custom voice '{label}'."
                    )
                    app_changed = True
                if "previewAudioContent" in sample:
                    sample.pop("previewAudioContent", None)
                    changes.append(
                        "Removed transient previewAudioContent blob from "
                        f"custom voice '{label}'."
                    )
                    app_changed = True

            for source in sources:
                if source.get("json_path") == self.app_json_path:
                    migrated, notes = migrate_text_to_v2(source["text"])
                    if migrated != source["text"]:
                        app_data[source["json_key"]] = migrated
                        changes.append(
                            f"{source['label']}: {', '.join(notes)}."
                        )
                        app_changed = True
            if app_changed:
                self._write_app_json(app_data)

        # Upgrade older Composite overrides and inline agent instructions.
        for agent_json in self._agent_json_files():
            rel_agent = self._rel(agent_json)
            try:
                agent_data = json.loads(agent_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            agent_changed = False
            agent_ms = agent_data.get("modelSettings")
            if isinstance(agent_ms, dict):
                agent_model = str(agent_ms.get("model") or "")
                if (
                    agent_model.startswith(COMPOSITE_MODEL_PREFIX)
                    and agent_model != TARGET_COMPOSITE_MODEL
                ):
                    agent_ms["model"] = TARGET_COMPOSITE_MODEL
                    changes.append(
                        f"Upgraded {rel_agent} modelSettings.model from "
                        f"'{agent_model}' to '{TARGET_COMPOSITE_MODEL}'."
                    )
                    agent_changed = True
            for source in sources:
                if source.get("json_path") == agent_json:
                    migrated, notes = migrate_text_to_v2(source["text"])
                    if migrated != source["text"]:
                        agent_data[source["json_key"]] = migrated
                        changes.append(
                            f"{source['label']}: {', '.join(notes)}."
                        )
                        agent_changed = True
            if agent_changed:
                self._write_json(agent_json, agent_data)

        # In-place tag migration ([tag] -> <tag>, remap, closing tags, prosody
        # order, dedupe, pause punctuation, and quoted-utterance placement)
        # across instruction files and tool files.
        text_targets = [
            *(s["path"] for s in sources if "path" in s),
            *sorted(
                self.app_root.glob("tools/*/python_function/python_code.py")
            ),
            *sorted(self.app_root.glob("tools/*/*.json")),
        ]
        for path in text_targets:
            if not path.is_file():
                continue
            orig = path.read_text(encoding="utf-8")
            escaped = (
                path.suffix == ".json"
                and JSON_ANGLE_ESCAPE_RE.search(orig) is not None
                and "<" not in orig
                and ">" not in orig
            )
            source_text = _decode_json_angle_escapes(orig) if escaped else orig
            migrated, notes = migrate_text_to_v2(
                source_text, fix_placement=path.suffix != ".json"
            )
            if migrated != source_text:
                self._write_text(
                    path,
                    _encode_json_angle_escapes(migrated)
                    if escaped
                    else migrated,
                )
                changes.append(f"{self._rel(path)}: {', '.join(notes)}.")

        status = "REMEDIATED" if changes else "NO_CHANGES_NEEDED"
        if self.dry_run:
            status = "DRY_RUN" if self.planned_diffs else "NO_CHANGES_NEEDED"
        return {
            "status": status,
            "changes_applied": changes,
            "removed_styles": removed_styles,
            "planned_diffs": self.planned_diffs,
            "post_remediation_audit": None if self.dry_run else self.audit(),
        }


# Backwards-compatible aliases
CXASVoiceV2Auditor = VoiceAgentV2Auditor
AuditAgentV2 = VoiceAgentV2Auditor


def _emit(text: str, output: str | None) -> None:
    print(text)
    if output:
        pathlib.Path(output).write_text(text + "\n", encoding="utf-8")


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
        "--dry-run",
        action="store_true",
        help="With --remediate: print a unified diff of planned changes; write nothing.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="With --remediate: save <file>.bak before changing a file.",
    )
    voice_group = parser.add_mutually_exclusive_group()
    voice_group.add_argument(
        "--bind-custom-voice",
        type=str,
        default=None,
        help=(
            "With --remediate: bind synthesizeSpeechConfigs.<locale>.voice "
            "across declared locales to the given customVoiceSamples name "
            "(or 'auto' when exactly one custom voice exists)."
        ),
    )
    voice_group.add_argument(
        "--prebuilt-voice",
        nargs="?",
        const=DEFAULT_PREBUILT_PERSONA,
        default=None,
        metavar="PERSONA",
        help=(
            "With --remediate: set synthesizeSpeechConfigs.<locale>.voice "
            "across declared locales to '<locale>-Chirp3-HD-<PERSONA>' "
            f"(defaults to '{DEFAULT_PREBUILT_PERSONA}' when passed without "
            "a value or with 'auto')."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Also write the report or JSON output to this file.",
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
    auditor = VoiceAgentV2Auditor(
        workspace_dir=args.workspace,
        dry_run=args.dry_run,
        backup=args.backup,
        bind_custom_voice=args.bind_custom_voice,
        prebuilt_voice=args.prebuilt_voice,
    )

    if args.lint_report:
        if args.remediate and not args.dry_run:
            auditor.remediate()
        report = auditor.to_lint_report()
        if hasattr(report, "to_json") and args.json_output:
            print(report.to_json())
        elif hasattr(report, "print_and_exit"):
            report.print_and_exit(json_output=args.json_output)
        else:
            print(json.dumps(report, indent=2))
    elif args.remediate:
        res = auditor.remediate()
        if args.report and not args.dry_run:
            rep = auditor.generate_prioritized_report()
            _emit(
                json.dumps(rep, indent=2)
                if args.json_output
                else rep["markdown_report"],
                args.output,
            )
            if rep["overall_status"] != "PASSED":
                sys.exit(1)
            return
        if args.json_output:
            _emit(json.dumps(res, indent=2), args.output)
        elif args.dry_run:
            _emit(
                "\n".join(res["planned_diffs"]) or "No changes needed.",
                args.output,
            )
        else:
            out = [f"=== Composite V2 Remediation Status: {res['status']} ==="]
            out += [f" - {ch}" for ch in res["changes_applied"]]
            if res["removed_styles"]:
                out.append(
                    "Removed speaking styles (write a short speaker profile if needed):"
                )
                out += [f"   {s}" for s in res["removed_styles"]]
            out.append(
                f"Post-remediation audit status: {res['post_remediation_audit']['overall_status']}"
            )
            _emit("\n".join(out), args.output)
        post = res.get("post_remediation_audit")
        if post and post["overall_status"] != "PASSED":
            sys.exit(1)
    elif args.report:
        rep = auditor.generate_prioritized_report()
        _emit(
            json.dumps(rep, indent=2)
            if args.json_output
            else rep["markdown_report"],
            args.output,
        )
        if rep["overall_status"] != "PASSED":
            sys.exit(1)
    else:
        res = auditor.audit()
        if args.json_output:
            _emit(json.dumps(res, indent=2, default=str), args.output)
        else:
            out = [
                f"=== CXAS Composite V2 Audit: {res['overall_status']} ===",
                f"Total Issues: {res['total_issues']}",
            ]
            for name, pdata in res["passes"].items():
                status = (
                    "SKIP"
                    if pdata.get("skipped")
                    else ("PASS" if pdata["passed"] else "FAIL")
                )
                out.append(f" [{status}] {name}")
                if pdata.get("skipped"):
                    out.append(f"   ! {pdata.get('note', '')}")
                out += [
                    f"   * [{issue['priority']}] {issue['code']}: {issue['message']}"
                    for issue in pdata["issues"]
                ]
            if res.get("handoffs"):
                out.append(
                    f" [INFO] handoffs ({len(res['handoffs'])} cross-app/external hand-off(s) detected)"
                )
            _emit("\n".join(out), args.output)
        if res["overall_status"] != "PASSED":
            sys.exit(1)


if __name__ == "__main__":
    main()
