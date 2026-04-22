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

from cxas_scrapi.core.apps import Apps


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_list_apps_mock(mock_client_cls):
    """Test Apps.list_apps using mocks."""
    mock_client = mock_client_cls.return_value

    mock_app = MagicMock()
    mock_app.display_name = "Test App"
    mock_app.name = "projects/p/locations/l/apps/test-app"

    # Mock list_apps response
    mock_client.list_apps.return_value = [mock_app]

    project_id = "mock-project"
    location = "us"

    apps_client = Apps(
        project_id=project_id, location=location, creds=MagicMock()
    )
    apps = apps_client.list_apps()

    assert len(apps) == 1
    assert apps[0].display_name == "Test App"
    print("PASS: Mock list_apps verified.")


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_get_apps_map(mock_client_cls):
    """Test Apps.get_apps_map using mocks."""
    mock_client = mock_client_cls.return_value

    mock_app1 = MagicMock()
    mock_app1.display_name = "Test App 1"
    mock_app1.name = "projects/p/locations/l/apps/test-app-1"

    mock_app2 = MagicMock()
    mock_app2.display_name = "Test App 2"
    mock_app2.name = "projects/p/locations/l/apps/test-app-2"

    # Mock list_apps response
    mock_client.list_apps.return_value = [mock_app1, mock_app2]

    project_id = "mock-project"
    location = "us"

    apps_client = Apps(
        project_id=project_id, location=location, creds=MagicMock()
    )
    apps_map = apps_client.get_apps_map()

    assert len(apps_map) == 2
    assert apps_map["projects/p/locations/l/apps/test-app-1"] == "Test App 1"
    assert apps_map["projects/p/locations/l/apps/test-app-2"] == "Test App 2"

    apps_map_reverse = apps_client.get_apps_map(reverse=True)
    assert len(apps_map_reverse) == 2
    assert (
        apps_map_reverse["Test App 1"]
        == "projects/p/locations/l/apps/test-app-1"
    )
    assert (
        apps_map_reverse["Test App 2"]
        == "projects/p/locations/l/apps/test-app-2"
    )


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_import_app_validation(mock_client_cls):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )

    # Test valid source (using app_content) for import_app
    apps_client.import_app(
        app_name="projects/mock-project/locations/us/apps/test",
        app_content=b"dummycontent",
    )

    # Test valid source (using gcs_uri) for import_as_new_app
    apps_client.import_as_new_app(
        display_name="test", gcs_uri="gs://bucket/app.zip"
    )

    # Test invalid: providing multiple sources (import_app)
    with pytest.raises(ValueError, match="Exactly one of"):
        apps_client.import_app(
            app_name="projects/mock-project/locations/us/apps/test",
            app_content=b"content",
            gcs_uri="gs://foo/bar",
        )

    # Test invalid: providing no sources (import_as_new_app)
    with pytest.raises(ValueError, match="Exactly one of"):
        apps_client.import_as_new_app(display_name="test")


