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

"""Core Versions class for CXAS Scrapi."""

from typing import Any, Dict, List

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.apps import Apps


class Versions(Apps):
    """Core Class for managing AppVersion Resources."""

    def __init__(
        self,
        app_name: str,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        """Initializes the Versions client."""
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]

        super().__init__(
            project_id=project_id,
            location=location,
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )
        self.resource_type = "versions"
        self.app_name = app_name

    def list_versions(self) -> List[types.AppVersion]:
        """Lists versions within the app."""
        request = types.ListAppVersionsRequest(parent=self.app_name)
        response = self.client.list_app_versions(request=request)
        return list(response)

    def get_versions_map(self, reverse: bool = False) -> Dict[str, str]:
        """Returns a map of version display names to full resource names.

        Args:
            reverse: If True, map display_name -> name.
        """
        versions = self.list_versions()
        versions_map: Dict[str, str] = {}

        for version in versions:
            display_name = version.display_name
            name = version.name
            if display_name and name:
                if reverse:
                    versions_map[display_name] = name
                else:
                    versions_map[name] = display_name

        return versions_map

    def get_version(self, version_id: str) -> types.AppVersion:
        """Gets a specific version."""
        request = types.GetAppVersionRequest(
            name=f"{self.app_name}/versions/{version_id}"
        )
        return self.client.get_app_version(request=request)

    def delete_version(self, version_id: str) -> None:
        """Deletes a specific version."""
        request = types.DeleteAppVersionRequest(
            name=f"{self.app_name}/versions/{version_id}"
        )
        self.client.delete_app_version(request=request)

    def revert_version(self, version_id: str) -> Any:
        """Reverts (Restores) a specific version."""
        request = types.RestoreAppVersionRequest(
            name=f"{self.app_name}/versions/{version_id}"
        )
        return self.client.restore_app_version(request=request)
