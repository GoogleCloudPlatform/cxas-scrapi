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

from cxas_scrapi.build.compiler import BuildError, build_all, build_app
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


def app_package(args: argparse.Namespace) -> None:
    app_dir = Path(args.app_dir).resolve()
    env_file = Path(args.env_file).resolve() if args.env_file else None

    zip_path = (
        Path(args.zip).resolve()
        if args.zip
        else app_dir.with_name(f"{app_dir.name}.zip")
    )

    try:
        out = package_app_archive(app_dir, zip_path, env_file)
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
    parser_build.set_defaults(func=app_build)

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
    parser_package.set_defaults(func=app_package)
