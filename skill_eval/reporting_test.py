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

"""Tests for reporting utilities."""

import json

from absl.testing import absltest

from skill_eval import benchmark, reporting, scorer


class ReportingTest(absltest.TestCase):
    def test_generate_json_report_handles_enum(self) -> None:
        res = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# My Scenario",
            head_name="Antigravity",
            turns=1,
            total_time_sec=10.5,
            messages=[{"role": "user", "content": "hi"}],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.RUNNING,
        )
        json_str = reporting.generate_json_report(res)
        data = json.loads(json_str)
        assert data["status"] == "RUNNING"

    def test_generate_index_html_shows_running_status(self) -> None:
        res = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# My Scenario",
            head_name="TestHead",
            turns=0,
            total_time_sec=0.0,
            messages=[],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.RUNNING,
        )
        html = reporting.generate_index_html([res])
        assert "RUNNING" in html
        assert "status-running" in html

    def test_generate_index_html_sanitizes_scenario_name_in_links(self) -> None:
        res = benchmark.ConversationResult(
            scenario_name="p/t/m.c.s.yaml",
            scenario_text="# My Scenario",
            head_name="TestHead",
            turns=1,
            total_time_sec=1.0,
            messages=[],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.FINISHED,
            success=True,
        )
        html = reporting.generate_index_html([res])
        expected_link = "detail_p_t_m_c_s_yaml_TestHead.html"
        assert expected_link in html
        assert 'href="detail_p/t/m.c.s.yaml_TestHead.html"' not in html

    def test_generate_detail_html_renders_rubric_and_thoughts(self) -> None:
        rubric_res = scorer.ScoreResult(
            scores=[scorer.RubricCriterion("C1", 2, "Reason 1")],
            total_score=2,
            max_score=2,
            summary="Overall summary",
        )
        res = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# Summary\nGoal context\n## Steps\n1. hi",
            head_name="TestHead",
            turns=1,
            total_time_sec=5.0,
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "agent", "content": "hello"},
            ],
            turn_metrics=[
                benchmark.TurnMetrics(
                    latency_sec=5.0,
                    simulator_latency_sec=0.0,
                    tool_calls=1,
                    tool_interactions=[
                        benchmark.ToolInteraction(
                            name="my_tool",
                            args={"foo": "bar"},
                            output="ok",
                            thought="I am thinking",
                        )
                    ],
                )
            ],
            status=benchmark.ExecutionStatus.FINISHED,
            success=True,
            rubric_results=rubric_res,
        )
        html = reporting.generate_detail_html(res)

        # Check table format
        assert "Criterion" in html
        assert "Reasoning" in html
        assert "Overall summary" in html
        assert "Reason 1" in html

        # Check scenario intro
        assert "scenario-intro" in html
        assert "Goal context" in html

        # Check thoughts
        assert "I am thinking" in html
        assert "Thought:" in html

    def test_generate_detail_html_shows_incomplete_turns_on_failure(
        self,
    ) -> None:
        res = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# My Scenario",
            head_name="TestHead",
            turns=1,
            total_time_sec=60.0,
            messages=[
                {"role": "user", "content": "What is the capital of France?"}
            ],
            turn_metrics=[],  # Failed before first turn finished
            status=benchmark.ExecutionStatus.FAILED,
            success=False,
            failure_reason="Language server timeout",
        )
        html = reporting.generate_detail_html(res)

        assert "Termination Signal:" in html
        assert "Language server timeout" in html
        assert "What is the capital of France?" in html
        assert "[FAILED]" in html

    def test_generate_detail_html_renders_log_files(self) -> None:
        res = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# My Scenario",
            head_name="TestHead",
            turns=1,
            total_time_sec=1.0,
            messages=[],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.FINISHED,
            success=True,
            log_files=["/absolute/path/to/logs/agent.log"],
        )
        # Case 1: report_dir is None (fallback to absolute file:// URI)
        html_no_dir = reporting.generate_detail_html(res)
        assert 'href="file:///absolute/path/to/logs/agent.log"' in html_no_dir
        assert "/absolute/path/to/logs/agent.log" in html_no_dir

        # Case 2: report_dir is provided (relative path resolution)
        html_with_dir = reporting.generate_detail_html(
            res, report_dir="/absolute/path/to"
        )
        assert 'href="logs/agent.log"' in html_with_dir
        assert "logs/agent.log" in html_with_dir


if __name__ == "__main__":
    absltest.main()
