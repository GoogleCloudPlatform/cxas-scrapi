"""CLI subcommands for managing CXAS Apps."""

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

import argparse
import inspect
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from cxas_scrapi.build import BlueprintCompilerError, enforce_pre_push_gate
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    strip_shared_bundles,
)
from cxas_scrapi.core.versions import Versions
from cxas_scrapi.utils.env_vars import (
    collect_env_var_declaration_names,
    extract_env_variable_defaults,
    preserve_env_var_placeholders_on_pull,
)

logger = logging.getLogger(__name__)


def _resolve_app_args(
    app_identifier: str, args: argparse.Namespace
) -> tuple[Apps, str, str]:
    """Resolves project, location, Apps client, app_name, and display_name."""
    project_id = (
        Common._get_project_id(app_identifier)
        if Common._get_project_id(app_identifier)
        else getattr(args, "project_id", None)
    )
    location = (
        Common._get_location(app_identifier)
        if Common._get_location(app_identifier)
        else getattr(args, "location", None)
    )

    if not project_id or not location:
        print(
            "Error: Could not determine project_id or location. Provide "
            "--project_id and --location if using a display name."
        )
        sys.exit(1)

    apps_client = Apps(project_id=project_id, location=location)
    app_name = app_identifier
    display_name = app_identifier

    if "projects/" not in app_identifier:
        app = apps_client.get_app_by_display_name(app_identifier)
        if app:
            app_name = app.name
            display_name = app.display_name
        else:
            print(f"App '{app_identifier}' not found.")
            sys.exit(1)

    return apps_client, app_name, display_name


def _handle_import_result(result: Any, success_verb: str) -> str | None:
    """Helper to wait for import LRO and report the outcome.

    The server excludes any resource that fails validation from the imported
    app and records the reason in ``ImportAppResponse.warnings``. Those
    messages are the only indication that part of the import did not land, so
    they are reported on stderr and treated as a failure rather than being
    discarded behind a success message.
    """
    if hasattr(result, "result"):
        print("Waiting for import to complete...")
        app = result.result()
    else:
        app = result

    app_name = getattr(app, "name", None)
    warnings = list(getattr(app, "warnings", None) or [])

    if warnings:
        print(
            f"ERROR: {len(warnings)} resource(s) were rejected by the server "
            "and are not present in the imported app:",
            file=sys.stderr,
        )
        for warning in warnings:
            print(f"  - {warning}", file=sys.stderr)
        print(
            "Fix the reported resources and push again.",
            file=sys.stderr,
        )
        sys.exit(1)

    if app_name:
        print(f"Successfully {success_verb}: {app_name}")
    else:
        print(f"Successfully {success_verb}.")
    return app_name


def app_pull(args: argparse.Namespace) -> None:
    """Handles the 'pull' command."""
    print(f"Pulling app: {args.app}")

    apps_client, app_name, _ = _resolve_app_args(args.app, args)

    version_id = getattr(args, "version_id", None)
    app_version = None
    if version_id:
        versions_client = Versions(app_name=app_name)
        try:
            app_version = versions_client.resolve_version_name(version_id)
        except ValueError as e:
            print(f"Error resolving version: {e}")
            sys.exit(1)

    _app_pull(
        apps_client,
        app_name,
        args.target_dir,
        getattr(args, "overwrite", False),
        app_version=app_version,
        app_dir_name=getattr(args, "app_dir_name", None),
        args=args,
    )


