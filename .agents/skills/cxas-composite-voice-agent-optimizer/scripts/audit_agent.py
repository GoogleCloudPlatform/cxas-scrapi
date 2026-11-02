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

- ``modelSettings.model`` is ``gemini-composite-v2``.
- Speaking style instructions (``synthesizeSpeechConfigs.<lang>.instruction``,
  ``customVoiceSamples[].voiceInstruction``) are cleared or kept below 12
  words, and Director's Notes are cleared; long style prompts conflict with
  inline audio tags.
- Inline audio tags use angle brackets (``<warm>``), not square brackets, and
  poorly performing tags are replaced with better-performing ones.

The cxas-scrapi linter supplies discovery, context, reporting, and the
model-agnostic rules reused here (I015, V104, T014).
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
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
TARGET_TEMPERATURE = 1.0

# ── Audio tag vocabulary (see references/tag_reference.md) ───────────────

PAUSE_TAGS: frozenset[str] = frozenset({"short pause", "long pause"})

# Reliably followed and essentially never spoken aloud.
RECOMMENDED_TAGS: frozenset[str] = frozenset(
    {
        "sigh",
        "sighs",
        "chuckles",
        "exhales",
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

# Usually followed; keep at a sentence start.
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
    }
)

# Poorly performing tag -> better-performing tag ("" deletes the tag).
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
}

# Tags the evaluation did not cover -> closest evaluated tag. Converted to
# angle brackets like any audio tag; the swap needs user confirmation.
SUGGESTIONS: dict[str, str] = {
    "seriousness": "clear",
    "serious": "clear",
    "formal": "clear",
    "uhm": "short pause",
    "um": "short pause",
    "medium pause": "short pause",
    "pause": "short pause",
    "breath": "exhales",
    "deep breath": "exhales",
    "excited": "enthusiasm",
    "excitement": "enthusiasm",
    "celebratory": "positive",
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
}

# Audio-tag-like words with no evaluated substitute.
UNEVALUATED_WORDS: frozenset[str] = frozenset(
    {
        "bored",
        "clears throat",
        "deadpan",
        "gasp",
        "gasps",
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
    RECOMMENDED_TAGS | SUPPORTED_TAGS | frozenset(REMAP) | UNEVALUATED_TAGS
)

PROSODY_RE = re.compile(r'^prosody\s+rate\s*=\s*"?\s*([^"\s]+?)\s*"?$', re.I)
BRACKET_TAG_RE = re.compile(r"\[(/?)([^\[\]\n]{1,40})\]")
ANGLE_TAG_RE = re.compile(r"<(/?)([^<>\n]{1,40})>")
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
    r"^\s*#+\s*(?:director'?s\s+notes?|audio\s+profile|transcript\s*:)",
    re.I | re.M,
)
OLD_FORMAT_RULE_RE = re.compile(
    r"square[\s-]+brackets?|bracketed\s+(?:delivery\s+|audio\s+)?tags?|"
    r"never\s+stack\s+(?:audio\s+|delivery\s+)?tags|do\s+not\s+bunch",
    re.I,
)
ANGLE_GUIDANCE_RE = re.compile(r"angle[\s-]+brackets?", re.I)
# The audio tag format block's own "never write a closing tag" bullet.
NO_CLOSING_RULE_RE = re.compile(
    r"never\s+(?:write|use|emit|add)\s+(?:a\s+|any\s+)?closing", re.I
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
    "en": "en-US-Chirp3-HD-Aoede",
    "en-au": "en-AU-Chirp3-HD-Aoede",
    "en-ca": "en-CA-Chirp3-HD-Aoede",
    "en-gb": "en-GB-Chirp3-HD-Aoede",
    "en-in": "en-IN-Chirp3-HD-Aoede",
    "es": "es-US-Chirp3-HD-Aoede",
    "es-es": "es-ES-Chirp3-HD-Aoede",
    "fr": "fr-FR-Chirp3-HD-Aoede",
    "fr-ca": "fr-CA-Chirp3-HD-Aoede",
    "de": "de-DE-Chirp3-HD-Aoede",
    "it": "it-IT-Chirp3-HD-Aoede",
    "ja": "ja-JP-Chirp3-HD-Aoede",
    "ko": "ko-KR-Chirp3-HD-Aoede",
    "pt": "pt-BR-Chirp3-HD-Aoede",
    "zh": "cmn-CN-Chirp3-HD-Aoede",
    "nl": "nl-NL-Chirp3-HD-Aoede",
    "nl-be": "nl-BE-Chirp3-HD-Aoede",
    "hi": "hi-IN-Chirp3-HD-Aoede",
}


