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

"""GitOps-native ephemeral CES workspaces.

A *workspace* is an ephemeral CES App spun up from a local Git feature branch
via ``cxas workspace up`` (or an automated ``[CI] PR-*`` app). Workspace
ownership and TTL live in ``App.metadata`` under ``cxas.*`` keys:

* ``cxas.workspace`` -- ``{"owner", "branch", "created"}``
* ``cxas.expiry``    -- ISO-8601 UTC timestamp after which ``cleanup`` deletes
* ``cxas.team``      -- optional team name for filtering
* ``cxas.parent``    -- optional parent app reference (legacy ``cxas branch``)

Any app without workspace metadata (and not matching ``[CI] PR-*``) is *not* a
workspace and is refused by ``delete_workspace`` / ``workspace down``.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cxas_scrapi.collab.metadata import (
    EXPIRY_KEY,
    LOCK_KEY,
    METADATA_PREFIX,
    TEAM_KEY,
    WORKSPACE_KEY,
    AppMetadataStore,
    current_user,
)
from cxas_scrapi.utils.profiles import (
    PROFILES_REL_DIR,
    ResolvedProfile,
    load_profile,
    resolve_project_root,
)

if TYPE_CHECKING:
    from cxas_scrapi.core.apps import Apps

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14.0
DEFAULT_CI_TTL_HOURS = 24.0
CI_PR_PREFIX = "[CI] PR-"
WS_LOCAL_PROFILE_FILENAME = "ws.local.yml"
SHARED_BRANCHES = frozenset(
    {"main", "master", "dev", "qa", "uat", "prod", "production"}
)


class NotAWorkspaceError(ValueError):
    """Raised when attempting to delete an app that is not a workspace."""


class NotOwnerError(PermissionError):
    """Raised when deleting another user's workspace without ``force``."""


class SharedBranchError(ValueError):
    """Raised when ``workspace up`` runs on a shared branch without name."""


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime.datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.datetime.now(datetime.timezone.utc)


def to_iso(dt: datetime.datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC)."""
    return (
        dt.astimezone(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_iso(value: Any) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` suffix allowed); None on failure."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def expiry_from_ttl(
    ttl_days: float, now: datetime.datetime | None = None
) -> str:
    """Return the ISO expiry timestamp ``ttl_days`` from ``now``."""
    now = now or utc_now()
    return to_iso(now + datetime.timedelta(days=ttl_days))


# ---------------------------------------------------------------------------
# Git & naming helpers
# ---------------------------------------------------------------------------


def current_git_branch(cwd: str | Path | None = None) -> str | None:
    """Return the current Git branch name, or None if detached / not a repo."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            branch = res.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _slug(value: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (cleaned or "default")[:max_len].strip("-")


def default_workspace_display_name(user: str, branch: str) -> str:
    """Build deterministic display name ``ws/<user>/<branch-slug>``."""
    local_user = (user or "user").split("@", 1)[0]
    user_slug = _slug(local_user, max_len=24)
    branch_slug = _slug(branch, max_len=40)
    return f"ws/{user_slug}/{branch_slug}"


def default_workspace_profile_filename(branch: str | None) -> str:
    """Return ``ws-<branch-slug>.local.yml`` or ``ws.local.yml``."""
    if branch and branch.strip():
        return f"ws-{_slug(branch, max_len=40)}.local.yml"
    return WS_LOCAL_PROFILE_FILENAME


def is_ci_pr_app_name(display_name: str) -> bool:
    """True when ``display_name`` matches the ``[CI] PR-*`` convention."""
    return display_name.startswith(CI_PR_PREFIX)


# ---------------------------------------------------------------------------
# Metadata decoding & WorkspaceInfo
# ---------------------------------------------------------------------------


def decode_cxas_metadata(app: Any) -> dict[str, Any]:
    """Return ``cxas.*`` keys of ``app.metadata`` (prefix stripped, decoded)."""
    raw = getattr(app, "metadata", None) or {}
    try:
        items = dict(raw).items()
    except (TypeError, ValueError):
        return {}
    result: dict[str, Any] = {}
    for key, value in items:
        if not isinstance(key, str) or not key.startswith(METADATA_PREFIX):
            continue
        short = key[len(METADATA_PREFIX) :]
        try:
            result[short] = json.loads(value)
        except (TypeError, ValueError):
            result[short] = value
    return result


