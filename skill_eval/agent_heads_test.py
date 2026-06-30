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

import asyncio
import os
import shutil
import unittest
from unittest import mock

from skill_eval import agent_heads


class ScaffoldingTestAgentTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.head = agent_heads.ScaffoldingTestAgent(scenario_name="test_scen")

    def test_send_message_increments_turns(self):
        self.assertEqual(self.head.get_tool_calls_count_last_turn(), 0)
        res1 = asyncio.run(self.head.send_message("hello"))
        self.assertIn("Turn 1", res1)
        self.assertEqual(self.head.get_tool_calls_count_last_turn(), 0)

        res2 = asyncio.run(self.head.send_message("again"))
        self.assertIn("Turn 2", res2)
        self.assertEqual(self.head.get_tool_calls_count_last_turn(), 1)
        self.assertEqual(len(self.head.get_tool_interactions_last_turn()), 1)
        self.assertEqual(
            self.head.get_tool_interactions_last_turn()[0].name,
            "mock_tool_call",
        )


class AntigravityAgentHeadTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.head = agent_heads.AntigravityAgentHead(
            scenario_name="test_session.yaml",
            scenario_path="/tmp/cxas_skill_eval/scenarios/test_session.yaml",
        )

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch.object(
        agent_heads.scenario,
        "get_asset_path",
        return_value="/src/path/asset1.md",
    )
    @mock.patch.object(shutil, "copy")
    @mock.patch.object(os, "makedirs")
    @mock.patch.object(agent_heads.AntigravityAgentHead, "_run_subprocess_cmd")
    @mock.patch.object(agent_heads.glob, "glob")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch.object(os, "chmod")
    @mock.patch.object(os, "symlink")
    @mock.patch("skill_eval.agent_heads.Agent")
    @mock.patch.object(os.path, "exists", return_value=True)
    @mock.patch.object(shutil, "copytree")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_initialize_copies_assets_and_starts_agent(
        self,
        mock_path_exists,
        mock_copytree,
        mock_os_exists,
        mock_agent_cls,
        mock_symlink,
        mock_chmod,
        mock_open,
        mock_glob,
        mock_run_subprocess,
        mock_makedirs,
        mock_copy,
        mock_get_asset_path,
    ):
        # Mock Agent class to act as async context manager
        mock_agent_instance = mock_agent_cls.return_value
        mock_agent_instance.__aenter__ = mock.AsyncMock(
            return_value=mock_agent_instance
        )
        mock_agent_instance.__aexit__ = mock.AsyncMock()

        # Mock _run_subprocess_cmd as a coroutine mock
        mock_run_subprocess.return_value = None

        # Mock glob to dynamically resolve Python site-packages version patterns
        mock_glob.side_effect = lambda pattern: [
            pattern.replace("python*", "python3.14")
        ]

        head = agent_heads.AntigravityAgentHead(
            scenario_name="test_scen",
            scenario_path="/tmp/cxas_skill_eval/scenarios/test_scen.yaml",
            assets=["asset1.md"],
        )

        asyncio.run(head.initialize())

        mock_get_asset_path.assert_called_once_with(
            "/tmp/cxas_skill_eval/scenarios/test_scen.yaml",
            "asset1.md",
        )
        mock_copy.assert_called_once_with(
            "/src/path/asset1.md",
            os.path.join(head._workspace_dir, "asset1.md"),
        )
        mock_agent_cls.assert_called_once()
        mock_agent_instance.__aenter__.assert_called_once()

        # Dynamically resolve expected file paths pointing to workspace virtualenv
        expected_pth = os.path.join(
            head._workspace_dir,
            ".venv",
            "lib",
            "python3.14",
            "site-packages",
            "cxas-scrapi.pth",
        )
        expected_cxas = os.path.join(
            head._workspace_dir, ".venv", "bin", "cxas"
        )

        # Assert programmatic local editable linkage and CLI script wrapper writes
        mock_glob.assert_called_once()
        mock_open.assert_has_calls(
            [
                mock.call(expected_pth, "w"),
                mock.call(expected_cxas, "w"),
            ],
            any_order=True,
        )
        mock_chmod.assert_has_calls(
            [
                mock.call(head._workspace_dir, 0o755),
                mock.call(expected_cxas, 0o755),
            ],
            any_order=True,
        )

        # Assert dynamic shared skills symlink setup
        expected_parent_skills = os.path.join(
            agent_heads.sys.prefix, "share", "cxas-scrapi"
        )
        expected_local_skills = os.path.join(
            head._workspace_dir, ".venv", "share", "cxas-scrapi"
        )
        mock_symlink.assert_called_once_with(
            expected_parent_skills, expected_local_skills
        )

        mock_run_subprocess.assert_has_calls(
            [
                mock.call(
                    ["uv", "venv", "--system-site-packages"],
                    head._workspace_dir,
                ),
            ]
        )

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch.object(
        agent_heads.scenario,
        "get_asset_path",
        return_value="/src/path/asset1.md",
    )
    @mock.patch.object(shutil, "copy")
    @mock.patch.object(os, "makedirs")
    @mock.patch.object(agent_heads.AntigravityAgentHead, "_run_subprocess_cmd")
    @mock.patch.object(agent_heads.glob, "glob")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch.object(os, "chmod")
    @mock.patch.object(os, "symlink")
    @mock.patch("skill_eval.agent_heads.Agent")
    @mock.patch.object(os.path, "exists", return_value=True)
    @mock.patch.object(shutil, "copytree")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_initialize_propagates_project_env_variables(
        self,
        mock_path_exists,
        mock_copytree,
        mock_os_exists,
        mock_agent_cls,
        mock_symlink,
        mock_chmod,
        mock_open,
        mock_glob,
        mock_run_subprocess,
        mock_makedirs,
        mock_copy,
        mock_get_asset_path,
    ):
        # Mock Agent class to act as async context manager
        mock_agent_instance = mock_agent_cls.return_value
        mock_agent_instance.__aenter__ = mock.AsyncMock(
            return_value=mock_agent_instance
        )
        mock_agent_instance.__aexit__ = mock.AsyncMock()
        mock_run_subprocess.return_value = None
        mock_glob.side_effect = lambda pattern: [
            pattern.replace("python*", "python3.14")
        ]

        # Set up original env variables to verify restore
        os.environ["PATH"] = "/usr/bin"
        os.environ["VIRTUAL_ENV"] = "/parent/venv"
        os.environ["GCLOUD_PROJECT"] = "original-project"
        os.environ["GOOGLE_CLOUD_PROJECT"] = "original-project"

        mock_project = "mock-project-id"
        head = agent_heads.AntigravityAgentHead(
            scenario_name="test_scen",
            scenario_path="/tmp/cxas_skill_eval/scenarios/test_scen.yaml",
            project=mock_project,
        )

        # Hook into __aenter__ to assert active variables
        def assert_env_variables(*args, **kwargs):
            self.assertEqual(os.environ.get("GCLOUD_PROJECT"), mock_project)
            self.assertEqual(
                os.environ.get("GOOGLE_CLOUD_PROJECT"), mock_project
            )
            self.assertNotIn("CLOUDSDK_CONFIG", os.environ)
            return mock_agent_instance

        mock_agent_instance.__aenter__.side_effect = assert_env_variables

        asyncio.run(head.initialize())
        asyncio.run(head.close())

        # Verify restoration
        self.assertEqual(os.environ.get("PATH"), "/usr/bin")
        self.assertEqual(os.environ.get("VIRTUAL_ENV"), "/parent/venv")
        self.assertEqual(os.environ.get("GCLOUD_PROJECT"), "original-project")
        self.assertEqual(
            os.environ.get("GOOGLE_CLOUD_PROJECT"), "original-project"
        )

    # Deleting obsolete workspace cleanup tests because directory cleanup was completely reverted.


if __name__ == "__main__":
    unittest.main()
