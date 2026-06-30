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

"""Tests for automated agent benchmarking orchestrator."""

import asyncio
import os
import pathlib
import shutil
import sys
import tempfile
from unittest import mock

from absl import flags
from absl.testing import absltest, flagsaver

from skill_eval import benchmark, run_benchmark, scenario

# Parse flags to avoid UnparsedFlagAccessError when running via pytest
try:
    flags.FLAGS(sys.argv, known_only=True)
except flags.Error:
    pass


class FakeAgentHead(benchmark.BaseAgentHead):
    def __init__(self, name: str):
        self._name = name

    async def send_message(self, message: str) -> str:
        return f"Response to {message}"

    def get_tool_calls_count_last_turn(self) -> int:
        return 0

    def get_tool_interactions_last_turn(
        self,
    ) -> list[benchmark.ToolInteraction]:
        return []

    async def reset(self) -> None:
        pass

    @property
    def name(self) -> str:
        return self._name

    def get_log_files(self) -> list[str]:
        return []

    async def close(self) -> None:
        pass


class BenchmarkRunnerTest(absltest.TestCase):
    def test_init_sets_scenario_fields(self):
        scen = scenario.Scenario(
            name="test", text="dummy", prompt="dummy", rubric=[]
        )
        runner = benchmark.BenchmarkRunner(
            scen=scen,
        )
        self.assertEqual(runner.scenario_name, "test")
        self.assertEqual(runner.scenario_text, "dummy")
        self.assertEqual(runner.scenario, scen)


class BenchmarkOrchestratorTest(absltest.TestCase):
    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        super().tearDown()

    def test_orchestrator_updates_reports_on_progress(self):
        orchestrator = run_benchmark.BenchmarkOrchestrator(
            report_dir=self.test_dir, model_location="global"
        )

        # Mock runner
        mock_runner = mock.MagicMock(spec=benchmark.BenchmarkRunner)
        mock_runner.scenario_name = "test.yaml"
        mock_runner.scenario_text = "# Scenario\nHello"
        mock_runner.run.return_value = benchmark.ConversationResult(
            scenario_name="test.yaml",
            scenario_text="# Scenario\nHello",
            head_name="TestHead",
            turns=1,
            total_time_sec=1.0,
            messages=[],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.FINISHED,
            success=True,
        )

        head = FakeAgentHead("TestHead")
        asyncio.run(orchestrator.run_head(head, mock_runner))

        # Verify artifacts
        self.assertTrue(
            os.path.exists(os.path.join(self.test_dir, "index.html"))
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self.test_dir, "test_yaml_TestHead.json")
            )
        )

    @mock.patch.dict(os.environ, {"BUILD_WORKSPACE_DIRECTORY": "/fake/client"})
    def test_get_workspace_root_returns_build_workspace(self):
        # Mock os.path.exists to return True
        with mock.patch("os.path.exists", return_value=True):
            self.assertEqual(
                run_benchmark._get_workspace_root(),
                "/fake/client",
            )

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_get_workspace_root_fallback_to_cwd(self):
        with (
            mock.patch("os.getcwd", return_value="/some/path/my/pkg"),
            mock.patch.object(pathlib.Path, "exists", return_value=False),
        ):
            self.assertEqual(
                run_benchmark._get_workspace_root(),
                "/some/path/my/pkg",
            )

    @mock.patch("os.path.isdir", return_value=True)
    @mock.patch("os.listdir", return_value=["s1.yaml", "s2.yaml"])
    @mock.patch("skill_eval.scenario.Scenario.from_file")
    def test_main_async_fails_on_duplicate_scenario_names(
        self, mock_from_file, _mock_listdir, _mock_isdir
    ):
        scen1 = mock.MagicMock()
        scen1.name = "DuplicateName"
        scen2 = mock.MagicMock()
        scen2.name = "DuplicateName"
        mock_from_file.side_effect = [scen1, scen2]

        temp_dir = self.create_tempdir().full_path
        with flagsaver.flagsaver(scenario_path="fake_dir", output_dir=temp_dir):
            with self.assertRaises(SystemExit):
                asyncio.run(run_benchmark.main_async())


if __name__ == "__main__":
    absltest.main()
