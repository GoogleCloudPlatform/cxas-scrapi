import argparse
import json
import typing
from unittest.mock import patch

import pandas as pd
import pytest

from cxas_scrapi.cli.main import combined_evals_report_cmd
from cxas_scrapi.cli.main import main as cli_main


def test_combined_evals_report_cmd(tmp_path: typing.Any) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    # Create dummy files
    sim_file = evals_dir / "sim_results.json"
    sim_file.write_text(json.dumps([{"name": "test_sim", "passed": True}]))

    tool_file = evals_dir / "tool_results.csv"
    df_tool = pd.DataFrame(
        [
            {
                "test_name": "test_tool",
                "tool": "my_tool",
                "status": "PASSED",
                "latency (ms)": 50,
                "errors": "",
            }
        ]
    )
    df_tool.to_csv(tool_file, index=False)

    callback_file = evals_dir / "callback_results.csv"
    df_callback = pd.DataFrame(
        [
            {
                "test_name": "test_callback",
                "agent_name": "my_agent",
                "callback_type": "my_callback",
                "status": "PASSED",
                "error_message": "",
            }
        ]
    )
    df_callback.to_csv(callback_file, index=False)

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.sim_user_model = None
            self.eval_model = None

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name=None,
            output_path=None,
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="text",
            sim_user_model=None,
            eval_model=None,
            runs=1,
            filter_files=[],
            filter_tags=[],
            filter_names=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
            use_tool_fakes=False,
            timestamp=None,
            expectations_only=False,
            deployment_id=None,
            progress_callback=None,
            capture_agent_audio=False,
            single_bidi_stream=False,
            skip_playback_wait=False,
            report_format="html",
            vertex_location=None,
            naturalness=None,
        )


def test_combined_evals_report_cmd_with_modality_and_runs(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "audio"
            self.runs = 5
            self.use_tool_fakes = False
            self.sim_user_model = None
            self.eval_model = None

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name=None,
            output_path=None,
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="audio",
            sim_user_model=None,
            eval_model=None,
            runs=5,
            filter_files=[],
            filter_tags=[],
            filter_names=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
            use_tool_fakes=False,
            timestamp=None,
            expectations_only=False,
            deployment_id=None,
            progress_callback=None,
            capture_agent_audio=False,
            single_bidi_stream=False,
            skip_playback_wait=False,
            report_format="html",
            vertex_location=None,
            naturalness=None,
        )


@patch("cxas_scrapi.cli.main.datetime.datetime", autospec=True)
def test_combined_evals_report_cmd_timestamped(
    mock_datetime: typing.Any, tmp_path: typing.Any
) -> None:
    # Mock datetime.now() to return a fixed value
    mock_datetime.now.return_value.strftime.return_value = "20260622_171403"

    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.timestamped = True
            self.sim_user_model = None
            self.eval_model = None

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name=None,
            output_path=None,
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="text",
            sim_user_model=None,
            eval_model=None,
            runs=1,
            filter_files=[],
            filter_tags=[],
            filter_names=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
            use_tool_fakes=False,
            timestamp="20260622_171403",
            expectations_only=False,
            deployment_id=None,
            progress_callback=None,
            capture_agent_audio=False,
            single_bidi_stream=False,
            skip_playback_wait=False,
            report_format="html",
            vertex_location=None,
            naturalness=None,
        )