def _app_pull(
    apps_client: Apps,
    app_name: str,
    target_dir: str,
    overwrite: bool = False,
    app_version: str | None = None,
    app_dir_name: str | None = None,
    args: argparse.Namespace | None = None,
) -> None:
    """Helper to pull an app from CXAS."""
    try:
        dest_app_root: str | None = None
        if app_dir_name is not None:
            stripped_dir_name = app_dir_name.strip()
            if not stripped_dir_name:
                raise ValueError("--app-dir-name cannot be empty.")
            abs_target = os.path.abspath(target_dir)
            abs_dest = os.path.abspath(
                os.path.join(target_dir, stripped_dir_name)
            )
            if (
                os.path.isabs(stripped_dir_name)
                or os.path.commonpath([abs_target, abs_dest]) != abs_target
            ):
                raise ValueError(
                    f"Invalid --app-dir-name '{app_dir_name}': must be a "
                    "relative path within --target-dir."
                )
            dest_app_root = os.path.normpath(
                os.path.join(target_dir, stripped_dir_name)
            )

        # Export the app
        print("Exporting app from CXAS...")
        lro = apps_client.export_app(app_name=app_name, app_version=app_version)
        response = lro.result()

        # Determine the target directory
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)

        # Extract content to target directory.
        top_dir = ""
        with zipfile.ZipFile(io.BytesIO(response.app_content)) as z:
            export_members = list(z.namelist())
            if export_members:
                first_part = export_members[0].split("/")[0]
                has_top_dir = (
                    bool(first_part)
                    and any("/" in m for m in export_members)
                    and all(
                        m == first_part or m.startswith(first_part + "/")
                        for m in export_members
                    )
                )
                top_dir = first_part if has_top_dir else ""

            pulled_app_root = (
                dest_app_root
                if dest_app_root is not None
                else (
                    os.path.join(target_dir, top_dir) if top_dir else target_dir
                )
            )
            prev_env_vars = collect_env_var_declaration_names(pulled_app_root)
            prev_env_defaults: dict[str, Any] = {}
            prev_env_path = os.path.join(pulled_app_root, "environment.json")
            if prev_env_vars and os.path.isfile(prev_env_path):
                try:
                    with open(prev_env_path, encoding="utf-8") as ef:
                        prev_env_defaults, _ = extract_env_variable_defaults(
                            json.load(ef)
                        )
                except (OSError, ValueError):
                    prev_env_defaults = {}

            if dest_app_root is not None:
                os.makedirs(dest_app_root, exist_ok=True)
                abs_dest_root = os.path.abspath(dest_app_root)
                prefix = f"{top_dir}/" if top_dir else ""
                for info in z.infolist():
                    rel_name = info.filename
                    if top_dir:
                        if rel_name in (top_dir, prefix):
                            continue
                        if rel_name.startswith(prefix):
                            rel_name = rel_name[len(prefix) :]
                    if not rel_name:
                        continue
                    target_member_path = os.path.abspath(
                        os.path.join(dest_app_root, rel_name)
                    )
                    if (
                        os.path.commonpath([abs_dest_root, target_member_path])
                        != abs_dest_root
                    ):
                        raise ValueError(
                            "Path traversal detected in archive entry: "
                            f"{info.filename}"
                        )
                    info.filename = rel_name
                    z.extract(info, path=dest_app_root)
            else:
                z.extractall(target_dir)

        # Handle overwrite logic if requested
        if overwrite and export_members:
            if dest_app_root is not None:
                app_root = dest_app_root
            else:
                top_dir = export_members[0].split("/")[0]
                app_root = os.path.join(target_dir, top_dir)

            if os.path.exists(app_root):
                # Build set of exported paths relative to app_root
                export_set = set()
                for member in export_members:
                    rel_path = (
                        os.path.relpath(member, top_dir)
                        if top_dir
                        else member.rstrip("/")
                    )
                    if rel_path and rel_path != ".":
                        export_set.add(rel_path)

                # Identify root level items in export (direct children of
                # app_root)
                export_root_items = set()
                for member in export_members:
                    if top_dir:
                        parts = member.split("/")
                        if len(parts) > 1 and parts[0] == top_dir and parts[1]:
                            export_root_items.add(parts[1])
                    else:
                        first = member.split("/")[0]
                        if first:
                            export_root_items.add(first)

                # Walk the local app root bottom-up to remove files then dirs
                for root, dirs, files in os.walk(app_root, topdown=False):
                    rel_root = os.path.relpath(root, app_root)
                    if rel_root == ".":
                        rel_root = ""

                    # Check files
                    for name in files:
                        local_rel_path = (
                            os.path.join(rel_root, name) if rel_root else name
                        )
                        parts = local_rel_path.split("/")

                        if len(parts) > 1:
                            # It's a non-root level file
                            root_item = parts[0]
                            # Only remove if it belongs to a folder that WAS in
                            # the export
                            if root_item in export_root_items:  # noqa: SIM102
                                if local_rel_path not in export_set:
                                    file_path = os.path.join(root, name)
                                    print(
                                        "Removing non-exported file: "
                                        f"{file_path}"
                                    )
                                    os.remove(file_path)

                    # Check directories (remove if empty and not in export)
                    for name in dirs:
                        local_rel_path = (
                            os.path.join(rel_root, name) if rel_root else name
                        )
                        parts = local_rel_path.split("/")

                        if len(parts) > 1:
                            root_item = parts[0]
                            if root_item in export_root_items:  # noqa: SIM102
                                if (
                                    local_rel_path not in export_set
                                    and (local_rel_path + "/") not in export_set
                                ):
                                    dir_path = os.path.join(root, name)
                                    # Only remove if it's empty (we didn't keep
                                    # any files inside it)
                                    if not os.listdir(dir_path):
                                        print(
                                            "Removing empty non-exported dir: "
                                            f"{dir_path}"
                                        )
                                        os.rmdir(dir_path)

        if dest_app_root is not None:
            app_root_for_strip = dest_app_root
        else:
            top_dir = export_members[0].split("/")[0] if export_members else ""
            app_root_for_strip = (
                os.path.join(target_dir, top_dir) if top_dir else target_dir
            )

        force_strip = getattr(args, "force", False) if args else False
        strip_shared_bundles(Path(app_root_for_strip), force=force_strip)

        if prev_env_vars:
            preserved = preserve_env_var_placeholders_on_pull(
                pulled_app_root,
                prev_env_vars,
                previous_env_defaults=prev_env_defaults,
            )
            if preserved:
                print(
                    "Preserved $env_var placeholders for "
                    f"{len(preserved)} variable(s) in app config and updated "
                    "environment.json."
                )

        print("Successfully pulled app.")

    except Exception as e:
        print(f"Failed to pull app: {e}")
        sys.exit(1)


