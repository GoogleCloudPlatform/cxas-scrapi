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

"""CXAS Voice Agent Auditor and Auto-Remediator for Gemini Composite v2.

Audits and remediates Google Cloud CX Agent Studio (CXAS) and Customer
Engagement Suite (CES) applications for the Gemini Composite v2 voice stack:

- ``modelSettings.model`` is ``gemini-composite-v2`` (temperature and
  ``speakingRate`` are not Composite v2 requirements and are never modified).
- Prebuilt Chirp 3 HD voices default to ``Aoede`` (``<locale>-Chirp3-HD-Aoede``)
  across declared locales; custom cloned voices (``customVoiceSamples[]``)
  validate ``gs://`` URIs, clear instructions, and set
  ``"voiceInstructionMode": "NO_INSTRUCTION"``.
- Speaking style instructions (``synthesizeSpeechConfigs.<lang>.instruction``)
  clear Director's Notes and stay empty or concise (up to 25 words, ~12-word
  speaker-identity profile for prebuilt voices; empty ``""`` for custom voices).
- Inline audio tags use English angle brackets (``<warm>``, ``<breath>``),
  poorly performing or translated tags are replaced, closing tags are rewritten
  or removed, and mid-sentence tag placement is checked and remediated.
- Conditional spoken text normalization enforces a ``Scope:`` tool-call guard
  whenever a normalization block is present and warns when configured locales
  fall outside platform runtime inline TTS text normalization.

The cxas-scrapi linter supplies discovery, context, reporting, and the
model-agnostic rules reused here (I015, V104, T014).
"""

from __future__ import annotations

import argparse
import ast
import copy
import difflib
import itertools
import json
import pathlib
import re
import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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

from cxas_scrapi.utils.linter import (  # noqa: E402
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    LintResult,
    Rule,
    RuleRegistry,
    Severity,
    build_context,
    build_registry,
    rule,
    run_rules,
)

TARGET_MODEL = "gemini-composite-v2"
COMPOSITE_MODEL_PREFIX = "gemini-composite"
DEFAULT_PREBUILT_PERSONA = "Aoede"
PROSODY_RESET_TAG = '<prosody rate="100%">'
SPEAKING_STYLE_WORD_LIMIT = 25

# ── Audio tag vocabulary (see references/tag_reference.md) ───────────────

PAUSE_TAGS: frozenset[str] = frozenset({"short pause", "long pause"})

# Reliably followed and natively supported on Gemini Composite v2.
RECOMMENDED_TAGS: frozenset[str] = frozenset(
    {
        "breath",
        "sigh",
        "sighs",
        "laugh",
        "chuckle",
        "chuckles",
        "exhales",
        "gasp",
        "throat-clearing",
        "whispers",
        "whispering",
        "fast",
        "faster",
        "short pause",
        "long pause",
        "empathetic",
        "warm",
        "calm",
        "clear",
        "positive",
        "determination",
        "interest",
        "enthusiasm",
        "relief",
    }
)

# Supported native tags; keep at a sentence start.
SUPPORTED_TAGS: frozenset[str] = frozenset(
    {
        "sympathetic",
        "tension",
        "anxiety",
        "awe",
        "surprised",
        "fearful",
        "negative",
        "disgusted",
        "heavy breath",
        "pant",
        "giggle",
        "snicker",
        "cackle",
        "cough",
        "sneeze",
        "snort",
        "tsk",
        "argh",
        "sob",
        "cry",
        "whimper",
        "groan",
        "yawn",
    }
)

ALLOWED_V2_TAGS: frozenset[str] = RECOMMENDED_TAGS | SUPPORTED_TAGS

# Poorly performing or non-canonical tag -> better-performing tag ("" deletes).
REMAP: dict[str, str] = {
    "happy": "positive",
    "hope": "warm",
    "adoration": "warm",
    "curiosity": "interest",
    "reassuring": "empathetic",
    "admiration": "positive",
    "crying": "sympathetic",
    "laughs": "chuckles",
    "neutral": "",
    "professional": "clear",
    "cautious": "calm",
    "alarm": "calm",
    "nervousness": "calm",
    "annoyance": "determination",
    "aggression": "determination",
    "slow": 'prosody rate="65%"',
    "slower": 'prosody rate="65%"',
    "medium pause": "short pause",
    "clears throat": "throat-clearing",
    "exhale": "exhales",
    "whisper": "whispers",
}

# Tags outside the evaluated customer-care set -> closest evaluated tag.
# Converted to angle brackets like any audio tag; swapping needs confirmation.
SUGGESTIONS: dict[str, str] = {
    "seriousness": "clear",
    "serious": "clear",
    "formal": "clear",
    "uhm": "short pause",
    "um": "short pause",
    "pause": "short pause",
    "deep breath": "breath",
    "excited": "enthusiasm",
    "excitement": "enthusiasm",
    "celebratory": "positive",
    "cheerful": "positive",
    "amusement": "chuckles",
    "laughing": "chuckles",
    "sad": "sympathetic",
    "apologetic": "empathetic",
    "friendly": "warm",
    "urgent": "determination",
    "confident": "determination",
    "confusion": "calm",
    "confused": "calm",
    "concerned": "calm",
    "panic": "calm",
    "anger": "determination",
    "frustration": "determination",
    "agitation": "determination",
    "curious": "interest",
    "extremely fast": "faster",
    "gasps": "gasp",
}

# Audio-tag-like words with no evaluated substitute.
UNEVALUATED_WORDS: frozenset[str] = frozenset(
    {
        "bored",
        "deadpan",
        "robotic",
        "sarcasm",
        "scared",
        "shouting",
        "sleepy",
        "yelling",
    }
)

UNEVALUATED_TAGS: frozenset[str] = frozenset(SUGGESTIONS) | UNEVALUATED_WORDS
KNOWN_TAGS: frozenset[str] = (
    ALLOWED_V2_TAGS | frozenset(REMAP) | UNEVALUATED_TAGS
)

# Common non-English translations of Composite v2 tags. Composite v2 requires
# English tag keywords across all spoken languages; translated tags leak aloud.
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
    re.I,
)

PROSODY_RE = re.compile(r'^prosody\s+rate\s*=\s*"?\s*([^"\s]+?)\s*"?$', re.I)
PROSODY_NOT_FIRST_RE = re.compile(
    r"(?P<before>(?:<(?!/|prosody\b)[a-z][a-z \-]{0,30}>[ \t]*)+)"
    r'(?P<pros><\s*prosody\s+rate\s*=\s*"?\d{1,3}\s*%"?\s*>)',
    re.I,
)
CLOSING_PROSODY_RE = re.compile(r"[ \t]*</\s*prosody\s*>", re.I)
BRACKET_TAG_RE = re.compile(r"\[(/?)([^\[\]\n]{1,40})\]")
ANGLE_TAG_RE = re.compile(r"<(/?)([^<>\n]{1,40})>")
BARE_BRACKET_WORD_RE = re.compile(r"\[([a-z]{3,20})\]")

# Bracketed labels for control logic, e.g. `user intent is [Negative]`.
LABEL_CONTEXT_RE = re.compile(
    r"\b(?:intents?|labels?|category|categories|classification|classified"
    r"\s+as|sentiment)\s*(?:is|was|are|=|==|:|of)?\s*$",
    re.I,
)
# A bracketed token inside a markup attribute value.
ATTRIBUTE_VALUE_RE = re.compile(
    r"<[A-Za-z_][\w:.\-]*\s[^<>\n]*=\s*(?:\"[^\"<>\n]*|'[^'<>\n]*)$"
)
DIRECTORS_NOTE_RE = re.compile(
    r"(?:^\s*#+\s*(?:director'?s\s+notes?|audio\s+profile|the\s+scene|"
    r"transcript\s*:)|<\s*agent_voice\b)",
    re.I | re.M,
)
OLD_FORMAT_RULE_RE = re.compile(
    r"square[\s-]+brackets?|bracketed\s+(?:delivery\s+|audio\s+)?tags?|"
    r"never\s+stack\s+(?:audio\s+|delivery\s+)?tags|do\s+not\s+bunch",
    re.I,
)
ANGLE_GUIDANCE_RE = re.compile(
    r"angle[\s-]+brackets?|<\s*instruction\s+id\s*=\s*[\"']audio tag format[\"']"
    r"|Affective_Delivery_and_Voice_Guidelines",
    re.I,
)
# The audio tag format block's own "never write a closing tag" bullet.
NO_CLOSING_RULE_RE = re.compile(
    r"never\s+(?:write|use|emit|add)\s+(?:a\s+|any\s+)?closing|"
    r"must\s+never\s+be\s+closed",
    re.I,
)

# Feeling-word-only speaking styles vs speaker-identity descriptors.
SPEAKER_IDENTITY_RE = re.compile(
    r"\b(?:woman|man|person|speaker|advisor|specialist|host|agent|voice|"
    r"baritone|tenor|alto|soprano|contralto|pitch|mid-pitch|mid-range|"
    r"pace|cadence|diction|drawl|accent|20s|30s|40s|50s|60s|twenties|"
    r"thirties|forties|fifties|young|middle-aged)\b",
    re.I,
)
FEELING_ONLY_WORDS: frozenset[str] = frozenset(
    {
        "warm",
        "friendly",
        "natural",
        "professional",
        "empathetic",
        "calm",
        "polite",
        "helpful",
        "kind",
        "cheerful",
        "positive",
        "reassuring",
        "sympathetic",
        "compassionate",
        "pleasant",
        "upbeat",
        "enthusiastic",
        "gentle",
        "patient",
        "approachable",
        "and",
        "or",
        "a",
        "an",
        "the",
        "very",
        "tone",
    }
)
ENGLISH_ACCENT_IN_STYLE_RE = re.compile(
    r"\b(?:american|australian|aussie|british|scottish|irish|canadian|"
    r"new\s+zealand|kiwi|cockney|english\s+accent|us\s+accent|uk\s+accent|"
    r"au\s+accent)\b",
    re.I,
)

