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

"""Core Changelogs class for CXAS Scrapi."""

from typing import List

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.agents import Agents


class Changelogs(Agents):
    """Core Class for managing Changelog Resources."""

    def __init__(self, app_name: str, **kwargs):
        """Initializes the Changelogs client.

        Args:
            app_name: The full resource name of the parent App
                (projects/PROJECT_ID/locations/LOCATION/apps/APP_ID).
        """
        # We inherit from Agents because it holds the AgentServiceClient
        # which contains changelog methods
        super().__init__(app_name=app_name, **kwargs)
        self.app_name = app_name
        self.resource_type = "changelogs"

    def list_changelogs(self) -> List[types.Changelog]:
        """Lists changelogs within the app."""

        request = types.ListChangelogsRequest(parent=self.app_name)
        response = self.client.list_changelogs(request=request)
        return list(response)

    def get_changelog(self, changelog_id: str) -> types.Changelog:
        """Gets a specific changelog."""
        request = types.GetChangelogRequest(
            name=f"{self.app_name}/changelogs/{changelog_id}"
        )
        return self.client.get_changelog(request=request)
