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

"""CLI subcommands for building, auditing, locking, and diffing CES bundles."""

import argparse
import dataclasses
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from cxas_scrapi.build import (
    BlueprintCompilerError,
    FirewallError,
    check_drift,
    compile_blueprint,
    format_build_table,
    run_firewall,
    write_lockfile,
)
from cxas_scrapi.build.compiler import BuildError, build_all, build_app
from cxas_scrapi.core.packaging import package_app_archive
from cxas_scrapi.core.python_bundler import PythonBundlerError


def _to_jsonable(obj: Any) -> Any:
    """Recursively converts build/firewall/drift objects to JSON-safe values."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _to_jsonable(obj.to_dict())
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _serialize_violation(violation: Any) -> dict[str, str]:
    """Serializes a FirewallViolation or dict into a canonical dict."""
    if isinstance(violation, dict):
        return {
            "message": str(violation.get("message", "")),
            "path": str(violation.get("path", "")),
            "rule_id": str(violation.get("rule_id", "")),
        }
    return {
        "message": str(getattr(violation, "message", "")),
        "path": str(getattr(violation, "path", "")),
        "rule_id": str(getattr(violation, "rule_id", "")),
    }


def app_build(args: argparse.Namespace) -> None:
    """Compiles either a polymorphic blueprint or a core+manifests app."""
    blueprint_dir = getattr(args, "blueprint", None)
    json_output = bool(getattr(args, "json", False))

    if blueprint_dir:
        try:
            results = compile_blueprint(
                blueprint_dir,
                args.out,
                brand=getattr(args, "brand", None),
                modality=getattr(args, "modality", None),
                write_locks=bool(getattr(args, "write_locks", False)),
                package_zips=bool(getattr(args, "zip", False)),
            )
        except (FirewallError, BlueprintCompilerError) as exc:
            if json_output:
                violations_raw = getattr(exc, "violations", [])
                print(
                    json.dumps(
                        {
                            "error": str(exc),
                            "passed": False,
                            "violations": [
                                _serialize_violation(v) for v in violations_raw
                            ],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(f"Build Error: {exc}", file=sys.stderr)
            sys.exit(1)

        total_violations = sum(
            len(getattr(r, "violations", [])) for r in results
        )
        if json_output:
            print(
                json.dumps(
                    {
                        "apps": _to_jsonable(results),
                        "passed": total_violations == 0,
                        "total_violations": total_violations,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(format_build_table(results))
        return

    core_dir = getattr(args, "core_dir", None)
    manifests_dir = getattr(args, "manifests_dir", None)
    env = getattr(args, "env", None)
    if not core_dir or not manifests_dir or not env:
        print(
            "Error: either --blueprint or (--core-dir, --manifests-dir, "
            "and --env) is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    native_python_env = bool(getattr(args, "native_python_env", False))
    strip_bridge = getattr(args, "strip_bridge", None)
    prune_dangling_lines = bool(getattr(args, "prune_dangling_lines", False))
    runtime_policy = getattr(args, "runtime_policy", None)
    tuning_overlay = getattr(args, "tuning_overlay", None)
    emit_deployments = not bool(getattr(args, "no_deployments", False))
    write_locks = bool(getattr(args, "write_locks", False))
    explicit_fw_profile = getattr(args, "firewall_profile", None)
    run_fw = bool(getattr(args, "firewall", False))
    eff_fw_profile = (
        explicit_fw_profile
        if explicit_fw_profile is not None
        else ("standard" if run_fw else None)
    )

    try:
        if getattr(args, "all", False):
            reports = build_all(
                core_dir=core_dir,
                manifests_dir=manifests_dir,
                out_root=args.out,
                environment=env,
                package_zip=bool(getattr(args, "zip", False)),
                native_python_env=native_python_env,
                strip_bridge=strip_bridge,
                prune_dangling_lines=prune_dangling_lines,
                runtime_policy=runtime_policy,
                tuning_overlay=tuning_overlay,
                emit_deployments=emit_deployments,
                write_locks=write_locks,
                firewall_profile=eff_fw_profile,
            )
            if json_output:
                print(json.dumps([r.model_dump(mode="json") for r in reports]))
            else:
                for r in reports:
                    print(f"Built {r.brand_id} {r.channel} to {r.out_dir}")
                    if r.warnings:
                        for w in r.warnings:
                            print(f"  Warning: {w}")
        else:
            brand = getattr(args, "brand", None)
            channel = getattr(args, "channel", None)
            if not brand or not channel:
                print(
                    "Error: --brand and --channel are required unless --all "
                    "is specified."
                )
                sys.exit(1)

            report = build_app(
                core_dir=core_dir,
                manifests_dir=manifests_dir,
                brand_id=brand,
                channel=channel,
                environment=env,
                out_dir=args.out,
                package_zip=bool(getattr(args, "zip", False)),
                native_python_env=native_python_env,
                strip_bridge=strip_bridge,
                prune_dangling_lines=prune_dangling_lines,
                runtime_policy=runtime_policy,
                tuning_overlay=tuning_overlay,
                emit_deployments=emit_deployments,
                write_lock=write_locks,
                firewall_profile=eff_fw_profile,
            )
            if json_output:
                print(report.model_dump_json())
            else:
                print(
                    f"Built {report.brand_id} {report.channel} "
                    f"to {report.out_dir}"
                )
                if report.warnings:
                    for w in report.warnings:
                        print(f"  Warning: {w}")
    except (
        BuildError,
        FirewallError,
        BlueprintCompilerError,
        PythonBundlerError,
    ) as exc:
        if json_output and isinstance(exc, FirewallError):
            violations_raw = getattr(exc, "violations", [])
            print(
                json.dumps(
                    {
                        "error": str(exc),
                        "passed": False,
                        "violations": [
                            _serialize_violation(v) for v in violations_raw
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Build Error: {exc}", file=sys.stderr)
        sys.exit(1)


def app_firewall(args: argparse.Namespace) -> None:
    """Runs static architecture firewall (`FW01`..`FW10`) on a bundle."""
    json_output = bool(getattr(args, "json", False))
    blueprint_dir = getattr(args, "blueprint", None)
    profile = getattr(args, "profile", None) or "strict"

    try:
        violations = run_firewall(
            args.bundle,
            blueprint_dir=blueprint_dir,
            profile=profile,
        )
    except BlueprintCompilerError as exc:
        if json_output:
            print(
                json.dumps(
                    {"error": str(exc), "passed": False},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Firewall Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_output:
        print(
            json.dumps(
                {
                    "bundle": str(Path(args.bundle).resolve()),
                    "passed": len(violations) == 0,
                    "total_violations": len(violations),
                    "violations": [_serialize_violation(v) for v in violations],
                },
                indent=2,
                sort_keys=True,
            )
        )
        if violations:
            sys.exit(1)
        return

    if violations:
        for violation in violations:
            print(str(violation), file=sys.stderr)
        sys.exit(1)
    print(f"FIREWALL PASS: {args.bundle} (0 violations)")


def app_lock(args: argparse.Namespace) -> None:
    """Writes normalized SHA-256 `<app>.lock.json` lockfiles."""
    dist_arg = getattr(args, "dist", None)
    bundle_arg = getattr(args, "bundle", None)
    lockfile_arg = getattr(args, "lockfile", None)

    if not dist_arg and not bundle_arg:
        print("Error: either --dist or --bundle is required.", file=sys.stderr)
        sys.exit(1)

    try:
        if bundle_arg:
            bundle_path = Path(bundle_arg)
            out_lock = write_lockfile(bundle_path, lockfile_path=lockfile_arg)
            print(f"LOCKED {bundle_path.resolve().name} -> {out_lock}")

        if dist_arg:
            dist_path = Path(dist_arg)
            if not dist_path.is_dir():
                raise BlueprintCompilerError(
                    f"Distribution directory does not exist: {dist_path}"
                )
            lock_sig = inspect.signature(write_lockfile).parameters
            supports_app_id = "app_id" in lock_sig or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in lock_sig.values()
            )
            for sub_dir in sorted(dist_path.iterdir()):
                if not sub_dir.is_dir() or sub_dir.name.startswith("."):
                    continue
                if (sub_dir / "app.json").is_file():
                    out_lock = write_lockfile(sub_dir)
                    print(f"LOCKED {sub_dir.name} -> {out_lock}")
                else:
                    for chan_dir in sorted(sub_dir.iterdir()):
                        if (
                            chan_dir.is_dir()
                            and not chan_dir.name.startswith(".")
                            and (chan_dir / "app.json").is_file()
                        ):
                            nested_id = f"{sub_dir.name}/{chan_dir.name}"
                            out_lock = (
                                write_lockfile(chan_dir, app_id=nested_id)
                                if supports_app_id
                                else write_lockfile(chan_dir)
                            )
                            print(f"LOCKED {nested_id} -> {out_lock}")
    except BlueprintCompilerError as exc:
        print(f"Lock Error: {exc}", file=sys.stderr)
        sys.exit(1)


def app_drift(args: argparse.Namespace) -> None:
    """Runs 3-way (`base` vs. `ours` vs. `live`) CES-to-Git drift check."""
    json_output = bool(getattr(args, "json", False))
    firewall_profile = getattr(args, "firewall_profile", None) or "strict"

    try:
        report = check_drift(
            args.app,
            args.dist,
            args.live,
            lockfile_path=getattr(args, "lockfile", None),
            blueprint_dir=getattr(args, "blueprint", None),
            conflicts_dir=getattr(args, "conflicts_dir", None),
            accept_live=tuple(getattr(args, "accept_live", None) or ()),
            force_ours=tuple(getattr(args, "force_ours", None) or ()),
            git_mirror=getattr(args, "git_mirror", None),
            firewall_profile=firewall_profile,
        )
    except (FirewallError, BlueprintCompilerError) as exc:
        if json_output:
            print(
                json.dumps(
                    {"error": str(exc), "passed": False},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Drift Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_output:
        payload = {
            **report.to_dict(),
            "passed": not report.has_unresolved,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        if report.has_unresolved:
            sys.exit(1)
        return

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
    """Packages an app bundle directory into a deterministic ZIP archive."""
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
            native_python_env=bool(getattr(args, "native_python_env", False)),
        )
        print(f"Packaged app to {out}")
    except Exception as e:
        print(f"Package Error: {e}", file=sys.stderr)
        sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Registers build, package, firewall, lock, and drift CLI subcommands."""
    parser_build = subparsers.add_parser(
        "build",
        help="Compile polymorphic blueprint or core app with manifests.",
    )
    parser_build.add_argument(
        "--blueprint",
        default=None,
        help=(
            "Polymorphic blueprint directory "
            "(blueprint.yaml, brands/, modalities/, runtime_policy.yaml)."
        ),
    )
    parser_build.add_argument(
        "--core-dir",
        required=False,
        default=None,
        help="Core app directory.",
    )
    parser_build.add_argument(
        "--manifests-dir",
        required=False,
        default=None,
        help="Manifests directory.",
    )
    parser_build.add_argument("--brand", default=None, help="Brand ID.")
    parser_build.add_argument("--channel", default=None, help="Channel name.")
    parser_build.add_argument(
        "--modality",
        default=None,
        help="Optional modality filter for --blueprint builds.",
    )
    parser_build.add_argument(
        "--env",
        required=False,
        default=None,
        help="Environment name.",
    )
    parser_build.add_argument(
        "--out", required=True, help="Output directory or root."
    )
    parser_build.add_argument(
        "--all", action="store_true", help="Build all brand/channel pairs."
    )
    parser_build.add_argument(
        "--write-locks",
        action="store_true",
        default=False,
        help=(
            "Write <app>.lock.json SHA-256 lockfiles alongside compiled "
            "bundles."
        ),
    )
    parser_build.add_argument(
        "--zip", action="store_true", help="Package to zip."
    )
    parser_build.add_argument(
        "--json", action="store_true", help="JSON output."
    )
    parser_build.add_argument(
        "--native-python-env",
        action="store_true",
        help="Native python env.",
    )
    parser_build.add_argument(
        "--strip-bridge",
        choices=["presentation", "search", "killswitch", "all"],
        default=None,
        help="Strip bridge shims.",
    )
    parser_build.add_argument(
        "--runtime-policy",
        default=None,
        help="Path to runtime_policy.yaml for CEL TransferRule synthesis.",
    )
    parser_build.add_argument(
        "--tuning-overlay",
        default=None,
        help="Path to tuning_overlay.yaml for modality prompt append rules.",
    )
    parser_build.add_argument(
        "--prune-dangling-lines",
        action="store_true",
        default=False,
        help="Auto-strip instruction lines referencing pruned agents or tools.",
    )
    parser_build.add_argument(
        "--no-deployments",
        action="store_true",
        default=False,
        help="Skip emitting deployments/*.yaml manifests.",
    )
    parser_build.add_argument(
        "--firewall",
        action="store_true",
        default=False,
        help="Run static architecture firewall on --core-dir builds.",
    )
    parser_build.add_argument(
        "--firewall-profile",
        choices=["strict", "standard"],
        default=None,
        help=(
            "Firewall profile for --core-dir builds "
            "(defaults to standard when --firewall is set)."
        ),
    )
    parser_build.set_defaults(func=app_build)

    parser_firewall = subparsers.add_parser(
        "firewall",
        help="Run static architecture firewall (FW01-FW10) on an app bundle.",
    )
    parser_firewall.add_argument(
        "--bundle",
        required=True,
        help="Compiled app bundle directory to inspect.",
    )
    parser_firewall.add_argument(
        "--blueprint",
        default=None,
        help="Optional blueprint directory for brand/modality checks.",
    )
    parser_firewall.add_argument(
        "--profile",
        choices=["strict", "standard"],
        default="strict",
        help=(
            "Firewall enforcement profile "
            "(strict enforces zero-Python FW01/FW02; standard allows Python)."
        ),
    )
    parser_firewall.add_argument(
        "--json",
        action="store_true",
        help="JSON output.",
    )
    parser_firewall.set_defaults(func=app_firewall)

    parser_lock = subparsers.add_parser(
        "lock",
        help="Generate normalized SHA-256 <app>.lock.json lockfiles.",
    )
    parser_lock.add_argument(
        "--dist",
        default=None,
        help="Distribution directory containing compiled <app>/ bundles.",
    )
    parser_lock.add_argument(
        "--bundle",
        default=None,
        help="Single compiled app bundle directory to lock.",
    )
    parser_lock.add_argument(
        "--lockfile",
        default=None,
        help="Explicit output path for --bundle lockfile.",
    )
    parser_lock.set_defaults(func=app_lock)

    parser_drift = subparsers.add_parser(
        "drift",
        help="Run 3-way CES-to-Git drift, conflict, and orphan detection.",
    )
    parser_drift.add_argument(
        "--app",
        required=True,
        help="Logical app identifier (<brand>-<modality>).",
    )
    parser_drift.add_argument(
        "--dist",
        required=True,
        help="Compiled distribution directory or app bundle path.",
    )
    parser_drift.add_argument(
        "--live",
        required=True,
        help="Exported live CES app directory.",
    )
    parser_drift.add_argument(
        "--lockfile",
        default=None,
        help="Optional baseline <app>.lock.json lockfile path.",
    )
    parser_drift.add_argument(
        "--blueprint",
        default=None,
        help="Optional blueprint directory for firewall validation.",
    )
    parser_drift.add_argument(
        "--conflicts-dir",
        default=None,
        help="Directory for .diff conflict marker files.",
    )
    parser_drift.add_argument(
        "--accept-live",
        action="extend",
        nargs="+",
        default=[],
        help="Resource IDs to accept from live export into baseline lockfile.",
    )
    parser_drift.add_argument(
        "--force-ours",
        action="extend",
        nargs="+",
        default=[],
        help="Resource IDs to force from compiled bundle over live edits.",
    )
    parser_drift.add_argument(
        "--git-mirror",
        default=None,
        help=(
            "Optional local git repository directory for live/<app> snapshots."
        ),
    )
    parser_drift.add_argument(
        "--profile",
        "--firewall-profile",
        dest="firewall_profile",
        choices=["strict", "standard"],
        default="strict",
        help="Static firewall profile used when validating --accept-live updates.",
    )
    parser_drift.add_argument(
        "--json",
        action="store_true",
        help="JSON output.",
    )
    parser_drift.set_defaults(func=app_drift)

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
        "--native-python-env",
        action="store_true",
        help="Native python env.",
    )
    parser_package.set_defaults(func=app_package)
