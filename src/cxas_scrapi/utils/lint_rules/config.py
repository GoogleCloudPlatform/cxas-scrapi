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
from typing import Any

import yaml

from cxas_scrapi.utils.env_vars import (
    ENV_VAR_PLACEHOLDER,
    infer_schema_type,
    parse_env_variable_declarations,
    validate_value_for_schema,
)
from cxas_scrapi.utils.linter import (
    APP_CONFIG_NAMES,
    CONFIG_EXTENSIONS,
    LintContext,
    LintResult,
    Rule,
    Severity,
    _find_dir_config,
    _load_config_dict,
    is_voice_app,
    parse_config_content,
    rel_to_project,
    rule,
)

_AUDIO_CONFIG_KEYS = ("audioProcessingConfig", "audio_processing_config")
_SYNTHESIZE_SPEECH_KEYS = (
    "synthesizeSpeechConfigs",
    "synthesize_speech_configs",
)
_LANGUAGE_SETTINGS_KEYS = ("languageSettings", "language_settings")
_DEFAULT_LANG_KEYS = ("defaultLanguageCode", "default_language_code")
_SUPPORTED_LANG_KEYS = ("supportedLanguageCodes", "supported_language_codes")
_MULTILINGUAL_KEYS = (
    "enableMultilingualSupport",
    "enable_multilingual_support",
)


def _get_field(mapping: dict[str, Any], camel: str, snake: str) -> Any:
    """Return ``mapping[camel]`` if present, else ``mapping.get(snake)``."""
    if camel in mapping:
        return mapping[camel]
    return mapping.get(snake)


def _find_agent_config_file(
    agent_dir: Path, root_agent_name: str
) -> Path | None:
    """Return ``<dir>.{json,yaml,yml}`` or ``<root>.{json,yaml,yml}``."""
    cfg = _find_dir_config(agent_dir)
    if cfg is not None:
        return cfg
    for ext in CONFIG_EXTENSIONS:
        candidate = agent_dir / f"{root_agent_name}{ext}"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


@rule("config")
class InvalidJson(Rule):
    id = "A001"
    name = "config-json-parse"
    description = "Config file must be valid JSON or YAML"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if file_path.suffix.lower() in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError as e:
                return [
                    self.make_result(
                        file=rel,
                        message=f"Invalid YAML: {e}",
                    )
                ]
            if not isinstance(data, dict):
                return [
                    self.make_result(
                        file=rel,
                        message="Invalid YAML: expected a mapping",
                    )
                ]
            return []

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
        data = parse_config_content(file_path, content)
        if data is None:
            return []

        results = []
        if file_path.name in APP_CONFIG_NAMES:
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

        if file_path.name in APP_CONFIG_NAMES:
            return []

        data = parse_config_content(file_path, content)
        if data is None:
            return []

        raw_tools = data.get("tools", [])
        if not isinstance(raw_tools, list):
            return []

        results = []
        for tool in raw_tools:
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


_NON_LLM_AGENT_KEYS: frozenset[str] = frozenset(
    {
        "guidedAgent",
        "guided_agent",
        "remoteDialogflowAgent",
        "remote_dialogflow_agent",
    }
)


