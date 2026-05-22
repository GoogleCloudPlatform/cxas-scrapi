# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Thin async wrappers around CXASOptimizer so the skill scripts can drive
Stage 1 (variable dedup) and Stage 2 (instruction state machines + tool mocks)
independently and surface the resulting logs to the user."""

from __future__ import annotations

import logging

from rich.console import Console

from cxas_scrapi.migration.data_models import MigrationIR, MigrationStatus
from cxas_scrapi.migration.optimizer import CXASOptimizer
from cxas_scrapi.migration.service import MigrationService
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


def _print_logs(optimizer: CXASOptimizer, label: str, console: Console) -> None:
    if not optimizer.optimization_logs:
        console.print(f"[dim]{label}: no log entries recorded.[/]")
        return
    console.print(f"\n[bold]{label} log:[/]")
    for entry in optimizer.optimization_logs:
        stage = entry.get("stage", "?")
        action = entry.get("action", "?")
        details = entry.get("details", "")
        console.print(f"  • [{stage}] [cyan]{action}[/]: {details}")


async def run_stage1(
    ir: MigrationIR,
    gemini_client: GeminiGenerate,
    console: Console,
) -> CXASOptimizer:
    """Run CXASOptimizer Stage 1 (global variable dedup) and print a summary.

    Mutates ir.parameters and ir.agents/ir.tools content in place.
    """
    console.print(
        "\n[bold cyan]CXASOptimizer Stage 1: global variable deduplication…[/]"
    )
    optimizer = CXASOptimizer(ir, gemini_client)
    before = len(ir.parameters)
    await optimizer.optimize_stage1()
    after = len(ir.parameters)
    console.print(f"[green]Stage 1 complete:[/] parameters {before} → {after}")
    _print_logs(optimizer, "Stage 1", console)
    return optimizer


async def run_stage2(
    ir: MigrationIR,
    gemini_client: GeminiGenerate,
    console: Console,
) -> CXASOptimizer:
    """Run CXASOptimizer Stage 2 (instruction state machines + tool mocks)
    on the supplied IR. Caller must redeploy the IR afterwards.
    """
    console.print(
        "\n[bold cyan]CXASOptimizer Stage 2: instruction state machines + "
        "tool mocks…[/]"
    )
    optimizer = CXASOptimizer(ir, gemini_client)
    await optimizer.optimize_stage2()
    console.print("[green]Stage 2 complete.[/]")
    _print_logs(optimizer, "Stage 2", console)
    return optimizer


def merge_optimizer_logs_into_ir(
    ir: MigrationIR, optimizer: CXASOptimizer, stage_label: str
) -> None:
    """Stash the optimizer's per-stage logs onto the IR so downstream
    consumers (e.g. the OptimizationReporter) can find them."""
    if optimizer is None or not optimizer.optimization_logs:
        return
    ir.optimization_logs.setdefault("stages", {})
    ir.optimization_logs["stages"][stage_label] = list(
        optimizer.optimization_logs
    )


async def run_stage_with_redeploy(
    service: MigrationService,
    stage: int,
    console: Console,
) -> CXASOptimizer:
    """Run a single CXASOptimizer stage and push the resulting IR changes
    via update-pass deploys. Used by stage1.py / stage2.py.

    Steps:
      1. Mark every IR agent COMPILED so the update-pass deploy knows to push.
      2. Run optimize_stageN.
      3. Call _deploy_base_resources(is_update_pass=True).
      4. Call _deploy_pending_agents(is_update_pass=True).

    Returns the optimizer instance so the caller can read optimization_logs.
    """
    if stage not in (1, 2):
        raise ValueError(f"stage must be 1 or 2, got {stage}")

    for agent in service.ir.agents.values():
        agent.status = MigrationStatus.COMPILED

    if stage == 1:
        optimizer = await run_stage1(service.ir, service.gemini_client, console)
    else:
        optimizer = await run_stage2(service.ir, service.gemini_client, console)

    console.print(f"\n[cyan]Pushing Stage {stage} changes to CXAS…[/]")
    try:
        await service._deploy_base_resources(is_update_pass=True)
        await service._deploy_pending_agents(is_update_pass=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("Stage %d redeploy failed: %s", stage, exc)
        console.print(f"[red]Stage {stage} redeploy failed: {exc}[/]")
        raise

    return optimizer