def _timestamp_to_iso(value: Any) -> str:
    """Best-effort conversion of a proto timestamp / datetime to ISO text."""
    if not value:
        return ""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return to_iso(value)
    if hasattr(value, "ToDatetime"):
        try:
            dt = value.ToDatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return to_iso(dt)
        except (ValueError, OverflowError):
            return ""
    return str(value)


@dataclasses.dataclass
class WorkspaceInfo:
    """Collaboration & lifecycle view of one CES app."""

    name: str
    display_name: str
    parent_app: str | None = None
    parent_seq: int | None = None
    team: str | None = None
    owner: str | None = None
    branch: str | None = None
    expiry: str | None = None
    last_change: str = ""
    create_time: str = ""
    is_ci: bool = False

    @property
    def is_workspace(self) -> bool:
        """True if app has ``cxas.parent``, ``cxas.workspace``, or CI PR."""
        return bool(self.parent_app or self.owner or self.is_ci)

    def is_expired(
        self,
        now: datetime.datetime | None = None,
        *,
        ci_ttl_hours: float = DEFAULT_CI_TTL_HOURS,
    ) -> bool:
        """True if workspace expiry (or CI PR age threshold) has passed."""
        ref = now or utc_now()
        expiry_dt = parse_iso(self.expiry)
        if expiry_dt is not None:
            return expiry_dt <= ref
        if self.is_ci:
            ts = parse_iso(self.last_change) or parse_iso(self.create_time)
            if ts is not None:
                return (ref - ts) >= datetime.timedelta(hours=ci_ttl_hours)
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize workspace view for ``--json`` output."""
        data = dataclasses.asdict(self)
        data["is_workspace"] = self.is_workspace
        data["expired"] = self.is_expired()
        return data


def workspace_info_from_app(app: Any) -> WorkspaceInfo:
    """Build a :class:`WorkspaceInfo` from an ``App`` proto."""
    meta = decode_cxas_metadata(app)
    parent = meta.get("parent") if isinstance(meta.get("parent"), dict) else {}
    ws = (
        meta.get(WORKSPACE_KEY)
        if isinstance(meta.get(WORKSPACE_KEY), dict)
        else {}
    )
    seq = parent.get("changelog_seq")
    try:
        seq = int(seq) if seq is not None else None
    except (TypeError, ValueError):
        seq = None
    team = meta.get(TEAM_KEY)
    expiry = meta.get(EXPIRY_KEY)
    display_name = getattr(app, "display_name", "") or ""
    return WorkspaceInfo(
        name=getattr(app, "name", "") or "",
        display_name=display_name,
        parent_app=parent.get("app_name") or None,
        parent_seq=seq,
        team=str(team) if team else None,
        owner=ws.get("owner") or None,
        branch=ws.get("branch") or None,
        expiry=str(expiry) if expiry else None,
        last_change=_timestamp_to_iso(getattr(app, "update_time", None)),
        create_time=_timestamp_to_iso(getattr(app, "create_time", None)),
        is_ci=is_ci_pr_app_name(display_name),
    )


def _user_matches(owner: str | None, user: str | None) -> bool:
    if not owner or not user:
        return False
    owner_l, user_l = owner.lower(), user.lower()
    return owner_l == user_l or owner_l.split("@")[0] == user_l.split("@")[0]


def list_workspaces(
    apps: Apps,
    *,
    parent: str | None = None,
    team: str | None = None,
    owner: str | None = None,
    include_ci: bool = False,
    include_all: bool = False,
) -> list[WorkspaceInfo]:
    """Scan the project's apps and return workspace views."""
    all_apps = list(apps.list_apps())
    by_name = {getattr(a, "name", ""): a for a in all_apps}
    parent_names: set[str] | None = None
    if parent:
        parent_names = {
            name
            for name, a in by_name.items()
            if parent in (name, getattr(a, "display_name", None))
            or name.rsplit("/", 1)[-1] == parent
        } or {parent}

    result: list[WorkspaceInfo] = []
    for app in all_apps:
        info = workspace_info_from_app(app)
        if not include_all:
            if not info.is_workspace:
                continue
            if info.is_ci and not include_ci and not info.owner:
                continue
        if parent_names is not None and info.parent_app not in parent_names:
            continue
        if team and (info.team or "") != team:
            continue
        if owner and not _user_matches(info.owner, owner):
            continue
        result.append(info)
    result.sort(key=lambda i: (i.team or "", i.display_name))
    return result


