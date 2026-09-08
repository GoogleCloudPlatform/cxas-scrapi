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

"""CXAS Voice Agent Configuration Auditor and Auto-Remediator.

Audits, inspects, and remediates Google Cloud CX Agent Studio (CXAS) and
Customer Engagement Suite (CES) applications for Gemini Composite V1 voice
naturalness, vocal persona stability, tool pacing, and Multilingual Coverage &
Language Drift Prevention.

"""

from __future__ import annotations

import argparse
import ast
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

# Import of cxas-scrapi linter primitives and utilities
try:
    from cxas_scrapi.utils.linter import (
        Discovery,
        LintConfig,
        LintContext,
        LintReport,
        LintResult,
        Rule,
        Severity,
        build_context,
        rule,
    )
except (ImportError, ModuleNotFoundError):
    import importlib.util

    _linter_path = None
    _cur = pathlib.Path(__file__).resolve().parent
    for _cand in [_cur, *list(_cur.parents)]:
        _candidate_linter = (
            _cand / "src" / "cxas_scrapi" / "utils" / "linter.py"
        )
        if _candidate_linter.is_file():
            _linter_path = _candidate_linter
            break

    if _linter_path and _linter_path.is_file():
        _spec = importlib.util.spec_from_file_location(
            "cxas_scrapi_linter", str(_linter_path)
        )
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        Discovery = _mod.Discovery
        LintConfig = _mod.LintConfig
        LintContext = _mod.LintContext
        LintReport = _mod.LintReport
        LintResult = _mod.LintResult
        Rule = _mod.Rule
        Severity = _mod.Severity
        build_context = _mod.build_context
        rule = _mod.rule
    else:
        raise ImportError("Could not find cxas_scrapi.utils.linter") from None


# Locale code to Natural Language Accent mapping
LOCALE_TO_ACCENT: dict[str, str] = {
    # Base 2-letter languages
    "en": "American English",
    "es": "Spanish accent",
    "fr": "Metropolitan French",
    "de": "German",
    "ja": "Japanese",
    "pt": "Brazilian Portuguese",
    "it": "Italian",
    "zh": "Mandarin Chinese",
    "ko": "Korean",
    "nl": "Dutch",
    "hi": "Hindi",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "tr": "Turkish",
    "id": "Indonesian",
    "mr": "Marathi",
    "ro": "Romanian",
    "ta": "Tamil",
    "te": "Telugu",
    "vi": "Vietnamese",
    # English regional dialects & blends
    "en-US": "American English",
    "en-GB": "British English",
    "en-AU": "Australian English",
    "en-CA": "Canadian English",
    "en-IN": "Indian English",
    "en-hi": "Hinglish",
    "en-es": "Spanglish",
    # Spanish regional dialects
    "es-US": "Spanish accent",
    "es-ES": "Castilian Spanish",
    "es-MX": "Latin American Spanish",
    # French regional dialects
    "fr-FR": "Metropolitan French",
    "fr-CA": "French Canadian",
    # German regional dialects
    "de-DE": "German",
    # Portuguese regional dialects
    "pt-BR": "Brazilian Portuguese",
    # Italian
    "it-IT": "Italian",
    # Dutch regional dialects
    "nl-NL": "Dutch",
    "nl-BE": "Flemish Dutch",
    # Other regional locales
    "zh-CN": "Mandarin Chinese",
    "da-DK": "Danish",
    "fi-FI": "Finnish",
    "hi-IN": "Hindi",
    "id-ID": "Indonesian",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "mr-IN": "Marathi",
    "pl-PL": "Polish",
    "ro-RO": "Romanian",
    "sv-SE": "Swedish",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "tr-TR": "Turkish",
    "vi-VN": "Vietnamese",
}

# Default Chirp3 HD voice mapping
DEFAULT_VOICES: dict[str, str] = {
    # Base 2-letter languages
    "en": "en-US-Chirp3-HD-Aoede",
    "es": "es-US-Chirp3-HD-Aoede",
    "fr": "fr-FR-Chirp3-HD-Aoede",
    "de": "de-DE-Chirp3-HD-Aoede",
    "ja": "ja-JP-Chirp3-HD-Aoede",
    "pt": "pt-BR-Chirp3-HD-Aoede",
    "it": "it-IT-Chirp3-HD-Aoede",
    "zh": "cmn-CN-Chirp3-HD-Aoede",
    "ko": "ko-KR-Chirp3-HD-Aoede",
    "nl": "nl-NL-Chirp3-HD-Aoede",
    "hi": "hi-IN-Chirp3-HD-Aoede",
    "sv": "sv-SE-Chirp3-HD-Aoede",
    "da": "da-DK-Chirp3-HD-Aoede",
    "fi": "fi-FI-Chirp3-HD-Aoede",
    "pl": "pl-PL-Chirp3-HD-Aoede",
    "tr": "tr-TR-Chirp3-HD-Aoede",
    "id": "id-ID-Chirp3-HD-Aoede",
    "mr": "mr-IN-Chirp3-HD-Aoede",
    "ro": "ro-RO-Chirp3-HD-Aoede",
    "ta": "ta-IN-Chirp3-HD-Aoede",
    "te": "te-IN-Chirp3-HD-Aoede",
    "vi": "vi-VN-Chirp3-HD-Aoede",
    # Specific locales
    "zh-CN": "cmn-CN-Chirp3-HD-Aoede",
    "da-DK": "da-DK-Chirp3-HD-Aoede",
    "nl-NL": "nl-NL-Chirp3-HD-Aoede",
    "nl-BE": "nl-BE-Chirp3-HD-Aoede",
    "en-AU": "en-AU-Chirp3-HD-Aoede",
    "en-CA": "en-CA-Chirp3-HD-Aoede",
    "en-IN": "en-IN-Chirp3-HD-Aoede",
    "en-GB": "en-GB-Chirp3-HD-Aoede",
    "en-US": "en-US-Chirp3-HD-Aoede",
    "en-hi": "en-IN-Chirp3-HD-Aoede",
    "en-es": "es-US-Chirp3-HD-Aoede",
    "fi-FI": "fi-FI-Chirp3-HD-Aoede",
    "fr-CA": "fr-CA-Chirp3-HD-Aoede",
    "fr-FR": "fr-FR-Chirp3-HD-Aoede",
    "de-DE": "de-DE-Chirp3-HD-Aoede",
    "hi-IN": "hi-IN-Chirp3-HD-Aoede",
    "id-ID": "id-ID-Chirp3-HD-Aoede",
    "it-IT": "it-IT-Chirp3-HD-Aoede",
    "ja-JP": "ja-JP-Chirp3-HD-Aoede",
    "ko-KR": "ko-KR-Chirp3-HD-Aoede",
    "mr-IN": "mr-IN-Chirp3-HD-Aoede",
    "pl-PL": "pl-PL-Chirp3-HD-Aoede",
    "pt-BR": "pt-BR-Chirp3-HD-Aoede",
    "ro-RO": "ro-RO-Chirp3-HD-Aoede",
    "es-MX": "es-US-Chirp3-HD-Aoede",
    "es-ES": "es-ES-Chirp3-HD-Aoede",
    "es-US": "es-US-Chirp3-HD-Aoede",
    "sv-SE": "sv-SE-Chirp3-HD-Aoede",
    "ta-IN": "ta-IN-Chirp3-HD-Aoede",
    "te-IN": "te-IN-Chirp3-HD-Aoede",
    "tr-TR": "tr-TR-Chirp3-HD-Aoede",
    "vi-VN": "vi-VN-Chirp3-HD-Aoede",
}

NORMALIZED_LOCALE_TO_ACCENT: dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in LOCALE_TO_ACCENT.items()
}
NORMALIZED_DEFAULT_VOICES: dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in DEFAULT_VOICES.items()
}

PROHIBITED_XML_TAGS: list[str] = [
    "state_update",
    "context",
    "reasoning",
    "thought",
    "internal",
    "call_tool",
    "parameter_update",
    "variable_update",
    "voice_lock",
    "voice_output",
]

INERT_TAGS: list[str] = [
    "warm",
    "calm",
    "clear",
    "professional",
    "empathetic",
    "reassuring",
    "sympathetic",
    "hope",
    "happy",
    "crying",
    "awe",
    "fearful",
    "surprised",
    "cautious",
    "alarm",
    "anxiety",
    "relief",
    "tension",
    "determination",
    "enthusiasm",
    "adoration",
    "interest",
    "curiosity",
    "annoyance",
    "aggression",
    "nervousness",
    "neutral",
    "negative",
    "positive",
    "admiration",
    "disgusted",
    "short pause",
    "long pause",
    "short_pause",
    "medium_pause",
    'prosody rate="85%"',
    'prosody rate="115%"',
    "formal",
    "casual",
    "mumbles",
    "stammers",
    "breathless",
    "panic",
]