@patch("builtins.open")
@patch("cxas_scrapi.core.apps.types.ImportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_import_app_local_file(mock_client_cls, mock_import_app_req, mock_open):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )
    mock_open.return_value.__enter__.return_value.read.return_value = (
        b"local_file_content"
    )

    apps_client.import_app(
        app_name="projects/mock-project/locations/us/apps/test",
        local_path="/fake/path/app.zip",
    )
    mock_open.assert_called_once_with("/fake/path/app.zip", "rb")

    # Assert that ImportAppRequest was instantiated with the correct arguments
    kwargs = mock_import_app_req.call_args[1]
    assert kwargs.get("app_content") == b"local_file_content"
    assert "gcs_uri" not in kwargs
    assert kwargs.get("app_id") == "test"


@patch("cxas_scrapi.core.apps.types.ImportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
@patch(
    "google.cloud.ces_v1beta.types.ImportAppRequest.ImportOptions.ConflictResolutionStrategy"
)
def test_import_app_conflict_strategy(
    mock_strategy_enum, mock_client_cls, mock_import_app_req
):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )

    # Test valid strategy definition
    apps_client.import_app(
        app_name="projects/mock-project/locations/us/apps/test",
        app_content=b"content",
        conflict_strategy="REPLACE",
    )
    kwargs = mock_import_app_req.call_args[1]
    assert "import_options" in kwargs

    # Test invalid strategy
    with pytest.raises(
        ValueError, match="must be either 'REPLACE' or 'OVERWRITE'"
    ):
        apps_client.import_app(
            app_name="projects/mock-project/locations/us/apps/test",
            app_content=b"content",
            conflict_strategy="INVALID",
        )


@patch("cxas_scrapi.core.apps.types.ImportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_import_app_backward_compatibility(
    mock_client_cls, mock_import_app_req
):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )

    with patch(
        "cxas_scrapi.core.apps.types.ImportAppRequest.ImportOptions"
    ) as mock_import_options:
        apps_client.import_app(
            app_name="projects/mock-project/locations/us/apps/target-id",
            app_content=b"content",
        )

        kwargs = mock_import_app_req.call_args[1]
        assert kwargs.get("app_id") == "target-id"

        # We just assert it was called and populated with the Enum type
        # correctly
        options_kwargs = mock_import_options.call_args[1]
        assert "conflict_resolution_strategy" in options_kwargs


@patch("cxas_scrapi.core.apps.types.ImportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_import_as_new_app(mock_client_cls, mock_import_app_req):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )

    apps_client.import_as_new_app(
        display_name="New Test App", app_content=b"content"
    )

    kwargs = mock_import_app_req.call_args[1]
    assert kwargs.get("display_name") == "New Test App"
    assert "app_id" not in kwargs
    assert "import_options" not in kwargs


@patch("cxas_scrapi.core.apps.types.ExportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_export_app_standard(mock_client_cls, mock_export_app_req):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )
    mock_operation = MagicMock()
    apps_client.client.export_app.return_value = mock_operation

    result = apps_client.export_app(
        app_name="projects/mock-project/locations/us/apps/test-app",
        gcs_uri="gs://bucket/path",
    )

    kwargs = mock_export_app_req.call_args[1]
    assert (
        kwargs.get("name") == "projects/mock-project/locations/us/apps/test-app"
    )
    assert kwargs.get("gcs_uri") == "gs://bucket/path"
    assert result == mock_operation


@patch("builtins.open")
@patch("cxas_scrapi.core.apps.types.ExportAppRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_export_app_local_path(mock_client_cls, mock_export_app_req, mock_open):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )
    mock_operation = MagicMock()
    mock_response = MagicMock()
    mock_response.app_content = b"exported_content"
    mock_operation.result.return_value = mock_response
    apps_client.client.export_app.return_value = mock_operation

    result = apps_client.export_app(
        app_name="projects/mock-project/locations/us/apps/test-app",
        local_path="/fake/path.zip",
    )

    kwargs = mock_export_app_req.call_args[1]
    assert (
        kwargs.get("name") == "projects/mock-project/locations/us/apps/test-app"
    )
    assert kwargs.get("gcs_uri") is None

    mock_open.assert_called_once_with("/fake/path.zip", "wb")
    mock_open.return_value.__enter__.return_value.write.assert_called_once_with(
        b"exported_content"
    )
    assert result == mock_response


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_export_app_validation(mock_client_cls):
    apps_client = Apps(
        project_id="mock-project", location="us", creds=MagicMock()
    )

    with pytest.raises(
        ValueError,
        match="Only one of 'gcs_uri' or 'local_path' can be provided",
    ):
        apps_client.export_app(
            app_name="projects/mock-project/locations/us/apps/test-app",
            gcs_uri="gs://bucket/path",
            local_path="/fake/path.zip",
        )
