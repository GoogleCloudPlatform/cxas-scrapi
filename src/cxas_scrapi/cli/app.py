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
import contextlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from cxas_scrapi.collab.drift import (
    DriftDetectedError,
    check_drift,
    record_last_push,
)
from cxas_scrapi.collab.metadata import (
    LOCK_KEY,
    METADATA_PREFIX,
    AppMetadataStore,
    LockHeldError,
    _decode_cxas,
    current_user,
    preserve_cxas_keys,
)
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.versions import Versions
from cxas_scrapi.utils.env_vars import (
    collect_env_var_declaration_names,
    extract_env_variable_defaults,
    preserve_env_var_placeholders_on_pull,
    resolve_app_env_variables_in_dir,
)
from cxas_scrapi.utils.profiles import (
    ProfileError,
    ResolvedProfile,
    apply_app_overrides_in_dir,
    guard_env_profile_target_conflict,
    guard_protected_mutation,
    load_profile,
    print_profile_warnings,
    render_app_config_diff,
    render_gecx_config_diff,
    restore_overridden_app_values,
    snapshot_overridden_app_values,
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


def _prepare_pull_branch(branch_name: str, cwd: str | None = None) -> None:
    """Create a clean Git branch before pulling Console edits."""
    work_dir = cwd if (cwd and os.path.isdir(cwd)) else os.getcwd()
    check_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if check_git.returncode != 0:
        print(
            "Error: --branch requires running inside a Git repository.",
            file=sys.stderr,
        )
        sys.exit(1)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.stdout.strip():
        print(
            "Error: Working tree has uncommitted changes. Commit or stash "
            "them before running `cxas pull --branch`.",
            file=sys.stderr,
        )
        sys.exit(1)

    checkout = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if checkout.returncode != 0:
        err = checkout.stderr.strip() or checkout.stdout.strip()
        print(
            f"Error: Failed to create git branch '{branch_name}': {err}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Switched to new git branch '{branch_name}'.")


def app_pull(args: argparse.Namespace) -> None:
    """Handles the 'pull' command."""
    profile: ResolvedProfile | None = None
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

    app_identifier = getattr(args, "app", None)
    if not app_identifier and profile is not None:
        app_identifier = (
            profile.target.app_resource_name or profile.target.display_name
        )

    if not app_identifier:
        print(
            "Error: No app specified. Provide an app argument or configure "
            "'target' in a --profile / CXAS_PROFILE.",
            file=sys.stderr,
        )
        sys.exit(1)

    branch_name = getattr(args, "branch", None)
    if branch_name:
        _prepare_pull_branch(branch_name, cwd=args.target_dir)

    print(f"Pulling app: {app_identifier}")

    apps_client, app_name, _ = _resolve_app_args(app_identifier, args)

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
        profile=profile,
        accept_profile_overrides=getattr(
            args, "accept_profile_overrides", False
        ),
    )


def _strip_cxas_metadata_on_pull(pulled_app_root: str | Path) -> list[str]:
    """Strip server-side ``cxas.*`` metadata keys from pulled app config.

    Preserves any non-``cxas.`` user metadata keys and removes the ``metadata``
    dict entirely if it becomes empty.
    """
    root = Path(pulled_app_root)
    app_cfg = next(
        (
            p
            for name in ("app.json", "app.yaml", "app.yml")
            if (p := root / name).is_file()
        ),
        None,
    )
    if app_cfg is None:
        return []

    try:
        raw_text = app_cfg.read_text(encoding="utf-8")
        data = (
            yaml.safe_load(raw_text)
            if app_cfg.suffix in (".yaml", ".yml")
            else json.loads(raw_text)
        )
    except (OSError, ValueError, yaml.YAMLError):
        return []

    if not isinstance(data, dict):
        return []

    target_dict = data["app"] if isinstance(data.get("app"), dict) else data
    meta = target_dict.get("metadata")
    if not isinstance(meta, dict):
        return []

    cxas_keys = [
        k
        for k in list(meta)
        if isinstance(k, str) and k.startswith(METADATA_PREFIX)
    ]
    if not cxas_keys:
        return []

    for key in cxas_keys:
        meta.pop(key, None)
    if not meta:
        target_dict.pop("metadata", None)

    if app_cfg.suffix in (".yaml", ".yml"):
        app_cfg.write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
    else:
        app_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return cxas_keys


