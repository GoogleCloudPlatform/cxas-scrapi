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

"""Unit tests for stateless CES-side drift detection (cxas_scrapi.collab.drift)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest import mock

from cxas_scrapi.collab.drift import (
    check_drift,
    is_drift_entry,
    record_last_push,
)

APP_NAME = "projects/p/locations/us/apps/a"


def _cl(
    seq: int,
    *,
    action: str = "UPDATE",
    resource_type: str = "AGENT",
    resource: str = f"{APP_NAME}/agents/root",
    display_name: str = "root",
    author: str = "console-user@example.com",
    description: str = "Updated instruction",
) -> SimpleNamespace:
    return SimpleNamespace(
        sequence_number=seq,
        action=action,
        resource_type=resource_type,
        resource=resource,
        display_name=display_name,
        author=author,
        description=description,
        create_time=datetime.datetime(
            2026, 11, 24, 15, 0, 0, tzinfo=datetime.timezone.utc
        ),
    )


def test_is_drift_entry_ignores_bookkeeping_and_non_config_entries() -> None:
    assert not is_drift_entry(
        _cl(
            2,
            resource_type="APP",
            resource=APP_NAME,
            description="Update app fields: metadata, locked",
        )
    )
    assert not is_drift_entry(
        _cl(
            3,
            resource_type="APP_VERSION",
            resource=f"{APP_NAME}/versions/v1",
            description="Created version",
        )
    )
    assert not is_drift_entry(
        _cl(
            4,
            resource_type="DEPLOYMENT",
            resource=f"{APP_NAME}/deployments/live",
        )
    )
    assert not is_drift_entry(
        _cl(
            5,
            resource_type="EVALUATION",
            resource=f"{APP_NAME}/evaluations/e1",
        )
    )
    assert is_drift_entry(_cl(6, resource_type="AGENT"))
    assert is_drift_entry(
        _cl(
            7,
            resource_type="APP",
            resource=APP_NAME,
            description="Update app fields: display_name, description",
        )
    )


def test_check_drift_no_baseline_returns_clean_report() -> None:
    store = mock.MagicMock()
    store.get_last_push.return_value = None
    cl_client = mock.MagicMock()

    report = check_drift(APP_NAME, store=store, changelogs=cl_client)
    assert report.has_baseline is False
    assert report.has_drift is False
    cl_client.list_changelogs.assert_not_called()


def test_check_drift_detects_out_of_band_edits_and_ignores_locks() -> None:
    store = mock.MagicMock()
    store.get_last_push.return_value = {
        "seq": 10,
        "git_sha": "abc123",
        "by": "ci-bot@example.com",
        "at": "2026-11-24T14:00:00Z",
    }
    cl_client = mock.MagicMock()
    cl_client.list_changelogs.return_value = [
        _cl(9, description="Before baseline"),
        _cl(10, action="IMPORT", resource_type="APP", resource=APP_NAME),
        _cl(
            11,
            resource_type="APP",
            resource=APP_NAME,
            description="Update app fields: metadata, locked",
        ),
        _cl(12, resource_type="AGENT", display_name="billing_agent"),
    ]

    report = check_drift(APP_NAME, store=store, changelogs=cl_client)
    assert report.has_baseline is True
    assert report.has_drift is True
    assert [e.seq for e in report.entries] == [12]
    summary = report.format_summary()
    assert "billing_agent" in summary
    assert "seq 12" in summary
    assert "--on-drift=overwrite" in summary


def test_record_last_push_writes_max_seq() -> None:
    store = mock.MagicMock()
    store.set_last_push.return_value = {"seq": 15, "git_sha": "1234567"}
    cl_client = mock.MagicMock()
    cl_client.list_changelogs.return_value = [_cl(10), _cl(15), _cl(12)]

    res = record_last_push(
        APP_NAME,
        store=store,
        changelogs=cl_client,
        git_sha="1234567",
        user="alice",
    )
    assert res["seq"] == 15
    store.set_last_push.assert_called_once_with(
        seq=15, git_sha="1234567", by="alice"
    )
