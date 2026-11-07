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

"""Gemini Composite v2 and Live Audio model-specific lint rules.

Implements deterministic, zero-false-positive voice checks aligned with the
CXAS Best Practices Guide (§1.2, §2.5, Appendix A Traps #24-#26):

- A013: Legacy Director's Notes / custom voice instruction mode on Composite v2
- I017: Invalid TTS audio tags (</prosody>, <slow>, [tag], translated tags)
- I018: Spoken text normalization block missing a Scope: tool-argument guard
- I019: Inline TTS delivery tags emitted on native A2A (live) models
"""

import ast
import json
import re
import warnings
from pathlib import Path

import yaml

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

COMPOSITE_V2_MODELS = ["gemini-composite-v2"]
LIVE_AUDIO_MODELS = [
    "gemini-3.1-flash-live",
    "gemini-3-flash-native-audio",
    "gemini-3.0-flash-native-audio-preview",
    "gemini-2.5-flash-native-audio-preview",
]

SPEAKING_STYLE_WORD_LIMIT = 25

_DIRECTORS_NOTE_RE = re.compile(
    r"(?:^\s*#+\s*(?:director'?s\s+notes?|audio\s+profile|the\s+scene|"
    r"transcript\s*:)|<\s*agent_voice\b)",
    re.IGNORECASE | re.MULTILINE,
)

_CLOSING_PROSODY_RE = re.compile(r"</\s*prosody\s*>", re.IGNORECASE)
_SLOW_TAG_RE = re.compile(r"<\s*slow(?:er)?\s*>", re.IGNORECASE)

_BRACKET_DELIVERY_TAGS = frozenset(
    {
        "breath",
        "sigh",
        "sighs",
        "laugh",
        "laughs",
        "chuckle",
        "chuckles",
        "exhale",
        "exhales",
        "gasp",
        "pause",
        "short pause",
        "medium pause",
        "long pause",
        "warm",
        "empathetic",
        "calm",
        "clear",
        "positive",
        "sympathetic",
        "whisper",
        "whispers",
        "whispering",
        "slow",
        "slower",
        "fast",
        "faster",
    }
)
_BRACKET_TAG_RE = re.compile(r"\[\s*(/?)\s*([a-z][a-z \-]{1,24}?)\s*\]", re.I)

_TRANSLATED_TAGS = (
    "pausa corta",
    "pausa breve",
    "pausa larga",
    "suspiro",
    "suspira",
    "courte pause",
    "longue pause",
    "soupir",
    "kurze pause",
    "lange pause",
    "seufzer",
)
_TRANSLATED_TAG_RE = re.compile(
    r"<\s*(?:" + "|".join(re.escape(t) for t in _TRANSLATED_TAGS) + r")\s*>",
    re.IGNORECASE,
)

_A2A_INLINE_TAGS = (
    "warm",
    "empathetic",
    "calm",
    "clear",
    "positive",
    "sympathetic",
    "enthusiasm",
    "relief",
    "interest",
    "determination",
    "breath",
    "sigh",
    "sighs",
    "laugh",
    "chuckle",
    "chuckles",
    "exhales",
    "gasp",
    "whispers",
    "whispering",
    "short pause",
    "long pause",
    "slow",
    "slower",
    "fast",
    "faster",
)
_A2A_ANGLE_TAG_RE = re.compile(
    r"<\s*(?:(?:"
    + "|".join(re.escape(t) for t in _A2A_INLINE_TAGS)
    + r")|/?prosody\b[^>\n]*)\s*>",
    re.IGNORECASE,
)

_LABEL_CONTEXT_RE = re.compile(
    r"\b(?:intents?|labels?|category|categories|classification|classified\s+as|"
    r"sentiment)\s*(?:is|was|are|=|==|:|of)?\s*$",
    re.IGNORECASE,
)