def _export_live_app_to_dir(
    apps_client: Apps,
    target_app_name: str,
    temp_root: Path,
) -> Path:
    """Exports an existing remote CES app as JSON and unwraps root wrapper."""
    tmp_zip = temp_root / "live_export.zip"
    extract_dir = temp_root / "live_extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes: bytes | bytearray | None = None
    try:
        sig_params = inspect.signature(apps_client.export_app).parameters
        if "app_id" in sig_params and "app_name" not in sig_params:
            call_kwargs: dict[str, Any] = {
                "app_id": target_app_name,
                "export_format": "JSON",
            }
            if "filepath" in sig_params:
                call_kwargs["filepath"] = str(tmp_zip)
            export_res = apps_client.export_app(**call_kwargs)
        else:
            export_res = apps_client.export_app(
                app_name=target_app_name,
                local_path=str(tmp_zip),
                export_format="JSON",
            )

        has_file = tmp_zip.is_file() and tmp_zip.stat().st_size > 0
        if not has_file:
            raw_bytes = getattr(export_res, "app_content", None)
            if not isinstance(raw_bytes, (bytes, bytearray)) and hasattr(
                export_res, "result"
            ):
                lro_resp = export_res.result()
                raw_bytes = getattr(lro_resp, "app_content", None)
            if not isinstance(raw_bytes, (bytes, bytearray)):
                raise BlueprintCompilerError(
                    f"Failed to export live app {target_app_name!r} for drift "
                    "gate."
                )
    except BlueprintCompilerError:
        raise
    except Exception as exc:
        raise BlueprintCompilerError(
            f"Failed to export live app {target_app_name!r} for drift gate: "
            f"{exc}"
        ) from exc

    abs_extract = extract_dir.resolve()
    try:
        zf_source: Path | io.BytesIO = (
            tmp_zip if has_file else io.BytesIO(bytes(raw_bytes or b""))
        )
        with zipfile.ZipFile(zf_source, "r") as zf:
            for info in zf.infolist():
                dest_member = (abs_extract / info.filename).resolve()
                if os.path.commonpath(
                    [str(abs_extract), str(dest_member)]
                ) != str(abs_extract):
                    raise BlueprintCompilerError(
                        f"Unsafe archive path in live export: {info.filename}"
                    )
            zf.extractall(abs_extract)
    except BlueprintCompilerError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BlueprintCompilerError(
            f"Invalid ZIP archive exported from live app "
            f"{target_app_name!r}: {exc}"
        ) from exc

    if (abs_extract / "app.json").is_file() or (
        abs_extract / "app.yaml"
    ).is_file():
        return abs_extract

    subdirs = [
        p
        for p in sorted(abs_extract.iterdir())
        if p.is_dir() and p.name != "__MACOSX"
    ]
    files = [p for p in abs_extract.iterdir() if p.is_file()]
    if len(subdirs) == 1 and not files:
        return subdirs[0]
    return abs_extract


