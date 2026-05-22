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
            app_name=None,
            output_path=str(evals_dir / "combined_report.html"),
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            format="html",
            include=["sims", "goldens", "scenarios"],
            modality="text",
            runs=1,
            filter_files=[],
            filter_tags=[],
            parallel=5,
            golden_timeout=600,
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
            app_name=None,
            output_path=str(evals_dir / "combined_report.html"),
            run=False,
            app_dir=None,
            tool_test_file=None,
            goldens_dir=None,
            simulation_dir=None,
            format="html",
            include=["sims", "goldens", "scenarios"],
            modality="audio",
            runs=5,
            filter_files=[],
            filter_tags=[],
            parallel=5,
            golden_timeout=600,
        )
