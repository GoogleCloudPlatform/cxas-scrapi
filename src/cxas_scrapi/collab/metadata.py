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

"""Release-guardrail & collaboration metadata stored on the CES App resource.

State (edit lock, last-push drift baseline, workspace/parent/team/expiry) is
stored as JSON-encoded strings under ``cxas.``-prefixed keys of
``App.metadata``. Every write is a compare-and-set against the App ``etag``
so concurrent writers never silently clobber each other.
"""

from __future__ import annotations

import datetime
import functools
import getpass
import json
import logging
import os
import shutil
import subprocess
import time
import typing
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud.ces_v1beta import types
from google.protobuf import field_mask_pb2

if typing.TYPE_CHECKING:
    from collections.abc import Callable

    from cxas_scrapi.core.apps import Apps

logger = logging.getLogger(__name__)

METADATA_PREFIX = "cxas."

LOCK_KEY = "lock"
LAST_PUSH_KEY = "last_push"
PARENT_KEY = "parent"
TEAM_KEY = "team"
EXPIRY_KEY = "expiry"
WORKSPACE_KEY = "workspace"

_RETRYABLE = (gexc.Aborted, gexc.FailedPrecondition)


class LockHeldError(RuntimeError):
    """Raised when an edit lock is held by another user."""

    def __init__(self, lock: dict[str, Any], message: str | None = None):
        self.lock = lock
        super().__init__(
            message
            or (
                f"App is locked by {lock.get('by')} until "
                f"{lock.get('until')}"
                + (f" ({lock['reason']})" if lock.get("reason") else "")
            )
        )


