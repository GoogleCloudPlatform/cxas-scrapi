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

"""Unit tests for cxas_scrapi.collab.metadata."""

from __future__ import annotations

import datetime
import json
from typing import Any
from unittest import mock

import pytest
from google.api_core import exceptions as gexc
from google.cloud.ces_v1beta import types

from cxas_scrapi.collab.metadata import (
    AppMetadataStore,
    ConcurrentModificationError,
    LockHeldError,
    current_user,
    preserve_cxas_keys,
)

APP_NAME = "projects/p/locations/us/apps/a"


class _FakeApps:
    """In-memory Apps fake supporting etag CAS and locked enforcement."""

    def __init__(
        self,
        metadata: dict[str, str] | None = None,
        locked: bool = False,
        reject_atomic_locked_write: bool = False,
        fail_first_n_aborts: int = 0,
    ) -> None:
        self.metadata: dict[str, str] = dict(metadata or {})
        self.locked = locked
        self.etag = "etag-1"
        self.version = 1
        self.reject_atomic_locked_write = reject_atomic_locked_write
        self.fail_first_n_aborts = fail_first_n_aborts
        self.writes: list[tuple[list[str], bool | None]] = []
        self.client = mock.MagicMock()
        self.client.update_app.side_effect = self._update_app

    def get_app(self, app_name: str) -> types.App:
        return types.App(
            name=app_name,
            metadata=dict(self.metadata),
            locked=self.locked,
            etag=self.etag,
        )

    def _update_app(self, request: types.UpdateAppRequest) -> types.App:
        if self.fail_first_n_aborts > 0:
            self.fail_first_n_aborts -= 1
            raise gexc.Aborted("etag mismatch")
        paths = list(request.update_mask.paths)
        if (
            self.reject_atomic_locked_write
            and self.locked
            and "metadata" in paths
            and "locked" in paths
        ):
            raise gexc.FailedPrecondition("App is locked")
        self.writes.append(
            (paths, request.app.locked if "locked" in paths else None)
        )
        if "metadata" in paths:
            self.metadata = dict(request.app.metadata)
        if "locked" in paths:
            self.locked = bool(request.app.locked)
        self.version += 1
        self.etag = f"etag-{self.version}"
        return self.get_app(request.app.name)


def test_preserve_cxas_keys_keeps_server_cxas_and_drops_local_cxas() -> None:
    old = {
        "cxas.lock": '{"by":"alice"}',
        "cxas.last_push": '{"seq":5}',
        "foo": "old",
    }
    new = {"cxas.lock": '{"by":"stale"}', "foo": "new", "bar": "added"}
    merged = preserve_cxas_keys(old, new)
    assert merged == {
        "foo": "new",
        "bar": "added",
        "cxas.lock": '{"by":"alice"}',
        "cxas.last_push": '{"seq":5}',
    }


def test_current_user_respects_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CXAS_USER", "ci-bot@example.com")
    assert current_user() == "ci-bot@example.com"


def test_cas_retry_succeeds_after_aborted() -> None:
    fake = _FakeApps(fail_first_n_aborts=2)
    store = AppMetadataStore(
        APP_NAME, apps=fake, max_retries=4, retry_backoff=0.0, user="alice"
    )
    store.set_last_push(seq=42, git_sha="abc1234", by="alice")
    lp = store.get_last_push()
    assert lp is not None
    assert lp["seq"] == 42
    assert lp["git_sha"] == "abc1234"


def test_cas_exhaustion_raises_concurrent_modification() -> None:
    fake = _FakeApps(fail_first_n_aborts=5)
    store = AppMetadataStore(
        APP_NAME, apps=fake, max_retries=2, retry_backoff=0.0, user="alice"
    )
    with pytest.raises(ConcurrentModificationError):
        store.set("team", "core")


def test_lock_acquire_status_foreign_refusal_and_release() -> None:
    fake = _FakeApps()
    alice = AppMetadataStore(
        APP_NAME, apps=fake, retry_backoff=0.0, user="alice"
    )
    bob = AppMetadataStore(APP_NAME, apps=fake, retry_backoff=0.0, user="bob")

    lock = alice.acquire_lock(user="alice", ttl_minutes=30, reason="release")
    assert lock["by"] == "alice"
    assert fake.locked is True

    state = alice.lock_state()
    assert state["native_locked"] is True
    assert state["stale"] is False
    assert state["lock"]["by"] == "alice"

    # Bob cannot steal or release without force
    with pytest.raises(LockHeldError):
        bob.acquire_lock(user="bob")
    with pytest.raises(LockHeldError):
        bob.release_lock(user="bob")

    # Alice releases cleanly
    assert alice.release_lock(user="alice") is True
    assert fake.locked is False
    assert alice.lock_state()["lock"] is None
    assert alice.release_lock(user="alice") is False


def test_stale_lock_detected_and_cleared() -> None:
    past = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = _FakeApps(
        metadata={"cxas.lock": json.dumps({"by": "alice", "until": past})},
        locked=True,
    )
    store = AppMetadataStore(APP_NAME, apps=fake, retry_backoff=0.0, user="bob")
    state = store.lock_state()
    assert state["lock"] is None
    assert state["native_locked"] is True
    assert state["stale"] is True

    assert store.clear_stale_lock() is True
    assert fake.locked is False


def test_fallback_unlock_write_relock_when_atomic_refused() -> None:
    future = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = _FakeApps(
        metadata={"cxas.lock": json.dumps({"by": "alice", "until": future})},
        locked=True,
        reject_atomic_locked_write=True,
    )
    store = AppMetadataStore(
        APP_NAME, apps=fake, retry_backoff=0.0, user="alice"
    )
    store.set_last_push(seq=10, git_sha="deadbee")
    assert fake.locked is True
    assert store.get_last_push()["seq"] == 10  # type: ignore[index]


def test_owners_and_history_removed_from_store() -> None:
    fake = _FakeApps()
    store: Any = AppMetadataStore(APP_NAME, apps=fake, user="alice")
    assert not hasattr(store, "get_owners")
    assert not hasattr(store, "set_owner")
    assert not hasattr(store, "record_history")