def _maybe_run_pre_push_gate(
    app_dir: str,
    apps_client: Apps | None,
    target_app_name: str | None,
    args: argparse.Namespace | None,
    *,
    firewall_already_ran: bool = False,
) -> None:
    """Runs static firewall and/or 3-way drift gate prior to importing."""
    if args is None:
        return

    run_fw = bool(getattr(args, "firewall", False)) and not firewall_already_ran
    live_dir = getattr(args, "live_dir", None)
    lockfile = getattr(args, "lockfile", None)
    want_drift = bool(
        getattr(args, "check_drift", False)
        or live_dir is not None
        or lockfile is not None
    )
    if not run_fw and not want_drift:
        return

    blueprint_dir = getattr(args, "blueprint", None)
    conflicts_dir = getattr(args, "conflicts_dir", None)
    force_ours = tuple(getattr(args, "force_ours", None) or ())
    firewall_profile = getattr(args, "firewall_profile", "strict") or "strict"

    try:
        if want_drift and live_dir is None:
            if not target_app_name or apps_client is None:
                raise BlueprintCompilerError(
                    "Pre-push drift check without --live-dir requires an "
                    "existing target app (--to or --app-name)."
                )
            with tempfile.TemporaryDirectory() as tmp_live_root:
                eff_live_dir = _export_live_app_to_dir(
                    apps_client, target_app_name, Path(tmp_live_root)
                )
                enforce_pre_push_gate(
                    app_dir,
                    live_dir=eff_live_dir,
                    lockfile_path=lockfile,
                    blueprint_dir=blueprint_dir,
                    conflicts_dir=conflicts_dir,
                    force_ours=force_ours,
                    run_static_firewall=run_fw,
                    firewall_profile=firewall_profile,
                )
        else:
            enforce_pre_push_gate(
                app_dir,
                live_dir=live_dir,
                lockfile_path=lockfile,
                blueprint_dir=blueprint_dir,
                conflicts_dir=conflicts_dir,
                force_ours=force_ours,
                run_static_firewall=run_fw,
                firewall_profile=firewall_profile,
            )
    except (BlueprintCompilerError, PythonBundlerError) as exc:
        print(f"Pre-push gate failed: {exc}", file=sys.stderr)
        sys.exit(1)


def app_push(args: argparse.Namespace) -> str | None:
    """Handles the 'push' command."""
    # We will reuse the deploy_agent logic from main.py, slightly adjusted.
    app_dir = args.app_dir if args.app_dir else "."
    print(f"Pushing app from {app_dir}...")

    target_app = getattr(args, "to", None)
    app_name_arg = getattr(args, "app_name", None)
    identifier = target_app or app_name_arg

    run_fw = bool(getattr(args, "firewall", False))
    live_dir_arg = getattr(args, "live_dir", None)
    lockfile_arg = getattr(args, "lockfile", None)
    want_drift = bool(
        getattr(args, "check_drift", False)
        or live_dir_arg is not None
        or lockfile_arg is not None
    )
    needs_remote_export = want_drift and live_dir_arg is None

    if needs_remote_export and not identifier:
        print(
            "Pre-push gate failed: Pre-push drift check without --live-dir "
            "requires an existing target app (--to or --app-name).",
            file=sys.stderr,
        )
        sys.exit(1)

    gate_ran_offline = False
    firewall_ran_offline = False
    if (run_fw or want_drift) and not needs_remote_export:
        _maybe_run_pre_push_gate(
            app_dir=app_dir,
            apps_client=None,
            target_app_name=None,
            args=args,
        )
        gate_ran_offline = True
    elif run_fw and needs_remote_export:
        firewall_profile = (
            getattr(args, "firewall_profile", "strict") or "strict"
        )
        try:
            enforce_pre_push_gate(
                app_dir,
                blueprint_dir=getattr(args, "blueprint", None),
                run_static_firewall=True,
                firewall_profile=firewall_profile,
            )
        except (BlueprintCompilerError, PythonBundlerError) as exc:
            print(f"Pre-push gate failed: {exc}", file=sys.stderr)
            sys.exit(1)
        firewall_ran_offline = True

    if identifier:
        apps_client, app_name, display_name = _resolve_app_args(
            identifier, args
        )
        print("Pushing to existing app... Overwriting if supported.")
    else:
        apps_client = Apps(
            project_id=getattr(args, "project_id", None),
            location=getattr(args, "location", None),
        )
        app_name = None
        print("No target specified, using existing name if needed.")
        display_name = getattr(args, "display_name", None) or "Pushed Agent"

    return _app_push(
        app_dir=app_dir,
        apps_client=apps_client,
        target_app_name=getattr(args, "app_name", None) or app_name,
        identifier=getattr(args, "to", None) or getattr(args, "app_name", None),
        display_name=getattr(args, "display_name", None) or display_name,
        env_file=getattr(args, "env_file", None),
        args=args,
        _skip_pre_push_gate=gate_ran_offline,
        _firewall_already_ran=firewall_ran_offline,
    )


