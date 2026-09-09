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

import typing
from typing import Any

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common


class Versions(Apps):
    """Core Class for managing AppVersion Resources."""

    def __init__(
        self,
        app_name: str,
        creds_path: str | None = None,
        creds_dict: dict[str, str] | None = None,
        creds: Any = None,
        scope: list[str] | None = None,
        **kwargs: typing.Any,
    ) -> None:
        """Initializes the Versions client."""
        project_id = Common._get_project_id(app_name)
        location = Common._get_location(app_name)
        if not project_id or not location:
            raise ValueError(
                f"Invalid app_name format: {app_name}. "
                "Expected format: "
                "projects/<project>/locations/<location>/apps/<app>"
            )

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

    def list_versions(self) -> list[types.AppVersion]:
        """Lists versions within the app."""
        request = types.ListAppVersionsRequest(parent=self.app_name)
        response = self.client.list_app_versions(request=request)
        return list(response)

    def get_versions_map(self, reverse: bool = False) -> dict[str, str]:
        """Returns a map of version display names to full resource names.

        Args:
            reverse: If True, map display_name -> name.
        """
        versions = self.list_versions()
        versions_map: dict[str, str] = {}

        for version in versions:
            display_name = version.display_name
            name = version.name
            if display_name and name:
                if reverse:
                    versions_map[display_name] = name
                else:
                    versions_map[name] = display_name

        return versions_map

    def create_version(
        self, display_name: str = "", description: str = ""
    ) -> types.AppVersion:
        """Creates a new version of the app."""
        app_version = types.AppVersion(
            display_name=display_name, description=description
        )
        request = types.CreateAppVersionRequest(
            parent=self.app_name, app_version=app_version
        )
        # Assuming generated client supports create_app_version natively
        return self.client.create_app_version(request=request)

    def resolve_version_name(self, version_identifier: str) -> str:
        """Resolves a version identifier to a canonical resource name.

        Accepts a full resource name, bare version ID, or display name.

        Args:
            version_identifier: Full resource name, bare version ID, or
                display name.

        Returns:
            The fully-qualified version resource name.

        Raises:
            ValueError: If the version cannot be found, is ambiguous (multiple
                versions share the same display name), or the identifier is
                empty.
        """
        if not version_identifier:
            raise ValueError("Version identifier must not be empty.")

        versions = self.list_versions()
        if not versions:
            raise ValueError(f"No versions found for app '{self.app_name}'.")

        # 1. Match full resource name (e.g. projects/.../versions/001)
        for v in versions:
            if v.name == version_identifier:
                return v.name

        # 2. Match version ID (the last segment of the resource name)
        for v in versions:
            v_id = v.name.split("/")[-1] if v.name else ""
            if v_id == version_identifier:
                return v.name

        # 3. Match display name (e.g. "v1.0"). Because display names are not
        # guaranteed to be unique, reject ambiguous matches.
        matching_by_display = [
            v for v in versions if v.display_name == version_identifier
        ]
        if len(matching_by_display) > 1:
            matching_ids = [
                v.name.split("/")[-1] for v in matching_by_display if v.name
            ]
            ids_str = ", ".join(f"'{m_id}'" for m_id in matching_ids)
            raise ValueError(
                f"Multiple versions found with display name "
                f"'{version_identifier}': {ids_str}. "
                "Please specify the exact version ID or full resource name "
                "instead to avoid ambiguity."
            )
        if len(matching_by_display) == 1:
            return matching_by_display[0].name

        available = [
            f"'{v.name.split('/')[-1]}' ({v.display_name})"
            if v.display_name
            else f"'{v.name.split('/')[-1]}'"
            for v in versions
        ]
        avail_str = ", ".join(available)
        raise ValueError(
            f"Version '{version_identifier}' not found for app "
            f"'{self.app_name}'. Available versions: {avail_str}"
        )

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
