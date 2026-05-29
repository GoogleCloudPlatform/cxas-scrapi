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

"""

import argparse
import asyncio
import glob
import logging
import os
import re
import sys
from typing import Any

from google.cloud.dialogflowcx_v3beta1 import services as cx_services
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cxas_scrapi.migration import grouping_review
from cxas_scrapi.migration.config import AGENT_MODELS, DEFAULT_MODEL
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    IRBundle,
    MigrationConfig,
    MigrationIR,
)
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.dfcx_exporter import ConversationalAgentsAPI
from cxas_scrapi.migration.main_visualizer import MainVisualizer
from cxas_scrapi.migration.service import MigrationService

logger = logging.getLogger(__name__)


class Tee:
    """Duplicates stdout/stderr to a file, preserving all raw formatting
    and ANSI escape colors.
    """

    def __init__(self, filepath: str):
        self.file = open(filepath, "a", encoding="utf-8")
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

    def write(self, data):
        self.stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def isatty(self) -> bool:
        return self.stdout.isatty()

    def __getattr__(self, name):
        return getattr(self.stdout, name)

    def close(self):
        if sys.stdout is self:
            sys.stdout = self.stdout
        if sys.stderr is self:
            sys.stderr = self.stderr
        if hasattr(self, "file") and not self.file.closed:
            self.file.close()


_current_tee = None
_current_log_handler = None


def start_tee_logging(target_name: str):
    """Starts duplicating standard stdout/stderr outputs to the target
    log file.
    """
    global _current_tee, _current_log_handler  # noqa: PLW0603
    close_tee_logging()

    log_path = f"{target_name}_migration.log"
    _current_tee = Tee(log_path)

    # Dynamically bind a FileHandler to the root logger to capture all
    # standard python logging (with timestamps)
    try:
        root_logger = logging.getLogger()
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        handler.setLevel(root_logger.level or logging.INFO)
        root_logger.addHandler(handler)
        _current_log_handler = handler
    except Exception as e:
        sys.stderr.write(f"[WARNING] Failed to bind dynamic file logger: {e}\n")

    logging.getLogger(__name__).info(
        f"Saving full console stdout/stderr locally to → {log_path}"
    )


def close_tee_logging():
    """Stops duplicating stdout/stderr and closes the active Tee file
    and dynamic logger handlers.
    """
    global _current_tee, _current_log_handler  # noqa: PLW0603

    if _current_log_handler:
        try:
            root_logger = logging.getLogger()
            root_logger.removeHandler(_current_log_handler)
            _current_log_handler.close()
        except Exception:
            pass
        _current_log_handler = None

    if _current_tee:
        _current_tee.close()
        _current_tee = None


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

        raw_env_choice = (
            Prompt.ask(
                "Enter Environment [[bold cyan]P[/]ROD/[bold cyan]A[/]UTOPUSH]",
                choices=["P", "p", "A", "a"],
                default="P",
                show_choices=False,
            )
            .strip()
            .upper()
        )

        if raw_env_choice == "P":
            env = "PROD"
        else:
            env = "AUTOPUSH"

        model = Prompt.ask(
            "Enter Global App Model",
            choices=AGENT_MODELS,
            default=DEFAULT_MODEL,
        )

        raw_choice = (
            Prompt.ask(
                "Set Migration Profile (Best Practices recommended) "
                "[[bold cyan]B[/]est Practices Optimization/"
                "[bold cyan]F[/]ast 1:1 Migration Only/"
                "[bold cyan]C[/]ustom (Advanced)]",
                choices=["B", "b", "F", "f", "C", "c"],
                default="B",
                show_choices=False,
            )
            .strip()
            .upper()
        )

        if raw_choice == "B":
            profile_choice = "Best Practices Optimization"
        elif raw_choice == "F":
            profile_choice = "Fast 1:1 Migration Only"
        else:
            profile_choice = "Custom (Advanced)"

        # Map raw settings based on profile selection
        if profile_choice == "Best Practices Optimization":
            profile = "standard"
            optimize_for_cxas = True
            persist_bundle = True
            gen_report = True
            architecture = "hub-and-spoke"

            # Ask subsequent conditional options (defaulting to standard
            # best practices)
            gen_unit_tests = Confirm.ask("Generate Unit Tests?", default=True)
            gen_hillclimbing_evals = Confirm.ask(
                "Generate Hillclimbing Evals? [yellow]*feature coming*[/]",
                default=False,
            )
            raw_eval_choice = (
                Prompt.ask(
                    "Enter Eval Target [yellow]*feature coming*[/] "
                    "[[bold cyan]C[/]ustom API Runner/"
                    "[bold cyan]N[/]ative Product Eval (Stub)]",
                    choices=["C", "c", "N", "n"],
                    default="C",
                    show_choices=False,
                )
                .strip()
                .upper()
            )

            if raw_eval_choice == "C":
                eval_runner_target = "Custom API Runner"
            else:
                eval_runner_target = "Native Product Eval (Stub)"

        elif profile_choice == "Fast 1:1 Migration Only":
            profile = "direct"
            optimize_for_cxas = False
            persist_bundle = False
            gen_report = True
            architecture = "hub-and-spoke"

            # Skip subsequent questions and use safe direct defaults
            gen_unit_tests = False
            gen_hillclimbing_evals = False
            eval_runner_target = "Custom API Runner"

        else:  # "Custom (Advanced)"
            profile = "custom"
            optimize_for_cxas = Confirm.ask("Optimize for CXAS?", default=True)

            if optimize_for_cxas:
                architecture = Prompt.ask(
                    "Choose Spoke-Hub Architecture style",
                    choices=["hub-and-spoke", "original-hierarchy"],
                    default="hub-and-spoke",
                )
                persist_bundle = Confirm.ask(
                    "Persist IR bundle for stage-resume?",
                    default=True,
                )
            else:
                architecture = "hub-and-spoke"
                persist_bundle = Confirm.ask(
                    "Persist IR bundle for stage-resume?",
                    default=False,
                )

            gen_report = Confirm.ask("Generate Migration Report?", default=True)

            # Subsequent questions are shown for Custom too
            gen_unit_tests = Confirm.ask("Generate Unit Tests?", default=True)
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
            profile=profile,
            architecture=architecture,
            gen_report=gen_report,
            gen_unit_tests=gen_unit_tests,
            gen_hillclimbing_evals=gen_hillclimbing_evals,
            eval_runner_target=eval_runner_target,
            migration_version="2.0",
            interactive=True,
            optimize_for_cxas=optimize_for_cxas,
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

    def _parse_agent_id(self, raw_input: str) -> str:
        """Parse and auto-extract a clean Dialogflow CX Agent Resource Name
        path from a raw user input string (which can be a browser URL or the
        raw path itself).
        """
        raw_input = raw_input.strip()
        match = re.search(
            r"projects/([^/]+)/locations/([^/]+)/agents/([a-zA-Z0-9-]+)",
            raw_input,
        )
        return match.group(0) if match else raw_input

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
            self.console.print(
                "\n[cyan]💡 Hint:[/] Paste either the full raw Agent "
                "Resource Name or the browser Console URL.\n"
                "   - [bold]Format:[/] "
                "projects/{project_id}/locations/{location}/"
                "agents/{agent_uuid}\n"
                "   - [bold]Example:[/] "
                "projects/my-project-123/locations/global/agents/"
                "a4371f49-5982-4293-801b-551cf940ab65\n"
            )
            raw_input = Prompt.ask("Enter Source Agent ID")
            agent_id = self._parse_agent_id(raw_input)
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
            start_tee_logging(config.target_name)
            try:
                asyncio.run(_run())
            finally:
                close_tee_logging()

            # Display status after migration
            if hasattr(migration_service, "ir") and migration_service.ir:
                self.display_status(migration_service.ir)

    async def _run_post_migration_opt_ins(
        self,
        migration_service: MigrationService,
        config: MigrationConfig,
        filtered_data: DFCXAgentIR,
    ) -> None:
        """Run the post-migration pipeline steps enabled by the selected
        profile: persist bundle, structural consolidation, and Stage 3
        topology wiring.

        Skips steps silently if their logical properties are inactive.
        Errors are logged but do not abort subsequent steps.
        """
        # Construct a bundle once if any opt-in needs one.
        bundle = None
        bundle_path = f"{config.target_name}_ir.json"
        if config.persist_bundle or config.consolidate or config.run_stage_3:
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
        #    Presents the interactive console groupings TUI review step
        #    to the user.
        if config.consolidate:
            try:

                async def _tui_callback(
                    ir, groupings, consolidator, root_key, dep_summary
                ):
                    return await grouping_review.interactive_review(
                        ir=ir,
                        groupings=groupings,
                        consolidator=consolidator,
                        root_key=root_key,
                        dep_summary=dep_summary,
                        console=self.console,
                    )

                await migration_service.run_stage_1(
                    bundle=bundle,
                    grouping_callback=_tui_callback,
                    version_label="0.0.3",
                    dedup_version_label="0.0.2",
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
                return

        # 2.5 Instruction state machines & tool mocks generation (Stage 2).
        if config.optimize_for_cxas:
            try:
                await migration_service.run_stage_2(
                    version_label="0.0.4",
                    generate_unit_tests=config.gen_unit_tests,
                    unit_tests_path=(
                        f"{config.target_name}_unit_tests.json"
                        if config.gen_unit_tests
                        else None
                    ),
                    run_lint=True,
                    write_report_to=(
                        f"{config.target_name}_optimization_report.md"
                        if config.gen_report
                        else None
                    ),
                    bundle=bundle,
                    persist_bundle_path=(
                        bundle_path if config.persist_bundle else None
                    ),
                )
                self.console.print(
                    "[green]Instruction state machines & tool mocks "
                    "complete.[/]"
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Stage 2 optimization failed: %s", exc)
                self.console.print(f"[yellow]Stage 2 failed: {exc}[/]")

        # 3. Stage 3 topology wiring (requires consolidation to have run
        #    successfully — bundle.grouping is set inside run_stage_1 above).
        if config.run_stage_3 and bundle is not None:
            try:
                mode = (
                    "hub"
                    if config.architecture == "hub-and-spoke"
                    else "hierarchy"
                )
                updated, skipped, failed = await migration_service.run_stage_3(
                    bundle=bundle,
                    mode=mode,
                    version_label="0.0.5",
                    persist_bundle_path=(
                        bundle_path if config.persist_bundle else None
                    ),
                    console=self.console,
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
    path = IRBundle.find_default_bundle(args.target_name)
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
    bundle = IRBundle.load(bundle_path)
    service = MigrationService.restore_from_bundle(
        bundle,
        project_id=getattr(args, "project_id", None),
        location=getattr(args, "location", None),
    )
    return service, bundle, bundle_path


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

    # Resolve standard/direct/custom profile variables
    profile = getattr(args, "profile", "standard")
    if profile == "standard":
        optimize_for_cxas = True
        persist_bundle = True
        gen_report = True
        architecture = "hub-and-spoke"
        gen_unit_tests = not getattr(args, "no_unit_tests", False)
        gen_hillclimbing_evals = getattr(args, "gen_hillclimbing_evals", False)
        eval_runner_target = getattr(
            args, "eval_runner_target", "Custom API Runner"
        )
    elif profile == "direct":
        optimize_for_cxas = False
        persist_bundle = False
        gen_report = True
        architecture = "hub-and-spoke"
        gen_unit_tests = False
        gen_hillclimbing_evals = False
        eval_runner_target = "Custom API Runner"
    else:  # "custom"
        optimize_for_cxas = not getattr(args, "no_optimize", False)
        persist_bundle = getattr(args, "persist_bundle", False)
        gen_report = not getattr(args, "no_report", False)
        architecture = getattr(args, "architecture", "hub-and-spoke")
        gen_unit_tests = not getattr(args, "no_unit_tests", False)
        gen_hillclimbing_evals = getattr(args, "gen_hillclimbing_evals", False)
        eval_runner_target = getattr(
            args, "eval_runner_target", "Custom API Runner"
        )

    config = MigrationConfig(
        project_id=args.project_id,
        target_name=args.target_name,
        env=args.env,
        model=args.model,
        profile=profile,
        architecture=architecture,
        optimize_for_cxas=optimize_for_cxas,
        persist_bundle=persist_bundle,
        gen_report=gen_report,
        gen_unit_tests=gen_unit_tests,
        gen_hillclimbing_evals=gen_hillclimbing_evals,
        eval_runner_target=eval_runner_target,
        source_agent_data_override=agent_data,
    )

    service = MigrationService(
        project_id=args.project_id,
        location=args.location,
        default_model=args.model,
    )

    async def _main():
        await service.run_migration(
            source_cx_agent_id=args.source_agent_id or "uploaded-agent",
            config=config,
        )

    start_tee_logging(config.target_name)
    try:
        asyncio.run(_main())
        _sub_console.print(
            f"[bold green]Migration complete:[/] {config.target_name}"
        )
    finally:
        close_tee_logging()


def run_stage_1(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx stage_1`` — variable dedup + structural

    Gemini consolidation against an existing bundle.
    """
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    persist_path = None if args.no_persist else bundle_path

    async def _main():
        return await service.run_stage_1(
            bundle=bundle,
            grouping_json_path=args.grouping_json,
            version_label=args.version_label,
            dedup_version_label=getattr(args, "dedup_version_label", None)
            or "0.0.2",
            persist_bundle_path=persist_path,
        )

    start_tee_logging(bundle.config.target_name)
    try:
        asyncio.run(_main())
        _sub_console.print("[bold green]Stage 1 complete.[/]")
    finally:
        close_tee_logging()