def _app_pull(
    apps_client: Apps,
    app_name: str,
    target_dir: str,
    overwrite: bool = False,
    app_version: str | None = None,
    app_dir_name: str | None = None,
    profile: ResolvedProfile | None = None,
    accept_profile_overrides: bool = False,
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
            prev_overridden_snapshot: dict[str, Any] = {}
            if (
                profile is not None
                and profile.app_overrides
                and not accept_profile_overrides
            ):
                prev_overridden_snapshot = snapshot_overridden_app_values(
                    pulled_app_root, profile
                )
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

        _strip_cxas_metadata_on_pull(pulled_app_root)

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

        if prev_overridden_snapshot and profile is not None:
            restored_keys = restore_overridden_app_values(
                pulled_app_root, prev_overridden_snapshot, profile
            )
            if restored_keys:
                print(
                    "Warning: Preserved tracked base value(s) for "
                    f"profile-overridden key(s): {', '.join(restored_keys)}. "
                    "Pass --accept-profile-overrides to overwrite them with "
                    "remote values.",
                    file=sys.stderr,
                )

        print("Successfully pulled app.")

    except Exception as e:
        print(f"Failed to pull app: {e}")
        sys.exit(1)


def app_push(args: argparse.Namespace) -> str | None:
    """Handles the 'push' command."""
    # Resolve --profile / CXAS_PROFILE at the top (ahead of staging/lint/patch).
    profile: ResolvedProfile | None = None
    try:
        profile = load_profile(getattr(args, "profile", None))
        guard_env_profile_target_conflict(
            profile, explicit_to=getattr(args, "to", None)
        )
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if profile is not None:
        print_profile_warnings(profile.warnings)

    app_dir = (
        getattr(args, "app_dir", None)
        or (str(profile.app_dir) if profile and profile.app_dir else None)
        or "."
    )
    env_file = getattr(args, "env_file", None) or (
        str(profile.env_file) if profile and profile.env_file else None
    )
    if profile is not None:
        if not getattr(args, "project_id", None) and profile.target.project_id:
            args.project_id = profile.target.project_id
        if not getattr(args, "location", None) and profile.target.location:
            args.location = profile.target.location

    target_app = getattr(args, "to", None)
    app_name_arg = getattr(args, "app_name", None)
    display_name_arg = getattr(args, "display_name", None)

    is_ephemeral = bool(getattr(args, "_ci_test_ephemeral", False))
    if not is_ephemeral and profile is not None:
        if not target_app and not app_name_arg:
            if profile.target.app_resource_name:
                app_name_arg = profile.target.app_resource_name
            elif profile.target.display_name and not display_name_arg:
                target_app = profile.target.display_name
        if not display_name_arg and profile.target.display_name:
            display_name_arg = profile.target.display_name

    identifier = target_app or app_name_arg

    if getattr(args, "dry_run", False):
        print("[DRY RUN] Resolved push configuration (no CES API calls):")
        if profile is not None:
            print(f"  {profile.format_banner()}")
            print(f"  Protected : {'true' if profile.protected else 'false'}")
        print(f"  App Dir   : {app_dir}")
        print(f"  Project   : {getattr(args, 'project_id', None) or '(unset)'}")
        print(f"  Location  : {getattr(args, 'location', None) or '(unset)'}")
        print(
            f"  Target    : {identifier or display_name_arg or 'Pushed Agent'}"
        )
        print(f"  Env File  : {env_file or '(none)'}")
        if profile is not None:
            try:
                app_diff = render_app_config_diff(app_dir, profile)
                gecx_path = profile.project_root / "gecx-config.json"
                gecx_diff = render_gecx_config_diff(
                    gecx_path if gecx_path.is_file() else None, profile
                )
            except ProfileError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            if app_diff:
                print("\n--- App Config Diff ---")
                print(app_diff, end="")
            if gecx_diff:
                print("\n--- gecx-config.json Diff ---")
                print(gecx_diff, end="")
        return None

    try:
        guard_protected_mutation(
            profile,
            command="push",
            allow_protected=getattr(args, "allow_protected", False),
        )
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Pushing app from {app_dir}...")

    if identifier:
        apps_client, app_name, display_name = _resolve_app_args(
            identifier, args
        )
        print("Pushing to existing app... Overwriting if supported.")
    else:
        apps_client = Apps(project_id=args.project_id, location=args.location)
        app_name = None
        print("No target specified, using existing name if needed.")
        display_name = display_name_arg or "Pushed Agent"

    return _app_push(
        app_dir=app_dir,
        apps_client=apps_client,
        target_app_name=app_name_arg or app_name,
        identifier=identifier,
        display_name=display_name_arg or display_name,
        env_file=env_file,
        args=args,
        profile=profile,
    )


def _safe_existing_app_state(
    apps_client: Apps | None,
    target_app_name: str | None,
) -> tuple[dict[str, str], bool, dict[str, Any] | None]:
    """Best-effort fetch of ``(metadata, native_locked, active_cxas_lock)``."""
    if not apps_client or not target_app_name:
        return {}, False, None
    try:
        existing = apps_client.get_app(target_app_name)
    except Exception:  # noqa: BLE001
        return {}, False, None

    raw_meta = getattr(existing, "metadata", None)
    meta = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}
    # Ensure keys/values are plain strings (guards against unconfigured mocks).
    clean_meta = {
        str(k): str(v)
        for k, v in meta.items()
        if isinstance(k, str) and isinstance(v, str)
    }
    raw_locked = getattr(existing, "locked", False)
    native_locked = raw_locked if isinstance(raw_locked, bool) else False
    active_lock = AppMetadataStore._active_lock(
        _decode_cxas(clean_meta).get(LOCK_KEY)
    )
    return clean_meta, native_locked, active_lock


