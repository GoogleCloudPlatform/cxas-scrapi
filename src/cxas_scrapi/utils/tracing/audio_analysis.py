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

"""Registry and scorecard-question loader for `cxas trace audio analyze`.

Built-in audio analyses are stored in `audio_analysis_questions.json` using the
same `qaScorecard` / `qaQuestions` schema as CCAI Insights Scorecard templates
(`questionBody`, `answerInstructions`, `answerChoices`, `abbreviation`), plus a
`fileFilter` field indicating which conversation audio files to pass to Gemini
(e.g. `"agent-turn"` vs `"full-session"`).

Custom questions that aren't defined in code can be supplied via a JSON/YAML
scorecard template file or inline in `trace.yaml` under `gemini.audio_metrics`.
"""

from __future__ import annotations

import enum
import json
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import json5
import yaml

from cxas_scrapi.utils import scorecard_template_manager

DEFAULT_QUESTIONS_PATH = os.path.join(
    os.path.dirname(__file__), "audio_analysis_questions.json"
)
DEFAULT_FILE_FILTER = "agent-turn"


class AnalysisType(str, enum.Enum):
    """Built-in audio analysis identifiers (compatible with Python 3.10+)."""

    AGENT_VOICE_CONSISTENCY = "agent_voice_consistency"
    NO_LONG_PAUSES = "no_long_pauses"
    AGENT_HAVING_TROUBLE = "agent_having_trouble"
    AGENT_LOOPING = "agent_looping"
    AGENT_CUTOFF = "agent_cutoff"

    def __str__(self) -> str:
        return self.value


_BUILTIN_ANALYSIS_TYPES: dict[str, AnalysisType] = {
    t.value: t for t in AnalysisType
}


class AudioAnalysis(ABC):
    """Base class for audio analyses."""

    @property
    @abstractmethod
    def name(self) -> AnalysisType | str:
        """Stable identifier for the analysis."""

    @property
    @abstractmethod
    def prompt(self) -> str:
        """The Gemini prompt to run on the filtered audio files."""

    @abstractmethod
    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        """Picks the subset of conversation audio files this analysis needs."""

    @classmethod
    def from_question(cls, question: dict[str, Any]) -> ScorecardAudioAnalysis:
        """Constructs a `ScorecardAudioAnalysis` from a scorecard question dict."""
        return ScorecardAudioAnalysis(question)


def build_prompt_from_question(question: dict[str, Any]) -> str:
    """Builds a PASS/FAIL Gemini evaluation prompt from a scorecard question dict."""
    explicit_prompt = question.get("prompt")
    if isinstance(explicit_prompt, str) and explicit_prompt.strip():
        return explicit_prompt

    question_body = str(
        question.get("questionBody") or question.get("body") or ""
    ).strip()
    instructions = str(
        question.get("answerInstructions") or question.get("instructions") or ""
    ).strip()
    raw_choices = question.get("answerChoices") or question.get("choices") or []

    sections: list[str] = []
    if instructions:
        sections.append(f"# Setup\n{instructions}")
    if question_body:
        task_line = (
            question_body
            if question_body.startswith("-")
            else f"- {question_body}"
        )
        sections.append(f"# Task\n{task_line}")

    output_lines: list[str] = []
    for choice in raw_choices:
        if not isinstance(choice, dict):
            continue
        key = str(
            choice.get("key")
            or choice.get("strValue")
            or choice.get("value")
            or ""
        ).strip()
        body = str(
            choice.get("body")
            or choice.get("description")
            or choice.get("strValue")
            or ""
        ).strip()
        if not key:
            continue
        if body and body.upper() != key.upper():
            output_lines.append(
                f"- Report {key} if {body} and provide a justification."
            )
        else:
            output_lines.append(f"- Report {key} and provide a justification.")

    joined_upper = "\n".join(output_lines).upper()
    if not output_lines or (
        "PASS" not in joined_upper or "FAIL" not in joined_upper
    ):
        output_lines.extend(
            [
                "- Report PASS if the criteria is met and provide a justification.",
                "- Report FAIL if the criteria is not met and provide a justification.",
            ]
        )

    sections.append("# Output\n" + "\n".join(output_lines))
    return "\n" + "\n\n".join(sections) + "\n"


