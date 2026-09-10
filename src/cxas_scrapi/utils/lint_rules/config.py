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

"""App and agent config lint rules (A001-A005).

Validates app.json and agent JSON configuration files.
"""

import json
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


@rule("config")
class InvalidJson(Rule):
    id = "A001"
    name = "config-json-parse"
    description = "Config file must be valid JSON"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"Invalid JSON: {e}",
                )
            ]
        return []


@rule("config")
class MissingRequiredFields(Rule):
    id = "A002"
    name = "config-required-fields"
    description = "Config must have required fields (name, displayName)"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        if file_path.name == "app.json":
            for field_name in ["name", "displayName"]:
                if field_name not in data:
                    results.append(
                        self.make_result(
                            file=rel,
                            message=f"Missing required field: '{field_name}'",
                        )
                    )
        return results


@rule("config")
class AgentToolNotExists(Rule):
    id = "A003"
    name = "config-tool-exists"
    description = "Agent config references non-existent tool"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name == "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        for tool in data.get("tools", []):
            if tool not in context.all_known_tools:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Agent config lists tool"
                            f" '{tool}' but it does"
                            " not exist"
                        ),
                        fix=(
                            "Available tools:"
                            f" {', '.join(sorted(context.all_known_tools))}"
                        ),
                    )
                )
        return results


@rule("config")
class AgentMissingInstruction(Rule):
    id = "A004"
    name = "config-missing-instruction"
    description = "Agent directory must have an instruction.txt file"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name == "app.json":
            return []

        agent_dir = file_path.parent
        instruction = agent_dir / "instruction.txt"
        if not instruction.exists():
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Agent '{agent_dir.name}'"
                        " has config but no"
                        " instruction.txt"
                    ),
                    fix=(
                        "Create instruction.txt"
                        " with <role>, <persona>,"
                        " and <taskflow> sections"
                    ),
                )
            ]
        return []


@rule("config")
class RootAgentMissingEndSession(Rule):
    id = "A005"
    name = "config-root-missing-end-session"
    description = "Root agent must have end_session tool associated"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        root_agent_name = data.get("rootAgent")
        if not root_agent_name:
            return []

        agent_dir = file_path.parent / "agents" / root_agent_name
        agent_json = agent_dir / f"{root_agent_name}.json"
        if not agent_json.exists():
            return []

        try:
            agent_data = json.loads(agent_json.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        tools = agent_data.get("tools", [])
        if "end_session" not in tools:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Root agent"
                        f" '{root_agent_name}' is"
                        " missing 'end_session'"
                        " tool — the agent cannot"
                        " terminate conversations"
                    ),
                    fix=(
                        "Associate end_session with"
                        " the root agent via:"
                        " agents_client"
                        ".update_agent("
                        "agent_name=...,"
                        " tools=[...,"
                        " 'end_session'])"
                    ),
                )
            ]
        return []


@rule("config")
class AppRootAgentValidation(Rule):
    id = "A006"
    name = "config-root-agent"
    description = (
        "App config must have a valid rootAgent pointing to an "
        "existing agent directory"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))

        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []

        # 1. Check case sensitivity / incorrect snake_case
        if "root_agent" in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Found 'root_agent' in app.json, but CXAS strictly "
                        "requires camelCase 'rootAgent'"
                    ),
                    fix="Rename 'root_agent' to 'rootAgent'",
                )
            )
            return results

        # 2. Check if rootAgent is missing
        root_agent_name = data.get("rootAgent")
        if not root_agent_name:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Missing required field 'rootAgent' in app.json. "
                        "An app must have a rootAgent to handle incoming "
                        "sessions"
                    ),
                    fix="Add 'rootAgent': '<agent_directory_name>' to app.json",
                )
            )
            return results

        # 3. Check if rootAgent is not a string
        if not isinstance(root_agent_name, str):
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Field 'rootAgent' in app.json must be a string, "
                        f"got {type(root_agent_name).__name__}"
                    ),
                )
            )
            return results

        # 4. Check if rootAgent exists under agents/
        agent_dir = file_path.parent / "agents" / root_agent_name
        if not agent_dir.exists() or not agent_dir.is_dir():
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"rootAgent '{root_agent_name}' specified in app.json "
                        "does not exist under the agents/ directory"
                    ),
                    fix=(
                        f"Create the directory 'agents/{root_agent_name}' "
                        "or fix the 'rootAgent' reference in app.json"
                    ),
                )
            )
            return results

        # 5. Check if <rootAgent>.json exists
        agent_json = agent_dir / f"{root_agent_name}.json"
        if not agent_json.exists():
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Root agent '{root_agent_name}' exists but is "
                        f"missing required '{root_agent_name}.json' file"
                    ),
                    fix=(
                        f"Create file 'agents/"
                        f"{root_agent_name}/{root_agent_name}.json'"
                    ),
                )
            )

        return results