def _inject_preserved_cxas_metadata(
    staged_dir: str,
    existing_meta: Mapping[str, str],
) -> None:
    """Preserve server-side ``cxas.*`` metadata in staged ``app.json``/``app.yaml``."""
    has_cxas = any(k.startswith(METADATA_PREFIX) for k in existing_meta)
    if not has_cxas:
        return

    root = Path(staged_dir)
    app_cfg = next(
        (
            p
            for name in ("app.json", "app.yaml")
            if (p := root / name).is_file()
        ),
        None,
    )
    if app_cfg is None:
        return

    try:
        raw_text = app_cfg.read_text(encoding="utf-8")
        data = (
            yaml.safe_load(raw_text)
            if app_cfg.suffix in (".yaml", ".yml")
            else json.loads(raw_text)
        )
    except (OSError, ValueError, yaml.YAMLError):
        return
    if not isinstance(data, dict):
        return

    target_dict = data["app"] if isinstance(data.get("app"), dict) else data
    current_meta = target_dict.get("metadata")
    target_dict["metadata"] = preserve_cxas_keys(
        existing_meta,
        current_meta if isinstance(current_meta, Mapping) else None,
    )

    if app_cfg.suffix in (".yaml", ".yml"):
        app_cfg.write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
    else:
        app_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@contextlib.contextmanager
