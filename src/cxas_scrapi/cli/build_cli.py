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

"""CLI subcommands for Profile-Based Build (`build`, `package`, `lock`, `drift`, `backport`)."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cxas_scrapi.build.builder import (
    BuildError,
    BuildReport,
    build_all,
    build_app,
)
from cxas_scrapi.build.bundle_io import BuildGateError, DriftGateError
from cxas_scrapi.build.drift import check_drift, write_lockfile
from cxas_scrapi.build.manifests import ManifestError
from cxas_scrapi.core.packaging import package_app_archive
from cxas_scrapi.core.python_bundler import PythonBundlerError

if TYPE_CHECKING:
    import argparse

backport_live_to_source: Any = None


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert result objects into JSON-serializable primitives."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _to_jsonable(obj.to_dict())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _print_build_report(report: BuildReport) -> None:
    print(f"Built {report.profile_id} {report.channel} to {report.out_dir}")
    for warning in report.warnings:
        print(f"  Warning: {warning}")


def _run_all_builds(args: argparse.Namespace) -> None:
    reports = build_all(
        core_dir=args.core_dir,
        manifests_dir=args.manifests_dir,
        out_root=args.out,
        environment=args.env,
        package_zip=args.zip,
        native_python_env=getattr(args, "native_python_env", False),
        write_locks=getattr(args, "write_locks", False),
        composition_mode=getattr(args, "composition_mode", None),
    )
    if args.json:
        print(json.dumps([r.model_dump(mode="json") for r in reports]))
        return
    for report in reports:
        _print_build_report(report)


def _run_single_build(args: argparse.Namespace) -> None:
    if not args.profile or not args.channel:
        print(
            "Error: --profile and --channel are required unless "
            "--all is specified.",
            file=sys.stderr,
        )
        sys.exit(1)

    report = build_app(
        core_dir=args.core_dir,
        manifests_dir=args.manifests_dir,
        profile_id=args.profile,
        channel=args.channel,
        environment=args.env,
        out_dir=args.out,
        package_zip=args.zip,
        native_python_env=getattr(args, "native_python_env", False),
        write_lock=getattr(args, "write_locks", False),
        composition_mode=getattr(args, "composition_mode", None),
    )
    if args.json:
        print(report.model_dump_json())
    else:
        _print_build_report(report)


def app_build(args: argparse.Namespace) -> None:
    """Build one target or the full profile/channel matrix and emit reports."""
    try:
        if args.all:
            _run_all_builds(args)
        else:
            _run_single_build(args)
    except (
        BuildError,
        BuildGateError,
        ManifestError,
        PythonBundlerError,
    ) as exc:
        print(f"Build Error: {exc}", file=sys.stderr)
        sys.exit(1)


def app_lock(args: argparse.Namespace) -> None:
    """Writes a normalized SHA-256 `<app>.lock.json` for a built bundle."""
    try:
        out_lock = write_lockfile(
            Path(args.bundle), lockfile_path=getattr(args, "lockfile", None)
        )
    except (BuildGateError, PythonBundlerError) as exc:
        print(f"Lock Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"LOCKED {Path(args.bundle).resolve().name} -> {out_lock}")


def app_drift(args: argparse.Namespace) -> None:
    """Runs the 3-way (`base` vs. `ours` vs. `live`) CES-to-Git drift check."""
    json_output = bool(getattr(args, "json", False))
    try:
        report = check_drift(
            args.app,
            args.dist,
            args.live,
            lockfile_path=getattr(args, "lockfile", None),
            conflicts_dir=getattr(args, "conflicts_dir", None),
            accept_live=tuple(getattr(args, "accept_live", None) or ()),
            force_ours=tuple(getattr(args, "force_ours", None) or ()),
            git_mirror=getattr(args, "git_mirror", None),
        )
    except (BuildGateError, PythonBundlerError) as exc:
        if json_output:
            print(json.dumps({"error": str(exc), "passed": False}, indent=2))
        else:
            print(f"Drift Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_output:
        payload = {**report.to_dict(), "passed": not report.has_unresolved}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for res in report.resources:
            line = f"{res.status:<8} {res.resource_id}"
            if res.resolved_by:
                line += f" (resolved_by={res.resolved_by})"
            if res.conflict_path:
                line += f" [conflict={res.conflict_path}]"
            print(line)
        if report.git_mirror_commit:
            print(f"GIT_MIRROR {report.git_mirror_commit}")
    if report.has_unresolved:
        sys.exit(1)


def app_backport(args: argparse.Namespace) -> None:
    """Back-port live CES Console edits into brand `overrides.yaml`."""
    json_output = bool(getattr(args, "json", False))
    bp_fn = backport_live_to_source
    if bp_fn is None:
        from cxas_scrapi.build.backport import (  # noqa: PLC0415
            backport_live_to_source as bp_fn,
        )

    pr_body_out = getattr(args, "pr_body_out", None)
    dry_run = bool(getattr(args, "dry_run", False))

    try:
        result = bp_fn(
            args.core_dir,
            args.manifests_dir,
            args.live,
            brand_id=getattr(args, "brand", None),
            channel=getattr(args, "channel", None),
            environment=getattr(args, "env", None),
            dist_dir=getattr(args, "dist", None),
            lockfile_path=getattr(args, "lockfile", None),
            target=getattr(args, "target", None) or "brand",
            scope=getattr(args, "scope", None) or "channel",
            conflicts_dir=getattr(args, "conflicts_dir", None),
            force_live=tuple(getattr(args, "force_live", None) or ()),
            run_lint=not bool(getattr(args, "no_lint", False)),
            dry_run=dry_run,
            pr_body_out=pr_body_out,
            git_branch=getattr(args, "git_branch", None),
        )
    except (
        DriftGateError,
        BuildGateError,
        BuildError,
        ManifestError,
        PythonBundlerError,
    ) as exc:
        if json_output:
            print(
                json.dumps(
                    {"error": str(exc), "passed": False},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Backport Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_output:
        print(
            json.dumps(
                {**_to_jsonable(result), "passed": True},
                indent=2,
                sort_keys=True,
            )
        )
        return

    prefix = "[DRY RUN] " if result.dry_run else ""
    if not result.changes:
        print(
            f"{prefix}BACKPORT CLEAN: {result.app_id} "
            "(0 live differences vs source)"
        )
    else:
        for chg in result.changes:
            print(
                f"{prefix}BACKPORTED {chg.resource_id} -> {chg.target_file} "
                f"({chg.summary})"
            )
        print(f"{prefix}ROUND_TRIP_PROOF PASS ({result.app_id})")
        siblings = ", ".join(result.unchanged_sibling_bundles) or "none"
        print(f"UNCHANGED_SIBLINGS {siblings}")

    if result.git_branch:
        print(f"GIT_BRANCH {result.git_branch}")
    if pr_body_out and not result.dry_run:
        print(f"PR_BODY {Path(pr_body_out).resolve()}")


def app_package(args: argparse.Namespace) -> None:
    """Package a CES app directory into a deterministic ZIP archive."""
    raw_app_dir = Path(args.app_dir)
    raw_env_file = Path(args.env_file) if args.env_file else None
    abs_app_dir = raw_app_dir.absolute()
    zip_path = (
        Path(args.zip)
        if args.zip
        else abs_app_dir.parent.resolve() / f"{abs_app_dir.name}.zip"
    )

    try:
        out = package_app_archive(
            raw_app_dir,
            zip_path,
            raw_env_file,
            native_python_env=getattr(args, "native_python_env", False),
        )
        print(f"Packaged app to {out}")
    except (OSError, ValueError, PythonBundlerError) as exc:
        print(f"Package Error: {exc}", file=sys.stderr)
        sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register `build`, `lock`, `drift`, and `package` subcommands."""
    parser_build = subparsers.add_parser(
        "build",
        help="Build profile/channel CES apps from a core app and manifests.",
    )
    parser_build.add_argument(
        "--core-dir", required=True, help="Core app directory."
    )
    parser_build.add_argument(
        "--manifests-dir", required=True, help="Manifests directory."
    )
    parser_build.add_argument("--profile", help="Build profile ID.")
    parser_build.add_argument("--channel", help="Channel name.")
    parser_build.add_argument("--env", required=True, help="Environment name.")
    parser_build.add_argument(
        "--out", required=True, help="Output directory or root."
    )
    parser_build.add_argument(
        "--all", action="store_true", help="Build all profile/channel pairs."
    )
    parser_build.add_argument(
        "--zip", action="store_true", help="Package each built app to a ZIP."
    )
    parser_build.add_argument(
        "--composition-mode",
        choices=["canonical_overlay", "brand_native"],
        default=None,
        help=(
            "Override the profile's composition_mode "
            "(canonical_overlay or brand_native)."
        ),
    )
    parser_build.add_argument(
        "--json", action="store_true", help="Emit JSON build report(s)."
    )
    parser_build.add_argument(
        "--native-python-env",
        action="store_true",
        help="Synthesize pythonEnvFiles.yaml without mutating python_code.py.",
    )
    parser_build.add_argument(
        "--write-locks",
        action="store_true",
        help="Write <app>.lock.json lockfiles next to built bundles.",
    )
    parser_build.set_defaults(func=app_build)

    parser_lock = subparsers.add_parser(
        "lock", help="Write a normalized SHA-256 <app>.lock.json lockfile."
    )
    parser_lock.add_argument(
        "--bundle", required=True, help="Built app bundle directory."
    )
    parser_lock.add_argument(
        "--lockfile", default=None, help="Explicit output lockfile path."
    )
    parser_lock.set_defaults(func=app_lock)

    parser_drift = subparsers.add_parser(
        "drift",
        help="Run 3-way CES-to-Git drift, conflict, and orphan detection.",
    )
    parser_drift.add_argument(
        "--app", required=True, help="Logical app identifier."
    )
    parser_drift.add_argument(
        "--dist", required=True, help="Built bundle (or dist root)."
    )
    parser_drift.add_argument(
        "--live", required=True, help="Exported live CES app directory."
    )
    parser_drift.add_argument(
        "--lockfile", default=None, help="Baseline <app>.lock.json lockfile."
    )
    parser_drift.add_argument(
        "--conflicts-dir", default=None, help="Directory for .diff markers."
    )
    parser_drift.add_argument(
        "--accept-live",
        action="extend",
        nargs="+",
        default=[],
        help="Resource IDs to accept from the live export into the lockfile.",
    )
    parser_drift.add_argument(
        "--force-ours",
        action="extend",
        nargs="+",
        default=[],
        help="Resource IDs to force from the built bundle.",
    )
    parser_drift.add_argument(
        "--git-mirror",
        default=None,
        help="Optional local git repo for live/<app> snapshots.",
    )
    parser_drift.add_argument(
        "--json", action="store_true", help="Emit JSON drift report."
    )
    parser_drift.set_defaults(func=app_drift)

    parser_backport = subparsers.add_parser(
        "backport",
        help=(
            "Back-port live CES Console edits into brand overrides.yaml "
            "with round-trip SHA-256 proof."
        ),
    )
    parser_backport.add_argument(
        "--core-dir",
        required=True,
        help="Templated core app directory.",
    )
    parser_backport.add_argument(
        "--manifests-dir",
        required=True,
        help="Manifests root directory (profiles/, envs/).",
    )
    parser_backport.add_argument(
        "--live",
        "--live-dir",
        dest="live",
        required=True,
        help="Exported live CES app directory.",
    )
    parser_backport.add_argument(
        "--profile",
        "--brand",
        dest="brand",
        default=None,
        help=(
            "Target profile ID (auto-detected from deployments/*.yaml when "
            "omitted)."
        ),
    )
    parser_backport.add_argument(
        "--channel",
        default=None,
        help="Target channel name (auto-detected when omitted).",
    )
    parser_backport.add_argument(
        "--env",
        default=None,
        help="Environment name (auto-detected when omitted).",
    )
    parser_backport.add_argument(
        "--dist",
        default=None,
        help="Optional built bundle or dist root to rebuild and re-lock.",
    )
    parser_backport.add_argument(
        "--lockfile",
        default=None,
        help=(
            "Optional baseline <app>.lock.json path for 3-way conflict "
            "checking."
        ),
    )
    parser_backport.add_argument(
        "--target",
        choices=["brand"],
        default="brand",
        help=(
            "Backport destination ('brand' writes to "
            "manifests/profiles/<profile>/overrides.yaml)."
        ),
    )
    parser_backport.add_argument(
        "--scope",
        choices=["channel", "brand"],
        default="channel",
        help=(
            "Scope overrides to the edited channel ('channel', default) or "
            "across all channels of that brand ('brand')."
        ),
    )
    parser_backport.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Compute changes, PR body, and round-trip proof without "
            "modifying manifests, dist, lockfiles, or Git branches."
        ),
    )
    parser_backport.add_argument(
        "--conflicts-dir",
        default=None,
        help=(
            "Directory for .diff conflict files if Git and live diverged "
            "concurrently."
        ),
    )
    parser_backport.add_argument(
        "--force-live",
        action="extend",
        nargs="+",
        default=[],
        help=(
            "Resource IDs where live Console edits override concurrent Git "
            "edits."
        ),
    )
    parser_backport.add_argument(
        "--no-lint",
        action="store_true",
        default=False,
        help="Skip cxas lint gate during backport.",
    )
    parser_backport.add_argument(
        "--pr-body-out",
        default=None,
        help="Optional file path to write a Pull Request Markdown summary.",
    )
    parser_backport.add_argument(
        "--git-branch",
        default=None,
        help=(
            "Optional local Git branch name to create/switch to after "
            "verification."
        ),
    )
    parser_backport.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    parser_backport.set_defaults(func=app_backport)

    parser_package = subparsers.add_parser(
        "package", help="Package an app directory into a deterministic ZIP."
    )
    parser_package.add_argument(
        "--app-dir", required=True, help="App directory."
    )
    parser_package.add_argument("--zip", help="Output ZIP file path.")
    parser_package.add_argument(
        "--env-file", help="Custom environment.json file."
    )
    parser_package.add_argument(
        "--native-python-env",
        action="store_true",
        help="Synthesize pythonEnvFiles.yaml without mutating python_code.py.",
    )
    parser_package.set_defaults(func=app_package)
