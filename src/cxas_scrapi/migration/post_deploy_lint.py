# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Post-deploy `cxas lint` runner. Pulls the deployed app to a temp directory
and lints it. Returns (passed, captured_output) for the reporter."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from rich.console import Console

from cxas_scrapi.migration.service import MigrationService

logger = logging.getLogger(__name__)


def _find_app_dir(temp_dir: str) -> str | None:
    """`cxas pull` lays out the app inside temp_dir. Find the first directory
    inside temp_dir and treat it as the app root."""
    for entry in os.listdir(temp_dir):
        candidate = os.path.join(temp_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return None


async def run_post_deploy_lint(
    service: MigrationService, console: Console
) -> tuple[bool, str]:
    """Pull the newly deployed app and run `cxas lint`.

    Returns (passed, captured_output_excerpt). On failure to pull, returns
    (False, error_message).
    """
    console.print("\n[bold cyan]Running post-deployment lint…[/]")
    app_resource = service.ir.metadata.app_resource_name
    if not app_resource:
        msg = "No app_resource_name on IR metadata; cannot lint."
        console.print(f"[yellow]{msg}[/]")
        return False, msg

    with tempfile.TemporaryDirectory(prefix="cxas_lint_") as temp_dir:
        console.print(f"  Pulling {app_resource} → {temp_dir}")
        pull = await asyncio.create_subprocess_exec(
            "cxas",
            "pull",
            app_resource,
            "--target-dir",
            temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, pull_err = await pull.communicate()
        if pull.returncode != 0:
            err = pull_err.decode(errors="replace")
            logger.error("cxas pull failed: %s", err)
            console.print(f"[red]cxas pull failed:[/]\n{err}")
            return False, err

        app_dir = _find_app_dir(temp_dir)
        if not app_dir:
            msg = f"No app directory found inside {temp_dir} after pull."
            console.print(f"[red]{msg}[/]")
            return False, msg

        console.print(f"  Linting {app_dir}")
        lint = await asyncio.create_subprocess_exec(
            "cxas",
            "lint",
            "--app-dir",
            app_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        lint_out, _ = await lint.communicate()
        output = lint_out.decode(errors="replace")
        console.print(output)

        if lint.returncode == 0:
            console.print("[bold green]Lint passed with 0 errors.[/]")
            return True, output
        console.print("[yellow]Lint reported issues — see output above.[/]")
        return False, output