_NEGATION_LINE_RE = re.compile(
    r"\b(?:never\s+(?:write|use|emit|add|include)|do\s+not\s+(?:write|use|emit|"
    r"add|include)|don't\s+(?:write|use|emit)|avoid\s+writing|no\s+closing\s+tag)",
    re.IGNORECASE,
)

_NORMALIZATION_BLOCK_RE = re.compile(
    r"(?:^[ \t]*<\s*(?P<tntag>instruction|guideline|rule)\b[^>\n]*"
    r'\b(?:id|name)\s*=\s*["\'][^"\']*text[\s_-]*normalization[^"\']*["\']'
    r"[^>\n]*>(?P<xml_body>.*?)</\s*(?P=tntag)\s*>|"
    r"^[ \t]*<\s*text_normalization\s*>(?P<tag_body>.*?)"
    r"</\s*text_normalization\s*>|"
    r"^#{1,4}\s*(?:spoken\s+)?text\s+normalization\b[^\n]*\n"
    r"(?P<md_body>.*?)(?=^#{1,4}\s|\Z))",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_TN_SCOPE_TARGET_RE = re.compile(
    r"\b(?:tools?|arguments?|parameters?|payloads?|inputs?|json)\b",
    re.IGNORECASE,
)
_TN_SCOPE_GUARD_RE = re.compile(
    r"\b(?:scope\s*:|never|do\s+not|don't|exclud\w*|"
    r"only\s+(?:to|for)\s+(?:the\s+)?(?:words?\s+)?spoken|"
    r"spoken\s+(?:words?|output|responses?|speech)\s+only)\b",
    re.IGNORECASE,
)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_at(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:] if end == -1 else text[start:end]


def _extract_callback_tts_strings(content: str) -> list[tuple[int, str]]:
    """Extract string literals passed to Part.from_text/customized_response."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(content)
    except SyntaxError:
        return []

    spans: list[tuple[int, str]] = []

    def _collect_strings(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                spans.append((getattr(sub, "lineno", 1), sub.value))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "Part"
            and func.attr in ("from_text", "from_customized_response")
        ):
            for arg in node.args:
                _collect_strings(arg)
            for kw in node.keywords:
                if kw.arg in (None, "text", "content"):
                    _collect_strings(kw.value)
    return spans


@rule("config", models=COMPOSITE_V2_MODELS)
class CompositeV2SpeechInstruction(Rule):
    id = "A013"
    name = "composite-v2-speech-instruction"
    description = (
        "Composite v2 synthesizeSpeechConfigs should clear legacy Director's "
        "Notes and set customVoiceSamples voiceInstructionMode"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in ("app.json", "app.yaml"):
            return []
        rel = str(file_path.relative_to(context.project_root))
        try:
            data = (
                json.loads(content)
                if file_path.suffix == ".json"
                else (yaml.safe_load(content) or {})
            )
        except Exception:
            return []
        if not isinstance(data, dict):
            return []

        audio = data.get("audioProcessingConfig")
        if not isinstance(audio, dict):
            return []

        results: list[LintResult] = []
        custom_samples = audio.get("customVoiceSamples")
        custom_voice_names: set[str] = set()
        if isinstance(custom_samples, list):
            for idx, sample in enumerate(custom_samples):
                if not isinstance(sample, dict):
                    continue
                vname = str(
                    sample.get("name") or sample.get("voiceName") or ""
                ).strip()
                if vname:
                    custom_voice_names.add(vname)
                mode = sample.get("voiceInstructionMode")
                if mode != "NO_INSTRUCTION":
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"customVoiceSamples[{idx}] should set "
                                "'voiceInstructionMode' to "
                                "'NO_INSTRUCTION' on gemini-composite-v2 "
                                f"(found {mode!r})"
                            ),
                            fix=(
                                'Set "voiceInstructionMode": "NO_INSTRUCTION" '
                                "on every customVoiceSamples entry."
                            ),
                        )
                    )

        speech_cfgs = audio.get("synthesizeSpeechConfigs")
        if isinstance(speech_cfgs, dict):
            for lang, cfg in speech_cfgs.items():
                if not isinstance(cfg, dict):
                    continue
                inst = str(cfg.get("instruction") or "").strip()
                if not inst:
                    continue
                voice = str(cfg.get("voice") or "").strip()
                words = inst.split()
                if voice and voice in custom_voice_names:
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"synthesizeSpeechConfigs.{lang}.instruction "
                                "must be empty ('') when using a custom "
                                "cloned voice on gemini-composite-v2"
                            ),
                            fix=(
                                f"Set synthesizeSpeechConfigs.{lang}"
                                '.instruction to "".'
                            ),
                        )
                    )
                elif _DIRECTORS_NOTE_RE.search(inst) or (
                    len(words) > SPEAKING_STYLE_WORD_LIMIT
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"synthesizeSpeechConfigs.{lang}.instruction "
                                f"({len(words)} words) contains legacy "
                                "Director's Notes or exceeds 25 words — "
                                "causes hyper-expressive TTS artifacts on "
                                "gemini-composite-v2"
                            ),
                            fix=(
                                f"Remove or clear synthesizeSpeechConfigs"
                                f".{lang}.instruction and move delivery "
                                "guidance to global_instruction.txt."
                            ),
                        )
                    )
        return results


def _check_composite_v2_tag_text(
    rule_obj: Rule,
    rel: str,
    text: str,
    base_line: int = 1,
    is_instruction: bool = True,
) -> list[LintResult]:
    results: list[LintResult] = []

    for m in _CLOSING_PROSODY_RE.finditer(text):
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Closing tag '{m.group(0).strip()}' is unsupported on "
                    "gemini-composite-v2 — audio tags are opening-only"
                ),
                fix=(
                    "Remove </prosody> and reset speaking rate at the next "
                    'sentence with <prosody rate="100%">.'
                ),
            )
        )

    for m in _SLOW_TAG_RE.finditer(text):
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Invalid tag '{m.group(0)}' on gemini-composite-v2 — "
                    "TTS may ignore or read it aloud"
                ),
                fix=(
                    'Replace <slow> with <prosody rate="65%"> at the start '
                    "of the sentence."
                ),
            )
        )

    for m in _BRACKET_TAG_RE.finditer(text):
        tag_name = re.sub(r"\s+", " ", m.group(2).strip().lower())
        if tag_name not in _BRACKET_DELIVERY_TAGS:
            continue
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        line_prefix = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        if _LABEL_CONTEXT_RE.search(line_prefix):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        inner_tag = (
            "short pause" if tag_name in ("pause", "medium pause") else tag_name
        )
        replacement = (
            '<prosody rate="65%">'
            if tag_name in ("slow", "slower")
            else f"<{inner_tag}>"
        )
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Square-bracket delivery tag '{m.group(0)}' is read aloud "
                    "verbatim on gemini-composite-v2"
                ),
                fix=(
                    "Use opening-only English angle brackets at sentence "
                    f"start (e.g., {replacement})."
                ),
            )
        )

    for m in _TRANSLATED_TAG_RE.finditer(text):
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Translated non-English audio tag '{m.group(0)}' leaks "
                    "aloud on gemini-composite-v2"
                ),
                fix=(
                    "Use English audio tag keywords (e.g., <short pause>, "
                    "<sigh>, <warm>) across all spoken locales."
                ),
            )
        )

    return results


@rule("instructions", models=COMPOSITE_V2_MODELS)
class CompositeV2InvalidAudioTagsInstruction(Rule):
    id = "I017"
    name = "composite-v2-invalid-audio-tags"
    description = (
        "Composite v2 instructions and callbacks must not use </prosody>, "
        "<slow>, square-bracket [tag] markup, or translated audio tags"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return _check_composite_v2_tag_text(
            self, rel, content, base_line=1, is_instruction=True
        )


@rule("callbacks", models=COMPOSITE_V2_MODELS)
class CompositeV2InvalidAudioTagsCallback(Rule):
    id = "I017"
    name = "composite-v2-invalid-audio-tags"
    description = (
        "Composite v2 instructions and callbacks must not use </prosody>, "
        "<slow>, square-bracket [tag] markup, or translated audio tags"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []
        for lineno, text in _extract_callback_tts_strings(content):
            results.extend(
                _check_composite_v2_tag_text(
                    self, rel, text, base_line=lineno, is_instruction=False
                )
            )
        return results


@rule("instructions", models=COMPOSITE_V2_MODELS)
class CompositeV2NormalizationScope(Rule):
    id = "I018"
    name = "composite-v2-normalization-scope"
    description = (
        "Spoken text normalization blocks must include a Scope: guard "
        "excluding tool arguments and JSON payloads"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []
        for m in _NORMALIZATION_BLOCK_RE.finditer(content):
            body = (
                m.group("xml_body")
                or m.group("tag_body")
                or m.group("md_body")
                or ""
            )
            if not (
                _TN_SCOPE_TARGET_RE.search(body)
                and _TN_SCOPE_GUARD_RE.search(body)
            ):
                results.append(
                    self.make_result(
                        file=rel,
                        line=_line_of(content, m.start()),
                        message=(
                            "Text normalization block lacks a 'Scope:' guard "
                            "excluding tool arguments/JSON payloads — causes "
                            "the LLM to spell out digits in tool arguments"
                        ),
                        fix=(
                            "Add 'Scope: Apply ONLY to spoken words delivered "
                            "to the caller. NEVER apply text normalization to "
                            "tool call arguments, JSON payloads, or state.'"
                        ),
                    )
                )
        return results


def _check_live_audio_tags(
    rule_obj: Rule,
    rel: str,
    text: str,
    base_line: int = 1,
    is_instruction: bool = True,
) -> list[LintResult]:
    results: list[LintResult] = []
    for m in _A2A_ANGLE_TAG_RE.finditer(text):
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Inline TTS tag '{m.group(0)}' is not supported on native "
                    "audio-to-audio models and will be spoken aloud literally"
                ),
                fix=(
                    "Remove inline TTS markup on live/native-audio models, or "
                    "switch modelSettings.model to 'gemini-composite-v2'."
                ),
            )
        )
    for m in _BRACKET_TAG_RE.finditer(text):
        tag_name = re.sub(r"\s+", " ", m.group(2).strip().lower())
        if tag_name not in _BRACKET_DELIVERY_TAGS:
            continue
        line_str = _line_at(text, m.start())
        if is_instruction and _NEGATION_LINE_RE.search(line_str):
            continue
        line_prefix = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        if _LABEL_CONTEXT_RE.search(line_prefix):
            continue
        lineno = base_line + _line_of(text, m.start()) - 1
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Inline delivery tag '{m.group(0)}' is not supported on "
                    "native audio-to-audio models and may be spoken aloud"
                ),
                fix=(
                    "Express tone in <persona> prose rather than inline tags "
                    "on live models."
                ),
            )
        )
    return results


@rule("instructions", models=LIVE_AUDIO_MODELS)
class LiveAudioInlineTagsInstruction(Rule):
    id = "I019"
    name = "live-audio-inline-tags"
    description = (
        "Native audio-to-audio (live) models must not emit inline TTS tags"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        return _check_live_audio_tags(
            self, rel, content, base_line=1, is_instruction=True
        )


@rule("callbacks", models=LIVE_AUDIO_MODELS)
class LiveAudioInlineTagsCallback(Rule):
    id = "I019"
    name = "live-audio-inline-tags"
    description = (
        "Native audio-to-audio (live) models must not emit inline TTS tags"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []
        for lineno, text in _extract_callback_tts_strings(content):
            results.extend(
                _check_live_audio_tags(
                    self, rel, text, base_line=lineno, is_instruction=False
                )
            )
        return results
