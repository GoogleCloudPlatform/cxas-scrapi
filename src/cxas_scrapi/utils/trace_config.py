"""Pydantic-validated config for the `cxas trace` command.

Resolution order: explicit `--config FILE` -> `./.cxas/trace.yaml` ->
`~/.cxas/trace.yaml` -> built-in defaults. Every section has defaults so the
file is optional.
"""

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

import logging
import os
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PROJECT_CONFIG_PATH = "./.cxas/trace.yaml"
USER_CONFIG_PATH = os.path.expanduser("~/.cxas/trace.yaml")


class AudioConfig(BaseModel):
    bucket_override: str | None = None
    uri_pattern: str = "{bucket}/{conversation_id}.wav"
    download_dir: str = "./.cxas/audio"
    mime_type: str = "audio/wav"
    # If True (and the platform-managed bucket layout is used), search the
    # configured bucket for objects whose path ends with
    # `/{conversation_id}/{search_filename}` and use the first match.
    search_bucket: bool = True
    search_filename: str = "full-session.wav"


class CloudLoggingConfig(BaseModel):
    default_level: str = "WARNING"
    time_padding_seconds: int = 30
    filter_template: str = (
        'severity >= "{level}"\n'
        'AND timestamp >= "{start_time}" AND timestamp <= "{end_time}"\n'
        'AND (jsonPayload.conversation_id="{conversation_id}"\n'
        '     OR labels.conversation_id="{conversation_id}")\n'
    )


class GeminiMetric(BaseModel):
    prompt: str


class GeminiConfig(BaseModel):
    model: str = "gemini-2.5-flash"
    audio_metrics: dict[str, GeminiMetric] = Field(
        default_factory=lambda: {
            "audio_cutoff": GeminiMetric(
                prompt=(
                    "Listen to this audio. Identify any places where the "
                    "agent's voice was cut off mid-sentence. For each, "
                    "report the approximate timestamp, the truncated phrase, "
                    "and a severity (low/medium/high)."
                )
            ),
            "voice_drift": GeminiMetric(
                prompt=(
                    "Identify unnatural changes in voice tone, pitch, or "
                    "speed across the call. Report approximate timestamps "
                    "and a brief description of each drift."
                )
            ),
            "humanness": GeminiMetric(
                prompt=(
                    "Rate how human-like the agent sounds on a 1-5 scale. "
                    "Flag specific moments of robotic delivery."
                )
            ),
            "background_noise": GeminiMetric(
                prompt=(
                    "Identify background noise, echoes, or audio-quality "
                    "issues. Report approximate timestamps and the type of "
                    "issue."
                )
            ),
        }
    )
    triage_metrics: dict[str, GeminiMetric] = Field(
        default_factory=lambda: {
            "hallucination": GeminiMetric(
                prompt=(
                    "Identify turns where the agent made factually incorrect "
                    "claims. Return the turn index, the claim, and why it "
                    "is incorrect."
                )
            ),
            "off_topic": GeminiMetric(
                prompt=(
                    "Flag turns where the agent's response was unrelated to "
                    "the user's most recent question. Return the turn index "
                    "and a brief reason."
                )
            ),
            "failed_understanding": GeminiMetric(
                prompt=(
                    "Flag turns where the agent appears to have "
                    "misunderstood the user. Return the turn index and a "
                    "brief description of the misunderstanding."
                )
            ),
        }
    )


class UIConfig(BaseModel):
    ces_console_base: str = "https://ces.cloud.google.com"
    ccai_insights_base: str = "https://ccai.cloud.google.com/insights"


class BugReportConfig(BaseModel):
    bucket: str | None = None
    path_template: str = (
        "{model_version}/{date}/{user}/{severity}/{conversation_id}/"
    )
    include: list[str] = Field(
        default_factory=lambda: [
            "transcript",
            "logs",
            "audio",
            "gemini_analysis",
            "environment",
        ]
    )


class TraceConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    cloud_logging: CloudLoggingConfig = Field(
        default_factory=CloudLoggingConfig
    )
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    bug_report: BugReportConfig = Field(default_factory=BugReportConfig)

    @classmethod
    def load(cls, explicit_path: str | None = None) -> "TraceConfig":
        """Loads config from an explicit path or the standard search paths."""
        path = cls._pick_path(explicit_path)
        if path is None:
            return cls()
        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        try:
            return cls.model_validate(raw)
        except Exception as e:
            raise ValueError(f"Invalid trace config at {path}: {e}") from e

    @staticmethod
    def _pick_path(explicit_path: str | None) -> str | None:
        if explicit_path:
            if not os.path.isfile(explicit_path):
                raise FileNotFoundError(
                    f"Trace config not found: {explicit_path}"
                )
            return explicit_path
        if os.path.isfile(PROJECT_CONFIG_PATH):
            return PROJECT_CONFIG_PATH
        if os.path.isfile(USER_CONFIG_PATH):
            return USER_CONFIG_PATH
        return None
