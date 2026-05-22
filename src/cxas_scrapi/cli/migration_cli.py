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

"""CLI for DFCX→CXAS migration.

Two entry points:

* :class:`MigrationCLI` — the interactive dashboard wired to
  ``cxas migrate dfcx``. Walks the user through project + target,
  resource selection, dependency analysis, review, then
  :meth:`MigrationService.run_migration`.
* :func:`register` — argparse subcommand tree for
  ``cxas migrate dfcx-cxas {run, stage1, stage2, stage3, resume}``.
  Non-interactive (scriptable) entry points around the same
  :class:`MigrationService` methods, plus stage-level resumability.
"""

import argparse
import asyncio
import glob
import logging
import os
import sys
from typing import Any

from google.cloud.dialogflowcx_v3beta1 import services as cx_services
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cxas_scrapi.migration import ir_bundle
from cxas_scrapi.migration.config import AGENT_MODELS, DEFAULT_MODEL
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    MigrationConfig,
    MigrationIR,
)
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.dfcx_exporter import ConversationalAgentsAPI
from cxas_scrapi.migration.ir_bundle import IRBundle
from cxas_scrapi.migration.main_visualizer import MainVisualizer
from cxas_scrapi.migration.service import MigrationService

logger = logging.getLogger(__name__)


class MigrationCLI:
    """Handles interactive CLI prompts and status reporting."""

    def __init__(self):
        self.console = Console()
        # Setup Rich logging

        logging.basicConfig(
            level="INFO",
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=self.console, rich_tracebacks=True)],
        )

    def check_auth(self) -> bool:
        """Checks if valid credentials are available."""
        self.console.print("[bold blue]Checking authentication...[/]")
        try:
            # Try to instantiate a client to trigger mTLS check
            cx_services.agents.AgentsClient()
            self.console.print("[green]✅ Authentication successful.[/]")
            return True
        except Exception as e:
            self.console.print("[red]❌ Authentication failed.[/]")
            self.console.print(f"[yellow]Error details:[/] {e}")
            self.console.print("\n[bold]To fix this, please ensure:[/]")
            self.console.print(
                "  1. You have run [cyan]gcloud auth application-default "
                "login[/]."
            )
            self.console.print(
                "  2. Your account has read access to the source DFCX project."
            )
            self.console.print(
                "  3. Your account has admin/editor access to the target "
                "CXAS project."
            )
            self.console.print(
                "  4. Set the [cyan]CXAS_OAUTH_TOKEN[/] environment "
                "variable if needed."
            )
            return False

    def compose_config(self, default_agent_name: str) -> MigrationConfig:
        """Prompt user for configuration and return a MigrationConfig object."""
        self.console.print("\n[bold blue]=== Migration Configuration ===[/]\n")

        project_id = Prompt.ask("Enter Google Cloud Project ID")

        target_name = Prompt.ask(
            "Enter Target Agent Name", default=default_agent_name
        )

        env = Prompt.ask(
            "Enter Environment", choices=["PROD", "AUTOPUSH"], default="PROD"
        )

        model = Prompt.ask(
            "Enter Global App Model",
            choices=AGENT_MODELS,
            default=DEFAULT_MODEL,
        )

        optimize_for_cxas = Confirm.ask("Optimize for CXAS?", default=True)

        # Opt-in extras (all default OFF for back-compat). Consolidation +
        # stage3 are nested under optimize_for_cxas because the consolidator
        # operates on the optimized IR; stage3 wires the consolidated agents.
        consolidate = optimize_for_cxas and Confirm.ask(
            "Run structural consolidation (Gemini N→M agent grouping)?",
            default=False,
        )
        run_stage3 = consolidate and Confirm.ask(
            "Run Stage 3 topology wiring (parent-child links)?",
            default=False,
        )
        persist_bundle = Confirm.ask(
            "Persist IR bundle for stage-resume?",
            default=False,
        )

        gen_report = Confirm.ask("Generate Migration Report?", default=True)
        gen_unit_tests = Confirm.ask(
            "Generate Unit Tests (Auto-Fix)? [yellow]*feature coming*[/]",
            default=True,
        )
        gen_hillclimbing_evals = Confirm.ask(
            "Generate Hillclimbing Evals? [yellow]*feature coming*[/]",
            default=False,
        )

        eval_runner_target = Prompt.ask(
            "Enter Eval Target [yellow]*feature coming*[/]",
            choices=["Custom API Runner", "Native Product Eval (Stub)"],
            default="Custom API Runner",
        )

        return MigrationConfig(
            project_id=project_id,
            target_name=target_name,
            env=env,
            model=model,
            gen_report=gen_report,
            gen_unit_tests=gen_unit_tests,
            gen_hillclimbing_evals=gen_hillclimbing_evals,
            eval_runner_target=eval_runner_target,
            migration_version="2.0",
            optimize_for_cxas=optimize_for_cxas,
            consolidate=consolidate,
            run_stage3=run_stage3,
            persist_bundle=persist_bundle,
        )

    def select_resources(self, agent_data: DFCXAgentIR) -> DFCXAgentIR:
        """Prompt user to select resources to migrate."""
        self.console.print("\n[bold blue]=== Resource Selection ===[/]\n")

        # Use Pydantic model directly
        data_dict = agent_data

        playbooks = data_dict.playbooks
        flows = data_dict.flows

        all_resources = []
        for pb in playbooks:
            all_resources.append(
                ("Playbook", pb.get("displayName", "Unnamed"), pb)
            )
        for flow in flows:
            f = flow.flow_data
            all_resources.append(
                ("Flow", f.get("displayName", "Unnamed"), flow)
            )

        if not all_resources:
            self.console.print("No playbooks or flows found in agent data.")
            return data_dict

        self.console.print("[bold]Available Resources:[/]")
        for i, (res_type, name, _) in enumerate(all_resources, 1):
            self.console.print(f"  {i}. [{res_type}] {name}")

        self.console.print("\nOptions:")
        self.console.print("  - Enter 'all' to start with ALL selected")
        self.console.print("  - Enter 'none' to start with NONE selected")
        self.console.print(
            "(you can specify to exclude/include specific resources by their "
            "numbers and ranges in next turn)"
        )

        mode = Prompt.ask("Your choice", choices=["all", "none"], default="all")

        if mode.lower() == "none":
            answer = Prompt.ask(
                "Enter comma-separated numbers or ranges to INCLUDE "
                "(e.g., 1,3 or 1-5) or Enter to finish",
                default="",
            )
            is_include = True
        else:
            answer = Prompt.ask(
                "Enter comma-separated numbers or ranges to EXCLUDE "
                "(e.g., 1,3 or 1-5) or Enter to finish",
                default="",
            )
            is_include = False

        if not answer:
            if is_include:
                filtered_data = data_dict.model_copy()
                filtered_data.playbooks = []
                filtered_data.flows = []
                return filtered_data
            else:
                return data_dict

        try:
            indices = []
            for part in answer.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    indices.extend(range(start, end + 1))
                else:
                    indices.append(int(part))

            indices = [i - 1 for i in indices]  # 0-based

            selected_playbooks = []
            selected_flows = []

            for i, (res_type, _name, data) in enumerate(all_resources):
                should_select = i in indices if is_include else i not in indices
                if should_select:
                    if res_type == "Playbook":
                        selected_playbooks.append(data)
                    elif res_type == "Flow":
                        selected_flows.append(data)

            filtered_data = data_dict.model_copy()
            filtered_data.playbooks = selected_playbooks
            filtered_data.flows = selected_flows
            return filtered_data

        except ValueError:
            self.console.print(
                "[red]Invalid input. Proceeding with default selection.[/]"
            )
            if is_include:
                filtered_data = data_dict.model_copy()
                filtered_data.playbooks = []
                filtered_data.flows = []
                return filtered_data
            else:
                return data_dict

    def run_dependency_analysis(
        self, agent_data: DFCXAgentIR, filtered_data: DFCXAgentIR
    ):
        """Run dependency analysis and show results."""
        self.console.print("\n[bold blue]=== Dependency Analysis ===[/]\n")

        analyzer = DependencyAnalyzer(agent_data)

        selected_ids = []
        for pb in filtered_data.playbooks:
            selected_ids.append(pb.get("name"))
        for flow in filtered_data.flows:
            f = flow.flow_data
            selected_ids.append(f.get("name"))

        outgoing, incoming = analyzer.get_impact(selected_ids)

        if outgoing:
            self.console.print("[yellow]⚠️ Missing Dependencies (Outgoing):[/]")
            self.console.print(
                " The selected resources reference these items, "
                "but they are NOT selected:"
            )
            for rid in outgoing:
                det = analyzer.get_details(rid)
                self.console.print(f"  - [{det['type']}] {det['name']}")
        else:
            self.console.print("[green]✅ No missing dependencies detected.[/]")

        if incoming:
            self.console.print("\n[cyan]ℹ️ Incoming References:[/]")
            self.console.print(
                " These unselected resources reference your selection:"
            )
            for rid in incoming:
                det = analyzer.get_details(rid)
                self.console.print(f"  - [{det['type']}] {det['name']}")

    def display_status(self, ir: MigrationIR):
        """Display the status of resources in the IR."""
        self.console.print("\n[bold blue]=== Migration Status ===[/]\n")

        table = Table(title="Resources Status")
        table.add_column("Type", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Status", style="green")

        for tool in ir.tools.values():
            table.add_row(tool.type, tool.id, tool.status.value)

        for agent in ir.agents.values():
            table.add_row(agent.type, agent.display_name, agent.status.value)

        self.console.print(table)

    def show_visualizations(self, prefix: str = "agent"):
        """Print links to visualizations."""
        self.console.print("\n[bold blue]=== Visualizations ===[/]\n")
        self.console.print(
            f"Topology graph exported to: [cyan]{prefix}_topology.svg[/]"
        )
        self.console.print(
            f"Detailed resources exported to: "
            f"[cyan]{prefix}_detailed_resources.md[/]"
        )
        self.console.print("Open the SVG file in a browser to view the graph.")

    def run(self, default_agent_name: str, cx_api: Any):
        """Runs the full interactive CLI dashboard."""
        self.console.print(
            "[bold green]Welcome to the CXAS Migration Tool![/bold green]"
        )

        if not self.check_auth():
            if not Confirm.ask(
                "Do you want to proceed anyway? (May fail later)", default=False
            ):
                return

        self.console.print(
            "This tool performs optimized best-practices DFCX to CXAS agents "
            "migration by extracting resources, analyzing inputs, converting "
            "and generating new instructions and tools, and deploying them.\n"
        )

        # 1. Load Source Agent
        choice = Prompt.ask(
            "Which source type to load the agent from",
            choices=["ID", "Zip File"],
            default="Zip File",
        )

        agent_data = None
        agent_id = "uploaded-agent"
        if choice == "ID":
            agent_id = Prompt.ask("Enter Source Agent ID")
            self.console.print(f"Loading Agent ID: {agent_id} ...")
            agent_data = cx_api.fetch_full_agent_details(
                agent_id, use_export=True
            )
        else:
            zip_path = Prompt.ask(
                "Enter path to local agent export (.zip)",
                default="~/Desktop/agent-examples/exported_agent_UAT-macys-conversational-chatbot-uat.zip",
            )
            zip_path = os.path.expanduser(zip_path)
            self.console.print(f"Loading agent from {zip_path}...")
            with open(zip_path, "rb") as f:
                content = f.read()
            agent_data = cx_api.process_local_agent_zip(content)

        if not agent_data:
            self.console.print("[red]Failed to load agent data.[/]")
            return

        self.console.print("[green]Agent data loaded successfully.[/]")

        while True:
            # 2. Configure
            config = self.compose_config(default_agent_name)

            # Initialize MigrationService with the provided project_id

            migration_service = MigrationService(
                project_id=config.project_id,
                location="us",
                default_model=config.model,
            )

            # 3. Select Resources
            filtered_data = self.select_resources(agent_data)

            # 4. Dependency Analysis
            if Confirm.ask("Run Dependency Analysis?", default=True):
                self.run_dependency_analysis(agent_data, filtered_data)

            # 5. Visualization
            if Confirm.ask(
                "Generate Visualizations (SVG & Markdown)?", default=True
            ):
                visualizer = MainVisualizer(filtered_data)
                prefix = config.target_name or "agent"
                visualizer.export_visualizations(prefix)
                self.show_visualizations(prefix)

            # Review and Loop
            self.console.print("\n[bold blue]=== Review ===[/]\n")
            self.console.print(f"Target Agent: {config.target_name}")
            self.console.print(
                f"Selected Playbooks: {len(filtered_data.playbooks)}"
            )
            self.console.print(f"Selected Flows: {len(filtered_data.flows)}")

            if Confirm.ask("Proceed to Migration?", default=True):
                break
            elif not Confirm.ask(
                "Do you want to re-configure and re-select resources?",
                default=True,
            ):
                self.console.print("Aborting migration.")
                return

        # 6. Start Migration
        if Confirm.ask("START MIGRATION?", default=True):
            config.source_agent_data_override = filtered_data

            async def _run():
                await migration_service.run_migration(
                    source_cx_agent_id=agent_id,
                    config=config,
                )
                await self._run_post_migration_opt_ins(
                    migration_service, config, filtered_data
                )

            self.console.print(
                f"🚀 Starting Migration to '{config.target_name}'..."
            )
            asyncio.run(_run())

            # Display status after migration
            if hasattr(migration_service, "ir") and migration_service.ir:
                self.display_status(migration_service.ir)

    async def _run_post_migration_opt_ins(
        self,
        migration_service: MigrationService,
        config: MigrationConfig,
        filtered_data: DFCXAgentIR,
    ) -> None:
        """Run the opt-in post-migration steps the user enabled in
        ``compose_config``: persist bundle, structural consolidation,
        Stage 3 topology wiring.

        Each step is independent and skipped silently if its flag is off.
        Errors are logged but do not abort subsequent steps.
        """
        # Construct a bundle once if any opt-in needs one.
        bundle = None
        bundle_path = f"{config.target_name}_ir.json"
        if config.persist_bundle or config.consolidate or config.run_stage3:
            bundle = IRBundle(
                config=config,
                source_agent_data=filtered_data,
                ir=migration_service.ir,
                app_url=(
                    f"https://ces.cloud.google.com/projects/"
                    f"{config.project_id}/locations/"
                    f"{migration_service.location}/apps/"
                    f"{migration_service.ir.metadata.app_id}"
                ),
            )

        # 1. Persist bundle after migrate (before any post-migration mutation
        #    so resume from a fresh migrate is possible).
        if config.persist_bundle and bundle is not None:
            try:
                migration_service.persist_bundle(
                    bundle, bundle_path, phase="migrate", status="ok"
                )
                self.console.print(f"[green]IR bundle saved → {bundle_path}[/]")
            except Exception as exc:  # noqa: BLE001
                logger.error("Bundle persist failed: %s", exc)

        # 2. Structural consolidation (Gemini-driven N→M grouping).
        #    MigrationCLI auto-accepts the proposed grouping
        #    (grouping_callback=None); the skill provides an interactive
        #    review TUI for the same flow.
        if config.consolidate:
            try:
                await migration_service.run_stage1(
                    consolidate=True,
                    bundle=bundle,
                    grouping_callback=None,
                    # Post-consolidation Version — keeps the 0.0.1/0.0.2/0.0.3
                    # sequence from the optimize_for_cxas branch intact and
                    # adds 0.0.4 for the consolidation step.
                    version_label="0.0.4",
                    persist_bundle_path=(
                        bundle_path if config.persist_bundle else None
                    ),
                )
                self.console.print(
                    "[green]Structural consolidation complete.[/]"
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Consolidation failed: %s", exc)
                self.console.print(f"[yellow]Consolidation failed: {exc}[/]")

        # 3. Stage 3 topology wiring (requires consolidation to have run
        #    successfully — bundle.grouping is set inside run_stage1 above).
        if config.run_stage3 and bundle is not None:
            try:
                updated, skipped, failed = await migration_service.run_stage3(
                    bundle=bundle,
                    mode="hub",
                    persist_bundle_path=(
                        bundle_path if config.persist_bundle else None
                    ),
                )
                self.console.print(
                    f"[green]Stage 3 wiring: updated={updated} "
                    f"skipped={skipped} failed={failed}[/]"
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Stage 3 wiring failed: %s", exc)
                self.console.print(f"[yellow]Stage 3 wiring failed: {exc}[/]")


# ===========================================================================
# `cxas migrate dfcx-cxas` subcommands
#
# Non-interactive (scriptable) entry points for the same MigrationService
# methods the MigrationCLI dashboard calls. Each subcommand is a thin
# argparse → method call wrapper. The `register()` function attaches
# the whole subtree under `migrate` from `cli/main.py`.
# ===========================================================================


_sub_console = Console()


def _resolve_bundle_path(args: argparse.Namespace) -> str:
    """Resolve the IR bundle path from CLI args.

    ``--ir-bundle PATH`` wins. Otherwise ``--target-name TARGET`` resolves
    to ``<TARGET>_ir.json`` in the current directory. Exits with a non-zero
    status if neither is provided or the resolved path doesn't exist.
    """
    if getattr(args, "ir_bundle", None):
        if not os.path.exists(args.ir_bundle):
            _sub_console.print(f"[red]IR bundle not found:[/] {args.ir_bundle}")
            sys.exit(1)
        return args.ir_bundle
    if not getattr(args, "target_name", None):
        _sub_console.print("[red]Pass --target-name or --ir-bundle.[/]")
        sys.exit(1)
    path = ir_bundle.find_default_bundle(args.target_name)
    if not path:
        _sub_console.print(
            f"[red]No bundle found:[/] {args.target_name}_ir.json "
            f"(searched in {os.getcwd()})"
        )
        sys.exit(1)
    return path


def _restore_service_and_bundle(
    args: argparse.Namespace,
) -> tuple[MigrationService, IRBundle, str]:
    """Load the bundle and restore a :class:`MigrationService` from it.
    Honors ``--project-id`` and ``--location`` overrides."""
    bundle_path = _resolve_bundle_path(args)
    _sub_console.print(f"[cyan]Loading IR bundle:[/] {bundle_path}")
    bundle = ir_bundle.load(bundle_path)
    service = MigrationService.restore_from_bundle(
        bundle,
        project_id=getattr(args, "project_id", None),
        location=getattr(args, "location", None),
    )
    return service, bundle, bundle_path


def _add_bundle_args(parser: argparse.ArgumentParser) -> None:
    """Add the bundle resolution + project/location overrides shared by
    every stage subcommand."""
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--ir-bundle",
        help="Path to an existing <target>_ir.json bundle.",
    )
    src.add_argument(
        "--target-name",
        help=(
            "Target name; resolves to <target>_ir.json in the current "
            "directory."
        ),
    )
    parser.add_argument(
        "--project-id", help="Override the bundle's project ID."
    )
    parser.add_argument(
        "--location", help="Override the bundle's CXAS location."
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive mode (skip confirmations).",
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def run_end_to_end(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx-cxas run`` — non-interactive end-to-end."""
    if not (args.source_agent_id or args.source_zip):
        _sub_console.print("[red]Pass --source-agent-id or --source-zip.[/]")
        sys.exit(1)

    cx_api = ConversationalAgentsAPI()
    if args.source_agent_id:
        _sub_console.print(
            f"[cyan]Fetching source agent:[/] {args.source_agent_id}"
        )
        agent_data = cx_api.fetch_full_agent_details(
            args.source_agent_id, use_export=True
        )
    else:
        _sub_console.print(f"[cyan]Loading source zip:[/] {args.source_zip}")
        with open(args.source_zip, "rb") as f:
            agent_data = cx_api.process_local_agent_zip(f.read())
    if not agent_data:
        _sub_console.print("[red]Failed to load source agent.[/]")
        sys.exit(1)

    config = MigrationConfig(
        project_id=args.project_id,
        target_name=args.target_name,
        env=args.env,
        model=args.model,
        optimize_for_cxas=not args.no_optimize,
        consolidate=args.consolidate,
        run_stage3=args.stage3,
        persist_bundle=args.persist_bundle,
        gen_report=True,
        gen_unit_tests=True,
        source_agent_data_override=agent_data,
    )

    service = MigrationService(
        project_id=args.project_id,
        location=args.location,
        default_model=args.model,
    )

    # Reuse the same opt-in helper the interactive dashboard uses — single
    # source of truth for post-migration plumbing.
    dashboard = MigrationCLI()

    async def _main():
        await service.run_migration(
            source_cx_agent_id=args.source_agent_id or "uploaded-agent",
            config=config,
        )
        await dashboard._run_post_migration_opt_ins(service, config, agent_data)

    asyncio.run(_main())
    _sub_console.print(
        f"[bold green]Migration complete:[/] {config.target_name}"
    )


def run_stage1(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx-cxas stage1`` — variable dedup + optional
    Gemini consolidation against an existing bundle."""
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    consolidate = not args.no_consolidate
    persist_path = None if args.no_persist else bundle_path

    async def _main():
        return await service.run_stage1(
            consolidate=consolidate,
            bundle=bundle if consolidate else None,
            grouping_json_path=args.grouping_json,
            on_integrity_fail=args.on_integrity_fail,
            version_label=args.version_label,
            persist_bundle_path=persist_path,
        )

    asyncio.run(_main())
    _sub_console.print("[bold green]Stage 1 complete.[/]")


def run_stage2(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx-cxas stage2`` — instruction state machines +
    tool mocks, with optional unit-test regen / lint / report."""
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    persist_path = None if args.no_persist else bundle_path
    target_name = bundle.config.target_name

    async def _main():
        await service.run_stage2(
            version_label=args.version_label,
            generate_unit_tests=not args.no_unit_tests,
            unit_tests_path=(
                f"{target_name}_unit_tests.json"
                if not args.no_unit_tests
                else None
            ),
            run_lint=not args.no_lint,
            write_report_to=(
                f"{target_name}_optimization_report.md"
                if not args.no_report
                else None
            ),
            bundle=bundle,
            persist_bundle_path=persist_path,
        )

    asyncio.run(_main())
    _sub_console.print("[bold green]Stage 2 complete.[/]")


def run_stage3(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx-cxas stage3`` — parent-child topology
    wiring after consolidation."""
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    persist_path = None if (args.no_persist or args.dry_run) else bundle_path

    async def _main():
        return await service.run_stage3(
            bundle=bundle,
            mode=args.mode,
            set_root=not args.no_set_root,
            dry_run=args.dry_run,
            persist_bundle_path=persist_path,
        )

    updated, skipped, failed = asyncio.run(_main())
    _sub_console.print(
        f"[bold green]Stage 3 complete:[/] "
        f"updated={updated} skipped={skipped} failed={failed}"
    )


def run_resume(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx-cxas resume`` — interactive bundle picker and
    stage menu. If ``--target-name`` or ``--ir-bundle`` is given, skips
    the picker and goes straight to the stage menu."""
    if args.target_name or args.ir_bundle:
        bundle_path = _resolve_bundle_path(args)
    else:
        candidates = sorted(glob.glob("*_ir.json"))
        if not candidates:
            _sub_console.print("[red]No bundles found in current directory.[/]")
            sys.exit(1)
        _sub_console.print("[cyan]Available IR bundles:[/]")
        for i, c in enumerate(candidates, 1):
            _sub_console.print(f"  {i}. {c}")
        choice = Prompt.ask(
            "Pick bundle",
            choices=[str(i) for i in range(1, len(candidates) + 1)],
            default="1",
        )
        bundle_path = candidates[int(choice) - 1]

    stage = Prompt.ask(
        "Which stage to run",
        choices=["stage1", "stage2", "stage3"],
        default="stage1",
    )

    common = dict(
        ir_bundle=bundle_path,
        target_name=None,
        project_id=args.project_id,
        location=args.location,
        yes=args.yes,
    )
    if stage == "stage1":
        run_stage1(
            argparse.Namespace(
                **common,
                no_consolidate=False,
                grouping_json=None,
                on_integrity_fail="abort",
                version_label="0.0.1",
                no_persist=False,
            )
        )
    elif stage == "stage2":
        run_stage2(
            argparse.Namespace(
                **common,
                version_label="0.0.2",
                no_unit_tests=False,
                no_lint=False,
                no_report=False,
                no_persist=False,
            )
        )
    else:
        run_stage3(
            argparse.Namespace(
                **common,
                mode="hub",
                no_set_root=False,
                dry_run=False,
                no_persist=False,
            )
        )


# ---------------------------------------------------------------------------
# Argparse registration
# ---------------------------------------------------------------------------


def register(migrate_subparsers: argparse._SubParsersAction) -> None:
    """Add the ``dfcx-cxas`` subcommand tree under the ``migrate``
    subparser. Called from ``cli/main.py`` alongside the existing
    ``dfcx`` registration."""
    parser_dc = migrate_subparsers.add_parser(
        "dfcx-cxas",
        help=(
            "Non-interactive DFCX→CXAS migration with stage-level resumability."
        ),
    )
    dc_subs = parser_dc.add_subparsers(
        title="dfcx-cxas commands",
        dest="dfcx_cxas_command",
        required=True,
    )

    # --- run -------------------------------------------------------------
    p_run = dc_subs.add_parser(
        "run",
        help=(
            "End-to-end migration (source → migrate → optional stages). "
            "Non-interactive."
        ),
    )
    src = p_run.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--source-agent-id", help="DFCX source agent resource name."
    )
    src.add_argument("--source-zip", help="Path to a DFCX agent export zip.")
    p_run.add_argument("--project-id", required=True)
    p_run.add_argument("--location", default="us")
    p_run.add_argument("--target-name", required=True)
    p_run.add_argument("--env", choices=["PROD", "AUTOPUSH"], default="PROD")
    p_run.add_argument("--model", default=DEFAULT_MODEL)
    p_run.add_argument(
        "--no-optimize",
        action="store_true",
        help="Skip Stage 1 + Stage 2 optimization passes.",
    )
    p_run.add_argument(
        "--consolidate",
        action="store_true",
        help="Run Gemini structural consolidation after migration.",
    )
    p_run.add_argument(
        "--stage3",
        action="store_true",
        help="Run Stage 3 topology wiring (requires --consolidate).",
    )
    p_run.add_argument(
        "--persist-bundle",
        action="store_true",
        help="Persist IR bundle (<target>_ir.json) for resume.",
    )
    p_run.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive (currently always non-interactive).",
    )
    p_run.set_defaults(func=run_end_to_end)

    # --- stage1 ---------------------------------------------------------
    p_s1 = dc_subs.add_parser(
        "stage1", help="Variable dedup + optional Gemini consolidation."
    )
    _add_bundle_args(p_s1)
    p_s1.add_argument(
        "--no-consolidate",
        action="store_true",
        help="Variable dedup only — skip Gemini consolidation.",
    )
    p_s1.add_argument(
        "--grouping-json",
        help="Load groupings from this JSON file instead of asking Gemini.",
    )
    p_s1.add_argument(
        "--on-integrity-fail",
        choices=["abort", "warn", "ignore"],
        default="abort",
        help="What to do if pre-deploy integrity checks find blockers.",
    )
    p_s1.add_argument(
        "--version-label",
        default="0.0.1",
        help="CXAS Version display_name to create after the stage.",
    )
    p_s1.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the updated bundle back to disk.",
    )
    p_s1.set_defaults(func=run_stage1)

    # --- stage2 ---------------------------------------------------------
    p_s2 = dc_subs.add_parser(
        "stage2",
        help="Instruction state machines + tool mocks + lint + report.",
    )
    _add_bundle_args(p_s2)
    p_s2.add_argument(
        "--version-label",
        default="0.0.2",
        help="CXAS Version display_name to create after the stage.",
    )
    p_s2.add_argument(
        "--no-unit-tests",
        action="store_true",
        help="Skip deterministic unit-test regeneration.",
    )
    p_s2.add_argument(
        "--no-lint",
        action="store_true",
        help="Skip post-deploy `cxas pull` + `cxas lint`.",
    )
    p_s2.add_argument(
        "--no-report",
        action="store_true",
        help="Skip OptimizationReporter audit markdown.",
    )
    p_s2.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the updated bundle back to disk.",
    )
    p_s2.set_defaults(func=run_stage2)

    # --- stage3 ---------------------------------------------------------
    p_s3 = dc_subs.add_parser(
        "stage3", help="Parent-child topology wiring after consolidation."
    )
    _add_bundle_args(p_s3)
    mode = p_s3.add_mutually_exclusive_group()
    mode.add_argument(
        "--hub-and-spoke",
        dest="mode",
        action="store_const",
        const="hub",
        help="(default) Root has every non-root group as a direct child.",
    )
    mode.add_argument(
        "--preserve-hierarchy",
        dest="mode",
        action="store_const",
        const="hierarchy",
        help="Derive children from the source DFCX dep graph.",
    )
    p_s3.set_defaults(mode="hub")
    p_s3.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the topology without applying.",
    )
    p_s3.add_argument(
        "--no-set-root",
        action="store_true",
        help="Skip resetting the app's root_agent.",
    )
    p_s3.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the updated bundle back to disk.",
    )
    p_s3.set_defaults(func=run_stage3)

    # --- resume ---------------------------------------------------------
    p_resume = dc_subs.add_parser(
        "resume", help="Interactive bundle picker + stage menu."
    )
    _add_bundle_args(p_resume)
    p_resume.set_defaults(func=run_resume)
