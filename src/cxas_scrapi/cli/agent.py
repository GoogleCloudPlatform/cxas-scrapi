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

"""CLI subcommands for managing CXAS agents."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from rich.console import Console

from cxas_scrapi.utils.agent_yaml import (
    find_agent_json,
    find_definition_yaml,
    guided_agent_to_yaml,
    is_guided_agent,
    is_guided_agent_synced,
    yaml_to_guided_agent,
)

logger = logging.getLogger(__name__)
console = Console()


def agent_sync_yaml(args: argparse.Namespace) -> None:
    """Handles the 'cxas agent sync-yaml' command.

    Synchronizes definition.yaml and agent.json manifests for Guided Agents.
    Supports --agent-dir, --dir, --to-json, --to-yaml, and --check flags.
    """
    to_json = getattr(args, "to_json", False)
    to_yaml = getattr(args, "to_yaml", False)
    check = getattr(args, "check", False)

    if to_json and to_yaml:
        console.print(
            "[red]Error: Cannot specify both --to-json and --to-yaml.[/red]"
        )
        sys.exit(1)

    if check and (to_json or to_yaml):
        console.print(
            "[red]Error: Cannot specify --check with --to-json or --to-yaml.[/red]"
        )
        sys.exit(1)

    target_agent_dirs: list[Path] = []
    agent_dir_arg = getattr(args, "agent_dir", None)

    if agent_dir_arg:
        p = Path(agent_dir_arg)
        if not p.exists():
            console.print(f"[red]Error: Agent directory not found: {p}[/red]")
            sys.exit(1)
        if p.is_file():
            p = p.parent
        target_agent_dirs = [p]
    else:
        base_dir_str = (
            getattr(args, "dir", None) or getattr(args, "app_dir", None) or "."
        )
        base_dir = Path(base_dir_str)
        if not base_dir.exists():
            console.print(f"[red]Error: Directory not found: {base_dir}[/red]")
            sys.exit(1)

        agents_dir = base_dir / "agents"
        if agents_dir.is_dir():
            target_agent_dirs = [
                p for p in sorted(agents_dir.iterdir()) if p.is_dir()
            ]
        elif (base_dir / "agent.json").is_file() or (
            base_dir / "definition.yaml"
        ).is_file():
            target_agent_dirs = [base_dir]

    if not target_agent_dirs:
        console.print("[yellow]No agent directories found to sync.[/yellow]")
        return

    drift_detected = False

    for agent_dir in target_agent_dirs:
        agent_name = agent_dir.name
        is_ga = is_guided_agent(agent_dir)
        has_yaml = find_definition_yaml(agent_dir) is not None
        has_json = find_agent_json(agent_dir) is not None

        # If user explicitly specified --agent-dir, but it's not a guided agent:
        if agent_dir_arg and not is_ga and not has_yaml:
            console.print(
                f"[yellow]Agent at '{agent_dir}' is not a Guided Agent.[/yellow]"
            )
            if check:
                drift_detected = True
            continue

        # When scanning multiple agents in an app directory, skip non-guided agents
        if not agent_dir_arg and not is_ga and not has_yaml:
            console.print(
                f"[dim]- {agent_name}: skipped (not a Guided Agent)[/dim]"
            )
            continue

        if check:
            synced, diff_desc = is_guided_agent_synced(agent_dir)
            if synced:
                console.print(f"[green]✓ {agent_name}: in sync[/green]")
            else:
                drift_detected = True
                console.print(f"[red]✗ {agent_name}: drift detected[/red]")
                if diff_desc:
                    console.print(diff_desc)
        elif to_json:
            if not has_yaml:
                console.print(
                    f"[yellow]! {agent_name}: missing definition.yaml to compile[/yellow]"
                )
                continue
            try:
                yaml_to_guided_agent(agent_dir)
                console.print(
                    f"[green]✓ Compiled definition.yaml -> agent.json for {agent_name}[/green]"
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to compile {agent_name}: {e}[/red]"
                )
                sys.exit(1)
        elif to_yaml:
            if not has_json and not is_ga:
                console.print(
                    f"[yellow]! {agent_name}: missing agent.json to extract[/yellow]"
                )
                continue
            try:
                guided_agent_to_yaml(agent_dir)
                console.print(
                    f"[green]✓ Extracted agent.json -> definition.yaml for {agent_name}[/green]"
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to extract {agent_name}: {e}[/red]"
                )
                sys.exit(1)
        # Default behavior:
        # If definition.yaml exists, compile definition.yaml -> agent.json (YAML is source of truth)
        # Else if only agent.json exists and is guided agent, extract to definition.yaml
        elif has_yaml:
            try:
                yaml_to_guided_agent(agent_dir)
                console.print(
                    f"[green]✓ Compiled definition.yaml -> agent.json for {agent_name}[/green]"
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to compile {agent_name}: {e}[/red]"
                )
                sys.exit(1)
        elif is_ga:
            try:
                guided_agent_to_yaml(agent_dir)
                console.print(
                    f"[green]✓ Extracted agent.json -> definition.yaml for {agent_name}[/green]"
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to extract {agent_name}: {e}[/red]"
                )
                sys.exit(1)

    if check and drift_detected:
        sys.exit(1)
