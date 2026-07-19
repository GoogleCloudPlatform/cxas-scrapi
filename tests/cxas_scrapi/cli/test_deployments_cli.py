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

"""Unit tests for :mod:`cxas_scrapi.cli.main`."""

import argparse
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import NotFound

from cxas_scrapi.cli.main import (
    deployments_create,
    deployments_list,
    deployments_promote,
)


@pytest.fixture(autouse=True)
def mock_deployments_sdk(mocker: Any) -> MagicMock:
    """Fixture to mock Deployments SDK client across both module references."""
    mock_client = MagicMock()
    mocker.patch(
        "cxas_scrapi.core.deployments.Deployments", return_value=mock_client
    )
    return mock_client


def test_deployments_list_success(
    mock_deployments_sdk: MagicMock, mocker: Any, capsys: Any
) -> None:
    """Verifies listing deployments outputs JSON array formatted results.

    Args:
        mock_deployments_sdk: Mocked Deployments SDK client fixture.
        mocker: Pytest mock fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_deployments_sdk.list_deployments.return_value = [
        {
            "name": "projects/p/locations/l/apps/a/deployments/d1",
            "displayName": "Dep 1",
        }
    ]
    mocker.patch(
        "google.protobuf.json_format.MessageToDict", side_effect=lambda x: x
    )

    args = argparse.Namespace(app_name="projects/p/locations/l/apps/a")
    deployments_list(args)

    captured = capsys.readouterr()
    assert (
        "Listing deployments for App: projects/p/locations/l/apps/a"
        in captured.out
    )
    assert "Dep 1" in captured.out


def test_deployments_create_traffic_split_parsing(
    mock_deployments_sdk: MagicMock, capsys: Any
) -> None:
    """Verifies valid traffic-split parsing options.

    Args:
        mock_deployments_sdk: Mocked Deployments SDK client fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_deployments_sdk.create_deployment.return_value = MagicMock(
        name="dep-1"
    )

    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        deployment_id="d1",
        display_name="Dep 1",
        version="v1",
        channel_type="API",
        traffic_split="v1:80,v2:20",
    )
    deployments_create(args)
    assert mock_deployments_sdk.create_deployment.call_count == 1
    call_kwargs = mock_deployments_sdk.create_deployment.call_args.kwargs
    assert call_kwargs["deployment_id"] == "d1"
    assert call_kwargs["traffic_split"] == {"v1": 80, "v2": 20}


def test_deployments_create_invalid_traffic_split(capsys: Any) -> None:
    """Verifies invalid traffic-split string triggers SystemExit.

    Args:
        capsys: Pytest stdout capture fixture.
    """
    args_invalid = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        deployment_id="d1",
        version="v1",
        traffic_split="invalid-split-format",
    )
    with pytest.raises(SystemExit) as excinfo:
        deployments_create(args_invalid)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error parsing traffic-split" in captured.out


def test_deployments_create_missing_version_and_split(capsys: Any) -> None:
    """Verifies error exit when neither version nor traffic-split is provided.

    Args:
        capsys: Pytest stdout capture fixture.
    """
    args = argparse.Namespace(
        app_name="projects/p/locations/l/apps/a",
        deployment_id="d1",
        version=None,
        traffic_split=None,
    )
    with pytest.raises(SystemExit) as excinfo:
        deployments_create(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: You must provide either `--version`" in captured.out


def test_deployments_promote_not_found_error(
    mock_deployments_sdk: MagicMock, capsys: Any
) -> None:
    """Verifies error handling when promoting a non-existent deployment ID.

    Args:
        mock_deployments_sdk: Mocked Deployments SDK client fixture.
        capsys: Pytest stdout capture fixture.
    """
    mock_deployments_sdk.update_deployment.side_effect = NotFound(
        "Deployment d1 not found"
    )

    args = argparse.Namespace(
        deployment_id="d1",
        version="v1",
        traffic_split=None,
        app_name="projects/p/locations/l/apps/a",
    )

    with pytest.raises(SystemExit) as excinfo:
        deployments_promote(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error updating deployment" in captured.out


def test_deployments_promote_traffic_split_update(
    mock_deployments_sdk: MagicMock, capsys: Any
) -> None:
    """Verifies updating traffic split on an existing deployment ID.

    Args:
        mock_deployments_sdk: Mocked Deployments SDK client fixture.
        capsys: Pytest stdout capture fixture.
    """
    args = argparse.Namespace(
        deployment_id="d1",
        version="v2",
        traffic_split="v1:50,v2:50",
        app_name="projects/p/locations/l/apps/a",
    )
    deployments_promote(args)

    assert mock_deployments_sdk.update_deployment.call_count == 1
    call_kwargs = mock_deployments_sdk.update_deployment.call_args.kwargs
    assert call_kwargs["deployment_id"] == "d1"
    assert call_kwargs["app_version"] == "v2"
    assert call_kwargs["traffic_split"] == {"v1": 50, "v2": 50}
    captured = capsys.readouterr()
    assert "Successfully updated deployment traffic." in captured.out
