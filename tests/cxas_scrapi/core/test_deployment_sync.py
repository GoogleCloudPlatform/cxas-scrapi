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

"""Tests for `deployments/*.yaml` pull/push sync (`deployment_sync.py`)."""

from __future__ import annotations

import argparse
import io
import zipfile
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import yaml
from google.cloud.ces_v1beta import types
from google.protobuf.timestamp_pb2 import Timestamp

from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.core.deployment_sync import (
    deployment_to_yaml_dict,
    export_deployments_to_dir,
    is_build_metadata_deployment,
    sync_deployments_from_dir,
)
from cxas_scrapi.core.packaging import package_app_archive


class _FakeDeploymentsClient:
    """In-memory stub mirroring `Deployments` public API."""

    def __init__(
        self,
        app_name: str,
        initial: list[types.Deployment] | None = None,
    ) -> None:
        self.app_name = app_name
        self._deployments: list[types.Deployment] = list(initial or [])
        self.created_calls: list[dict[str, Any]] = []
        self.updated_calls: list[tuple[str, dict[str, Any]]] = []

    def list_deployments(self) -> list[types.Deployment]:
        return list(self._deployments)

    def create_deployment(self, **kwargs: Any) -> types.Deployment:
        self.created_calls.append(dict(kwargs))
        dep_id = kwargs["deployment_id"]
        return types.Deployment(
            name=f"{self.app_name}/deployments/{dep_id}",
            display_name=kwargs["display_name"],
            app_version=kwargs["app_version"],
        )

    def update_deployment(
        self, deployment_id: str, **kwargs: Any
    ) -> types.Deployment:
        self.updated_calls.append((deployment_id, dict(kwargs)))
        return types.Deployment(
            name=f"{self.app_name}/deployments/{deployment_id}",
            display_name=kwargs.get("display_name", deployment_id),
        )


def _sample_proto_deployment(
    app_name: str, dep_id: str = "web-prod"
) -> types.Deployment:
    ts = Timestamp(seconds=1_700_000_000)
    dep = types.Deployment(
        name=f"{app_name}/deployments/{dep_id}",
        display_name="Web Production",
        app_version=f"{app_name}/versions/v1",
        etag='W/"abc123"',
        create_time=ts,
        update_time=ts,
        channel_profile=types.ChannelProfile(
            channel_type=types.ChannelProfile.ChannelType.WEB_UI,
            disable_dtmf=True,
            disable_barge_in_control=False,
            noise_suppression_level="high",
            persona_property=types.ChannelProfile.PersonaProperty(
                persona="CONCISE"
            ),
            web_widget_config=types.ChannelProfile.WebWidgetConfig(
                modality=types.ChannelProfile.WebWidgetConfig.Modality.CHAT_ONLY,
                theme=types.ChannelProfile.WebWidgetConfig.Theme.DARK,
                web_widget_title="Support Chat",
            ),
        ),
    )
    if hasattr(types, "ExperimentConfig"):
        dep.experiment_config = types.ExperimentConfig(
            version_release=types.ExperimentConfig.VersionRelease(
                state=types.ExperimentConfig.State.RUNNING,
                traffic_allocations=[
                    types.ExperimentConfig.VersionRelease.TrafficAllocation(
                        app_version=f"{app_name}/versions/v1",
                        traffic_percentage=80,
                    ),
                    types.ExperimentConfig.VersionRelease.TrafficAllocation(
                        app_version=f"{app_name}/versions/v2",
                        traffic_percentage=20,
                    ),
                ],
            )
        )
    return dep


def test_is_build_metadata_deployment_classification() -> None:
    assert is_build_metadata_deployment(
        {
            "profileId": "brand_alpha",
            "channel": "DIGITAL_APP",
            "environment": "prod",
            "channelProfile": {"channelType": "WEB_UI"},
        }
    )
    assert not is_build_metadata_deployment(
        {
            "deploymentId": "web-prod",
            "displayName": "Web Prod",
            "appVersion": "v1",
        }
    )
    assert not is_build_metadata_deployment(
        {
            "profileId": "brand_alpha",
            "deploymentId": "web-prod",
        }
    )
    assert not is_build_metadata_deployment(
        {
            "profileId": "brand_alpha",
            "appVersion": "v1",
        }
    )
    assert not is_build_metadata_deployment(
        {
            "profileId": "brand_alpha",
            "experimentConfig": {"versionRelease": {}},
        }
    )


def test_deployment_to_yaml_dict_strips_volatile_fields_and_validates_id() -> (
    None
):
    app_name = "projects/p/locations/us/apps/a1"
    dep = _sample_proto_deployment(app_name, "web-prod")
    doc = deployment_to_yaml_dict(dep)

    assert doc["deploymentId"] == "web-prod"
    assert doc["displayName"] == "Web Production"
    assert doc["appVersion"] == f"{app_name}/versions/v1"
    assert doc["channelProfile"]["channelType"] == "WEB_UI"
    assert doc["channelProfile"]["webWidgetConfig"]["theme"] == "DARK"
    assert doc["channelProfile"]["personaProperty"] == {"persona": "CONCISE"}
    for volatile in ("name", "createTime", "updateTime", "etag"):
        assert volatile not in doc

    # Path traversal in deployment ID is rejected.
    bad_dep = types.Deployment(
        name=f"{app_name}/deployments/../../escape",
        display_name="Bad",
    )
    with pytest.raises(ValueError, match="invalid deploymentId"):
        deployment_to_yaml_dict(bad_dep)