def _bundle_declares_lock(app_dir: str) -> bool:
    """True when the bundle's app.json declares the governed lock (FW09)."""
    app_json = Path(app_dir) / "app.json"
    if not app_json.is_file():
        return False
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("locked") is True


def _stage_unlocked_import_payload(app_dir: str, staging_root: Path) -> Path:
    """Copies the bundle and sets app.json `locked` to false for import.

    CES rejects importing into an app that is (or would become) locked
    (FAILED_PRECONDITION "App ... is locked"), so the lock is applied with
    UpdateApp after import instead of travelling inside the payload.
    """
    staged = staging_root / Path(app_dir).resolve().name
    shutil.copytree(app_dir, staged)
    app_json = staged / "app.json"
    data = json.loads(app_json.read_text(encoding="utf-8"))
    data["locked"] = False
    app_json.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return staged


def _is_app_locked(apps_client: Apps, app_name: str) -> bool:
    try:
        return getattr(apps_client.get_app(app_name), "locked", False) is True
    except Exception as e:  # noqa: BLE001
        print(f"Warning: could not read lock state of {app_name}: {e}")
        return False


def _app_push(
    app_dir: str,
    apps_client: Apps = None,
    target_app_name: str | None = None,
    identifier: str | None = None,
    display_name: str | None = None,
    env_file: str | None = None,
    args: argparse.Namespace | None = None,
    *,
    _skip_pre_push_gate: bool = False,
    _firewall_already_ran: bool = False,
) -> str | None:
    """Helper to push an app to CXAS."""
    if not _skip_pre_push_gate:
        _maybe_run_pre_push_gate(
            app_dir=app_dir,
            apps_client=apps_client,
            target_app_name=target_app_name,
            args=args,
            firewall_already_ran=_firewall_already_ran,
        )

    from cxas_scrapi.core.packaging import package_app_archive

    temp_zip = tempfile.mktemp(suffix=".zip")
    native_python_env = (
        getattr(args, "native_python_env", False) if args else False
    )

    desired_lock = _bundle_declares_lock(app_dir)
    was_locked = bool(target_app_name) and _is_app_locked(
        apps_client, target_app_name
    )
    relock_target: str | None = None
    staging_dir: str | None = None
    payload_dir = app_dir

    try:
        if was_locked:
            print(
                f"Target app {target_app_name} is locked; unlocking it for "
                "import (it is re-locked afterwards)..."
            )
            apps_client.update_app(target_app_name, locked=False)
            relock_target = target_app_name
        if desired_lock:
            staging_dir = tempfile.mkdtemp(prefix="cxas_push_")
            payload_dir = str(
                _stage_unlocked_import_payload(app_dir, Path(staging_dir))
            )
            print(
                "Bundle declares locked=true (governed lock): importing with "
                "locked=false and applying the lock after import."
            )
        # Inject explicit env_file if provided
        env_path: Path | None = None
        if env_file:
            if os.path.exists(env_file):
                env_path = Path(env_file)
                print(
                    f"Included custom environment file "
                    f"from {env_file} as environment.json"
                )
            else:
                print(
                    f"Warning: Custom environment file "
                    f"'{env_file}' not found. Skipping."
                )
                if collect_env_var_declaration_names(app_dir):
                    raise FileNotFoundError(
                        f"Custom environment file '{env_file}' not found; "
                        "required to resolve '$env_var' variableDeclarations"
                    )

        resolved_vars: list[str] = []
        package_app_archive(
            Path(payload_dir),
            Path(temp_zip),
            env_path,
            native_python_env=native_python_env,
            wrapper_dir="agent",
            resolved_env_vars=resolved_vars,
        )
        if resolved_vars:
            print(
                f"Resolved {len(resolved_vars)} '$env_var' variable "
                f"default(s) from environment.json: {', '.join(resolved_vars)}"
            )

        with open(temp_zip, "rb") as f:
            app_content = f.read()

        # If no target is specified, the SDK creates a new app with the
        # provided display_name.
        print("Uploading to CES...")

        if target_app_name:
            conflict_strategy = (
                "OVERWRITE"
                if args and getattr(args, "overwrite", False)
                else None
            )
            result = apps_client.import_app(
                app_name=target_app_name,
                app_content=app_content,
                conflict_strategy=conflict_strategy,
            )
        else:
            result = apps_client.import_as_new_app(
                display_name=display_name, app_content=app_content
            )
        app_name = _handle_import_result(
            result, "pushed to" if identifier else "pushed"
        )

        if args and getattr(args, "create_version", False) and app_name:
            print(f"Creating version for {app_name}...")
            versions_client = Versions(
                app_name=app_name,
                creds=apps_client.creds,
            )
            version_display_name = (
                getattr(args, "version_name", None)
                or f"import-{time.strftime('%Y%m%d%H%M%S')}"
            )
            description = getattr(args, "version_description", None)
            version = versions_client.create_version(
                display_name=version_display_name,
                description=description,
            )
            version_name = version.name
            print(
                f"Created app version: {version_name} "
                f"with display name {version_display_name}"
            )
            args.created_version_name = version_name

        if (desired_lock or was_locked) and app_name:
            print(f"Locking app {app_name} (locked=true)...")
            apps_client.update_app(app_name, locked=True)
            relock_target = None
            print(f"App {app_name} is locked.")

        return app_name

    except Exception as e:
        print(f"Failed to push app: {e}")
        sys.exit(1)
    finally:
        if relock_target:
            print(
                f"Push did not complete; restoring lock on {relock_target}..."
            )
            try:
                apps_client.update_app(relock_target, locked=True)
            except Exception as relock_err:  # noqa: BLE001
                print(
                    f"ERROR: failed to restore lock on {relock_target}: "
                    f"{relock_err}",
                    file=sys.stderr,
                )
        if staging_dir and os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        if os.path.exists(temp_zip):
            os.remove(temp_zip)