# Prompt acoustic & script-purity checks.
ELLIPSIS_PAUSE_COLLISION_RE = re.compile(
    r"(?:\.\.\.|\u2026)\s*<(?:short|long)\s+pause>", re.I
)
ALL_CAPS_AFTER_VOCAL_TAG_RE = re.compile(
    r"<(?:exhales|sighs?)>\s+([A-Z]{2,})\b"
)
ALLOWED_CAPS_ACRONYMS: frozenset[str] = frozenset(
    {"SIM", "OK", "ID", "TV", "UK", "US", "AI", "EU", "UN"}
)
ENDING_SOUND_TAG_RE = re.compile(r"<\s*(?:sighs?|exhales)\s*>", re.I)
TOOL_REF_RE = re.compile(r"\{@TOOL:\s*([\w.\-]+)\s*\}")
ENDING_TOOL_NAME_RE = re.compile(
    r"end_session|transfer|escalat|hand_?off|live_?agent|human", re.I
)
ENDING_PHRASE_RE = re.compile(
    r"\b(?:end(?:s|ing)?\s+the\s+(?:call|session)"
    r"|transfer(?:s|ring)?\s+(?:the\s+call|you)"
    r"|put(?:ting)?\s+you\s+(?:straight\s+)?through"
    r"|escalat\w*"
    r"|hand(?:ing)?\s+you\s+(?:over|off)"
    r"|connect(?:ing)?\s+you\s+(?:to|with)\s+(?:a\s+|an\s+|our\s+)?"
    r"(?:human|live|specialist))",
    re.I,
)
NEGATION_RE = re.compile(
    r"\b(?:never|do\s+not|don't|avoid|must\s+not|instead\s+of|rather\s+than)\b",
    re.I,
)
CLAUSE_BOUNDARY_RE = re.compile(r"[.;:()!?\n]")

UTTERANCE_SPAN_RE = re.compile(
    r'"[^"\n]{3,400}"|(?<![\w])\'[^\'\n]{3,400}\'(?![\w])'
)
QUOTED_SPAN_RE = re.compile(r'(?<!")"(?!")(?:[^"\n]|\n(?!\s*\n)){0,400}"(?!")')
_TAG_QUOTE_SENTINEL = "\ue000"
TAG_WITH_QUOTES_RE = re.compile(r"<[^<>\n]*\"[^<>\n]*>")
DEVANAGARI_CHAR_RE = re.compile(r"[\u0900-\u097F]")
LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")

# Tag list deduplication helpers.
TAG_LIST_ITEM_RE = re.compile(r"([`'\"]?)<([^<>\n]{1,40})>\1")
TAG_LIST_SEP_RE = re.compile(r"^\s*(?:,|/|,?\s+(?:or|and))\s*$", re.I)
TAG_LIST_CONJ_RE = re.compile(r"\b(?:or|and)\b", re.I)
TWO_ITEM_COMMA_RE = re.compile(r"^\s*,\s*(?=(?:or|and)\b)", re.I)

# Conditional spoken text normalization checks.
NORMALIZATION_BLOCK_RE = re.compile(
    r'(?:<\s*instruction\s+id\s*=\s*["\']text normalization[^"\']*["\']\s*>'
    r"(.*?)</\s*instruction\s*>|"
    r"<\s*text_normalization\s*>(.*?)</\s*text_normalization\s*>)",
    re.I | re.DOTALL,
)
PLATFORM_TN_EXACT_LOCALES: frozenset[str] = frozenset(
    {"fr", "fr-ca", "es", "es-us"}
)

# Cross-app hand-offs.
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

CHIRP3_VOICE_RE = re.compile(
    r"^(?P<locale>[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)-Chirp3-HD-(?P<name>\w+)$"
)

TEXT_SUFFIXES = (".txt", ".json", ".jsonl", ".md", ".py", ".yaml", ".yml")
EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

# SDK lint rules reused for Composite v2 -> priority.
SDK_RULE_PRIORITY: dict[str, str] = {"I015": "P0", "V104": "P0", "T014": "P2"}

DEFAULT_VOICES: dict[str, str] = {
    "en": f"en-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-us": f"en-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-au": f"en-AU-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-ca": f"en-CA-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-gb": f"en-GB-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "en-in": f"en-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-us": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-es": f"es-ES-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "es-mx": f"es-US-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr": f"fr-FR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr-fr": f"fr-FR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "fr-ca": f"fr-CA-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "de": f"de-DE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "de-de": f"de-DE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "it": f"it-IT-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "it-it": f"it-IT-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ja": f"ja-JP-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ja-jp": f"ja-JP-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ko": f"ko-KR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ko-kr": f"ko-KR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pt": f"pt-BR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pt-br": f"pt-BR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh-cn": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn-cn": f"cmn-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "cmn-tw": f"cmn-TW-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "zh-tw": f"cmn-TW-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "yue": f"yue-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "yue-cn": f"yue-CN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl": f"nl-NL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl-nl": f"nl-NL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "nl-be": f"nl-BE-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "hi": f"hi-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "hi-in": f"hi-IN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pl": f"pl-PL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "pl-pl": f"pl-PL-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ro": f"ro-RO-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "ro-ro": f"ro-RO-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "tr": f"tr-TR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "tr-tr": f"tr-TR-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "vi": f"vi-VN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
    "vi-vn": f"vi-VN-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
}


def _norm_locale(locale: str) -> str:
    return locale.strip().lower().replace("_", "-")


def is_platform_tn_supported_locale(locale: str) -> bool:
    """Returns True if platform runtime inline TTS normalization covers locale.

    Composite v2 runtime inline normalization covers English (`en`, `en-*`),
    Canadian/generic French (`fr`, `fr-CA`), and US/generic Spanish (`es`,
    `es-US`). Other locales (such as `es-ES`, `es-MX`, `fr-FR`, `de-DE`,
    `cmn-CN`, `hi-IN`, `ja-JP`, `ko-KR`, `pt-BR`) are not covered at runtime.
    """
    norm = _norm_locale(locale)
    if not norm:
        return True
    return (
        norm == "en"
        or norm.startswith("en-")
        or norm in PLATFORM_TN_EXACT_LOCALES
    )


def get_default_voice(locale: str) -> str:
    """Returns the default Chirp3-HD-Aoede voice for a locale code."""
    norm = _norm_locale(locale)
    return DEFAULT_VOICES.get(
        norm,
        DEFAULT_VOICES.get(
            norm.split("-")[0],
            f"{locale}-Chirp3-HD-{DEFAULT_PREBUILT_PERSONA}",
        ),
    )


def derive_locale_voice(
    locale: str,
    persona_voice: str | None,
    custom_voice_names: set[str] | None = None,
) -> str:
    """Returns a voice for `locale` preserving the app's prebuilt or custom persona."""
    clean_persona = str(persona_voice or "").strip()
    match = CHIRP3_VOICE_RE.match(clean_persona)
    if match:
        fallback_locale = get_default_voice(locale).split("-Chirp3-HD-")[0]
        return f"{fallback_locale}-Chirp3-HD-{match.group('name')}"
    if custom_voice_names and clean_persona in custom_voice_names:
        return clean_persona
    return get_default_voice(locale)


def normalize_tag(inner: str) -> str:
    """Lowercases a tag body and collapses whitespace and JSON escapes."""
    collapsed = re.sub(r"\s+", " ", inner.replace('\\"', '"')).strip()
    prosody = PROSODY_RE.match(collapsed)
    if prosody:
        rate = prosody.group(1)
        if not rate.endswith("%") and rate.replace(".", "", 1).isdigit():
            rate = f"{rate}%"
        return f'prosody rate="{rate}"'
    return collapsed.lower()


def is_audio_tag(name: str) -> bool:
    return name in KNOWN_TAGS or bool(PROSODY_RE.match(name))


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _is_label_context(content: str, start: int) -> bool:
    prefix = content[content.rfind("\n", 0, start) + 1 : start]
    return bool(
        LABEL_CONTEXT_RE.search(prefix) or ATTRIBUTE_VALUE_RE.search(prefix)
    )


def _is_negated(text: str, pos: int, window: int = 120) -> bool:
    """Returns True if the clause before `pos` negates what follows."""
    prefix = text[max(0, pos - window) : pos]
    cut = max((m.end() for m in CLAUSE_BOUNDARY_RE.finditer(prefix)), default=0)
    return bool(NEGATION_RE.search(prefix[cut:]))


def _is_meta_documentation_line(line: str) -> bool:
    lower = line.lower()
    return (
        "never write" in lower
        or "never use square brackets" in lower
        or "inline_tag" in lower
        or "audio_tag_regex" in lower
        or "bracket_tag_re" in lower
    )


def _mask_tag_quotes(text: str) -> str:
    if _TAG_QUOTE_SENTINEL in text:
        return text
    return TAG_WITH_QUOTES_RE.sub(
        lambda m: m.group(0).replace('"', _TAG_QUOTE_SENTINEL), text
    )


def _unmask_tag_quotes(text: str) -> str:
    return text.replace(_TAG_QUOTE_SENTINEL, '"')


def _speech_configs(app_data: dict[str, Any]) -> dict[str, Any]:
    audio = app_data.get("audioProcessingConfig")
    if not isinstance(audio, dict):
        return {}
    cfgs = audio.get("synthesizeSpeechConfigs")
    return cfgs if isinstance(cfgs, dict) else {}


def _custom_voice_samples(app_data: dict[str, Any]) -> list[dict[str, Any]]:
    audio = app_data.get("audioProcessingConfig")
    if not isinstance(audio, dict):
        return []
    samples = audio.get("customVoiceSamples")
    if not isinstance(samples, list):
        return []
    return [s for s in samples if isinstance(s, dict)]


def _custom_voice_names(app_data: dict[str, Any]) -> set[str]:
    return {
        str(s.get("name")).strip()
        for s in _custom_voice_samples(app_data)
        if str(s.get("name") or "").strip()
    }


def _declared_languages(app_data: dict[str, Any]) -> list[str]:
    settings = app_data.get("languageSettings")
    if not isinstance(settings, dict):
        return []
    langs = [settings.get("defaultLanguageCode")]
    supported = settings.get("supportedLanguageCodes")
    if isinstance(supported, list):
        langs.extend(supported)
    seen: dict[str, str] = {}
    for lang in langs:
        if isinstance(lang, str) and lang.strip():
            seen.setdefault(_norm_locale(lang), lang.strip())
    return list(seen.values())


def _all_configured_locales(app_data: dict[str, Any]) -> list[str]:
    locales = _declared_languages(app_data)
    seen = {_norm_locale(loc) for loc in locales}
    for key in _speech_configs(app_data):
        if isinstance(key, str) and key.strip():
            norm = _norm_locale(key)
            if norm not in seen:
                seen.add(norm)
                locales.append(key.strip())
    return locales or ["en-US"]


def _find_speech_config(cfgs: dict[str, Any], locale: str) -> Any:
    norm = _norm_locale(locale)
    for key in (norm, norm.split("-")[0]):
        for k, v in cfgs.items():
            if isinstance(k, str) and _norm_locale(k) == key:
                return v
    return None


def _persona_voice(
    cfgs: dict[str, Any],
    default_lang: str,
    custom_names: set[str],
) -> str:
    default_cfg = _find_speech_config(cfgs, default_lang)
    if (
        isinstance(default_cfg, dict)
        and str(default_cfg.get("voice") or "").strip()
    ):
        return str(default_cfg["voice"]).strip()
    for cfg in cfgs.values():
        if not isinstance(cfg, dict):
            continue
        voice = str(cfg.get("voice") or "").strip()
        if CHIRP3_VOICE_RE.match(voice) or voice in custom_names:
            return voice
    return ""