def test_export_deployments_preserves_build_metadata_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    app_name = "projects/p/locations/us/apps/a1"
    app_root = tmp_path / "app"
    dep_dir = app_root / "deployments"
    dep_dir.mkdir(parents=True)

    # Seed a cxas build channel-metadata YAML that must NEVER be deleted.
    meta_file = dep_dir / "digital-app.yaml"
    meta_content = (
        "profileId: brand_alpha\nchannel: DIGITAL_APP\nenvironment: prod\n"
    )
    meta_file.write_text(meta_content, encoding="utf-8")

    client = _FakeDeploymentsClient(
        app_name,
        initial=[
            _sample_proto_deployment(app_name, "web-prod"),
            types.Deployment(
                name=f"{app_name}/deployments/voice-prod",
                display_name="Voice Prod",
                app_version=f"{app_name}/versions/v2",
            ),
        ],
    )
    written = export_deployments_to_dir(client, app_root)
    assert [p.name for p in written] == ["voice-prod.yaml", "web-prod.yaml"]
    assert meta_file.is_file()
    assert meta_file.read_text(encoding="utf-8") == meta_content

    parsed = yaml.safe_load((dep_dir / "web-prod.yaml").read_text("utf-8"))
    assert parsed["deploymentId"] == "web-prod"
    assert "etag" not in parsed

    # Symlink inside deployments/ must be rejected cleanly.
    outside = tmp_path / "outside.yaml"
    outside.write_text("deploymentId: sneaky\n", encoding="utf-8")
    (dep_dir / "sneaky.yaml").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        export_deployments_to_dir(client, app_root)


