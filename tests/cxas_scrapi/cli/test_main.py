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

"""Tests for the main CLI entry point."""

import subprocess

import pytest

from cxas_scrapi.cli.main import get_parser


def test_get_parser():
    """Test that the parser can be initialized and parses help correctly."""
    parser = get_parser()
    assert parser is not None

    # Test parsing a simple command to verify the parser structure
    args = parser.parse_args(
        ["apps", "list", "--project-id", "test-project", "--location", "us"]
    )
    assert args.command == "apps"
    assert args.project_id == "test-project"
    assert args.location == "us"


def test_cli_installed_help():
    """Test that the 'cxas' command is installed and executable (verifies
    setup.py)."""
    # This tests the installation of the wheel we just built and installed.
    # When running tests via 'conda run -n cxas-scrapi pytest', 'cxas'
    # should be in the PATH.
    try:
        result = subprocess.run(
            ["cxas", "--help"], capture_output=True, text=True, check=True
        )
        assert result.returncode == 0
        assert "usage: cxas" in result.stdout
    except FileNotFoundError:
        pytest.fail(
            "The 'cxas' command was not found in the environment. "
            "Is it installed?"
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"'cxas --help' failed with return code {e.returncode}. "
            f"Output: {e.output}"
        )