def find_app(apps: Apps, identifier: str) -> Any:
    """Resolve an app by resource name, trailing id, or display name."""
    if identifier.startswith("projects/"):
        return apps.get_app(app_name=identifier)
    app = apps.get_app_by_display_name(identifier)
    if app:
        return app
    for candidate in apps.list_apps():
        if getattr(candidate, "name", "").rsplit("/", 1)[-1] == identifier:
            return candidate
    raise LookupError(f"App '{identifier}' not found.")


def foreign_owner(info: WorkspaceInfo, user: str | None = None) -> str | None:
    """Return ``info.owner`` if another user owns the workspace, else None."""
    if not info.owner:
        return None
    caller = user or current_user()
    return None if _user_matches(info.owner, caller) else info.owner


def delete_workspace(
    apps: Apps,
    app: Any,
    *,
    user: str | None = None,
    force: bool = False,
) -> WorkspaceInfo:
    """Delete a workspace app, refusing non-workspaces and foreign owners."""
    info = (
        app if isinstance(app, WorkspaceInfo) else workspace_info_from_app(app)
    )
    if not info.is_workspace:
        raise NotAWorkspaceError(
            f"'{info.display_name}' ({info.name}) is not a workspace "
            "(missing cxas.workspace / cxas.parent metadata). "
            "Use `cxas delete` for regular apps."
        )
    owner = foreign_owner(info, user=user)
    if owner and not force:
        raise NotOwnerError(
            f"Workspace '{info.display_name}' is owned by {owner}, not you. "
            "Pass --force to delete another user's workspace."
        )
    app_obj = app
    # CES ListApps omits App.locked (returning False), so always fetch the
    # authoritative GetApp view whenever locked is not already True.
    if getattr(app_obj, "locked", False) is not True:
        try:
            app_obj = apps.get_app(app_name=info.name)
        except Exception:  # noqa: BLE001
            app_obj = None
    if getattr(app_obj, "locked", False) is True:
        caller = user or current_user()

        def _clear_lock(d: dict[str, Any]) -> None:
            d.pop(LOCK_KEY, None)

        AppMetadataStore(info.name, apps=apps, user=caller).update(
            _clear_lock,
            native_lock=False,
            check_lock=False,
        )
    apps.delete_app(app_name=info.name, force=True)
    return info


def expired_workspaces(
    apps: Apps,
    *,
    team: str | None = None,
    owner: str | None = None,
    all_users: bool = False,
    include_ci: bool = True,
    ci_ttl_hours: float = DEFAULT_CI_TTL_HOURS,
    now: datetime.datetime | None = None,
) -> list[WorkspaceInfo]:
    """Return expired workspaces (and optionally expired ``[CI] PR-*`` apps)."""
    effective_owner = None if all_users else (owner or current_user())
    ref = now or utc_now()
    candidates = list_workspaces(
        apps,
        team=team,
        owner=None,
        include_ci=include_ci,
    )
    expired: list[WorkspaceInfo] = []
    for info in candidates:
        if info.is_ci and not info.owner:
            if not include_ci:
                continue
        elif effective_owner and not _user_matches(info.owner, effective_owner):
            continue
        if info.is_expired(ref, ci_ttl_hours=ci_ttl_hours):
            expired.append(info)
    return expired


