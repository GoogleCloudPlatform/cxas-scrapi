#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Stage 2: instruction state machines + tool mocks + lint + report.

Thin shell over :meth:`MigrationService.run_stage2`. Loads the IR bundle
written by :mod:`stage1`, restores a :class:`MigrationService`, then
delegates everything (CXASOptimizer Stage 2, redeploy, version
checkpoint, unit-test regen, post-deploy lint, OptimizationReporter
markdown, bundle persist) to the service method.
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
            "Stage 2: instruction state machines + tool mocks + lint + report."
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
        "--no-unit-tests",
        action="store_true",
        help="Skip deterministic unit test regeneration.",
    )
    p.add_argument(
        "--no-lint",
        action="store_true",
        help="Skip the post-deploy `cxas pull` + `cxas lint`.",
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="Skip the OptimizationReporter audit markdown.",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Non-interactive.")
    return p


def _resolve_bundle_path(args) -> str:
    if args.ir_bundle:
        return args.ir_bundle
    path = IRBundle.find_default_bundle(args.target_name)
    if not path:
        console.print(
            "[red]No IR bundle found.[/] Run migrate.py / stage1.py first."
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
    target_name = bundle.config.target_name

    service = MigrationService.restore_from_bundle(
        bundle,
        project_id=args.project_id,
        location=args.location,
    )

    unit_tests_path = (
        f"{target_name}_unit_tests.json" if not args.no_unit_tests else None
    )
    report_path = (
        f"{target_name}_optimization_report.md" if not args.no_report else None
    )

    with tracker.phase(
        "Stage 2",
        "instruction state machines + tool mocks + redeploy",
    ):
        await service.run_stage_2(
            version_label="0.0.4",
            generate_unit_tests=not args.no_unit_tests,
            unit_tests_path=unit_tests_path,
            run_lint=not args.no_lint,
            write_report_to=report_path,
            bundle=bundle,
            persist_bundle_path=bundle_path,
            console=console,
        )

    console.print()
    console.print(tracker.summary_table())
    console.print("\n[bold green]Stage 2 complete.[/]")
    console.print(f"  • IR bundle:        {bundle_path}")
    if unit_tests_path:
        console.print(f"  • Unit tests:       {unit_tests_path}")
    if report_path:
        console.print(f"  • Audit report:     {report_path}")
    if bundle.app_url:
        console.print(f"  • App console:      {bundle.app_url}")


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