def _atomic_push_lock(
    *,
    meta_store: AppMetadataStore | None,
    with_lock: bool,
    was_natively_locked: bool,
    active_lock: dict[str, Any] | None,
    caller: str,
) -> Iterator[tuple[bool, dict[str, Any] | None]]:
    """Context manager for ``--with-lock``: unlock -> push -> guaranteed re-lock.

    Yields ``(ignore_app_lock, active_push_lock)`` so callers can inject the
    in-flight ``cxas.lock`` into the staged ``app.json``/``app.yaml`` before
    ``Apps.import_app()`` runs.
    """
    if not with_lock or meta_store is None:
        yield (
            bool(
                was_natively_locked
                and active_lock
                and active_lock.get("by") == caller
            ),
            active_lock,
        )
        return

    # Track whether we acquired a temporary lock solely for this push window.
    acquired_for_push = not (active_lock and active_lock.get("by") == caller)
    held_lock = active_lock
    if acquired_for_push:
        held_lock = meta_store.acquire_lock(
            user=caller,
            reason="cxas push --with-lock",
            native=True,
        )

    # Temporarily clear native App.locked while keeping cxas.lock intact, then
    # ALWAYS re-lock in finally — even if import_app fails! If we acquired the
    # lock solely for this push, clear cxas.lock while keeping native_lock=True
    # so we don't leave a 60-minute lease blocking subsequent CI runs/engineers.
    meta_store.update(lambda _d: None, native_lock=False, check_lock=False)
    try:
        yield True, held_lock
    finally:

        def _restore_lock(d: dict[str, Any]) -> None:
            if acquired_for_push:
                d.pop(LOCK_KEY, None)
            elif held_lock is not None:
                d[LOCK_KEY] = held_lock

        try:
            meta_store.update(_restore_lock, native_lock=True, check_lock=False)
        except Exception as relock_err:  # noqa: BLE001
            logger.warning(
                "Could not restore native lock after --with-lock push: %s",
                relock_err,
            )
            meta_store._relock_best_effort()


