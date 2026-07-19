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

"""Track B integration tests for Click CLI entrypoints."""

from click.testing import CliRunner

from cxas_scrapi.cli.main import cli


def test_click_root_help() -> None:
    """Verifies root --help outputs usage text and registered subcommands."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Google Customer Engagement Suite" in result.output
    assert "app" in result.output
    assert "evals" in result.output
    assert "deployments" in result.output


def test_click_lint_help() -> None:
    """Verifies lint subcommand --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["lint", "--help"])
    assert result.exit_code == 0
    assert "--app-dir" in result.output


def test_click_trace_search_help() -> None:
    """Verifies trace search subcommand --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["trace", "search", "--help"])
    assert result.exit_code == 0
    assert "--app-name" in result.output
    assert "--match" in result.output
