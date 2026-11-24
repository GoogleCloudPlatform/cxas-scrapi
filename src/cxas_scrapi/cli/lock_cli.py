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

"""CLI subcommands for `cxas lock`, `unlock`, `lock-status`, and `rollback`."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from cxas_scrapi.collab.metadata import (
    AppMetadataStore,
    LockHeldError,
    current_user,
)
from cxas_scrapi.utils.profiles import (
    ProfileError,
    guard_protected_mutation,
    load_profile,
    print_profile_warnings,
)

if TYPE_CHECKING:
    import argparse

    from cxas_scrapi.core.apps import Apps


def _resolve_lock_target(
    args: argparse.Namespace,
) -> tuple[Apps, str]:
    """Resolve ``(apps_client, app_name)`` from CLI args and/or --profile."""
    from cxas_scrapi.cli.app import _resolve_app_args

    profile = None
    try:
        profile = load_profile(getattr(args, "profile", None))
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if profile is not None:
        print_profile_warnings(profile.warnings)
        print(profile.format_banner())
        if not getattr(args, "project_id", None) and profile.target.project_id:
            args.project_id = profile.target.project_id
        if not getattr(args, "location", None) and profile.target.location:
            args.location = profile.target.location

    identifier = getattr(args, "app", None) or getattr(args, "app_name", None)
    if not identifier and profile is not None:
        identifier = (
            profile.target.app_resource_name or profile.target.display_name
        )
    if not identifier:
        print(
            "Error: No app specified. Pass an app identifier, --app-name, "
            "or configure 'target' in --profile / CXAS_PROFILE.",
            file=sys.stderr,
        )
        sys.exit(1)

    apps_client, app_name, _ = _resolve_app_args(identifier, args)
    return apps_client, app_name


def cmd_lock(args: argparse.Namespace) -> None:
    """Handle `cxas lock`."""
    apps_client, app_name = _resolve_lock_target(args)
    user = getattr(args, "user", None) or current_user()
    store = AppMetadataStore(app_name, apps=apps_client, user=user)
    try:
        lock = store.acquire_lock(
            user=user,
            ttl_minutes=getattr(args, "ttl", 60),
            reason=getattr(args, "reason", "") or "",
            force=getattr(args, "force", False),
            native=not getattr(args, "no_native", False),
        )
    except (LockHeldError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    reason = f" ({lock['reason']})" if lock.get("reason") else ""
    native_label = (
        "native + metadata"
        if not getattr(args, "no_native", False)
        else "metadata-only"
    )
    print(
        f"Locked {app_name} [{native_label}] by {lock['by']} "
        f"until {lock['until']}{reason}"
    )


def cmd_unlock(args: argparse.Namespace) -> None:
    """Handle `cxas unlock`."""
    apps_client, app_name = _resolve_lock_target(args)
    user = getattr(args, "user", None) or current_user()
    store = AppMetadataStore(app_name, apps=apps_client, user=user)
    try:
        released = store.release_lock(
            user=user, force=getattr(args, "force", False)
        )
    except LockHeldError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if released:
        print(f"Unlocked {app_name}.")
    else:
        print(f"App {app_name} is not locked.")


def cmd_lock_status(args: argparse.Namespace) -> None:
    """Handle `cxas lock-status`."""
    apps_client, app_name = _resolve_lock_target(args)
    store = AppMetadataStore(app_name, apps=apps_client)
    state = store.lock_state()

    if getattr(args, "json", False):
        print(json.dumps({"app_name": app_name, **state}, indent=2))
        return

    lock = state["lock"]
    native = state["native_locked"]
    stale = state["stale"]

    print(f"App          : {app_name}")
    print(f"Native Lock  : {'LOCKED' if native else 'unlocked'}")
    if lock:
        reason = f" ({lock['reason']})" if lock.get("reason") else ""
        print(
            f"Held By      : {lock.get('by')} until {lock.get('until')}{reason}"
        )
    elif stale:
        print("Status       : STALE (expired cxas.lock left App.locked=True)")
    elif native:
        print(
            "Status       : Natively locked outside cxas (e.g. Cloud Console)"
        )
    else:
        print("Status       : Unlocked")


def cmd_rollback(args: argparse.Namespace) -> None:
    """Handle `cxas rollback` — flip a deployment to a previous app version."""
    from cxas_scrapi.core.deployments import Deployments
    from cxas_scrapi.core.versions import Versions

    profile = None
    try:
        profile = load_profile(getattr(args, "profile", None))
        guard_protected_mutation(
            profile,
            command="rollback",
            allow_protected=getattr(args, "allow_protected", False),
        )
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if profile is not None:
        print_profile_warnings(profile.warnings)

    app_name = getattr(args, "app_name", None)
    if not app_name and profile is not None:
        app_name = profile.target.app_resource_name
    if not app_name:
        print(
            "Error: --app-name is required (or configure 'target' in a "
            "--profile / CXAS_PROFILE).",
            file=sys.stderr,
        )
        sys.exit(1)

    deployment_id = getattr(args, "deployment", None)
    if not deployment_id and profile is not None:
        deployment_id = profile.target.deployment_id
    if not deployment_id:
        print(
            "Error: --deployment is required (or set target.deployment_id in "
            "your profile).",
            file=sys.stderr,
        )
        sys.exit(1)
    deployment_id = deployment_id.rsplit("/deployments/", 1)[-1]

    version_input = getattr(args, "version", None)
    if not version_input:
        print("Error: --version is required for rollback.", file=sys.stderr)
        sys.exit(1)

    versions_client = Versions(app_name=app_name)
    try:
        target_version = versions_client.resolve_version_name(version_input)
    except ValueError as e:
        print(
            f"Error resolving version '{version_input}': {e}", file=sys.stderr
        )
        sys.exit(1)

    deployments_client = Deployments(app_name=app_name)
    try:
        current_dep = deployments_client.get_deployment(
            deployment_id=deployment_id
        )
        current_version = getattr(current_dep, "app_version", None) or "(none)"

        print(
            f"Rolling back deployment '{deployment_id}' on {app_name}:\n"
            f"  {current_version} -> {target_version}"
        )
        deployments_client.update_deployment(
            deployment_id=deployment_id,
            app_version=target_version,
        )
        print(
            f"Successfully rolled back deployment '{deployment_id}' to "
            f"{target_version}."
        )

        # Warn if other deployments on this app still point to the old version.
        if current_version and current_version not in (
            "(none)",
            target_version,
        ):
            try:
                other_deps = deployments_client.list_deployments()
                still_on_old = [
                    (
                        getattr(d, "name", "").rsplit("/", 1)[-1]
                        or getattr(d, "display_name", "?")
                    )
                    for d in other_deps
                    if getattr(d, "name", "").rsplit("/", 1)[-1]
                    != deployment_id
                    and getattr(d, "app_version", None) == current_version
                ]
                if still_on_old:
                    print(
                        "Warning: Other deployment(s) on this app still point "
                        f"to {current_version}: {', '.join(still_on_old)}.",
                        file=sys.stderr,
                    )
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:
        print(f"Failed to rollback deployment: {e}", file=sys.stderr)
        sys.exit(1)


def _add_common_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "app",
        nargs="?",
        default=None,
        help="App Resource Name or Display Name (optional when using --profile).",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Explicit App Resource Name (projects/.../locations/.../apps/...).",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="The GCP Project ID (optional if inferred from resource name or profile).",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="The GCP Location (optional if inferred from resource name or profile).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "Runtime profile name (.cxas/profiles/<name>.yml) or file path. "
            "Falls back to CXAS_PROFILE env var."
        ),
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register `lock`, `unlock`, `lock-status`, and `rollback` subcommands."""
    p_lock = subparsers.add_parser(
        "lock",
        help="Acquire a native CES edit lock (App.locked + cxas.lock TTL).",
    )
    _add_common_target_args(p_lock)
    p_lock.add_argument(
        "--ttl",
        type=int,
        default=60,
        help="Lock TTL in minutes (default: 60).",
    )
    p_lock.add_argument(
        "--reason",
        default="",
        help="Optional reason recorded in cxas.lock metadata.",
    )
    p_lock.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Steal an active lock held by another user.",
    )
    p_lock.add_argument(
        "--no-native",
        action="store_true",
        default=False,
        help="Write cxas.lock metadata without setting platform App.locked.",
    )
    p_lock.set_defaults(func=cmd_lock)

    p_unlock = subparsers.add_parser(
        "unlock",
        help="Release the native CES edit lock (App.locked + cxas.lock).",
    )
    _add_common_target_args(p_unlock)
    p_unlock.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force-release a lock held by another user.",
    )
    p_unlock.set_defaults(func=cmd_unlock)

    p_status = subparsers.add_parser(
        "lock-status",
        help="Inspect the current native and cxas.lock status of an app.",
    )
    _add_common_target_args(p_status)
    p_status.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output lock status as JSON.",
    )
    p_status.set_defaults(func=cmd_lock_status)

    p_rollback = subparsers.add_parser(
        "rollback",
        help="Roll back a deployment to a previous app version.",
    )
    p_rollback.add_argument(
        "--app-name",
        default=None,
        help="CXAS App resource name (defaults to profile target).",
    )
    p_rollback.add_argument(
        "--deployment",
        "--deployment-id",
        dest="deployment",
        default=None,
        help="Deployment ID to roll back (defaults to profile target.deployment_id).",
    )
    p_rollback.add_argument(
        "--version",
        required=True,
        help=(
            "Target version display name (e.g. 'v1.0.0'), bare version ID, "
            "or full version resource name."
        ),
    )
    p_rollback.add_argument(
        "--profile",
        default=None,
        help=(
            "Runtime profile name (.cxas/profiles/<name>.yml) or file path. "
            "Falls back to CXAS_PROFILE env var."
        ),
    )
    p_rollback.add_argument(
        "--allow-protected",
        action="store_true",
        default=False,
        help=(
            "Allow rolling back a profile marked protected: true from a local "
            "workstation (not needed when CI=true)."
        ),
    )
    p_rollback.add_argument(
        "--project-id", default=None, help="GCP Project ID."
    )
    p_rollback.add_argument("--location", default=None, help="GCP Location.")
    p_rollback.set_defaults(func=cmd_rollback)