def app_create(args: argparse.Namespace) -> None:
    """Handles the 'create' command."""
    print(f"Creating app: {args.name}")
    apps_client = Apps(project_id=args.project_id, location=args.location)
    try:
        app = apps_client.create_app(
            app_id=args.app_id,
            display_name=args.name,
            description=args.description,
        )
        print(f"App created successfully: {app.name}")
    except Exception as e:
        print(f"Failed to create app: {e}")
        sys.exit(1)


def app_delete(args: argparse.Namespace) -> None:
    """Handles the 'delete' command."""

    app_name_arg = getattr(args, "app_name", None)

    if app_name_arg:
        print(f"Deleting App: {app_name_arg}")
        project_id = Common._get_project_id(app_name_arg)
        location = Common._get_location(app_name_arg)
        app_name = app_name_arg
    elif args.display_name and args.project_id and args.location:
        print(f"Deleting App by Display Name: {args.display_name}")
        project_id = args.project_id
        location = args.location
        app_name = None
    else:
        print(
            "Error: Must provide either --app_name OR "
            "(--display_name, --project_id, --location)"
        )
        sys.exit(1)

    if not project_id or not location:
        print("Error: Could not determine project_id or location.")
        sys.exit(1)

    apps_client = Apps(project_id=project_id, location=location)

    try:
        if not app_name:
            # Lookup by display name
            app = apps_client.get_app_by_display_name(args.display_name)
            if app:
                app_name = app.name
                print(f"Found app ID: {app_name}")
            else:
                print(
                    f"App with display name '{args.display_name}' "
                    "not found. Nothing to delete."
                )
                return

        apps_client.delete_app(app_name=app_name, force=args.force)
        print(f"Successfully deleted {app_name}")
    except Exception as e:
        print(f"Failed to delete app: {e}")
        sys.exit(1)