def _is_feeling_only_style(value: str) -> bool:
    if SPEAKER_IDENTITY_RE.search(value):
        return False
    tokens = [
        t for t in re.findall(r"[a-zA-Z]+(?:-[a-zA-Z]+)?", value.lower()) if t
    ]
    return bool(tokens) and all(t in FEELING_ONLY_WORDS for t in tokens)


def _style_fields(
    app_data: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any], str, str | None, bool]]:
    """Yields (label, owner, key, locale, is_custom_voice) for style fields."""
    custom_names = _custom_voice_names(app_data)
    for lang, cfg in _speech_configs(app_data).items():
        if isinstance(cfg, dict) and "instruction" in cfg:
            voice = str(cfg.get("voice") or "").strip()
            yield (
                f"synthesizeSpeechConfigs['{lang}'].instruction",
                cfg,
                "instruction",
                lang,
                bool(voice and voice in custom_names),
            )
    for i, sample in enumerate(_custom_voice_samples(app_data)):
        if "voiceInstruction" in sample:
            name = str(sample.get("name") or "").strip()
            label = (
                f"customVoiceSamples['{name}'].voiceInstruction"
                if name
                else f"customVoiceSamples[{i}].voiceInstruction"
            )
            yield label, sample, "voiceInstruction", None, True

    stack: list[tuple[str, Any]] = [("", app_data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("directorsNote", "directors_note"):
                    yield f"{path}{k}", node, k, None, False
                else:
                    stack.append((f"{path}{k}.", v))
        elif isinstance(node, list):
            stack.extend((path, v) for v in node)


def _load_json(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _scalar_edits(
    old: Any, new: Any, key: str = ""
) -> Iterator[tuple[str, Any, Any]] | None:
    """Yields (key, old, new) for changed scalars; None if keys were added."""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) != set(new):
            return None
        edits = []
        for k in old:
            sub = _scalar_edits(old[k], new[k], k)
            if sub is None:
                return None
            edits.extend(sub)
        return iter(edits)
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return None
        edits = []
        for o, n in zip(old, new, strict=True):
            sub = _scalar_edits(o, n, key)
            if sub is None:
                return None
            edits.extend(sub)
        return iter(edits)
    return iter([(key, old, new)] if old != new else [])


def _minimal_json_edit(raw: str, old: Any, new: Any) -> str:
    """Rewrites only the changed JSON values, keeping the file's formatting.

    Falls back to a full 2-space re-serialization when a value cannot be
    located unambiguously or keys were added/removed.
    """
    edits = _scalar_edits(old, new)
    patched: str | None = raw
    for key, old_val, new_val in edits or []:
        pattern = re.compile(
            r'("' + re.escape(key) + r'"\s*:\s*)'
            r'("(?:[^"\\]|\\.)*"|-?[\d.eE+-]+|true|false|null)'
        )
        hits = (
            [
                m
                for m in pattern.finditer(patched)
                if json.loads(m.group(2)) == old_val
            ]
            if key and patched is not None
            else []
        )
        if len(hits) != 1 or patched is None:
            patched = None
            break
        m = hits[0]
        patched = (
            patched[: m.start(2)]
            + json.dumps(new_val, ensure_ascii=False)
            + patched[m.end(2) :]
        )
    if edits is not None and patched is not None:
        try:
            if json.loads(patched) == new:
                return patched
        except json.JSONDecodeError:
            pass
    return json.dumps(new, indent=2, ensure_ascii=False) + "\n"


def _rel(file_path: pathlib.Path, context: LintContext) -> str:
    try:
        return str(file_path.relative_to(context.project_root))
    except ValueError:
        return str(file_path)


# ── Audio tag scanning & migration ───────────────────────────────────────


@dataclass
class TagMatch:
    """An audio tag found in a file."""

    start: int
    end: int
    name: str
    bracket: bool
    closing: bool
    translated_target: str | None = None


def iter_audio_tags(content: str) -> Iterator[TagMatch]:
    """Yields audio tags in square or angle brackets, skipping non-tags."""
    seen_spans: set[tuple[int, int]] = set()
    for pattern, bracket in ((BRACKET_TAG_RE, True), (ANGLE_TAG_RE, False)):
        for m in pattern.finditer(content):
            raw_inner = m.group(2)
            if bracket and raw_inner.strip().isupper():
                continue  # [BOOKING_ID] style placeholder
            name = normalize_tag(raw_inner)
            closing = bool(m.group(1))
            translated = TRANSLATED_TAG_MAP.get(name) if not closing else None
            if not (
                is_audio_tag(name)
                or translated is not None
                or (closing and name == "prosody")
            ):
                continue
            prev = content[m.start() - 1] if m.start() else ""
            nxt = content[m.end()] if m.end() < len(content) else ""
            if bracket and (prev.isalnum() or prev in ("_", ")") or nxt == "("):
                continue  # code subscript or markdown link
            if _is_label_context(content, m.start()):
                continue
            if translated is not None and _is_negated(content, m.start()):
                continue
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            yield TagMatch(
                m.start(),
                m.end(),
                name,
                bracket,
                closing,
                translated_target=translated,
            )


def _target_tag(name: str, translated_target: str | None = None) -> str:
    """Returns the Composite v2 tag body for a tag ("" means delete)."""
    if translated_target is not None:
        return translated_target
    prosody = PROSODY_RE.match(name)
    if prosody:
        rate = prosody.group(1)
        if not rate.endswith("%") and rate.replace(".", "", 1).isdigit():
            rate = f"{rate}%"
        return f'prosody rate="{rate}"'
    return REMAP.get(name, name)


def _inside_double_quotes(
    content: str, start: int, escaped: bool = False
) -> bool:
    """Whether `start` sits inside a "..." span on its line."""
    masked = _mask_tag_quotes(content)
    line = masked[masked.rfind("\n", 0, start) + 1 : start]
    pattern = r'\\"' if escaped else r'(?<!\\)["\u201c\u201d]'
    return len(re.findall(pattern, line)) % 2 == 1


def _rebuild_tag_list(
    line: str, items: list[re.Match[str]], drop: list[bool]
) -> str:
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
        inner = normalize_tag(match.group(2))
        if not is_audio_tag(inner):
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
            inner = normalize_tag(item.group(2))
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


def _reorder_prosody_first(line: str) -> tuple[str, int]:
    """Moves <prosody rate="N%"> to the front of a tag stack."""
    moved = 0

    def _swap(m: re.Match[str]) -> str:
        nonlocal moved
        before_tags = re.findall(r"<([^<>]+)>", m.group("before"))
        if not all(is_audio_tag(normalize_tag(t)) for t in before_tags):
            return m.group(0)
        moved += 1
        return m.group("pros") + m.group("before").rstrip()

    return PROSODY_NOT_FIRST_RE.sub(_swap, line), moved


def _relocate_utterance_tags_and_punctuate_pauses(
    utterance: str,
) -> tuple[str, int, int]:
    """Inserts commas before mid-sentence pauses and lifts style tags to sentence start."""
    if len(utterance) < 4 or "<" not in utterance:
        return utterance, 0, 0

    inner = utterance[1:-1]
    pause_commas = 0
    style_moves = 0

    def _punct_pause(m: re.Match[str]) -> str:
        nonlocal pause_commas
        pause_commas += 1
        return f"{m.group(1)}, {m.group(2)}"

    inner = re.sub(
        r"([A-Za-z0-9\u00C0-\u024F\u0900-\u0963\u0966-\u097F\u3040-\u30FF"
        r"\u4E00-\u9FFF\uAC00-\uD7AF])[ \t]+(<(?:short|long)\s+pause>)",
        _punct_pause,
        inner,
        flags=re.I,
    )

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
            normalize_tag(m.group(1))
            for m in re.finditer(r"<([^>]+)>", leading_part)
        }
        lifted_tags: list[str] = []

        def _lift_style_tag(
            m: re.Match[str],
            existing_set: set[str] = existing_leading,
            lifted_list: list[str] = lifted_tags,
        ) -> str:
            nonlocal style_moves
            tag_name = normalize_tag(m.group(1))
            if tag_name not in ALLOWED_V2_TAGS or tag_name in PAUSE_TAGS:
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
            joiner = "" if prefix.endswith(">") else (" " if prefix else "")
            sent = prefix + joiner + "".join(lifted_tags) + " " + new_rest
        rebuilt_parts.append(sent + delim)

    return f'"{"".join(rebuilt_parts)}"', pause_commas, style_moves


def migrate_tags(
    content: str, json_escaped: bool = False
) -> tuple[str, list[str]]:
    """Converts square/translated tags, remaps poor tags, and fixes placement."""
    changes: list[str] = []
    out: list[str] = []
    pos = 0
    for tag in sorted(iter_audio_tags(content), key=lambda t: t.start):
        line_start = content.rfind("\n", 0, tag.start) + 1
        line_end = content.find("\n", tag.end)
        line = content[line_start : line_end if line_end != -1 else None]
        if _is_meta_documentation_line(line):
            continue

        start, end = tag.start, tag.end
        if tag.closing:
            if NO_CLOSING_RULE_RE.search(line):
                continue
            if tag.name == "prosody":
                remainder = line[tag.end - line_start :]
                spoken_after = re.sub(r"<[^<>]*>", "", remainder)
                quote_delim = '\\"' if json_escaped else '"'
                if re.search(
                    r"[A-Za-z0-9\u00C0-\uFFFF]",
                    spoken_after.split(quote_delim)[0],
                ):
                    reset = PROSODY_RESET_TAG
                    if json_escaped:
                        reset = reset.replace('"', '\\"')
                    if start > 0 and content[start - 1] == " ":
                        new = reset
                    else:
                        new = " " + reset
                    if content[end : end + 1].isalnum():
                        new += " "
                    changes.append(
                        f"L{_line_of(content, start)}: "
                        f"{content[tag.start : tag.end]} -> {reset}"
                    )
                    out.append(content[pos:start])
                    out.append(new)
                    pos = end
                    continue
            if (
                start > pos
                and content[start - 1] == " "
                and content[end : end + 1] in ("", " ", ".", ",", "\n")
            ):
                start -= 1
            changes.append(
                f"L{_line_of(content, tag.start)}: "
                f"{content[tag.start : tag.end]} -> (removed)"
            )
            out.append(content[pos:start])
            pos = end
            continue

        if (
            not tag.bracket
            and tag.name not in REMAP
            and tag.translated_target is None
        ):
            continue

        target = _target_tag(tag.name, tag.translated_target)
        if (
            tag.translated_target is None
            and tag.name not in REMAP
            and not PROSODY_RE.match(tag.name)
        ):
            target = content[start + 1 : end - 1].strip()
        if '"' in target and json_escaped:
            target = target.replace('"', '\\"')
        new = f"<{target}>" if target else ""
        if not new:
            if (
                start > pos
                and content[start - 1] in ("'", "`")
                and content[end : end + 1] == content[start - 1]
            ):
                start -= 1
                end += 1
                if content[end : end + 2] == ", ":
                    end += 2
                elif start >= pos + 2 and content[start - 2 : start] == ", ":
                    start -= 2
            elif content[end : end + 1] == " ":
                end += 1
        changes.append(
            f"L{_line_of(content, start)}: {content[tag.start : tag.end]}"
            f" -> {new or '(removed)'}"
        )
        out.append(content[pos:start])
        out.append(new)
        pos = end
    out.append(content[pos:])
    migrated = "".join(out)

    # Reorder <prosody> to the front of tag stacks and deduplicate tag lists.
    reordered_lines: list[str] = []
    for lineno, raw_line in enumerate(migrated.splitlines(keepends=True), 1):
        if _is_meta_documentation_line(raw_line):
            reordered_lines.append(raw_line)
            continue
        cur_line, moves = _reorder_prosody_first(raw_line)
        if moves:
            changes.append(f"L{lineno}: moved <prosody> first in tag stack")
        cur_line = _deduplicate_tag_lists_in_line(cur_line)
        reordered_lines.append(cur_line)
    migrated = "".join(reordered_lines)
    migrated = re.sub(
        r"<([a-z \-]{2,30})>(?:\s*(?:,|/|\bor\b|\band\b)?\s*<\1>)+",
        r"<\1>",
        migrated,
    )

    # Fix mid-sentence pause punctuation and style-tag placement in quoted
    # utterances for non-JSON text files.
    if not json_escaped:
        pause_commas = 0
        style_moves = 0

        def _rewrite_utterance(m: re.Match[str]) -> str:
            nonlocal pause_commas, style_moves
            rewritten, pc, sm = _relocate_utterance_tags_and_punctuate_pauses(
                m.group(0)
            )
            pause_commas += pc
            style_moves += sm
            return rewritten

        migrated = _unmask_tag_quotes(
            QUOTED_SPAN_RE.sub(_rewrite_utterance, _mask_tag_quotes(migrated))
        )
        if pause_commas:
            changes.append(
                f"Inserted comma before {pause_commas} mid-sentence pause tag(s)"
            )
        if style_moves:
            changes.append(
                f"Moved {style_moves} mid-sentence style tag(s) to sentence start"
            )

    return migrated, changes


# ── Composite v2 lint rules ──────────────────────────────────────────────
#
# Registered with the scrapi linter. They are model-agnostic so that they
# also flag apps (and agent-level overrides) still on Composite v1.


@rule("config")
class CompositeV2Model(Rule):
    id = "CV201"
    name = "composite-v2-model"
    description = f"modelSettings.model must be {TARGET_MODEL}"
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if data is None:
            return []
        settings = data.get("modelSettings") or {}
        model = settings.get("model") if isinstance(settings, dict) else None
        is_app = file_path.name == "app.json"
        if model == TARGET_MODEL or (model is None and not is_app):
            return []
        rel = _rel(file_path, context)
        if isinstance(model, str) and not model.startswith(
            COMPOSITE_MODEL_PREFIX
        ):
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"modelSettings.model is '{model}', a non-Composite"
                        " model. Confirm with the user before upgrading."
                    ),
                    severity=Severity.WARNING,
                    fix=f"Ask the user; then set it to '{TARGET_MODEL}'",
                )
            ]
        return [
            self.make_result(
                file=rel,
                message=f"modelSettings.model is '{model}'.",
                fix=f"Set modelSettings.model to '{TARGET_MODEL}'",
            )
        ]


