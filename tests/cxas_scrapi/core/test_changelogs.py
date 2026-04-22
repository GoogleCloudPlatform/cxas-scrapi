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

from cxas_scrapi.core.changelogs import Changelogs


@patch("cxas_scrapi.core.agents.AgentServiceClient")
def test_list_changelogs(mock_client_cls):
    """Test Changelogs.list_changelogs."""
    mock_client = mock_client_cls.return_value

    mock_cl = MagicMock()
    mock_cl.name = "projects/p/locations/l/apps/a/changelogs/123"
    mock_client.list_changelogs.return_value = [mock_cl]

    cl_client = Changelogs(app_name="projects/p/locations/l/apps/a")
    res = cl_client.list_changelogs()

    assert len(res) == 1
    assert res[0].name == "projects/p/locations/l/apps/a/changelogs/123"
    mock_client.list_changelogs.assert_called_once()


@patch("cxas_scrapi.core.agents.AgentServiceClient")
def test_get_changelog(mock_client_cls):
    """Test Changelogs.get_changelog."""
    mock_client = mock_client_cls.return_value
    mock_cl = MagicMock()
    mock_cl.name = "projects/p/locations/l/apps/a/changelogs/c1"
    mock_client.get_changelog.return_value = mock_cl

    cl_client = Changelogs(app_name="projects/p/locations/l/apps/a")
    res = cl_client.get_changelog("c1")

    assert res.name == "projects/p/locations/l/apps/a/changelogs/c1"
    mock_client.get_changelog.assert_called_once()