WORKING_TAGS: list[str] = [
    "whispers",
    "whispering",
    "sigh",
    "sighs",
    "chuckles",
    "laughs",
    "gasp",
    "exhales",
    "clears throat",
    "slow",
    "slower",
    "fast",
    "faster",
    "confusion",
    "sleepy",
    "bored",
    "seriousness",
    "serious",
    "deadpan",
    "excitement",
    "excited",
    "celebratory",
    "yelling",
]


def get_locale_accent(locale: str) -> str:
    """Returns the natural language accent description for a given locale code."""
    normalized = locale.strip().lower().replace("_", "-")
    if normalized in NORMALIZED_LOCALE_TO_ACCENT:
        return NORMALIZED_LOCALE_TO_ACCENT[normalized]
    lang_prefix = normalized.split("-")[0]
    if lang_prefix in NORMALIZED_LOCALE_TO_ACCENT:
        return NORMALIZED_LOCALE_TO_ACCENT[lang_prefix]
    if any(
        kw in locale.lower()
        for kw in [
            "english",
            "spanish",
            "french",
            "german",
            "accent",
            "italian",
        ]
    ):
        return locale.strip()
    return LOCALE_TO_ACCENT.get(locale, f"{locale} accent")


def get_default_voice(locale: str) -> str:
    """Returns the default Chirp3-HD voice identifier for a given locale code."""
    normalized = locale.strip().lower().replace("_", "-")
    if normalized in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[normalized]
    lang_prefix = normalized.split("-")[0]
    if lang_prefix in NORMALIZED_DEFAULT_VOICES:
        return NORMALIZED_DEFAULT_VOICES[lang_prefix]
    return DEFAULT_VOICES.get(locale, f"{locale}-Chirp3-HD-Aoede")


def _find_speech_config(
    speech_configs: dict[str, Any], locale: str
) -> tuple[str, dict[str, Any]] | None:
    """Finds matching speech configuration for a locale in synthesizeSpeechConfigs."""
    if not isinstance(speech_configs, dict):
        return None
    if locale in speech_configs and isinstance(speech_configs[locale], dict):
        return locale, speech_configs[locale]

    norm_locale = locale.strip().lower().replace("_", "-")
    for k, v in speech_configs.items():
        if (
            isinstance(k, str)
            and isinstance(v, dict)
            and k.strip().lower().replace("_", "-") == norm_locale
        ):
            return k, v

    root_lang = norm_locale.split("-")[0]
    for k, v in speech_configs.items():
        if (
            isinstance(k, str)
            and isinstance(v, dict)
            and k.strip().lower().replace("_", "-") == root_lang
        ):
            return k, v

    return None


def generate_golden_directors_note(locale: str) -> str:
    """Generates a golden-standard Director's Note prompt for the specified locale."""
    accent = get_locale_accent(locale)
    normalized_locale = locale.strip().lower().replace("_", "-")

    if normalized_locale.startswith("es") or normalized_locale == "en-es":
        bridge_words = 'bridge words (like "eh," "ah," or "veamos")'
    elif normalized_locale.startswith("fr"):
        bridge_words = 'bridge words (like "euh," "bah," or "voyons")'
    elif normalized_locale.startswith("de"):
        bridge_words = 'bridge words (like "äh," "öh," or "schauen wir mal")'
    elif normalized_locale.startswith("pt"):
        bridge_words = 'bridge words (like "é," "hum," or "vejamos")'
    elif normalized_locale.startswith("it"):
        bridge_words = 'bridge words (like "eh," "ehm," or "vediamo")'
    elif normalized_locale.startswith("ja"):
        bridge_words = 'bridge words (like "ええと," "あの," or "そうですね")'
    elif normalized_locale == "en-gb":
        bridge_words = 'bridge words (like "um," "er," or "ah")'
    elif normalized_locale == "en-au":
        bridge_words = 'bridge words (like "um," "ah," or "yeah")'
    else:
        bridge_words = 'bridge words (like "um," "ah," or "hmm")'

    return (
        "Read the following transcript based on the audio profile and director's"
        " note. You must read the entire transcript strictly verbatim.\n\n# Audio"
        " Profile\nYou are a real human being working in customer"
        " care—warm, patient, and highly empathetic. You are sitting at your"
        " desk, answering a live phone call. This delivery must sound 100%"
        " unscripted, like a genuine, spontaneous conversation with someone you"
        " truly want to help.\n\n# Director's note\n* Persona & Tone: Friendly,"
        " grounded, and authentically conversational. Imagine you are helping a"
        ' neighbor. Speak with a subtle "smile" in your voice, keeping the'
        " energy upbeat but deeply reassuring.\n* Pacing: Keep the pace brisk and"
        " efficient, like a busy but highly competent agent quickly relaying"
        " information. Do not over-exaggerate pauses. Keep filler words"
        " incredibly brief.\n* Intonation & Emotion: Use natural, dynamic pitch"
        " variations to express active listening and engagement. Avoid sounding"
        " monotone, rigid, or like an automated recording. Let genuine human"
        " warmth guide your vocal melody.\n* Realism & Imperfections: If"
        f" {bridge_words} are in the text, deliver them naturally and"
        " thoughtfully, exactly as a human does when searching for information or"
        " gathering their thoughts.\n* Consistency: Maintain your natural, human"
        " conversational style throughout the entire read. When reading phone"
        " numbers, account IDs, or digits, group them naturally with slight"
        " pauses (e.g., reading a phone number in clusters), just as you would"
        " when reading numbers off a screen to a friend.\n*"
        f" Accent: {accent}\n\n## Transcript:\n"
    )


class InstructionTarget:
    """Represents an agent instruction file or JSON configuration target."""

    def __init__(self, file_path: pathlib.Path, workspace_path: pathlib.Path):
        self.file_path = file_path
        self.workspace_path = workspace_path
        try:
            self.rel_path = str(file_path.relative_to(workspace_path))
        except ValueError:
            self.rel_path = str(file_path)
        self.is_json = (
            file_path.name == "agent.json" or file_path.name.endswith(".json")
        )
        self.referenced_file_path: pathlib.Path | None = None

    def _resolve_referenced_txt_path(self, inst_path_str: str) -> pathlib.Path:
        """Resolves a referenced relative .txt path against workspace and file directory."""
        clean = inst_path_str.strip()
        candidate1 = self.workspace_path / clean
        if candidate1.is_file():
            return candidate1
        candidate2 = self.file_path.parent / clean
        if candidate2.is_file():
            return candidate2
        return candidate1

    def get_content(self) -> str | None:
        """Reads and returns the instruction string from the target file."""
        if not self.file_path.exists():
            return None
        try:
            raw = self.file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if self.is_json:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    inst = data.get("instruction")
                    if isinstance(inst, str):
                        if inst.strip().endswith(".txt"):
                            ref_path = self._resolve_referenced_txt_path(inst)
                            self.referenced_file_path = ref_path
                            if ref_path.is_file():
                                return ref_path.read_text(encoding="utf-8")
                            return None
                        return inst
                    return None
                return None
            except json.JSONDecodeError:
                return None
        return raw

    def set_content(self, new_content: str) -> bool:
        """Writes updated instruction content back to the target file."""
        if not self.file_path.exists():
            return False
        try:
            if self.is_json:
                raw = self.file_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    inst = data.get("instruction")
                    if isinstance(inst, str) and inst.strip().endswith(".txt"):
                        ref_path = self._resolve_referenced_txt_path(inst)
                        self.referenced_file_path = ref_path
                        ref_path.parent.mkdir(parents=True, exist_ok=True)
                        ref_path.write_text(new_content, encoding="utf-8")
                        return True
                    data["instruction"] = new_content
                    self.file_path.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    return True
                return False
            else:
                self.file_path.write_text(new_content, encoding="utf-8")
                return True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False


