#!/usr/bin/env python3
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

"""Interactive configuration wizard for gecx-config.json.

Uses Rich for display and InquirerPy for interactive prompts.
Called by setup.sh after virtualenv and dependencies are installed.
"""

import json
import os
import subprocess
import sys
import uuid
import warnings

# Suppress noisy GCP auth warnings during app listing
warnings.filterwarnings("ignore", message=".*quota project.*")
warnings.filterwarnings("ignore", message=".*end user credentials.*")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.utils import get_style
from InquirerPy.validator import EmptyInputValidator

console = Console()

# Keybinding: map Escape to skip/cancel so fuzzy prompts can be aborted.
ESCAPE_KEYBINDINGS = {"skip": [{"key": "escape"}]}

# Style with a contrasting background for the highlighted row.
# Uses a steel-blue background with white text for the pointer/selected row,
# and magenta for fuzzy-matched characters so they pop against both
# light and dark terminal themes.
PROMPT_STYLE = get_style(
    {
        "pointer": "#ffffff bg:#4a6fa5",
        "fuzzy_match": "#ff79c6 bold",
        "fuzzy_prompt": "#6c99bb",
        "fuzzy_info": "#888888",
        "fuzzy_border": "#4a6fa5",
        "questionmark": "#6c99bb bold",
        "answer": "#61afef",
        "input": "#98c379",
        "marker": "#e5c07b",
    },
    style_override=False,
)

def _resolve_config_dir():
    """Return the directory where gecx-config.json and cxas_app/ should live.

    If .active-project is set, use that project directory.
    Otherwise fall back to CWD.
    """
    pointer = os.path.join(os.getcwd(), ".active-project")
    if os.path.exists(pointer):
        with open(pointer) as f:
            name = f.read().strip()
        if name:
            candidate = os.path.join(os.getcwd(), name)
            if os.path.isdir(candidate):
                return candidate
    return os.getcwd()


def _config_file():
    return os.path.join(_resolve_config_dir(), "gecx-config.json")


# Kept for backward compat with code that reads CONFIG_FILE directly
CONFIG_FILE = _config_file()

DEFAULT_PROJECTS = [
]

LOCATIONS = ["us", "eu"]


def banner():
    console.print()
    console.print(
        Panel(
            "[bold]GECX Project Configuration Wizard[/bold]\n"
            "This will create [cyan]gecx-config.json[/cyan] with your project settings.",
            box=box.DOUBLE,
            style="blue",
        )
    )
    console.print()



def fetch_cxas_apps(project_id, location):
    """Fetch existing CXAS apps for a project using SCRAPI."""
    try:
        from cxas_scrapi.core.apps import Apps

        apps_client = Apps(project_id=project_id, location=location)
        apps_list = apps_client.list_apps()
        results = []
        for app in apps_list:
            display_name = getattr(app, "display_name", None) or ""
            name = getattr(app, "name", "") or ""
            app_id = name.split("/")[-1] if "/" in name else name
            results.append({"display_name": display_name, "app_id": app_id, "name": name})
        return results
    except Exception as e:
        console.print(f"  [dim]Could not fetch apps: {e}[/dim]")
        return []


def select_project():
    """Select GCP project ID — prompts user to enter it directly."""
    console.print("[bold]1. GCP Project ID[/bold]")

    default = DEFAULT_PROJECTS[0] if DEFAULT_PROJECTS else ""

    project = inquirer.text(
        message="Enter GCP project ID:",
        default=default,
        validate=EmptyInputValidator("Project ID cannot be empty"),
    ).execute()

    console.print(f"  Selected: [green]{project}[/green]\n")
    return project


