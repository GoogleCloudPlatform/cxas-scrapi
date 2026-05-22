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
from google.cloud.ces_v1beta import types

from cxas_scrapi.core.deployments import Deployments


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_list_deployments(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_dep = MagicMock()
    mock_dep.name = "dep1"
    mock_client.list_deployments.return_value = [mock_dep]

    deps = Deployments("projects/p/locations/l/apps/A")
    res = deps.list_deployments()
    assert len(res) == 1
    assert res[0].name == "dep1"


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_get_deployments_map(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_dep1 = MagicMock()
    mock_dep1.name = "d1"
    mock_dep1.display_name = "n1"
    mock_dep2 = MagicMock()
    mock_dep2.name = "d2"
    mock_dep2.display_name = "n2"
    mock_client.list_deployments.return_value = [mock_dep1, mock_dep2]

    deps = Deployments("projects/p/locations/l/apps/A")
    res = deps.get_deployments_map()
    assert res["d1"] == "n1"
    assert res["d2"] == "n2"

    res_rev = deps.get_deployments_map(reverse=True)
    assert res_rev["n1"] == "d1"
    assert res_rev["n2"] == "d2"


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_get_deployment(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_dep = MagicMock()
    mock_dep.name = "projects/p/locations/l/apps/A/deployments/dep_id"
    mock_client.get_deployment.return_value = mock_dep

    deps = Deployments("projects/p/locations/l/apps/A")
    res = deps.get_deployment("dep_id")
    assert res.name == "projects/p/locations/l/apps/A/deployments/dep_id"
    mock_client.get_deployment.assert_called_once()


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_create_deployment(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.create_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.create_deployment("dep_id", "my_dep", "v1")
    mock_client.create_deployment.assert_called_once()
    args = mock_client.create_deployment.call_args[1]["request"]
    assert args.parent == "projects/p/locations/l/apps/A"
    assert args.deployment_id == "dep_id"


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_create_deployment_with_options(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.create_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.create_deployment(
        "dep_id",
        "my_dep",
        "v1",
        channel_type=Deployments.ChannelType.WEB_UI,
        modality=Deployments.Modality.CHAT_ONLY,
        theme=Deployments.Theme.DARK,
        web_widget_title="My Title",
        disable_dtmf=True,
        disable_barge_in_control=True,
    )

    mock_client.create_deployment.assert_called_once()

    args = mock_client.create_deployment.call_args[1]["request"]

    assert args.parent == "projects/p/locations/l/apps/A"
    assert args.deployment_id == "dep_id"

    dep = args.deployment
    assert dep.display_name == "my_dep"
    assert dep.app_version == "v1"

    cp = dep.channel_profile
    assert cp.disable_dtmf is True
    assert cp.disable_barge_in_control is True

    wwc = cp.web_widget_config
    assert wwc.web_widget_title == "My Title"
    assert wwc.modality == (
        types.ChannelProfile.WebWidgetConfig.Modality.CHAT_ONLY
    )
    assert wwc.theme == types.ChannelProfile.WebWidgetConfig.Theme.DARK


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_update_deployment(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.update_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.update_deployment("dep_id", display_name="new_name")
    mock_client.update_deployment.assert_called_once()
    args = mock_client.update_deployment.call_args[1]["request"]
    assert (
        args.deployment.name
        == "projects/p/locations/l/apps/A/deployments/dep_id"
    )
    assert args.deployment.display_name == "new_name"


@patch("cxas_scrapi.core.deployments.types.Deployment")
@patch("cxas_scrapi.core.deployments.types.UpdateDeploymentRequest")
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_update_deployment_with_options(
    mock_client_cls, mock_req_cls, mock_dep_cls
):
    mock_client = mock_client_cls.return_value
    mock_client.update_deployment.return_value = MagicMock()

    def side_effect(**kwargs):
        m = MagicMock()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    mock_req_cls.side_effect = side_effect

    mock_dep = MagicMock()
    mock_dep_cls.return_value = mock_dep

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.update_deployment(
        "dep_id",
        modality=Deployments.Modality.CHAT_ONLY,
        theme=Deployments.Theme.DARK,
    )
    mock_client.update_deployment.assert_called_once()

    mock_dep_cls.assert_called_once_with(
        name="projects/p/locations/l/apps/A/deployments/dep_id"
    )

    assert mock_dep.channel_profile is not None
    cp = mock_dep.channel_profile

    wwc = cp.web_widget_config
    assert wwc.modality == (
        types.ChannelProfile.WebWidgetConfig.Modality.CHAT_ONLY
    )
    assert wwc.theme == types.ChannelProfile.WebWidgetConfig.Theme.DARK

    args = mock_client.update_deployment.call_args[1]["request"]
    mask = args.update_mask
    assert "channel_profile.web_widget_config.modality" in mask.paths
    assert "channel_profile.web_widget_config.theme" in mask.paths


def test_build_web_widget_config():
    kwargs = {
        "modality": "CHAT_ONLY",
        "theme": "DARK",
        "web_widget_title": "New Title",
        "other_arg": "value",
    }
    mask_paths = []

    wwc = Deployments._build_web_widget_config(kwargs, mask_paths)

    assert wwc is not None
    assert wwc.web_widget_title == "New Title"
    assert wwc.modality == (
        types.ChannelProfile.WebWidgetConfig.Modality.CHAT_ONLY
    )
    assert wwc.theme == types.ChannelProfile.WebWidgetConfig.Theme.DARK

    assert "channel_profile.web_widget_config.modality" in mask_paths
    assert "channel_profile.web_widget_config.theme" in mask_paths
    assert "channel_profile.web_widget_config.web_widget_title" in mask_paths

    assert "modality" not in kwargs
    assert "theme" not in kwargs
    assert "web_widget_title" not in kwargs
    assert kwargs["other_arg"] == "value"


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_create_deployment_with_strings(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.create_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.create_deployment(
        "dep_id",
        "my_dep",
        "v1",
        channel_type="WEB_UI",
        modality="CHAT_ONLY",
        theme="DARK",
    )
    mock_client.create_deployment.assert_called_once()

    args = mock_client.create_deployment.call_args[1]["request"]
    dep = args.deployment

    cp = dep.channel_profile
    wwc = cp.web_widget_config
    assert wwc.modality == (
        types.ChannelProfile.WebWidgetConfig.Modality.CHAT_ONLY
    )
    assert wwc.theme == types.ChannelProfile.WebWidgetConfig.Theme.DARK


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_update_deployment_all_options(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.update_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.update_deployment(
        "dep_id",
        channel_type=Deployments.ChannelType.API,
        disable_dtmf=True,
        disable_barge_in_control=True,
    )
    mock_client.update_deployment.assert_called_once()

    args = mock_client.update_deployment.call_args[1]["request"]
    dep = args.deployment

    cp = dep.channel_profile
    assert cp.channel_type == types.common.ChannelProfile.ChannelType.API
    assert cp.disable_dtmf is True
    assert cp.disable_barge_in_control is True

    mask = args.update_mask
    assert "channel_profile.channel_type" in mask.paths
    assert "channel_profile.disable_dtmf" in mask.paths
    assert "channel_profile.disable_barge_in_control" in mask.paths


@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_delete_deployment(mock_client_cls):
    mock_client = mock_client_cls.return_value

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.delete_deployment("dep_id")
    mock_client.delete_deployment.assert_called_once()
    args = mock_client.delete_deployment.call_args[1]["request"]
    assert args.name == "projects/p/locations/l/apps/A/deployments/dep_id"


@pytest.mark.parametrize(
    "channel_type_enum, expected_proto_value",
    [
        (
            Deployments.ChannelType.GOOGLE_TELEPHONY_PLATFORM,
            types.common.ChannelProfile.ChannelType.GOOGLE_TELEPHONY_PLATFORM,
        ),
        (
            Deployments.ChannelType.CONTACT_CENTER_AS_A_SERVICE,
            types.common.ChannelProfile.ChannelType.CONTACT_CENTER_AS_A_SERVICE,
        ),
        (
            Deployments.ChannelType.AUDIOCODES,
            types.common.ChannelProfile.ChannelType.CONTACT_CENTER_INTEGRATION,
        ),
        (
            Deployments.ChannelType.API,
            types.common.ChannelProfile.ChannelType.API,
        ),
        (
            Deployments.ChannelType.FIVE9,
            types.common.ChannelProfile.ChannelType.FIVE9,
        ),
        (
            Deployments.ChannelType.TWILIO,
            types.common.ChannelProfile.ChannelType.TWILIO,
        ),
    ],
)
@patch("cxas_scrapi.core.apps.AgentServiceClient")
def test_create_deployment_different_channels(
    mock_client_cls, channel_type_enum, expected_proto_value
):
    mock_client = mock_client_cls.return_value
    mock_client.create_deployment.return_value = MagicMock()

    deps = Deployments("projects/p/locations/l/apps/A")
    deps.create_deployment(
        "dep_id",
        "my_dep",
        "v1",
        channel_type=channel_type_enum,
    )
    mock_client.create_deployment.assert_called_once()
    args = mock_client.create_deployment.call_args[1]["request"]
    assert args.deployment.channel_profile.channel_type == expected_proto_value