def _resolve_agent_dir(agents_dir: Path, agent_ref: str) -> Path | None:
    """Resolve an agent reference to its directory under ``agents/``.

    CES exports may set ``rootAgent`` to the agent's ``displayName`` (which can
    contain spaces or differ in casing from the sanitized directory name).
    """
    direct = agents_dir / agent_ref
    if direct.is_dir() and not direct.is_symlink():
        return direct

    sanitized = agents_dir / agent_ref.replace(" ", "_")
    if sanitized.is_dir() and not sanitized.is_symlink():
        return sanitized

    if not agents_dir.is_dir() or agents_dir.is_symlink():
        return None

    ref_norm = agent_ref.replace(" ", "_").lower()
    for subdir in sorted(agents_dir.iterdir()):
        if subdir.is_symlink() or not subdir.is_dir():
            continue
        if subdir.name.replace(" ", "_").lower() == ref_norm:
            return subdir
        agent_cfg_path = _find_dir_config(subdir)
        if agent_cfg_path is not None:
            agent_data = _load_config_dict(agent_cfg_path)
            if (
                isinstance(agent_data, dict)
                and agent_data.get("displayName") == agent_ref
            ):
                return subdir

    return None


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

        if file_path.name in APP_CONFIG_NAMES:
            return []

        data = parse_config_content(file_path, content)
        if data is not None and (_NON_LLM_AGENT_KEYS & data.keys()):
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

        if file_path.name not in APP_CONFIG_NAMES:
            return []

        data = parse_config_content(file_path, content)
        if data is None:
            return []

        root_agent_name = data.get("rootAgent")
        if not root_agent_name or not isinstance(root_agent_name, str):
            return []

        agent_dir = _resolve_agent_dir(
            file_path.parent / "agents", root_agent_name
        )
        if not agent_dir:
            return []

        agent_cfg_path = _find_agent_config_file(agent_dir, root_agent_name)
        if agent_cfg_path is None:
            return []

        agent_data = _load_config_dict(agent_cfg_path)
        if agent_data is None:
            return []

        tools = agent_data.get("tools", [])
        if not isinstance(tools, list) or "end_session" not in tools:
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

        if file_path.name not in APP_CONFIG_NAMES:
            return []

        data = parse_config_content(file_path, content)
        if data is None:
            return []

        results = []

        # 1. Check case sensitivity / incorrect snake_case
        if "root_agent" in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Found 'root_agent' in {file_path.name}, but CXAS "
                        "strictly requires camelCase 'rootAgent'"
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
                        f"Missing required field 'rootAgent' in "
                        f"{file_path.name}. An app must have a rootAgent to "
                        "handle incoming sessions"
                    ),
                    fix=(
                        "Add 'rootAgent': '<agent_directory_name>' to "
                        f"{file_path.name}"
                    ),
                )
            )
            return results

        # 3. Check if rootAgent is not a string
        if not isinstance(root_agent_name, str):
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Field 'rootAgent' in {file_path.name} must be a "
                        f"string, got {type(root_agent_name).__name__}"
                    ),
                )
            )
            return results

        # 4. Check if rootAgent exists under agents/
        agent_dir = _resolve_agent_dir(
            file_path.parent / "agents", root_agent_name
        )
        if not agent_dir:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"rootAgent '{root_agent_name}' specified in "
                        f"{file_path.name} does not exist under the agents/ "
                        "directory"
                    ),
                    fix=(
                        f"Create the directory 'agents/{root_agent_name}' "
                        f"or fix the 'rootAgent' reference in {file_path.name}"
                    ),
                )
            )
            return results

        # 5. Check if <rootAgent>.{json,yaml,yml} exists
        if _find_agent_config_file(agent_dir, root_agent_name) is None:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Root agent '{root_agent_name}' exists but is "
                        f"missing required '{agent_dir.name}.json' file"
                    ),
                    fix=(
                        f"Create file 'agents/"
                        f"{agent_dir.name}/{agent_dir.name}.json'"
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
                            "missing '# Director's note' header"
                        ),
                        fix=(
                            "Add '# Director's note' section to speech "
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
            data.get("languageSettings") or data.get("language_settings") or {}
        )
        if not isinstance(lang_settings, dict):
            lang_settings = {}

        default_lang = lang_settings.get(
            "defaultLanguageCode"
        ) or lang_settings.get("default_language_code")
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
                                        f"Change to 'Accent: {expected_accent}'"
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


