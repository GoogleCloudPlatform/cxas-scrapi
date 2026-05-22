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

"""Tests for scrapi-sim-runner.py and run-all-evals.py wrappers.

Run from the project root:
    python -m pytest .agents/skills/cxas-agent-foundry/scripts/tests/test_runners.py
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def config_mock():
    """Fixture providing a dynamic config dictionary."""
    return {
        "gcp_project_id": "test-proj",
        "deployed_app_id": "test-app",
        "app_name": "test-app",
        "default_channel": "text",
        "modality": "text",
        "app_resource": "projects/test-proj/locations/us/apps/test-app",
    }


@pytest.fixture(autouse=True)
def setup_stubs(config_mock):
    """Setup fake config module so script imports do not trigger project lookups."""
    fake = types.ModuleType("config")
    fake.load_app_name = lambda: "projects/test-proj/locations/us/apps/test-app"
    fake.load_config = lambda: config_mock
    fake.get_project_path = lambda *a: "/tmp/fakeproj/" + "/".join(a)
    fake.resolve_project_dir = lambda: "/tmp/fakeproj"
    sys.modules["config"] = fake

    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)

    yield

    # Cleanup stub
    sys.modules.pop("config", None)


@pytest.fixture
def sim_runner():
    """Import the scrapi-sim-runner module."""
    spec = importlib.util.spec_from_file_location(
        "scrapi_sim_runner", os.path.join(_SCRIPTS_DIR, "scrapi-sim-runner.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def all_evals_runner():
    """Import the run-all-evals module."""
    spec = importlib.util.spec_from_file_location(
        "run_all_evals", os.path.join(_SCRIPTS_DIR, "run-all-evals.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test scrapi-sim-runner.py
# ---------------------------------------------------------------------------


def test_sim_runner_cmd_run_delegation(sim_runner):
    """Verify scrapi-sim-runner.py run command delegates directly to SimulationEvals."""
    mock_args = MagicMock()
    mock_args.eval = ["test_case_1"]
    mock_args.priority = "P0"
    mock_args.tag = None
    mock_args.model = "gemini-pro"
    mock_args.channel = "audio"
    mock_args.runs = 3
    mock_args.parallel = 1
    mock_args.verbose = True
    mock_args.gcs_report_path = None

    mock_templates = {
        "test_case_1": {
            "steps": [],
            "expectations": [],
            "session_parameters": {},
        }
    }

    # Mock conversation object returned by simulate_conversation
    mock_conv = MagicMock()
    mock_conv.current_turn = 1
    mock_conv.steps_progress = []
    mock_conv.expectation_results = []
    mock_conv.get_transcript.return_value = []
    mock_conv._session_id = "fake_sess"
    mock_conv._detailed_trace = []

    with (
        patch.object(sim_runner, "load_yaml", return_value={}),
        patch.object(
            sim_runner, "load_sim_templates", return_value=mock_templates
        ),
        patch.object(sim_runner, "EnhancedSimRunner") as MockEnhancedRunner,
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_html_report"
        ) as mock_gen_report,
    ):
        mock_sim_inst = MockEnhancedRunner.return_value
        mock_sim_inst.simulate_conversation.return_value = mock_conv

        sim_runner.cmd_run(mock_args)

        # Assert that EnhancedSimRunner was initialized with correct parameters
        assert MockEnhancedRunner.call_count == 3
        MockEnhancedRunner.assert_any_call(
            app_name="projects/test-proj/locations/us/apps/test-app",
            user_agent_extension=sim_runner.USER_AGENT_EXTENSION,
        )

        # Assert simulate_conversation was called with correct parameters
        mock_sim_inst.simulate_conversation.assert_any_call(
            test_case={
                "name": "test_case_1",
                "steps": [],
                "expectations": [],
                "session_parameters": {},
                "metadata": {},
            },
            model="gemini-pro",
            console_logging=True,
            modality="audio",
        )


# ---------------------------------------------------------------------------
# Test run-all-evals.py
# ---------------------------------------------------------------------------


def test_run_all_evals_includes_all_by_default(all_evals_runner):
    """Verify run-all-evals.py invokes combined report with goldens, sims, and scenarios by default."""
    mock_args = MagicMock()
    mock_args.channel = "text"
    mock_args.runs = 5
    mock_args.skip_sims = False
    mock_args.skip_goldens = False
    mock_args.priority = "P0"
    mock_args.sim_parallel = 4

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=mock_args),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
        ) as mock_gen_report,
    ):
        all_evals_runner.main()

        # Assert generate_combined_report_from_dir was called with goldens + sims + tools + callbacks
        mock_gen_report.assert_called_once()
        call_kwargs = mock_gen_report.call_args[1]

        assert "goldens" in call_kwargs["include"]
        assert "sims" in call_kwargs["include"]
        assert "tools" in call_kwargs["include"]
        assert "callbacks" in call_kwargs["include"]
        assert call_kwargs["runs"] == 5
        assert call_kwargs["modality"] == "text"
        assert call_kwargs["filter_tags"] == ["P0"]
        assert call_kwargs["parallel"] == 4


def test_run_all_evals_excludes_goldens_and_sims(all_evals_runner, config_mock):
    """Verify run-all-evals.py skips goldens and sims correctly based on CLI flags."""
    # Set dynamic config mock modality to audio
    config_mock["modality"] = "audio"
    config_mock["default_channel"] = "audio"

    mock_args = MagicMock()
    mock_args.channel = "audio"
    mock_args.runs = 2
    mock_args.skip_sims = True
    mock_args.skip_goldens = True
    mock_args.priority = "P1,P2"
    mock_args.sim_parallel = 8

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=mock_args),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
        ) as mock_gen_report,
    ):
        all_evals_runner.main()

        mock_gen_report.assert_called_once()
        call_kwargs = mock_gen_report.call_args[1]

        assert "tools" in call_kwargs["include"]
        assert "callbacks" in call_kwargs["include"]
        assert "goldens" not in call_kwargs["include"]
        assert "sims" not in call_kwargs["include"]
        assert call_kwargs["runs"] == 2
        assert call_kwargs["modality"] == "audio"
        assert call_kwargs["filter_tags"] == ["P1", "P2"]
        assert call_kwargs["parallel"] == 8
