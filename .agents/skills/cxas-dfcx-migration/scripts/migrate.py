#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Pure 1:1 DFCX → CXAS migration.

This script is the first of three in the cxas-dfcx-migration skill:

  migrate.py        — 1:1 conversion of every selected playbook/flow.
  stage1.py         — variable dedup + optional Gemini consolidation.
  stage2.py         — instruction state machines + tool mocks + lint + report.

Each script writes its updated IR to <target>_ir.json so the next stage can
resume without re-running the expensive per-flow Step 2A/2B/2C compile.

Mirrors the canonical flow in src/cxas_scrapi/cli/migration_cli.py:
  auth → load source → config → resource select → dep analysis → review → run.

Replaces rich.Prompt with InquirerPy and asks project + location upfront
(default location is `us`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

# Skill-local helpers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prompts  # noqa: E402
import _shared  # noqa: E402

from cxas_scrapi.migration import html_preview, phase_tracker
from cxas_scrapi.migration.config import AGENT_MODELS
from cxas_scrapi.migration.data_models import IRBundle, MigrationConfig
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.eval_generator import DeterministicEvalGenerator
from cxas_scrapi.migration.main_visualizer import MainVisualizer
from cxas_scrapi.migration.service import MigrationService

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "1:1 DFCX → CXAS migration. Writes <target>_ir.json for "
            "stage1.py / stage2.py."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--source-agent-id",
        help=(
            "Full DFCX resource name: projects/<p>/locations/<l>/agents/<uuid>"
        ),
    )
    src.add_argument("--zip-file", help="Path to local DFCX export .zip")

    p.add_argument(
        "--project-id", help="Target GCP project ID (prompted if omitted)"
    )
    p.add_argument(
        "--location",
        default=None,
        help="Target CXAS location (prompted if omitted; default 'us')",
    )
    p.add_argument("--target-name", help="Display name for the new CXAS app")
    p.add_argument("--env", choices=["PROD", "AUTOPUSH"], default=None)
    p.add_argument("--model", choices=AGENT_MODELS, default=None)
    p.add_argument("--migration-version", choices=["1.0", "2.0"], default=None)

    p.add_argument(
        "--gen-report",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate migration report (default: yes)",
    )
    p.add_argument(
        "--gen-unit-tests",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Generate <target>_unit_tests.json from the deployed IR "
            "(default: yes)"
        ),
    )
    p.add_argument(
        "--no-preview-html",
        action="store_true",
        help="Skip the pre-migration HTML tree preview",
    )
    p.add_argument(
        "--preview-only",
        action="store_true",
        help="Generate the HTML tree preview and exit (no migration)",
    )
    p.add_argument(
        "--export-svg",
        action="store_true",
        help=(
            "Also call MainVisualizer.export_visualizations for "
            "SVG/markdown topology"
        ),
    )
    p.add_argument(
        "--skip-resource-selection",
        action="store_true",
        help="Skip the multi-select resource picker (migrate everything).",
    )
    p.add_argument(
        "--skip-dependency-analysis",
        action="store_true",
        help="Skip the dependency analysis step.",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive; accept all defaults.",
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run(args) -> None:
    tracker = phase_tracker.PhaseTracker(console)

    # Phase 0: auth
    if not _shared.auth_check(console):
        if not args.yes and not _prompts.prompt_yes_no(
            "Proceed anyway? (will likely fail)", default=False
        ):
            sys.exit(1)

    # Phase 1: project + location + source loading
    with tracker.phase("Source load", "fetch + parse DFCX agent"):
        agent_data, agent_id, _cx_api = _shared.load_source_agent_inquirer(
            args, console
        )

    inputs = _shared.collect_migration_inputs(args, console)

    # Phase 2: pre-flight HTML preview
    if not args.no_preview_html:
        with tracker.phase("Preview HTML", "topology + per-resource trees"):
            try:
                analyzer_pre = DependencyAnalyzer(agent_data)
                preview_path = html_preview.generate_html_report(
                    agent_data,
                    analyzer_pre,
                    output_path=f"{inputs['target_name']}_tree_preview.html",
                )
                stats = html_preview.collect_stats(agent_data, analyzer_pre)
                est = stats["estimated_minutes"]
                console.print(
                    f"[bold green]Preview ready:[/] {preview_path}\n"
                    f"  • {stats['playbook_count']} playbooks, "
                    f"{stats['flow_count']} flows, "
                    f"{stats['tool_count']} tools, "
                    f"{stats['routing_edge_count']} routing edges\n"
                    f"  • Estimated 1:1 migration time: ~{est} min"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Preview HTML generation failed: %s", exc)

    if args.preview_only:
        console.print(
            "\n[yellow]--preview-only set; exiting before migration.[/]"
        )
        return

    # Phase 3: optional resource selection (delegates to MigrationCLI)
    filtered_data = agent_data
    if not args.skip_resource_selection:
        filtered_data = _shared.select_resources(agent_data, console)

    # Phase 4: optional dependency analysis (delegates to MigrationCLI)
    if not args.skip_dependency_analysis:
        _shared.run_dependency_analysis(agent_data, filtered_data, console)

    # Phase 5: build config
    config = MigrationConfig(
        project_id=inputs["project_id"],
        target_name=inputs["target_name"],
        env=inputs["env"],
        model=inputs["model"],
        gen_report=args.gen_report,
        gen_unit_tests=args.gen_unit_tests,
        gen_hillclimbing_evals=False,
        eval_runner_target="Custom API Runner",
        migration_version=inputs["migration_version"],
        # Stage 1 / Stage 2 are separate scripts; never inline-optimize here.
        optimize_for_cxas=False,
        source_agent_data_override=filtered_data,
    )

    if args.export_svg:
        try:
            MainVisualizer(filtered_data).export_visualizations(
                inputs["target_name"]
            )
            _shared.show_visualizations(inputs["target_name"], console)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SVG export failed: %s", exc)

    # Phase 6: review + confirm
    if not args.yes:
        pb_count = len(filtered_data.playbooks)
        flow_count = len(filtered_data.flows)
        console.print(
            "\n[bold blue]=== Review ===[/]\n"
            f"Target Agent:    {config.target_name}\n"
            f"Project:         {config.project_id}\n"
            f"Location:        {inputs['location']}\n"
            f"Environment:     {config.env}\n"
            f"Model:           {config.model}\n"
            f"Logic Version:   {config.migration_version}\n"
            f"Selected:        {pb_count} playbooks, {flow_count} flows\n"
        )
        if not _prompts.prompt_yes_no("Start migration?", default=True):
            console.print("Aborted.")
            return

    # Phase 5: run migration
    service = MigrationService(
        project_id=inputs["project_id"],
        location=inputs["location"],
        default_model=inputs["model"],
    )

    with tracker.phase("Migration", "MigrationService.run_migration"):
        await service.run_migration(source_cx_agent_id=agent_id, config=config)

    # Phase 6: persist IR bundle. service.persist_bundle handles the
    # IR snapshot + stage_history append + atomic file write.
    bundle = IRBundle(
        config=config,
        source_agent_data=agent_data,
        ir=service.ir,
        app_url=(
            f"https://ces.cloud.google.com/projects/{config.project_id}"
            f"/locations/{inputs['location']}/apps/{service.ir.metadata.app_id}"
            if service.ir.metadata.app_id
            else None
        ),
    )
    bundle_path = service.persist_bundle(
        bundle,
        f"{inputs['target_name']}_ir.json",
        phase="migrate",
        status="ok",
        notes=f"{len(service.ir.agents)} agents",
    )

    # Phase 7: deterministic unit tests
    test_path = ""
    if args.gen_unit_tests:
        with tracker.phase("Unit tests", "DeterministicEvalGenerator"):
            try:
                gen = DeterministicEvalGenerator(service.ir)
                by_agent: dict[str, list] = {}
                for agent_name in service.ir.agents:
                    cases = gen.generate_tests_for_agent(agent_name)
                    if cases:
                        by_agent[agent_name] = [
                            tc.model_dump(mode="json") for tc in cases
                        ]
                test_path = f"{inputs['target_name']}_unit_tests.json"
                with open(test_path, "w") as f:
                    json.dump(by_agent, f, indent=2, default=str)
                total = sum(len(v) for v in by_agent.values())
                console.print(
                    f"[green]Wrote {total} deterministic tests for "
                    f"{len(by_agent)} agents → {test_path}[/]"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unit test generation failed: %s", exc)

    # Phase 8: final summary
    console.print()
    console.print(tracker.summary_table())
    console.print("\n[bold green]Migration complete.[/]")
    console.print(f"  • IR bundle:        {bundle_path}")
    if config.gen_report:
        console.print(
            f"  • Migration report: {config.target_name}_migration_report.md"
        )
    if test_path:
        console.print(f"  • Unit tests:       {test_path}")
    if bundle.app_url:
        console.print(f"  • App console:      {bundle.app_url}")
    console.print(
        "\n[dim]Next:[/] [cyan]stage_1.py --target-name "
        f"{inputs['target_name']}[/] for variable dedup + consolidation."
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