@rule("config")
class AppVariableDefaultsAndEnvVars(Rule):
    id = "A017"
    name = "variable-defaults-and-env-vars"
    description = (
        "Validates variableDeclarations default types and cross-checks "
        "$env_var placeholders against environment.json"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in APP_CONFIG_NAMES:
            return []

        rel = rel_to_project(file_path, context)
        data = parse_config_content(file_path, content)
        if data is None:
            return []

        raw_decls = (
            data.get("variableDeclarations")
            if "variableDeclarations" in data
            else data.get("variable_declarations")
        )
        results: list[LintResult] = []
        app_vars: dict[str, dict[str, Any]] = {}
        placeholder_vars: set[str] = set()

        if isinstance(raw_decls, list):
            for decl in raw_decls:
                if not isinstance(decl, dict):
                    continue
                name = decl.get("name")
                schema = decl.get("schema")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(schema, dict)
                ):
                    continue
                app_vars[name] = schema
                if "default" not in schema:
                    continue
                default_val = schema["default"]
                if default_val == ENV_VAR_PLACEHOLDER:
                    placeholder_vars.add(name)
                    continue
                type_err = validate_value_for_schema(default_val, schema)
                if type_err:
                    expected = infer_schema_type(schema) or "matching"
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Variable '{name}' has invalid schema.default "
                                f"({type_err})"
                            ),
                            fix=(
                                f"Set schema.default for '{name}' to a valid "
                                f"{expected} value or '{ENV_VAR_PLACEHOLDER}'"
                            ),
                        )
                    )

        env_path: Path | None = None
        if context.env_file is not None:
            env_path = context.env_file
            env_rel = rel_to_project(env_path, context)
            if not env_path.is_file():
                results.append(
                    self.make_result(
                        file=env_rel,
                        message=f"Environment file '{env_path}' not found",
                        fix="Provide an existing JSON file to --env-file",
                    )
                )
                return results
        else:
            candidate = file_path.parent / "environment.json"
            if candidate.is_file():
                env_path = candidate
                env_rel = rel_to_project(env_path, context)

        if env_path is None:
            return results

        try:
            env_data = json.loads(env_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(
                self.make_result(
                    file=env_rel,
                    message=f"Invalid JSON in {env_path.name}: {e}",
                )
            )
            return results

        parsed = parse_env_variable_declarations(env_data)
        for err in parsed.errors:
            results.append(
                self.make_result(
                    file=env_rel,
                    message=err,
                    fix=(
                        "Declare environment variable defaults under "
                        "'app.variableDeclarations' as "
                        "{'name': '<var>', 'schema': {'default': <value>}}"
                    ),
                )
            )

        for var_name in sorted(placeholder_vars):
            if var_name not in parsed.defaults and (
                var_name not in parsed.seen_names
            ):
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Variable '{var_name}' has schema.default="
                            f"'{ENV_VAR_PLACEHOLDER}' in {file_path.name} but "
                            f"is missing from {env_path.name} under "
                            "'app.variableDeclarations'"
                        ),
                        fix=(
                            f"Add {{'name': '{var_name}', 'schema': "
                            f"{{'default': ...}}}} under "
                            f"'app.variableDeclarations' in {env_path.name}"
                        ),
                    )
                )

        for var_name, val in parsed.defaults.items():
            if var_name not in app_vars:
                results.append(
                    self.make_result(
                        file=env_rel,
                        message=(
                            f"Variable '{var_name}' in {env_path.name} "
                            "'app.variableDeclarations' is not declared in "
                            f"{file_path.name} 'variableDeclarations'"
                        ),
                        fix=(
                            f"Declare '{var_name}' in {file_path.name} with "
                            f"schema.default='{ENV_VAR_PLACEHOLDER}' or remove "
                            f"it from {env_path.name}"
                        ),
                    )
                )
                continue

            app_schema = app_vars[var_name]
            app_type = infer_schema_type(app_schema)
            env_type = parsed.declared_types.get(var_name)
            if app_type and env_type and env_type.upper() != app_type.upper():
                results.append(
                    self.make_result(
                        file=env_rel,
                        message=(
                            f"Variable '{var_name}' in {env_path.name} "
                            f"declares schema.type '{env_type}', which does "
                            f"not match '{app_type}' in {file_path.name}"
                        ),
                        fix=(
                            f"Change schema.type to '{app_type}' or omit "
                            f"schema.type in {env_path.name}"
                        ),
                    )
                )

            type_err = validate_value_for_schema(val, app_schema)
            if type_err:
                results.append(
                    self.make_result(
                        file=env_rel,
                        message=(
                            f"Variable '{var_name}' in {env_path.name} has "
                            f"invalid schema.default ({type_err})"
                        ),
                        fix=(
                            f"Update schema.default for '{var_name}' in "
                            f"{env_path.name} to match {file_path.name}"
                        ),
                    )
                )

            if var_name not in placeholder_vars:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Variable '{var_name}' has an environment "
                            f"override in {env_path.name}, but schema.default "
                            f"in {file_path.name} is not "
                            f"'{ENV_VAR_PLACEHOLDER}'"
                        ),
                        fix=(
                            f"Set schema.default to '{ENV_VAR_PLACEHOLDER}' "
                            f"for '{var_name}' in {file_path.name}"
                        ),
                    )
                )

        return results