# ── Composite V1 Voice Rules (A007-A010) ──────────────────────────────────

LOCALE_TO_ACCENT: dict[str, str] = {
    "en": "American English",
    "en-US": "American English",
    "en-GB": "British English",
    "en-AU": "Australian English",
    "en-CA": "Canadian English",
    "en-IN": "Indian English",
    "en-hi": "Hinglish",
    "en-es": "Spanglish",
    "es": "Spanish accent",
    "es-US": "Spanish accent",
    "es-ES": "Castilian Spanish",
    "es-MX": "Latin American Spanish",
    "fr": "Metropolitan French",
    "fr-FR": "Metropolitan French",
    "fr-CA": "French Canadian",
    "de": "German",
    "de-DE": "German",
    "ja": "Japanese",
    "ja-JP": "Japanese",
    "pt": "Brazilian Portuguese",
    "pt-BR": "Brazilian Portuguese",
    "it": "Italian",
    "it-IT": "Italian",
    "zh": "Mandarin Chinese",
    "zh-CN": "Mandarin Chinese",
    "ko": "Korean",
    "ko-KR": "Korean",
    "nl": "Dutch",
    "nl-NL": "Dutch",
    "nl-BE": "Flemish Dutch",
    "hi": "Hindi",
    "hi-IN": "Hindi",
    "sv": "Swedish",
    "sv-SE": "Swedish",
    "da": "Danish",
    "da-DK": "Danish",
    "fi": "Finnish",
    "fi-FI": "Finnish",
    "pl": "Polish",
    "pl-PL": "Polish",
    "tr": "Turkish",
    "tr-TR": "Turkish",
    "id": "Indonesian",
    "id-ID": "Indonesian",
    "mr": "Marathi",
    "mr-IN": "Marathi",
    "ro": "Romanian",
    "ro-RO": "Romanian",
    "ta": "Tamil",
    "ta-IN": "Tamil",
    "te": "Telugu",
    "te-IN": "Telugu",
    "vi": "Vietnamese",
    "vi-VN": "Vietnamese",
}

NORMALIZED_LOCALE_TO_ACCENT: dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in LOCALE_TO_ACCENT.items()
}


def get_locale_accent(locale: str) -> str:
    """Returns the natural language accent description for a locale code."""
    normalized = locale.strip().lower().replace("_", "-")
    if normalized in NORMALIZED_LOCALE_TO_ACCENT:
        return NORMALIZED_LOCALE_TO_ACCENT[normalized]
    lang_prefix = normalized.split("-")[0]
    if lang_prefix in NORMALIZED_LOCALE_TO_ACCENT:
        return NORMALIZED_LOCALE_TO_ACCENT[lang_prefix]
    return LOCALE_TO_ACCENT.get(locale, f"{locale} accent")


def _find_speech_config(
    speech_configs: dict, locale: str
) -> tuple[str, dict] | None:
    """Finds matching speech configuration for a locale."""
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