def test_sync_deployments_create_update_skip_and_version_normalization(
    tmp_path: Path,
) -> None:
    app_name = "projects/p/locations/us/apps/a1"
    app_root = tmp_path / "app"
    dep_dir = app_root / "deployments"
    dep_dir.mkdir(parents=True)

    # 1. Build metadata YAML -> skipped cleanly.
    (dep_dir / "digital-app.yaml").write_text(
        yaml.safe_dump(
            {
                "profileId": "brand_alpha",
                "channel": "DIGITAL_APP",
                "environment": "dev",
            }
        ),
        encoding="utf-8",
    )

    # 2. Existing deployment -> update_deployment with normalized version.
    (dep_dir / "web-prod.yaml").write_text(
        yaml.safe_dump(
            {
                "deploymentId": "web-prod",
                "displayName": "Web Production v2",
                "appVersion": "v2",
                "channelProfile": {
                    "channelType": "WEB_UI",
                    "disableDtmf": True,
                    "disableBargeInControl": True,
                    "noiseSuppressionLevel": "moderate",
                    "personaProperty": {"persona": "CHATTY"},
                    "webWidgetConfig": {
                        "modality": "CHAT_AND_VOICE",
                        "theme": "LIGHT",
                        "webWidgetTitle": "Help Desk",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. New deployment (.yml extension) with experimentConfig traffic split -> create_deployment.
    (dep_dir / "voice-canary.yml").write_text(
        yaml.safe_dump(
            {
                "deploymentId": "voice-canary",
                "displayName": "Voice Canary",
                "appVersion": "v1",
                "channelProfile": {
                    "channelType": "GOOGLE_TELEPHONY_PLATFORM",
                    "disableDtmf": False,
                },
                "experimentConfig": {
                    "versionRelease": {
                        "state": "RUNNING",
                        "trafficAllocations": [
                            {"appVersion": "v1", "trafficPercentage": 90},
                            {"appVersion": "v2", "trafficPercentage": 10},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    client = _FakeDeploymentsClient(
        app_name,
        initial=[_sample_proto_deployment(app_name, "web-prod")],
    )
    summary = sync_deployments_from_dir(client, app_root)
    assert summary == {
        "created": ["voice-canary"],
        "updated": ["web-prod"],
        "skipped": ["digital-app.yaml"],
    }

    assert len(client.updated_calls) == 1
    upd_id, upd_kw = client.updated_calls[0]
    assert upd_id == "web-prod"
    assert upd_kw["app_version"] == f"{app_name}/versions/v2"
    assert upd_kw["display_name"] == "Web Production v2"
    assert upd_kw["channel_type"] == "WEB_UI"
    assert upd_kw["modality"] == "CHAT_AND_VOICE"
    assert upd_kw["theme"] == "LIGHT"
    assert upd_kw["web_widget_title"] == "Help Desk"
    assert upd_kw["disable_dtmf"] is True
    assert upd_kw["disable_barge_in_control"] is True
    assert upd_kw["persona_property"] == "CHATTY"
    assert upd_kw["noise_suppression_level"] == "moderate"

    assert len(client.created_calls) == 1
    crt_kw = client.created_calls[0]
    assert crt_kw["deployment_id"] == "voice-canary"
    assert crt_kw["display_name"] == "Voice Canary"
    assert crt_kw["app_version"] == f"{app_name}/versions/v1"
    assert crt_kw["channel_type"] == "GOOGLE_TELEPHONY_PLATFORM"
    assert crt_kw["traffic_split"] == {
        f"{app_name}/versions/v1": 90,
        f"{app_name}/versions/v2": 10,
    }


def test_sync_deployments_rejects_symlinks_traversal_and_excluded_from_zip(
    tmp_path: Path,
) -> None:
    app_name = "projects/p/locations/us/apps/a1"
    app_root = tmp_path / "app"
    (app_root / "deployments").mkdir(parents=True)
    (app_root / "app.json").write_text('{"displayName": "A"}\n', "utf-8")
    (app_root / "deployments" / "web-prod.yaml").write_text(
        yaml.safe_dump(
            {
                "deploymentId": "web-prod",
                "displayName": "Web",
                "appVersion": "v1",
            }
        ),
        encoding="utf-8",
    )

    # Confirm deployments/ is NEVER included in ImportApp ZIPs!
    zip_out = tmp_path / "app.zip"
    package_app_archive(app_root, zip_out)
    with zipfile.ZipFile(zip_out) as zf:
        assert not any(
            name.startswith("deployments/") or "/deployments/" in name
            for name in zf.namelist()
        )

    # Path traversal in deploymentId rejected before any API calls.
    (app_root / "deployments" / "evil.yaml").write_text(
        yaml.safe_dump(
            {
                "deploymentId": "../escape",
                "displayName": "Evil",
                "appVersion": "v1",
            }
        ),
        encoding="utf-8",
    )
    client = _FakeDeploymentsClient(app_name)
    with pytest.raises(ValueError, match="invalid deploymentId"):
        sync_deployments_from_dir(client, app_root)
    assert client.created_calls == []
    assert client.updated_calls == []

    # Symlink file in deployments/ rejected.
    (app_root / "deployments" / "evil.yaml").unlink()
    ext = tmp_path / "ext.yaml"
    ext.write_text("deploymentId: ext\n", encoding="utf-8")
    (app_root / "deployments" / "link.yaml").symlink_to(ext)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        sync_deployments_from_dir(client, app_root)


def test_cli_pull_and_push_sync_deployments_wiring(tmp_path: Path) -> None:
    app_name = "projects/p/locations/us/apps/a1"
    fake_dep_client = _FakeDeploymentsClient(
        app_name,
        initial=[_sample_proto_deployment(app_name, "web-prod")],
    )

    # Build a minimal exported app ZIP for _app_pull.
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("my_app/app.json", '{"displayName": "My App"}\n')
    mock_apps = MagicMock()
    mock_apps.creds = "fake-creds"
    mock_lro = MagicMock()
    mock_lro.result.return_value = MagicMock(app_content=zip_buf.getvalue())
    mock_apps.export_app.return_value = mock_lro
    mock_apps.import_app.return_value = MagicMock(
        result=MagicMock(return_value=MagicMock(name=app_name, warnings=[]))
    )
    mock_apps.import_app.return_value.result.return_value.name = app_name

    pull_target = tmp_path / "pulled"
    pull_args = argparse.Namespace(force=False, sync_deployments=True)

    with patch(
        "cxas_scrapi.core.deployments.Deployments",
        return_value=fake_dep_client,
    ) as mock_dep_cls:
        cli_app._app_pull(
            mock_apps,
            app_name,
            str(pull_target),
            overwrite=False,
            args=pull_args,
        )
        mock_dep_cls.assert_called_once_with(
            app_name=app_name, creds="fake-creds"
        )

    pulled_dep_yaml = pull_target / "my_app" / "deployments" / "web-prod.yaml"
    assert pulled_dep_yaml.is_file()

    # Now push that pulled dir back with --sync-deployments.
    push_args = argparse.Namespace(
        overwrite=True,
        native_python_env=False,
        create_version=False,
        sync_deployments=True,
        check_drift=False,
        live_dir=None,
        lockfile=None,
    )
    with patch(
        "cxas_scrapi.core.deployments.Deployments",
        return_value=fake_dep_client,
    ) as mock_dep_cls:
        cli_app._app_push(
            app_dir=str(pull_target / "my_app"),
            apps_client=mock_apps,
            target_app_name=app_name,
            identifier=app_name,
            args=push_args,
            _skip_pre_push_gate=True,
        )
        mock_dep_cls.assert_called_once_with(
            app_name=app_name, creds="fake-creds"
        )

    assert len(fake_dep_client.updated_calls) == 1
    assert fake_dep_client.updated_calls[0][0] == "web-prod"
