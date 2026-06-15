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
    CallbackRunResult,
    GoldenRunResult,
    SimulationRunResult,
    ToolRunResult,
)
from cxas_scrapi.reporting.result_stats import (
    EvaluationStats,
    get_evaluation_result_stats,
)


def test_empty_stats():
    df = get_evaluation_result_stats(
        golden_results=[], sim_results=[], tool_results=[], callback_results=[]
    )
    stats = EvaluationStats(df)
    assert stats.passed_sum == 0
    assert stats.total_sum == 0
    assert stats.overall_pct == 0.0
    assert stats.golden.total == 0
    assert stats.sim.total == 0
    assert stats.tool.total == 0
    assert stats.callback.total == 0


def test_aggregate_stats():
    golden = [
        GoldenRunResult(
            raw={
                "name": "g1",
                "passed": True,
                "duration_s": 1.5,
                "modality": "text",
            }
        ),
        GoldenRunResult(
            raw={
                "name": "g2",
                "passed": False,
                "duration_s": 2.0,
                "modality": "text",
            }
        ),
    ]
    sim = [
        SimulationRunResult(
            raw={
                "name": "s1",
                "passed": True,
                "duration_s": 3.0,
                "modality": "text",
            }
        ),
    ]
    tool = [
        ToolRunResult(
            raw={"name": "t1", "passed": False, "errors": "Some error"}
        ),
    ]
    callback = [
        CallbackRunResult(
            raw={"name": "c1", "passed": True, "agent": "WelcomeAgent"}
        ),
    ]

    df = get_evaluation_result_stats(
        golden_results=golden,
        sim_results=sim,
        tool_results=tool,
        callback_results=callback,
    )
    stats = EvaluationStats(df, golden, sim, tool, callback)

    assert stats.passed_sum == 3  # g1, s1, c1
    assert stats.total_sum == 5
    assert stats.overall_pct == 60.0

    assert stats.golden.passed == 1
    assert stats.golden.total == 2
    assert stats.golden.pct == 50.0
    assert stats.golden.value_class == "fail"

    assert stats.sim.passed == 1
    assert stats.sim.total == 1
    assert stats.sim.pct == 100.0
    assert stats.sim.value_class == "pass"


def test_failure_patterns_classification():
    golden = [
        # Fail due to Low similarity
        GoldenRunResult(
            raw={
                "name": "g_fail_text",
                "passed": False,
                "turns": [
                    {
                        "comparisons": [
                            {
                                "outcome": "FAIL",
                                "type": "text",
                                "expected": "hello",
                                "actual": "hi",
                            }
                        ]
                    }
                ],
            }
        ),
        # Fail due to expectation not met
        GoldenRunResult(
            raw={
                "name": "g_fail_exp",
                "passed": False,
                "expectations": [
                    {"status": "Not Met", "expectation": "Must say greeting"}
                ],
            }
        ),
    ]

    sim = [
        # Fail due to uncompleted goal
        SimulationRunResult(
            raw={
                "name": "s_fail_goal",
                "passed": False,
                "step_details": [{"status": "Failed", "goal": "Book a table"}],
            }
        )
    ]

    tool = [
        # Fail with default operator error
        ToolRunResult(
            raw={
                "name": "t_fail_default",
                "passed": False,
                "errors": "operator='Operator.CONTAINS', expected='PASSED'",
            }
        ),
        # Fail with empty errors (fallback case)
        ToolRunResult(
            raw={
                "name": "t_fail_empty_errors",
                "passed": False,
                "errors": "",
            }
        ),
    ]

    callback = [
        # Fail with custom error
        CallbackRunResult(
            raw={
                "name": "c_fail_err",
                "passed": False,
                "error": "Connection timed out",
            }
        ),
        # Fail with empty error (fallback case)
        CallbackRunResult(
            raw={
                "name": "c_fail_empty_err",
                "passed": False,
                "error": "",
            }
        ),
    ]

    df = get_evaluation_result_stats(
        golden_results=golden,
        sim_results=sim,
        tool_results=tool,
        callback_results=callback,
    )
    stats = EvaluationStats(df, golden, sim, tool, callback)
    failures = stats.failure_groups

    assert "Semantic similarity too low" in failures
    assert ("golden", "g_fail_text") in failures["Semantic similarity too low"]

    assert "Expectation not met: Must say greeting" in failures
    assert ("golden", "g_fail_exp") in failures[
        "Expectation not met: Must say greeting"
    ]

    assert "Goal not completed: Book a table" in failures
    assert ("sim", "s_fail_goal") in failures[
        "Goal not completed: Book a table"
    ]

    assert (
        "Default expectation: $.result contains PASSED (needs customization)"
        in failures
    )
    assert ("tool", "t_fail_default") in failures[
        "Default expectation: $.result contains PASSED (needs customization)"
    ]

    assert "Unknown tool failure" in failures
    assert ("tool", "t_fail_empty_errors") in failures["Unknown tool failure"]

    assert "Callback: Connection timed out" in failures
    assert ("callback", "c_fail_err") in failures[
        "Callback: Connection timed out"
    ]

    assert "Callback: Unknown error" in failures
    assert ("callback", "c_fail_empty_err") in failures[
        "Callback: Unknown error"
    ]


def test_missing_fields_stats():
    golden = [GoldenRunResult(raw={"name": "g1"})]
    sim = [SimulationRunResult(raw={"name": "s1"})]
    tool = [ToolRunResult(raw={"name": "t1"})]
    callback = [CallbackRunResult(raw={"name": "c1"})]

    df = get_evaluation_result_stats(
        golden_results=golden,
        sim_results=sim,
        tool_results=tool,
        callback_results=callback,
    )
    stats = EvaluationStats(df, golden, sim, tool, callback)

    assert stats.passed_sum == 0
    assert stats.total_sum == 4
    assert stats.overall_pct == 0.0

    assert stats.golden.total == 1
    assert stats.golden.passed == 0
    assert stats.golden.pct == 0.0
    assert stats.golden.modality == "text"
    assert stats.golden.duration_s == 0.0

    assert stats.sim.total == 1
    assert stats.sim.passed == 0
    assert stats.sim.pct == 0.0
    assert stats.sim.modality == "text"
    assert stats.sim.duration_s == 0.0

    assert stats.tool.total == 1
    assert stats.tool.passed == 0
    assert stats.tool.pct == 0.0
    assert stats.tool.modality == "tool"

    assert stats.callback.total == 1
    assert stats.callback.passed == 0
    assert stats.callback.pct == 0.0
    assert stats.callback.modality == "callback"
