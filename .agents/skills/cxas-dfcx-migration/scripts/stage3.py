#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Stage 3: parent-child topology wiring for consolidated CXAS agents.

Thin shell over :meth:`MigrationService.run_stage3`. Loads the IR bundle
written by :mod:`stage1` (which must have run consolidation —
``bundle.grouping`` is required) and delegates the wiring to the
service method.

Two modes:

  --hub-and-spoke (default)
    Root has every non-root group as a direct child; non-root groups
    have no children. Peer transfers route via root. Always cycle-free.

  --preserve-hierarchy
    Derive children from the source DFCX dep graph with cycle breaking.
    Use only when the source has a real hierarchy worth preserving.
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

from cxas_scrapi.migration import ir_bundle, phase_tracker
from cxas_scrapi.migration.service import MigrationService

logger = logging.getLogger(__name__)
console = Console()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Stage 3: rewire consolidated agent parent-child topology. "
            "Idempotent."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--ir-bundle", help="Path to <target>_ir.json")
    src.add_argument(
        "--target-name", help="Resolves to <target>_ir.json in cwd"
    )
    p.add_argument("--project-id", help="Override bundle project ID")
    p.add_argument("--location", help="Override bundle location")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--hub-and-spoke",
        dest="mode",
        action="store_const",
        const="hub",
        help=("(default) Root has every non-root group as a direct child."),
    )
    mode.add_argument(
        "--preserve-hierarchy",
        dest="mode",
        action="store_const",
        const="hierarchy",
        help="Derive children from the source DFCX dep graph.",
    )
    p.set_defaults(mode="hub")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the proposed parent → children mapping without applying.",
    )
    p.add_argument(
        "--no-set-root",
        action="store_true",
        help="Skip resetting the app's root_agent.",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Non-interactive.")
    return p


def _resolve_bundle_path(args) -> str:
    if args.ir_bundle:
        return args.ir_bundle
    path = ir_bundle.find_default_bundle(args.target_name)
    if not path:
        console.print(
            "[red]No IR bundle found.[/] Run migrate.py + stage1.py first."
        )
        sys.exit(1)
    return path


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

    if not bundle.grouping:
        console.print(
            "[red]Bundle has no `grouping`.[/] Stage 3 only runs after "
            "Stage 1 consolidation. If you ran stage1 with "
            "--no-consolidate, the original 1:1 topology is still in "
            "effect and Stage 3 isn't needed."
        )
        sys.exit(1)

    service = MigrationService.restore_from_bundle(
        bundle,
        project_id=args.project_id,
        location=args.location,
    )

    mode_label = (
        "hub-and-spoke (root has all groups as direct children)"
        if args.mode == "hub"
        else "preserve-hierarchy (source dep graph, cycles broken)"
    )
    with tracker.phase("Stage 3 — apply topology", mode_label):
        updated, skipped, failed = await service.run_stage3(
            bundle=bundle,
            mode=args.mode,
            set_root=not args.no_set_root,
            dry_run=args.dry_run,
            persist_bundle_path=(None if args.dry_run else bundle_path),
        )

    console.print()
    console.print(tracker.summary_table())
    console.print("\n[bold green]Stage 3 complete.[/]")
    console.print(f"  • updated={updated}, skipped={skipped}, failed={failed}")


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
