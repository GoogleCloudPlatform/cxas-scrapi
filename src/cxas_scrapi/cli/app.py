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
import io
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common

logger = logging.getLogger(__name__)


def _resolve_app_args(
    app_identifier: str, args: argparse.Namespace
) -> tuple[Apps, str, str]:
    """Resolves project, location, Apps client, app_id, and display_name."""
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
    app_id = app_identifier
    display_name = app_identifier

    if "projects/" not in app_identifier:
        app = apps_client.get_app_by_display_name(app_identifier)
        if app:
            app_id = app.name
            display_name = app.display_name
        else:
            print(f"App '{app_identifier}' not found.")
            sys.exit(1)

    return apps_client, app_id, display_name


def _handle_import_result(result: Any, success_verb: str) -> Optional[str]:
    """Helper to wait for import LRO and print success message."""
    if hasattr(result, "result"):
        print("Waiting for import to complete...")
        app = result.result()
    else:
        app = result

    app_name = getattr(app, "name", None)
    if app_name:
        print(f"Successfully {success_verb}: {app_name}")
    else:
        print(f"Successfully {success_verb}.")
    return app_name


def app_pull(args: argparse.Namespace) -> None:
    """Handles the 'pull' command."""
    print(f"Pulling app: {args.app}")

    apps_client, app_id, _ = _resolve_app_args(args.app, args)

    _app_pull(apps_client, app_id, args.target_dir)


def _app_pull(apps_client: Apps, app_id: str, target_dir: str) -> None:
    """Helper to pull an app from CXAS."""
    try:
        # Export the app
        print("Exporting app from CXAS...")
        lro = apps_client.export_app(app_name=app_id)
        response = lro.result()

        # Determine the target directory
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)

        # Extract content to target directory.
        with zipfile.ZipFile(io.BytesIO(response.app_content)) as z:
            z.extractall(target_dir)

        print("Successfully pulled app.")

    except Exception as e:
        print(f"Failed to pull app: {e}")
        sys.exit(1)


def app_push(args: argparse.Namespace) -> Optional[str]:  # noqa: C901
    """Handles the 'push' command."""
    # We will reuse the deploy_agent logic from main.py, slightly adjusted.
    app_dir = args.app_dir if args.app_dir else "."
    print(f"Pushing app from {app_dir}...")

    target_app = getattr(args, "to", None)
    app_id_arg = getattr(args, "app_id", None)
    identifier = target_app or app_id_arg

    if identifier:
        apps_client, app_id, display_name = _resolve_app_args(identifier, args)
        print("Pushing to existing app... Overwriting if supported.")
    else:
        apps_client = Apps(project_id=args.project_id, location=args.location)
        app_id = None
        print("No target specified, using existing name if needed.")
        display_name = getattr(args, "display_name", None) or "Pushed Agent"

    _app_push(
        app_dir=app_dir,
        apps_client=apps_client,
        target_app_id=getattr(args, "app_id", None) or app_id,
        identifier=getattr(args, "to", None) or getattr(args, "app_id", None),
        display_name=getattr(args, "display_name", None) or display_name,
        env_file=getattr(args, "env_file", None),
    )


