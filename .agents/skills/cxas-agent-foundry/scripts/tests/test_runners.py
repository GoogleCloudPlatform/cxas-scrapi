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

"""Tests for scrapi-sim-runner.py and run-evals.py wrappers.

Run from the project root:
    python -m pytest \
        .agents/skills/cxas-agent-foundry/scripts/tests/test_runners.py
"""

import importlib.util
import inspect
import json
import os
import pathlib
import sys
import types
import typing
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils import reporting as _reporting_mod

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_SPEC = importlib.util.spec_from_file_location(
    "_real_foundry_config", os.path.join(_SCRIPTS_DIR, "config.py")
)
_REAL_CONFIG = importlib.util.module_from_spec(_CONFIG_SPEC)
_CONFIG_SPEC.loader.exec_module(_REAL_CONFIG)


def _expected_loc_param(target: typing.Any) -> str:
    """Return expected location kwarg name for current SDK target signature."""
    if "gemini_location" in inspect.signature(target).parameters:
        return "gemini_location"
    return "vertex_location"


@pytest.fixture
def config_mock() -> typing.Any:
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
def setup_stubs(
    config_mock: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> typing.Any:
    """Setup fake config module and clear ambient location env vars.

    Prevents script imports from triggering project lookups.
    """
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GECX_PROJECT", raising=False)

    def _resolve_loc(
        explicit: str | None = None,
        cfg: dict[str, typing.Any] | None = None,
    ) -> str:
        return _REAL_CONFIG.resolve_gemini_location_from_config(
            explicit, cfg if cfg is not None else config_mock
        )

    fake = types.ModuleType("config")
    fake.load_app_name = lambda: "projects/test-proj/locations/us/apps/test-app"
    fake.load_config = lambda: config_mock
    fake.get_project_path = lambda *a: "/tmp/fakeproj/" + "/".join(a)
    fake.resolve_project_dir = lambda: "/tmp/fakeproj"
    fake.resolve_gemini_location_from_config = _resolve_loc
    fake.gemini_location_kwarg = _REAL_CONFIG.gemini_location_kwarg
    sys.modules["config"] = fake

    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)

    yield

    # Cleanup stub
    sys.modules.pop("config", None)


