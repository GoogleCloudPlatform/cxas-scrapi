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

"""Snapshot testing: run evals against a frozen app version.

When one teammate pushes to a shared app while another runs tests, the test
run can silently mix two app versions (half the sessions hit the old draft,
half the new one). This module fixes that by:

* ``freeze_snapshot`` -- creating an immutable app version plus a per-user
  deployment (``cxas-test-snapshot-<user>``) pointing at it, so sessions run
  with ``deployment_id`` and platform goldens run with ``app_version`` see one
  consistent version.
* ``RunContext`` -- recording which version a run used (or
  ``"draft (unfrozen)"``), stamping it onto results, and warning when the
  app's design changed during (or up to ``DRAFT_PROPAGATION_LAG`` before) a
  draft run that executed session-based tests ("results may mix versions").
  Platform golden runs are pinned to the app config at trigger time by the
  platform, so they never get that warning.
* An informational notice when someone else holds the app lock.

Everything here is opt-in and best-effort: failures in stamping/guarding
never fail the test run itself.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import functools
import json
import os
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DRAFT_LABEL = "draft (unfrozen)"
LOCAL_LABEL = "local files"
SNAPSHOT_DEPLOYMENT_PREFIX = "cxas-test-snapshot"
SNAPSHOT_VERSION_PREFIX = "test-snapshot"
# Draft edits take ~1-3+ min to reach serving, so an edit made shortly
# *before* a session-based draft run can still leak into its sessions.
DRAFT_PROPAGATION_LAG = datetime.timedelta(minutes=3)
MIXED_VERSIONS_WARNING = (
    "WARNING: the app design changed during this draft run or within "
    "{lag_min} min before it started (changelog seq {seqs}; {when}). Draft "
    "edits take ~1-3 min to reach serving, so session-based results "
    "({categories}) may mix versions (possible, not confirmed). Re-run with "
    "--snapshot to test a frozen version."
)
GOLDENS_PINNED_INFO = (
    "Note: the app design changed during this run (changelog seq {start} -> "
    "{end}), but platform golden/evaluation runs are pinned by the platform "
    "to the app config at trigger time, so their results are not mixed."
)
LABEL_NOT_APPLIED_NOTE = (
    "Note: --snapshot-label '{label}' was not applied: the draft is "
    "unchanged, so the platform reused existing version {version} (its "
    "description is kept). The requested label is recorded in the run "
    "context (run_context.snapshot_label)."
)
SNAPSHOT_HINT = (
    "Tip: tests are running against the live draft. Session-based tests "
    "(simulations, shadow/turn evals, tool tests) can mix versions if "
    "teammates push meanwhile (draft edits take ~1-3 min to propagate); use "
    "--snapshot to freeze a version first. Platform golden runs are pinned "
    "at trigger time; --snapshot only adds a version stamp for them."
)
LABEL_WITHOUT_SNAPSHOT_ERROR = (
    "--snapshot-label requires --snapshot (the label is stored on the "
    "frozen snapshot version; without --snapshot nothing is frozen)."
)
TOOL_TESTS_DRAFT_NOTE = (
    "Note: tool tests run against the draft (platform can't pin them: the "
    "tool-execution API has no version/deployment field). Only "
    "simulations, shadow evals and goldens used {version}."
)
UNPINNED_CHANGED_WARNING = (
    "WARNING: the app design changed after the snapshot was frozen or within "
    "{lag_min} min before the {categories} tests started (changelog seq "
    "{seqs}; {when}). {categories} tests run on the draft (they can't be "
    "pinned to {version}), so their results may not match the frozen version "
    "(possible, not confirmed)."
)
RUN_CONTEXT_FILENAME = "run_context.json"
# Context started by a CLI wrapper, shared with nested `run_all_evals` calls.
_ACTIVE_CONTEXT: dict[str, Any] = {}
_MAX_ID_LEN = 63

# Categories of `run_all_evals` results and which app state they exercise.
# Sessions-based categories honour deployment_id / app_version; tool tests
# execute on the draft; callback tests run locally on the app directory.
_SESSION_CATEGORIES = ("simulation", "shadow", "golden")
_DRAFT_CATEGORIES = ("tool",)
_LOCAL_CATEGORIES = ("callback",)
# Categories that execute against the live draft session-by-session (so a
# mid-run design change can split their results across versions).
_LIVE_DRAFT_CATEGORIES = ("simulation", "shadow", "tool")
# Platform EvaluationRuns: pinned to the app config at trigger time.
_PLATFORM_CATEGORIES = ("golden",)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _current_user() -> str:
    try:
        from cxas_scrapi.collab.metadata import current_user  # noqa: PLC0415

        return current_user()
    except Exception:  # noqa: BLE001
        return os.environ.get("CXAS_USER") or os.environ.get("USER") or "user"


def _slug(value: str, max_len: int = _MAX_ID_LEN) -> str:
    """Lowercase [a-z0-9-] slug usable in resource IDs."""
    local = value.split("@", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    return (slug or "user")[:max_len].strip("-")


def snapshot_deployment_id(user: str | None = None) -> str:
    """Returns the per-user reusable snapshot deployment id."""
    user = user or _current_user()
    room = _MAX_ID_LEN - len(SNAPSHOT_DEPLOYMENT_PREFIX) - 1
    return f"{SNAPSHOT_DEPLOYMENT_PREFIX}-{_slug(user, room)}"


def snapshot_version_display_name(
    user: str | None = None, now: datetime.datetime | None = None
) -> str:
    """Returns ``test-snapshot-<YYYYmmdd-HHMMSS>-<user>``."""
    user = user or _current_user()
    ts = (now or _utcnow()).strftime("%Y%m%d-%H%M%S")
    base = f"{SNAPSHOT_VERSION_PREFIX}-{ts}"
    room = _MAX_ID_LEN - len(base) - 1
    return f"{base}-{_slug(user, room)}"


def _is_not_found(exc: BaseException) -> bool:
    try:
        from google.api_core import exceptions as gexc  # noqa: PLC0415

        if isinstance(exc, gexc.NotFound):
            return True
    except ImportError:  # pragma: no cover
        pass
    return "404" in str(exc) or "not found" in str(exc).lower()


def _is_locked_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "locked" in msg:
        return True
    try:
        from google.api_core import exceptions as gexc  # noqa: PLC0415

        if isinstance(exc, gexc.FailedPrecondition) and "lock" in msg:
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


def _resource_id(name: str | None) -> str | None:
    return name.rsplit("/", 1)[-1] if name else None


# ---------------------------------------------------------------------------
# Freezing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SnapshotInfo:
    """Result of ``freeze_snapshot_info``."""

    app_name: str
    version_name: str
    version_id: str
    version_display_name: str
    deployment_id: str
    deployment_name: str
    user: str
    created_deployment: bool = False
    # The platform dedups create_version when the draft is unchanged and
    # returns the existing latest version (our name/label are then ignored).
    reused_version: bool = False
    pruned_versions: list[str] = dataclasses.field(default_factory=list)


def freeze_snapshot_info(
    app_name: str,
    label: str | None = None,
    user: str | None = None,
    versions: Any = None,
    deployments: Any = None,
    keep: int | None = 10,
) -> SnapshotInfo:
    """Creates an app version + points the user's snapshot deployment at it.

    Args:
        app_name: Full app resource name.
        label: Free-form label stored in the version description.
        user: Identity used for naming; defaults to ``current_user()``.
        versions: Optional ``Versions`` client (for tests / reuse).
        deployments: Optional ``Deployments`` client (for tests / reuse).
        keep: Prune this user's older ``test-snapshot-*`` versions beyond the
            newest ``keep`` (never the deployed one). ``None`` disables.
    """
    user = user or _current_user()
    if versions is None:
        from cxas_scrapi.core.versions import Versions  # noqa: PLC0415

        versions = Versions(app_name=app_name)
    if deployments is None:
        from cxas_scrapi.core.deployments import Deployments  # noqa: PLC0415

        deployments = Deployments(app_name=app_name)

    deployment_id = snapshot_deployment_id(user)
    created = False
    existing_dep = None
    try:
        existing_dep = deployments.get_deployment(deployment_id)
        exists = True
    except Exception as e:  # noqa: BLE001
        if not _is_not_found(e):
            raise
        exists = False

    # Pre-prune older snapshot versions BEFORE create_version so apps near
    # CES's 200-version cap free a slot ahead of creating the new snapshot.
    pruned_before: list[str] = []
    if keep is not None:
        protect_before: set[str] = set()
        current_dep_ver = (
            getattr(existing_dep, "app_version", None)
            if existing_dep is not None
            else None
        )
        if current_dep_ver:
            protect_before.add(current_dep_ver)
        with contextlib.suppress(Exception):
            pruned_before = prune_snapshot_versions(
                versions,
                user=user,
                keep=max(keep - 1, 0),
                protect=protect_before,
            )

    display_name = snapshot_version_display_name(user)
    description = f"cxas test snapshot by {user}"
    if label:
        description += f": {label}"
    try:
        version = versions.create_version(
            display_name=display_name, description=description
        )
    except Exception as exc:
        if _is_locked_error(exc):
            raise RuntimeError(
                f"Cannot create snapshot version on locked app '{app_name}': "
                "CES CreateAppVersion cannot run on a natively locked app. "
                "Unlock the app first (`cxas unlock`) or run `--snapshot` "
                "against an unlocked dev/workspace app."
            ) from exc
        raise
    version_name = version.name
    version_id = _resource_id(version_name) or ""

    if exists:
        deployment = deployments.update_deployment(
            deployment_id=deployment_id, app_version=version_name
        )
    else:
        deployment = deployments.create_deployment(
            deployment_id=deployment_id,
            display_name=deployment_id,
            app_version=version_name,
        )
        created = True

    info = SnapshotInfo(
        app_name=app_name,
        version_name=version_name,
        version_id=version_id,
        version_display_name=getattr(version, "display_name", "")
        or display_name,
        deployment_id=deployment_id,
        deployment_name=getattr(deployment, "name", "")
        or f"{app_name}/deployments/{deployment_id}",
        user=user,
        created_deployment=created,
        reused_version=bool(getattr(version, "display_name", ""))
        and version.display_name != display_name,
        pruned_versions=list(pruned_before),
    )
    if keep is not None:
        with contextlib.suppress(Exception):
            pruned_after = prune_snapshot_versions(
                versions, user=user, keep=keep, protect={version_name}
            )
            for v in pruned_after:
                if v not in info.pruned_versions:
                    info.pruned_versions.append(v)
    return info


def freeze_snapshot(
    app_name: str, label: str | None = None, **kwargs: Any
) -> tuple[str, str]:
    """Freezes the app and returns ``(version_id, deployment_id)``."""
    info = freeze_snapshot_info(app_name, label=label, **kwargs)
    return info.version_id, info.deployment_id


def prune_snapshot_versions(
    versions: Any,
    user: str,
    keep: int = 10,
    protect: set[str] | None = None,
) -> list[str]:
    """Deletes this user's oldest ``test-snapshot-*-<user>`` versions.

    Only versions whose display name matches our naming scheme *and* the
    user's slug are considered; ``protect`` names are never deleted.
    """
    protect = protect or set()
    user_slug = snapshot_version_display_name(user).split("-", 4)[-1]
    pattern = re.compile(
        rf"^{SNAPSHOT_VERSION_PREFIX}-\d{{8}}-\d{{6}}-{re.escape(user_slug)}$"
    )
    mine = [
        v
        for v in versions.list_versions()
        if pattern.match(getattr(v, "display_name", "") or "")
    ]
    mine.sort(key=lambda v: v.display_name, reverse=True)  # ts in name
    deleted = []
    for v in mine[keep:]:
        if v.name in protect:
            continue
        try:
            versions.delete_version(_resource_id(v.name))
            deleted.append(v.name)
        except Exception:  # noqa: BLE001, PERF203
            # Still referenced by a deployment, or no permission: skip.
            continue
    return deleted


# ---------------------------------------------------------------------------
# Version pinning for platform goldens
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def pinned_app_version(app_version: str | None) -> Iterator[None]:
    """Makes ``Evaluations.run_evaluation`` evaluate ``app_version``.

    ``RunEvaluationRequest.app_version`` exists in the API but
    ``Evaluations.run_evaluation`` does not expose it; this scoped wrapper
    sets it on outgoing requests (only when not already set).
    """
    if not app_version:
        yield
        return
    from cxas_scrapi.core.evaluations import Evaluations  # noqa: PLC0415

    original = Evaluations.run_evaluation

    @functools.wraps(original)
    def _run(self: Any, *args: Any, **kwargs: Any) -> Any:
        client = self.client
        had_attr = "run_evaluation" in vars(client)
        inner = client.run_evaluation

        def _client_run(*cargs: Any, **ckwargs: Any) -> Any:
            request = ckwargs.get("request")
            if request is not None and not getattr(
                request, "app_version", None
            ):
                request.app_version = app_version
            return inner(*cargs, **ckwargs)

        client.run_evaluation = _client_run
        try:
            return original(self, *args, **kwargs)
        finally:
            if had_attr:
                client.run_evaluation = inner
            else:
                with contextlib.suppress(AttributeError):
                    del client.run_evaluation

    Evaluations.run_evaluation = _run
    try:
        yield
    finally:
        Evaluations.run_evaluation = original


# ---------------------------------------------------------------------------
# Changelog / lock helpers
# ---------------------------------------------------------------------------


_NON_CONFIG_SEGMENTS = (
    "/evaluations/",
    "/evaluationRuns/",
    "/evaluationDatasets/",
    "/evaluationExpectations/",
    "/scheduledEvaluationRuns/",
    "/versions/",
    "/deployments/",
    "/conversations/",
    "/changelogs/",
)
_NON_CONFIG_WORDS = ("evaluation", "appversion", "deployment", "conversation")
# App fields whose updates don't change agent behaviour (team locks/owners).
_NON_BEHAVIOUR_APP_FIELDS = {"metadata", "locked"}


def is_config_change(entry: Any) -> bool:
    """True if a changelog entry changes what the agent does.

    Test runs themselves upsert evaluations, and freezing creates versions /
    deployments; metadata-only app updates (locks, owners) don't change
    behaviour either. None of those should trigger the mixed-version warning.
    """
    resource = str(getattr(entry, "resource", "") or "")
    if any(seg in resource for seg in _NON_CONFIG_SEGMENTS):
        return False
    kind = (
        (
            f"{getattr(entry, 'resource_type', '') or ''} "
            f"{getattr(entry, 'action', '') or ''}"
        )
        .lower()
        .replace(" ", "")
    )
    if any(word in kind for word in _NON_CONFIG_WORDS):
        return False
    description = str(getattr(entry, "description", "") or "").strip()
    match = re.match(r"(?i)^update app fields:\s*(.+)$", description)
    if match:
        fields = {f.strip().lower() for f in match.group(1).split(",")}
        if fields <= _NON_BEHAVIOUR_APP_FIELDS:
            return False
    return True


def list_config_changes(
    app_name: str, changelogs: Any = None, config_only: bool = True
) -> list[Any] | None:
    """Changelog entries with an int sequence number (None if unknown)."""
    try:
        if changelogs is None:
            from cxas_scrapi.core.changelogs import (  # noqa: PLC0415
                Changelogs,
            )

            changelogs = Changelogs(app_name=app_name)
        return [
            c
            for c in changelogs.list_changelogs()
            if isinstance(getattr(c, "sequence_number", None), int)
            and (not config_only or is_config_change(c))
        ]
    except Exception:  # noqa: BLE001
        return None


def latest_changelog_seq(
    app_name: str, changelogs: Any = None, config_only: bool = True
) -> int | None:
    """Returns the highest changelog sequence number, or None if unknown.

    With ``config_only`` (default) only entries that change agent behaviour
    count (see ``is_config_change``).
    """
    entries = list_config_changes(app_name, changelogs, config_only)
    if entries is None:
        return None
    return max((int(c.sequence_number) for c in entries), default=0)


def _entry_time(entry: Any) -> datetime.datetime | None:
    ts = getattr(entry, "create_time", None)
    if not isinstance(ts, datetime.datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts


def _parse_iso(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def foreign_lock_notice(
    app_name: str, store: Any = None, user: str | None = None
) -> str | None:
    """Returns a one-line notice if someone else holds the app lock."""
    try:
        if store is None:
            from cxas_scrapi.collab.metadata import (  # noqa: PLC0415
                AppMetadataStore,
            )

            store = AppMetadataStore(app_name)
        lock = store.get_lock()
        if not isinstance(lock, dict) or not lock.get("by"):
            return None
        user = user or _current_user()
        if lock["by"] == user:
            return None
        reason = f" ({lock['reason']})" if lock.get("reason") else ""
        return (
            f"Note: app is locked by {lock['by']} until "
            f"{lock.get('until', '?')}{reason}. Running tests anyway "
            "(informational)."
        )
    except Exception:  # noqa: BLE001
        return None


def resolve_deployment_version(
    app_name: str, deployment_id: str, deployments: Any = None
) -> tuple[str | None, str | None]:
    """Returns ``(version_name, display_name)`` a deployment points at."""
    try:
        if deployments is None:
            from cxas_scrapi.core.deployments import (  # noqa: PLC0415
                Deployments,
            )

            deployments = Deployments(app_name=app_name)
        dep = deployments.get_deployment(_resource_id(deployment_id))
        version_name = getattr(dep, "app_version", None) or None
        if not isinstance(version_name, str):
            return None, None
        display = None
        with contextlib.suppress(Exception):
            from cxas_scrapi.core.versions import Versions  # noqa: PLC0415

            v = Versions(app_name=app_name).get_version(
                _resource_id(version_name)
            )
            display = getattr(v, "display_name", None) or None
        return version_name, display if isinstance(display, str) else None
    except Exception:  # noqa: BLE001
        return None, None


# ---------------------------------------------------------------------------
# Run context (stamping + mixed-version guard)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunContext:
    """Tracks which app version a test run used."""

    app_name: str | None
    frozen: bool = False
    app_version: str | None = None
    app_version_display_name: str | None = None
    deployment_id: str | None = None
    changelog_seq_start: int | None = None
    changelog_seq_end: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    lock_notice: str | None = None
    warning: str | None = None
    info: str | None = None
    snapshot: SnapshotInfo | None = None
    snapshot_label: str | None = None
    # Categories that actually executed (see ``mark_ran``).
    categories_run: set[str] = dataclasses.field(default_factory=set)
    # First time each category started (ISO), and when the snapshot froze.
    category_started: dict[str, str] = dataclasses.field(default_factory=dict)
    frozen_at: str | None = None
    _changelogs: Any = dataclasses.field(default=None, repr=False)

    def mark_ran(self, category: str) -> None:
        """Records that tests of ``category`` executed in this run.

        Used to decide whether a design change during a draft run can have
        mixed versions: only live-draft, session-by-session categories
        (simulation / shadow / tool) can; platform goldens are pinned by the
        platform at trigger time.
        """
        self.categories_run.add(category)
        self.category_started.setdefault(category, _utcnow().isoformat())

    @property
    def live_draft_categories(self) -> list[str]:
        return [c for c in _LIVE_DRAFT_CATEGORIES if c in self.categories_run]

    @property
    def unpinned_categories(self) -> list[str]:
        """Categories that ran on the draft although a version was pinned."""
        if self.is_draft:
            return []
        return [c for c in _DRAFT_CATEGORIES if c in self.categories_run]

    @property
    def is_draft(self) -> bool:
        return not self.frozen and not self.app_version

    @property
    def version_label(self) -> str:
        if self.is_draft:
            return DRAFT_LABEL
        return self.app_version_display_name or self.app_version or "?"

    @property
    def pinned_version(self) -> str | None:
        """Version to pin platform goldens to (snapshot mode only)."""
        return self.app_version if self.frozen else None

    def stamp(self) -> dict[str, Any]:
        """JSON-serialisable record of the run's version."""
        snap = self.snapshot
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "app_version_id": _resource_id(self.app_version),
            "app_version_display_name": self.version_label,
            "deployment_id": self.deployment_id,
            "frozen": self.frozen,
            "snapshot_label": self.snapshot_label,
            "snapshot_label_applied": (
                None
                if not (snap and self.snapshot_label)
                else not snap.reused_version
            ),
            "snapshot_reused_version": bool(snap and snap.reused_version),
            "changelog_seq_start": self.changelog_seq_start,
            "changelog_seq_end": self.changelog_seq_end,
            "possible_mixed_versions": bool(self.warning),
            # Deprecated alias of possible_mixed_versions (kept for compat).
            "mixed_versions": bool(self.warning),
            "warning": self.warning,
            "info": self.info,
            "categories_run": sorted(self.categories_run),
            "unpinned_categories": self.unpinned_categories,
            "lock_notice": self.lock_notice,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "categories": {
                **dict.fromkeys(_SESSION_CATEGORIES, self.version_label),
                **dict.fromkeys(_DRAFT_CATEGORIES, DRAFT_LABEL),
                **dict.fromkeys(_LOCAL_CATEGORIES, LOCAL_LABEL),
            },
        }

    def _recent_changes(
        self,
        entries: list[Any],
        window_start: datetime.datetime | None = None,
    ) -> list[Any]:
        """Config changes from ``window_start`` (default ``started - lag``).

        Entries without a ``create_time`` fall back to "seq moved past the
        baseline" (i.e. changed during the run).
        """
        if window_start is None:
            started = _parse_iso(self.started_at)
            window_start = (
                started - DRAFT_PROPAGATION_LAG if started is not None else None
            )
        recent = []
        for e in entries:
            ts = _entry_time(e)
            if ts is not None and window_start is not None:
                if ts >= window_start:
                    recent.append(e)
            elif (
                self.changelog_seq_start is not None
                and int(e.sequence_number) > self.changelog_seq_start
            ):
                recent.append(e)
        return sorted(recent, key=lambda e: int(e.sequence_number))

    def _describe(
        self, changes: list[Any], ref: datetime.datetime | None = None
    ) -> tuple[str, str]:
        seqs = ", ".join(str(int(e.sequence_number)) for e in changes)
        started = ref or _parse_iso(self.started_at)
        parts = []
        for e in changes:
            ts = _entry_time(e)
            if ts is None:
                continue
            rel = ""
            if started is not None:
                delta = (ts - started).total_seconds()
                rel = (
                    f" ({int(-delta)}s before start)"
                    if delta < 0
                    else f" ({int(delta)}s after start)"
                )
            parts.append(f"{ts.strftime('%H:%M:%S')}Z{rel}")
        when = "at " + ", ".join(parts) if parts else "during the run"
        return seqs, when

    def finish(self) -> dict[str, Any]:
        """Closes the run: checks for mixed versions, returns the stamp.

        Only live-draft, session-based categories get the mixed-versions
        warning, with the detection window widened by
        ``DRAFT_PROPAGATION_LAG`` before the start. Platform goldens are
        pinned at trigger time, so a change during a goldens-only run gets
        an informational note instead.
        """
        self.finished_at = _utcnow().isoformat()
        if not self.is_draft:
            self._check_unpinned()
            return self.stamp()
        if not self.app_name:
            return self.stamp()
        entries = list_config_changes(self.app_name, self._changelogs)
        if entries is None:
            return self.stamp()
        self.changelog_seq_end = max(
            (int(e.sequence_number) for e in entries), default=0
        )
        live = self.live_draft_categories
        if live:
            changes = self._recent_changes(entries)
            if changes:
                seqs, when = self._describe(changes)
                self.warning = MIXED_VERSIONS_WARNING.format(
                    lag_min=int(DRAFT_PROPAGATION_LAG.total_seconds() // 60),
                    seqs=seqs,
                    when=when,
                    categories=", ".join(live),
                )
            return self.stamp()
        start, end = self.changelog_seq_start, self.changelog_seq_end
        platform_only = not self.categories_run or any(
            c in self.categories_run for c in _PLATFORM_CATEGORIES
        )
        if platform_only and start is not None and end != start:
            self.info = GOLDENS_PINNED_INFO.format(start=start, end=end)
        return self.stamp()

    def _check_unpinned(self) -> None:
        """Draft-change check for categories a snapshot can't pin (tools).

        Window: from the freeze (or ``DRAFT_PROPAGATION_LAG`` before those
        tests started, whichever is earlier) up to now.
        """
        unpinned = self.unpinned_categories
        if not (unpinned and self.app_name):
            return
        entries = list_config_changes(self.app_name, self._changelogs)
        if entries is None:
            return
        self.changelog_seq_end = max(
            (int(e.sequence_number) for e in entries), default=0
        )
        starts = [
            t
            for c in unpinned
            if (t := _parse_iso(self.category_started.get(c))) is not None
        ]
        ref = min(starts) if starts else _parse_iso(self.started_at)
        if ref is None:
            return
        window_start = ref - DRAFT_PROPAGATION_LAG
        frozen = _parse_iso(self.frozen_at)
        if frozen is not None and frozen < window_start:
            window_start = frozen
        changes = [
            e
            for e in entries
            if (ts := _entry_time(e)) is not None and ts >= window_start
        ]
        if not changes:
            return
        changes.sort(key=lambda e: int(e.sequence_number))
        seqs, when = self._describe(changes, ref=ref)
        self.warning = UNPINNED_CHANGED_WARNING.format(
            lag_min=int(DRAFT_PROPAGATION_LAG.total_seconds() // 60),
            categories=", ".join(unpinned),
            seqs=seqs,
            when=when.replace(" start)", " tool start)"),
            version=self.version_label,
        )

    def summary_line(self) -> str:
        if self.is_draft:
            return f"App version under test: {DRAFT_LABEL}"
        vid = _resource_id(self.app_version)
        dep = f", deployment {self.deployment_id}" if self.deployment_id else ""
        line = (
            f"App version under test: {self.version_label} (version {vid}{dep})"
        )
        if self.unpinned_categories:
            line += f"; {', '.join(self.unpinned_categories)} tests: draft"
        return line


def begin_run(
    app_name: str | None,
    snapshot: bool = False,
    label: str | None = None,
    deployment_id: str | None = None,
    hint: bool = False,
    printer: Callable[[str], Any] = print,
    changelogs: Any = None,
    lock_store: Any = None,
    **freeze_kwargs: Any,
) -> RunContext:
    """Starts a run: lock notice, optional freeze, draft-run baseline.

    ``snapshot=True`` freezes (errors propagate: the user asked for a frozen
    run). Everything else is best-effort. If a CLI wrapper
    (``with_snapshot_support``) already started a context for this app, it
    is reused so freezing / hints / summaries happen once.
    """
    if label and not snapshot:
        raise ValueError(LABEL_WITHOUT_SNAPSHOT_ERROR)
    active = _ACTIVE_CONTEXT.get("ctx")
    if active is not None and app_name and active.app_name == app_name:
        return active

    ctx = RunContext(
        app_name=app_name,
        deployment_id=deployment_id,
        started_at=_utcnow().isoformat(),
        _changelogs=changelogs,
    )
    if not app_name:
        return ctx

    ctx.lock_notice = foreign_lock_notice(app_name, store=lock_store)
    if ctx.lock_notice:
        printer(ctx.lock_notice)

    if snapshot:
        ctx.frozen_at = _utcnow().isoformat()
        info = freeze_snapshot_info(app_name, label=label, **freeze_kwargs)
        ctx.snapshot = info
        ctx.frozen = True
        ctx.app_version = info.version_name
        ctx.app_version_display_name = info.version_display_name
        ctx.deployment_id = info.deployment_id
        verb = "Created" if info.created_deployment else "Updated"
        what = (
            "Draft unchanged since version"
            if info.reused_version
            else "Froze app as version"
        )
        reuse = "; reusing it" if info.reused_version else ""
        printer(
            f"{what} {info.version_display_name} ({info.version_id}){reuse}; "
            f"{verb} deployment {info.deployment_id}."
        )
        ctx.snapshot_label = label or None
        if label and info.reused_version:
            printer(
                LABEL_NOT_APPLIED_NOTE.format(
                    label=label, version=info.version_display_name
                )
            )
    elif deployment_id:
        ctx.app_version, ctx.app_version_display_name = (
            resolve_deployment_version(app_name, deployment_id)
        )
        # Re-using an existing snapshot deployment: treat as frozen so
        # platform goldens are pinned to the same version.
        dep = _resource_id(deployment_id) or ""
        if ctx.app_version and dep.startswith(SNAPSHOT_DEPLOYMENT_PREFIX):
            ctx.frozen = True
    else:
        ctx.changelog_seq_start = latest_changelog_seq(app_name, changelogs)
        if hint:
            printer(SNAPSHOT_HINT)
    return ctx


def stamp_results(
    results: dict[str, Any], stamp: dict[str, Any]
) -> dict[str, Any]:
    """Adds ``run_context`` + per-record version fields to runner results.

    Existing ``app_version*`` fields on records (e.g. platform golden
    results that already report their version) are kept.
    """
    results["run_context"] = stamp
    categories = stamp.get("categories", {})
    for category, records in results.items():
        if category == "run_context" or not isinstance(records, list):
            continue
        label = categories.get(category, stamp["app_version_display_name"])
        version = (
            stamp.get("app_version")
            if category in _SESSION_CATEGORIES
            else None
        )
        for rec in records:
            if isinstance(rec, dict):
                rec.setdefault("app_version", version)
                rec.setdefault("app_version_display_name", label)
    return results


def write_run_context(
    output_dir: str, stamp: dict[str, Any], timestamp: str | None = None
) -> str:
    """Writes the stamp as ``run_context[_<ts>].json`` in ``output_dir``."""
    name = RUN_CONTEXT_FILENAME
    if timestamp:
        base, ext = os.path.splitext(name)
        name = f"{base}_{timestamp}{ext}"
    path = os.path.join(output_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stamp, f, indent=2, default=str)
    return path


def report_finish(
    ctx: RunContext, printer: Callable[[str], Any] = print
) -> dict[str, Any]:
    """``ctx.finish()`` plus console summary / mixed-version warning.

    Idempotent: a context that already finished is not re-checked/printed.
    """
    if ctx.finished_at is not None:
        return ctx.stamp()
    stamp = ctx.finish()
    printer(ctx.summary_line())
    if ctx.unpinned_categories:
        printer(TOOL_TESTS_DRAFT_NOTE.format(version=ctx.version_label))
    if ctx.warning:
        printer(ctx.warning)
    elif ctx.info:
        printer(ctx.info)
    return stamp


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def add_snapshot_args(parser: Any) -> None:
    """Adds ``--snapshot`` / ``--snapshot-label`` to an argparse parser."""
    parser.add_argument(
        "--snapshot",
        action="store_true",
        default=False,
        help=(
            "Freeze the app into a new version + your personal "
            f"'{SNAPSHOT_DEPLOYMENT_PREFIX}-<user>' deployment first, and run "
            "all tests against it. Recommended mainly for session-based tests "
            "(simulations, shadow/turn evals), which otherwise hit the live "
            "draft and can mix versions if teammates push meanwhile. Platform "
            "golden runs are already pinned at trigger time; for them "
            "--snapshot gives a version stamp."
        ),
    )
    parser.add_argument(
        "--snapshot-label",
        default=None,
        help=(
            "Optional label stored in the snapshot version description "
            "(not applied if the draft is unchanged and the platform reuses "
            "the existing version; always recorded in the run context)."
        ),
    )


def _runs_session_tests(args: Any) -> bool:
    """True if a CLI invocation may run live-draft, session-based tests.

    ``cxas run`` (no ``--include``) only triggers platform EvaluationRuns,
    which the platform pins at trigger time. ``evals report`` runs tests
    only with ``--run`` and session-based ones only for sims/shadows/tools.
    """
    include = getattr(args, "include", None)
    if include is None:
        return False
    if not getattr(args, "run", True):
        return False
    if isinstance(include, str):
        include = include.split(",")
    cats = {str(c).strip().lower() for c in include}
    return bool(cats & {"sims", "shadows", "tools", "all"})


def with_snapshot_support(
    handler: Callable[[Any], Any],
) -> Callable[[Any], Any]:
    """Wraps a CLI handler taking ``args`` with snapshot testing.

    Before: rejects ``--snapshot-label`` without ``--snapshot`` (exit 2),
    lock notice, freeze (``--snapshot``) or draft hint (only when
    session-based tests may run). During: platform goldens are pinned to the
    frozen version and, if the parser has ``--deployment-id``, sessions use
    the snapshot deployment. After (also on ``sys.exit``): prints the
    version used and the possible-mixed-version warning.
    """

    @functools.wraps(handler)
    def _wrapped(args: Any) -> Any:
        app_name = getattr(args, "app_name", None)
        snapshot = bool(getattr(args, "snapshot", False))
        label = getattr(args, "snapshot_label", None)
        if label and not snapshot:
            print(f"Error: {LABEL_WITHOUT_SNAPSHOT_ERROR}", file=sys.stderr)
            sys.exit(2)
        ctx = begin_run(
            app_name,
            snapshot=snapshot,
            label=label,
            deployment_id=getattr(args, "deployment_id", None),
            hint=_runs_session_tests(args),
        )
        if ctx.frozen and hasattr(args, "deployment_id"):
            args.deployment_id = ctx.deployment_id
        _ACTIVE_CONTEXT["ctx"] = ctx
        try:
            with pinned_app_version(ctx.pinned_version):
                return handler(args)
        finally:
            _ACTIVE_CONTEXT.pop("ctx", None)
            with contextlib.suppress(Exception):
                report_finish(ctx)

    return _wrapped


__all__ = [
    "DRAFT_LABEL",
    "RunContext",
    "SnapshotInfo",
    "add_snapshot_args",
    "begin_run",
    "foreign_lock_notice",
    "freeze_snapshot",
    "freeze_snapshot_info",
    "latest_changelog_seq",
    "pinned_app_version",
    "prune_snapshot_versions",
    "report_finish",
    "snapshot_deployment_id",
    "snapshot_version_display_name",
    "stamp_results",
    "with_snapshot_support",
    "write_run_context",
]
