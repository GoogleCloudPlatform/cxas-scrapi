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

from cxas_scrapi.build.bundle_io import BuildGateError
from cxas_scrapi.build.drift import enforce_pre_push_gate
from cxas_scrapi.core.apps import Apps, validate_zip_entry_name
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    strip_shared_bundles,
)
from cxas_scrapi.core.versions import Versions
from cxas_scrapi.utils.env_vars import (
    collect_env_var_declaration_names,
    collect_tool_and_toolset_env_var_placeholders,
    extract_env_variable_defaults,
    preserve_env_var_placeholders_on_pull,
    preserve_tool_and_toolset_env_vars_on_pull,
)

logger = logging.getLogger(__name__)

# Top-level folders and files that CES ExportApp omits entirely once their last
# resource is deleted. Folders present in the new export are pruned
# automatically, so only these need to be listed explicitly.
_REMOVABLE_APP_ROOT_DIRS = frozenset(
    {
        "tools",
        "toolsets",
        "guardrails",
        "examples",
        "evaluations",
        "evaluationDatasets",
        "evaluationExpectations",
    }
)

_REMOVABLE_APP_ROOT_FILES = frozenset(
    {
        "global_instruction.txt",
        "environment.json",
    }
)


def _to_posix_relpath(path: str, start: str) -> str:
    """Return a forward-slash relative path from ``start`` (empty for self)."""
    rel = os.path.relpath(path, start).replace("\\", "/")
    return "" if rel == "." else rel.strip("/")


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

        # Validate archive entries and extract into a staging directory first
        # so Zip-Slip or bundle-strip failures never overwrite local files.
        top_dir = ""
        with zipfile.ZipFile(io.BytesIO(response.app_content)) as z:
            export_members = list(z.namelist())
            for info in z.infolist():
                validate_zip_entry_name(info.filename)

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
            prev_resource_placeholders = (
                collect_tool_and_toolset_env_var_placeholders(pulled_app_root)
            )
            prev_env_defaults: dict[str, Any] = {}
            prev_env_data: dict[str, Any] | None = None
            prev_env_path = os.path.join(pulled_app_root, "environment.json")
            if (prev_env_vars or prev_resource_placeholders) and os.path.isfile(
                prev_env_path
            ):
                try:
                    with open(prev_env_path, encoding="utf-8") as ef:
                        loaded_prev_env = json.load(ef)
                    if isinstance(loaded_prev_env, dict):
                        prev_env_data = loaded_prev_env
                    if prev_env_vars:
                        prev_env_defaults, _ = extract_env_variable_defaults(
                            loaded_prev_env
                        )
                except (OSError, ValueError):
                    prev_env_defaults = {}
                    prev_env_data = None

            with tempfile.TemporaryDirectory() as stage_dir:
                if dest_app_root is not None:
                    staged_app_root = os.path.join(stage_dir, "app")
                    os.makedirs(staged_app_root, exist_ok=True)
                    abs_stage_root = os.path.abspath(staged_app_root)
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
                            os.path.join(staged_app_root, rel_name)
                        )
                        if (
                            os.path.commonpath(
                                [abs_stage_root, target_member_path]
                            )
                            != abs_stage_root
                        ):
                            raise ValueError(
                                "Path traversal detected in archive entry: "
                                f"{info.filename}"
                            )
                        info.filename = rel_name
                        z.extract(info, path=staged_app_root)
                else:
                    abs_stage_dir = os.path.abspath(stage_dir)
                    for info in z.infolist():
                        target_member_path = os.path.abspath(
                            os.path.join(stage_dir, info.filename)
                        )
                        if (
                            os.path.commonpath(
                                [abs_stage_dir, target_member_path]
                            )
                            != abs_stage_dir
                        ):
                            raise ValueError(
                                "Path traversal detected in archive entry: "
                                f"{info.filename}"
                            )
                    z.extractall(stage_dir)
                    staged_app_root = (
                        os.path.join(stage_dir, top_dir)
                        if top_dir
                        else stage_dir
                    )

                force_strip = getattr(args, "force", False) if args else False
                has_local_lib = os.path.isdir(
                    os.path.join(pulled_app_root, "python_lib")
                ) or os.path.isdir(
                    os.path.join(
                        pulled_app_root,
                        "pythonEnvFiles",
                        "content",
                        "python_lib",
                    )
                )
                strip_shared_bundles(
                    Path(staged_app_root),
                    local_source_dir=(
                        Path(pulled_app_root)
                        if has_local_lib
                        else Path(staged_app_root)
                    ),
                    force=force_strip,
                )

                os.makedirs(target_dir, exist_ok=True)
                os.makedirs(pulled_app_root, exist_ok=True)
                shutil.copytree(
                    staged_app_root, pulled_app_root, dirs_exist_ok=True
                )

        # Handle overwrite logic if requested. Pruning is scoped to a dedicated
        # app folder (the archive's top-level folder or --app-dir-name) so a
        # flat archive never prunes an arbitrary --target-dir such as ".".
        if overwrite and export_members and (top_dir or dest_app_root):
            app_root = pulled_app_root

            if os.path.isdir(app_root):
                # Build set of exported POSIX paths relative to app_root
                export_set: set[str] = set()
                export_root_dirs: set[str] = set()
                prefix = f"{top_dir}/" if top_dir else ""
                for member in export_members:
                    norm_member = member.replace("\\", "/").strip("/")
                    if not norm_member:
                        continue
                    if top_dir:
                        if norm_member == top_dir:
                            continue
                        if not norm_member.startswith(prefix):
                            continue
                        rel_path = norm_member[len(prefix) :]
                    else:
                        rel_path = norm_member
                    if not rel_path or rel_path == ".":
                        continue
                    export_set.add(rel_path)
                    parts = rel_path.split("/")
                    if len(parts) > 1 or member.endswith("/"):
                        export_root_dirs.add(parts[0])

                managed_root_dirs = _REMOVABLE_APP_ROOT_DIRS | export_root_dirs

                # Walk the local app root bottom-up to remove files then dirs
                for root, dirs, files in os.walk(app_root, topdown=False):
                    rel_root = _to_posix_relpath(root, app_root)

                    # Check files
                    for name in files:
                        local_rel_path = (
                            f"{rel_root}/{name}" if rel_root else name
                        )
                        parts = local_rel_path.split("/")
                        is_managed = (
                            parts[0] in managed_root_dirs
                            if len(parts) > 1
                            else name in _REMOVABLE_APP_ROOT_FILES
                        )
                        if is_managed and local_rel_path not in export_set:
                            file_path = os.path.join(root, name)
                            print(f"Removing non-exported file: {file_path}")
                            os.remove(file_path)

                    # Check directories (remove if empty and not in export)
                    for name in dirs:
                        local_rel_path = (
                            f"{rel_root}/{name}" if rel_root else name
                        )
                        parts = local_rel_path.split("/")
                        dir_prefix = f"{local_rel_path}/"
                        if (
                            parts[0] in managed_root_dirs
                            and local_rel_path not in export_set
                            and not any(
                                m.startswith(dir_prefix) for m in export_set
                            )
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

        if prev_resource_placeholders:
            preserved_resources = preserve_tool_and_toolset_env_vars_on_pull(
                pulled_app_root,
                prev_resource_placeholders,
                previous_env_data=prev_env_data,
            )
            if preserved_resources:
                print(
                    "Preserved $env_var placeholders for "
                    f"{len(preserved_resources)} tool/toolset field(s) and "
                    "updated environment.json."
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
                raise BuildGateError(
                    f"Failed to export live app {target_app_name!r} for drift "
                    "gate."
                )
    except BuildGateError:
        raise
    except Exception as exc:
        raise BuildGateError(
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
                try:
                    validate_zip_entry_name(info.filename)
                except ValueError as exc:
                    raise BuildGateError(
                        f"Unsafe archive path in live export: {info.filename}"
                    ) from exc
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise BuildGateError(
                        f"Symlink entry not permitted in live export: "
                        f"{info.filename}"
                    )
                dest_member = (abs_extract / info.filename).resolve()
                if os.path.commonpath(
                    [str(abs_extract), str(dest_member)]
                ) != str(abs_extract):
                    raise BuildGateError(
                        f"Unsafe archive path in live export: {info.filename}"
                    )
            zf.extractall(abs_extract)
    except BuildGateError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildGateError(
            f"Invalid ZIP archive exported from live app "
            f"{target_app_name!r}: {exc}"
        ) from exc

    if (
        (abs_extract / "app.json").is_file()
        or (abs_extract / "app.yaml").is_file()
        or (abs_extract / "app.yml").is_file()
    ):
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
) -> None:
    """Runs the 3-way drift gate prior to importing (when requested)."""
    if args is None:
        return

    live_dir = getattr(args, "live_dir", None)
    lockfile = getattr(args, "lockfile", None)
    want_drift = bool(
        getattr(args, "check_drift", False)
        or live_dir is not None
        or lockfile is not None
    )
    if not want_drift:
        return

    conflicts_dir = getattr(args, "conflicts_dir", None)
    force_ours = tuple(getattr(args, "force_ours", None) or ())

    try:
        if live_dir is None:
            if not target_app_name or apps_client is None:
                raise BuildGateError(
                    "Pre-push drift check without --live-dir requires an "
                    "existing target app (--to or --app-name)."
                )
            with tempfile.TemporaryDirectory() as tmp_live_root:
                eff_live_dir = _export_live_app_to_dir(
                    apps_client, target_app_name, Path(tmp_live_root)
                )
                report = enforce_pre_push_gate(
                    app_dir,
                    live_dir=eff_live_dir,
                    lockfile_path=lockfile,
                    conflicts_dir=conflicts_dir,
                    force_ours=force_ours,
                )
        else:
            report = enforce_pre_push_gate(
                app_dir,
                live_dir=live_dir,
                lockfile_path=lockfile,
                conflicts_dir=conflicts_dir,
                force_ours=force_ours,
            )
    except (BuildGateError, PythonBundlerError) as exc:
        print(f"Pre-push gate failed: {exc}", file=sys.stderr)
        sys.exit(1)
    if report is not None:
        n_clean = sum(1 for r in report.resources if r.status == "CLEAN")
        print(
            f"Drift gate passed: {n_clean}/{len(report.resources)} "
            "resource(s) CLEAN, 0 unresolved."
        )


def app_push(args: argparse.Namespace) -> str | None:
    """Handles the 'push' command."""
    if getattr(args, "patch", False):
        from cxas_scrapi.cli.hot_patch_cli import run_hot_patch_cli

        return run_hot_patch_cli(args)

    # We will reuse the deploy_agent logic from main.py, slightly adjusted.
    app_dir = args.app_dir if args.app_dir else "."
    print(f"Pushing app from {app_dir}...")

    target_app = getattr(args, "to", None)
    app_name_arg = getattr(args, "app_name", None)
    identifier = target_app or app_name_arg

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
    if want_drift and not needs_remote_export:
        _maybe_run_pre_push_gate(
            app_dir=app_dir,
            apps_client=None,
            target_app_name=None,
            args=args,
        )
        gate_ran_offline = True

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
    )


def _find_app_config_file(app_dir: Path) -> Path | None:
    """Return the app's root config file (app.json, app.yaml, or app.yml)."""
    for name in ("app.json", "app.yaml", "app.yml"):
        candidate = app_dir / name
        if candidate.is_file():
            return candidate
    return None


def _bundle_lock_policy(app_dir: str) -> bool | None:
    """Return explicit governed lock policy (`True`/`False`) or `None`."""
    cfg = _find_app_config_file(Path(app_dir))
    if cfg is None:
        return None
    try:
        text = cfg.read_text(encoding="utf-8")
        if cfg.suffix.lower() in (".yaml", ".yml"):
            import yaml  # noqa: PLC0415

            from cxas_scrapi.utils.yaml_utils import (  # noqa: PLC0415
                Yaml12SafeLoader,
            )

            data = yaml.load(text, Loader=Yaml12SafeLoader)
        else:
            data = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    raw_locked = data.get("locked")
    return raw_locked if isinstance(raw_locked, bool) else None


def _bundle_declares_lock(app_dir: str) -> bool:
    """True when the local app config declares ``locked: true``."""
    return _bundle_lock_policy(app_dir) is True


def _clear_staged_app_lock(staged_dir: Path) -> None:
    """Set ``locked: false`` in staged ``app.json`` / ``app.yaml`` for import."""
    cfg = _find_app_config_file(staged_dir)
    if cfg is None:
        return
    text = cfg.read_text(encoding="utf-8")
    if cfg.suffix.lower() in (".yaml", ".yml"):
        import yaml  # noqa: PLC0415

        from cxas_scrapi.utils.yaml_utils import (  # noqa: PLC0415
            Yaml12SafeLoader,
        )

        staged_data = yaml.load(text, Loader=Yaml12SafeLoader)
        if isinstance(staged_data, dict):
            staged_data["locked"] = False
            cfg.write_text(
                yaml.safe_dump(
                    staged_data, sort_keys=False, allow_unicode=True
                ),
                encoding="utf-8",
            )
    else:
        staged_data = json.loads(text)
        if isinstance(staged_data, dict):
            staged_data["locked"] = False
            cfg.write_text(
                json.dumps(staged_data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )


def _is_app_locked(apps_client: Apps | None, app_name: str) -> bool:
    """True when the remote CES app currently has locked=True."""
    if apps_client is None:
        return False
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
) -> str | None:
    """Helper to push an app to CXAS."""
    if not _skip_pre_push_gate:
        _maybe_run_pre_push_gate(
            app_dir=app_dir,
            apps_client=apps_client,
            target_app_name=target_app_name,
            args=args,
        )

    from cxas_scrapi.core.packaging import package_app_archive  # noqa: PLC0415

    native_python_env = (
        getattr(args, "native_python_env", False) if args else False
    )

    desired_lock = _bundle_lock_policy(app_dir)
    was_locked = bool(target_app_name) and _is_app_locked(
        apps_client, target_app_name
    )
    relock_target: str | None = None

    try:
        if was_locked:
            if desired_lock is False:
                print(
                    f"Target app {target_app_name} is locked; unlocking it "
                    "(bundle declares locked=false)..."
                )
            else:
                print(
                    f"Target app {target_app_name} is locked; unlocking it for "
                    "import (it will be re-locked afterwards)..."
                )
            apps_client.update_app(target_app_name, locked=False)
            relock_target = target_app_name

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
                from cxas_scrapi.utils.tracing.app_config import (  # noqa: PLC0415
                    has_bool_env_var_placeholders,
                )

                if (
                    collect_env_var_declaration_names(app_dir)
                    or collect_tool_and_toolset_env_var_placeholders(app_dir)
                    or has_bool_env_var_placeholders(app_dir)
                ):
                    raise FileNotFoundError(
                        f"Custom environment file '{env_file}' not found; "
                        "required to resolve '$env_var' placeholders"
                    )

        post_stage_hook = None
        if desired_lock is True:
            post_stage_hook = _clear_staged_app_lock
            print(
                "Bundle declares locked=true (governed lock): importing with "
                "locked=false and applying the lock after import."
            )

        resolved_vars: list[str] = []
        resolved_resources: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_zip = Path(temp_dir) / "app.zip"
            package_app_archive(
                Path(app_dir),
                temp_zip,
                env_path,
                native_python_env=native_python_env,
                wrapper_dir="agent",
                resolved_env_vars=resolved_vars,
                resolved_tool_env_vars=resolved_resources,
                post_stage_hook=post_stage_hook,
            )
            if resolved_vars:
                print(
                    f"Resolved {len(resolved_vars)} '$env_var' variable "
                    f"default(s) from environment.json: "
                    f"{', '.join(resolved_vars)}"
                )
            if resolved_resources:
                print(
                    f"Resolved {len(resolved_resources)} '$env_var' "
                    "tool/toolset field(s) from environment.json: "
                    f"{', '.join(resolved_resources)}"
                )
            app_content = temp_zip.read_bytes()

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
        should_lock_after = desired_lock is True or (
            desired_lock is None and was_locked
        )
        if should_lock_after and app_name:
            relock_target = app_name

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

        if should_lock_after and app_name:
            print(f"Locking app {app_name} (locked=true)...")
            relock_target = None
            apps_client.update_app(app_name, locked=True)
            print(f"App {app_name} is locked.")
        else:
            relock_target = None

        return app_name

    except Exception as e:
        print(f"Failed to push app: {e}")
        sys.exit(1)
    finally:
        if relock_target and apps_client is not None:
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
    manifests_arg = getattr(args, "manifests_dir", None)
    context.manifests_dir = (
        Path(manifests_arg).resolve() if manifests_arg else None
    )
    context.profile_id = getattr(args, "profile", None)
    context.channel = getattr(args, "channel", None)

    if manifests_arg is not None:
        from cxas_scrapi.build.manifests import (  # noqa: PLC0415
            ManifestError,
            load_manifests,
        )

        manifests_err: str | None = None
        if not context.manifests_dir.is_dir():
            manifests_err = (
                f"Manifests directory not found: {context.manifests_dir}"
            )
        else:
            try:
                loaded_manifests = load_manifests(context.manifests_dir)
                if (
                    context.profile_id
                    and context.profile_id not in loaded_manifests.profiles
                ):
                    manifests_err = (
                        f"Unknown profile '{context.profile_id}'. Known "
                        f"profiles: {sorted(loaded_manifests.profiles)}"
                    )
                elif context.channel:
                    eff_profile = context.profile_id
                    if not eff_profile:
                        from cxas_scrapi.utils.lint_rules.polymorphic import (  # noqa: PLC0415
                            _deployment_metadata,
                        )

                        eff_profile, _ = _deployment_metadata(
                            discovery.app_root
                        )
                    if eff_profile and eff_profile in loaded_manifests.policies:
                        known_ch = loaded_manifests.policies[
                            eff_profile
                        ].channels
                        if context.channel not in known_ch:
                            manifests_err = (
                                f"Unknown channel '{context.channel}' for "
                                f"profile '{eff_profile}'. Known channels: "
                                f"{sorted(known_ch)}"
                            )
            except ManifestError as exc:
                manifests_err = (
                    f"Failed to load manifests from "
                    f"'{context.manifests_dir}': {exc}"
                )

        if manifests_err:
            if json_output:
                print(
                    json.dumps(
                        [
                            {
                                "file": str(context.manifests_dir),
                                "severity": "error",
                                "rule_id": "SETUP",
                                "message": manifests_err,
                            }
                        ]
                    )
                )
            else:
                print(f"ERROR: {manifests_err}", file=sys.stderr)
            sys.exit(1)

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