def _extract_supported_languages(ls: dict[str, Any]) -> list[str]:
    """Return deduplicated non-empty supportedLanguageCodes in order."""
    raw = _get_field(ls, *_SUPPORTED_LANG_KEYS)
    if not isinstance(raw, list):
        return []
    codes = [
        item.strip() for item in raw if isinstance(item, str) and item.strip()
    ]
    return list(dict.fromkeys(codes))


def _missing_voice_tts_languages(
    data: dict[str, Any], supported: list[str]
) -> list[str]:
    """Return supported languages lacking a non-empty TTS config entry."""
    audio_cfg = _get_field(data, *_AUDIO_CONFIG_KEYS)
    tts_cfgs = (
        _get_field(audio_cfg, *_SYNTHESIZE_SPEECH_KEYS)
        if isinstance(audio_cfg, dict)
        else None
    )
    valid_keys = (
        {
            k.strip().lower()
            for k, v in tts_cfgs.items()
            if isinstance(k, str) and k.strip() and isinstance(v, dict) and v
        }
        if isinstance(tts_cfgs, dict)
        else set()
    )
    return [lang for lang in supported if lang.lower() not in valid_keys]


@rule("config")
class VoiceLanguageTtsParity(Rule):
    """Validates languageSettings parity and voice TTS coverage."""

    id = "A018"
    name = "voice-language-tts-parity"
    description = (
        "Validates languageSettings default/supported/multilingual parity "
        "and voice synthesizeSpeechConfigs coverage"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.name not in APP_CONFIG_NAMES:
            return []

        data = parse_config_content(file_path, content)
        if data is None:
            return []

        ls = _get_field(data, *_LANGUAGE_SETTINGS_KEYS)
        if not isinstance(ls, dict):
            return []

        rel = rel_to_project(file_path, context)
        raw_default = _get_field(ls, *_DEFAULT_LANG_KEYS)
        default_lang = (
            raw_default.strip()
            if isinstance(raw_default, str) and raw_default.strip()
            else None
        )
        supported = _extract_supported_languages(ls)
        multilingual = _get_field(ls, *_MULTILINGUAL_KEYS)

        results: list[LintResult] = []
        if default_lang and supported and default_lang not in supported:
            results.append(
                self.make_result(
                    file=rel,
                    severity=Severity.ERROR,
                    message=(
                        f"defaultLanguageCode '{default_lang}' is not in "
                        f"supportedLanguageCodes {supported}."
                    ),
                    fix=(
                        f"Add '{default_lang}' to supportedLanguageCodes or "
                        "align defaultLanguageCode."
                    ),
                )
            )

        if len(supported) > 1 and multilingual is False:
            results.append(
                self.make_result(
                    file=rel,
                    severity=Severity.WARNING,
                    message=(
                        f"Multiple supportedLanguageCodes ({supported}) "
                        "declared while enableMultilingualSupport is false."
                    ),
                    fix=(
                        "Set enableMultilingualSupport to true or reduce "
                        "supportedLanguageCodes to a single language."
                    ),
                )
            )

        if supported and is_voice_app(context):
            results.extend(
                self.make_result(
                    file=rel,
                    severity=Severity.WARNING,
                    message=(
                        f"Voice app language '{lang}' is missing "
                        "a valid entry in audioProcessingConfig."
                        "synthesizeSpeechConfigs."
                    ),
                    fix=(
                        f"Add a '{lang}' voice config under "
                        "audioProcessingConfig."
                        "synthesizeSpeechConfigs."
                    ),
                )
                for lang in _missing_voice_tts_languages(data, supported)
            )

        return results
