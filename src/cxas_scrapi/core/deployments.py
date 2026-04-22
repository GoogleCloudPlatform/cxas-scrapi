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

"""Core Deployments class for CXAS Scrapi."""

from typing import Any, Dict, List

from google.cloud.ces_v1beta import types
from google.protobuf import field_mask_pb2

from cxas_scrapi.core.apps import Apps


class Deployments(Apps):
    """Core Class for managing Deployment Resources."""

    def __init__(
        self,
        app_name: str,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        """Initializes the Deployments client."""
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
        self.resource_type = "deployments"
        self.app_name = app_name

    def list_deployments(self) -> List[types.Deployment]:
        """Lists deployments within a specific app."""
        request = types.ListDeploymentsRequest(parent=self.app_name)
        response = self.client.list_deployments(request=request)
        return list(response)

    def get_deployments_map(self, reverse: bool = False) -> Dict[str, str]:
        """Creates a map of Deployment full names to display names.

        Args:
            reverse: If True, map display_name -> name.
        """
        deployments = self.list_deployments()
        deployments_dict: Dict[str, str] = {}

        for deployment in deployments:
            display_name = deployment.display_name
            name = deployment.name
            if display_name and name:
                if reverse:
                    deployments_dict[display_name] = name
                else:
                    deployments_dict[name] = display_name
        return deployments_dict

    def get_deployment(self, deployment_id: str) -> types.Deployment:
        """Gets a specific deployment."""
        request = types.GetDeploymentRequest(
            name=f"{self.app_name}/deployments/{deployment_id}"
        )
        return self.client.get_deployment(request=request)

    def create_deployment(
        self,
        deployment_id: str,
        display_name: str,
        app_version: str,
        channel_profile: str = "WEB_AND_MOBILE",
    ) -> types.Deployment:
        """Creates a new deployment."""
        deployment = types.Deployment(
            display_name=display_name, app_version=app_version
        )

        # Optionally set channel profile if we want to be explicit
        if channel_profile:
            deployment.channel_profile.channel_type = getattr(
                types.common.ChannelProfile.ChannelType, channel_profile.upper()
            )

        request = types.CreateDeploymentRequest(
            parent=self.app_name,
            deployment_id=deployment_id,
            deployment=deployment,
        )
        return self.client.create_deployment(request=request)

    def update_deployment(
        self, deployment_id: str, **kwargs
    ) -> types.Deployment:
        """Updates specific fields of an existing Deployment."""
        deployment = types.Deployment(
            name=f"{self.app_name}/deployments/{deployment_id}"
        )
        mask_paths = []

        for key, value in kwargs.items():
            setattr(deployment, key, value)
            mask_paths.append(key)

        request = types.UpdateDeploymentRequest(
            deployment=deployment,
            update_mask=field_mask_pb2.FieldMask(paths=mask_paths),
        )
        return self.client.update_deployment(request=request)

    def delete_deployment(self, deployment_id: str) -> None:
        """Deletes a specific deployment."""
        request = types.DeleteDeploymentRequest(
            name=f"{self.app_name}/deployments/{deployment_id}"
        )
        self.client.delete_deployment(request=request)
