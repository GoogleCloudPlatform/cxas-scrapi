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

"""CLI dashboard and interactive prompts for migration."""

import asyncio
import logging
import os
from typing import Any

from google.cloud.dialogflowcx_v3beta1 import services as cx_services
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cxas_scrapi.migration.config import AGENT_MODELS, DEFAULT_MODEL
from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    MigrationConfig,
    MigrationIR,
)
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
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

        migration_version = Prompt.ask(
            "Enter Logic Version", choices=["1.0", "2.0"], default="2.0"
        )

        gen_report = Confirm.ask("Generate Migration Report?", default=True)
        gen_unit_tests = Confirm.ask(
            "Generate Unit Tests (Auto-Fix)?", default=True
        )
        gen_hillclimbing_evals = Confirm.ask(
            "Generate Hillclimbing Evals?", default=False
        )

        eval_runner_target = Prompt.ask(
            "Enter Eval Target",
            choices=["Custom API Runner", "Native Product Eval (Stub)"],
            default="Custom API Runner",
        )

        optimize_for_cxas = Confirm.ask("Optimize for CXAS?", default=False)

        return MigrationConfig(
            project_id=project_id,
            target_name=target_name,
            env=env,
            model=model,
            gen_report=gen_report,
            gen_unit_tests=gen_unit_tests,
            gen_hillclimbing_evals=gen_hillclimbing_evals,
            eval_runner_target=eval_runner_target,
            migration_version=migration_version,
            optimize_for_cxas=optimize_for_cxas,
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
            self.console.print(
                f"Selected Flows: {len(filtered_data.flows)}"
            )

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

            self.console.print(
                f"🚀 Starting Migration to '{config.target_name}'..."
            )
            asyncio.run(_run())

            # Display status after migration
            if hasattr(migration_service, "ir") and migration_service.ir:
                self.display_status(migration_service.ir)