def _app_push(
    app_dir: str,
    apps_client: Apps = None,
    target_app_name: str | None = None,
    identifier: str | None = None,
    display_name: str | None = None,
    env_file: str | None = None,
    args: argparse.Namespace | None = None,
    profile: ResolvedProfile | None = None,
) -> str | None:
    """Helper to push an app to CXAS."""
    with_lock = bool(args and getattr(args, "with_lock", False))
    on_drift = (getattr(args, "on_drift", None) if args else None) or "warn"
    fail_if_version_exists = bool(
        args and getattr(args, "fail_if_version_exists", False)
    )
    create_version = bool(args and getattr(args, "create_version", False))
    requested_version_name = (
        getattr(args, "version_name", None) if args else None
    )

    caller = current_user()
    existing_meta, is_natively_locked, active_lock = _safe_existing_app_state(
        apps_client, target_app_name
    )
    meta_store = (
        AppMetadataStore(target_app_name, apps=apps_client, user=caller)
        if target_app_name and apps_client is not None
        else None
    )

    # 1. Enforce foreign edit lock (never overridden even by --force).
    if active_lock and active_lock.get("by") != caller:
        err = LockHeldError(active_lock)
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    if (
        target_app_name
        and is_natively_locked
        and not with_lock
        and not (active_lock and active_lock.get("by") == caller)
    ):
        print(
            f"Error: App '{target_app_name}' is natively locked. "
            "Pass --with-lock to atomically unlock/push/re-lock, or run "
            "`cxas unlock` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Stateless CES-side drift gate against cxas.last_push.
    if (
        target_app_name
        and meta_store is not None
        and on_drift != "overwrite"
        and f"{METADATA_PREFIX}last_push" in existing_meta
    ):
        try:
            drift_report = check_drift(target_app_name, store=meta_store)
        except Exception as drift_err:  # noqa: BLE001
            logger.debug("Drift check skipped: %s", drift_err)
            drift_report = None
        if drift_report is not None and drift_report.has_drift:
            if on_drift == "fail":
                print(
                    f"Error: {DriftDetectedError(drift_report)}",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Warning: {drift_report.format_summary()}", file=sys.stderr)

    # 3. Pre-check --fail-if-version-exists before mutating CES.
    if (
        target_app_name
        and create_version
        and requested_version_name
        and fail_if_version_exists
    ):
        pre_versions = Versions(
            app_name=target_app_name,
            creds=apps_client.creds,
        )
        existing_versions = pre_versions.list_versions()
        if any(
            getattr(v, "display_name", None) == requested_version_name
            for v in existing_versions
        ):
            print(
                f"Error: App version '{requested_version_name}' already exists "
                f"on {target_app_name} (--fail-if-version-exists).",
                file=sys.stderr,
            )
            sys.exit(1)

    temp_dir = tempfile.mkdtemp()
    temp_zip = tempfile.mktemp(suffix=".zip")
    inner_dir = os.path.join(temp_dir, "agent")
    os.makedirs(inner_dir)

    valid_roots = [
        "app.yaml",
        "app.json",
        "global_instruction.txt",
        "environment.json",
        "agents",
        "tools",
        "examples",
        "guardrails",
        "toolsets",
        "evaluations",
        "evaluationDatasets",
        "evaluationExpectations",
        ".github/workflows",
    ]

    try:
        for item in valid_roots:
            src_path = os.path.join(app_dir, item)
            if os.path.exists(src_path):
                dst_path = os.path.join(inner_dir, item)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)

        # Inject explicit env_file if provided
        env_file_missing = False
        if env_file:
            if os.path.exists(env_file):
                dst_path = os.path.join(inner_dir, "environment.json")
                shutil.copy2(env_file, dst_path)
                print(
                    f"Included custom environment file "
                    f"from {env_file} as environment.json"
                )
            else:
                env_file_missing = True
                print(
                    f"Warning: Custom environment file "
                    f"'{env_file}' not found. Skipping."
                )

        # Apply profile overrides to staged app.json / app.yaml BEFORE
        # $env_var and boolean env-override resolvers run.
        if profile is not None and profile.app_overrides:
            staged_cfg, overlay_warnings = apply_app_overrides_in_dir(
                inner_dir, profile
            )
            print_profile_warnings(overlay_warnings)
            if staged_cfg is not None:
                print(
                    f"Applied profile '{profile.name}' overrides to staged "
                    f"{staged_cfg.name}"
                )

        from cxas_scrapi.utils.tracing.app_config import (  # noqa: PLC0415
            has_bool_env_var_placeholders,
            resolve_env_booleans_in_dir,
        )

        if env_file_missing and (
            collect_env_var_declaration_names(inner_dir)
            or has_bool_env_var_placeholders(inner_dir)
        ):
            raise FileNotFoundError(
                f"Custom environment file '{env_file}' not found; required to "
                "resolve '$env_var' placeholders"
            )

        resolved_vars = resolve_app_env_variables_in_dir(inner_dir)
        if resolved_vars:
            print(
                f"Resolved {len(resolved_vars)} '$env_var' variable "
                f"default(s) from environment.json: {', '.join(resolved_vars)}"
            )

        # Resolve boolean $env_var placeholders and boolean environment
        # overrides in the staged inner_dir before creating the zip archive.
        resolve_env_booleans_in_dir(inner_dir)

        with _atomic_push_lock(
            meta_store=meta_store,
            with_lock=with_lock,
            was_natively_locked=is_natively_locked,
            active_lock=active_lock,
            caller=caller,
        ) as (ignore_app_lock, active_push_lock):
            # Preserve server-side cxas.* metadata keys (including any lock
            # acquired for this --with-lock window) so import_app never wipes
            # lock, last_push, workspace, or expiry state.
            staged_meta = dict(existing_meta)
            if active_push_lock is not None:
                staged_meta[f"{METADATA_PREFIX}{LOCK_KEY}"] = json.dumps(
                    active_push_lock, separators=(",", ":"), sort_keys=True
                )
            if staged_meta:
                _inject_preserved_cxas_metadata(inner_dir, staged_meta)

            # ZIP does not support timestamps before 1980.
            # Touch files and directories in temp_dir with timestamps < 1980.
            limit_time = time.mktime((1980, 1, 1, 0, 0, 0, 0, 0, 0))
            for root, dirs, files in os.walk(temp_dir):
                for name in files:
                    path = os.path.join(root, name)
                    if os.path.getmtime(path) < limit_time:
                        os.utime(path, None)
                for name in dirs:
                    path = os.path.join(root, name)
                    if os.path.getmtime(path) < limit_time:
                        os.utime(path, None)

            # Zip the filtered agent directory
            shutil.make_archive(temp_zip.replace(".zip", ""), "zip", temp_dir)

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
                import_kwargs: dict[str, Any] = {
                    "app_name": target_app_name,
                    "app_content": app_content,
                    "conflict_strategy": conflict_strategy,
                }
                if ignore_app_lock:
                    import_kwargs["ignore_app_lock"] = True
                result = apps_client.import_app(**import_kwargs)
            else:
                result = apps_client.import_as_new_app(
                    display_name=display_name, app_content=app_content
                )
            app_name = _handle_import_result(
                result, "pushed to" if identifier else "pushed"
            )

            # Record stateless CES-side drift baseline (cxas.last_push).
            if app_name and apps_client is not None:
                try:
                    post_store = (
                        meta_store
                        if meta_store is not None
                        and app_name == target_app_name
                        else AppMetadataStore(
                            app_name, apps=apps_client, user=caller
                        )
                    )
                    record_last_push(
                        app_name,
                        store=post_store,
                        user=caller,
                        app_dir=app_dir,
                    )
                except Exception as rec_err:  # noqa: BLE001
                    logger.debug(
                        "Could not record cxas.last_push baseline: %s", rec_err
                    )

            if create_version and app_name:
                print(f"Creating version for {app_name}...")
                versions_client = Versions(
                    app_name=app_name,
                    creds=apps_client.creds,
                )
                version_display_name = (
                    requested_version_name
                    or f"import-{time.strftime('%Y%m%d%H%M%S')}"
                )
                description = getattr(args, "version_description", None)
                version = versions_client.create_version(
                    display_name=version_display_name,
                    description=description,
                )
                version_name = version.name
                actual_display = getattr(version, "display_name", None)
                if (
                    isinstance(actual_display, str)
                    and actual_display
                    and actual_display != version_display_name
                ):
                    msg = (
                        "CES deduplicated version creation because the draft "
                        f"is unchanged since existing version "
                        f"'{actual_display}' ({version_name}); requested "
                        f"version name '{version_display_name}' was not applied."
                    )
                    if fail_if_version_exists:
                        print(f"Error: {msg}", file=sys.stderr)
                        sys.exit(1)
                    print(f"Warning: {msg}", file=sys.stderr)
                print(
                    f"Created app version: {version_name} "
                    f"with display name {actual_display or version_display_name}"
                )
                args.created_version_name = version_name

        return app_name

    except Exception as e:
        print(f"Failed to push app: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_zip):
            os.remove(temp_zip)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


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
    profile: ResolvedProfile | None = None
    try:
        profile = load_profile(getattr(args, "profile", None))
        guard_protected_mutation(
            profile,
            command="delete",
            allow_protected=getattr(args, "allow_protected", False),
        )
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if profile is not None:
        print_profile_warnings(profile.warnings)
        if not getattr(args, "project_id", None) and profile.target.project_id:
            args.project_id = profile.target.project_id
        if not getattr(args, "location", None) and profile.target.location:
            args.location = profile.target.location

    app_name_arg = getattr(args, "app_name", None)
    display_name_arg = getattr(args, "display_name", None)
    if not app_name_arg and not display_name_arg and profile is not None:
        if profile.target.app_resource_name:
            app_name_arg = profile.target.app_resource_name
        elif profile.target.display_name:
            display_name_arg = profile.target.display_name
            args.display_name = display_name_arg

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
