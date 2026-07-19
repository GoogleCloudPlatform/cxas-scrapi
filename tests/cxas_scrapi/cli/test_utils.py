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

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from click.testing import CliRunner

from cxas_scrapi.cli.main import cli
from cxas_scrapi.cli.utils import LazyCallable, to_dataclass


def test_lazy_callable_invokes_target(mocker: Any) -> None:
    """Verifies LazyCallable defers module import and forwards arguments.

    Args:
        mocker: Pytest mock fixture.
    """
    mock_func = MagicMock(return_value="success")
    mock_module = MagicMock()
    mock_module.my_func = mock_func

    mocker.patch("importlib.import_module", return_value=mock_module)

    lazy = LazyCallable("cxas_scrapi.cli.dummy", "my_func")
    res = lazy("arg1", key="val")

    assert res == "success"
    mock_func.assert_called_once_with("arg1", key="val")
    lazy("arg2")
    assert mock_func.call_count == 2


def test_lazy_callable_getattr_and_dir(mocker: Any) -> None:
    """Verifies LazyCallable handles staticmethods, class attributes, and __dir__.

    Args:
        mocker: Pytest mock fixture.
    """

    class DummyClass:
        attr = "val"

        @staticmethod
        def static_method() -> str:
            return "static"

    mock_module = MagicMock()
    mock_module.DummyClass = DummyClass

    mocker.patch("importlib.import_module", return_value=mock_module)

    lazy = LazyCallable("cxas_scrapi.cli.dummy", "DummyClass")
    assert lazy.attr == "val"
    assert lazy.static_method() == "static"
    assert "static_method" in dir(lazy)


def test_help_cmd_with_subcommand() -> None:
    """Verifies help_cmd handles specific subcommand help requests."""
    runner = CliRunner()
    result = runner.invoke(cli, ["help", "lint"])
    assert result.exit_code == 0
    assert "usage:" in result.output.lower()


def test_help_cmd_default() -> None:
    """Verifies help_cmd prints general root help when no subcommand is specified."""
    runner = CliRunner()
    result = runner.invoke(cli, ["help"])
    assert result.exit_code == 0
    assert "usage:" in result.output.lower()


@dataclass(frozen=False)
class DummyConfig:
    foo: str = ""
    items: list[str] | None = None


def test_to_dataclass() -> None:
    """Verifies to_dataclass converts dictionaries or namespaces into dataclasses cleanly."""
    obj = to_dataclass(DummyConfig, None, foo="bar", items=("a", "b"))
    assert obj.foo == "bar"
    assert obj.items == ["a", "b"]