def app_branch(args: argparse.Namespace) -> None:
    """Handles the 'branch' command."""
    print(f"Branching from {args.source} to {args.new_name}")
    # Composite operation: pull existing, create new, push content.

    apps_client, app_name, _ = _resolve_app_args(args.source, args)
    env_file = getattr(args, "env_file", None)

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            print("Pulling source app...")
            _app_pull(apps_client, app_name, temp_dir)

            extracted_dirs = [
                d
                for d in os.listdir(temp_dir)
                if os.path.isdir(os.path.join(temp_dir, d))
            ]
            app_dir = (
                os.path.join(temp_dir, extracted_dirs[0])
                if extracted_dirs
                else temp_dir
            )

            print("Pushing branched app...")
            _app_push(
                app_dir=app_dir,
                apps_client=apps_client,
                display_name=args.new_name,
                env_file=env_file,
            )
        except Exception as e:
            print(f"Failed to branch app: {e}")
            sys.exit(1)


def apps_list(args: argparse.Namespace) -> None:
    """Handles the 'apps list' command."""
    print(f"Listing apps for project {args.project_id} in {args.location}...")
    apps_client = Apps(project_id=args.project_id, location=args.location)
    try:
        apps = apps_client.list_apps()
        if not apps:
            print("No apps found.")
            return

        # Attempt to format output using pandas if available.
        try:
            import pandas as pd  # noqa: PLC0415

            data = [
                {"Display Name": app.display_name, "Name": app.name}
                for app in apps
            ]
            df = pd.DataFrame(data)
            print("\nApps:")
            print(df.to_string(index=False))
        except ImportError:
            for app in apps:
                print(f"- {app.display_name} ({app.name})")

    except Exception as e:
        print(f"Failed to list apps: {e}")
        sys.exit(1)


def apps_get(args: argparse.Namespace) -> None:
    """Handles the 'apps get' command."""
    print(f"Getting app: {args.app}")

    apps_client, app_name, _ = _resolve_app_args(args.app, args)

    try:
        app = apps_client.get_app(app_name=app_name)
        print("\nApp Details:")
        print(f"Name: {app.name}")
        print(f"Display Name: {app.display_name}")
        print(f"Description: {getattr(app, 'description', '')}")
        print(f"Create Time: {getattr(app, 'create_time', '')}")
        print(f"Update Time: {getattr(app, 'update_time', '')}")
        # Note: could dump full JSON if needed.
    except Exception as e:
        print(f"Failed to get app details: {e}")
        sys.exit(1)