@pytest.fixture
def evals_runner() -> typing.Any:
    """Import the run-evals module."""
    spec = importlib.util.spec_from_file_location(
        "run_evals", os.path.join(_SCRIPTS_DIR, "run-evals.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sim_runner() -> typing.Any:
    """Import the scrapi-sim-runner module."""
    spec = importlib.util.spec_from_file_location(
        "scrapi_sim_runner", os.path.join(_SCRIPTS_DIR, "scrapi-sim-runner.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test scrapi-sim-runner.py
# ---------------------------------------------------------------------------


def test_sim_runner_cmd_run_delegation(sim_runner: typing.Any) -> typing.Any:
    """Verify scrapi-sim-runner.py run command delegates to SimulationEvals."""
    mock_args = MagicMock()
    mock_args.eval = ["test_case_1"]
    mock_args.priority = "P0"
    mock_args.tag = None
    mock_args.model = "gemini-pro"
    mock_args.sim_user_model = None
    mock_args.eval_model = None
    mock_args.channel = "audio"
    mock_args.runs = 3
    mock_args.parallel = 1
    mock_args.verbose = True
    mock_args.gcs_report_path = None
    mock_args.expectations_only = False
    mock_args.use_tool_fakes = False
    mock_args.capture_agent_audio = False
    mock_args.deployment_id = None
    mock_args.background_noise = None
    mock_args.bg_noise_level = None
    mock_args.bg_noise_snr = None
    mock_args.burst_noise_level = None
    mock_args.burst_noise_snr = None
    mock_args.burst_noise_files = None
    mock_args.gemini_location = None

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
        patch.object(
            sim_runner, "EnhancedSimRunner", autospec=True
        ) as MockEnhancedRunner,
        patch("cxas_scrapi.utils.reporting.generate_html_report"),
    ):
        mock_sim_inst = MockEnhancedRunner.return_value
        mock_sim_inst.run_simulations.return_value = []

        sim_runner.cmd_run(mock_args)

        sim_loc_key = _expected_loc_param(sim_runner.SimulationEvals.__init__)
        # Assert that EnhancedSimRunner was initialized with correct parameters
        assert MockEnhancedRunner.call_count == 1
        MockEnhancedRunner.assert_any_call(
            app_name="projects/test-proj/locations/us/apps/test-app",
            user_agent_extension="skill/cxas-agent-foundry/scrapi-sim-runner",
            expectations_only=False,
            deployment_id=None,
            **{sim_loc_key: "global"},
        )

        # Assert run_simulations was called with correct parameters
        mock_sim_inst.run_simulations.assert_any_call(
            test_cases=[
                {
                    "name": "test_case_1",
                    "steps": [],
                    "expectations": [],
                    "audio_expectations": [],
                    "session_parameters": {},
                    "metadata": {},
                }
            ],
            runs=3,
            parallel=1,
            sim_user_model="gemini-pro",
            eval_model="gemini-pro",
            modality="audio",
            verbose=True,
            use_tool_fakes=False,
            capture_agent_audio=False,
            background_noise_file=None,
            bg_noise_snr=None,
            burst_noise_files=None,
            burst_noise_snr=None,
        )


def test_sim_runner_cmd_run_decoupled_models(
    sim_runner: typing.Any,
) -> typing.Any:
    """Verify scrapi-sim-runner parses and forwards decoupled model flags."""
    mock_templates = {
        "test_case_1": {
            "steps": [],
            "expectations": [],
            "session_parameters": {},
        }
    }
    argv = [
        "scrapi-sim-runner.py",
        "run",
        "--eval",
        "test_case_1",
        "--model",
        "fallback-model",
        "--sim-user-model",
        "gemini-3.5-flash-lite",
        "--eval-model",
        "gemini-3-flash-preview",
    ]

    with (
        patch.object(sys, "argv", argv),
        patch.object(sim_runner, "load_yaml", return_value={}),
        patch.object(
            sim_runner, "load_sim_templates", return_value=mock_templates
        ),
        patch.object(
            sim_runner, "EnhancedSimRunner", autospec=True
        ) as MockEnhancedRunner,
        patch.object(sim_runner, "generate_html_report"),
    ):
        mock_sim_inst = MockEnhancedRunner.return_value
        mock_sim_inst.run_simulations.return_value = []

        sim_runner.main()

        mock_sim_inst.run_simulations.assert_any_call(
            test_cases=[
                {
                    "name": "test_case_1",
                    "steps": [],
                    "expectations": [],
                    "audio_expectations": [],
                    "session_parameters": {},
                    "metadata": {},
                }
            ],
            runs=1,
            parallel=1,
            sim_user_model="gemini-3.5-flash-lite",
            eval_model="gemini-3-flash-preview",
            modality="text",
            verbose=False,
            use_tool_fakes=False,
            capture_agent_audio=False,
            background_noise_file=None,
            bg_noise_snr=None,
            burst_noise_files=None,
            burst_noise_snr=None,
        )


# ---------------------------------------------------------------------------
# Test run-evals.py
# ---------------------------------------------------------------------------


def test_run_evals_includes_all_by_default(
    evals_runner: typing.Any,
) -> typing.Any:
    """Verify run-evals.py invokes combined report.

    It should include goldens, sims, and scenarios by default.
    """
    mock_args = MagicMock()
    mock_args.channel = "text"
    mock_args.runs = 5
    mock_args.skip_sims = False
    mock_args.skip_goldens = False
    mock_args.priority = "P0"
    mock_args.sim_parallel = 4
    mock_args.gemini_location = None

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=mock_args),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
            autospec=True,
        ) as mock_gen_report,
    ):
        evals_runner.main()

        # Assert generate_combined_report_from_dir was called with
        # goldens + sims + tools + callbacks
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


