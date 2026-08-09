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

"""App and agent config lint rules (A001-A007).

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


@rule("config")
class LanguageVoiceCoverage(Rule):
    id = "A007"
    name = "config-language-voice-coverage"
    description = (
        "On a composite model app, every configured language needs a "
        "synthesizeSpeechConfigs entry carrying the same delivery keys "
        "as the default language"
    )
    default_severity = Severity.WARNING

    # Keys on SynthesizeSpeechConfig that steer delivery. A locale that
    # omits one the default locale sets will not sound like the rest of
    # the app.
    DELIVERY_KEYS = ("voice", "instruction", "speakingRate", "model")

    # synthesizeSpeechConfigs is only consulted on the composite model,
    # which cascades into a separate text-to-speech step. Matched as a
    # substring so a later composite version stays covered.
    COMPOSITE_MARKER = "composite"

    @classmethod
    def _serving_key(cls, code: str, by_lower: dict[str, str]) -> str:
        """Return the map key CES would use for `code`, or "" if none does.

        Lookup is case-insensitive and falls back to the root language, so
        an 'es' entry serves 'es-US' and an 'EN-US' entry serves 'en-us'.
        """
        lowered = code.lower()
        if lowered in by_lower:
            return by_lower[lowered]
        return by_lower.get(lowered.split("-")[0], "")

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

        model = (data.get("modelSettings") or {}).get("model") or ""
        if self.COMPOSITE_MARKER not in str(model).lower():
            # A native audio model speaks directly and never reaches the
            # synthesis step, so a coverage gap costs it nothing. An unset
            # model inherits from the parent and cannot be confirmed
            # composite, so stay silent rather than guess.
            return []

        audio = data.get("audioProcessingConfig") or {}
        configs = audio.get("synthesizeSpeechConfigs") or {}
        if not isinstance(configs, dict) or not configs:
            # Text-only agents carry no voice config at all, so there is
            # nothing to keep in sync.
            return []

        settings = data.get("languageSettings") or {}
        default_code = settings.get("defaultLanguageCode") or ""
        supported = settings.get("supportedLanguageCodes") or []
        if not isinstance(supported, list):
            supported = []

        expected: list[str] = []
        for code in [default_code, *supported]:
            if isinstance(code, str) and code and code not in expected:
                expected.append(code)

        # CES resolves a locale case-insensitively and falls back to the
        # root language, so coverage is not a plain key lookup.
        by_lower = {
            key.lower(): key for key in configs if isinstance(key, str)
        }
        served = {code: self._serving_key(code, by_lower) for code in expected}

        # The entry every other one is compared against: the first
        # configured language that actually resolves, so the fix text never
        # points at a locale that is itself uncovered.
        reference = next(
            (
                served[code]
                for code in expected
                if served[code] and isinstance(configs[served[code]], dict)
            ),
            "",
        )
        like = f"'{reference}'" if reference else "the other locales"

        results = []

        for code in expected:
            if served[code]:
                continue
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        f"Language '{code}' is declared in languageSettings "
                        "but no synthesizeSpeechConfigs entry serves it, so "
                        "nothing in this app sets its voice or delivery"
                    ),
                    fix=(
                        "synthesizeSpeechConfigs is keyed by locale and "
                        "app.json is the source of truth for a pushed app. "
                        f"Add an entry for '{code}' carrying the same keys "
                        f'as {like}: {{"voice": ..., "instruction": ...}}'
                    ),
                )
            )

        if not reference:
            return results

        reference_keys = {
            key for key in self.DELIVERY_KEYS if key in configs[reference]
        }
        seen = {reference}
        for code in expected:
            key = served[code]
            if not key or key in seen or not isinstance(configs[key], dict):
                continue
            seen.add(key)
            missing = sorted(reference_keys - set(configs[key]))
            if missing:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"synthesizeSpeechConfigs['{key}'] is missing "
                            f"{', '.join(missing)}, which "
                            f"'{reference}' sets. That language will be "
                            "delivered differently from the rest of the app"
                        ),
                        fix=(
                            f"Copy {', '.join(missing)} from "
                            f"'{reference}' into '{key}'. A style prompt "
                            "stays in one language across locales, but the "
                            "accent line inside it has to name the locale's "
                            "own accent"
                        ),
                    )
                )

        return results
