"""Manages loading and saving of Scorecard templates from/to JSON/YAML files."""

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

import json
import os
from collections.abc import Collection, Iterable
from typing import Any, TypeAlias

import json5
import yaml

# Type aliases for scorecard and question resources.
QaScorecard: TypeAlias = dict[str, Any]
QaQuestion: TypeAlias = dict[str, Any]

# Keys used in the scorecard template JSON/YAML.
SCORECARD_KEY = "qaScorecard"
QUESTIONS_KEY = "qaQuestions"


def load_scorecard_template(
    template_path: str,
) -> tuple[QaScorecard, list[QaQuestion]]:
    """Loads a scorecard and its questions from a template file.

    Supports JSON, JSON5, and YAML formats.

    Args:
      template_path: The path to the template file.

    Returns:
      A tuple containing the scorecard and a list of questions.

    Raises:
      ValueError: If the template format is not supported.
    """
    _, ext = os.path.splitext(template_path)
    ext = ext.lower()

    with open(template_path, "r") as f:
        if ext in (".yaml", ".yml"):
            template_data = yaml.safe_load(f)
        elif ext == ".json5":
            template_data = json5.load(f)
        elif ext == ".json":
            template_data = json.load(f)
        else:
            supported = (".json", ".json5", ".yaml", ".yml")
            raise ValueError(
                f"Unsupported template format: {ext}. Supported: {supported}"
            )

    scorecard = template_data.get(SCORECARD_KEY, {})

    questions = template_data.get(QUESTIONS_KEY, [])
    # Sort by order if available
    questions.sort(key=lambda x: x.get("order", 0))

    return scorecard, questions


def _get_nested_field(data: dict[str, Any], field_path: str) -> Any:
    """Gets a nested field from a dictionary using dot notation."""
    keys = field_path.split(".")
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _set_nested_field(
    data: dict[str, Any], field_path: str, value: Any
) -> None:
    """Sets a nested field in a dictionary using dot notation."""
    keys = field_path.split(".")
    for key in keys[:-1]:
        data = data.setdefault(key, {})
    data[keys[-1]] = value


def save_scorecard_template(
    scorecard: QaScorecard,
    questions: Iterable[QaQuestion],
    template_path: str,
    fields_to_export: Collection[str] | None = None,
) -> None:
    """Saves a scorecard and its questions to a file (JSON, JSON5, or YAML)."""
    template_dir = os.path.dirname(template_path)
    if template_dir:
        os.makedirs(template_dir, exist_ok=True)

    # Clear output-only fields and resource names before saving to template
    scorecard_dict = dict(scorecard)
    for field in ["name", "createTime", "updateTime", "latestRevision"]:
        scorecard_dict.pop(field, None)

    questions_list = []
    for question in sorted(questions, key=lambda q: q.get("order", 0)):
        q_dict_full = dict(question)

        if fields_to_export:
            # Use allowlist filtering
            q_dict = {}
            for field_path in fields_to_export:
                val = _get_nested_field(q_dict_full, field_path)
                if val is not None:
                    _set_nested_field(q_dict, field_path, val)
        else:
            # Default behavior: pop sensitive fields
            q_dict = q_dict_full
            for field in ["name", "createTime", "updateTime"]:
                q_dict.pop(field, None)

        questions_list.append(q_dict)

    template_data = {
        SCORECARD_KEY: scorecard_dict,
        QUESTIONS_KEY: questions_list,
    }

    _, ext = os.path.splitext(template_path)
    ext = ext.lower()

    with open(template_path, "w") as f:
        if ext in (".yaml", ".yml"):
            yaml.dump(
                template_data,
                f,
                sort_keys=False,
                indent=2,
                allow_unicode=True,
                width=80,
                default_flow_style=False,
            )
        elif ext == ".json5":
            json5.dump(template_data, f, indent=2)
        elif ext == ".json":
            json.dump(template_data, f, indent=2)
        else:
            supported = (".json", ".json5", ".yaml", ".yml")
            raise ValueError(
                f"Unsupported template format: {ext}. Supported: {supported}"
            )