def _invalid_style_detail(
    key: str,
    value: Any,
    locale: str | None = None,
    is_custom_voice: bool = False,
) -> tuple[str, Severity, bool] | None:
    """Returns (reason, severity, clear_in_remediate) or None if valid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if key in ("directorsNote", "directors_note") or DIRECTORS_NOTE_RE.search(
        text
    ):
        return (
            "holds a Director's Note or <agent_voice> DSP block",
            Severity.ERROR,
            True,
        )
    if key == "voiceInstruction":
        return (
            f"has a {len(text.split())}-word voiceInstruction (custom voice"
            " reference audio already defines vocal identity; text"
            " descriptions cause speaker drift)",
            Severity.WARNING,
            True,
        )
    if is_custom_voice:
        return (
            f"sets a non-empty instruction ('{text}') on a locale bound to a"
            " custom cloned voice, competing with the reference audio",
            Severity.WARNING,
            True,
        )
    word_count = len(text.split())
    if word_count > SPEAKING_STYLE_WORD_LIMIT:
        return (
            f"has {word_count} words (keep empty or concise, up to"
            f" {SPEAKING_STYLE_WORD_LIMIT} words — ideally a ~12-word speaker"
            " profile)",
            Severity.WARNING,
            True,
        )
    if (
        locale
        and not _norm_locale(locale).startswith("en")
        and ENGLISH_ACCENT_IN_STYLE_RE.search(text)
    ):
        m = ENGLISH_ACCENT_IN_STYLE_RE.search(text)
        term = m.group(0) if m else "English"
        return (
            f"contains an English-locale accent descriptor ('{term}') on"
            f" non-English locale '{locale}', forcing a foreign accent",
            Severity.WARNING,
            False,
        )
    if _is_feeling_only_style(text):
        return (
            f"uses feeling words ('{text}') instead of describing who the"
            " speaker is (age, timbre, pitch, pace); steer emotions with"
            " inline <...> tags",
            Severity.WARNING,
            True,
        )
    return None


@rule("config")
class CompositeV2SpeakingStyle(Rule):
    id = "CV203"
    name = "composite-v2-speaking-style"
    description = (
        "Director's Notes must be cleared; prebuilt speaking styles must be"
        f" empty or concise (<= {SPEAKING_STYLE_WORD_LIMIT} words, ~12-word"
        " speaker profile) and custom voice styles must be empty"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if data is None:
            return []
        results = []
        rel = _rel(file_path, context)
        for label, owner, key, locale, is_custom in _style_fields(data):
            detail = _invalid_style_detail(
                key, owner.get(key), locale=locale, is_custom_voice=is_custom
            )
            if detail is None:
                continue
            reason, severity, _ = detail
            if key in ("directorsNote", "directors_note") or is_custom:
                fix = f'Clear {label} to ""'
            elif (
                locale
                and not _norm_locale(locale).startswith("en")
                and ENGLISH_ACCENT_IN_STYLE_RE.search(str(owner.get(key) or ""))
            ):
                fix = (
                    f"Remove the English accent descriptor from {label} or"
                    f' clear {label} to ""'
                )
            else:
                fix = (
                    f'Clear {label} to "" or use a ~12-word speaker profile'
                    f" (<= {SPEAKING_STYLE_WORD_LIMIT} words)"
                )
            results.append(
                self.make_result(
                    file=rel,
                    severity=severity,
                    message=(
                        f"{label} {reason}. Long speaking style instructions"
                        " and Director's Notes conflict with inline audio"
                        " tags on Composite v2."
                    ),
                    fix=fix,
                )
            )
        return results


@rule("config")
class CompositeV2VoiceParity(Rule):
    id = "CV204"
    name = "composite-v2-voice-parity"
    description = (
        "Every declared language needs a valid synthesizeSpeechConfigs voice"
        " and multi-locale enablement"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if file_path.name != "app.json" or data is None:
            return []
        rel = _rel(file_path, context)
        results: list[LintResult] = []

        lang_settings = data.get("languageSettings") or {}
        default_lang = "en-US"
        supported: list[Any] = []
        if isinstance(lang_settings, dict):
            default_lang = (
                str(lang_settings.get("defaultLanguageCode") or "").strip()
                or "en-US"
            )
            raw_sup = lang_settings.get("supportedLanguageCodes")
            if isinstance(raw_sup, list):
                supported = raw_sup
            if any(
                isinstance(x, str)
                and _norm_locale(x) != _norm_locale(default_lang)
                for x in supported
            ) and not lang_settings.get("enableMultilingualSupport"):
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.WARNING,
                        message=(
                            "languageSettings.supportedLanguageCodes declares"
                            f" additional locales ({supported}), but"
                            " enableMultilingualSupport is not true."
                        ),
                        fix=(
                            "Set languageSettings.enableMultilingualSupport"
                            " to true (--remediate fixes this)"
                        ),
                    )
                )

        cfgs = _speech_configs(data)
        custom_names = _custom_voice_names(data)
        declared = _declared_languages(data) or [default_lang]
        if not cfgs:
            suggested = get_default_voice(default_lang)
            results.append(
                self.make_result(
                    file=rel,
                    severity=Severity.ERROR,
                    message=(
                        "audioProcessingConfig.synthesizeSpeechConfigs is"
                        " missing or empty."
                    ),
                    fix=(
                        f"Configure synthesizeSpeechConfigs for {declared}"
                        f" (default prebuilt voice '{suggested}'; --remediate"
                        " scaffolds declared locales)"
                    ),
                )
            )
            return results

        persona = _persona_voice(cfgs, default_lang, custom_names)
        for lang in declared:
            cfg = _find_speech_config(cfgs, lang)
            suggested = derive_locale_voice(lang, persona, custom_names)
            if not isinstance(cfg, dict):
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Declared language '{lang}' has no"
                            " synthesizeSpeechConfigs entry."
                        ),
                        fix=(
                            f"Add '{lang}' with voice '{suggested}'"
                            " (--remediate scaffolds this)"
                        ),
                    )
                )
                continue

            if str(cfg.get("voiceSampleGcsUri") or "").strip():
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Locale '{lang}' sets deprecated inline"
                            " synthesizeSpeechConfigs.voiceSampleGcsUri, which"
                            " falls back to a prebuilt voice at runtime."
                        ),
                        fix=(
                            "Define the custom voice in"
                            " audioProcessingConfig.customVoiceSamples[] and"
                            " reference its name in voice"
                        ),
                    )
                )

            voice = str(cfg.get("voice") or "").strip()
            if not voice:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Declared language '{lang}' has no"
                            " synthesizeSpeechConfigs voice."
                        ),
                        fix=(
                            f"Set voice to '{suggested}' (--remediate fills"
                            " missing voices)"
                        ),
                    )
                )
            elif "Chirp3-HD" not in voice and voice not in custom_names:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.WARNING,
                        message=(
                            f"Locale '{lang}' uses legacy voice '{voice}';"
                            " expected a *-Chirp3-HD-* voice or"
                            " customVoiceSamples name."
                        ),
                        fix=(
                            f"Use '{suggested}' (run --remediate"
                            " --prebuilt-voice to apply Aoede across locales)"
                            " or bind a custom voice"
                        ),
                    )
                )
        return results


@rule("config")
class CompositeV2CustomVoice(Rule):
    id = "CV205"
    name = "composite-v2-custom-voice"
    description = (
        "Custom cloned voices must set gs:// URIs and"
        " voiceInstructionMode=NO_INSTRUCTION"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if file_path.name != "app.json" or data is None:
            return []
        samples = _custom_voice_samples(data)
        if not samples:
            return []

        rel = _rel(file_path, context)
        cfgs = _speech_configs(data)
        referenced = {
            str(cfg.get("voice")).strip()
            for cfg in cfgs.values()
            if isinstance(cfg, dict) and str(cfg.get("voice") or "").strip()
        }
        results: list[LintResult] = []
        for sample in samples:
            name = str(sample.get("name") or "").strip()
            label = name or "customVoiceSamples[]"
            missing_uris: list[str] = []
            if (
                not str(sample.get("voiceSampleGcsUri") or "")
                .strip()
                .startswith("gs://")
            ):
                missing_uris.append("voiceSampleGcsUri")
            if (
                not str(sample.get("consentAudioGcsUri") or "")
                .strip()
                .startswith("gs://")
            ):
                missing_uris.append("consentAudioGcsUri")
            if missing_uris:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Custom voice '{label}' is missing a valid"
                            f" 'gs://' URI for {', '.join(missing_uris)}"
                            " (causes silent fallback to a prebuilt voice)."
                        ),
                        fix=(
                            f"Set valid 'gs://...' URIs for"
                            f" {', '.join(missing_uris)} on '{label}'"
                        ),
                    )
                )

            mode = str(sample.get("voiceInstructionMode") or "").strip()
            if mode != "NO_INSTRUCTION":
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Custom voice '{label}' has"
                            f" voiceInstructionMode='{mode or 'unspecified'}'"
                            " instead of 'NO_INSTRUCTION' (can auto-generate"
                            " <agent_voice> or restore prior instructions on"
                            " push)."
                        ),
                        fix=(
                            'Set "voiceInstructionMode": "NO_INSTRUCTION"'
                            " (--remediate fixes this)"
                        ),
                    )
                )

            if str(sample.get("previewAudioContent") or "").strip():
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.INFO,
                        message=(
                            f"Custom voice '{label}' embeds a transient"
                            " previewAudioContent audio blob in app.json."
                        ),
                        fix=(
                            "Remove previewAudioContent (--remediate strips"
                            " this automatically)"
                        ),
                    )
                )

            if name and cfgs and name not in referenced:
                results.append(
                    self.make_result(
                        file=rel,
                        severity=Severity.INFO,
                        message=(
                            f"Custom voice '{name}' is not referenced by any"
                            " synthesizeSpeechConfigs.<locale>.voice."
                        ),
                        fix=(
                            f"Run --remediate --bind-custom-voice '{name}'"
                            " (or 'auto'), or remove the unused custom voice"
                        ),
                    )
                )
        return results


class _AudioTagRule(Rule):
    """Base for rules that inspect inline audio tags in any text file."""

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        """Returns (message, fix) for a tag this rule flags, else None."""
        raise NotImplementedError

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results = []
        for tag in iter_audio_tags(content):
            line_start = content.rfind("\n", 0, tag.start) + 1
            line_end = content.find("\n", tag.end)
            line = content[line_start : line_end if line_end != -1 else None]
            if _is_meta_documentation_line(line):
                continue
            found = self.findings(tag, content, file_path)
            if found:
                results.append(
                    self.make_result(
                        file=_rel(file_path, context),
                        line=_line_of(content, tag.start),
                        message=found[0],
                        fix=found[1],
                    )
                )
        return results


@rule("instructions")
class CompositeV2BracketTag(_AudioTagRule):
    id = "CV210"
    name = "composite-v2-bracket-tag"
    description = "Audio tags must use English angle-bracket keywords"
    default_severity = Severity.ERROR

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if tag.closing:
            return None
        raw = content[tag.start : tag.end]
        if tag.translated_target is not None:
            return (
                f"Translated non-English audio tag {raw}; Composite v2"
                " requires English tag keywords.",
                f"Replace with <{tag.translated_target}>",
            )
        if not tag.bracket:
            return None
        target = _target_tag(tag.name)
        return (
            f"Square-bracket audio tag {raw}.",
            f"Replace with <{target}>" if target else "Remove the tag",
        )


@rule("instructions")
class CompositeV2TagToReplace(_AudioTagRule):
    id = "CV211"
    name = "composite-v2-tag-to-replace"
    description = "Poorly performing audio tags must be replaced"
    default_severity = Severity.ERROR

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if (
            tag.bracket
            or tag.closing
            or tag.translated_target is not None
            or tag.name not in REMAP
        ):
            return None
        target = REMAP[tag.name]
        return (
            f"<{tag.name}> performs poorly or is non-canonical on Composite v2.",
            f"Replace with <{target}>" if target else "Remove the tag",
        )


@rule("instructions")
class CompositeV2UnevaluatedTag(_AudioTagRule):
    id = "CV212"
    name = "composite-v2-unevaluated-tag"
    description = "Unevaluated or misspelled audio tags need user confirmation"
    default_severity = Severity.WARNING

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if (
            tag.bracket
            or tag.closing
            or tag.translated_target is not None
            or tag.name not in UNEVALUATED_TAGS
        ):
            return None
        suggestion = SUGGESTIONS.get(tag.name)
        return (
            f"<{tag.name}> was not evaluated on Composite v2.",
            f"Ask the user; closest evaluated tag is <{suggestion}>"
            if suggestion
            else "Ask the user whether to keep it",
        )

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results = super().check(file_path, content, context)
        if file_path.suffix not in (".txt", ".md"):
            return results
        rel = _rel(file_path, context)
        for m in BARE_BRACKET_WORD_RE.finditer(content):
            word = m.group(1).lower()
            if is_audio_tag(word) or word in TRANSLATED_TAG_MAP:
                continue
            prev = content[m.start() - 1] if m.start() else ""
            nxt = content[m.end()] if m.end() < len(content) else ""
            if prev.isalnum() or prev in ("_", ")") or nxt == "(":
                continue
            if _is_label_context(content, m.start()):
                continue
            close = difflib.get_close_matches(
                word, sorted(ALLOWED_V2_TAGS), n=1, cutoff=0.75
            )
            if close:
                results.append(
                    self.make_result(
                        file=rel,
                        line=_line_of(content, m.start()),
                        message=(
                            f"Unrecognized bracketed token '[{word}]' looks"
                            f" like a misspelled audio tag."
                        ),
                        fix=(
                            f"Convert to <{close[0]}> if it is an audio tag,"
                            " or leave it if it is a placeholder"
                        ),
                    )
                )
        return results


@rule("instructions")
class CompositeV2ClosingTag(_AudioTagRule):
    id = "CV213"
    name = "composite-v2-closing-tag"
    description = "Audio tags are point markers; never write closing tags"
    default_severity = Severity.ERROR

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        line_start = content.rfind("\n", 0, tag.start) + 1
        line_end = content.find("\n", tag.end)
        line = content[line_start : line_end if line_end != -1 else None]
        if not tag.closing or NO_CLOSING_RULE_RE.search(line):
            return None
        return (
            f"Closing audio tag {content[tag.start : tag.end]}.",
            'Delete it; use <prosody rate="100%"> to reset the pace'
            " (--remediate fixes this)",
        )


@rule("instructions")
class CompositeV2TagPlacement(_AudioTagRule):
    id = "CV214"
    name = "composite-v2-tag-placement"
    description = (
        "Style tags go at a sentence start (<prosody> first); pause tags"
        " follow punctuation"
    )
    default_severity = Severity.WARNING

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if tag.bracket or tag.closing or tag.translated_target is not None:
            return None
        before = content[content.rfind("\n", 0, tag.start) + 1 : tag.start]
        after = content[tag.end : tag.end + 2]
        escaped = file_path.suffix in (".json", ".jsonl")
        if (
            not re.search(
                r"[A-Za-z0-9\u00C0-\u024F\u0900-\u097F\u3040-\u30FF"
                r"\u4E00-\u9FFF\uAC00-\uD7AF] $",
                before,
            )
            or not re.match(
                r" [A-Za-z\u00C0-\u024F\u0900-\u097F\u3040-\u30FF"
                r"\u4E00-\u9FFF\uAC00-\uD7AF]",
                after,
            )
            or not _inside_double_quotes(content, tag.start, escaped)
        ):
            return None
        if tag.name in PAUSE_TAGS:
            return (
                f"<{tag.name}> follows a word without punctuation.",
                "Add a comma or other punctuation before the pause tag"
                " (--remediate fixes this in prompts)",
            )
        return (
            f"<{tag.name}> sits mid-sentence in a quoted utterance.",
            "Move the tag to the start of the sentence it colors"
            " (--remediate fixes this in prompts)",
        )

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results = super().check(file_path, content, context)
        rel = _rel(file_path, context)
        for lineno, line in enumerate(content.splitlines(), 1):
            if _is_meta_documentation_line(line):
                continue
            if _reorder_prosody_first(line)[1]:
                results.append(
                    self.make_result(
                        file=rel,
                        line=lineno,
                        message=(
                            "<prosody rate> follows another tag in a stack;"
                            " put <prosody rate> first."
                        ),
                        fix=(
                            'Move <prosody rate="N%"> to the front of the'
                            " stack (--remediate fixes this)"
                        ),
                    )
                )
        return results


@rule("instructions")
class CompositeV2PromptStyle(Rule):
    id = "CV215"
    name = "composite-v2-prompt-style"
    description = (
        "Prompts must not carry Director's Notes, old tag-format rules,"
        " ending-turn sighs, double pauses, or cross-script collisions"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results: list[LintResult] = []
        rel = _rel(file_path, context)
        for m in DIRECTORS_NOTE_RE.finditer(content):
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(content, m.start()),
                    severity=Severity.ERROR,
                    message=(
                        "Director's Note / Audio Profile text in a prompt:"
                        f" '{m.group(0).strip()}'."
                    ),
                    fix="Remove speaking-style presets from the prompt",
                )
            )
        for m in OLD_FORMAT_RULE_RE.finditer(content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_end = content.find("\n", m.end())
            line = content[line_start : line_end if line_end != -1 else None]
            if _is_negated(line, m.start() - line_start):
                continue
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(content, m.start()),
                    severity=Severity.ERROR,
                    message=f"Rule written for the old tag format: '{m[0]}'.",
                    fix="Rewrite it to match the audio tag format block",
                )
            )

        for lineno, line in enumerate(content.splitlines(), 1):
            lower = line.lower()
            if _is_meta_documentation_line(line):
                continue

            # 1. <sigh> / <sighs> / <exhales> on ending or transfer turns.
            ending_sounds = [
                m.group(0)
                for m in ENDING_SOUND_TAG_RE.finditer(line)
                if not _is_negated(line, m.start())
            ]
            if ending_sounds:
                has_ending = any(
                    ENDING_TOOL_NAME_RE.search(m.group(1))
                    and not _is_negated(line, m.start())
                    for m in TOOL_REF_RE.finditer(line)
                ) or any(
                    not _is_negated(line, m.start())
                    for m in ENDING_PHRASE_RE.finditer(line)
                )
                if has_ending:
                    unique_sounds = ", ".join(dict.fromkeys(ending_sounds))
                    results.append(
                        self.make_result(
                            file=rel,
                            line=lineno,
                            severity=Severity.WARNING,
                            message=(
                                f"Uses {unique_sounds} on a session-ending"
                                " or transfer/escalation turn."
                            ),
                            fix=(
                                "Replace <sigh>/<exhales> on closing or"
                                " transfer turns with <calm><empathetic>"
                            ),
                        )
                    )

            # 2. Ellipsis stacked immediately before a pause tag.
            if (
                "never stack" not in lower
                and "avoid" not in lower
                and "rather than" not in lower
                and ELLIPSIS_PAUSE_COLLISION_RE.search(line)
            ):
                results.append(
                    self.make_result(
                        file=rel,
                        line=lineno,
                        severity=Severity.WARNING,
                        message=(
                            "Ellipsis ('...') stacked immediately before a"
                            " pause tag causes an unnatural double pause."
                        ),
                        fix=(
                            "Use ', <short pause>' or '. <short pause>',"
                            " never '... <short pause>'"
                        ),
                    )
                )

            # 3. ALL-CAPS word right after <exhales> / <sigh>.
            if "never" not in lower:
                cap_match = ALL_CAPS_AFTER_VOCAL_TAG_RE.search(line)
                if (
                    cap_match
                    and cap_match.group(1) not in ALLOWED_CAPS_ACRONYMS
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            line=lineno,
                            severity=Severity.WARNING,
                            message=(
                                f"ALL-CAPS word '{cap_match.group(1)}' right"
                                " after <exhales>/<sigh> can cause a barked"
                                " opening syllable."
                            ),
                            fix=(
                                "Keep the first word after <exhales>/<sigh>"
                                " normal case and stress a word later in the"
                                " clause"
                            ),
                        )
                    )

            # 4. Mixed Latin tokens inside quoted Devanagari utterances.
            if "instead of" not in lower and "rather than" not in lower:
                masked = _mask_tag_quotes(line)
                for span in UTTERANCE_SPAN_RE.finditer(masked):
                    if _is_negated(line, span.start()):
                        continue
                    inner = _unmask_tag_quotes(span.group(0)[1:-1])
                    cleaned = re.sub(r"<[^<>]+>", " ", inner)
                    cleaned = re.sub(r"\{[^{}]+\}", " ", cleaned)
                    cleaned = re.sub(r"/[^/\s]+/", " ", cleaned)
                    if len(DEVANAGARI_CHAR_RE.findall(cleaned)) >= 3:
                        latin_words = LATIN_WORD_RE.findall(cleaned)
                        if latin_words:
                            words_str = ", ".join(sorted(set(latin_words)))
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=lineno,
                                    severity=Severity.WARNING,
                                    message=(
                                        "Quoted Devanagari utterance mixes"
                                        f" Latin word(s) ({words_str}), risking"
                                        " mid-sentence English code-switching."
                                    ),
                                    fix=(
                                        "Write brand names and acronyms"
                                        " phonetically in Devanagari script"
                                        " (e.g. 'वाई-फाई', 'सिम')"
                                    ),
                                )
                            )
        return results


@rule("instructions")
class CompositeV2GlobalGuidelines(Rule):
    id = "CV216"
    name = "composite-v2-global-guidelines"
    description = "Global instruction must carry the audio tag format block"
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if ANGLE_GUIDANCE_RE.search(content):
            return []
        return [
            self.make_result(
                file=_rel(file_path, context),
                message=(
                    "Global instruction has no angle-bracket audio tag"
                    " guidance."
                ),
                fix=(
                    "Merge the Affective Delivery and Voice Guidelines /"
                    " audio tag format block"
                ),
            )
        ]


@rule("instructions")
class CompositeV2TextNormalization(Rule):
    id = "CV217"
    name = "composite-v2-text-normalization"
    description = (
        "Spoken text normalization blocks must include a Scope: tool-call"
        " guard; locales outside platform runtime normalization should define"
        " target-language spoken normalization"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results: list[LintResult] = []
        rel = _rel(file_path, context)
        for m in NORMALIZATION_BLOCK_RE.finditer(content):
            body = m.group(1) or m.group(2) or ""
            if not re.search(r"\btool", body, re.I):
                results.append(
                    self.make_result(
                        file=rel,
                        line=_line_of(content, m.start()),
                        severity=Severity.ERROR,
                        message=(
                            "Spoken text normalization block lacks a"
                            " spoken-only Scope: guard excluding tool calls"
                            " and arguments, risking corrupted tool lookups."
                        ),
                        fix=(
                            "Add a Scope: paragraph stating normalization"
                            " applies ONLY to spoken words and NEVER to tool"
                            ' calls or arguments (pass "1234", not "one two'
                            ' three four")'
                        ),
                    )
                )
        return results


@rule("instructions")
class CompositeV2CallbackTagRegex(Rule):
    id = "CV218"
    name = "composite-v2-callback-tag-regex"
    description = (
        "after_model_callback INLINE_TAG regex should match every Composite v2"
        " audio tag used in instructions"
    )
    default_severity = Severity.INFO

    @staticmethod
    def _compile_inline_tag(source: str) -> re.Pattern[str] | None:
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
                flags |= re.I
            try:
                return re.compile(node.value.args[0].value, flags)
            except re.error:
                return None
        return None

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        del file_path, content, context
        return []

    def check_with_used_tags(
        self,
        file_path: pathlib.Path,
        content: str,
        context: LintContext,
        used_tags: set[str],
    ) -> list[LintResult]:
        if "INLINE_TAG" not in content or not used_tags:
            return []
        pattern = self._compile_inline_tag(content)
        if pattern is None:
            missing = [
                t
                for t in ("short pause", "empathetic", "breath")
                if t in used_tags and t not in content
            ]
        else:
            missing = sorted(
                t for t in used_tags if not pattern.search(f"<{t}>")
            )
        if not missing:
            return []
        formatted = ", ".join(f"<{t}>" for t in missing)
        return [
            self.make_result(
                file=_rel(file_path, context),
                severity=Severity.INFO,
                message=(
                    f"INLINE_TAG regex does not match Composite v2 tag(s) used"
                    f" in prompts: {formatted}."
                ),
                fix=(
                    "Add the missing tags to INLINE_TAG so callback text"
                    " normalization preserves them"
                ),
            )
        ]


CONFIG_RULES: list[Rule] = [
    CompositeV2Model(),
    CompositeV2SpeakingStyle(),
    CompositeV2VoiceParity(),
    CompositeV2CustomVoice(),
]
TEXT_RULES: list[Rule] = [
    CompositeV2BracketTag(),
    CompositeV2TagToReplace(),
    CompositeV2UnevaluatedTag(),
    CompositeV2ClosingTag(),
    CompositeV2TagPlacement(),
]
PROMPT_RULES: list[Rule] = [
    CompositeV2PromptStyle(),
    CompositeV2TextNormalization(),
]

RULE_PRIORITY: dict[str, str] = {
    **SDK_RULE_PRIORITY,
    "CV201": "P0",
    "CV203": "P0",
    "CV204": "P0",
    "CV205": "P0",
    "CV210": "P0",
    "CV211": "P0",
    "CV212": "P1",
    "CV213": "P0",
    "CV214": "P2",
    "CV215": "P0",
    "CV216": "P0",
    "CV217": "P0",
    "CV218": "P2",
}

PRIORITY_TITLES: dict[str, str] = {
    "P0": "🔴 Priority P0: Critical Voice & Synthesis Blockers (Fix First)",
    "P1": "🟡 Priority P1: Multi-Language, Speaking Style & Tag Hygiene",
    "P2": "🟢 Priority P2: Placement Review, Callbacks & Tool Pacing (User-Approved)",
}


def _priority_for_result(result: LintResult) -> str:
    """Maps a LintResult to P0/P1/P2 respecting per-finding severity overrides."""
    base = RULE_PRIORITY.get(result.rule_id, "P2")
    if base == "P2" or result.severity == Severity.INFO:
        return "P2"
    if result.severity == Severity.WARNING:
        return "P1"
    return base


class VoiceAgentAuditor:
    """Audits and remediates CXAS voice configurations for Composite v2."""

    def __init__(
        self,
        workspace_dir: pathlib.Path | str = ".",
        app_name: str | None = None,
        prebuilt_voice: str | None = None,
        bind_custom_voice: str | None = None,
        backup: bool = False,
    ):
        if prebuilt_voice and bind_custom_voice:
            msg = "Cannot specify both prebuilt_voice and bind_custom_voice."
            raise ValueError(msg)
        self.workspace_path = pathlib.Path(workspace_dir).resolve()
        self.app_name = app_name
        self.prebuilt_voice = prebuilt_voice
        self.bind_custom_voice = bind_custom_voice
        self.backup = backup
        self.discovery = Discovery(
            app_dir=self.workspace_path,
            evals_dir=self.workspace_path / "evals",
        )
        self.app_root = self.discovery.app_root or self.workspace_path
        self.app_json_path = self.discovery.discover_app_config() or (
            self.app_root / "app.json"
        )
        self.config = LintConfig.load(self.workspace_path)

    # ── Discovery helpers ──

    def _context(self) -> LintContext:
        return build_context(
            project_root=self.workspace_path,
            config=self.config,
            discovery=self.discovery,
            model_override=TARGET_MODEL,
        )

    def _read_app_data(self) -> dict[str, Any]:
        if not self.app_json_path.is_file():
            return {}
        try:
            return (
                _load_json(self.app_json_path.read_text(encoding="utf-8")) or {}
            )
        except OSError:
            return {}

    def config_files(self) -> list[pathlib.Path]:
        files = [self.app_json_path] if self.app_json_path.is_file() else []
        files.extend(self.discovery.discover_agent_configs().values())
        return files

    def prompt_files(self) -> list[pathlib.Path]:
        files = [
            p
            for p in self.discovery.discover_agents().values()
            if p.name == "instruction.txt"
        ]
        global_inst = self.discovery.discover_global_instruction()
        if global_inst:
            files.insert(0, global_inst)
        return files

    def callback_files(self) -> list[pathlib.Path]:
        seen: set[pathlib.Path] = set()
        files: list[pathlib.Path] = []
        for pattern in (
            "agents/*/after_model_callbacks/**/*.py",
            "callbacks/**/*.py",
        ):
            for p in sorted(self.app_root.glob(pattern)):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    files.append(p)
        return files

    def text_files(self) -> list[pathlib.Path]:
        """Every app file that may carry audio tags."""
        files = []
        for p in sorted(self.app_root.rglob("*")):
            parts = p.relative_to(self.app_root).parts
            if (
                p.is_file()
                and p.suffix in TEXT_SUFFIXES
                and not p.name.endswith(".bak")
                and not any(
                    part.startswith(".") or part in EXCLUDED_DIRS
                    for part in parts
                )
            ):
                files.append(p)
        return files

    def find_handoffs(self) -> list[dict[str, Any]]:
        """Collects informational cross-app and external hand-off references."""
        handoffs: list[dict[str, Any]] = []
        context = self._context()
        app_data = self._read_app_data()
        own_app = str(app_data.get("name") or self.app_name or "").strip()
        seen_resources: set[str] = set()
        for path in self.text_files():
            rel = _rel(path, context)
            if self.config.is_ignored(rel):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                for m in HANDOFF_RE.finditer(line):
                    key = m.group(1)
                    handoffs.append(
                        {
                            "type": "handoff",
                            "target_kind": key,
                            "file": rel,
                            "line": lineno,
                            "message": (
                                f"Hands the call to {HANDOFF_TARGETS[key]}."
                                " The destination runs its own session, voice,"
                                " and TTS model."
                            ),
                        }
                    )
                for m in APP_RESOURCE_RE.finditer(line):
                    resource = m.group(0)
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
                                f"References external CES app '{resource}'."
                                " Check whether that destination app needs its"
                                " own Composite v2 migration."
                            ),
                        }
                    )
        return handoffs

    # ── Audit ──

    def _run_sdk_rules(self, context: LintContext) -> list[LintResult]:
        """Runs reused SDK lint rules, enabled for Composite v2."""
        sdk = build_registry()
        registry = RuleRegistry()
        for rule_id in SDK_RULE_PRIORITY:
            sdk_rule = sdk.get(rule_id)
            if sdk_rule is not None:
                reused = copy.copy(sdk_rule)
                reused.models = None
                registry.register(reused)
        report = LintReport()
        run_rules(
            registry=registry,
            config=self.config,
            context=context,
            discovery=self.discovery,
            report=report,
        )
        for result in report.results:
            result.message = result.message.replace(
                "Gemini Composite V1", "Gemini Composite v2"
            )
        return report.results

    def _apply(
        self,
        rules: list[Rule],
        files: list[pathlib.Path],
        context: LintContext,
    ) -> list[LintResult]:
        results = []
        for path in files:
            if self.config.is_ignored(_rel(path, context)):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for r in rules:
                results.extend(r.check(path, content, context))
        return results

    def _workspace_tn_and_callback_results(
        self, context: LintContext
    ) -> list[LintResult]:
        """Runs workspace-wide locale text-normalization & callback checks."""
        results: list[LintResult] = []
        prompts = self.prompt_files()
        combined_prompts: list[str] = []
        used_tags: set[str] = set()
        for path in prompts:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            combined_prompts.append(text)
            for tag in iter_audio_tags(text):
                if (
                    not tag.bracket
                    and not tag.closing
                    and tag.name in ALLOWED_V2_TAGS
                    and not _is_negated(text, tag.start)
                ):
                    used_tags.add(tag.name)

        app_data = self._read_app_data()
        inline_global = app_data.get("globalInstruction")
        if (
            isinstance(inline_global, str)
            and "\n" in inline_global
            and not self.discovery.discover_global_instruction()
        ):
            combined_prompts.insert(0, inline_global)

        if combined_prompts:
            joined = "\n".join(combined_prompts)
            unsupported_locales = [
                loc
                for loc in _all_configured_locales(app_data)
                if not is_platform_tn_supported_locale(loc)
            ]
            if unsupported_locales and not NORMALIZATION_BLOCK_RE.search(
                joined
            ):
                global_inst = self.discovery.discover_global_instruction()
                target_file = (
                    _rel(global_inst, context)
                    if global_inst
                    else _rel(self.app_json_path, context)
                )
                results.append(
                    CompositeV2TextNormalization().make_result(
                        file=target_file,
                        severity=Severity.WARNING,
                        message=(
                            f"App configures locale(s) {unsupported_locales}"
                            " not covered by platform runtime inline TTS text"
                            " normalization (which covers en-*, fr/fr-CA, and"
                            " es/es-US), and prompts lack '<instruction"
                            ' id="text normalization for speech">\' .'
                        ).replace(" ' .", "'."),
                        fix=(
                            'Add <instruction id="text normalization for'
                            ' speech"> with a spoken-only Scope: tool-call'
                            " guard and target-language readouts for"
                            f" {unsupported_locales}"
                        ),
                    )
                )

        cb_rule = CompositeV2CallbackTagRegex()
        for cb_path in self.callback_files():
            if self.config.is_ignored(_rel(cb_path, context)):
                continue
            try:
                cb_text = cb_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            results.extend(
                cb_rule.check_with_used_tags(
                    cb_path, cb_text, context, used_tags
                )
            )
        return results

    def lint_results(self) -> list[LintResult]:
        """Runs every Composite v2 check and returns linter results."""
        context = self._context()
        results = self._apply(CONFIG_RULES, self.config_files(), context)
        results += self._apply(TEXT_RULES, self.text_files(), context)
        results += self._apply(PROMPT_RULES, self.prompt_files(), context)
        global_inst = self.discovery.discover_global_instruction()
        if global_inst:
            results += self._apply(
                [CompositeV2GlobalGuidelines()], [global_inst], context
            )
        else:
            app_data = self._read_app_data()
            inline_global = app_data.get("globalInstruction")
            if isinstance(inline_global, str) and "\n" in inline_global:
                results += CompositeV2GlobalGuidelines().check(
                    self.app_json_path, inline_global, context
                )
            else:
                results.append(
                    CompositeV2GlobalGuidelines().make_result(
                        file=_rel(self.app_json_path, context),
                        message="App has no global instruction.",
                        fix=(
                            "Create global_instruction.txt with the Affective"
                            " Delivery and Voice Guidelines and reference it"
                            " from app.json"
                        ),
                    )
                )
        results += self._workspace_tn_and_callback_results(context)
        results += self._run_sdk_rules(context)
        return results

    def audit(self) -> dict[str, Any]:
        """Returns all findings grouped by priority plus informational handoffs."""
        by_priority: dict[str, list[dict[str, Any]]] = {
            "P0": [],
            "P1": [],
            "P2": [],
        }
        for r in self.lint_results():
            priority = _priority_for_result(r)
            by_priority[priority].append(
                {
                    "code": r.rule_id,
                    "file": r.file,
                    "line": r.line,
                    "message": r.message,
                    "recommended": r.fix_suggestion,
                    "priority": priority,
                }
            )
        total = sum(len(v) for v in by_priority.values())
        return {
            "workspace": str(self.workspace_path),
            "app_json": (
                str(self.app_json_path)
                if self.app_json_path.is_file()
                else None
            ),
            "target_model": TARGET_MODEL,
            "overall_status": "PASSED" if total == 0 else "FAILED",
            "total_issues": total,
            "priority_counts": {k: len(v) for k, v in by_priority.items()},
            "issues": by_priority,
            "handoffs": self.find_handoffs(),
        }

    def to_lint_report(self) -> LintReport:
        report = LintReport()
        report.add_all(self.lint_results())
        return report

    def format_markdown_report(self, audit_res: dict[str, Any]) -> str:
        counts = audit_res["priority_counts"]
        handoffs = audit_res.get("handoffs") or []
        lines = [
            "# CXAS Composite v2 Voice Readiness Report\n",
            f"**Workspace:** `{self.workspace_path}`",
            f"**Target model:** `{TARGET_MODEL}`",
            f"**Overall Status:** `{audit_res['overall_status']}`"
            f" ({audit_res['total_issues']} issues)\n",
            "### Summary",
            f"- 🔴 **P0:** {counts['P0']}",
            f"- 🟡 **P1:** {counts['P1']}",
            f"- 🟢 **P2:** {counts['P2']}",
            f"- ℹ️ **Hand-offs (Informational):** {len(handoffs)}\n",
            "---",
        ]
        for priority, issues in audit_res["issues"].items():
            if not issues:
                continue
            lines += [
                f"\n## {PRIORITY_TITLES[priority]}\n",
                "| Code | Location | Issue | Recommended |",
                "| :--- | :--- | :--- | :--- |",
            ]
            for item in issues:
                loc = item["file"] + (
                    f":{item['line']}" if item["line"] else ""
                )
                cells = [
                    item["code"],
                    f"`{loc}`",
                    item["message"],
                    item["recommended"] or "",
                ]
                lines.append(
                    "| "
                    + " | ".join(c.replace("|", "\\|") for c in cells)
                    + " |"
                )
        if handoffs:
            lines += [
                "\n## ℹ️ Cross-App & External Hand-Offs (Informational)\n",
                "| Type | Target | Location | Note |",
                "| :--- | :--- | :--- | :--- |",
            ]
            for h in handoffs:
                loc = h["file"] + (f":{h['line']}" if h.get("line") else "")
                cells = [
                    h["type"],
                    f"`{h['target_kind']}`",
                    f"`{loc}`",
                    h["message"],
                ]
                lines.append(
                    "| "
                    + " | ".join(c.replace("|", "\\|") for c in cells)
                    + " |"
                )
        if not audit_res["total_issues"]:
            lines.append("\n🎉 **No issues detected.**")
        return "\n".join(lines)

    # ── Remediation ──

    def _backup_file(self, path: pathlib.Path, dry_run: bool) -> None:
        if self.backup and not dry_run and path.is_file():
            shutil.copy2(path, f"{path}.bak")

    def _patch_json(
        self,
        path: pathlib.Path,
        mutate: Callable[[dict[str, Any]], list[str]],
        dry_run: bool,
    ) -> list[str]:
        raw = path.read_text(encoding="utf-8")
        data = _load_json(raw)
        if data is None:
            return []
        old = copy.deepcopy(data)
        changes = mutate(data)
        if changes and not dry_run:
            self._backup_file(path, dry_run)
            path.write_text(
                _minimal_json_edit(raw, old, data), encoding="utf-8"
            )
        return changes

    @staticmethod
    def _upgrade_model(data: dict[str, Any], is_app: bool) -> list[str]:
        """Upgrades modelSettings.model to Composite v2 without touching temperature."""
        settings = data.get("modelSettings")
        if not isinstance(settings, dict):
            if not is_app:
                return []
            model = None
        else:
            model = settings.get("model")

        changes: list[str] = []
        if model != TARGET_MODEL and (
            (model is None and is_app)
            or (
                isinstance(model, str)
                and model.startswith(COMPOSITE_MODEL_PREFIX)
            )
        ):
            if not isinstance(settings, dict):
                settings = data["modelSettings"] = {}
            settings["model"] = TARGET_MODEL
            changes.append(f"modelSettings.model: {model} -> {TARGET_MODEL}")
        return changes

    def _remediate_voices_and_custom_samples(
        self, data: dict[str, Any]
    ) -> list[str]:
        """Scaffolds declared locales/voices (`Aoede` default) and custom voice guards."""
        changes: list[str] = []
        lang_settings = data.get("languageSettings")
        default_lang = "en-US"
        if isinstance(lang_settings, dict):
            default_lang = (
                str(lang_settings.get("defaultLanguageCode") or "").strip()
                or "en-US"
            )
            supported = lang_settings.get("supportedLanguageCodes") or []
            if any(
                isinstance(x, str)
                and _norm_locale(x) != _norm_locale(default_lang)
                for x in supported
            ) and not lang_settings.get("enableMultilingualSupport"):
                lang_settings["enableMultilingualSupport"] = True
                changes.append(
                    "languageSettings.enableMultilingualSupport: False -> True"
                )

        declared = _declared_languages(data) or [default_lang]
        audio_cfg = data.get("audioProcessingConfig")
        if not isinstance(audio_cfg, dict):
            audio_cfg = data["audioProcessingConfig"] = {}

        samples = _custom_voice_samples(data)
        custom_names = _custom_voice_names(data)
        for sample in samples:
            name = (
                str(sample.get("name") or "").strip() or "customVoiceSamples[]"
            )
            if sample.get("voiceInstructionMode") != "NO_INSTRUCTION":
                sample["voiceInstructionMode"] = "NO_INSTRUCTION"
                changes.append(
                    f"customVoiceSamples['{name}'].voiceInstructionMode"
                    " -> 'NO_INSTRUCTION'"
                )
            if "previewAudioContent" in sample:
                sample.pop("previewAudioContent", None)
                changes.append(
                    f"Removed previewAudioContent blob from"
                    f" customVoiceSamples['{name}']"
                )

        target_custom: str | None = None
        if self.bind_custom_voice:
            if (
                self.bind_custom_voice.lower() == "auto"
                and len(custom_names) == 1
            ):
                target_custom = next(iter(custom_names))
            elif self.bind_custom_voice in custom_names:
                target_custom = self.bind_custom_voice

        target_prebuilt_seed = (
            f"en-US-Chirp3-HD-{self.prebuilt_voice}"
            if self.prebuilt_voice
            else None
        )

        cfgs = audio_cfg.get("synthesizeSpeechConfigs")
        if not isinstance(cfgs, dict):
            cfgs = audio_cfg["synthesizeSpeechConfigs"] = {}

        persona = (
            target_custom
            or target_prebuilt_seed
            or _persona_voice(cfgs, default_lang, custom_names)
        )
        default_cfg = _find_speech_config(cfgs, default_lang)
        default_rate = (
            default_cfg.get("speakingRate")
            if isinstance(default_cfg, dict)
            else None
        )

        for lang in declared:
            existing = _find_speech_config(cfgs, lang)
            if not isinstance(existing, dict):
                voice_id = derive_locale_voice(lang, persona, custom_names)
                new_entry: dict[str, Any] = {"voice": voice_id}
                if default_rate is not None:
                    new_entry["speakingRate"] = default_rate
                new_entry["instruction"] = ""
                cfgs[lang] = new_entry
                changes.append(
                    f"Added synthesizeSpeechConfigs['{lang}'] with voice"
                    f" '{voice_id}'"
                )
                continue

            cur_voice = str(existing.get("voice") or "").strip()
            desired_prebuilt = (
                derive_locale_voice(lang, target_prebuilt_seed, custom_names)
                if target_prebuilt_seed
                else None
            )
            if target_custom and cur_voice != target_custom:
                existing["voice"] = target_custom
                changes.append(
                    f"synthesizeSpeechConfigs['{lang}'].voice:"
                    f" '{cur_voice}' -> '{target_custom}'"
                )
            elif desired_prebuilt and cur_voice != desired_prebuilt:
                existing["voice"] = desired_prebuilt
                changes.append(
                    f"synthesizeSpeechConfigs['{lang}'].voice:"
                    f" '{cur_voice}' -> '{desired_prebuilt}'"
                )
            elif not cur_voice:
                filled = derive_locale_voice(lang, persona, custom_names)
                existing["voice"] = filled
                changes.append(
                    f"synthesizeSpeechConfigs['{lang}'].voice: '' -> '{filled}'"
                )
        return changes

    @staticmethod
    def _clear_style(
        data: dict[str, Any], removed_styles: list[str]
    ) -> list[str]:
        changes = []
        for label, owner, key, locale, is_custom in list(_style_fields(data)):
            raw_val = owner.get(key)
            detail = _invalid_style_detail(
                key, raw_val, locale=locale, is_custom_voice=is_custom
            )
            if detail and detail[2]:
                snippet = str(raw_val).strip()
                owner[key] = ""
                removed_styles.append(f"{label}: {snippet}")
                changes.append(f'Cleared {label} to ""')
        return changes

    def remediate(self, dry_run: bool = False) -> dict[str, Any]:
        """Applies deterministic Composite v2 fixes in place."""
        changes: dict[str, list[str]] = {}
        removed_styles: list[str] = []
        context = self._context()

        for path in self.config_files():
            is_app = path == self.app_json_path

            def mutate(
                data: dict[str, Any], is_app: bool = is_app
            ) -> list[str]:
                found = self._upgrade_model(data, is_app)
                if is_app:
                    found += self._remediate_voices_and_custom_samples(data)
                return found + self._clear_style(data, removed_styles)

            found = self._patch_json(path, mutate, dry_run)
            if found:
                changes.setdefault(_rel(path, context), []).extend(found)

        for path in self.text_files():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            migrated, found = migrate_tags(
                content, json_escaped=path.suffix in (".json", ".jsonl")
            )
            if migrated != content:
                if not dry_run:
                    self._backup_file(path, dry_run)
                    path.write_text(migrated, encoding="utf-8")
                changes.setdefault(_rel(path, context), []).extend(found)

        return {
            "status": (
                ("DRY_RUN" if dry_run else "REMEDIATED")
                if changes
                else "NO_CHANGES_NEEDED"
            ),
            "changes_applied": changes,
            "removed_styles": removed_styles,
            "post_remediation_audit": self.audit(),
        }


def _emit_output(text: str, output_path: pathlib.Path | None) -> None:
    if output_path is not None:
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description=(
            "CXAS Composite v2 voice auditor and auto-remediator."
            " Without flags, prints an audit summary."
        )
    )
    parser.add_argument(
        "--workspace",
        "--app-dir",
        dest="workspace",
        default=".",
        help="Path to the CXAS app directory (the one with app.json).",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Optional CXAS / CES application full resource name.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print the prioritized (P0/P1/P2) Markdown readiness report.",
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="Apply deterministic fixes to local workspace files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --remediate, list the changes without writing files.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="With --remediate, save <file>.bak before modifying each file.",
    )
    voice_group = parser.add_mutually_exclusive_group()
    voice_group.add_argument(
        "--prebuilt-voice",
        nargs="?",
        const=DEFAULT_PREBUILT_PERSONA,
        default=None,
        metavar="PERSONA",
        help=(
            "With --remediate, set declared locales to <locale>-Chirp3-HD-PERSONA"
            f" (defaults to '{DEFAULT_PREBUILT_PERSONA}')."
        ),
    )
    voice_group.add_argument(
        "--bind-custom-voice",
        default=None,
        metavar="NAME_OR_AUTO",
        help=(
            "With --remediate, bind synthesizeSpeechConfigs.<locale>.voice"
            " across declared locales to a customVoiceSamples name (or 'auto')."
        ),
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="Optional file path to write the report or JSON output.",
    )
    parser.add_argument(
        "--json-output",
        "--json",
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
    auditor = VoiceAgentAuditor(
        args.workspace,
        app_name=args.app_name,
        prebuilt_voice=args.prebuilt_voice,
        bind_custom_voice=args.bind_custom_voice,
        backup=args.backup,
    )

    if args.lint_report:
        report = auditor.to_lint_report()
        if args.json_output:
            _emit_output(report.to_json(), args.output)
            sys.exit(1 if report.errors else 0)
        report.print_and_exit(show_fixes=True)

    if args.remediate:
        result = auditor.remediate(dry_run=args.dry_run)
        audit_res = result["post_remediation_audit"]
        if args.json_output:
            _emit_output(json.dumps(result, indent=2), args.output)
        else:
            lines = [f"=== Remediation Status: {result['status']} ==="]
            for file, file_changes in result["changes_applied"].items():
                lines.append(f"\n{file}")
                for change in file_changes:
                    lines.append(f"  - {change}")
            if result["removed_styles"]:
                lines.append("\nCleared speaking styles (for reference):")
                for item in result["removed_styles"]:
                    lines.append(f"  - {item}")
            lines.append(
                f"\nPost-remediation audit: {audit_res['overall_status']}"
            )
            if audit_res["total_issues"]:
                lines.append("\n=== Remaining Issues (manual fixes) ===\n")
                lines.append(auditor.format_markdown_report(audit_res))
            _emit_output("\n".join(lines), args.output)
    else:
        audit_res = auditor.audit()
        if args.json_output:
            _emit_output(json.dumps(audit_res, indent=2), args.output)
        elif args.report:
            _emit_output(auditor.format_markdown_report(audit_res), args.output)
        else:
            lines = [
                f"=== CXAS Composite v2 Audit: {audit_res['overall_status']}"
            ]
            for priority, issues in audit_res["issues"].items():
                for item in issues:
                    loc = item["file"] + (
                        f":{item['line']}" if item["line"] else ""
                    )
                    lines.append(
                        f"[{priority}] {item['code']} {loc}: {item['message']}"
                    )
            _emit_output("\n".join(lines), args.output)

    sys.exit(0 if audit_res["overall_status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