class ConcurrentModificationError(RuntimeError):
    """Raised when a CAS write keeps losing the etag race."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _to_iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime.datetime:
    """Parses an ISO-8601 timestamp (``Z`` suffix allowed) as aware UTC."""
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # Tolerate hand-written plain strings.
        return raw


def _full_key(key: str) -> str:
    return key if key.startswith(METADATA_PREFIX) else METADATA_PREFIX + key


def _decode_cxas(metadata: typing.Mapping[str, str]) -> dict[str, Any]:
    return {
        k[len(METADATA_PREFIX) :]: _decode(v)
        for k, v in metadata.items()
        if k.startswith(METADATA_PREFIX)
    }


def preserve_cxas_keys(
    old: typing.Mapping[str, str] | None, new: typing.Mapping[str, str] | None
) -> dict[str, str]:
    """Returns ``new`` metadata with all ``cxas.*`` keys taken from ``old``.

    Use on push so a local app.json (which may carry stale or no collab
    keys) never overwrites server-side collaboration state.
    """
    merged = {
        k: v
        for k, v in dict(new or {}).items()
        if not k.startswith(METADATA_PREFIX)
    }
    for k, v in dict(old or {}).items():
        if k.startswith(METADATA_PREFIX):
            merged[k] = v
    return merged


def _adc_email() -> str | None:
    """Email of the Application Default Credentials identity, if known."""
    import google.auth  # noqa: PLC0415
    import google.auth.transport.requests  # noqa: PLC0415

    creds, _ = google.auth.default(
        scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"]
    )
    email = getattr(creds, "service_account_email", None)
    if email and "@" in email and email != "default":
        return email
    request = google.auth.transport.requests.Request()
    if not getattr(creds, "token", None) or not getattr(creds, "valid", False):
        creds.refresh(request)
    resp = request(
        url="https://oauth2.googleapis.com/tokeninfo",
        method="POST",
        body=f"access_token={creds.token}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if resp.status != 200:
        return None
    email = json.loads(resp.data).get("email")
    return email if email and "@" in email else None


def _gcloud_account() -> str | None:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return None
    try:
        out = subprocess.run(
            [gcloud, "config", "get-value", "account"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and "@" in out else None


@functools.lru_cache(maxsize=1)
def _detect_user() -> str:
    try:
        email = _adc_email()
        if email:
            return email
    except Exception as e:  # noqa: BLE001
        logger.debug("ADC identity lookup failed: %s", e)
    account = _gcloud_account()
    if account:
        return account
    try:
        return os.environ.get("USER") or getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


def current_user() -> str:
    """Best-effort identity of the caller.

    Order: ``$CXAS_USER`` override, ADC identity email (matches the platform
    changelog ``author``), active gcloud account, then ``$USER``/login name.
    """
    override = os.environ.get("CXAS_USER")
    if override:
        return override
    return _detect_user()


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class AppMetadataStore:
    """CAS-safe key/value store over ``App.metadata`` (``cxas.*`` keys)."""

    def __init__(
        self,
        app_name: str,
        apps: Apps | None = None,
        max_retries: int = 5,
        retry_backoff: float = 0.2,
        user: str | None = None,
    ) -> None:
        self.app_name = app_name
        if apps is None:
            from cxas_scrapi.core.apps import Apps  # noqa: PLC0415
            from cxas_scrapi.core.common import Common  # noqa: PLC0415

            project_id = Common._get_project_id(app_name)
            location = Common._get_location(app_name)
            if not project_id or not location:
                raise ValueError(
                    "app_name must be a full resource name "
                    "(projects/.../locations/.../apps/...)."
                )
            apps = Apps(project_id=project_id, location=location)
        self.apps = apps
        self.max_retries = max(1, max_retries)
        self.retry_backoff = retry_backoff
        self._user = user
        self._atomic_lock_writes = True

    @property
    def user(self) -> str:
        """Identity used to decide whether we hold the edit lock."""
        if self._user is None:
            self._user = current_user()
        return self._user

    # ---- low level -------------------------------------------------------

    def _fetch(self) -> types.App:
        return self.apps.get_app(self.app_name)

    def _write(
        self,
        etag: str,
        metadata: dict[str, str] | None = None,
        locked: bool | None = None,
    ) -> types.App:
        app = types.App(name=self.app_name)
        paths = []
        if metadata is not None:
            app.metadata = metadata
            paths.append("metadata")
        if locked is not None:
            app.locked = locked
            paths.append("locked")
        if etag:
            app.etag = etag
        request = types.UpdateAppRequest(
            app=app,
            update_mask=field_mask_pb2.FieldMask(paths=paths),
        )
        return self.apps.client.update_app(request=request)

    def _relock_best_effort(self, attempts: int = 3) -> None:
        for i in range(attempts):
            try:
                app = self._fetch()
                if not app.locked:
                    self._write(app.etag, locked=True)
                return
            except Exception as e:  # noqa: BLE001
                if i == attempts - 1:
                    logger.warning(
                        "Could not restore native lock on %s: %s",
                        self.app_name,
                        e,
                    )
                else:
                    time.sleep(self.retry_backoff)

    def update(
        self,
        fn: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        native_lock: bool | None = None,
        check_lock: bool = True,
    ) -> dict[str, Any]:
        """Atomically read-modify-writes the decoded ``cxas.*`` dict."""
        last_exc: Exception | None = None
        restore_native: bool | None = None
        for attempt in range(self.max_retries):
            app = self._fetch()
            raw = dict(app.metadata)
            current = _decode_cxas(raw)
            if check_lock and app.locked:
                held = self._active_lock(current.get(LOCK_KEY))
                if held and held.get("by") != self.user:
                    raise LockHeldError(held)
            result = fn(current)
            if result is None:
                result = current
            new_meta = {
                k: v
                for k, v in raw.items()
                if not k.startswith(METADATA_PREFIX)
            }
            for k, v in result.items():
                if v is None:
                    continue
                new_meta[_full_key(k)] = _encode(v)

            if restore_native is None:
                restore_native = bool(app.locked)
                if restore_native and self._is_stale_lock(
                    current.get(LOCK_KEY)
                ):
                    restore_native = False
            target_locked = (
                restore_native if native_lock is None else native_lock
            )
            meta_changed = new_meta != raw
            if not meta_changed and bool(app.locked) == target_locked:
                return result

            try:
                if self._atomic_lock_writes:
                    with_lock = bool(app.locked) or target_locked
                    try:
                        self._write(
                            app.etag,
                            metadata=new_meta if meta_changed else None,
                            locked=target_locked if with_lock else None,
                        )
                        return result
                    except gexc.FailedPrecondition as e:
                        if "locked" not in str(e).lower():
                            raise
                        logger.debug(
                            "Atomic metadata+locked write refused: %s", e
                        )
                        self._atomic_lock_writes = False
                self._write_with_unlock(
                    app, new_meta if meta_changed else None, target_locked
                )
                return result
            except Exception as e:
                if not isinstance(e, _RETRYABLE):
                    raise
                last_exc = e
                logger.debug(
                    "etag conflict on %s (attempt %d): %s",
                    self.app_name,
                    attempt + 1,
                    e,
                )
                time.sleep(self.retry_backoff * (2**attempt))
        raise ConcurrentModificationError(
            f"Could not update metadata for {self.app_name} after "
            f"{self.max_retries} attempts: {last_exc}"
        )

    def _write_with_unlock(
        self,
        app: types.App,
        new_meta: dict[str, str] | None,
        target_locked: bool,
    ) -> None:
        """Legacy path: clear ``App.locked``, write, restore it."""
        unlocked_by_us = False
        try:
            etag = app.etag
            if app.locked and (new_meta is not None or not target_locked):
                etag = self._write(etag, locked=False).etag
                unlocked_by_us = True
            if new_meta is not None:
                etag = self._write(etag, metadata=new_meta).etag
            if target_locked and (unlocked_by_us or not app.locked):
                self._write(etag, locked=True)
        except Exception:
            if unlocked_by_us and target_locked:
                self._relock_best_effort()
            raise

    # ---- generic ---------------------------------------------------------

    def all(self) -> dict[str, Any]:
        """Returns all ``cxas.*`` values (prefix stripped, decoded)."""
        return _decode_cxas(self._fetch().metadata)

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._fetch().metadata
        full = _full_key(key)
        if full not in raw:
            return default
        return _decode(raw[full])

    def set(self, key: str, value: Any) -> None:
        short = _full_key(key)[len(METADATA_PREFIX) :]

        def _fn(d: dict[str, Any]) -> None:
            d[short] = value

        self.update(_fn)

    def delete(self, key: str) -> None:
        short = _full_key(key)[len(METADATA_PREFIX) :]

        def _fn(d: dict[str, Any]) -> None:
            d.pop(short, None)

        self.update(_fn)

    # ---- lock ------------------------------------------------------------

    @staticmethod
    def _active_lock(lock: Any) -> dict[str, Any] | None:
        if not isinstance(lock, dict) or not lock.get("by"):
            return None
        until = lock.get("until")
        if until:
            try:
                if parse_iso(until) <= _now():
                    return None
            except ValueError:
                return None
        return lock

    @classmethod
    def _is_stale_lock(cls, lock: Any) -> bool:
        """True if a cxas lock entry exists but has expired."""
        return bool(lock) and cls._active_lock(lock) is None

    def lock_state(self) -> dict[str, Any]:
        """Full lock picture in one read."""
        app = self._fetch()
        raw = _decode_cxas(app.metadata).get(LOCK_KEY)
        native = bool(app.locked)
        return {
            "lock": self._active_lock(raw),
            "native_locked": native,
            "stale": native and self._is_stale_lock(raw),
        }

    def clear_stale_lock(self) -> bool:
        """Removes an expired cxas lock and its leftover ``App.locked``."""
        state = self.lock_state()
        if state["lock"] is not None:
            return False
        raw_lock = self.get(LOCK_KEY)
        if not raw_lock:
            return False

        def _fn(d: dict[str, Any]) -> None:
            if self._is_stale_lock(d.get(LOCK_KEY)):
                d.pop(LOCK_KEY, None)

        self.update(
            _fn,
            native_lock=False if state["native_locked"] else None,
            check_lock=False,
        )
        return True

    def get_lock(self) -> dict[str, Any] | None:
        """Returns the active lock or None if unlocked/expired."""
        return self._active_lock(self.get(LOCK_KEY))

    def is_locked_for(self, user: str) -> bool:
        """True if an active lock is held by someone other than ``user``."""
        lock = self.get_lock()
        return bool(lock and lock.get("by") != user)

    def is_natively_locked(self) -> bool:
        """True if the platform ``App.locked`` flag is set."""
        return bool(self._fetch().locked)

    def acquire_lock(
        self,
        user: str,
        ttl_minutes: int = 60,
        reason: str = "",
        force: bool = False,
        native: bool = True,
    ) -> dict[str, Any]:
        """Takes the edit lock for ``user`` and sets ``App.locked``."""
        if ttl_minutes is None or ttl_minutes <= 0:
            raise ValueError(
                f"Lock TTL must be a positive number of minutes "
                f"(got {ttl_minutes})."
            )
        new_lock = {
            "by": user,
            "until": _to_iso(_now() + datetime.timedelta(minutes=ttl_minutes)),
            "reason": reason or "",
        }

        def _fn(d: dict[str, Any]) -> None:
            existing = self._active_lock(d.get(LOCK_KEY))
            if existing and existing.get("by") != user and not force:
                raise LockHeldError(existing)
            d[LOCK_KEY] = new_lock

        self.update(_fn, native_lock=True if native else None, check_lock=False)
        return new_lock

    def release_lock(self, user: str, force: bool = False) -> bool:
        """Clears ``App.locked`` and deletes ``cxas.lock``."""
        app = self._fetch()
        if not app.locked and LOCK_KEY not in _decode_cxas(app.metadata):
            return False

        def _fn(d: dict[str, Any]) -> None:
            existing = self._active_lock(d.get(LOCK_KEY))
            if existing and existing.get("by") != user and not force:
                raise LockHeldError(
                    existing,
                    f"Lock is held by {existing.get('by')}; use force to "
                    "release it.",
                )
            d.pop(LOCK_KEY, None)

        self.update(_fn, native_lock=False, check_lock=False)
        return True

    # ---- last_push / parent / team / expiry ------------------------------

    def get_last_push(self) -> dict[str, Any] | None:
        val = self.get(LAST_PUSH_KEY)
        return val if isinstance(val, dict) else None

    def set_last_push(
        self,
        seq: int,
        *,
        git_sha: str | None = None,
        by: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "seq": int(seq),
            "by": by or self.user,
            "at": at or _to_iso(_now()),
        }
        if git_sha:
            payload["git_sha"] = git_sha
        self.set(LAST_PUSH_KEY, payload)
        return payload

    def get_parent(self) -> dict[str, Any] | None:
        parent = self.get(PARENT_KEY)
        return parent if isinstance(parent, dict) else None

    def set_parent(self, app_name: str, changelog_seq: int) -> None:
        self.set(
            PARENT_KEY,
            {"app_name": app_name, "changelog_seq": int(changelog_seq)},
        )

    def get_team(self) -> str | None:
        return self.get(TEAM_KEY)

    def set_team(self, team: str) -> None:
        self.set(TEAM_KEY, team)

    def get_expiry(self) -> str | None:
        return self.get(EXPIRY_KEY)

    def set_expiry(self, iso: str) -> None:
        self.set(EXPIRY_KEY, iso)
