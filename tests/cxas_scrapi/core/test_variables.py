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

from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.variables import Variables


@patch("cxas_scrapi.core.variables.Variables.get_app")
def test_list_variables(mock_get_app):
    mock_app = MagicMock()
    mock_app.variable_declarations = ["var1", "var2"]
    mock_get_app.return_value = mock_app

    v = Variables("projects/p/locations/l/apps/A")
    res = v.list_variables()
    assert res == ["var1", "var2"]
    mock_get_app.assert_called_once_with("projects/p/locations/l/apps/A")


@patch("cxas_scrapi.core.variables.Variables.get_app")
def test_get_variable(mock_get_app):
    mock_app = MagicMock()
    v1 = MagicMock(name="v1")
    v1.name = "my_var"
    mock_app.variable_declarations = [v1]
    mock_get_app.return_value = mock_app

    v = Variables("projects/p/locations/l/apps/A")
    res = v.get_variable("my_var")
    assert res == v1

    res2 = v.get_variable("unknown")
    assert res2 is None


@patch("cxas_scrapi.core.variables.types.App.VariableDeclaration")
@patch("cxas_scrapi.core.variables.Variables.update_app")
@patch("cxas_scrapi.core.variables.Variables.get_app")
def test_create_variable(mock_get_app, mock_update_app, mock_vd):
    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_vd.side_effect = side_effect

    mock_app = MagicMock()
    mock_app.variable_declarations = []
    mock_get_app.return_value = mock_app

    v = Variables("projects/p/locations/l/apps/A")
    v.create_variable("my_var", "STRING", "val")

    mock_update_app.assert_called_once()
    args = mock_update_app.call_args[1]
    assert "variable_declarations" in args
    assert len(args["variable_declarations"]) == 1
    new_var = args["variable_declarations"][0]
    assert new_var.name == "my_var"


@patch("cxas_scrapi.core.variables.types.App.VariableDeclaration")
@patch("cxas_scrapi.core.variables.Variables.update_app")
@patch("cxas_scrapi.core.variables.Variables.get_app")
def test_update_variable(mock_get_app, mock_update_app, mock_vd):
    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_vd.side_effect = side_effect

    mock_app = MagicMock()
    v1 = MagicMock()
    v1.name = "my_var"
    v1.schema = MagicMock()
    mock_app.variable_declarations = [v1]
    mock_get_app.return_value = mock_app

    v = Variables("projects/p/locations/l/apps/A")
    v.update_variable("my_var", "INTEGER", 5)

    mock_update_app.assert_called_once()
    args = mock_update_app.call_args[1]
    assert "variable_declarations" in args
    assert len(args["variable_declarations"]) == 1
    assert args["variable_declarations"][0].schema.default == 5


@patch("cxas_scrapi.core.variables.Variables.update_app")
@patch("cxas_scrapi.core.variables.Variables.get_app")
def test_delete_variable(mock_get_app, mock_update_app):
    mock_app = MagicMock()
    v1 = MagicMock()
    v1.name = "rem_var"
    v2 = MagicMock()
    v2.name = "keep_var"
    mock_app.variable_declarations = [v1, v2]
    mock_get_app.return_value = mock_app

    v = Variables("projects/p/locations/l/apps/A")
    v.delete_variable("rem_var")

    mock_update_app.assert_called_once()
    args = mock_update_app.call_args[1]
    assert "variable_declarations" in args
    assert len(args["variable_declarations"]) == 1
    assert args["variable_declarations"][0].name == "keep_var"


def test_check_schema_type():
    with pytest.raises(ValueError):
        Variables("projects/p/locations/l/apps/A")._check_schema_type("INVALID")
    Variables("projects/p/locations/l/apps/A")._check_schema_type(
        "STRING"
    )  # Should pass


def test_create_variable_already_exists():
    with (
        patch(
            "cxas_scrapi.core.variables.Variables.update_app"
        ) as mock_update_app,
        patch("cxas_scrapi.core.variables.Variables.get_app") as mock_get_app,
    ):
        mock_app = MagicMock()
        v1 = MagicMock()
        v1.name = "my_var"
        mock_app.variable_declarations = [v1]
        mock_get_app.return_value = mock_app

        v = Variables("projects/p/locations/l/apps/A")
        v.create_variable("my_var", "STRING", "val")

        mock_update_app.assert_not_called()


def test_delete_variable_not_found():
    with (
        patch(
            "cxas_scrapi.core.variables.Variables.update_app"
        ) as mock_update_app,
        patch("cxas_scrapi.core.variables.Variables.get_app") as mock_get_app,
    ):
        mock_app = MagicMock()
        mock_app.variable_declarations = []
        mock_get_app.return_value = mock_app

        v = Variables("projects/p/locations/l/apps/A")
        v.delete_variable("unknown_var")

        mock_update_app.assert_not_called()
