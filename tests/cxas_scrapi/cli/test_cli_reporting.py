import json
from unittest.mock import patch

import pandas as pd

from cxas_scrapi.cli.main import combined_evals_report_cmd


@patch("cxas_scrapi.core.workspace.load_workspace_config", autospec=True)
def test_combined_evals_report_cmd(mock_load_config, tmp_path):
    mock_load_config.return_value = {
        "gcp_project_id": "test-project",
        "deployed_app_id": "test-app",
        "location": "global",
        "evals_dir": str(tmp_path / "evals"),
    }
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
            self.format = "html"
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name="projects/test-project/locations/global/apps/test-app",
            output_path=str(evals_dir / "combined_report.html"),
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="text",
            runs=1,
            filter_files=[],
            filter_tags=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
        )


@patch("cxas_scrapi.core.workspace.load_workspace_config", autospec=True)
def test_combined_evals_report_cmd_with_modality_and_runs(
    mock_load_config, tmp_path
):
    mock_load_config.return_value = {
        "gcp_project_id": "test-project",
        "deployed_app_id": "test-app",
        "location": "global",
        "evals_dir": str(tmp_path / "evals"),
    }
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
            self.format = "html"
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "audio"
            self.runs = 5

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name="projects/test-project/locations/global/apps/test-app",
            output_path=str(evals_dir / "combined_report.html"),
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="audio",
            runs=5,
            filter_files=[],
            filter_tags=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
        )


@patch("cxas_scrapi.core.workspace.load_workspace_config", autospec=True)
def test_combined_evals_report_cmd_resolves_gcs_path_from_workspace_config(
    mock_load_config, tmp_path
):
    mock_load_config.return_value = {
        "gcs_path": "gs://my-bucket/report.html",
        "gcp_project_id": "test-project",
        "deployed_app_id": "test-app",
        "location": "global",
        "evals_dir": str(tmp_path / "evals"),
    }
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
            self.format = "html"
            self.include = "sims,goldens,scenarios"
            self.input_dir = None
            self.modality = "text"
            self.runs = 1

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir",
        autospec=True,
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            output_dir=str(evals_dir),
            golden_run=None,
            app_name="projects/test-project/locations/global/apps/test-app",
            output_path="gs://my-bucket/report.html",
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            include=["sims", "goldens", "scenarios"],
            modality="text",
            runs=1,
            filter_files=[],
            filter_tags=[],
            parallel=5,
            golden_timeout=600,
            bg_noise_file=None,
            burst_noise_files=None,
        )
