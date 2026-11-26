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

"""Argparse subcommand handlers for ``cxas workspace`` (up, list, down, cleanup)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from cxas_scrapi.collab import workspace as ws
from cxas_scrapi.collab.metadata import current_user
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.utils.profiles import ProfileError, load_profile

if TYPE_CHECKING:
    import argparse


def _add_project_location_profile_args(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--project-id",
        default=None,
        help="GCP Project ID (optional if supplied by --profile or resource name).",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="GCP Location (optional if supplied by --profile or resource name).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "Runtime profile name (.cxas/profiles/<name>.yml) or file path. "
            "Falls back to CXAS_PROFILE env var."
        ),
    )


def _resolve_apps_client(
    args: argparse.Namespace, identifier: str | None = None
) -> tuple[Apps, Any]:
    profile = None
    try:
        profile = load_profile(getattr(args, "profile", None))
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    project_id = (
        (Common._get_project_id(identifier) if identifier else None)
        or getattr(args, "project_id", None)
        or (profile.target.project_id if profile else None)
    )
    location = (
        (Common._get_location(identifier) if identifier else None)
        or getattr(args, "location", None)
        or (profile.target.location if profile else None)
    )
    if not project_id or not location:
        print(
            "Error: Could not determine project_id or location. Provide "
            "--project-id and --location, or --profile.",
            file=sys.stderr,
        )
        sys.exit(1)
    return Apps(project_id=project_id, location=location), profile


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "Refusing to proceed without confirmation (non-interactive); "
            "pass --yes.",
            file=sys.stderr,
        )
        return False
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    widths = {
        c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns
    }
    print("  ".join(c.upper().ljust(widths[c]) for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def workspace_up_cmd(args: argparse.Namespace) -> None:
    """Handle `cxas workspace up`."""
    try:
        result = ws.workspace_up(
            app_dir=getattr(args, "app_dir", None),
            project_id=getattr(args, "project_id", None),
            location=getattr(args, "location", None),
            branch=getattr(args, "branch", None),
            name=getattr(args, "name", None),
            profile=getattr(args, "profile", None),
            team=getattr(args, "team", None),
            ttl_days=getattr(args, "ttl_days", ws.DEFAULT_TTL_DAYS),
            env_file=getattr(args, "env_file", None),
            use_profile_env_file=not getattr(args, "no_env_file", False),
            write_profile=not getattr(args, "no_write_profile", False),
        )
    except Exception as e:
        print(f"Failed to bring up workspace: {e}", file=sys.stderr)
        sys.exit(1)

    verb = "created" if result.created else "updated"
    print(f"\nWorkspace {verb}:")
    print(f"  name:    {result.display_name}")
    print(f"  app:     {result.app_name}")
    print(f"  owner:   {result.owner}")
    if result.branch:
        print(f"  branch:  {result.branch}")
    if result.team:
        print(f"  team:    {result.team}")
    print(f"  expires: {result.expiry}")
    if result.profile_path:
        print(f"  profile: {result.profile_path}")
        print(f"  export:  export CXAS_PROFILE={result.profile_path.stem}")


def workspace_list_cmd(args: argparse.Namespace) -> None:
    """Handle `cxas workspace list`."""
    apps, _ = _resolve_apps_client(args)
    owner = current_user() if getattr(args, "mine", False) else None
    try:
        infos = ws.list_workspaces(
            apps,
            team=getattr(args, "team", None),
            owner=owner,
            include_ci=getattr(args, "include_ci", False),
        )
    except Exception as e:
        print(f"Failed to list workspaces: {e}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps([i.to_dict() for i in infos], indent=2))
        return

    if not infos:
        print("No workspaces found.")
        return

    now = ws.utc_now()
    rows = []
    for info in infos:
        expires = info.expiry or "-"
        if info.is_expired(now):
            expires += " (expired)"
        rows.append(
            {
                "name": info.display_name,
                "branch": info.branch or ("[CI]" if info.is_ci else "-"),
                "team": info.team or "-",
                "owner": info.owner or "-",
                "expires": expires,
                "last change": info.last_change or "-",
            }
        )
    _print_table(
        rows, ["name", "branch", "team", "owner", "expires", "last change"]
    )


def workspace_down_cmd(args: argparse.Namespace) -> None:
    """Handle `cxas workspace down` (and alias `delete`)."""
    pr_num = getattr(args, "pr", None)
    name = getattr(args, "name", None)
    branch = getattr(args, "branch", None)

    identifier: str | None = None
    if pr_num is not None:
        identifier = f"{ws.CI_PR_PREFIX}{pr_num}"
    elif name:
        identifier = name
    else:
        eff_branch = branch or ws.current_git_branch()
        if eff_branch:
            identifier = ws.default_workspace_display_name(
                current_user(), eff_branch
            )

    apps, profile = _resolve_apps_client(args, identifier)
    if not identifier and profile is not None:
        identifier = (
            profile.target.app_resource_name or profile.target.display_name
        )

    if not identifier:
        print(
            "Error: Could not determine workspace to delete. Pass a workspace "
            "name, --pr <num>, --branch <name>, or --profile.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        app = ws.find_app(apps, identifier)
    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    info = ws.workspace_info_from_app(app)
    if not info.is_workspace:
        print(
            f"Error: '{info.display_name}' is not a workspace (missing "
            "cxas.workspace / cxas.parent metadata). Refusing to delete; "
            "use `cxas delete` for regular apps.",
            file=sys.stderr,
        )
        sys.exit(1)

    force = bool(getattr(args, "force", False))
    owner = ws.foreign_owner(info)
    if owner and not force:
        print(
            f"Error: workspace '{info.display_name}' is owned by {owner}, "
            "not you. Pass --force to delete it anyway.",
            file=sys.stderr,
        )
        sys.exit(1)

    owned = f", owned by {owner}" if owner else ""
    if not _confirm(
        f"Delete workspace '{info.display_name}' ({info.name}{owned})?",
        bool(getattr(args, "yes", False)),
    ):
        print("Aborted.")
        sys.exit(1)

    try:
        ws.delete_workspace(apps, app, force=force)
    except Exception as e:
        print(f"Failed to delete workspace: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Deleted workspace {info.display_name} ({info.name}).")


def workspace_cleanup_cmd(args: argparse.Namespace) -> None:
    """Handle `cxas workspace cleanup`."""
    all_users = bool(getattr(args, "all_users", False))
    include_ci = bool(getattr(args, "include_ci", False))
    ci_ttl_hours = float(getattr(args, "ci_ttl_hours", ws.DEFAULT_CI_TTL_HOURS))
    user = None if all_users else current_user()
    apps, _ = _resolve_apps_client(args)

    try:
        expired = ws.expired_workspaces(
            apps,
            team=getattr(args, "team", None),
            owner=user,
            all_users=all_users,
            include_ci=include_ci,
            ci_ttl_hours=ci_ttl_hours,
        )
    except Exception as e:
        print(f"Failed to scan workspaces: {e}", file=sys.stderr)
        sys.exit(1)

    scope = (
        "all users" if all_users else f"owner {user}; --all-users for everyone"
    )
    if getattr(args, "team", None):
        scope += f", team {args.team}"
    if include_ci:
        scope += f", include CI (> {ci_ttl_hours:g}h)"

    if not expired:
        print(f"No expired workspaces ({scope}).")
        return

    print(f"{len(expired)} expired workspace(s) ({scope}):")
    for info in expired:
        kind = "[CI]" if info.is_ci and not info.owner else (info.owner or "-")
        print(
            f"  - {info.display_name} (owner={kind}, "
            f"team={info.team or '-'}, expiry={info.expiry or info.last_change})"
        )
    if all_users or include_ci:
        print("Per owner:")
        for owner_name, count in ws.count_by_owner(expired).items():
            print(f"  {owner_name}: {count}")

    if getattr(args, "dry_run", False):
        print("[DRY RUN] No workspaces deleted.")
        return

    if not _confirm(
        f"Delete {len(expired)} expired workspace(s)?",
        bool(getattr(args, "yes", False)),
    ):
        print("Aborted.")
        sys.exit(1)

    deleted = 0
    for info in expired:
        try:
            ws.delete_workspace(apps, info, force=all_users or info.is_ci)
            print(f"Deleted {info.display_name} ({info.name}).")
            deleted += 1
        except Exception as e:
            print(
                f"Warning: failed to delete {info.display_name}: {e}",
                file=sys.stderr,
            )
    print(f"Cleanup complete: deleted {deleted}/{len(expired)} workspace(s).")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register `cxas workspace` subcommands on the root CLI parser."""
    parser_ws = subparsers.add_parser(
        "workspace",
        help="Manage ephemeral GitOps workspaces (up, list, down, cleanup).",
    )
    ws_sub = parser_ws.add_subparsers(
        title="Workspace Commands",
        dest="workspace_command",
        required=True,
    )

    p_up = ws_sub.add_parser(
        "up",
        help=(
            "Create or update an ephemeral workspace app and write a "
            "branch-scoped ws-<branch>.local.yml profile."
        ),
    )
    p_up.add_argument(
        "--app-dir",
        default=None,
        help="Local app directory (defaults to profile app_dir or '.').",
    )
    p_up.add_argument(
        "--name",
        default=None,
        help="Explicit workspace display name (defaults to ws/<user>/<branch>).",
    )
    p_up.add_argument(
        "--branch",
        default=None,
        help="Git branch name (auto-detected if omitted).",
    )
    p_up.add_argument("--team", default=None, help="Optional team tag.")
    p_up.add_argument(
        "--ttl-days",
        type=float,
        default=ws.DEFAULT_TTL_DAYS,
        help=f"Workspace TTL in days (default: {ws.DEFAULT_TTL_DAYS:g}).",
    )
    p_up.add_argument(
        "--env-file",
        default=None,
        help="Optional environment JSON file (defaults to envs/env-ws.local.json or profile env_file).",
    )
    p_up.add_argument(
        "--no-env-file",
        action="store_true",
        default=False,
        help="Do not inherit env_file from --profile (avoids shared dev backends).",
    )
    p_up.add_argument(
        "--no-write-profile",
        action="store_true",
        default=False,
        help="Do not write .cxas/profiles/ws-<branch>.local.yml.",
    )
    _add_project_location_profile_args(p_up)
    p_up.set_defaults(func=workspace_up_cmd)

    p_list = ws_sub.add_parser(
        "list", help="List ephemeral workspaces in the project."
    )
    p_list.add_argument(
        "--mine",
        action="store_true",
        default=False,
        help="Only show workspaces owned by the current user.",
    )
    p_list.add_argument("--team", default=None, help="Filter by team name.")
    p_list.add_argument(
        "--include-ci",
        action="store_true",
        default=False,
        help="Also list automated '[CI] PR-*' ephemeral apps.",
    )
    p_list.add_argument(
        "--json", action="store_true", default=False, help="Output as JSON."
    )
    _add_project_location_profile_args(p_list)
    p_list.set_defaults(func=workspace_list_cmd)

    p_down = ws_sub.add_parser(
        "down",
        aliases=["delete"],
        help="Delete a workspace app (refuses non-workspace apps).",
    )
    p_down.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Workspace display name or resource name (defaults to current branch).",
    )
    p_down.add_argument(
        "--branch",
        default=None,
        help="Git branch whose workspace should be deleted.",
    )
    p_down.add_argument(
        "--pr",
        default=None,
        help="Pull Request number to delete matching '[CI] PR-<num>' app.",
    )
    p_down.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Allow deleting a workspace owned by another user.",
    )
    p_down.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt.",
    )
    _add_project_location_profile_args(p_down)
    p_down.set_defaults(func=workspace_down_cmd)

    p_cleanup = ws_sub.add_parser(
        "cleanup",
        help="Delete expired ephemeral workspaces (and optionally orphaned CI apps).",
    )
    p_cleanup.add_argument("--team", default=None, help="Filter by team name.")
    p_cleanup.add_argument(
        "--all-users",
        action="store_true",
        default=False,
        help="Sweep expired workspaces for all owners (not just yourself).",
    )
    p_cleanup.add_argument(
        "--include-ci",
        action="store_true",
        default=False,
        help="Also sweep orphaned '[CI] PR-*' apps older than --ci-ttl-hours.",
    )
    p_cleanup.add_argument(
        "--ci-ttl-hours",
        type=float,
        default=ws.DEFAULT_CI_TTL_HOURS,
        help=f"Age threshold in hours for '[CI] PR-*' apps (default: {ws.DEFAULT_CI_TTL_HOURS:g}).",
    )
    p_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List expired workspaces without deleting them.",
    )
    p_cleanup.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt.",
    )
    _add_project_location_profile_args(p_cleanup)
    p_cleanup.set_defaults(func=workspace_cleanup_cmd)
