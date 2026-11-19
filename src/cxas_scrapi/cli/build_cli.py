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

"""CLI script for running CXAS SCRAPI build and package."""

import argparse
import json
import sys
from pathlib import Path

from cxas_scrapi.build.bundle_io import BuildGateError
from cxas_scrapi.build.compiler import BuildError, build_all, build_app
from cxas_scrapi.build.drift import check_drift, write_lockfile
from cxas_scrapi.core.packaging import package_app_archive


def app_build(args: argparse.Namespace) -> None:
    try:
        if args.all:
            reports = build_all(
                core_dir=args.core_dir,
                manifests_dir=args.manifests_dir,
                out_root=args.out,
                environment=args.env,
                package_zip=args.zip,
                native_python_env=getattr(args, "native_python_env", False),
                strip_bridge=getattr(args, "strip_bridge", None),
                write_locks=getattr(args, "write_locks", False),
            )
            if args.json:
                print(json.dumps([r.model_dump(mode="json") for r in reports]))
            else:
                for r in reports:
                    print(f"Built {r.brand_id} {r.channel} to {r.out_dir}")
                    if r.warnings:
                        for w in r.warnings:
                            print(f"  Warning: {w}")
        else:
            if not args.brand or not args.channel:
                print(
                    "Error: --brand and --channel are required unless --all is specified."
                )
                sys.exit(1)

            report = build_app(
                core_dir=args.core_dir,
                manifests_dir=args.manifests_dir,
                brand_id=args.brand,
                channel=args.channel,
                environment=args.env,
                out_dir=args.out,
                package_zip=args.zip,
                native_python_env=getattr(args, "native_python_env", False),
                strip_bridge=getattr(args, "strip_bridge", None),
                write_lock=getattr(args, "write_locks", False),
            )
            if args.json:
                print(report.model_dump_json())
            else:
                print(
                    f"Built {report.brand_id} {report.channel} to {report.out_dir}"
                )
                if report.warnings:
                    for w in report.warnings:
                        print(f"  Warning: {w}")
    except BuildError as e:
        print(f"Build Error: {e}", file=sys.stderr)
        sys.exit(1)


def app_lock(args: argparse.Namespace) -> None:
    """Writes a normalized SHA-256 `<app>.lock.json` for a compiled bundle."""
    try:
        out_lock = write_lockfile(
            Path(args.bundle), lockfile_path=getattr(args, "lockfile", None)
        )
    except BuildGateError as exc:
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
    except BuildGateError as exc:
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


def app_package(args: argparse.Namespace) -> None:
    app_dir = Path(args.app_dir).resolve()
    env_file = Path(args.env_file).resolve() if args.env_file else None

    zip_path = (
        Path(args.zip).resolve()
        if args.zip
        else app_dir.with_name(f"{app_dir.name}.zip")
    )

    try:
        out = package_app_archive(
            app_dir,
            zip_path,
            env_file,
            native_python_env=getattr(args, "native_python_env", False),
        )
        print(f"Packaged app to {out}")
    except Exception as e:
        print(f"Package Error: {e}", file=sys.stderr)
        sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> None:
    # Build
    parser_build = subparsers.add_parser(
        "build", help="Compile core app with manifests."
    )
    parser_build.add_argument(
        "--core-dir", required=True, help="Core app directory."
    )
    parser_build.add_argument(
        "--manifests-dir", required=True, help="Manifests directory."
    )
    parser_build.add_argument("--brand", help="Brand ID.")
    parser_build.add_argument("--channel", help="Channel name.")
    parser_build.add_argument("--env", required=True, help="Environment name.")
    parser_build.add_argument(
        "--out", required=True, help="Output directory or root."
    )
    parser_build.add_argument(
        "--all", action="store_true", help="Build all brand/channel pairs."
    )
    parser_build.add_argument(
        "--zip", action="store_true", help="Package to zip."
    )
    parser_build.add_argument(
        "--json", action="store_true", help="JSON output."
    )
    parser_build.add_argument(
        "--native-python-env", action="store_true", help="Native python env."
    )
    parser_build.add_argument(
        "--strip-bridge",
        choices=["presentation", "search", "killswitch", "all"],
        help="Strip bridge shims.",
    )
    parser_build.add_argument(
        "--write-locks",
        action="store_true",
        help="Write <app>.lock.json lockfiles next to compiled bundles.",
    )
    parser_build.set_defaults(func=app_build)

    # Lock
    parser_lock = subparsers.add_parser(
        "lock", help="Write a normalized SHA-256 <app>.lock.json lockfile."
    )
    parser_lock.add_argument(
        "--bundle", required=True, help="Compiled app bundle directory."
    )
    parser_lock.add_argument(
        "--lockfile", default=None, help="Explicit output lockfile path."
    )
    parser_lock.set_defaults(func=app_lock)

    # Drift
    parser_drift = subparsers.add_parser(
        "drift",
        help="Run 3-way CES-to-Git drift, conflict, and orphan detection.",
    )
    parser_drift.add_argument(
        "--app", required=True, help="Logical app identifier."
    )
    parser_drift.add_argument(
        "--dist", required=True, help="Compiled bundle (or dist root)."
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
        nargs="+",
        default=[],
        help="Resource IDs to accept from the live export into the lockfile.",
    )
    parser_drift.add_argument(
        "--force-ours",
        nargs="+",
        default=[],
        help="Resource IDs to force from the compiled bundle.",
    )
    parser_drift.add_argument(
        "--git-mirror",
        default=None,
        help="Optional local git repo for live/<app> snapshots.",
    )
    parser_drift.add_argument("--json", action="store_true", help="JSON.")
    parser_drift.set_defaults(func=app_drift)

    # Package
    parser_package = subparsers.add_parser(
        "package", help="Package app to deterministic ZIP."
    )
    parser_package.add_argument(
        "--app-dir", required=True, help="App directory."
    )
    parser_package.add_argument("--zip", help="Output zip file.")
    parser_package.add_argument(
        "--env-file", help="Custom environment.json file."
    )
    parser_package.add_argument(
        "--native-python-env", action="store_true", help="Native python env."
    )
    parser_package.set_defaults(func=app_package)