def count_by_owner(infos: list[WorkspaceInfo]) -> dict[str, int]:
    """Group workspace counts by owner (using ``[CI]`` for CI apps)."""
    counts: dict[str, int] = {}
    for info in infos:
        key = info.owner or ("[CI]" if info.is_ci else "(unowned)")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# GitOps `workspace up`
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class WorkspaceUpResult:
    """Outcome of ``workspace_up``."""

    app_name: str
    display_name: str
    created: bool
    owner: str
    branch: str | None
    team: str | None
    expiry: str
    profile_path: Path | None = None


def _write_ws_local_profile(
    *,
    profile_path: Path,
    project_id: str,
    location: str,
    app_id: str,
    display_name: str,
    app_dir: str | None,
    env_file: str | None,
    base_profile: ResolvedProfile | None,
) -> Path:
    """Emit gitignored ``.cxas/profiles/ws.local.yml`` for the workspace."""
    payload: dict[str, Any] = {
        "protected": False,
        "description": f"Ephemeral workspace '{display_name}' (auto-generated)",
        "target": {
            "project_id": project_id,
            "location": location,
            "app_id": app_id,
            "display_name": display_name,
        },
    }
    if app_dir:
        payload["app_dir"] = app_dir
    if env_file:
        payload["env_file"] = env_file
    inherited_overrides: dict[str, dict[str, Any]] = (
        copy.deepcopy(base_profile.overrides)
        if base_profile is not None and base_profile.overrides
        else {}
    )
    # Always keep the workspace displayName so subsequent
    # `cxas push --profile ws.local` never renames the workspace back to
    # tracked app.json's displayName.
    updated_cfg = False
    for cfg_name in ("app.json", "app.yaml"):
        if cfg_name in inherited_overrides and isinstance(
            inherited_overrides[cfg_name], dict
        ):
            inherited_overrides[cfg_name]["displayName"] = display_name
            updated_cfg = True
    if not updated_cfg:
        inherited_overrides["app.json"] = {"displayName": display_name}
    payload["overrides"] = inherited_overrides

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )
    return profile_path


