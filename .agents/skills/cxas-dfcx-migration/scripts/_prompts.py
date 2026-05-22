# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""InquirerPy-based prompt library shared across migrate / stage1 / stage2.

Mirrors the prompt patterns used in the cxas-agent-foundry skill's
configure.py so the UX is consistent across both skills.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.utils import get_style
from InquirerPy.validator import EmptyInputValidator

from cxas_scrapi.core.apps import Apps

logger = logging.getLogger(__name__)

LOCATIONS = ["us", "eu", "global"]
DEFAULT_LOCATION = "us"

ESCAPE_KEYBINDINGS = {"skip": [{"key": "escape"}]}

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


def is_interactive() -> bool:
    """True if stdin is a TTY (so InquirerPy prompts work). Otherwise the
    caller should fall back to the supplied --flag values or fail loudly."""
    return os.isatty(0) and os.isatty(1)


def _ensure_interactive(prompt_label: str) -> None:
    if not is_interactive():
        raise RuntimeError(
            f"Interactive prompt for {prompt_label!r} requested but stdin is "
            "not a TTY. Pass the value via CLI flag (or run with --yes if "
            "the script supports defaults)."
        )


# ---------------------------------------------------------------------------
# Top-level prompts
# ---------------------------------------------------------------------------


def prompt_project_id(default: str | None = None) -> str:
    _ensure_interactive("project_id")
    return inquirer.text(
        message="GCP project ID:",
        default=default or "",
        validate=EmptyInputValidator("Project ID cannot be empty"),
        style=PROMPT_STYLE,
    ).execute()


def prompt_location(default: str = DEFAULT_LOCATION) -> str:
    _ensure_interactive("location")
    if default not in LOCATIONS:
        default = DEFAULT_LOCATION
    return inquirer.select(
        message="Location:",
        choices=LOCATIONS,
        default=default,
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()


def prompt_target_name(default: str) -> str:
    _ensure_interactive("target_name")
    return inquirer.text(
        message="Target CXAS app name:",
        default=default,
        validate=EmptyInputValidator("Target name cannot be empty"),
        style=PROMPT_STYLE,
    ).execute()


def prompt_env(default: str = "PROD") -> str:
    _ensure_interactive("env")
    return inquirer.select(
        message="Environment:",
        choices=["PROD", "AUTOPUSH"],
        default=default,
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()


def prompt_logic_version(default: str = "2.0") -> str:
    _ensure_interactive("logic_version")
    return inquirer.select(
        message="Logic version:",
        choices=["2.0", "1.0"],
        default=default,
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()


def prompt_model(choices: list[str], default: str) -> str:
    _ensure_interactive("model")
    if len(choices) > 5:
        return inquirer.fuzzy(
            message="Global app model (type to filter):",
            choices=choices,
            default=default,
            max_height="40%",
            style=PROMPT_STYLE,
        ).execute()
    return inquirer.select(
        message="Global app model:",
        choices=choices,
        default=default,
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()


def prompt_yes_no(message: str, default: bool = True) -> bool:
    _ensure_interactive(message)
    return inquirer.confirm(
        message=message,
        default=default,
        style=PROMPT_STYLE,
    ).execute()


# ---------------------------------------------------------------------------
# Source agent picker
# ---------------------------------------------------------------------------


def prompt_source_load_mode() -> str:
    _ensure_interactive("source_load_mode")
    return inquirer.select(
        message="Load source DFCX agent from:",
        choices=[
            Choice(value="ID", name="Agent ID (fetch via API)"),
            Choice(value="Zip", name="Local .zip export"),
        ],
        default="ID",
        pointer=">",
        style=PROMPT_STYLE,
    ).execute()


def prompt_source_agent_id(default: str | None = None) -> str:
    _ensure_interactive("source_agent_id")
    return inquirer.text(
        message="Source DFCX agent ID:",
        default=default or "",
        validate=EmptyInputValidator("Agent ID cannot be empty"),
        style=PROMPT_STYLE,
    ).execute()


def prompt_zip_path(default: str | None = None) -> str:
    _ensure_interactive("zip_path")
    return inquirer.filepath(
        message="Path to local DFCX export (.zip):",
        default=default or "~/",
        validate=EmptyInputValidator("Path cannot be empty"),
        style=PROMPT_STYLE,
    ).execute()


# ---------------------------------------------------------------------------
# CXAS app picker (used by stage1 / stage2 when no IR bundle is available)
# ---------------------------------------------------------------------------


def fetch_cxas_apps(project_id: str, location: str) -> list[dict]:
    """Fetch existing CXAS apps for a project. Returns [] on failure."""
    try:
        apps_client = Apps(project_id=project_id, location=location)
        apps_list = apps_client.list_apps()
        results = []
        for app in apps_list:
            display_name = getattr(app, "display_name", None) or ""
            name = getattr(app, "name", "") or ""
            app_id = name.split("/")[-1] if "/" in name else name
            results.append(
                {"display_name": display_name, "app_id": app_id, "name": name}
            )
        return results
    except Exception as exc:
        logger.warning(
            "Could not fetch apps from %s/%s: %s", project_id, location, exc
        )
        return []


def prompt_app_picker(project_id: str, location: str) -> tuple[str, str, str]:
    """Pick an existing CXAS app via fuzzy search.

    Returns (app_id, display_name, app_resource_name).
    """
    _ensure_interactive("app_picker")
    apps = fetch_cxas_apps(project_id, location)
    if not apps:
        raise RuntimeError(
            f"No CXAS apps found in {project_id}/{location} (or list failed)."
        )

    labels = []
    lookup = {}
    for app in apps:
        if app["display_name"]:
            label = f"{app['display_name']}  ({app['app_id']})"
        else:
            label = app["app_id"]
        labels.append(label)
        lookup[label] = app

    selected_label = inquirer.fuzzy(
        message="Select an app (type to filter, Esc to cancel):",
        choices=labels,
        max_height="60%",
        style=PROMPT_STYLE,
        mandatory=True,
        keybindings=ESCAPE_KEYBINDINGS,
    ).execute()
    if selected_label is None:
        raise RuntimeError("App selection cancelled.")

    selected = lookup[selected_label]
    return selected["app_id"], selected["display_name"], selected["name"]


# ---------------------------------------------------------------------------
# Resource selection (replaces MigrationCLI.select_resources rich.Prompt
# version with a pretty checkbox)
# ---------------------------------------------------------------------------


def prompt_resource_selection(
    items: list[tuple[str, str, Any]],
) -> list[tuple[str, str, Any]]:
    """Multi-select picker over (resource_type, display_name, raw_data).

    Returns the subset the user kept. Default = all selected.
    """
    _ensure_interactive("resources")
    choices = [
        Choice(value=i, name=f"[{rtype}] {name}", enabled=True)
        for i, (rtype, name, _) in enumerate(items)
    ]
    selected_indices = inquirer.checkbox(
        message=(
            "Select resources to migrate (Space to toggle, Enter to confirm):"
        ),
        choices=choices,
        validate=lambda result: (
            len(result) > 0 or "Select at least one resource"
        ),
        style=PROMPT_STYLE,
        instruction="(default: all selected)",
    ).execute()
    return [items[i] for i in selected_indices]
