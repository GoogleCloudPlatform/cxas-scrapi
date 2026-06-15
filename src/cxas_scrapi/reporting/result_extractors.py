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

"""Strongly-typed lossless extractors wrapping raw execution outcome schemas cleanly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExpectationDetail:
    """Lossless wrapper around Dialogflow expectation criteria evaluation outcome."""

    raw: dict[str, Any]

    @property
    def expectation(self) -> str:
        return str(self.raw.get("expectation", "?"))

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "?"))

    @property
    def is_met(self) -> bool:
        return self.status == "Met"

    @property
    def justification(self) -> str:
        return str(self.raw.get("justification", ""))


@dataclass
class SimStepDetail:
    """Lossless wrapper around simulation execution trace step details."""

    raw: dict[str, Any]

    @property
    def goal(self) -> str:
        return str(self.raw.get("goal", "?"))

    @property
    def success_criteria(self) -> str:
        return str(self.raw.get("success_criteria", "?"))

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "?"))

    @property
    def justification(self) -> str:
        return str(self.raw.get("justification", ""))


@dataclass
class TraceEntry:
    """Lossless wrapper around a processed conversational interaction trace turn."""

    raw: tuple[str, ...]

    @property
    def kind(self) -> str:
        return self.raw[0]

    @property
    def text(self) -> str:
        return self.raw[1]

    @property
    def result(self) -> str:
        return self.raw[2] if len(self.raw) > 2 else ""


@dataclass
class GoldenRunResult:
    """Lossless strongly-typed model wrapping a raw golden result."""

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name", "?"))

    @property
    def passed(self) -> bool:
        return bool(self.raw.get("passed", False))

    @property
    def status(self) -> str:
        val = self.raw.get("status")
        if not val or val == "?":
            return "PASS" if self.passed else "FAIL"
        return str(val)

    @property
    def duration_s(self) -> float:
        return float(self.raw.get("duration_s", 0.0))

    @property
    def modality(self) -> str:
        return str(self.raw.get("modality", "text"))

    @property
    def expectation_details(self) -> list[ExpectationDetail]:
        raw_details = self.raw.get("expectation_details", []) or []
        return [ExpectationDetail(raw=x) for x in raw_details]

    @property
    def expectations(self) -> list[ExpectationDetail]:
        raw_details = self.raw.get("expectations", []) or []
        return [ExpectationDetail(raw=x) for x in raw_details]

    @property
    def turns(self) -> list[dict[str, Any]]:
        return self.raw.get("turns", []) or []


@dataclass
class SimulationRunResult:
    """Lossless strongly-typed model wrapping a raw simulation run execution."""

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name", "?"))

    @property
    def passed(self) -> bool:
        return bool(self.raw.get("passed", False))

    @property
    def duration_s(self) -> float:
        return float(self.raw.get("duration_s", 0.0))

    @property
    def sim_wall_clock_s(self) -> float:
        return float(self.raw.get("sim_wall_clock_s", 0.0))

    @property
    def modality(self) -> str:
        return str(self.raw.get("modality", "text"))

    @property
    def run_number(self) -> int:
        return int(self.raw.get("run", 1))

    @property
    def session_id(self) -> str:
        return str(self.raw.get("session_id") or self.raw.get("evaluation", ""))

    @property
    def goals(self) -> int:
        return int(self.raw.get("goals", 0))

    @property
    def expectations(self) -> int:
        return int(self.raw.get("expectations", 0))

    @property
    def turns(self) -> int:
        return int(self.raw.get("turns", 0))

    @property
    def session_parameters(self) -> dict[str, Any]:
        return self.raw.get("session_parameters", {}) or {}

    @property
    def step_details(self) -> list[SimStepDetail]:
        raw_steps = self.raw.get("step_details", []) or []
        return [SimStepDetail(raw=s) for s in raw_steps]

    @property
    def expectation_details(self) -> list[ExpectationDetail]:
        raw_exps = self.raw.get("expectation_details", []) or []
        return [ExpectationDetail(raw=x) for x in raw_exps]

    @property
    def processed_trace(self) -> list[TraceEntry]:
        raw_trace = self.raw.get("_processed_trace", []) or []
        return [TraceEntry(raw=t) for t in raw_trace]

    @property
    def error(self) -> str:
        return str(self.raw.get("error", ""))


@dataclass
class ToolRunResult:
    """Lossless strongly-typed model wrapping a raw tool execution result."""

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name", "?"))

    @property
    def passed(self) -> bool:
        return bool(self.raw.get("passed", False))

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "?"))

    @property
    def tool(self) -> str:
        return str(self.raw.get("tool", "?"))

    @property
    def latency_ms(self) -> float:
        return float(self.raw.get("latency_ms", 0.0))

    @property
    def errors(self) -> str:
        return str(self.raw.get("errors", ""))


@dataclass
class CallbackRunResult:
    """Lossless strongly-typed model wrapping a raw callback execution result."""

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name", "?"))

    @property
    def passed(self) -> bool:
        return bool(self.raw.get("passed", False))

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "?"))

    @property
    def agent(self) -> str:
        return str(self.raw.get("agent", "?"))

    @property
    def callback_type(self) -> str:
        return str(self.raw.get("callback_type", "?"))

    @property
    def error(self) -> str:
        return str(self.raw.get("error", ""))
