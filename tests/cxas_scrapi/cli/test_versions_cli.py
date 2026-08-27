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

"""Unit tests for cxas_scrapi CLI versions subcommands."""

import argparse
import json
import typing
from unittest.mock import MagicMock, patch
import pytest

from cxas_scrapi.cli.versions_cli import (
    app_versions_create,
    app_versions_list,
)


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_create_success(
    mock_resolve: typing.Any,
    mock_versions_cls: typing.Any,
    capsys: typing.Any,
) -> None:
    mock_apps = MagicMock()
    mock_apps.creds = "fake_creds"
    mock_resolve.return_value = (
        mock_apps,
        "projects/test-project/locations/global/apps/test-app",
        "test-app",
    )

    mock_version = MagicMock()
    mock_version.name = (
        "projects/test-project/locations/global/apps/test-app/versions/v123"
    )
    mock_version.display_name = "v1.0.0"
    mock_version.description = "Test version description"
    mock_version.create_time = "2026-08-27T10:00:00Z"

    mock_v_inst = mock_versions_cls.return_value
    mock_v_inst.create_version.return_value = mock_version

    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        display_name="v1.0.0",
        description="Test version description",
        json=False,
    )

    app_versions_create(args)

    mock_versions_cls.assert_called_once_with(
        app_name="projects/test-project/locations/global/apps/test-app",
        creds="fake_creds",
    )
    mock_v_inst.create_version.assert_called_once_with(
        display_name="v1.0.0",
        description="Test version description",
    )


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_create_default_name(
    mock_resolve: typing.Any,
    mock_versions_cls: typing.Any,
) -> None:
    mock_apps = MagicMock()
    mock_apps.creds = None
    mock_resolve.return_value = (
        mock_apps,
        "projects/test-project/locations/global/apps/test-app",
        "test-app",
    )

    mock_version = MagicMock()
    mock_version.name = (
        "projects/test-project/locations/global/apps/test-app/versions/v123"
    )
    mock_version.display_name = "version-20260827-100000"
    mock_version.description = ""
    mock_version.create_time = "2026-08-27T10:00:00Z"

    mock_v_inst = mock_versions_cls.return_value
    mock_v_inst.create_version.return_value = mock_version

    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        display_name=None,
        description=None,
        json=False,
    )

    app_versions_create(args)

    assert mock_v_inst.create_version.call_count == 1
    call_kwargs = mock_v_inst.create_version.call_args[1]
    assert call_kwargs["display_name"].startswith("version-")
    assert call_kwargs["description"] == ""


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_create_json(
    mock_resolve: typing.Any,
    mock_versions_cls: typing.Any,
    capsys: typing.Any,
) -> None:
    mock_apps = MagicMock()
    mock_apps.creds = None
    mock_resolve.return_value = (
        mock_apps,
        "projects/test-project/locations/global/apps/test-app",
        "test-app",
    )

    mock_version = {
        "name": "projects/test-project/locations/global/apps/test-app/versions/v123",
        "display_name": "v1.0.0",
        "description": "JSON test",
        "create_time": "2026-08-27T10:00:00Z",
    }

    mock_v_inst = mock_versions_cls.return_value
    mock_v_inst.create_version.return_value = mock_version

    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        display_name="v1.0.0",
        description="JSON test",
        json=True,
    )

    app_versions_create(args)

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["name"] == mock_version["name"]
    assert data["version_id"] == "v123"
    assert data["display_name"] == "v1.0.0"
    assert data["description"] == "JSON test"


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_create_failure(
    mock_resolve: typing.Any,
    mock_versions_cls: typing.Any,
) -> None:
    mock_apps = MagicMock()
    mock_apps.creds = None
    mock_resolve.return_value = (
        mock_apps,
        "projects/test-project/locations/global/apps/test-app",
        "test-app",
    )

    mock_v_inst = mock_versions_cls.return_value
    mock_v_inst.create_version.side_effect = RuntimeError("API Error")

    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
        display_name="v1.0.0",
        description="Error test",
        json=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        app_versions_create(args)
    assert exc_info.value.code == 1


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_list_success(
    mock_resolve: typing.Any,
    mock_versions_cls: typing.Any,
) -> None:
    mock_apps = MagicMock()
    mock_resolve.return_value = (
        mock_apps,
        "projects/test-project/locations/global/apps/test-app",
        "test-app",
    )

    mock_v = MagicMock()
    mock_v.name = "projects/test-project/locations/global/apps/test-app/versions/v1"
    mock_v.display_name = "v1"
    mock_v.description = "desc"
    mock_v.create_time = "2026-08-27T10:00:00Z"
    mock_v.creator = "user@example.com"

    mock_v_inst = mock_versions_cls.return_value
    mock_v_inst.list_versions.return_value = [mock_v]

    args = argparse.Namespace(
        app_name="projects/test-project/locations/global/apps/test-app",
    )

    app_versions_list(args)
    mock_v_inst.list_versions.assert_called_once()