def workspace_up(
    app_dir: str | None = None,
    *,
    project_id: str | None = None,
    location: str | None = None,
    branch: str | None = None,
    name: str | None = None,
    profile: str | None = None,
    team: str | None = None,
    ttl_days: float = DEFAULT_TTL_DAYS,
    env_file: str | None = None,
    use_profile_env_file: bool = True,
    write_profile: bool = True,
    write_profile_path: str | Path | None = None,
    apps: Apps | None = None,
    user: str | None = None,
) -> WorkspaceUpResult:
    """Idempotently create or update an ephemeral workspace from ``app_dir``."""
    from cxas_scrapi.cli.app import _app_push  # noqa: PLC0415
    from cxas_scrapi.core.apps import Apps as AppsClient  # noqa: PLC0415

    resolved_profile: ResolvedProfile | None = load_profile(profile)
    project_root = (
        resolved_profile.project_root
        if resolved_profile is not None
        else resolve_project_root(app_dir)[0]
    )

    eff_project_id = project_id or (
        resolved_profile.target.project_id if resolved_profile else None
    )
    eff_location = location or (
        resolved_profile.target.location if resolved_profile else None
    )
    if not eff_project_id or not eff_location:
        raise ValueError(
            "project_id and location are required (pass --project-id / "
            "--location or --profile)."
        )

    eff_app_dir = (
        app_dir
        or (
            str(resolved_profile.app_dir)
            if resolved_profile and resolved_profile.app_dir
            else None
        )
        or "."
    )

    caller = user or current_user()
    eff_branch = branch or current_git_branch(cwd=eff_app_dir)

    if not name:
        if not eff_branch:
            raise SharedBranchError(
                "Could not detect a Git feature branch. Checkout a feature "
                "branch or pass --name explicitly."
            )
        if eff_branch.lower() in SHARED_BRANCHES:
            raise SharedBranchError(
                f"Refusing to derive workspace name from shared branch "
                f"'{eff_branch}'. Switch to a feature branch or pass --name "
                "explicitly."
            )
        display_name = default_workspace_display_name(caller, eff_branch)
    else:
        display_name = name

    # Resolve env_file with shared-dev isolation guard:
    # --env-file > --no-env-file > envs/env-ws.local.json > profile (warn)
    isolated_ws_env = project_root / "envs" / "env-ws.local.json"
    if env_file:
        eff_env_file: str | None = env_file
    elif not use_profile_env_file:
        eff_env_file = None
    elif isolated_ws_env.is_file():
        eff_env_file = str(isolated_ws_env)
    elif resolved_profile and resolved_profile.env_file:
        eff_env_file = str(resolved_profile.env_file)
        print(
            f"Warning: Workspace '{display_name}' is inheriting shared "
            f"env_file '{eff_env_file}' from profile "
            f"'{resolved_profile.name}'. To avoid writing into shared dev "
            "resources (e.g. BQ/GCS), create 'envs/env-ws.local.json', "
            "pass --env-file <isolated.json>, or pass --no-env-file.",
            file=sys.stderr,
        )
    else:
        eff_env_file = None

    apps_client = apps or AppsClient(
        project_id=eff_project_id, location=eff_location
    )
    existing = apps_client.get_app_by_display_name(display_name)
    created = existing is None
    target_app_name = getattr(existing, "name", None) if existing else None

    profile_filename = default_workspace_profile_filename(eff_branch)

    # Build a staging profile so app_overrides apply AND displayName is always
    # pinned to the workspace display_name (preventing import_app from renaming
    # the workspace back to tracked app.json on idempotent updates).
    if resolved_profile is not None:
        staging_profile = copy.deepcopy(resolved_profile)
        updated_cfg = False
        for cfg_name in ("app.json", "app.yaml"):
            if cfg_name in staging_profile.overrides and isinstance(
                staging_profile.overrides[cfg_name], dict
            ):
                staging_profile.overrides[cfg_name]["displayName"] = (
                    display_name
                )
                updated_cfg = True
        if not updated_cfg:
            staging_profile.overrides["app.json"] = {
                "displayName": display_name
            }
    else:
        staging_profile = ResolvedProfile(
            name=profile_filename.removesuffix(".yml"),
            source_path=project_root / PROFILES_REL_DIR / profile_filename,
            project_root=project_root,
            overrides={"app.json": {"displayName": display_name}},
        )

    pushed_app_name = _app_push(
        app_dir=eff_app_dir,
        apps_client=apps_client,
        target_app_name=target_app_name,
        identifier=target_app_name,
        display_name=display_name,
        env_file=eff_env_file,
        args=None,
        profile=staging_profile,
    )
    if not pushed_app_name:
        raise RuntimeError(
            f"Failed to push workspace app '{display_name}' from {eff_app_dir}."
        )

    expiry = expiry_from_ttl(ttl_days)
    now_iso = to_iso(utc_now())

    store = AppMetadataStore(pushed_app_name, apps=apps_client, user=caller)

    def _stamp(d: dict[str, Any]) -> None:
        existing_ws = d.get(WORKSPACE_KEY)
        created_at = (
            existing_ws.get("created")
            if isinstance(existing_ws, dict) and existing_ws.get("created")
            else now_iso
        )
        d[WORKSPACE_KEY] = {
            "owner": caller,
            "branch": eff_branch or "",
            "created": created_at,
        }
        d[EXPIRY_KEY] = expiry
        if team:
            d[TEAM_KEY] = team

    store.update(_stamp)

    written_profile: Path | None = None
    if write_profile:
        target_profile_path = (
            Path(write_profile_path)
            if write_profile_path
            else project_root / PROFILES_REL_DIR / profile_filename
        )
        app_id = pushed_app_name.rsplit("/", 1)[-1]
        written_profile = _write_ws_local_profile(
            profile_path=target_profile_path,
            project_id=eff_project_id,
            location=eff_location,
            app_id=app_id,
            display_name=display_name,
            app_dir=eff_app_dir,
            env_file=eff_env_file,
            base_profile=resolved_profile,
        )

    return WorkspaceUpResult(
        app_name=pushed_app_name,
        display_name=display_name,
        created=created,
        owner=caller,
        branch=eff_branch,
        team=team,
        expiry=expiry,
        profile_path=written_profile,
    )
