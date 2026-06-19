#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Stage 3: parent-child topology wiring for consolidated CXAS agents.

Thin shell over :meth:`MigrationService.run_stage_3`. Loads the IR bundle
written by :mod:`stage_1` (which must have run consolidation —
``bundle.grouping`` is required) and delegates the wiring to the
service method.

Supported architectures layout style:

  --architecture {hub-and-spoke,original-hierarchy}
    hub-and-spoke: (Default) Root has every non-root group as a direct child;
      non-root groups have no children. Always cycle-free.
    original-hierarchy: Derive children from the source DFCX dependency graph
      with smart cycle breaking.
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

from cxas_scrapi.migration import phase_tracker
from cxas_scrapi.migration.data_models import IRBundle
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
    p.add_argument(
        "--architecture",
        choices=["hub-and-spoke", "original-hierarchy"],
        default="hub-and-spoke",
        help=(
            "Spoke-Hub architecture style mapping to compile child routing "
            "(Default: 'hub-and-spoke')."
        ),
    )
    p.add_argument("--yes", "-y", action="store_true", help="Non-interactive.")
    return p


def _resolve_bundle_path(args) -> str:
    if args.ir_bundle:
        return args.ir_bundle
    path = IRBundle.find_default_bundle(args.target_name)
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
    bundle = IRBundle.load(bundle_path)

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

    mode = (
        "hub"
        if getattr(args, "architecture", "hub-and-spoke") == "hub-and-spoke"
        else "hierarchy"
    )
    mode_label = f"Architecture style: {args.architecture}"

    with tracker.phase("Stage 3 — apply topology", mode_label):
        updated, skipped, failed = await service.run_stage_3(
            bundle=bundle,
            mode=mode,
            version_label="0.0.5",
            persist_bundle_path=bundle_path,
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
