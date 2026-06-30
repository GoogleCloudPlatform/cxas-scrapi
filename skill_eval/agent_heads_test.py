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

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch.object(
        agent_heads.scenario,
        "get_asset_path",
        return_value="/src/path/asset1.md",
    )
    @mock.patch.object(shutil, "copy")
    @mock.patch.object(os, "makedirs")
    @mock.patch.object(agent_heads.AntigravityAgentHead, "_run_subprocess_cmd")
    @mock.patch.object(os, "chmod")
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
        mock_chmod,
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

        # Assert LocalAgentConfig was called with correct skills_paths
        config_passed = mock_agent_cls.call_args[0][0]
        expected_skills_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.abspath(agent_heads.__file__))
            ),
            ".agents",
            "skills",
        )
        self.assertEqual(config_passed.skills_paths, [expected_skills_path])

        # Assert clean uv venv and pip install are executed
        mock_run_subprocess.assert_has_calls(
            [
                mock.call(
                    ["uv", "venv", "--python", agent_heads.sys.executable],
                    head._workspace_dir,
                ),
                mock.call(
                    ["uv", "pip", "install", mock.ANY],
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
    @mock.patch.object(os, "chmod")
    @mock.patch("skill_eval.agent_heads.Agent")
    @mock.patch.object(os.path, "exists", return_value=True)
    @mock.patch.object(shutil, "copytree")
    @mock.patch("pathlib.Path.exists", return_value=True)
    @mock.patch("google.auth.default")
    @mock.patch.object(
        agent_heads.AntigravityAgentHead, "_get_uv_index_env_vars"
    )
    def test_initialize_propagates_project_env_variables(
        self,
        mock_get_uv_env_vars,
        mock_auth_default,
        mock_path_exists,
        mock_copytree,
        mock_os_exists,
        mock_agent_cls,
        mock_chmod,
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

        # Mock google.auth.default to return valid credentials with mock token
        mock_creds = mock.Mock()
        mock_creds.valid = False
        mock_creds.token = "mock-gcp-token"
        mock_auth_default.return_value = (mock_creds, "mock-project")

        # Mock _get_uv_index_env_vars to return mock index variables
        mock_get_uv_env_vars.return_value = {
            "UV_INDEX_PRIVATE_DEFAULT_USERNAME": "oauth2accesstoken",
            "UV_INDEX_PRIVATE_DEFAULT_PASSWORD": "mock-gcp-token",
        }

        # Set up original env variables to verify restore
        os.environ["PATH"] = "/usr/bin"
        os.environ["VIRTUAL_ENV"] = "/parent/venv"
        os.environ["GCLOUD_PROJECT"] = "original-project"
        os.environ["GOOGLE_CLOUD_PROJECT"] = "original-project"
        os.environ["UV_KEYRING_PROVIDER"] = "original-provider"
        os.environ["UV_INDEX_PRIVATE_DEFAULT_USERNAME"] = (
            "original-username"
        )
        os.environ["UV_INDEX_PRIVATE_DEFAULT_PASSWORD"] = (
            "original-password"
        )

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

            # Verify active child virtual environment in os.environ
            expected_venv = os.path.join(head._workspace_dir, ".venv")
            self.assertEqual(os.environ.get("VIRTUAL_ENV"), expected_venv)
            self.assertTrue(
                os.environ.get("PATH").startswith(
                    os.path.join(expected_venv, "bin")
                )
            )
            self.assertEqual(
                os.environ.get("UV_KEYRING_PROVIDER"), "subprocess"
            )
            self.assertEqual(
                os.environ.get("UV_INDEX_PRIVATE_DEFAULT_USERNAME"),
                "oauth2accesstoken",
            )
            self.assertEqual(
                os.environ.get("UV_INDEX_PRIVATE_DEFAULT_PASSWORD"),
                "mock-gcp-token",
            )
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
        self.assertEqual(
            os.environ.get("UV_KEYRING_PROVIDER"), "original-provider"
        )
        self.assertEqual(
            os.environ.get("UV_INDEX_PRIVATE_DEFAULT_USERNAME"),
            "original-username",
        )
        self.assertEqual(
            os.environ.get("UV_INDEX_PRIVATE_DEFAULT_PASSWORD"),
            "original-password",
        )

    # Deleting obsolete workspace cleanup tests because directory cleanup was completely reverted.

    @mock.patch("pathlib.Path.exists")
    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=b"""
[[index]]
name = "custom-index-name"
url = "https://example.com/simple"
""",
    )
    def test_get_uv_index_env_vars_parses_toml(self, mock_file, mock_exists):
        mock_exists.return_value = True
        head = agent_heads.AntigravityAgentHead(
            scenario_name="test-scenario",
            scenario_path="/tmp/scenarios/test.yaml",
            project="test-project",
            location="test-location",
        )
        env_vars = head._get_uv_index_env_vars("test-token")
        self.assertEqual(
            env_vars,
            {
                "UV_INDEX_CUSTOM_INDEX_NAME_USERNAME": "oauth2accesstoken",
                "UV_INDEX_CUSTOM_INDEX_NAME_PASSWORD": "test-token",
            },
        )


if __name__ == "__main__":
    unittest.main()