def select_location():
    """Select CXAS location."""
    console.print("[bold]2. Location[/bold]")

    location = inquirer.select(
        message="Select location:",
        choices=LOCATIONS,
        default="us",
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()

    console.print(f"  Selected: [green]{location}[/green]\n")
    return location


def select_app(project_id, location):
    """Select an existing CXAS app or create a new one.

    Pressing Escape during fuzzy search returns to the mode selection.
    """
    console.print("[bold]3. CXAS App[/bold]")

    # Cache fetched apps so we don't re-fetch on Escape loop
    apps = None
    app_labels = None
    app_lookup = None

    while True:
        mode = inquirer.select(
            message="How do you want to specify the app?",
            choices=[
                Choice(value="existing", name="Search existing apps in CXAS"),
                Choice(value="create", name="Create a new app in CXAS"),
            ],
            default="existing",
            pointer=">",
            style=PROMPT_STYLE,
        ).execute()

        if mode == "create":
            return _create_new_app(project_id, location)

        # Fetch apps from CXAS (once)
        if apps is None:
            console.print(f"  [dim]Fetching apps from {project_id}/{location}...[/dim]")
            apps = fetch_cxas_apps(project_id, location)

            if not apps:
                console.print("  [yellow]No apps found.[/yellow]")
                continue

            # Build choices as simple strings for reliable fuzzy matching
            app_labels = []
            app_lookup = {}
            for app in apps:
                if app["display_name"]:
                    label = f"{app['display_name']}  ({app['app_id']})"
                else:
                    label = app["app_id"]
                app_labels.append(label)
                app_lookup[label] = app

        console.print(f"  [dim]Found {len(apps)} apps. Type to filter, Escape to go back.[/dim]")

        selected_label = inquirer.fuzzy(
            message="Search for an app (type to filter, Esc to go back):",
            choices=app_labels,
            max_height="60%",
            style=PROMPT_STYLE,
            mandatory=False,
            keybindings=ESCAPE_KEYBINDINGS,
        ).execute()

        if selected_label is None:
            # User pressed Escape — loop back to mode choice
            console.print("  [dim]Cancelled. Choose again.[/dim]")
            continue

        selected = app_lookup[selected_label]
        console.print(f"  Selected: [green]{selected['display_name']}[/green] ({selected['app_id']})\n")
        return selected["app_id"], selected["display_name"]


def _create_new_app(project_id, location):
    """Create a new app in CXAS via SCRAPI and return the platform-assigned UUID."""
    generated_id = str(uuid.uuid4())
    app_id_slug = inquirer.text(
        message="App ID:",
        default=generated_id,
    ).execute()
    display_name = inquirer.text(
        message="Display name:",
        validate=EmptyInputValidator("Display name cannot be empty"),
    ).execute()
    description = inquirer.text(
        message="Description (optional):",
        default="",
    ).execute()

    console.print(f"  [dim]Creating app '{app_id_slug}' in {project_id}/{location}...[/dim]")

    try:
        from cxas_scrapi.core.apps import Apps

        apps_client = Apps(project_id=project_id, location=location)
        app = apps_client.create_app(
            app_id=app_id_slug,
            display_name=display_name,
            description=description or None,
        )
        app_name = getattr(app, "name", "") or ""
        # The platform assigns a UUID — extract it from the resource path
        real_app_id = app_name.split("/")[-1] if "/" in app_name else app_id_slug

        console.print(f"  [bold green]Created![/bold green] App ID: [green]{real_app_id}[/green]\n")
        return real_app_id, display_name

    except Exception as e:
        console.print(f"  [red]Failed to create app: {e}[/red]")
        console.print("  [yellow]Create the app manually, then re-run .agents/skills/cxas-agent-foundry/scripts/setup.sh --configure[/yellow]")

        return None, display_name


def select_modality():
    """Select default modality."""
    console.print("[bold]4. Default Modality[/bold]")

    modality = inquirer.select(
        message="Select default modality:",
        choices=[
            Choice(value="audio", name="audio  (voice-first agent)"),
            Choice(value="text", name="text   (chat-first agent)"),
        ],
        default="audio",
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()

    console.print(f"  Selected: [green]{modality}[/green]\n")
    return modality


def select_model():
    """Select the default model for the agent."""
    console.print("[bold]5. Model[/bold]")

    model = inquirer.text(
        message="Enter model name:",
        default="gemini-3.1-flash-live",
    ).execute()

    console.print(f"  Selected: [green]{model}[/green]\n")
    return model


def select_gcs_bucket():
    """Enter GCS bucket for storing artifacts."""
    console.print("[bold]6. GCS Bucket[/bold]")

    def validate_bucket(val):
        if not val:
            return True  # optional
        if not val.startswith("gs://"):
            return "Bucket must start with gs://"
        return True

    bucket = inquirer.text(
        message="Enter GCS bucket (leave empty to skip):",
        default="",
        validate=validate_bucket,
    ).execute()

    if bucket:
        console.print(f"  Bucket: [green]{bucket}[/green]\n")
    else:
        console.print("  [dim]Skipped[/dim]\n")
    return bucket or None


def review_and_confirm(config):
    """Show a summary table and ask for confirmation."""
    console.print("[bold]Review Configuration[/bold]\n")

    table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("GCP Project", config["gcp_project_id"])
    table.add_row("Location", config["location"])
    table.add_row("App Name", config.get("app_name", ""))
    app_id_display = config["deployed_app_id"] or "[yellow]Not yet assigned (will be set after app creation)[/yellow]"
    table.add_row("App ID", app_id_display)
    table.add_row("Model", config.get("model", "gemini-3.1-flash-live"))
    table.add_row("Modality", config["modality"])
    table.add_row("App Directory", config["app_dir"])
    if config.get("gcs_bucket"):
        table.add_row("GCS Bucket", config["gcs_bucket"])

    console.print(table)
    console.print()

    confirmed = inquirer.confirm(
        message="Write this configuration to gecx-config.json?",
        default=True,
        style=PROMPT_STYLE,
    ).execute()

    return confirmed


def load_existing_config():
    """Load existing config if present."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def _show_current_config(config):
    """Display the current configuration."""
    table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("GCP Project", config.get("gcp_project_id", ""))
    table.add_row("Location", config.get("location", ""))
    table.add_row("App Name", config.get("app_name", ""))
    app_id = config.get("deployed_app_id")
    table.add_row("App ID", app_id or "[yellow]Not set[/yellow]")
    table.add_row("Model", config.get("model", "gemini-3.1-flash-live"))
    table.add_row("Modality", config.get("modality", ""))
    table.add_row("App Directory", config.get("app_dir", ""))
    if config.get("gcs_bucket"):
        table.add_row("GCS Bucket", config["gcs_bucket"])

    console.print(table)


def _has_local_changes(app_dir):
    """Check if the app directory has uncommitted git changes."""
    if not os.path.isdir(app_dir):
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", app_dir],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # git not available or timed out — check if dir has any files
        return any(os.scandir(app_dir))


def main():
    banner()

    existing = load_existing_config()
    if existing:
        console.print(f"[yellow]Existing {CONFIG_FILE} found:[/yellow]\n")
        _show_current_config(existing)
        console.print()

        action = inquirer.select(
            message="What would you like to do?",
            choices=[
                Choice(value="reconfigure", name="Reconfigure (update settings)"),
                Choice(value="pull_only", name="Pull app from CXAS (refresh local files)"),
                Choice(value="exit", name="Exit (keep current config)"),
            ],
            default="exit",
            pointer=">",
            style=PROMPT_STYLE,
        ).execute()

        if action == "exit":
            console.print("\n[dim]No changes made.[/dim]")
            return

        if action == "pull_only":
            app_dir = os.path.join(_resolve_config_dir(), existing.get("app_dir", "cxas_app/"))
            if _has_local_changes(app_dir):
                confirm_pull = inquirer.confirm(
                    message=f"{app_dir} has uncommitted changes. Pull will overwrite them. Continue?",
                    default=False,
                    style=PROMPT_STYLE,
                ).execute()
                if not confirm_pull:
                    console.print("\n[dim]Cancelled.[/dim]")
                    return
            _pull_app(existing)
            return

        # reconfigure — fall through to the wizard

    # Gather inputs
    project_id = select_project()
    location = select_location()
    app_id, app_name = select_app(project_id, location)
    modality = select_modality()
    model = select_model()
    gcs_bucket = select_gcs_bucket()

    # Build config
    config = {
        "gcp_project_id": project_id,
        "location": location,
        "app_name": app_name,
        "deployed_app_id": app_id,
        "app_dir": "cxas_app/",
        "model": model,
        "modality": modality,
        "default_channel": modality,
        "environments": {
            "dev": {
                "app_id": app_id,
                "description": "Development/sandbox app",
            },
            "prod": {
                "app_id": None,
                "description": "Production app (not yet configured)",
            },
        },
    }

    if gcs_bucket:
        config["gcs_bucket"] = gcs_bucket

    # Review
    if review_and_confirm(config):
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        console.print(f"\n[bold green]Wrote {CONFIG_FILE}[/bold green]")
        if not config["deployed_app_id"]:
            console.print(
                "[yellow]Note: deployed_app_id is not set. It will be updated automatically\n"
                "after you create and push the app to CXAS.[/yellow]"
            )
        else:
            # Pull the app locally — warn if overwriting local changes
            app_dir = os.path.join(_resolve_config_dir(), config["app_dir"])
            if _has_local_changes(app_dir):
                confirm_pull = inquirer.confirm(
                    message=f"{app_dir} has uncommitted changes. Pull will overwrite them. Continue?",
                    default=False,
                    style=PROMPT_STYLE,
                ).execute()
                if not confirm_pull:
                    console.print(f"\n[dim]Skipped pull. Run manually later: cxas pull ...[/dim]")
                    return
            _pull_app(config)
    else:
        console.print("\n[yellow]Configuration cancelled. Run again to retry.[/yellow]")
        sys.exit(1)


def _pull_app(config):
    """Pull the app from CXAS into the local app directory."""
    app_id = config["deployed_app_id"]
    project = config["gcp_project_id"]
    location = config["location"]
    # Resolve app_dir relative to the project directory
    app_dir = os.path.join(_resolve_config_dir(), config["app_dir"])

    console.print(f"\n[bold]Pulling app to [cyan]{app_dir}[/cyan]...[/bold]")

    # Set GOOGLE_CLOUD_PROJECT so gcloud/SCRAPI can resolve the project
    env = os.environ.copy()
    env["GOOGLE_CLOUD_PROJECT"] = project
    # Suppress Python warnings from child process
    env["PYTHONWARNINGS"] = "ignore"

    # cxas pull requires the full resource path, not just the UUID
    if app_id.startswith("projects/"):
        app_resource = app_id
    else:
        app_resource = f"projects/{project}/locations/{location}/apps/{app_id}"

    pull_cmd = [
        "cxas", "pull", app_resource,
        "--project-id", project,
        "--location", location,
        "--target-dir", app_dir,
    ]

    try:
        result = subprocess.run(
            pull_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

        # Filter out warning lines from stderr — only keep actual errors
        stderr_lines = [
            line for line in (result.stderr or "").strip().split("\n")
            if line.strip()
            and "UserWarning" not in line
            and "warnings.warn" not in line
            and "WARNING" not in line
            and "Consider running" not in line
            and "troubleshooting" not in line
        ]
        real_errors = "\n".join(stderr_lines).strip()

        if result.returncode == 0:
            console.print(f"[bold green]App pulled to {app_dir}[/bold green]")
        elif real_errors:
            console.print(f"[red]Pull failed: {real_errors}[/red]")
            console.print(f"[yellow]Try manually: {' '.join(pull_cmd)}[/yellow]")
        else:
            # Non-zero exit but only warnings in stderr — might have still worked
            if os.path.isdir(app_dir) and any(os.scandir(app_dir)):
                console.print(f"[bold green]App pulled to {app_dir}[/bold green] [dim](with warnings)[/dim]")
            else:
                console.print(f"[red]Pull failed (no files created).[/red]")
                console.print(f"[yellow]Try manually: {' '.join(pull_cmd)}[/yellow]")
    except FileNotFoundError:
        console.print("[yellow]cxas command not found. Pull manually:[/yellow]")
        console.print(f"[dim]  {' '.join(pull_cmd)}[/dim]")
    except subprocess.TimeoutExpired:
        console.print("[yellow]Pull timed out. Try manually:[/yellow]")
        console.print(f"[dim]  {' '.join(pull_cmd)}[/dim]")


def parse_args():
    """Parse CLI arguments for non-interactive mode."""
    import argparse

    parser = argparse.ArgumentParser(description="GECX Project Configuration")
    parser.add_argument("--project-id", help="GCP project ID")
    parser.add_argument("--location", choices=LOCATIONS, help="CXAS location")
    parser.add_argument("--app-id", help="Existing CXAS app ID")
    parser.add_argument("--app-name", help="App display name")
    parser.add_argument("--create-app", action="store_true", help="Create a new app")
    parser.add_argument("--modality", choices=["audio", "text"], help="Default modality")
    parser.add_argument("--model", help="Model name")
    parser.add_argument("--gcs-bucket", help="GCS bucket (gs://...)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Run without interactive prompts (requires --project-id)")
    return parser.parse_args()


def main_non_interactive(args):
    """Run configuration without interactive prompts."""
    banner()

    if not args.project_id:
        console.print("[red]--project-id is required in non-interactive mode[/red]")
        sys.exit(1)

    project_id = args.project_id
    location = args.location or "us"
    modality = args.modality or "audio"
    model = args.model or "gemini-3.1-flash-live"
    gcs_bucket = args.gcs_bucket or None

    console.print(f"  Project: [green]{project_id}[/green]")
    console.print(f"  Location: [green]{location}[/green]")

    # Determine app
    app_id = args.app_id
    app_name = args.app_name or ""

    if args.create_app:
        if not app_name:
            console.print("[red]--app-name is required when creating a new app[/red]")
            sys.exit(1)
        console.print(f"  [dim]Creating app '{app_name}' in {project_id}/{location}...[/dim]")
        try:
            from cxas_scrapi.core.apps import Apps
            apps_client = Apps(project_id=project_id, location=location)
            app = apps_client.create_app(
                app_id=str(uuid.uuid4()),
                display_name=app_name,
                description=None,
            )
            resource_name = getattr(app, "name", "") or ""
            app_id = resource_name.split("/")[-1] if "/" in resource_name else app_id
            console.print(f"  [bold green]Created![/bold green] App ID: [green]{app_id}[/green]")
        except Exception as e:
            console.print(f"  [red]Failed to create app: {e}[/red]")
            app_id = None
    elif not app_id:
        # List apps and let the caller pick by inspecting output,
        # or just leave app_id unset for later configuration
        console.print("  [yellow]No --app-id provided. Listing available apps...[/yellow]")
        apps = fetch_cxas_apps(project_id, location)
        if apps:
            for app in apps[:20]:
                name = app.get("display_name") or "(unnamed)"
                console.print(f"    {name}  [dim]({app['app_id']})[/dim]")
            if len(apps) > 20:
                console.print(f"    [dim]... and {len(apps) - 20} more[/dim]")
            console.print("\n  [yellow]Re-run with --app-id <id> to select one.[/yellow]")
            sys.exit(1)
        else:
            console.print("  [yellow]No apps found. Use --create-app --app-name <name> to create one.[/yellow]")
            sys.exit(1)

    config = {
        "gcp_project_id": project_id,
        "location": location,
        "app_name": app_name,
        "deployed_app_id": app_id,
        "app_dir": "cxas_app/",
        "model": model,
        "modality": modality,
        "default_channel": modality,
        "environments": {
            "dev": {
                "app_id": app_id,
                "description": "Development/sandbox app",
            },
            "prod": {
                "app_id": None,
                "description": "Production app (not yet configured)",
            },
        },
    }

    if gcs_bucket:
        config["gcs_bucket"] = gcs_bucket

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    console.print(f"\n[bold green]Wrote {CONFIG_FILE}[/bold green]")

    if app_id:
        _pull_app(config)


if __name__ == "__main__":
    args = parse_args()
    if args.non_interactive:
        main_non_interactive(args)
    else:
        main()
