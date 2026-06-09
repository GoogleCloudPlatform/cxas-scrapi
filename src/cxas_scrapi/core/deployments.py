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

from enum import Enum
from typing import Any

from google.cloud.ces_v1beta import types
from google.protobuf import field_mask_pb2

from cxas_scrapi.core.apps import Apps


class Deployments(Apps):
    """Core Class for managing Deployment Resources."""

    class ChannelType(Enum):
        WEB_UI = "WEB_UI"
        API = "API"
        TWILIO = "TWILIO"
        GOOGLE_TELEPHONY_PLATFORM = "GOOGLE_TELEPHONY_PLATFORM"
        CONTACT_CENTER_AS_A_SERVICE = "CONTACT_CENTER_AS_A_SERVICE"
        FIVE9 = "FIVE9"
        AUDIOCODES = "CONTACT_CENTER_INTEGRATION"

    class Modality(Enum):
        CHAT_AND_VOICE = "CHAT_AND_VOICE"
        VOICE_ONLY = "VOICE_ONLY"
        CHAT_ONLY = "CHAT_ONLY"
        CHAT_VOICE_AND_VIDEO = "CHAT_VOICE_AND_VIDEO"

    class Theme(Enum):
        LIGHT = "LIGHT"
        DARK = "DARK"

    def __init__(
        self,
        app_name: str,
        creds_path: str | None = None,
        creds_dict: dict[str, str] | None = None,
        creds: Any = None,
        scope: list[str] | None = None,
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

    @classmethod
    def _build_web_widget_config(
        cls, kwargs: dict[str, Any], mask_paths: list[str] | None = None
    ) -> types.ChannelProfile.WebWidgetConfig | None:
        """Helper to build WebWidgetConfig and update mask paths."""
        wwc_fields = ["modality", "theme", "web_widget_title"]
        has_wwc_update = any(k in kwargs for k in wwc_fields)

        if not has_wwc_update:
            return None

        wwc = types.ChannelProfile.WebWidgetConfig()

        if "modality" in kwargs:
            modality = kwargs.pop("modality")
            if isinstance(modality, str):
                modality = cls.Modality[modality.upper()]
            wwc.modality = getattr(
                types.ChannelProfile.WebWidgetConfig.Modality, modality.value
            )
            if mask_paths is not None:
                mask_paths.append("channel_profile.web_widget_config.modality")

        if "theme" in kwargs:
            theme = kwargs.pop("theme")
            if isinstance(theme, str):
                theme = cls.Theme[theme.upper()]
            wwc.theme = getattr(
                types.ChannelProfile.WebWidgetConfig.Theme, theme.value
            )
            if mask_paths is not None:
                mask_paths.append("channel_profile.web_widget_config.theme")

        if "web_widget_title" in kwargs:
            wwc.web_widget_title = kwargs.pop("web_widget_title")
            if mask_paths is not None:
                mask_paths.append(
                    "channel_profile.web_widget_config.web_widget_title"
                )

        return wwc

    def list_deployments(self) -> list[types.Deployment]:
        """Lists deployments within a specific app."""
        request = types.ListDeploymentsRequest(parent=self.app_name)
        response = self.client.list_deployments(request=request)
        return list(response)

    def get_deployments_map(self, reverse: bool = False) -> dict[str, str]:
        """Creates a map of Deployment full names to display names.

        Args:
            reverse: If True, map display_name -> name.
        """
        deployments = self.list_deployments()
        deployments_dict: dict[str, str] = {}

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
        channel_type: ChannelType | str = ChannelType.API,
        modality: Modality | str | None = None,
        theme: Theme | str | None = None,
        web_widget_title: str | None = None,
        disable_dtmf: bool = False,
        disable_barge_in_control: bool = False,
    ) -> types.Deployment:
        """Creates a new deployment with specified configuration.

        Note: `modality`, `theme`, and `web_widget_title` are only applicable
        when `channel_type` is `ChannelType.WEB_UI`.
        """

        deployment = types.Deployment(
            display_name=display_name, app_version=app_version
        )

        # Convert string to enum if needed
        if isinstance(channel_type, str):
            channel_type = self.ChannelType[channel_type.upper()]

        channel_profile = types.ChannelProfile()

        channel_profile.channel_type = getattr(
            types.common.ChannelProfile.ChannelType, channel_type.value
        )

        channel_profile.disable_dtmf = disable_dtmf
        channel_profile.disable_barge_in_control = disable_barge_in_control

        if channel_type == self.ChannelType.WEB_UI:
            wwc_kwargs = {
                "modality": modality or self.Modality.CHAT_AND_VOICE,
                "theme": theme or self.Theme.LIGHT,
            }
            if web_widget_title:
                wwc_kwargs["web_widget_title"] = web_widget_title

            wwc = self._build_web_widget_config(wwc_kwargs)
            if wwc:
                channel_profile.web_widget_config = wwc

        deployment.channel_profile = channel_profile

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

        channel_profile_fields = [
            "channel_type",
            "modality",
            "theme",
            "web_widget_title",
            "disable_dtmf",
            "disable_barge_in_control",
        ]

        has_channel_profile_update = any(
            k in kwargs for k in channel_profile_fields
        )

        if has_channel_profile_update:
            channel_profile = types.ChannelProfile()

            if "channel_type" in kwargs:
                channel_type = kwargs.pop("channel_type")
                if isinstance(channel_type, str):
                    channel_type = self.ChannelType[channel_type.upper()]
                channel_profile.channel_type = getattr(
                    types.common.ChannelProfile.ChannelType, channel_type.value
                )
                mask_paths.append("channel_profile.channel_type")

            if "disable_dtmf" in kwargs:
                channel_profile.disable_dtmf = kwargs.pop("disable_dtmf")
                mask_paths.append("channel_profile.disable_dtmf")

            if "disable_barge_in_control" in kwargs:
                channel_profile.disable_barge_in_control = kwargs.pop(
                    "disable_barge_in_control"
                )
                mask_paths.append("channel_profile.disable_barge_in_control")

            wwc = self._build_web_widget_config(kwargs, mask_paths)
            if wwc:
                channel_profile.web_widget_config = wwc

            deployment.channel_profile = channel_profile

        # Handle remaining kwargs as top-level fields
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
