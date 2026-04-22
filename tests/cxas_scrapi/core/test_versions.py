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

from cxas_scrapi.core.versions import Versions


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_list_versions(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_ver = MagicMock()
    mock_ver.name = "projects/p/locations/l/apps/A/versions/v1"
    mock_client.list_app_versions.return_value = [mock_ver]

    v = Versions("projects/p/locations/l/apps/A")
    res = v.list_versions()
    assert len(res) == 1
    assert res[0].name == "projects/p/locations/l/apps/A/versions/v1"
    mock_client.list_app_versions.assert_called_once()


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_get_versions_map(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_v1 = MagicMock()
    mock_v1.name = "projects/p/locations/l/apps/A/versions/v1"
    mock_v1.display_name = "n1"
    mock_v2 = MagicMock()
    mock_v2.name = "projects/p/locations/l/apps/A/versions/v2"
    mock_v2.display_name = "n2"
    mock_client.list_app_versions.return_value = [mock_v1, mock_v2]

    v = Versions("projects/p/locations/l/apps/A")
    res = v.get_versions_map()
    assert res["projects/p/locations/l/apps/A/versions/v1"] == "n1"
    assert res["projects/p/locations/l/apps/A/versions/v2"] == "n2"

    res_rev = v.get_versions_map(reverse=True)
    assert res_rev["n1"] == "projects/p/locations/l/apps/A/versions/v1"
    assert res_rev["n2"] == "projects/p/locations/l/apps/A/versions/v2"


@patch("cxas_scrapi.core.versions.types.GetAppVersionRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_get_version(mock_client_cls, mock_req_cls):
    mock_client = mock_client_cls.return_value
    mock_v = MagicMock()
    mock_v.name = "projects/p/locations/l/apps/A/versions/v1"
    mock_client.get_app_version.return_value = mock_v

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect

    v = Versions("projects/p/locations/l/apps/A")
    res = v.get_version("v1")
    assert res.name == "projects/p/locations/l/apps/A/versions/v1"
    mock_client.get_app_version.assert_called_once()
    assert (
        mock_client.get_app_version.call_args[1]["request"].name
        == "projects/p/locations/l/apps/A/versions/v1"
    )


@patch("cxas_scrapi.core.versions.types.DeleteAppVersionRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_delete_version(mock_client_cls, mock_req_cls):
    mock_client = mock_client_cls.return_value

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect

    v = Versions("projects/p/locations/l/apps/A")
    v.delete_version("v_id")
    mock_client.delete_app_version.assert_called_once()
    args = mock_client.delete_app_version.call_args[1]["request"]
    assert args.name == "projects/p/locations/l/apps/A/versions/v_id"


@patch("cxas_scrapi.core.versions.types.RestoreAppVersionRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_revert_version(mock_client_cls, mock_req_cls):
    mock_client = mock_client_cls.return_value
    mock_client.restore_app_version.return_value = MagicMock()

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect

    v = Versions("projects/p/locations/l/apps/A")
    v.revert_version("v_id")
    mock_client.restore_app_version.assert_called_once()
    args = mock_client.restore_app_version.call_args[1]["request"]
    assert args.name == "projects/p/locations/l/apps/A/versions/v_id"
