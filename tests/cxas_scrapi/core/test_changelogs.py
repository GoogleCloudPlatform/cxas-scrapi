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

import typing
from unittest.mock import MagicMock, patch

from cxas_scrapi.core.changelogs import Changelogs


@patch("cxas_scrapi.core.agents.AgentServiceClient")
def test_list_changelogs(mock_client_cls: typing.Any) -> None:
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
def test_get_changelog(mock_client_cls: typing.Any) -> None:
    """Test Changelogs.get_changelog."""
    mock_client = mock_client_cls.return_value
    mock_cl = MagicMock()
    mock_cl.name = "projects/p/locations/l/apps/a/changelogs/c1"
    mock_client.get_changelog.return_value = mock_cl

    cl_client = Changelogs(app_name="projects/p/locations/l/apps/a")
    res = cl_client.get_changelog("c1")

    assert res.name == "projects/p/locations/l/apps/a/changelogs/c1"
    mock_client.get_changelog.assert_called_once()


def test_summarize_changelogs_custom_vertex_location() -> None:
    """Test ChangelogUtils.summarize_changelogs with vertex_location."""
    from cxas_scrapi.utils.changelog_utils import ChangelogUtils

    changelogs = [
        {
            "action": "CREATE",
            "resourceType": "Tool",
            "name": "tool_1",
            "description": "tool desc",
        }
    ]
    with patch("cxas_scrapi.utils.changelog_utils.GeminiGenerate") as mock_gem:
        mock_gen_inst = mock_gem.return_value
        mock_gen_inst.generate.return_value = "- Created tool 'tool_1'"
        res = ChangelogUtils.summarize_changelogs(
            vertex_client_or_project=None,
            changelogs=changelogs,
            project_id="my-proj",
            vertex_location="europe-west4",
        )
        assert "- Created tool 'tool_1'" in res
        mock_gem.assert_called_once_with(
            project_id="my-proj",
            location="europe-west4",
            model_name="gemini-2.5-flash",
        )
