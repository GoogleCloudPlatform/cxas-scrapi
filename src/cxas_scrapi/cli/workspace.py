"""CLI subcommands for managing GECX Workspaces."""

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
import json
import os
import sys
from pathlib import Path

import toml
from rich.console import Console
from rich.syntax import Syntax

from cxas_scrapi.core import workspace as ws


def find_repo_root() -> Path | None:
    """Find the repository root by crawling up from CWD for standard markers."""
    try:
        path = Path.cwd().resolve()
    except Exception:
        return None
    home = Path.home().resolve()
    while True:
        if (
            (path / "WORKSPACE").exists()
            or (path / ".git").exists()
            or (path / ".jj").exists()
        ):
            return path
        if path == home or path == Path("/"):
            break
        parent = path.parent
        if parent == path:
            break
        path = parent
    return None


def workspace_set(args: argparse.Namespace) -> None:
    """Handles the 'workspace set' command, updating the active project pointer."""
    target_dir = getattr(args, "target_dir", None)
    updates = {
        "gcp_project_id": getattr(args, "gcp_project_id", None),
        "deployed_app_id": getattr(args, "deployed_app_id", None),
        "location": getattr(args, "location", None),
        "app_dir": getattr(args, "app_dir", None),
        "evals_dir": getattr(args, "evals_dir", None),
        "output_dir": getattr(args, "output_dir", None),
        "model": getattr(args, "model", None),
        "modality": getattr(args, "modality", None),
    }
    has_updates = any(v is not None for v in updates.values())

    if not target_dir:
        if getattr(args, "profile", None) or has_updates:
            try:
                target_dir = ws.resolve_project_dir()
            except ValueError:
                pass
        if not target_dir:
            print("Error: No path or update flags provided.")
            if hasattr(args, "parser"):
                args.parser.print_help()
            sys.exit(1)

    workspace_root = ws.find_workspace_root()
    if not workspace_root:
        repo_root = find_repo_root()
        workspace_root = str(repo_root) if repo_root else None

    target_path = Path(target_dir)

    resolved_path = None

    if target_path.is_absolute():
        resolved_path = target_path.resolve()
    else:
        # Try relative to CWD
        candidate = Path.cwd() / target_path
        if candidate.exists():
            resolved_path = candidate.resolve()
        elif workspace_root:
            # Try relative to workspace root
            candidate = Path(workspace_root) / target_path
            if candidate.exists():
                resolved_path = candidate.resolve()

        if not resolved_path:
            # Try relative to repository root
            repo_root = find_repo_root()
            if repo_root:
                candidate = repo_root / target_path
                if candidate.exists():
                    resolved_path = candidate.resolve()

    if not resolved_path:
        # Fallback to CWD resolution if not found
        resolved_path = (Path.cwd() / target_path).resolve()

    if not resolved_path.exists():
        print(f"Error: Path '{resolved_path}' does not exist.")
        sys.exit(1)

    if (
        not (resolved_path / "gecx-config.toml").exists()
        and not (resolved_path / "gecx-config.json").exists()
    ):
        print(
            f"Error: Directory '{resolved_path}' does not contain a"
            " gecx-config.toml or gecx-config.json file."
        )
        print(
            "\nTo initialize, please run:\n"
            f'1. cxas init --target-dir="{args.target_dir}"'
        )
        sys.exit(1)

    if not workspace_root:
        print("Error: Could not find SCRAPI workspace root.")
        sys.exit(1)

    rel_path = os.path.relpath(resolved_path, workspace_root)
    if rel_path.startswith("..") or os.path.isabs(rel_path):
        print(
            f"Error: The target directory '{resolved_path}' must be inside the"
            f" workspace root '{workspace_root}'."
        )
        sys.exit(1)

    active_project_dir = Path(workspace_root) / ".scrapi"
    active_project_dir.mkdir(parents=True, exist_ok=True)
    active_project_file = active_project_dir / "active-project"

    pointer_data = {}
    if active_project_file.is_file():
        try:
            with open(active_project_file, encoding="utf-8") as f:
                content = f.read().strip()
            try:
                pointer_data = toml.loads(content)
            except Exception:
                try:
                    pointer_data = json.loads(content)
                except Exception:
                    pass
        except Exception:
            pass

    pointer_data["base-dir"] = str(resolved_path)
    if getattr(args, "profile", None):
        pointer_data["active-profile"] = args.profile
    elif "active-profile" in pointer_data:
        del pointer_data["active-profile"]

    with open(active_project_file, "w", encoding="utf-8") as f:
        toml.dump(pointer_data, f)

    print(f"Successfully set active project to: {resolved_path}")

    # Update configuration if updates are provided
    if has_updates:
        try:
            updated, config_file = ws.update_workspace_config(updates)
            if updated:
                print(f"Updated configuration file: {config_file}")
            else:
                print("No updates provided.")

        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

    # Clear cached project dir and config cache, and update ws._project_dir
    ws._project_dir = str(resolved_path)
    ws._workspace_config_cache = None


def workspace_show(args: argparse.Namespace) -> None:
    """Handles the 'workspace show' command, printing the resolved configuration with styles."""
    try:
        project_dir = ws.resolve_project_dir()
        config = ws.load_workspace_config()
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    config_path = Path(project_dir) / "gecx-config.toml"
    if not config_path.exists():
        config_path = Path(project_dir) / "gecx-config.json"

    profile = None
    workspace_root = ws.find_workspace_root()
    if workspace_root:
        active_project_file = (
            Path(workspace_root) / ".scrapi" / "active-project"
        )
        if active_project_file.is_file():
            try:
                with active_project_file.open("r", encoding="utf-8") as f:
                    profile = toml.load(f).get("active-profile")
            except Exception:
                pass

    console = Console()

    console.print(
        f"[bold green]Project Path:[/bold green] [cyan]{project_dir}[/cyan]"
    )
    console.print(
        f"[bold green]Configuration File:[/bold green] [cyan]{config_path}[/cyan]"
    )
    console.print(
        "[bold green]Active Profile:[/bold green]"
        f" [yellow]{profile or 'default'}[/yellow]"
    )
    console.print("-" * 40)

    # Filter out internal private keys from printed configuration
    printed_config = {k: v for k, v in config.items() if not k.startswith("_")}
    json_str = json.dumps(printed_config, indent=2)
    syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
    console.print(syntax)


def workspace_create(args: argparse.Namespace) -> None:
    """Handles the 'workspace create' command, creating only gecx-config.json."""
    target_dir = Path(getattr(args, "target_dir", ".")).resolve()
    ws.create_default_config(str(target_dir))


def workspace_unset(args: argparse.Namespace) -> None:
    """Handles the 'workspace unset' command, deleting pointer files."""
    if ws.unset_active_project():
        print("Successfully unset the active project workspace configuration.")
    else:
        print("No active project workspace configuration was set.")
