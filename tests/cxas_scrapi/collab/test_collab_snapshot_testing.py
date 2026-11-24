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

"""Unit tests for cxas_scrapi.collab.snapshot_testing."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from cxas_scrapi.collab import snapshot_testing as st

APP_NAME = "projects/p/locations/us/apps/a"


def test_freeze_snapshot_creates_version_and_updates_deployment() -> None:
    versions = mock.MagicMock()
    versions.create_version.return_value = SimpleNamespace(
        name=f"{APP_NAME}/versions/v-123",
        display_name="test-snapshot-20261124-120000-alice",
    )
    versions.list_versions.return_value = []
    deployments = mock.MagicMock()
    deployments.get_deployment.return_value = SimpleNamespace(
        name=f"{APP_NAME}/deployments/cxas-test-snapshot-alice"
    )
    deployments.update_deployment.return_value = SimpleNamespace(
        name=f"{APP_NAME}/deployments/cxas-test-snapshot-alice"
    )

    info = st.freeze_snapshot_info(
        APP_NAME,
        label="pr-42",
        user="alice@example.com",
        versions=versions,
        deployments=deployments,
        keep=None,
    )
    assert info.version_id == "v-123"
    assert info.deployment_id == "cxas-test-snapshot-alice"
    assert info.created_deployment is False
    deployments.update_deployment.assert_called_once()


def test_begin_run_rejects_label_without_snapshot() -> None:
    with pytest.raises(
        ValueError, match="--snapshot-label requires --snapshot"
    ):
        st.begin_run(APP_NAME, snapshot=False, label="bad")


def test_run_context_warns_on_mixed_versions_for_live_draft() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    changelogs = mock.MagicMock()
    changelogs.list_changelogs.return_value = [
        SimpleNamespace(
            sequence_number=5,
            resource=f"{APP_NAME}/agents/root",
            resource_type="AGENT",
            action="UPDATE",
            description="Edited prompt",
            create_time=now,
        )
    ]

    ctx = st.RunContext(
        app_name=APP_NAME,
        started_at=(now - datetime.timedelta(seconds=10)).isoformat(),
        changelog_seq_start=4,
        _changelogs=changelogs,
    )
    ctx.mark_ran("simulation")
    stamp = ctx.finish()
    assert stamp["possible_mixed_versions"] is True
    assert "may mix versions" in (stamp["warning"] or "")


def test_run_context_goldens_only_gets_pinned_info_not_warning() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    changelogs = mock.MagicMock()
    changelogs.list_changelogs.return_value = [
        SimpleNamespace(
            sequence_number=5,
            resource=f"{APP_NAME}/agents/root",
            resource_type="AGENT",
            action="UPDATE",
            description="Edited prompt",
            create_time=now,
        )
    ]
    ctx = st.RunContext(
        app_name=APP_NAME,
        started_at=(now - datetime.timedelta(seconds=10)).isoformat(),
        changelog_seq_start=4,
        _changelogs=changelogs,
    )
    ctx.mark_ran("golden")
    stamp = ctx.finish()
    assert stamp["possible_mixed_versions"] is False
    assert stamp["warning"] is None
    assert "pinned by the platform" in (stamp["info"] or "")