def app_lint(args: argparse.Namespace) -> None:
    """Handles the 'lint' command."""
    from cxas_scrapi.utils.linter import (  # noqa: PLC0415
        SINGLE_RESOURCE_RULES,
        Discovery,
        LintConfig,
        LintContext,
        LintReport,
        build_context,
        build_registry,
        run_rules,
    )

    registry = build_registry()

    model_override = getattr(args, "model", None)
    model_only = getattr(args, "model_only", False)

    if getattr(args, "list_rules", False):
        print("CXAS Agent Linter — Available Rules")
        print("=" * 60)
        registry.list_rules(model=model_override, model_only=model_only)
        sys.exit(0)

    json_output = getattr(args, "json_output", False)
    show_fixes = getattr(args, "fix", False)

    # Per-resource validation (--agent, --tool, etc.)
    for flag, rule_id in SINGLE_RESOURCE_RULES.items():
        resource_dir = getattr(args, flag, None)
        if not resource_dir:
            continue
        resource_path = Path(resource_dir).resolve()
        rule_obj = registry.get(rule_id)
        report = LintReport()
        context = LintContext(
            project_root=resource_path.parent,
            app_dir=resource_path.parent,
            evals_dir=resource_path.parent,
        )
        if not json_output:
            print(f"Validating {flag}: {resource_path.name}")
            print("=" * 60)
        if rule_obj is not None:
            for result in rule_obj.check(resource_path, "", context):
                report.add(result)
        report.print_and_exit(json_output, show_fixes)
        return

    # Full app lint
    project_root = Path(getattr(args, "app_dir", ".")).resolve()
    config = LintConfig.load(project_root)

    app_dir = project_root / config.app_dir
    evals_dir = project_root / config.evals_dir

    limit_agents = None
    agents_arg = getattr(args, "agents", None)
    if agents_arg:
        limit_agents = set(a.strip() for a in agents_arg.split(","))

    limit_tools = None
    tools_arg = getattr(args, "tools", None)
    if tools_arg:
        limit_tools = set(t.strip() for t in tools_arg.split(","))

    discovery = Discovery(
        app_dir,
        evals_dir,
        limit_agents=limit_agents,
        limit_tools=limit_tools,
    )

    if not discovery.app_root:
        if not json_output:
            print(f"ERROR: No app directory found under {app_dir}")
            print(
                "Ensure the directory contains app.json/app.yaml and agents/. "
                "Use --app-dir to specify the app location."
            )
        else:
            import json  # noqa: PLC0415

            print(
                json.dumps(
                    [
                        {
                            "file": str(app_dir),
                            "severity": "error",
                            "rule_id": "SETUP",
                            "message": (
                                f"No app directory found under {app_dir}"
                            ),
                        }
                    ]
                )
            )
        sys.exit(1)

    env_file_arg = getattr(args, "env_file", None)
    resolved_env_file = Path(env_file_arg).resolve() if env_file_arg else None

    context = build_context(
        project_root,
        config,
        discovery,
        model_override=model_override,
        env_file=resolved_env_file,
    )

    if not json_output:
        print(f"Linting app: {discovery.app_root.name}")
        print("=" * 60)
        if context.model:
            print(f"  Model:     {context.model}")
        agents = discovery.discover_agents()
        tools = discovery.discover_tools()
        callbacks = discovery.discover_callbacks()
        evals = discovery.discover_evals()
        print(f"  Agents:    {len(agents)}")
        print(f"  Tools:     {len(tools)}")
        print(f"  Callbacks: {len(callbacks)}")
        print(f"  Evals:     {len(evals)}")

    categories = None
    if getattr(args, "validate_only", False):
        categories = ["structure", "config", "schema"]
    elif getattr(args, "only", None):
        categories = [args.only]

    specific_rules = None
    rule_arg = getattr(args, "rule", None)
    if rule_arg:
        specific_rules = set(r.strip() for r in rule_arg.split(","))

    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        categories=categories,
        specific_rules=specific_rules,
        model_only=model_only,
    )

    report.print_and_exit(json_output, show_fixes)


def app_init(args: argparse.Namespace) -> None:
    """Handles the 'init' command -- copies skill files."""

    target_dir = Path(getattr(args, "target_dir", ".")).resolve()
    force = getattr(args, "force", False)
    skills_root = Path(sys.prefix) / "share" / "cxas-scrapi" / "skills"

    if not skills_root.exists():
        print(f"ERROR: Bundled skills not found at {skills_root}")
        print(
            "This may happen if cxas-scrapi was installed without skill data."
        )
        sys.exit(1)

    overwrite_all = force
    copied, skipped = 0, 0

    for item in sorted(skills_root.iterdir()):
        dest = target_dir / item.name
        if dest.exists() and not overwrite_all:
            choice = _prompt_overwrite(
                item.name, getattr(args, "no_input", False)
            )
            if choice == "abort":
                print("Aborted.")
                sys.exit(0)
            elif choice == "all":
                overwrite_all = True
            elif choice == "skip":
                skipped += 1
                print(f"  Skipped: {item.name}")
                continue

        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

        copied += 1
        print(f"  Installed: {item.name}")

    print(f"\nDone. {copied} installed, {skipped} skipped.")


def _prompt_overwrite(name: str, no_input: bool = False) -> str:
    """Prompt user for overwrite decision."""
    if not sys.stdin.isatty() or no_input:
        msg = (
            f"ERROR: '{name}' already exists. "
            "Non-interactive environment detected or --no-input specified."
        )
        print(msg, file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    while True:
        choice = (
            input(
                f"  '{name}' already exists. "
                "[o]verwrite / [a]ll / [s]kip / [q]uit? "
            )
            .strip()
            .lower()
        )
        if choice in ("o", "overwrite"):
            return "yes"
        if choice in ("a", "all"):
            return "all"
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("q", "quit"):
            return "abort"