def _norm_locale(locale: str) -> str:
    return locale.strip().lower().replace("_", "-")


def get_default_voice(locale: str) -> str:
    """Returns the default Chirp3-HD voice for a locale code."""
    norm = _norm_locale(locale)
    return DEFAULT_VOICES.get(
        norm,
        DEFAULT_VOICES.get(norm.split("-")[0], f"{locale}-Chirp3-HD-Aoede"),
    )


def normalize_tag(inner: str) -> str:
    """Lowercases a tag body and collapses whitespace and JSON escapes."""
    return re.sub(r"\s+", " ", inner.replace('\\"', '"')).strip().lower()


def is_audio_tag(name: str) -> bool:
    return name in KNOWN_TAGS or bool(PROSODY_RE.match(name))


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _is_label_context(content: str, start: int) -> bool:
    prefix = content[content.rfind("\n", 0, start) + 1 : start]
    return bool(
        LABEL_CONTEXT_RE.search(prefix) or ATTRIBUTE_VALUE_RE.search(prefix)
    )


def _speech_configs(app_data: dict[str, Any]) -> dict[str, Any]:
    audio = app_data.get("audioProcessingConfig")
    if not isinstance(audio, dict):
        return {}
    cfgs = audio.get("synthesizeSpeechConfigs")
    return cfgs if isinstance(cfgs, dict) else {}


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
            seen.setdefault(_norm_locale(lang), lang)
    return list(seen.values())


def _find_speech_config(cfgs: dict[str, Any], locale: str) -> Any:
    norm = _norm_locale(locale)
    for key in (norm, norm.split("-")[0]):
        for k, v in cfgs.items():
            if isinstance(k, str) and _norm_locale(k) == key:
                return v
    return None