def _extract_question_name(question: dict[str, Any]) -> str:
    raw = (
        question.get("abbreviation")
        or question.get("id")
        or question.get("name")
        or ""
    )
    name = str(raw).strip()
    if "/" in name and not question.get("abbreviation"):
        name = name.rsplit("/", maxsplit=1)[-1]
    if not name:
        raise ValueError(
            "Audio analysis question must define an 'abbreviation', 'id', or 'name'."
        )
    return name


def _extract_file_filter(question: dict[str, Any]) -> str | list[str]:
    data_opts = question.get("qaQuestionDataOptions")
    nested_filter = (
        data_opts.get("fileFilter") if isinstance(data_opts, dict) else None
    )
    raw_filter = (
        question.get("fileFilter")
        or question.get("file_filter")
        or nested_filter
        or DEFAULT_FILE_FILTER
    )
    if isinstance(raw_filter, list):
        return [str(item) for item in raw_filter if str(item)]
    return str(raw_filter)


class ScorecardAudioAnalysis(AudioAnalysis):
    """Audio analysis backed by a scorecard `QaQuestion` dictionary."""

    def __init__(
        self,
        question: dict[str, Any],
        fallback_filter: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        raw_name = _extract_question_name(question)
        self._name: AnalysisType | str = _BUILTIN_ANALYSIS_TYPES.get(
            raw_name, raw_name
        )
        self._question: dict[str, Any] = dict(question)
        self._file_filter: str | list[str] = _extract_file_filter(question)
        self._fallback_filter = fallback_filter
        self._prompt: str = build_prompt_from_question(question)

    @property
    def name(self) -> AnalysisType | str:
        return self._name

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def question(self) -> dict[str, Any]:
        return dict(self._question)

    @property
    def file_filter(self) -> str | list[str]:
        return self._file_filter

    def to_question_dict(self) -> dict[str, Any]:
        return dict(self._question)

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        if self._fallback_filter is not None and not (
            "fileFilter" in self._question or "file_filter" in self._question
        ):
            return self._fallback_filter(files_in_conversation)

        if isinstance(self._file_filter, list):
            return [
                f
                for f in files_in_conversation
                if any(pat in f for pat in self._file_filter)
            ]
        if self._file_filter in ("*", "all"):
            return [
                f for f in files_in_conversation if f.lower().endswith(".wav")
            ]
        return [f for f in files_in_conversation if self._file_filter in f]


def _load_raw_questions_from_file(template_path: str) -> list[dict[str, Any]]:
    _, ext = os.path.splitext(template_path)
    ext = ext.lower()
    with open(template_path) as f:
        if ext in (".yaml", ".yml"):
            data = yaml.safe_load(f)
        elif ext == ".json5":
            data = json5.load(f)
        elif ext == ".json":
            data = json.load(f)
        else:
            supported = (".json", ".json5", ".yaml", ".yml")
            raise ValueError(
                f"Unsupported template format: {ext}. Supported: {supported}"
            )

    if isinstance(data, list):
        questions = list(data)
        questions.sort(
            key=lambda x: x.get("order", 0) if isinstance(x, dict) else 0
        )
        return questions

    if isinstance(data, dict):
        if (
            scorecard_template_manager.QUESTIONS_KEY in data
            or scorecard_template_manager.SCORECARD_KEY in data
        ):
            _, questions = scorecard_template_manager.load_scorecard_template(
                template_path
            )
            return questions
        if "questions" in data and isinstance(data["questions"], list):
            questions = list(data["questions"])
            questions.sort(
                key=lambda x: x.get("order", 0) if isinstance(x, dict) else 0
            )
            return questions

    raise ValueError(
        f"Invalid audio analysis questions file at {template_path}: "
        "expected a scorecard template with 'qaQuestions' or a list of question dicts."
    )


def load_audio_questions(
    template_path: str | None = None,
) -> dict[str, AudioAnalysis]:
    """Loads audio analysis questions from a JSON/JSON5/YAML scorecard file."""
    path = template_path or DEFAULT_QUESTIONS_PATH
    questions = _load_raw_questions_from_file(path)
    registry: dict[str, AudioAnalysis] = {}
    for q in questions:
        analysis = ScorecardAudioAnalysis.from_question(q)
        registry[str(analysis.name)] = analysis
    return registry


ANALYSIS_REGISTRY: dict[str, AudioAnalysis] = load_audio_questions()
