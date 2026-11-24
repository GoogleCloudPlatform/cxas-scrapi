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

"""CLI subcommands for `cxas profile` (list, show, validate)."""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from cxas_scrapi.utils.profiles import (
    ENV_PROFILE_VAR,
    ProfileError,
    list_profiles,
    load_profile,
    render_app_config_diff,
    render_gecx_config_diff,
    validate_profile,
)


def profile_list(args: argparse.Namespace) -> None:
    """Handle `cxas profile list`."""
    summaries = list_profiles()
    active_env = os.environ.get(ENV_PROFILE_VAR, "").strip()

    if getattr(args, "json", False):
        payload = [
            {
                "name": s.name,
                "path": str(s.path),
                "protected": s.protected,
                "target": s.target_label,
                "extends": s.extends,
                "error": s.error,
                "active": s.name == active_env,
            }
            for s in summaries
        ]
        print(json.dumps(payload, indent=2))
        return

    if not summaries:
        print("No profiles found in .cxas/profiles/.")
        return

    for s in summaries:
        marker = "*" if s.name == active_env else " "
        if s.error:
            print(f"{marker} {s.name:<20} [INVALID] {s.error}")
            continue
        prot = " [protected]" if s.protected else ""
        ext = f" (extends: {s.extends})" if s.extends else ""
        print(f"{marker} {s.name:<20} {s.target_label}{prot}{ext}")


def profile_show(args: argparse.Namespace) -> None:
    """Handle `cxas profile show [name]`."""
    name = getattr(args, "name", None)
    try:
        profile = load_profile(name)
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if profile is None:
        print(
            f"Error: No profile specified and {ENV_PROFILE_VAR} is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(profile.to_dict(), indent=2))
        return

    print(profile.format_banner())
    print(f"Source    : {profile.source_path} ({profile.source_kind})")
    if len(profile.chain) > 1:
        chain_str = " -> ".join(p.name for p in profile.chain)
        print(f"Chain     : {chain_str}")
    print(f"Protected : {'true' if profile.protected else 'false'}")
    if profile.description:
        print(f"Desc      : {profile.description}")
    if profile.app_dir:
        print(f"App Dir   : {profile.app_dir}")
    if profile.env_file:
        print(f"Env File  : {profile.env_file}")

    if profile.overrides:
        print("Overrides :")
        for fname in sorted(profile.overrides):
            keys = ", ".join(sorted(profile.overrides[fname].keys()))
            print(f"  - {fname}: {keys or '(empty)'}")

    for warning in profile.warnings:
        print(f"Warning   : {warning}", file=sys.stderr)

    if getattr(args, "diff", False):
        app_dir = profile.app_dir or profile.project_root
        app_diff = render_app_config_diff(app_dir, profile)
        gecx_path = profile.project_root / "gecx-config.json"
        gecx_diff = render_gecx_config_diff(
            gecx_path if gecx_path.is_file() else None, profile
        )
        if app_diff:
            print("\n--- App Config Diff ---")
            print(app_diff, end="")
        if gecx_diff:
            print("\n--- gecx-config.json Diff ---")
            print(gecx_diff, end="")
        if not app_diff and not gecx_diff:
            print("\n(No config diffs to display.)")


def profile_validate(args: argparse.Namespace) -> None:
    """Handle `cxas profile validate [name] [--all]`."""
    validate_all = getattr(args, "all", False)
    name = getattr(args, "name", None)
    as_json = getattr(args, "json", False)

    targets: list[str] = []
    if validate_all:
        targets = [str(s.path) for s in list_profiles()]
        if not targets:
            if as_json:
                print(json.dumps({"valid": True, "profiles": []}, indent=2))
            else:
                print("No profiles found to validate.")
            return
    elif name:
        targets = [name]
    else:
        env_ref = os.environ.get(ENV_PROFILE_VAR, "").strip()
        if env_ref:
            targets = [env_ref]
        else:
            targets = [str(s.path) for s in list_profiles()]
            if not targets:
                print(
                    "Error: No profile specified and no profiles found in "
                    ".cxas/profiles/.",
                    file=sys.stderr,
                )
                sys.exit(1)

    results = []
    has_errors = False
    for target in targets:
        prof, errors, warnings_list = validate_profile(target)
        valid = not errors
        if not valid:
            has_errors = True
        results.append(
            {
                "profile": prof.name if prof else target,
                "path": str(prof.source_path) if prof else target,
                "valid": valid,
                "errors": errors,
                "warnings": warnings_list,
            }
        )

    if as_json:
        print(
            json.dumps({"valid": not has_errors, "profiles": results}, indent=2)
        )
    else:
        for res in results:
            status = "OK" if res["valid"] else "FAILED"
            print(f"[{status}] {res['profile']} ({res['path']})")
            for w in res["warnings"]:
                print(f"  WARN : {w}", file=sys.stderr)
            for err in res["errors"]:
                print(f"  ERROR: {err}", file=sys.stderr)

    if has_errors:
        sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register `cxas profile` subcommands on the root CLI parser."""
    parser_profile = subparsers.add_parser(
        "profile",
        help="Manage runtime environment profiles (.cxas/profiles/).",
    )
    profile_subparsers = parser_profile.add_subparsers(
        title="Profile Commands",
        dest="profile_command",
        required=True,
    )

    p_list = profile_subparsers.add_parser(
        "list", help="List available profiles for the active project/repo."
    )
    p_list.add_argument(
        "--json", action="store_true", help="Output profiles as JSON."
    )
    p_list.set_defaults(func=profile_list)

    p_show = profile_subparsers.add_parser(
        "show",
        help="Show resolved target, paths, and overrides for a profile.",
    )
    p_show.add_argument(
        "name",
        nargs="?",
        default=None,
        help=f"Profile name or file path (defaults to ${ENV_PROFILE_VAR}).",
    )
    p_show.add_argument(
        "--json", action="store_true", help="Output resolved profile as JSON."
    )
    p_show.add_argument(
        "--diff",
        action="store_true",
        help="Show unified diffs against local app config and gecx-config.json.",
    )
    p_show.set_defaults(func=profile_show)

    p_val = profile_subparsers.add_parser(
        "validate",
        help="Validate profile syntax, inheritance, whitelist, and overrides.",
    )
    p_val.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Profile name or file path (defaults to $CXAS_PROFILE or all).",
    )
    p_val.add_argument(
        "--all",
        action="store_true",
        help="Validate all discovered profiles in .cxas/profiles/.",
    )
    p_val.add_argument(
        "--json", action="store_true", help="Output validation result as JSON."
    )
    p_val.set_defaults(func=profile_validate)
