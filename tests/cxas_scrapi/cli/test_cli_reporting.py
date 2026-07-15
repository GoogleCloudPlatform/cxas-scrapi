import json
from unittest.mock import patch

import pandas as pd

from cxas_scrapi.cli.main import combined_evals_report_cmd


def test_combined_evals_report_cmd(tmp_path):
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

    class Args:
        def __init__(self):
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
            persist_bidi_websocket=False,
            progress_callback=None,
            capture_agent_audio=False,
        )


def test_combined_evals_report_cmd_with_modality_and_runs(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args:
        def __init__(self):
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
            persist_bidi_websocket=False,
            progress_callback=None,
            capture_agent_audio=False,
        )


@patch("cxas_scrapi.cli.main.datetime.datetime", autospec=True)
def test_combined_evals_report_cmd_timestamped(mock_datetime, tmp_path):
    # Mock datetime.now() to return a fixed value
    mock_datetime.now.return_value.strftime.return_value = "20260622_171403"

    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args:
        def __init__(self):
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
            persist_bidi_websocket=False,
            progress_callback=None,
            capture_agent_audio=False,
        )


def test_combined_evals_report_cmd_with_filters_and_progress(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args:
        def __init__(self):
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


def test_combined_evals_report_cmd_with_deployment_id(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()

    class Args:
        def __init__(self):
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