def test_combined_evals_report_cmd_with_filters_and_progress(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.filter_files = "sim1.yaml,sim2.yaml"
            self.filter_tags = "P0,P1"
            self.filter_names = "test_name_1,test_name_2"
            self.json_progress = True
            self.sim_user_model = None
            self.eval_model = None

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        # Verify call arguments
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["filter_files"] == ["sim1.yaml", "sim2.yaml"]
        assert call_kwargs["filter_tags"] == ["P0", "P1"]
        assert call_kwargs["filter_names"] == ["test_name_1", "test_name_2"]
        assert call_kwargs["progress_callback"] is not None


def test_combined_evals_report_cmd_format_json(tmp_path: typing.Any) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.sim_user_model = None
            self.eval_model = None
            self.format = "json"

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["report_format"] == "json"


def test_combined_evals_report_cmd_with_deployment_id(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.deployment_id = "test-dep-id"
            self.sim_user_model = None
            self.eval_model = None

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["deployment_id"] == "test-dep-id"


def test_combined_evals_report_cmd_with_vertex_location(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.deployment_id = None
            self.sim_user_model = None
            self.eval_model = None
            self.vertex_location = "europe-west4"

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["vertex_location"] == "europe-west4"


@pytest.mark.parametrize("flag_value", [True, False, None])
def test_combined_evals_report_cmd_naturalness_passthrough(
    tmp_path: typing.Any, flag_value: bool | None
) -> None:
    """The tri-state flag reaches the report generator unchanged.

    `False` must survive as `False` so that `--no-naturalness` can switch
    off a metric a test case declared, which is what makes it useful for
    hill-climbing on correctness first.
    """
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1
            self.use_tool_fakes = False
            self.deployment_id = None
            self.sim_user_model = None
            self.eval_model = None
            self.naturalness = flag_value

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        assert mock_report.call_args[1]["naturalness"] is flag_value


@pytest.mark.parametrize("flag_value", [True, False, None])
def test_combined_evals_report_cmd_audio_analysis_passthrough(
    tmp_path: typing.Any, flag_value: bool | None
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "audio"
            self.runs = 1
            self.use_tool_fakes = False
            self.deployment_id = None
            self.sim_user_model = None
            self.eval_model = None
            self.audio_analysis = flag_value

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        assert mock_report.call_args[1].get("audio_analysis") is flag_value


def test_combined_evals_report_cmd_exits_nonzero_when_gate_fails(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "text"
            self.runs = 3
            self.use_tool_fakes = False
            self.sim_user_model = None
            self.eval_model = None
            self.min_run_passes = 2
            self.min_pass_rate = 0.8

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
        return_value=(
            str(evals_dir / "combined_report.html"),
            {"gate_passed": False, "pass_rate": 0.5},
        ),
    ) as mock_report:
        with pytest.raises(SystemExit) as excinfo:
            combined_evals_report_cmd(args)
        assert excinfo.value.code == 1
        mock_report.assert_called_once()
        assert mock_report.call_args[1]["min_run_passes"] == 2
        assert mock_report.call_args[1]["min_pass_rate"] == 0.8
        assert mock_report.call_args[1]["return_gate_status"] is True


def test_combined_evals_report_cmd_new_upstream_flags(
    tmp_path: typing.Any,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args(argparse.Namespace):
        def __init__(self) -> None:
            self.output_dir = str(evals_dir)
            self.output = None
            self.gcs_path = None
            self.golden_run = None
            self.app_name = "projects/p/locations/l/apps/a"
            self.run = True
            self.app_dir = None
            self.tool_test_file = None
            self.goldens_dir = None
            self.simulation_dir = None
            self.include = "sims"
            self.input_dir = None
            self.modality = "audio"
            self.runs = 1
            self.use_tool_fakes = False
            self.sim_user_model = None
            self.eval_model = None
            self.exclude_files = "broken.yaml,wip.json"
            self.sim_user_voices = "en-US-Journey-D,es-US-Journey-F"
            self.junit_xml = str(evals_dir / "sponge_log.xml")
            self.testsuite_prefix = "custom_evals"
            self.report_title = "Custom Eval Dashboard"
            self.dry_run = True
            self.enriched_report = True
            self.recordings_bucket = "gs://my-ces-recordings/prefix"

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
        return_value=("", {"gate_passed": True, "dry_run": True}),
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["exclude_files"] == ["broken.yaml", "wip.json"]
        assert call_kwargs["sim_user_voices"] == [
            "en-US-Journey-D",
            "es-US-Journey-F",
        ]
        assert call_kwargs["junit_xml"] == str(evals_dir / "sponge_log.xml")
        assert call_kwargs["testsuite_prefix"] == "custom_evals"
        assert call_kwargs["report_title"] == "Custom Eval Dashboard"
        assert call_kwargs["dry_run"] is True
        assert call_kwargs["enriched_report"] is True
        assert (
            call_kwargs["recordings_bucket"] == "gs://my-ces-recordings/prefix"
        )
        assert call_kwargs["return_gate_status"] is True


@pytest.mark.parametrize(
    ("gemini_loc", "vertex_loc", "expected_run_loc", "expected_report_arg"),
    [
        (None, None, "europe-west4", None),
        ("us-east4", None, "us-east4", "us-east4"),
        (None, "europe-west1", "europe-west1", "europe-west1"),
        ("   ", "europe-west1", "europe-west1", "europe-west1"),
        ("us-east4", "europe-west1", "us-east4", "us-east4"),
    ],
)
def test_combined_evals_report_cmd_resolves_gemini_location_and_backwards_compat(
    tmp_path: typing.Any,
    monkeypatch: typing.Any,
    gemini_loc: str | None,
    vertex_loc: str | None,
    expected_run_loc: str,
    expected_report_arg: str | None,
) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")

    args = argparse.Namespace(
        output_dir=str(evals_dir),
        output=str(tmp_path / "report.html"),
        gcs_path=None,
        golden_run=None,
        app_name="projects/p/locations/us/apps/a",
        run=True,
        app_dir=None,
        tool_test_file=None,
        goldens_dir=None,
        simulation_dir=None,
        include="sims",
        input_dir=None,
        modality="text",
        runs=1,
        use_tool_fakes=False,
        sim_user_model=None,
        eval_model=None,
        gemini_location=gemini_loc,
        vertex_location=vertex_loc,
    )

    with (
        patch(
            "cxas_scrapi.utils.reporting.run_all_evals",
            return_value={
                "callback": [],
                "tool": [],
                "golden": [],
                "simulation": [],
            },
        ) as mock_run_all,
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_html_report",
            return_value=str(tmp_path / "report.html"),
        ),
    ):
        combined_evals_report_cmd(args)
        assert (
            mock_run_all.call_args.kwargs["vertex_location"] == expected_run_loc
        )

    cli_flags: list[str] = []
    if gemini_loc is not None:
        cli_flags.extend(["--gemini-location", gemini_loc])
    if vertex_loc is not None:
        cli_flags.extend(["--vertex-location", vertex_loc])

    with (
        patch(
            "sys.argv",
            [
                "cxas",
                "evals",
                "report",
                "--output-dir",
                str(evals_dir),
                *cli_flags,
            ],
        ),
        patch(
            "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
        ) as mock_gen_report,
    ):
        cli_main()
        assert (
            mock_gen_report.call_args.kwargs["vertex_location"]
            == expected_report_arg
        )