def _style_fields(app_data: dict[str, Any]) -> Iterator[tuple[str, dict, str]]:
    """Yields (label, owner, key) for every speaking-style field."""
    for lang, cfg in _speech_configs(app_data).items():
        if isinstance(cfg, dict) and "instruction" in cfg:
            yield (
                f"synthesizeSpeechConfigs['{lang}'].instruction",
                cfg,
                "instruction",
            )
    audio = app_data.get("audioProcessingConfig")
    samples = audio.get("customVoiceSamples") if isinstance(audio, dict) else []
    for i, sample in enumerate(samples if isinstance(samples, list) else []):
        if isinstance(sample, dict) and "voiceInstruction" in sample:
            yield (
                f"customVoiceSamples[{i}].voiceInstruction",
                sample,
                "voiceInstruction",
            )
    stack: list[tuple[str, Any]] = [("", app_data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("directorsNote", "directors_note"):
                    yield f"{path}{k}", node, k
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
    located unambiguously or keys were added.
    """
    edits = _scalar_edits(old, new)
    patched = raw
    for key, old_val, new_val in edits or []:
        # Match the key and its scalar token, then compare decoded values so
        # that escaping differences (e.g. \u0027) don't matter.
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
            if key
            else []
        )
        if len(hits) != 1:
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


# ── Audio tag scanning ───────────────────────────────────────────────────


@dataclass
class TagMatch:
    """An audio tag found in a file."""

    start: int
    end: int
    name: str
    bracket: bool
    closing: bool


def iter_audio_tags(content: str) -> Iterator[TagMatch]:
    """Yields audio tags in square or angle brackets, skipping non-tags."""
    for pattern, bracket in ((BRACKET_TAG_RE, True), (ANGLE_TAG_RE, False)):
        for m in pattern.finditer(content):
            name = normalize_tag(m.group(2))
            closing = bool(m.group(1))
            if not (is_audio_tag(name) or (closing and name == "prosody")):
                continue
            prev = content[m.start() - 1] if m.start() else ""
            nxt = content[m.end()] if m.end() < len(content) else ""
            if bracket and (prev.isalnum() or prev in ("_", ")") or nxt == "("):
                continue  # code subscript or markdown link
            if _is_label_context(content, m.start()):
                continue
            yield TagMatch(m.start(), m.end(), name, bracket, closing)


def _target_tag(name: str) -> str:
    """Returns the Composite v2 tag body for a tag ("" means delete)."""
    prosody = PROSODY_RE.match(name)
    if prosody:
        # Never change a rate; only the brackets and quoting change.
        return f'prosody rate="{prosody.group(1)}"'
    return REMAP.get(name, name)


def _inside_double_quotes(
    content: str, start: int, escaped: bool = False
) -> bool:
    """Whether `start` sits inside a "..." span on its line.

    Args:
      content: File content.
      start: Offset to test.
      escaped: Count only JSON-escaped quotes (a quote inside a JSON string).
    """
    line = content[content.rfind("\n", 0, start) + 1 : start]
    pattern = r'\\"' if escaped else r'(?<!\\)["\u201c\u201d]'
    return len(re.findall(pattern, line)) % 2 == 1


def migrate_tags(content: str, json_escaped: bool) -> tuple[str, list[str]]:
    """Converts square-bracket audio tags and remaps poor tags.

    Args:
      content: File content.
      json_escaped: Whether the content is JSON, so quotes in tags are escaped.

    Returns:
      The migrated content and a list of change descriptions.
    """
    changes: list[str] = []
    out: list[str] = []
    pos = 0
    for tag in sorted(iter_audio_tags(content), key=lambda t: t.start):
        if tag.closing or (not tag.bracket and tag.name not in REMAP):
            continue
        start, end = tag.start, tag.end
        target = _target_tag(tag.name)
        if tag.name not in REMAP and not PROSODY_RE.match(tag.name):
            target = content[start + 1 : end - 1]  # keep the original case
        if '"' in target and (
            json_escaped or _inside_double_quotes(content, start)
        ):
            target = target.replace('"', '\\"')
        new = f"<{target}>" if target else ""
        if not new and content[end : end + 1] == " ":
            end += 1  # drop the space a deleted tag leaves behind
        changes.append(
            f"L{_line_of(content, start)}: {content[tag.start : tag.end]}"
            f" -> {new or '(removed)'}"
        )
        out.append(content[pos:start])
        out.append(new)
        pos = end
    out.append(content[pos:])
    # Drop duplicates a remap creates: `<calm>, <calm>` or `<calm><calm>`.
    migrated = re.sub(
        r"<([a-z ]{2,30})>(?:\s*(?:,|/|\bor\b|\band\b)?\s*<\1>)+",
        r"<\1>",
        "".join(out),
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


@rule("config")
class CompositeV2Temperature(Rule):
    id = "CV202"
    name = "composite-v2-temperature"
    description = "modelSettings.temperature must be 1.0"
    default_severity = Severity.WARNING

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if file_path.name != "app.json" or data is None:
            return []
        settings = data.get("modelSettings") or {}
        temp = (
            settings.get("temperature") if isinstance(settings, dict) else None
        )
        if temp == TARGET_TEMPERATURE:
            return []
        return [
            self.make_result(
                file=_rel(file_path, context),
                message=(
                    f"modelSettings.temperature is {temp}; 1.0 avoids"
                    " acoustic repetition loops."
                ),
                fix="Set modelSettings.temperature to 1.0",
            )
        ]


SPEAKING_STYLE_WORD_LIMIT = 12


def _invalid_style_reason(key: str, value: Any) -> str | None:
    """Returns why a speaking-style value is invalid on Composite v2, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    if key in ("directorsNote", "directors_note") or DIRECTORS_NOTE_RE.search(
        value
    ):
        return "holds a Director's Note"
    word_count = len(value.split())
    if word_count >= SPEAKING_STYLE_WORD_LIMIT:
        return (
            f"has {word_count} words (must be empty or below"
            f" {SPEAKING_STYLE_WORD_LIMIT} words)"
        )
    return None


@rule("config")
class CompositeV2SpeakingStyle(Rule):
    id = "CV203"
    name = "composite-v2-speaking-style"
    description = (
        "Speaking style instructions must be empty or below 12 words;"
        " Director's Notes must be cleared"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if data is None:
            return []
        results = []
        for label, owner, key in _style_fields(data):
            reason = _invalid_style_reason(key, owner.get(key))
            if not reason:
                continue
            fix = (
                f'Clear {label} to ""'
                if key in ("directorsNote", "directors_note")
                else f'Clear {label} to "" or keep it below 12 words'
            )
            results.append(
                self.make_result(
                    file=_rel(file_path, context),
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
        "Every declared language needs a synthesizeSpeechConfigs voice"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_json(content)
        if file_path.name != "app.json" or data is None:
            return []
        cfgs = _speech_configs(data)
        results = []
        for lang in _declared_languages(data):
            cfg = _find_speech_config(cfgs, lang)
            if not isinstance(cfg, dict) or not cfg.get("voice"):
                results.append(
                    self.make_result(
                        file=_rel(file_path, context),
                        message=(
                            f"Declared language '{lang}' has no"
                            " synthesizeSpeechConfigs voice."
                        ),
                        fix=f"Add a voice, e.g. '{get_default_voice(lang)}'",
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
    description = "Audio tags must use angle brackets"
    default_severity = Severity.ERROR

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if not tag.bracket or tag.closing:
            return None
        target = _target_tag(tag.name)
        return (
            f"Square-bracket audio tag {content[tag.start : tag.end]}.",
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
        if tag.bracket or tag.closing or tag.name not in REMAP:
            return None
        target = REMAP[tag.name]
        return (
            f"<{tag.name}> performs poorly on Composite v2.",
            f"Replace with <{target}>" if target else "Remove the tag",
        )


@rule("instructions")
class CompositeV2UnevaluatedTag(_AudioTagRule):
    id = "CV212"
    name = "composite-v2-unevaluated-tag"
    description = "Unevaluated audio tags need user confirmation"
    default_severity = Severity.WARNING

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if tag.bracket or tag.closing or tag.name not in UNEVALUATED_TAGS:
            return None
        suggestion = SUGGESTIONS.get(tag.name)
        return (
            f"<{tag.name}> was not evaluated on Composite v2.",
            f"Ask the user; closest evaluated tag is <{suggestion}>"
            if suggestion
            else "Ask the user whether to keep it",
        )


@rule("instructions")
class CompositeV2ClosingTag(_AudioTagRule):
    id = "CV213"
    name = "composite-v2-closing-tag"
    description = "Audio tags are point markers; never write closing tags"
    default_severity = Severity.WARNING

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
            'Delete it; use <prosody rate="100%"> to reset the pace',
        )


@rule("instructions")
class CompositeV2TagPlacement(_AudioTagRule):
    id = "CV214"
    name = "composite-v2-tag-placement"
    description = (
        "Style tags go at a sentence start; pause tags follow punctuation"
    )
    default_severity = Severity.WARNING

    def findings(
        self, tag: TagMatch, content: str, file_path: pathlib.Path
    ) -> tuple[str, str] | None:
        if tag.bracket or tag.closing:
            return None
        before = content[content.rfind("\n", 0, tag.start) + 1 : tag.start]
        after = content[tag.end : tag.end + 2]
        escaped = file_path.suffix in (".json", ".jsonl")
        # Only flag tags mid-sentence inside a quoted utterance.
        if (
            not re.search(r"[A-Za-z0-9] $", before)
            or not re.match(r" [A-Za-z]", after)
            or not _inside_double_quotes(content, tag.start, escaped)
        ):
            return None
        if tag.name in PAUSE_TAGS:
            return (
                f"<{tag.name}> follows a word without punctuation.",
                "Add a comma or other punctuation before the pause tag",
            )
        return (
            f"<{tag.name}> sits mid-sentence in a quoted utterance.",
            "Move the tag to the start of the sentence it colors",
        )


@rule("instructions")
class CompositeV2PromptStyle(Rule):
    id = "CV215"
    name = "composite-v2-prompt-style"
    description = (
        "Prompts must not carry Director's Notes or old tag-format rules"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: pathlib.Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results = []
        for m in DIRECTORS_NOTE_RE.finditer(content):
            results.append(
                self.make_result(
                    file=_rel(file_path, context),
                    line=_line_of(content, m.start()),
                    message=(
                        "Director's Note / Audio Profile text in a prompt:"
                        f" '{m.group(0).strip()}'."
                    ),
                    fix="Remove speaking-style presets from the prompt",
                )
            )
        for m in OLD_FORMAT_RULE_RE.finditer(content):
            results.append(
                self.make_result(
                    file=_rel(file_path, context),
                    line=_line_of(content, m.start()),
                    message=f"Rule written for the old tag format: '{m[0]}'.",
                    fix="Rewrite it to match the audio tag format block",
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


CONFIG_RULES: list[Rule] = [
    CompositeV2Model(),
    CompositeV2Temperature(),
    CompositeV2SpeakingStyle(),
    CompositeV2VoiceParity(),
]
TEXT_RULES: list[Rule] = [
    CompositeV2BracketTag(),
    CompositeV2TagToReplace(),
    CompositeV2UnevaluatedTag(),
    CompositeV2ClosingTag(),
    CompositeV2TagPlacement(),
]
PROMPT_RULES: list[Rule] = [CompositeV2PromptStyle()]

RULE_PRIORITY: dict[str, str] = {
    **SDK_RULE_PRIORITY,
    "CV201": "P0",
    "CV202": "P0",
    "CV203": "P0",
    "CV204": "P1",
    "CV210": "P0",
    "CV211": "P0",
    "CV212": "P1",
    "CV213": "P1",
    "CV214": "P2",
    "CV215": "P0",
    "CV216": "P0",
}

PRIORITY_TITLES: dict[str, str] = {
    "P0": "🔴 Priority P0: Critical Voice & Synthesis Blockers (Fix First)",
    "P1": "🟡 Priority P1: Multi-Language, Stability & Tag Review",
    "P2": "🟢 Priority P2: Placement Review & Tool Pacing (User-Approved)",
}


class VoiceAgentAuditor:
    """Audits and remediates CXAS voice configurations for Composite v2."""

    def __init__(
        self,
        workspace_dir: pathlib.Path | str = ".",
        app_name: str | None = None,
    ):
        self.workspace_path = pathlib.Path(workspace_dir).resolve()
        self.app_name = app_name
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

    def text_files(self) -> list[pathlib.Path]:
        """Every app file that may carry audio tags."""
        files = []
        for p in sorted(self.app_root.rglob("*")):
            parts = p.relative_to(self.app_root).parts
            if (
                p.is_file()
                and p.suffix in TEXT_SUFFIXES
                and not any(
                    part.startswith(".") or part in EXCLUDED_DIRS
                    for part in parts
                )
            ):
                files.append(p)
        return files

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
        results += self._run_sdk_rules(context)
        return results

    def audit(self) -> dict[str, Any]:
        """Returns all findings grouped by priority."""
        by_priority: dict[str, list[dict[str, Any]]] = {
            "P0": [],
            "P1": [],
            "P2": [],
        }
        for r in self.lint_results():
            priority = RULE_PRIORITY.get(r.rule_id, "P2")
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
        }

    def to_lint_report(self) -> LintReport:
        report = LintReport()
        report.add_all(self.lint_results())
        return report

    def format_markdown_report(self, audit_res: dict[str, Any]) -> str:
        counts = audit_res["priority_counts"]
        lines = [
            "# CXAS Composite v2 Voice Readiness Report\n",
            f"**Workspace:** `{self.workspace_path}`",
            f"**Target model:** `{TARGET_MODEL}`",
            f"**Overall Status:** `{audit_res['overall_status']}`"
            f" ({audit_res['total_issues']} issues)\n",
            "### Summary",
            f"- 🔴 **P0:** {counts['P0']}",
            f"- 🟡 **P1:** {counts['P1']}",
            f"- 🟢 **P2:** {counts['P2']}\n",
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
        if not audit_res["total_issues"]:
            lines.append("\n🎉 **No issues detected.**")
        return "\n".join(lines)

    # ── Remediation ──

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
            path.write_text(
                _minimal_json_edit(raw, old, data), encoding="utf-8"
            )
        return changes

    @staticmethod
    def _upgrade_model(data: dict[str, Any], is_app: bool) -> list[str]:
        settings = data.get("modelSettings")
        if not isinstance(settings, dict):
            if not is_app:
                return []
            settings = data["modelSettings"] = {}
        changes = []
        model = settings.get("model")
        if model != TARGET_MODEL and (
            (model is None and is_app)
            or (
                isinstance(model, str)
                and model.startswith(COMPOSITE_MODEL_PREFIX)
            )
        ):
            settings["model"] = TARGET_MODEL
            changes.append(f"modelSettings.model: {model} -> {TARGET_MODEL}")
        if is_app and settings.get("temperature") != TARGET_TEMPERATURE:
            changes.append(
                f"modelSettings.temperature: {settings.get('temperature')}"
                f" -> {TARGET_TEMPERATURE}"
            )
            settings["temperature"] = TARGET_TEMPERATURE
        return changes

    @staticmethod
    def _clear_style(data: dict[str, Any]) -> list[str]:
        changes = []
        for label, owner, key in list(_style_fields(data)):
            if _invalid_style_reason(key, owner.get(key)):
                owner[key] = ""
                changes.append(f'Cleared {label} to ""')
        return changes

    def remediate(self, dry_run: bool = False) -> dict[str, Any]:
        """Applies the deterministic Composite v2 fixes in place."""
        changes: dict[str, list[str]] = {}
        context = self._context()

        for path in self.config_files():
            is_app = path == self.app_json_path

            def mutate(data: dict[str, Any], is_app: bool = is_app) -> list:
                found = self._upgrade_model(data, is_app)
                return found + self._clear_style(data)

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
                    path.write_text(migrated, encoding="utf-8")
                changes.setdefault(_rel(path, context), []).extend(found)

        return {
            "status": (
                ("DRY_RUN" if dry_run else "REMEDIATED")
                if changes
                else "NO_CHANGES_NEEDED"
            ),
            "changes_applied": changes,
            "post_remediation_audit": self.audit(),
        }


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
    auditor = VoiceAgentAuditor(args.workspace, app_name=args.app_name)

    if args.lint_report:
        report = auditor.to_lint_report()
        if args.json_output:
            print(report.to_json())
            sys.exit(1 if report.errors else 0)
        report.print_and_exit(show_fixes=True)

    if args.remediate:
        result = auditor.remediate(dry_run=args.dry_run)
        audit_res = result["post_remediation_audit"]
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"=== Remediation Status: {result['status']} ===")
            for file, file_changes in result["changes_applied"].items():
                print(f"\n{file}")
                for change in file_changes:
                    print(f"  - {change}")
            print(f"\nPost-remediation audit: {audit_res['overall_status']}")
            if audit_res["total_issues"]:
                print("\n=== Remaining Issues (manual fixes) ===\n")
                print(auditor.format_markdown_report(audit_res))
    else:
        audit_res = auditor.audit()
        if args.json_output:
            print(json.dumps(audit_res, indent=2))
        elif args.report:
            print(auditor.format_markdown_report(audit_res))
        else:
            print(f"=== CXAS Composite v2 Audit: {audit_res['overall_status']}")
            for priority, issues in audit_res["issues"].items():
                for item in issues:
                    loc = item["file"] + (
                        f":{item['line']}" if item["line"] else ""
                    )
                    print(
                        f"[{priority}] {item['code']} {loc}: {item['message']}"
                    )

    sys.exit(0 if audit_res["overall_status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