@rule("config", models=["gemini-composite-v1"])
class CompositeAudioProfile(Rule):
    id = "A007"
    name = "composite-audio-profile"
    description = (
        "Validates synthesizeSpeechConfigs, Audio Profile, and Director's "
        "Notes for Gemini Composite V1"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        results = []
        audio_cfg = (
            data.get("audioProcessingConfig")
            or data.get("audio_processing_config")
            or {}
        )
        speech_configs = (
            audio_cfg.get("synthesizeSpeechConfigs")
            or audio_cfg.get("synthesize_speech_configs")
            if isinstance(audio_cfg, dict)
            else None
        )

        if not isinstance(speech_configs, dict) or not speech_configs:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "app.json is missing "
                        "audioProcessingConfig.synthesizeSpeechConfigs "
                        "required for Gemini Composite V1 voice agents"
                    ),
                    fix=(
                        "Add audioProcessingConfig.synthesizeSpeechConfigs "
                        "with at least one locale entry (e.g., 'en-US')"
                    ),
                )
            )
            return results

        import re  # noqa: PLC0415

        for locale, cfg in speech_configs.items():
            if not isinstance(cfg, dict):
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{locale}'] lacks a "
                            "Director's Note instruction"
                        ),
                    )
                )
                continue

            instruction = cfg.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{locale}'] lacks a "
                            "Director's Note instruction"
                        ),
                    )
                )
                continue

            has_audio_profile = bool(
                re.search(
                    r"^#+\s*audio\s*profile",
                    instruction,
                    re.IGNORECASE | re.MULTILINE,
                )
            )
            if not has_audio_profile:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{locale}'].instruction "
                            "missing '# Audio Profile' header"
                        ),
                        fix=(
                            "Add '# Audio Profile' section to speech "
                            "instruction"
                        ),
                    )
                )

            has_directors_note = bool(
                re.search(
                    r"^#+\s*director'?s\s*notes?",
                    instruction,
                    re.IGNORECASE | re.MULTILINE,
                )
            )
            if not has_directors_note:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{locale}'].instruction "
                            "missing '# Director\'s note' header"
                        ),
                        fix=(
                            "Add '# Director\'s note' section to speech "
                            "instruction"
                        ),
                    )
                )

            has_trailing_transcript_hook = bool(
                re.search(
                    r"#{2,3}\s*transcript\s*:\s*$",
                    instruction.strip(),
                    re.IGNORECASE,
                )
            )
            if not has_trailing_transcript_hook:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{locale}'].instruction "
                            "missing trailing '## Transcript:' hook"
                        ),
                        fix="End instruction with '## Transcript:\\n'",
                    )
                )

        return results


@rule("config", models=["gemini-composite-v1"])
class CompositeAccentSpecification(Rule):
    id = "A008"
    name = "composite-accent-specification"
    description = (
        "Ensures natural language accent names instead of raw locale "
        "codes in Director's Notes"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        audio_cfg = (
            data.get("audioProcessingConfig")
            or data.get("audio_processing_config")
            or {}
        )
        speech_configs = (
            audio_cfg.get("synthesizeSpeechConfigs")
            or audio_cfg.get("synthesize_speech_configs")
            if isinstance(audio_cfg, dict)
            else {}
        )
        if not isinstance(speech_configs, dict):
            return []

        import re  # noqa: PLC0415

        locale_code_pattern = re.compile(
            r"Accent:\s*([a-z]{2}(?:[-_][a-z0-9]{2,3})?)\b", re.IGNORECASE
        )

        results = []
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
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"synthesizeSpeechConfigs['{locale}']: "
                                f"Locale code '{invalid_code}' used in "
                                "Accent directive. Use natural language name "
                                f"'{recommended}' instead."
                            ),
                            fix=f"Change to 'Accent: {recommended}'",
                        )
                    )

        return results


