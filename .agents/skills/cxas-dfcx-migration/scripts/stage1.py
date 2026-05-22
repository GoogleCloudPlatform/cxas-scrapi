#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Stage 1: CXASOptimizer variable dedup + optional Gemini consolidation.

Thin shell over :meth:`MigrationService.run_stage1`. Loads the IR bundle
written by :mod:`migrate`, restores a :class:`MigrationService` from it,
then delegates everything (dedup, consolidator, integrity checks,
topology link, orphan cleanup, version checkpoint, bundle persist) to
the service method.

This script's only skill-specific responsibility is wiring the
interactive grouping review TUI (`cxas_scrapi.migration.grouping_review`) into
the service's ``grouping_callback``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prompts  # noqa: E402
import _shared  # noqa: E402

from cxas_scrapi.migration import grouping_review, ir_bundle, phase_tracker
from cxas_scrapi.migration.service import MigrationService
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)
console = Console()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Stage 1: variable dedup + optional Gemini consolidation. "
            "Loads <target>_ir.json."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--ir-bundle",
        help="Path to <target>_ir.json (defaults to newest in cwd)",
    )
    src.add_argument(
        "--target-name",
        help="Resolves to <target>_ir.json in the cwd",
    )
    p.add_argument("--project-id", help="Override bundle project ID")
    p.add_argument("--location", help="Override bundle location")

    p.add_argument(
        "--no-consolidate",
        action="store_true",
        help="Skip Gemini consolidation; only run CXASOptimizer Stage 1.",
    )
    p.add_argument(
        "--gemini-model",
        default="gemini-3.1-pro-preview",
        help=(
            "Model for the grouping proposal (default: gemini-3.1-pro-preview)"
        ),
    )
    p.add_argument(
        "--grouping-json",
        default=None,
        help="Load a previously persisted grouping instead of asking Gemini.",
    )
    p.add_argument(
        "--on-integrity-fail",
        choices=["abort", "warn", "ignore"],
        default="abort",
        help="What to do if pre-deploy integrity checks find blockers.",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Non-interactive.")
    return p


def _resolve_bundle_path(args) -> str:
    if args.ir_bundle:
        return args.ir_bundle
    path = ir_bundle.find_default_bundle(args.target_name)
    if not path:
        console.print(
            "[red]No IR bundle found.[/] Run migrate.py first, or pass "
            "--ir-bundle / --target-name."
        )
        sys.exit(1)
    return path


def _make_grouping_callback(yes: bool):
    """Build the grouping_callback for `MigrationService.run_stage1`.

    Non-interactive (``--yes``) callers get auto-accept — the proposed
    groupings are returned unchanged. Interactive callers get the
    accept/re-propose/merge/split/rename TUI from
    :mod:`cxas_scrapi.migration.grouping_review`.
    """
    if yes:
        return None  # service auto-accepts when callback is None

    async def cb(*, ir, groupings, consolidator, root_key, dep_summary):
        return await grouping_review.interactive_review(
            ir,
            groupings,
            consolidator,
            root_key=root_key,
            dep_summary=dep_summary,
            console=console,
        )

    return cb


async def _run(args) -> None:
    tracker = phase_tracker.PhaseTracker(console)

    if not _shared.auth_check(console):
        if not args.yes and not _prompts.prompt_yes_no(
            "Proceed anyway?", default=False
        ):
            sys.exit(1)

    bundle_path = _resolve_bundle_path(args)
    console.print(f"[cyan]Loading IR bundle:[/] {bundle_path}")
    bundle = ir_bundle.load(bundle_path)
    target_name = bundle.config.target_name

    service = MigrationService.restore_from_bundle(
        bundle,
        project_id=args.project_id,
        location=args.location,
    )

    consolidate = not args.no_consolidate
    gemini = None
    if consolidate and args.gemini_model:
        # Honor --gemini-model by constructing a client up front; service
        # would otherwise use its default model.
        gemini = GeminiGenerate(
            project_id=service.project_id,
            location="global",
            model_name=args.gemini_model,
            max_concurrent_requests=10,
        )

    with tracker.phase(
        "Stage 1",
        "variable dedup" + (" + Gemini consolidation" if consolidate else ""),
    ):
        await service.run_stage1(
            consolidate=consolidate,
            bundle=bundle if consolidate else None,
            gemini_client=gemini,
            grouping_callback=_make_grouping_callback(args.yes),
            grouping_json_path=args.grouping_json,
            on_integrity_fail=args.on_integrity_fail,
            version_label="0.0.1",
            persist_bundle_path=bundle_path,
            console=console,
        )

    console.print()
    console.print(tracker.summary_table())
    console.print("\n[bold green]Stage 1 complete.[/]")
    console.print(f"  • IR bundle:        {bundle_path}")
    if bundle.grouping:
        console.print(f"  • Grouping JSON:    {target_name}_grouping.json")
    if bundle.app_url:
        console.print(f"  • App console:      {bundle.app_url}")
    console.print(
        f"\n[dim]Next:[/] [cyan]stage2.py --target-name {target_name}[/]"
        " for instruction state machines + tool mocks + lint + report."
    )


def main() -> None:
    logging.basicConfig(
        level="INFO",
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    args = _build_parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
