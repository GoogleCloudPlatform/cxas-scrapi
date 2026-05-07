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
    df_tool = pd.DataFrame([{
        "test_name": "test_tool",
        "tool": "my_tool",
        "status": "PASSED",
        "latency (ms)": 50,
        "errors": ""
    }])
    df_tool.to_csv(tool_file, index=False)

    callback_file = evals_dir / "callback_results.csv"
    df_callback = pd.DataFrame([{
        "test_name": "test_callback",
        "agent_name": "my_agent",
        "callback_type": "my_callback",
        "status": "PASSED",
        "error_message": ""
    }])
    df_callback.to_csv(callback_file, index=False)

    class Args:
        def __init__(self):
            self.evals_dir = str(evals_dir)
            self.output = None
            self.golden_run = None
            self.app_name = None
            self.run = False
            self.app_dir = None
            self.tool_test_file = None
            self.golden_file = None
            self.simulation_dir = None
            self.format = "html"
            self.include = "sims,goldens,scenarios"

    args = Args()

    with patch(
        "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
    ) as mock_report:
        combined_evals_report_cmd(args)

        mock_report.assert_called_once_with(
            evals_dir=str(evals_dir),
            golden_run=None,
            app_name=None,
            output_path=str(evals_dir / "combined_report.html"),
            run=False,
            app_dir=None,
            tool_test_file=None,
            golden_file=None,
            simulation_dir=None,
            format="html",
            include=["sims", "goldens", "scenarios"]
        )