@rule("config", models=["gemini-composite-v1"])
class CompositeMultilangCoverage(Rule):
    id = "A009"
    name = "composite-multilang-coverage"
    description = (
        "Audits multi-language voice parity across declared languages "
        "for Gemini Composite V1"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        lang_settings = (
            data.get("languageSettings")
            or data.get("language_settings")
            or {}
        )
        if not isinstance(lang_settings, dict):
            lang_settings = {}

        default_lang = (
            lang_settings.get("defaultLanguageCode")
            or lang_settings.get("default_language_code")
        )
        supported_langs = (
            lang_settings.get("supportedLanguageCodes")
            or lang_settings.get("supported_language_codes")
            or []
        )
        if not isinstance(supported_langs, list):
            supported_langs = []

        all_declared_langs = set()
        if default_lang and isinstance(default_lang, str):
            all_declared_langs.add(default_lang)
        all_declared_langs.update(
            [lang for lang in supported_langs if isinstance(lang, str)]
        )

        if not all_declared_langs:
            return []

        audio_cfg = (
            data.get("audioProcessingConfig")
            or data.get("audio_processing_config")
            or {}
        )
        speech_configs = (
            audio_cfg.get("synthesizeSpeechConfigs")
            or audio_cfg.get("synthesize_speech_configs")
            if isinstance(audio_cfg, dict)
            else {}
        )
        if not isinstance(speech_configs, dict):
            speech_configs = {}

        import re  # noqa: PLC0415

        results = []
        for lang in sorted(all_declared_langs):
            match_res = _find_speech_config(speech_configs, lang)
            if not match_res:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Declared language '{lang}' has no entry in "
                            "audioProcessingConfig.synthesizeSpeechConfigs"
                        ),
                        fix=(
                            f"Add synthesizeSpeechConfigs['{lang}'] with "
                            "voice and Director's Note instruction"
                        ),
                    )
                )
            else:
                matched_key, cfg = match_res
                if not cfg.get("voice") or not isinstance(
                    cfg.get("voice"), str
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Declared language '{lang}' "
                                f"(key: '{matched_key}') is missing a voice "
                                "identifier"
                            ),
                        )
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
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Declared language '{lang}' "
                                f"(key: '{matched_key}') is missing a "
                                "complete Director's Note instruction"
                            ),
                        )
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
                            results.append(
                                self.make_result(
                                    file=rel,
                                    message=(
                                        f"synthesizeSpeechConfigs"
                                        f"['{matched_key}']: Specifies "
                                        f"'Accent: {found_accent}'. Expected "
                                        f"'Accent: {expected_accent}'."
                                    ),
                                    fix=(
                                        "Change to 'Accent: "
                                        f"{expected_accent}'"
                                    ),
                                    severity=Severity.WARNING,
                                )
                            )

        return results


@rule("config", models=["gemini-composite-v1"])
class CompositeSamplingTemperature(Rule):
    id = "A010"
    name = "composite-sampling-temperature"
    description = (
        "Enforces modelSettings.temperature = 1.0 for Gemini Composite V1 "
        "to prevent acoustic repetition deadlocks"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if file_path.name != "app.json":
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        model_settings = (
            data.get("modelSettings") or data.get("model_settings") or {}
        )
        if not isinstance(model_settings, dict):
            model_settings = {}

        temp = model_settings.get("temperature")
        results = []

        if temp is None:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "modelSettings.temperature is missing from app.json. "
                        "Set to 1.0 to avoid acoustic repetition deadlocks "
                        "in Gemini Composite V1."
                    ),
                    fix="Add 'temperature': 1.0 to modelSettings",
                )
            )
        elif not isinstance(temp, (int, float)) or temp < 1.0 or temp > 1.0:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"modelSettings.temperature is {temp}. Set to 1.0 "
                        "to avoid deterministic acoustic repetition deadlocks "
                        "in Gemini Composite V1."
                    ),
                    fix="Change modelSettings.temperature to 1.0",
                )
            )

        return results