class VoiceAgentAuditor:
    """Audits and remediates CXAS voice configurations and speech architectures.

    Integrated with cxas-scrapi discovery and core services.
    """

    def __init__(
        self,
        workspace_dir: pathlib.Path | str = ".",
        app_name: str | None = None,
        project_id: str | None = None,
        location: str | None = None,
    ):
        self.workspace_path = pathlib.Path(workspace_dir).resolve()
        self.app_name = app_name
        self.project_id = project_id
        self.location = location
        self._instruction_targets_cache: list[InstructionTarget] | None = None

        # Scrapi discovery helper
        self.discovery = Discovery(
            app_dir=self.workspace_path,
            evals_dir=self.workspace_path / "evals",
        )
        self.app_json_path = self._find_app_json()

    def _find_app_json(self) -> pathlib.Path:
        """Locates the primary app.json configuration in the workspace via Discovery."""
        discovered = self.discovery.discover_app_config()
        if discovered is not None and discovered.is_file():
            return discovered

        direct = self.workspace_path / "app.json"
        if direct.is_file():
            return direct

        for p in self.workspace_path.glob("**/app.json"):
            if p.is_file() and not any(
                part.startswith(".") or part == "build"
                for part in p.relative_to(self.workspace_path).parts
            ):
                return p

        return direct

    def _find_instruction_targets(
        self, refresh: bool = False
    ) -> list[InstructionTarget]:
        """Discovers all agent instruction and configuration files in the workspace.

        Reuses Discovery to locate canonical global_instruction.txt and sub-agent
        instructions/configs, while maintaining fallback scanning for non-standard layouts.

        Caches discovered targets on the instance to avoid redundant recursive
        filesystem globs across repeated audit and remediation passes.

        Args:
          refresh: Whether to refresh cached targets from filesystem.

        Returns:
          List of InstructionTarget objects found in the workspace.
        """
        if self._instruction_targets_cache is not None and not refresh:
            return self._instruction_targets_cache

        targets: list[InstructionTarget] = []
        seen_paths: set[pathlib.Path] = set()

        # 1. Canonical Discovery: global_instruction.txt
        global_inst = self.discovery.discover_global_instruction()
        if global_inst is not None and global_inst.is_file():
            p_resolved = global_inst.resolve()
            seen_paths.add(p_resolved)
            targets.append(InstructionTarget(global_inst, self.workspace_path))

        # 2. Canonical Discovery: agent instruction files and configs
        discovered_agents = self.discovery.discover_agents()
        for _agent_name, agent_path in discovered_agents.items():
            if agent_path.is_file():
                p_resolved = agent_path.resolve()
                if p_resolved not in seen_paths:
                    target = InstructionTarget(agent_path, self.workspace_path)
                    content = target.get_content()
                    if content is not None:
                        if (
                            target.referenced_file_path is not None
                            and target.referenced_file_path.resolve()
                            in seen_paths
                        ):
                            continue
                        seen_paths.add(p_resolved)
                        if target.referenced_file_path is not None:
                            seen_paths.add(
                                target.referenced_file_path.resolve()
                            )
                        targets.append(target)

        # 3. Supplemental fallback for non-standard workspaces or multi-level layouts
        patterns = [
            "**/global_instruction.txt",
            "**/instruction.txt",
        ]
        for pattern in patterns:
            for p in sorted(self.workspace_path.glob(pattern)):
                if p.is_file() and not any(
                    part.startswith(".") or part == "build"
                    for part in p.relative_to(self.workspace_path).parts
                ):
                    p_resolved = p.resolve()
                    if p_resolved not in seen_paths:
                        seen_paths.add(p_resolved)
                        targets.append(
                            InstructionTarget(p, self.workspace_path)
                        )

        for p in sorted(self.workspace_path.glob("**/agent.json")):
            if p.is_file() and not any(
                part.startswith(".") or part == "build"
                for part in p.relative_to(self.workspace_path).parts
            ):
                if (p.parent / "instruction.txt").is_file():
                    continue
                p_resolved = p.resolve()
                if p_resolved not in seen_paths:
                    target = InstructionTarget(p, self.workspace_path)
                    content = target.get_content()
                    if content is not None:
                        if (
                            target.referenced_file_path is not None
                            and target.referenced_file_path.resolve()
                            in seen_paths
                        ):
                            continue
                        seen_paths.add(p_resolved)
                        if target.referenced_file_path is not None:
                            seen_paths.add(
                                target.referenced_file_path.resolve()
                            )
                        targets.append(target)

        for p in sorted(self.workspace_path.glob("agents/*/*.json")):
            if p.is_file() and not any(
                part.startswith(".") or part == "build"
                for part in p.relative_to(self.workspace_path).parts
            ):
                if (p.parent / "instruction.txt").is_file():
                    continue
                p_resolved = p.resolve()
                if p_resolved not in seen_paths and p.name != "agent.json":
                    target = InstructionTarget(p, self.workspace_path)
                    content = target.get_content()
                    if content is not None:
                        if (
                            target.referenced_file_path is not None
                            and target.referenced_file_path.resolve()
                            in seen_paths
                        ):
                            continue
                        seen_paths.add(p_resolved)
                        if target.referenced_file_path is not None:
                            seen_paths.add(
                                target.referenced_file_path.resolve()
                            )
                        targets.append(target)

        self._instruction_targets_cache = targets
        return targets

    def _read_app_json(self) -> dict[str, Any]:
        """Reads and parses the app.json configuration file."""
        if not self.app_json_path.exists():
            return {}
        try:
            with open(self.app_json_path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _write_app_json(self, data: dict[str, Any]) -> None:
        """Writes updated configuration dictionary back to app.json."""
        self.app_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.app_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def _get_root_agent_name(
        self, app_data: dict[str, Any] | None = None
    ) -> str:
        """Extracts the root agent name/identifier from app.json or defaults to 'root_agent'."""
        if app_data is None:
            app_data = self._read_app_json()
        if isinstance(app_data, dict):
            root_agent = app_data.get("rootAgent")
            if isinstance(root_agent, str) and root_agent.strip():
                clean = root_agent.strip().rstrip("/")
                return clean.split("/")[-1]
        return "root_agent"

    def _is_root_agent(
        self,
        target: InstructionTarget,
        app_data: dict[str, Any] | None = None,
    ) -> bool:
        """Determines whether an instruction target belongs to the configured root agent."""
        if target.file_path.name == "global_instruction.txt":
            return True

        root_name = self._get_root_agent_name(app_data).lower()
        rel_lower = target.rel_path.lower()
        rel_parts = [p.lower() for p in pathlib.Path(target.rel_path).parts]

        # Workspace top-level single-file agent targets (e.g. instruction.txt).
        if len(rel_parts) == 1:
            return True

        if root_name in rel_parts:
            return True

        if target.file_path.stem.lower() == root_name:
            return True

        if f"agents/{root_name}/" in rel_lower or rel_lower.startswith(
            f"{root_name}/"
        ):
            return True

        if f"agents/{root_name}." in rel_lower:
            return True

        return "root_agent" in rel_parts or "root_agent" in rel_lower

    def audit_audio_profile(
        self, app_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Audits synthesizeSpeechConfigs, Audio Profile headers, and transcript hooks."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        issues: list[dict[str, Any]] = []
        audio_cfg = app_data.get("audioProcessingConfig")
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}
        speech_configs = audio_cfg.get("synthesizeSpeechConfigs")

        if not isinstance(speech_configs, dict) or not speech_configs:
            issues.append(
                {
                    "code": "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
                    "message": (
                        "app.json is missing"
                        " audioProcessingConfig.synthesizeSpeechConfigs."
                    ),
                }
            )
        else:
            for locale, cfg in speech_configs.items():
                if not isinstance(cfg, dict):
                    issues.append(
                        {
                            "code": "MISSING_DIRECTORS_NOTE",
                            "locale": locale,
                            "message": (
                                f"synthesizeSpeechConfigs[{locale}] lacks a Director's Note"
                                " instruction."
                            ),
                        }
                    )
                    continue
                instruction = cfg.get("instruction")
                if not isinstance(instruction, str) or not instruction:
                    issues.append(
                        {
                            "code": "MISSING_DIRECTORS_NOTE",
                            "locale": locale,
                            "message": (
                                f"synthesizeSpeechConfigs[{locale}] lacks a Director's Note"
                                " instruction."
                            ),
                        }
                    )
                else:
                    has_audio_profile = bool(
                        re.search(
                            r"^#+\s*audio\s*profile",
                            instruction,
                            re.IGNORECASE | re.MULTILINE,
                        )
                    )
                    if not has_audio_profile:
                        issues.append(
                            {
                                "code": "MISSING_AUDIO_PROFILE_HEADER",
                                "locale": locale,
                                "message": (
                                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                                    " '# Audio Profile' header."
                                ),
                            }
                        )
                    has_directors_note = bool(
                        re.search(
                            r"^#+\s*director'?s\s*notes?",
                            instruction,
                            re.IGNORECASE | re.MULTILINE,
                        )
                    )
                    if not has_directors_note:
                        issues.append(
                            {
                                "code": "MISSING_DIRECTORS_NOTE_HEADER",
                                "locale": locale,
                                "message": (
                                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                                    " '# Director's note' header."
                                ),
                            }
                        )
                    # Ensure transcript hook strictly concludes the prompt.
                    has_trailing_transcript_hook = bool(
                        re.search(
                            r"#{2,3}\s*transcript\s*:\s*$",
                            instruction.strip(),
                            re.IGNORECASE,
                        )
                    )
                    if not has_trailing_transcript_hook:
                        issues.append(
                            {
                                "code": "MISSING_TRANSCRIPT_HOOK",
                                "locale": locale,
                                "message": (
                                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                                    " trailing '## Transcript:' hook."
                                ),
                            }
                        )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def audit_accent_specifications(
        self, app_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Audits Accent directives to ensure natural language names instead of locale codes."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        issues: list[dict[str, Any]] = []
        audio_cfg = app_data.get("audioProcessingConfig")
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}
        speech_configs = audio_cfg.get("synthesizeSpeechConfigs")
        if not isinstance(speech_configs, dict):
            speech_configs = {}

        locale_code_pattern = re.compile(
            r"Accent:\s*([a-z]{2}(?:[-_][a-z0-9]{2,3})?)\b", re.IGNORECASE
        )

        for locale, cfg in speech_configs.items():
            if not isinstance(cfg, dict):
                continue
            instruction = cfg.get("instruction")
            if not isinstance(instruction, str):
                continue
            match = locale_code_pattern.search(instruction)
            if match:
                invalid_code = match.group(1)
                recommended = get_locale_accent(invalid_code)
                if recommended.lower() != invalid_code.lower() and (
                    "-" in invalid_code
                    or "_" in invalid_code
                    or len(invalid_code) == 2
                ):
                    issues.append(
                        {
                            "code": "LOCALE_CODE_IN_ACCENT",
                            "locale": locale,
                            "found": match.group(0),
                            "invalid_code": invalid_code,
                            "recommended": f"Accent: {recommended}",
                            "message": (
                                f"Locale code '{invalid_code}' used in Accent directive. Use"
                                f" '{recommended}' instead."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def audit_multilang_coverage(
        self, app_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Audits multi-language voice parity across all declared languages (Multilingual Coverage & Language Drift Prevention)."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        issues: list[dict[str, Any]] = []
        lang_settings = app_data.get("languageSettings")
        if not isinstance(lang_settings, dict):
            lang_settings = {}
        default_lang = lang_settings.get("defaultLanguageCode")
        supported_langs = lang_settings.get("supportedLanguageCodes")
        if not isinstance(supported_langs, list):
            supported_langs = []

        all_declared_langs = set()
        if default_lang and isinstance(default_lang, str):
            all_declared_langs.add(default_lang)
        all_declared_langs.update(
            [lang for lang in supported_langs if isinstance(lang, str)]
        )

        audio_cfg = app_data.get("audioProcessingConfig")
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}
        speech_configs = audio_cfg.get("synthesizeSpeechConfigs")
        if not isinstance(speech_configs, dict):
            speech_configs = {}

        for lang in sorted(all_declared_langs):
            match_res = _find_speech_config(speech_configs, lang)
            if not match_res:
                issues.append(
                    {
                        "code": "MISSING_SYNTHESIZE_SPEECH_CONFIG_LANG",
                        "locale": lang,
                        "message": (
                            f"Declared language '{lang}' has no entry in"
                            " audioProcessingConfig.synthesizeSpeechConfigs."
                        ),
                    }
                )
            else:
                matched_key, cfg = match_res
                if not cfg.get("voice") or not isinstance(
                    cfg.get("voice"), str
                ):
                    issues.append(
                        {
                            "code": "MISSING_VOICE_IDENTIFIER",
                            "locale": lang,
                            "message": (
                                f"Declared language '{lang}' (key: '{matched_key}') is"
                                " missing a voice identifier."
                            ),
                        }
                    )
                instruction = cfg.get("instruction")
                has_directors_note = bool(
                    isinstance(instruction, str)
                    and re.search(
                        r"^#+\s*director'?s\s*notes?",
                        instruction,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )
                if (
                    not isinstance(instruction, str)
                    or not instruction
                    or not has_directors_note
                ):
                    issues.append(
                        {
                            "code": "MISSING_DIRECTORS_NOTE_INSTRUCTION",
                            "locale": lang,
                            "message": (
                                f"Declared language '{lang}' (key: '{matched_key}') is"
                                " missing a complete Director's Note instruction."
                            ),
                        }
                    )
                else:
                    accent_match = re.search(
                        r"\bAccent:\s*([^\n\r]+)", instruction, re.IGNORECASE
                    )
                    if accent_match:
                        found_accent = accent_match.group(1).strip()
                        expected_accent = get_locale_accent(lang)
                        if (
                            found_accent.lower() != expected_accent.lower()
                            and found_accent.lower() != lang.lower()
                            and found_accent.lower().replace("_", "-")
                            != lang.lower().replace("_", "-")
                        ):
                            issues.append(
                                {
                                    "code": "ACCENT_DIRECTIVE_MISMATCH",
                                    "locale": lang,
                                    "found": found_accent,
                                    "expected": expected_accent,
                                    "message": (
                                        f"Config for '{lang}' specifies 'Accent: {found_accent}'."
                                        f" Change to 'Accent: {expected_accent}'."
                                    ),
                                }
                            )

        var_decls = app_data.get("variableDeclarations")
        if not isinstance(var_decls, list):
            var_decls = []
        var_names = [
            v.get("name")
            for v in var_decls
            if isinstance(v, dict) and isinstance(v.get("name"), str)
        ]
        if (
            "user_language" not in var_names
            and "user_lang" not in var_names
            and "app_language" not in var_names
        ):
            issues.append(
                {
                    "code": "MISSING_LANGUAGE_SESSION_VAR",
                    "variable": "user_language",
                    "message": (
                        "Neither 'user_language' nor 'app_language' session variable is"
                        " declared in app.json.variableDeclarations."
                    ),
                }
            )

        return {
            "passed": len(issues) == 0,
            "declared_languages": sorted(list(all_declared_langs)),
            "issues": issues,
        }

    def audit_prohibited_xml_tags(self) -> dict[str, Any]:
        """Audits instruction files for prohibited internal platform XML tags."""
        issues: list[dict[str, Any]] = []
        targets = self._find_instruction_targets()

        tags_joined = "|".join([re.escape(tag) for tag in PROHIBITED_XML_TAGS])
        pattern = re.compile(
            rf"<\s*/?\s*(?:{tags_joined})\b[^>]*>", re.IGNORECASE
        )

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                for match in pattern.finditer(line):
                    tag_found = match.group(0)
                    issues.append(
                        {
                            "code": "PROHIBITED_XML_TAG_FOUND",
                            "file": target.rel_path,
                            "line": line_num,
                            "tag": tag_found,
                            "message": (
                                f"Prohibited platform XML tag '{tag_found}' found in"
                                f" {target.rel_path}:{line_num}. Custom/internal XML tags"
                                " trigger thought-leakage regex safety filters and cause"
                                " generic error fallbacks. Remove custom XML tags from"
                                " instructions."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "files_scanned": len(targets),
            "issues": issues,
        }

    def audit_variable_setting_antipatterns(self) -> dict[str, Any]:
        """Audits instruction files for raw text variable mutation anti-patterns."""
        issues: list[dict[str, Any]] = []
        targets = self._find_instruction_targets()

        var_set_pattern = re.compile(
            r"(?i)\bset\s+[a-zA-Z0-9_]+\s*=", re.IGNORECASE
        )

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                for match in var_set_pattern.finditer(line):
                    matched_text = match.group(0)
                    issues.append(
                        {
                            "code": "VARIABLE_SETTING_ANTIPATTERN",
                            "file": target.rel_path,
                            "line": line_num,
                            "match": matched_text,
                            "message": (
                                f"Text-based variable setting anti-pattern '{matched_text}'"
                                f" found in {target.rel_path}:{line_num}. State mutations"
                                " cannot occur via raw output text. Use tool calls (e.g."
                                " update_language) instead."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "files_scanned": len(targets),
            "issues": issues,
        }

    def audit_permissive_contradictions(self) -> dict[str, Any]:
        """Audits for permissive prompt instructions that contradict language and persona locks."""
        issues: list[dict[str, Any]] = []
        targets = self._find_instruction_targets()

        permissive_patterns = [
            re.compile(
                r"(?i)\b(?:reply|respond|answer|speak)\s+in\s+(?:the\s+)?(?:same\s+)?language\s+(?:as|that)\s+(?:the\s+)?(?:customer|caller|user)"
            ),
            re.compile(
                r"(?i)\badapt\s+to\s+(?:the\s+)?(?:customer'?s?|caller'?s?|user'?s?)\s+(?:language|dialect)"
            ),
            re.compile(
                r"(?i)\bmatch\s+(?:the\s+)?(?:customer'?s?|caller'?s?|user'?s?)\s+(?:language|dialect)"
            ),
            re.compile(
                r"(?i)\bwhichever\s+language\s+(?:the\s+)?(?:customer|caller|user)\s+(?:speaks|prefers)"
            ),
        ]

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                for pat in permissive_patterns:
                    match = pat.search(line)
                    if match:
                        matched_text = match.group(0)
                        issues.append(
                            {
                                "code": "PERMISSIVE_CONTRADICTION_FOUND",
                                "file": target.rel_path,
                                "line": line_num,
                                "match": matched_text,
                                "message": (
                                    f"Permissive language contradiction '{matched_text}' found"
                                    f" in {target.rel_path}:{line_num}. Permissive directives"
                                    " override session language locks whenever noisy ASR or"
                                    " phonetic slips occur. Replace with strict session"
                                    " language constraints."
                                ),
                            }
                        )

        return {
            "passed": len(issues) == 0,
            "files_scanned": len(targets),
            "issues": issues,
        }

    def audit_unregistered_template_variables(
        self, app_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Verifies that all {{var}} references in instructions are declared in app.json."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        issues: list[dict[str, Any]] = []
        var_decls = app_data.get("variableDeclarations")
        if not isinstance(var_decls, list):
            var_decls = []

        declared_vars = set()
        for v in var_decls:
            if isinstance(v, dict) and isinstance(v.get("name"), str):
                declared_vars.add(v["name"])

        targets = self._find_instruction_targets()
        var_pattern = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                for match in var_pattern.finditer(line):
                    var_name = match.group(1)
                    if var_name not in declared_vars:
                        issues.append(
                            {
                                "code": "UNREGISTERED_TEMPLATE_VARIABLE",
                                "file": target.rel_path,
                                "line": line_num,
                                "variable": var_name,
                                "message": (
                                    f"Template variable '{{{{{var_name}}}}}' in"
                                    f" {target.rel_path}:{line_num} is not declared in"
                                    " app.json.variableDeclarations. Unregistered variables"
                                    " resolve to empty strings or cause runtime template"
                                    " errors."
                                ),
                            }
                        )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def audit_inert_tags(self) -> dict[str, Any]:
        """Audits instruction files for ineffective or inert acoustic emotion tags."""
        issues: list[dict[str, Any]] = []
        targets = self._find_instruction_targets()

        escaped_tags = [re.escape(f"[{tag}]") for tag in INERT_TAGS]
        pattern = re.compile("|".join(escaped_tags), re.IGNORECASE)

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                for match in pattern.finditer(line):
                    tag_found = match.group(0)
                    issues.append(
                        {
                            "code": "INERT_TAG_FOUND",
                            "file": target.rel_path,
                            "line": line_num,
                            "tag": tag_found,
                            "message": (
                                f"Inert acoustic tag '{tag_found}' found in"
                                f" {target.rel_path}:{line_num}. This tag produces zero"
                                " acoustic effect and should be removed."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "files_scanned": len(targets),
            "issues": issues,
        }

    def audit_natural_speech_cues(self) -> dict[str, Any]:
        """Audits instruction files for reflexive conversational turn closings and loop triggers."""
        issues: list[dict[str, Any]] = []
        targets = self._find_instruction_targets()

        reflexive_closing_pattern = re.compile(
            r"(?:"
            r"is\s+there\s+anything\s+else\s+(?:i\s+can\s+(?:help|assist)"
            r"\s+you\s+with|you\s+need(?:\s+help\s+with)?)"
            r"|can\s+i\s+(?:help|assist)\s+you\s+with\s+anything\s+else"
            r"(?:\s+today)?"
            r"|anything\s+else\s+i\s+can\s+(?:help|assist)\s+you\s+with"
            r")",
            re.IGNORECASE,
        )

        for target in targets:
            content = target.get_content()
            if content is None:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                if reflexive_closing_pattern.search(line):
                    issues.append(
                        {
                            "code": "REFLEXIVE_CLOSING_LOOP",
                            "file": target.rel_path,
                            "line": line_num,
                            "message": (
                                "Reflexive closing phrase found in"
                                f" {target.rel_path}:{line_num}. Replace with comprehension"
                                " confirmations or natural pauses."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def audit_anti_looping_and_stability(
        self, app_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Audits sampling temperature and sub-agent voice lock directives for long-call stability."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        issues: list[dict[str, Any]] = []

        model_settings = app_data.get("modelSettings")
        if not isinstance(model_settings, dict):
            model_settings = {}
        temp = model_settings.get("temperature")
        if temp is None:
            issues.append(
                {
                    "code": "LOW_SAMPLING_TEMPERATURE",
                    "found": None,
                    "recommended": 1.0,
                    "message": (
                        "modelSettings.temperature is missing from app.json. Set to 1.0"
                        " to avoid deterministic acoustic repetition deadlocks."
                    ),
                }
            )
        elif isinstance(temp, (int, float)) and temp < 1.0:
            issues.append(
                {
                    "code": "LOW_SAMPLING_TEMPERATURE",
                    "found": temp,
                    "recommended": 1.0,
                    "message": (
                        f"modelSettings.temperature is {temp}. Set to 1.0 to avoid"
                        " deterministic acoustic repetition deadlocks."
                    ),
                }
            )
        elif not isinstance(temp, (int, float)):
            issues.append(
                {
                    "code": "LOW_SAMPLING_TEMPERATURE",
                    "found": temp,
                    "recommended": 1.0,
                    "message": (
                        f"modelSettings.temperature is invalid ({temp}). Set to 1.0 to"
                        " avoid deterministic acoustic repetition deadlocks."
                    ),
                }
            )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def _get_active_agent_tools(self) -> dict[str, list[str]]:
        """Discovers all tools declared across agent configuration files (agent.json['tools']).

        Returns:
            Mapping of tool_name -> list of agent names referencing the tool.
        """
        active_tools: dict[str, list[str]] = {}
        agent_configs = self.discovery.discover_agent_configs()
        if not agent_configs:
            agents_dir = self.workspace_path / "agents"
            if agents_dir.is_dir():
                for d in sorted(agents_dir.iterdir()):
                    if d.is_dir():
                        json_file = d / f"{d.name}.json"
                        if json_file.is_file():
                            agent_configs[d.name] = json_file

        for agent_name, config_path in sorted(agent_configs.items()):
            if not config_path.is_file():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                tools_list = data.get("tools", [])
                if isinstance(tools_list, list):
                    for item in tools_list:
                        tool_name = None
                        if isinstance(item, str):
                            tool_name = item.strip().split("/")[-1]
                        elif isinstance(item, dict):
                            raw_name = (
                                item.get("name")
                                or item.get("displayName")
                                or item.get("tool")
                            )
                            if isinstance(raw_name, str):
                                tool_name = raw_name.strip().split("/")[-1]
                        if tool_name:
                            if tool_name not in active_tools:
                                active_tools[tool_name] = []
                            if agent_name not in active_tools[tool_name]:
                                active_tools[tool_name].append(agent_name)
            except (OSError, json.JSONDecodeError):
                continue

        return active_tools

    def _resolve_tool_path(
        self, tool_name: str
    ) -> tuple[pathlib.Path | None, str]:
        """Resolves the primary definition file and type for a tool.

        Returns:
            (path, tool_type) where tool_type is 'python', 'json', 'builtin', or 'missing'.
        """
        builtin_tools = {"end_session", "transfer_to_agent"}
        if tool_name in builtin_tools or tool_name.startswith("sys."):
            return None, "builtin"

        tools_dir = self.workspace_path / "tools" / tool_name
        if not tools_dir.is_dir():
            return None, "missing"

        py_code = tools_dir / "python_function" / "python_code.py"
        if py_code.is_file():
            return py_code, "python"

        tool_json = tools_dir / f"{tool_name}.json"
        if tool_json.is_file():
            return tool_json, "json"

        json_files = sorted(tools_dir.glob("*.json"))
        if json_files:
            return json_files[0], "json"

        return None, "missing"

    def _extract_tool_docstring(
        self, file_path: pathlib.Path, tool_name: str, tool_type: str
    ) -> tuple[str | None, int]:
        """Extracts the docstring / description and line number for a tool."""
        if tool_type == "python":
            try:
                content = file_path.read_text(encoding="utf-8")
                tree = ast.parse(content)
                for node in tree.body:
                    if (
                        isinstance(node, ast.FunctionDef)
                        and node.name == tool_name
                    ):
                        doc = ast.get_docstring(node)
                        return doc, node.lineno
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef):
                        doc = ast.get_docstring(node)
                        return doc, node.lineno
                return ast.get_docstring(tree), 1
            except Exception:
                return None, 1
        elif tool_type == "json":
            try:
                content = file_path.read_text(encoding="utf-8")
                data = json.loads(content)
                desc = (
                    data.get("description")
                    or data.get("openApiTool", {}).get("description")
                    or data.get("clientFunction", {}).get("description")
                )
                return desc if isinstance(desc, str) else None, 1
            except Exception:
                return None, 1

        return None, 1

    def _is_fast_or_terminal_tool(self, tool_name: str) -> bool:
        """Determines if a tool is fast, instant, routing, or terminal (exempt from pacing)."""
        name_lower = tool_name.lower()
        fast_patterns = [
            r"(?:^|_)end(?:_session)?(?:$|_)",
            r"(?:^|_)exit(?:$|_)",
            r"(?:^|_)wrap_up(?:$|_)",
            r"(?:^|_)terminate(?:$|_)",
            r"(?:^|_)router(?:$|_)",
            r"(?:^|_)classify(?:$|_)",
            r"(?:^|_)intent(?:$|_)",
            r"(?:^|_)increment(?:$|_)",
            r"(?:^|_)counter(?:$|_)",
            r"(?:^|_)dnis(?:$|_)",
            r"(?:^|_)delay(?:$|_)",
            r"(?:^|_)update_cic(?:$|_)",
            r"(?:^|_)get_cic(?:$|_)",
            r"(?:^|_)telemetry(?:$|_)",
            r"(?:^|_)pindrop_call_state(?:$|_)",
            r"(?:^|_)initialize_session(?:$|_)",
            r"(?:^|_)mock(?:$|_)",
        ]
        return any(re.search(pat, name_lower) for pat in fast_patterns)

    def audit_tool_docstring_contracts(self) -> dict[str, Any]:
        """Audits tool docstrings and execution contracts for declared agent tools."""
        issues: list[dict[str, Any]] = []
        active_tools = self._get_active_agent_tools()

        when_call_pattern = re.compile(
            r"(?i)\bwhen\s+to\s+(?:call|invoke|use)\b|\bexecution\s+guidelines?\b|\busage\s+guidelines?\b"
        )
        when_not_call_pattern = re.compile(
            r"(?i)\bwhen\s+not\s+to\s+(?:call|invoke|use)\b|\bdo\s+not\s+call\b|\bnever\s+call\b|\bnegative\s+contract\b"
        )

        for tool_name, agents in sorted(active_tools.items()):
            tool_path, tool_type = self._resolve_tool_path(tool_name)
            agents_str = ", ".join(agents)

            if tool_type == "builtin":
                continue

            if tool_type == "missing" or tool_path is None:
                issues.append(
                    {
                        "code": "MISSING_DECLARED_TOOL_FILE",
                        "tool": tool_name,
                        "agents": agents,
                        "file": str(self.workspace_path / "tools" / tool_name),
                        "message": (
                            f"Tool '{tool_name}' declared in agent config(s) [{agents_str}]"
                            f" is missing its definition file under tools/{tool_name}/."
                        ),
                    }
                )
                continue

            try:
                rel_path = str(tool_path.relative_to(self.workspace_path))
            except ValueError:
                rel_path = str(tool_path)

            doc, line_no = self._extract_tool_docstring(
                tool_path, tool_name, tool_type
            )

            if not doc or not doc.strip():
                issues.append(
                    {
                        "code": "MISSING_TOOL_DOCSTRING",
                        "tool": tool_name,
                        "agents": agents,
                        "file": rel_path,
                        "line": line_no,
                        "message": (
                            f"Tool '{tool_name}' (declared in: {agents_str}) lacks a"
                            f" docstring or description in {rel_path}."
                        ),
                        "recommended": (
                            f"Add a complete docstring to '{tool_name}' explaining its capabilities,"
                            " input arguments, return values, and explicit execution boundaries."
                        ),
                    }
                )
                continue

            if tool_type == "python":
                if not when_call_pattern.search(doc):
                    issues.append(
                        {
                            "code": "MISSING_TOOL_WHEN_TO_CALL",
                            "tool": tool_name,
                            "agents": agents,
                            "file": rel_path,
                            "line": line_no,
                            "message": (
                                f"Tool '{tool_name}' docstring in {rel_path} lacks an explicit"
                                " 'When to Call:' execution contract."
                            ),
                            "recommended": (
                                "Add a 'When to Call:' section specifying positive trigger conditions."
                            ),
                        }
                    )
                if not when_not_call_pattern.search(doc):
                    issues.append(
                        {
                            "code": "MISSING_TOOL_WHEN_NOT_TO_CALL",
                            "tool": tool_name,
                            "agents": agents,
                            "file": rel_path,
                            "line": line_no,
                            "message": (
                                f"Tool '{tool_name}' docstring in {rel_path} lacks an explicit"
                                " 'When NOT to Call:' negative boundary contract."
                            ),
                            "recommended": (
                                "Add a 'When NOT to Call:' section specifying negative operational constraints."
                            ),
                        }
                    )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "tools_evaluated": len(active_tools),
        }

    def audit_tool_conversational_pacing(self) -> dict[str, Any]:
        """Audits latency-sensitive vs instant tools for conversational pacing directives."""
        issues: list[dict[str, Any]] = []
        active_tools = self._get_active_agent_tools()

        pacing_pattern = re.compile(
            r"(?i)\b(?:conversational\s+)?pacing\s+phrase\b|\bbefore\s+calling\s+this\s+tool,\s+speak\b|\bspeak\s+a\s+brief\b|\bspoken\s+pacing\b"
        )

        for tool_name, agents in sorted(active_tools.items()):
            tool_path, tool_type = self._resolve_tool_path(tool_name)
            if tool_type in ("builtin", "missing") or tool_path is None:
                continue

            try:
                rel_path = str(tool_path.relative_to(self.workspace_path))
            except ValueError:
                rel_path = str(tool_path)

            doc, line_no = self._extract_tool_docstring(
                tool_path, tool_name, tool_type
            )
            if not doc or not doc.strip():
                continue

            is_fast = self._is_fast_or_terminal_tool(tool_name)
            has_pacing = bool(pacing_pattern.search(doc))
            agents_str = ", ".join(agents)

            if not is_fast and not has_pacing:
                issues.append(
                    {
                        "code": "MISSING_TOOL_CONVERSATIONAL_PACING",
                        "tool": tool_name,
                        "agents": agents,
                        "file": rel_path,
                        "line": line_no,
                        "message": (
                            f"Latency-sensitive tool '{tool_name}' (declared in: {agents_str}) lacks"
                            f" a conversational pacing directive in {rel_path}. In Gemini Composite V1,"
                            " speak a brief natural bridge phrase before calling to prevent caller dead air"
                            " during remote execution."
                        ),
                        "recommended": (
                            "Before calling this tool, speak a brief, natural conversational pacing phrase"
                            " (e.g., 'Let me check that for you...')."
                        ),
                    }
                )
            elif is_fast and has_pacing:
                issues.append(
                    {
                        "code": "IMPROPER_PACING_ON_FAST_TOOL",
                        "tool": tool_name,
                        "agents": agents,
                        "file": rel_path,
                        "line": line_no,
                        "message": (
                            f"Fast/terminal tool '{tool_name}' in {rel_path} should NOT have a"
                            " conversational pacing directive. Emitting pacing phrases before instant routing"
                            " or session exit creates awkward caller utterances."
                        ),
                        "recommended": (
                            "Remove conversational pacing directive from fast/terminal tool docstring."
                        ),
                    }
                )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "tools_evaluated": len(active_tools),
        }

    def audit(self) -> dict[str, Any]:
        """Executes all audit passes and returns a complete compliance audit report."""
        app_data = self._read_app_json()

        passes = {
            "app_audio_profile": self.audit_audio_profile(app_data),
            "accent_specifications": self.audit_accent_specifications(app_data),
            "multilang_coverage": self.audit_multilang_coverage(app_data),
            "prohibited_xml_tags": self.audit_prohibited_xml_tags(),
            "permissive_contradictions": self.audit_permissive_contradictions(),
            "unregistered_template_variables": (
                self.audit_unregistered_template_variables(app_data)
            ),
            "variable_setting_antipatterns": (
                self.audit_variable_setting_antipatterns()
            ),
            "inert_tags": self.audit_inert_tags(),
            "natural_speech_cues": self.audit_natural_speech_cues(),
            "anti_looping_and_stability": self.audit_anti_looping_and_stability(
                app_data
            ),
            "tool_docstring_contracts": self.audit_tool_docstring_contracts(),
            "tool_conversational_pacing": (
                self.audit_tool_conversational_pacing()
            ),
        }

        total_issues = sum(len(p.get("issues", [])) for p in passes.values())

        return {
            "workspace": str(self.workspace_path),
            "app_json": (
                str(self.app_json_path) if self.app_json_path.exists() else None
            ),
            "overall_status": "PASSED" if total_issues == 0 else "FAILED",
            "total_issues": total_issues,
            "passes": passes,
        }

    def to_lint_report(self) -> LintReport:
        """Converts voice audit issues to a cxas-scrapi standard LintReport."""
        audit_res = self.audit()
        report = LintReport()

        for _pass_name, pass_data in audit_res.get("passes", {}).items():
            for issue in pass_data.get("issues", []):
                file_path = issue.get("file", str(self.app_json_path))
                line = issue.get("line")
                code = issue.get("code", "VOICE_RULE")
                msg = issue.get("message", "")
                fix = issue.get("recommended", "")

                # P0/P1 map to ERROR, P2 maps to WARNING
                is_error = code in {
                    "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
                    "MISSING_DIRECTORS_NOTE",
                    "MISSING_AUDIO_PROFILE_HEADER",
                    "MISSING_DIRECTORS_NOTE_HEADER",
                    "MISSING_TRANSCRIPT_HOOK",
                    "LOCALE_CODE_IN_ACCENT",
                    "PROHIBITED_XML_TAG_FOUND",
                    "PERMISSIVE_CONTRADICTION_FOUND",
                    "UNREGISTERED_TEMPLATE_VARIABLE",
                    "MISSING_DECLARED_TOOL_FILE",
                    "MISSING_SYNTHESIZE_SPEECH_CONFIG_LANG",
                    "MISSING_VOICE_IDENTIFIER",
                    "MISSING_DIRECTORS_NOTE_INSTRUCTION",
                    "ACCENT_DIRECTIVE_MISMATCH",
                    "MISSING_LANGUAGE_SESSION_VAR",
                    "A007_FAIL_MISSING_LANG",
                    "A007_FAIL_MISSING_VOICE",
                    "A007_FAIL_MISSING_INSTRUCTION",
                    "A007_FAIL_ACCENT_MISMATCH",
                    "A007_FAIL_MISSING_VAR",
                    "LOW_SAMPLING_TEMPERATURE",
                    "CONVERSATIONAL_SHELL_AGENT_FOUND",
                    "MISSING_TOOL_DOCSTRING",
                    "MISSING_TOOL_WHEN_TO_CALL",
                    "MISSING_TOOL_WHEN_NOT_TO_CALL",
                    "MISSING_TOOL_CONVERSATIONAL_PACING",
                    "IMPROPER_PACING_ON_FAST_TOOL",
                }
                sev = (
                    getattr(Severity, "ERROR", "error")
                    if is_error
                    else getattr(Severity, "WARNING", "warning")
                )

                result = LintResult(
                    file=file_path,
                    rule_id=code,
                    severity=sev,
                    message=msg,
                    line=line,
                    fix_suggestion=fix,
                )
                report.add(result)

        return report

    def generate_prioritized_report(self) -> dict[str, Any]:
        """Generates a prioritized report of suggested agent improvements focusing on voice and conversational naturalness."""
        audit_res = self.audit()

        p0_issues: list[dict[str, Any]] = []
        p1_issues: list[dict[str, Any]] = []
        p2_issues: list[dict[str, Any]] = []

        p0_codes = {
            "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
            "MISSING_DIRECTORS_NOTE",
            "MISSING_AUDIO_PROFILE_HEADER",
            "MISSING_DIRECTORS_NOTE_HEADER",
            "MISSING_TRANSCRIPT_HOOK",
            "LOCALE_CODE_IN_ACCENT",
            "PROHIBITED_XML_TAG_FOUND",
            "PERMISSIVE_CONTRADICTION_FOUND",
            "UNREGISTERED_TEMPLATE_VARIABLE",
            "UNSAFE_CALLBACK_STATE_ACCESS",
            "UNCONDITIONAL_CALLBACK_STATE_OVERWRITE",
            "MISSING_DECLARED_TOOL_FILE",
        }

        p1_codes = {
            "MISSING_SYNTHESIZE_SPEECH_CONFIG_LANG",
            "MISSING_VOICE_IDENTIFIER",
            "MISSING_DIRECTORS_NOTE_INSTRUCTION",
            "ACCENT_DIRECTIVE_MISMATCH",
            "MISSING_LANGUAGE_SESSION_VAR",
            "A007_FAIL_MISSING_LANG",
            "A007_FAIL_MISSING_VOICE",
            "A007_FAIL_MISSING_INSTRUCTION",
            "A007_FAIL_ACCENT_MISMATCH",
            "A007_FAIL_MISSING_VAR",
            "LOW_SAMPLING_TEMPERATURE",
            "CONVERSATIONAL_SHELL_AGENT_FOUND",
            "MISSING_TOOL_DOCSTRING",
            "MISSING_TOOL_WHEN_TO_CALL",
            "MISSING_TOOL_WHEN_NOT_TO_CALL",
            "MISSING_TOOL_CONVERSATIONAL_PACING",
            "IMPROPER_PACING_ON_FAST_TOOL",
        }

        for pass_name, pass_data in audit_res.get("passes", {}).items():
            for issue in pass_data.get("issues", []):
                code = issue.get("code")
                item = {
                    "pass": pass_name,
                    "code": code,
                    "message": issue.get("message"),
                    "details": issue,
                }
                if code in p0_codes:
                    item["priority"] = "P0"
                    item["category"] = "Critical Voice & Synthesis Blocker"
                    p0_issues.append(item)
                elif code in p1_codes:
                    item["priority"] = "P1"
                    item["category"] = "High Impact Multi-Language & Stability"
                    p1_issues.append(item)
                else:
                    item["priority"] = "P2"
                    item["category"] = (
                        "Medium Impact Hygiene & Conversational Texture"
                    )
                    p2_issues.append(item)

        markdown_report = self._format_markdown_report(
            audit_res, p0_issues, p1_issues, p2_issues
        )

        return {
            "workspace": str(self.workspace_path),
            "overall_status": audit_res["overall_status"],
            "total_issues": audit_res["total_issues"],
            "priority_counts": {
                "P0": len(p0_issues),
                "P1": len(p1_issues),
                "P2": len(p2_issues),
            },
            "p0_critical_voice_blockers": p0_issues,
            "p1_multilang_and_stability": p1_issues,
            "p2_hygiene_and_texture": p2_issues,
            "markdown_report": markdown_report,
        }

    def _format_markdown_report(
        self,
        audit_res: dict[str, Any],
        p0: list[dict[str, Any]],
        p1: list[dict[str, Any]],
        p2: list[dict[str, Any]],
    ) -> str:
        """Formats prioritized audit results into a readable Markdown report."""
        lines = []
        lines.append(
            "# CXAS Voice & Conversational Optimization Prioritized Report\n"
        )
        lines.append(f"**Workspace:** `{self.workspace_path}`")
        lines.append(
            "**Overall Compliance Status:**"
            f" `{audit_res['overall_status']}` ({audit_res['total_issues']} Issues"
            " Detected)\n"
        )
        lines.append("### Summary of Prioritized Improvements")
        lines.append(
            "- 🔴 **Priority P0 (Critical Voice Naturalness & Synthesis"
            f" Blockers):** {len(p0)}"
        )
        lines.append(
            "- 🟡 **Priority P1 (High Impact Multi-Language & Stability):**"
            f" {len(p1)}"
        )
        lines.append(
            "- 🟢 **Priority P2 (Medium Impact Hygiene & Speech Texture):**"
            f" {len(p2)}\n"
        )
        lines.append("---")

        if p0:
            lines.append(
                "\n## 🔴 Priority P0: Critical Voice & Synthesis Blockers (Fix"
                " First)\n"
            )
            lines.append(
                "These issues directly degrade speech synthesis quality, cause"
                " Director's Note prompt leakage into spoken audio, trigger platform"
                " safety abortions, or distort vocal persona accents.\n"
            )
            for idx, item in enumerate(p0, start=1):
                lines.append(f"### P0.{idx} {item['code']}")
                lines.append(f"- **Message:** {item['message']}")
                lines.append(f"- **Audit Pass:** `{item['pass']}`")
                lines.append("")

        if p1:
            lines.append(
                "\n## 🟡 Priority P1: High Impact Multi-Language & Stability"
                " Improvements\n"
            )
            lines.append(
                "These issues break Multilingual Coverage & Language Drift Prevention,"
                " risk speaker drift during long calls, or cause deterministic acoustic"
                " repetition loops.\n"
            )
            for idx, item in enumerate(p1, start=1):
                lines.append(f"### P1.{idx} {item['code']}")
                lines.append(f"- **Message:** {item['message']}")
                lines.append(f"- **Audit Pass:** `{item['pass']}`")
                lines.append("")

        if p2:
            lines.append(
                "\n## 🟢 Priority P2: Medium Impact Hygiene & Conversational"
                " Texture\n"
            )
            lines.append(
                "These issues waste context tokens, use inert acoustic tags, or"
                " introduce IVR-style conversational dead-ends.\n"
            )
            for idx, item in enumerate(p2, start=1):
                lines.append(f"### P2.{idx} {item['code']}")
                lines.append(f"- **Message:** {item['message']}")
                lines.append(f"- **Audit Pass:** `{item['pass']}`")
                lines.append("")

        if not (p0 or p1 or p2):
            lines.append(
                "\n🎉 **No issues detected! Application is 100% compliant with"
                " Composite Voice Best Practices.**"
            )

        return "\n".join(lines)

    def remediate_audio_profile_and_multilang(
        self, app_data: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], list[str]]:
        """Remediates audioProcessingConfig, Director's Notes, and Multilingual Coverage in app.json."""
        if app_data is None:
            app_data = self._read_app_json()
        if not isinstance(app_data, dict):
            app_data = {}

        changes: list[str] = []

        if not isinstance(app_data.get("modelSettings"), dict):
            app_data["modelSettings"] = {}
            changes.append("Initialized modelSettings dictionary.")
        model_settings = app_data["modelSettings"]

        if model_settings.get("model") != "gemini-composite-v1":
            model_settings["model"] = "gemini-composite-v1"
            changes.append("Set modelSettings.model to 'gemini-composite-v1'.")
        if model_settings.get("temperature") != 1.0:
            model_settings["temperature"] = 1.0
            changes.append("Set modelSettings.temperature to 1.0.")

        if not isinstance(app_data.get("languageSettings"), dict):
            app_data["languageSettings"] = {}
            changes.append("Initialized languageSettings dictionary.")
        lang_settings = app_data["languageSettings"]

        default_lang = lang_settings.get("defaultLanguageCode")
        if not default_lang or not isinstance(default_lang, str):
            default_lang = "en-US"
        lang_settings["defaultLanguageCode"] = default_lang

        supported_langs = lang_settings.get("supportedLanguageCodes")
        if not isinstance(supported_langs, list):
            supported_langs = []
            lang_settings["supportedLanguageCodes"] = supported_langs

        valid_supported = [
            lang for lang in supported_langs if isinstance(lang, str)
        ]
        all_langs = set([default_lang, *valid_supported])

        if not isinstance(app_data.get("audioProcessingConfig"), dict):
            app_data["audioProcessingConfig"] = {}
            changes.append("Initialized audioProcessingConfig dictionary.")
        audio_cfg = app_data["audioProcessingConfig"]

        if not isinstance(audio_cfg.get("synthesizeSpeechConfigs"), dict):
            audio_cfg["synthesizeSpeechConfigs"] = {}
            changes.append("Initialized synthesizeSpeechConfigs dictionary.")
        speech_cfgs = audio_cfg["synthesizeSpeechConfigs"]

        for k in list(speech_cfgs.keys()):
            if isinstance(k, str):
                norm_k = k.strip().lower().replace("_", "-")
                # Do not append root prefixes (e.g. "es") to supportedLanguageCodes
                if "-" not in norm_k:
                    continue
                matching_declared = [
                    lang
                    for lang in all_langs
                    if lang.strip().lower().replace("_", "-") == norm_k
                ]
                if not matching_declared:
                    all_langs.add(k)
                    if k != default_lang and k not in supported_langs:
                        supported_langs.append(k)
                        changes.append(
                            f"Added '{k}' to languageSettings.supportedLanguageCodes."
                        )

        for lang in sorted(all_langs):
            golden_note = generate_golden_directors_note(lang)
            default_voice = get_default_voice(lang)
            target_accent = get_locale_accent(lang)

            norm_lang = lang.strip().lower().replace("_", "-")
            matched_key = None
            cfg = None
            if lang in speech_cfgs and isinstance(speech_cfgs[lang], dict):
                matched_key = lang
                cfg = speech_cfgs[lang]
            else:
                for k, v in speech_cfgs.items():
                    if (
                        isinstance(k, str)
                        and isinstance(v, dict)
                        and k.strip().lower().replace("_", "-") == norm_lang
                    ):
                        matched_key = k
                        cfg = v
                        break

            if matched_key is None or cfg is None:
                speech_cfgs[lang] = {
                    "voice": default_voice,
                    "speakingRate": 1.0,
                    "instruction": golden_note,
                }
                changes.append(
                    f"Injected golden synthesizeSpeechConfigs for language '{lang}'."
                )
            else:
                if not cfg.get("voice") or not isinstance(
                    cfg.get("voice"), str
                ):
                    cfg["voice"] = default_voice
                    changes.append(
                        f"Set default voice '{default_voice}' for '{matched_key}'."
                    )
                if "speakingRate" not in cfg or not isinstance(
                    cfg.get("speakingRate"), (int, float)
                ):
                    cfg["speakingRate"] = 1.0
                    changes.append(f"Set speakingRate 1.0 for '{matched_key}'.")

                current_inst = cfg.get("instruction")
                if not isinstance(current_inst, str):
                    current_inst = ""

                if re.search(
                    r"\bAccent:\s*[^\n\r]+", current_inst, flags=re.IGNORECASE
                ):
                    fixed_accent = re.sub(
                        r"\bAccent:\s*[^\n\r]+",
                        f"Accent: {target_accent}",
                        current_inst,
                        flags=re.IGNORECASE,
                    )
                    if fixed_accent != current_inst:
                        cfg["instruction"] = fixed_accent
                        changes.append(
                            f"Normalized Accent directive to 'Accent: {target_accent}' for"
                            f" '{matched_key}'."
                        )
                        current_inst = fixed_accent

                has_directors_note = bool(
                    re.search(
                        r"^#+\s*director'?s\s*notes?",
                        current_inst,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )
                has_audio_profile = bool(
                    re.search(
                        r"^#+\s*audio\s*profile",
                        current_inst,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )

                if not has_directors_note or not has_audio_profile:
                    cfg["instruction"] = golden_note
                    changes.append(
                        f"Updated '{matched_key}' instruction with complete Golden"
                        " Director's Note."
                    )

                # Check if transcript hook strictly concludes the prompt
                elif not re.search(
                    r"#{2,3}\s*transcript\s*:\s*$",
                    current_inst.strip(),
                    re.IGNORECASE,
                ):
                    if re.search(
                        r"(?i)\n*(?:#{1,3}\s*)?transcript\s*:?\s*$",
                        current_inst,
                    ):
                        fixed_inst = re.sub(
                            r"(?i)\n*(?:#{1,3}\s*)?transcript\s*:?\s*$",
                            "\n\n## Transcript:\n",
                            current_inst,
                        )
                    else:
                        fixed_inst = (
                            current_inst.rstrip() + "\n\n## Transcript:\n"
                        )
                    cfg["instruction"] = fixed_inst
                    changes.append(
                        "Appended missing '## Transcript:' hook to"
                        f" '{matched_key}' instruction."
                    )
                    current_inst = fixed_inst

        for k in list(speech_cfgs.keys()):
            if isinstance(k, str):
                norm_k = k.strip().lower().replace("_", "-")
                matching_declared = [
                    lang
                    for lang in all_langs
                    if lang.strip().lower().replace("_", "-") == norm_k
                ]
                if not matching_declared and "-" not in norm_k:
                    del speech_cfgs[k]
                    changes.append(
                        f"Removed root language key '{k}' from synthesizeSpeechConfigs"
                        " in favor of declared locale configs."
                    )

        return app_data, changes

    # Backwards-compatible alias
    remediate_audio_profile_and_rule_a007 = (
        remediate_audio_profile_and_multilang
    )

    def remediate(self, auto_fix: bool = True) -> dict[str, Any]:
        """Performs automated in-place remediation on workspace audio configuration in app.json."""
        if not auto_fix:
            return {"status": "SKIPPED", "changes": []}

        all_changes: list[str] = []

        app_data, app_changes = self.remediate_audio_profile_and_multilang()
        if app_changes:
            self._write_app_json(app_data)
            all_changes.extend(app_changes)

        return {
            "status": "REMEDIATED" if all_changes else "NO_CHANGES_NEEDED",
            "changes_applied": all_changes,
            "post_remediation_audit": self.audit(),
        }


# Backwards compatible alias classes
CXASVoiceAuditor = VoiceAgentAuditor
AuditAgent = VoiceAgentAuditor


def main() -> None:
    """Main CLI entrypoint for CXAS Voice Configuration Auditor and Auto-Remediator."""
    parser = argparse.ArgumentParser(
        description="CXAS Voice Configuration Auditor and Auto-Remediator."
    )
    parser.add_argument(
        "--workspace",
        "--app-dir",
        dest="workspace",
        type=str,
        default=".",
        help="Path to CXAS workspace directory.",
    )
    parser.add_argument(
        "--app-name",
        type=str,
        default=None,
        help="Optional CXAS / CES application full resource name.",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Perform audit without modifying files.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "Generate a prioritized report of all suggested voice and"
            " conversational naturalness improvements."
        ),
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="Apply automated in-place remediation to local workspace files.",
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
        workspace_dir=args.workspace,
        app_name=args.app_name,
    )

    if args.lint_report:
        report = auditor.to_lint_report()
        if args.json_output:
            print(report.to_json())
        elif hasattr(report, "print_and_exit"):
            report.print_and_exit(json_output=args.json_output)
        else:
            print(report.to_json())
    elif args.report:
        prioritized_report = auditor.generate_prioritized_report()
        if args.json_output:
            print(json.dumps(prioritized_report, indent=2))
        else:
            print(prioritized_report["markdown_report"])
    elif args.remediate:
        result = auditor.remediate(auto_fix=True)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"=== Remediation Status: {result['status']} ===")
            for change in result["changes_applied"]:
                print(f" - {change}")
            print(
                "\nPost-remediation audit status:"
                f" {result['post_remediation_audit']['overall_status']}"
            )
            if result["post_remediation_audit"]["overall_status"] != "PASSED":
                prioritized_report = auditor.generate_prioritized_report()
                print("\n" + "=" * 60)
                print(
                    "=== Unremediated Issues Requiring Prompt/Variable Refactoring ==="
                )
                print("=" * 60 + "\n")
                print(prioritized_report["markdown_report"])

        if result["post_remediation_audit"]["overall_status"] != "PASSED":
            sys.exit(1)
    else:
        report = auditor.audit()
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"=== CXAS Voice Audit: {report['overall_status']} ===")
            print(f"Total Issues Found: {report['total_issues']}")
            for pass_name, pass_data in report["passes"].items():
                status = "PASS" if pass_data["passed"] else "FAIL"
                print(f"\n[{status}] {pass_name}:")
                for issue in pass_data.get("issues", []):
                    print(f"  * {issue.get('message')}")

        if report["overall_status"] != "PASSED":
            sys.exit(1)


if __name__ == "__main__":
    main()