def _app_push(
    app_dir: str,
    apps_client: Apps = None,
    target_app_id: str = None,
    identifier: str = None,
    display_name: str = None,
    env_file: str = None,
) -> Optional[str]:
    """Helper to push an app to CXAS."""
    temp_dir = tempfile.mkdtemp()
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
    if env_file:
        if os.path.exists(env_file):
            dst_path = os.path.join(inner_dir, "environment.json")
            shutil.copy2(env_file, dst_path)
            print(
                f"Included custom environment file "
                f"from {env_file} as environment.json"
            )
        else:
            print(
                f"Warning: Custom environment file "
                f"'{env_file}' not found. Skipping."
            )

    # Zip the filtered agent directory
    temp_zip = tempfile.mktemp(suffix=".zip")
    shutil.make_archive(temp_zip.replace(".zip", ""), "zip", temp_dir)

    try:
        with open(temp_zip, "rb") as f:
            app_content = f.read()

        # If no target is specified, the SDK creates a new app with the
        # provided display_name.
        print("Uploading to CES...")

        if target_app_id:
            # Extract UUID if full resource name provided.
            if "apps/" in target_app_id:
                target_app_id = target_app_id.rsplit("apps/", maxsplit=1)[-1]
            result = apps_client.import_app(
                app_name=target_app_id, app_content=app_content
            )
        else:
            result = apps_client.import_as_new_app(
                display_name=display_name, app_content=app_content
            )
        return _handle_import_result(
            result, "pushed to" if identifier else "pushed"
        )

    except Exception as e:
        print(f"Failed to push app: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_zip):
            os.remove(temp_zip)


def app_create(args: argparse.Namespace) -> None:
    """Handles the 'create' command."""
    print(f"Creating app: {args.name}")
    apps_client = Apps(project_id=args.project_id, location=args.location)
    try:
        app = apps_client.create_app(
            app_id=getattr(args, "app_name", None),
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
        app_id = app_name_arg
    elif args.display_name and args.project_id and args.location:
        print(f"Deleting App by Display Name: {args.display_name}")
        project_id = args.project_id
        location = args.location
        app_id = None
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
        if not app_id:
            # Lookup by display name
            app = apps_client.get_app_by_display_name(args.display_name)
            if app:
                app_id = app.name
                print(f"Found app ID: {app_id}")
            else:
                print(
                    f"App with display name '{args.display_name}' "
                    "not found. Nothing to delete."
                )
                return

        apps_client.delete_app(app_name=app_id, force=args.force)
        print(f"Successfully deleted {app_id}")
    except Exception as e:
        print(f"Failed to delete app: {e}")
        sys.exit(1)


def app_branch(args: argparse.Namespace) -> None:
    """Handles the 'branch' command."""
    print(f"Branching from {args.source} to {args.new_name}")
    # Composite operation: pull existing, create new, push content.

    apps_client, app_id, _ = _resolve_app_args(args.source, args)
    env_file = getattr(args, "env_file", None)

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            print("Pulling source app...")
            _app_pull(apps_client, app_id, temp_dir)

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

    apps_client, app_id, _ = _resolve_app_args(args.app, args)

    try:
        app = apps_client.get_app(app_id=app_id)
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


def app_lint(args: argparse.Namespace) -> None:  # noqa: C901
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

    if getattr(args, "list_rules", False):
        print("CXAS Agent Linter — Available Rules")
        print("=" * 60)
        registry.list_rules()
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
        for result in rule_obj.check(resource_path, "", context):
            report.add(result)
        report.print_and_exit(json_output, show_fixes)
        return

    # Full app lint
    project_root = Path(getattr(args, "app_dir", ".")).resolve()
    config = LintConfig.load(project_root)

    app_dir = project_root / config.app_dir
    evals_dir = project_root / config.evals_dir
    discovery = Discovery(app_dir, evals_dir)

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

    context = build_context(project_root, config, discovery)

    if not json_output:
        print(f"Linting app: {discovery.app_root.name}")
        print("=" * 60)
        agents = discovery.discover_agents()
        tools = discovery.discover_tools()
        callbacks = discovery.discover_callbacks()
        evals = discovery.discover_evals()
        print(f"  Agents: {len(agents)}")
        print(f"  Tools: {len(tools)}")
        print(f"  Callbacks: {len(callbacks)}")
        print(f"  Evals: {len(evals)}")

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
    )

    report.print_and_exit(json_output, show_fixes)


def app_init(args: argparse.Namespace) -> None:
    """Handles the 'init' command -- copies skill files."""
    import shutil  # noqa: PLC0415

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
            choice = _prompt_overwrite(item.name)
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


def _prompt_overwrite(name: str) -> str:
    """Prompt user for overwrite decision."""
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
