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

"""Unit tests for :mod:`cxas_scrapi.cli.utils`."""

import argparse
from typing import Any
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.cli.utils import LazyCallable, cmd_help


def test_lazy_callable_invokes_target(mocker: Any) -> None:
    """Verifies LazyCallable defers module import and forwards arguments.

    Args:
        mocker: Pytest mock fixture.
    """
    mock_func = MagicMock(return_value="success")
    mock_module = MagicMock()
    setattr(mock_module, "my_func", mock_func)

    mocker.patch("importlib.import_module", return_value=mock_module)

    lazy = LazyCallable("cxas_scrapi.cli.dummy", "my_func")
    res = lazy("arg1", key="val")

    assert res == "success"
    mock_func.assert_called_once_with("arg1", key="val")
    # Subsequent calls reuse cached function
    lazy("arg2")
    assert mock_func.call_count == 2


def test_cmd_help_with_subcommand(capsys: Any) -> None:
    """Verifies cmd_help handles specific subcommand help requests.

    Args:
        capsys: Pytest stdout capture fixture.
    """
    args = argparse.Namespace(help_command="lint")
    cmd_help(args)
    captured = capsys.readouterr()
    assert "Usage: cxas" in captured.out or "lint" in captured.out


def test_cmd_help_default(capsys: Any) -> None:
    """Verifies cmd_help prints general root help when no subcommand is specified.

    Args:
        capsys: Pytest stdout capture fixture.
    """
    args = argparse.Namespace(help_command=None)
    cmd_help(args)
    captured = capsys.readouterr()
    assert "Usage:" in captured.out or "cxas" in captured.out