def test_run_evals_excludes_goldens_and_sims(
    evals_runner: typing.Any, config_mock: typing.Any
) -> typing.Any:
    """Verify run-evals.py skips goldens and sims correctly.

    Based on CLI flags.
    """
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
    mock_args.gemini_location = None

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=mock_args),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
            autospec=True,
        ) as mock_gen_report,
    ):
        evals_runner.main()

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


def test_gemini_location_kwarg_signatures() -> None:
    """Verify gemini_location_kwarg handles pre-6a and post-6a signatures."""

    def legacy_fn(vertex_location: str = "global") -> None:
        del vertex_location

    def modern_fn(
        vertex_location: str = "global",
        gemini_location: str | None = None,
    ) -> None:
        del vertex_location, gemini_location

    assert _REAL_CONFIG.gemini_location_kwarg(legacy_fn, "europe-west4") == {
        "vertex_location": "europe-west4"
    }
    assert _REAL_CONFIG.gemini_location_kwarg(modern_fn, "europe-west4") == {
        "gemini_location": "europe-west4"
    }


def test_runners_gemini_location_forwarding(
    evals_runner: typing.Any,
    sim_runner: typing.Any,
    config_mock: typing.Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify config, env, and --gemini-location forwarding across runners."""
    assert (
        _REAL_CONFIG.resolve_gemini_location_from_config(
            None, {"vertex_location": "asia-northeast1"}
        )
        == "asia-northeast1"
    )
    assert (
        _REAL_CONFIG.resolve_gemini_location_from_config(
            None,
            {
                "gemini_location": "europe-west1",
                "vertex_location": "asia-northeast1",
            },
        )
        == "europe-west1"
    )
    config_mock["gemini_location"] = "europe-west1"
    sim_loc_key = _expected_loc_param(sim_runner.SimulationEvals.__init__)
    report_loc_key = _expected_loc_param(
        _reporting_mod.generate_combined_report_from_dir
    )

    mock_templates = {
        "test_case_1": {
            "steps": [],
            "expectations": [],
            "session_parameters": {},
        }
    }

    # 1. Config gemini_location used when CLI flag and env var are unset.
    with (
        patch.object(
            sys,
            "argv",
            ["scrapi-sim-runner.py", "run", "--eval", "test_case_1"],
        ),
        patch.object(sim_runner, "load_yaml", return_value={}),
        patch.object(
            sim_runner, "load_sim_templates", return_value=mock_templates
        ),
        patch.object(
            sim_runner, "EnhancedSimRunner", autospec=True
        ) as mock_sim_cls,
        patch.object(sim_runner, "generate_html_report"),
    ):
        mock_sim_cls.return_value.run_simulations.return_value = []
        sim_runner.main()
        assert mock_sim_cls.call_args.kwargs[sim_loc_key] == "europe-west1"

    # 2. GOOGLE_CLOUD_LOCATION overrides gecx-config.json gemini_location.
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    with (
        patch.object(sys, "argv", ["run-evals.py", "--skip-goldens"]),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
            autospec=True,
        ) as mock_gen_report,
    ):
        evals_runner.main()
        assert mock_gen_report.call_args.kwargs[report_loc_key] == "us-central1"

    # 3. Explicit --gemini-location overrides both env var and gecx-config.json.
    with (
        patch.object(
            sys,
            "argv",
            [
                "run-evals.py",
                "--skip-goldens",
                "--gemini-location",
                "europe-west4",
            ],
        ),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
            autospec=True,
        ) as mock_gen_report,
    ):
        evals_runner.main()
        assert (
            mock_gen_report.call_args.kwargs[report_loc_key] == "europe-west4"
        )


def test_optional_config_discovery_and_partial_dict_fallback(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify 4-step gecx-config.json discovery and partial-dict fallback."""
    (tmp_path / ".agents").mkdir()
    monkeypatch.chdir(tmp_path)

    # 4. Single <subdir>/gecx-config.json auto-detection & partial cfg fallback.
    single_dir = tmp_path / "proj_single"
    single_dir.mkdir()
    (single_dir / "gecx-config.json").write_text(
        json.dumps({"gemini_location": "us-east4"}), encoding="utf-8"
    )
    assert _REAL_CONFIG.resolve_gemini_location_from_config() == "us-east4"
    assert (
        _REAL_CONFIG.resolve_gemini_location_from_config(
            cfg={"gemini_location": ""}
        )
        == "us-east4"
    )
    assert (
        _REAL_CONFIG.resolve_gemini_location_from_config(cfg={}) == "us-east4"
    )

    # 3. CWD ./gecx-config.json beats single-subdir auto-detect.
    (tmp_path / "gecx-config.json").write_text(
        json.dumps({"gemini_location": "us-west1"}), encoding="utf-8"
    )
    assert _REAL_CONFIG.resolve_gemini_location_from_config() == "us-west1"

    # 2. .active-project pointer overrides CWD ./gecx-config.json.
    active_dir = tmp_path / "proj_active"
    active_dir.mkdir()
    (active_dir / "gecx-config.json").write_text(
        json.dumps({"gemini_location": "europe-west1"}), encoding="utf-8"
    )
    (tmp_path / ".active-project").write_text("proj_active\n", encoding="utf-8")
    assert _REAL_CONFIG.resolve_gemini_location_from_config() == "europe-west1"

    # 1. GECX_PROJECT env var overrides .active-project and CWD.
    env_dir = tmp_path / "proj_env"
    env_dir.mkdir()
    (env_dir / "gecx-config.json").write_text(
        json.dumps({"gemini_location": "asia-northeast1"}), encoding="utf-8"
    )
    monkeypatch.setenv("GECX_PROJECT", "proj_env")
    assert (
        _REAL_CONFIG.resolve_gemini_location_from_config() == "asia-northeast1"
    )

    # Malformed JSON falls back without raising.
    (env_dir / "gecx-config.json").write_text("{bad json", encoding="utf-8")
    assert _REAL_CONFIG.resolve_gemini_location_from_config() == "europe-west1"

    broken_ws = tmp_path / "broken_ws"
    (broken_ws / ".agents").mkdir(parents=True)
    (broken_ws / "gecx-config.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.delenv("GECX_PROJECT", raising=False)
    monkeypatch.chdir(broken_ws)
    assert _REAL_CONFIG.resolve_gemini_location_from_config() == "global"


def test_configure_resolve_gemini_location() -> None:
    """Verify configure.py _resolve_gemini_location precedence and stripping."""
    cfg_spec = importlib.util.spec_from_file_location(
        "_foundry_configure", os.path.join(_SCRIPTS_DIR, "configure.py")
    )
    assert cfg_spec is not None and cfg_spec.loader is not None
    cfg_mod = importlib.util.module_from_spec(cfg_spec)
    cfg_spec.loader.exec_module(cfg_mod)

    assert (
        cfg_mod._resolve_gemini_location(
            "  europe-west4 ", {"gemini_location": "us-central1"}
        )
        == "europe-west4"
    )
    assert (
        cfg_mod._resolve_gemini_location(
            "   ",
            {
                "gemini_location": " europe-west1 ",
                "vertex_location": "asia-east1",
            },
        )
        == "europe-west1"
    )
    assert (
        cfg_mod._resolve_gemini_location(
            None, {"vertex_location": " asia-east1 "}
        )
        == "asia-east1"
    )
    assert cfg_mod._resolve_gemini_location(None, {}) is None
    assert cfg_mod._resolve_gemini_location(None, None) is None
