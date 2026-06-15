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

from cxas_scrapi.reporting.result_extractors import (
    ExpectationDetail,
    SimStepDetail,
    TraceEntry,
    GoldenRunResult,
    SimulationRunResult,
    ToolRunResult,
    CallbackRunResult,
)


def test_expectation_detail():
    raw = {
        "expectation": "Say hello",
        "status": "Met",
        "justification": "Agent welcomed user",
    }
    detail = ExpectationDetail(raw)
    assert detail.expectation == "Say hello"
    assert detail.status == "Met"
    assert detail.is_met is True
    assert detail.justification == "Agent welcomed user"

    # Fallbacks
    empty = ExpectationDetail({})
    assert empty.expectation == "?"
    assert empty.status == "?"
    assert empty.is_met is False
    assert empty.justification == ""


def test_sim_step_detail():
    raw = {
        "goal": "Book flight",
        "success_criteria": "Booking confirmed",
        "status": "Success",
        "justification": "Confirmed with ID 123",
    }
    detail = SimStepDetail(raw)
    assert detail.goal == "Book flight"
    assert detail.success_criteria == "Booking confirmed"
    assert detail.status == "Success"
    assert detail.justification == "Confirmed with ID 123"

    empty = SimStepDetail({})
    assert empty.goal == "?"
    assert empty.success_criteria == "?"
    assert empty.status == "?"
    assert empty.justification == ""


def test_trace_entry():
    raw = ("user", "hello", "passed")
    entry = TraceEntry(raw)
    assert entry.kind == "user"
    assert entry.text == "hello"
    assert entry.result == "passed"

    raw_short = ("agent", "hi")
    entry_short = TraceEntry(raw_short)
    assert entry_short.kind == "agent"
    assert entry_short.text == "hi"
    assert entry_short.result == ""


def test_golden_run_result():
    raw = {
        "name": "g1",
        "passed": True,
        "status": "PASS",
        "duration_s": 1.5,
        "modality": "voice",
        "expectation_details": [{"expectation": "e1"}],
        "expectations": [{"expectation": "e2"}],
        "turns": [{"turn": 1}],
    }
    res = GoldenRunResult(raw)
    assert res.name == "g1"
    assert res.passed is True
    assert res.status == "PASS"
    assert res.duration_s == 1.5
    assert res.modality == "voice"
    assert len(res.expectation_details) == 1
    assert res.expectation_details[0].expectation == "e1"
    assert len(res.expectations) == 1
    assert res.expectations[0].expectation == "e2"
    assert res.turns == [{"turn": 1}]

    # Fallbacks
    empty = GoldenRunResult({})
    assert empty.name == "?"
    assert empty.passed is False
    assert empty.status == "FAIL"
    assert empty.duration_s == 0.0
    assert empty.modality == "text"
    assert empty.expectation_details == []
    assert empty.expectations == []
    assert empty.turns == []


def test_simulation_run_result():
    raw = {
        "name": "s1",
        "passed": True,
        "duration_s": 10.0,
        "sim_wall_clock_s": 12.0,
        "modality": "audio",
        "run": 2,
        "session_id": "sess1",
        "goals": 3,
        "expectations": 4,
        "turns": 5,
        "session_parameters": {"p1": "v1"},
        "step_details": [{"goal": "g1"}],
        "expectation_details": [{"expectation": "e1"}],
        "_processed_trace": [("user", "u1")],
        "error": "some error",
    }
    res = SimulationRunResult(raw)
    assert res.name == "s1"
    assert res.passed is True
    assert res.duration_s == 10.0
    assert res.sim_wall_clock_s == 12.0
    assert res.modality == "audio"
    assert res.run_number == 2
    assert res.session_id == "sess1"
    assert res.goals == 3
    assert res.expectations == 4
    assert res.turns == 5
    assert res.session_parameters == {"p1": "v1"}
    assert len(res.step_details) == 1
    assert res.step_details[0].goal == "g1"
    assert len(res.expectation_details) == 1
    assert res.expectation_details[0].expectation == "e1"
    assert len(res.processed_trace) == 1
    assert res.processed_trace[0].kind == "user"
    assert res.error == "some error"

    # Fallbacks
    empty = SimulationRunResult({})
    assert empty.name == "?"
    assert empty.passed is False
    assert empty.duration_s == 0.0
    assert empty.sim_wall_clock_s == 0.0
    assert empty.modality == "text"
    assert empty.run_number == 1
    assert empty.session_id == ""
    assert empty.goals == 0
    assert empty.expectations == 0
    assert empty.turns == 0
    assert empty.session_parameters == {}
    assert empty.step_details == []
    assert empty.expectation_details == []
    assert empty.processed_trace == []
    assert empty.error == ""


def test_tool_run_result():
    raw = {
        "name": "t1",
        "passed": True,
        "status": "PASSED",
        "tool": "my_tool",
        "latency_ms": 150.0,
        "errors": "none",
    }
    res = ToolRunResult(raw)
    assert res.name == "t1"
    assert res.passed is True
    assert res.status == "PASSED"
    assert res.tool == "my_tool"
    assert res.latency_ms == 150.0
    assert res.errors == "none"

    empty = ToolRunResult({})
    assert empty.name == "?"
    assert empty.passed is False
    assert empty.status == "?"
    assert empty.tool == "?"
    assert empty.latency_ms == 0.0
    assert empty.errors == ""


def test_callback_run_result():
    raw = {
        "name": "c1",
        "passed": False,
        "status": "FAILED",
        "agent": "agent1",
        "callback_type": "type1",
        "error": "timeout",
    }
    res = CallbackRunResult(raw)
    assert res.name == "c1"
    assert res.passed is False
    assert res.status == "FAILED"
    assert res.agent == "agent1"
    assert res.callback_type == "type1"
    assert res.error == "timeout"

    empty = CallbackRunResult({})
    assert empty.name == "?"
    assert empty.passed is False
    assert empty.status == "?"
    assert empty.agent == "?"
    assert empty.callback_type == "?"
    assert empty.error == ""
