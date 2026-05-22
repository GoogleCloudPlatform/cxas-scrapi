# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Shared helpers for the cxas-dfcx-migration skill scripts.

The skill's stage scripts (`migrate.py` / `stage1.py` / `stage2.py` /
`stage3.py`) collapsed to thin shells over the `MigrationService`
methods. What remains here is the prompt + auth glue that's specific
to the skill's InquirerPy UX:

* :func:`auth_check`                — gcloud + DFCX client init
  (delegates to :meth:`MigrationCLI.check_auth`)
* :func:`prompt_project_and_location` — InquirerPy variant of the
  project + location prompts (default location is ``us``, matching
  ``MigrationCLI.run``)
* :func:`load_source_agent_inquirer` — InquirerPy variant of the source
  loader (Agent ID vs Zip File)
* :func:`collect_migration_inputs`  — InquirerPy variant of
  :meth:`MigrationCLI.compose_config` (asks for location too)
* :func:`select_resources` / :func:`run_dependency_analysis` /
  :func:`show_visualizations` — pure delegations to the matching
  :class:`MigrationCLI` methods so the skill's `migrate.py` can reuse
  the existing rich.Prompt-based pickers without forking them.
"""

from __future__ import annotations

import functools
import os
import sys
from datetime import datetime
from typing import Any

import _prompts  # InquirerPy-backed prompts (skill-local module)
from rich.console import Console

from cxas_scrapi.cli.migration_cli import MigrationCLI
from cxas_scrapi.migration.config import AGENT_MODELS, DEFAULT_MODEL
from cxas_scrapi.migration.data_models import DFCXAgentIR
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.dfcx_exporter import ConversationalAgentsAPI


@functools.lru_cache(maxsize=1)
def _cli() -> MigrationCLI:
    """Lazy-instantiate MigrationCLI once per process — used as the
    backend for the delegation helpers below.

    ``MigrationCLI.__init__`` calls ``logging.basicConfig`` which is
    idempotent after the first call, so this is safe even when the
    skill scripts have already set up logging.
    """
    return MigrationCLI()


# ---------------------------------------------------------------------------
# Delegation helpers (forward to MigrationCLI methods)
# ---------------------------------------------------------------------------


def auth_check(console: Console) -> bool:
    """Delegate to :meth:`MigrationCLI.check_auth`."""
    cli = _cli()
    cli.console = console
    return cli.check_auth()


def run_dependency_analysis(
    full_data: DFCXAgentIR,
    filtered_data: DFCXAgentIR,
    console: Console,
) -> tuple[DependencyAnalyzer, list[str], list[str]]:
    """Delegate the printing to
    :meth:`MigrationCLI.run_dependency_analysis`, then also build and
    return the analyzer + outgoing/incoming impact lists so downstream
    callers don't have to recompute them."""
    cli = _cli()
    cli.console = console
    cli.run_dependency_analysis(full_data, filtered_data)

    analyzer = DependencyAnalyzer(full_data)
    selected_ids: list[str] = [pb.get("name") for pb in filtered_data.playbooks]
    selected_ids += [f.flow_data.get("name") for f in filtered_data.flows]
    outgoing, incoming = analyzer.get_impact(selected_ids)
    return analyzer, outgoing, incoming


def select_resources(agent_data: DFCXAgentIR, console: Console) -> DFCXAgentIR:
    """Delegate to :meth:`MigrationCLI.select_resources`."""
    cli = _cli()
    cli.console = console
    return cli.select_resources(agent_data)


def show_visualizations(prefix: str, console: Console) -> None:
    """Delegate to :meth:`MigrationCLI.show_visualizations` — prints the
    SVG / markdown paths that ``MainVisualizer.export_visualizations``
    just wrote."""
    cli = _cli()
    cli.console = console
    cli.show_visualizations(prefix)


# ---------------------------------------------------------------------------
# InquirerPy-based skill-local prompts
# ---------------------------------------------------------------------------


def prompt_project_and_location(args, console: Console) -> tuple[str, str]:
    """Ask for project_id + location upfront. CLI flags
    (``--project-id``, ``--location``) are honored as defaults /
    overrides. Default location is ``us`` (matches
    :meth:`MigrationCLI.run`)."""
    project_id = getattr(args, "project_id", None)
    location = getattr(args, "location", None) or _prompts.DEFAULT_LOCATION

    if not project_id:
        project_id = _prompts.prompt_project_id()
    if not getattr(args, "location", None) and _prompts.is_interactive():
        location = _prompts.prompt_location(default=location)

    console.print(
        f"[dim]Using[/] [cyan]project[/]=[bold]{project_id}[/] "
        f"[cyan]location[/]=[bold]{location}[/]"
    )
    return project_id, location


def load_source_agent_inquirer(
    args, console: Console
) -> tuple[DFCXAgentIR, str, ConversationalAgentsAPI]:
    """InquirerPy variant of the source loader. Honors
    ``--source-agent-id`` / ``--zip-file`` CLI flags as overrides;
    otherwise prompts."""
    cx_api = ConversationalAgentsAPI()
    zip_path = getattr(args, "zip_file", None)
    agent_id = getattr(args, "source_agent_id", None)

    if not zip_path and not agent_id:
        mode = _prompts.prompt_source_load_mode()
        if mode == "ID":
            agent_id = _prompts.prompt_source_agent_id()
        else:
            zip_path = _prompts.prompt_zip_path()

    if zip_path:
        path = os.path.expanduser(zip_path)
        console.print(f"Loading agent from zip: {path}")
        with open(path, "rb") as f:
            agent_data = cx_api.process_local_agent_zip(f.read())
        agent_id = "uploaded-agent"
    else:
        console.print(f"Loading Agent ID: {agent_id}")
        agent_data = cx_api.fetch_full_agent_details(agent_id, use_export=True)

    if not agent_data:
        console.print("[red]Failed to load source agent.[/]")
        sys.exit(1)

    console.print("[green]Agent data loaded successfully.[/]")
    return agent_data, agent_id, cx_api


def collect_migration_inputs(
    args,
    console: Console,
    *,
    default_target_prefix: str = "migrated_agent",
) -> dict[str, Any]:
    """InquirerPy variant of :meth:`MigrationCLI.compose_config` that
    also asks for location and logic version. Honors CLI overrides.
    Returns ``project_id`` / ``location`` / ``target_name`` / ``env`` /
    ``model`` / ``migration_version``."""
    default_target = (
        f"{default_target_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    project_id, location = prompt_project_and_location(args, console)

    target_name = getattr(args, "target_name", None) or (
        _prompts.prompt_target_name(default_target)
        if _prompts.is_interactive()
        else default_target
    )
    env = getattr(args, "env", None) or (
        _prompts.prompt_env() if _prompts.is_interactive() else "PROD"
    )
    model = getattr(args, "model", None) or (
        _prompts.prompt_model(AGENT_MODELS, DEFAULT_MODEL)
        if _prompts.is_interactive()
        else DEFAULT_MODEL
    )
    migration_version = getattr(args, "migration_version", None) or (
        _prompts.prompt_logic_version() if _prompts.is_interactive() else "2.0"
    )
    return {
        "project_id": project_id,
        "location": location,
        "target_name": target_name,
        "env": env,
        "model": model,
        "migration_version": migration_version,
    }