def run_stage_2(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx stage_2`` — instruction state machines +
    tool mocks, with optional unit-test regen / lint / report."""
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    persist_path = None if args.no_persist else bundle_path
    target_name = bundle.config.target_name

    async def _main():
        await service.run_stage_2(
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

    start_tee_logging(bundle.config.target_name)
    try:
        asyncio.run(_main())
        _sub_console.print("[bold green]Stage 2 complete.[/]")
    finally:
        close_tee_logging()


def run_stage_3(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx stage_3`` — parent-child topology wiring

    after consolidation.
    """
    service, bundle, bundle_path = _restore_service_and_bundle(args)
    persist_path = None if args.no_persist else bundle_path
    mode = (
        "hub"
        if getattr(args, "architecture", "hub-and-spoke") == "hub-and-spoke"
        else "hierarchy"
    )

    async def _main():
        return await service.run_stage_3(
            bundle=bundle,
            mode=mode,
            version_label=args.version_label,
            persist_bundle_path=persist_path,
            console=_sub_console,
        )

    start_tee_logging(bundle.config.target_name)
    try:
        updated, skipped, failed = asyncio.run(_main())
        _sub_console.print(
            f"[bold green]Stage 3 complete:[/] "
            f"updated={updated} skipped={skipped} failed={failed}"
        )
    finally:
        close_tee_logging()


def run_resume(args: argparse.Namespace) -> None:
    """``cxas migrate dfcx --optimize --stage resume`` — interactive

    bundle picker and stage menu. If ``--target-name`` or ``--ir-bundle``
    is given, skips the picker and goes straight to the stage menu.
    """
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
        choices=["stage_1", "stage_2", "stage_3"],
        default="stage_1",
    )

    common = dict(
        ir_bundle=bundle_path,
        target_name=None,
        project_id=args.project_id,
        location=args.location,
        yes=args.yes,
    )
    if stage == "stage_1":
        run_stage_1(
            argparse.Namespace(
                **common,
                grouping_json=None,
                version_label="0.0.3",
                dedup_version_label="0.0.2",
                no_persist=False,
            )
        )
    elif stage == "stage_2":
        run_stage_2(
            argparse.Namespace(
                **common,
                version_label="0.0.4",
                no_unit_tests=False,
                no_lint=False,
                no_report=False,
                no_persist=False,
            )
        )
    else:
        run_stage_3(
            argparse.Namespace(
                **common,
                architecture="hub-and-spoke",
                version_label="0.0.5",
                no_persist=False,
            )
        )
