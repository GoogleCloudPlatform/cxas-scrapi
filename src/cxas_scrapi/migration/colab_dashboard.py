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

import asyncio
import copy
import json
import logging
import os
import re
import time
from datetime import datetime

import ipywidgets as widgets
from IPython.display import clear_output, display

from cxas_scrapi.migration.config import AGENT_MODELS
from cxas_scrapi.migration.data_models import MigrationConfig
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.main_visualizer import MainVisualizer

try:
    from google.colab import auth, files
except ImportError:
    auth = None
    files = None

logger = logging.getLogger(__name__)

AGENT_ID_PATTERN = re.compile(r"projects/[^/]+/locations/[^/]+/agents/[^/]+")


class MigrationConfigurator:
    def __init__(self):
        self.style = {"description_width": "initial"}
        self.layout = widgets.Layout(width="98%")

        default_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

        self.project_id = widgets.Text(
            description="Project ID:",
            placeholder="e.g., my-gcp-project",
            value=default_project,
            style=self.style,
            layout=self.layout,
        )

        default_agent_name = (
            f"migrated_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        self.target_name = widgets.Text(
            description="Target Agent Name:",
            placeholder="e.g., my_migrated_agent_v1",
            value=default_agent_name,
            style=self.style,
            layout=self.layout,
        )
        self.env = widgets.Dropdown(
            options=["PROD", "AUTOPUSH"],
            value="PROD",
            description="Environment:",
            style=self.style,
            layout=self.layout,
        )
        self.model = widgets.Dropdown(
            options=AGENT_MODELS,
            value="gemini-2.5-flash-001",
            description="Global App Model:",
            style=self.style,
            layout=self.layout,
        )
        self.migration_version = widgets.Dropdown(
            options=[
                ("2.0 (Beta, Playbooks/Flows/Hybrid)", "2.0"),
                ("1.0 (Legacy, Playbooks only)", "1.0"),
            ],
            value="1.0",
            description="Logic Version:",
            style=self.style,
            layout=self.layout,
        )
        self.gen_report = widgets.Checkbox(
            value=True, description="Generate Migration Report"
        )
        self.gen_unit_tests = widgets.Checkbox(
            value=True, description="Generate Unit Tests (Auto-Fix)"
        )
        self.gen_hillclimbing_evals = widgets.Checkbox(
            value=False, description="Generate Hillclimbing Evals"
        )
        self.eval_runner_target = widgets.Dropdown(
            options=["Custom API Runner", "Native Product Eval (Stub)"],
            value="Custom API Runner",
            description="Eval Target:",
            style=self.style,
            layout=self.layout,
        )

    def render(self):
        return widgets.VBox(
            [
                widgets.HTML("<h3>Migration Configuration</h3>"),
                self.project_id,
                self.target_name,
                self.env,
                self.model,
                self.migration_version,
                widgets.HBox([self.gen_report]),
                widgets.HTML("<b>Testing & Evaluation</b>"),
                self.eval_runner_target,
                widgets.HBox(
                    [self.gen_unit_tests, self.gen_hillclimbing_evals]
                ),
            ],
            layout=widgets.Layout(
                border="1px solid #ddd", padding="10px", margin="10px 0"
            ),
        )

    def get_config(self) -> MigrationConfig:
        return MigrationConfig(
            project_id=self.project_id.value,
            target_name=self.target_name.value,
            env=self.env.value,
            model=self.model.value,
            gen_report=self.gen_report.value,
            gen_unit_tests=self.gen_unit_tests.value,
            gen_hillclimbing_evals=self.gen_hillclimbing_evals.value,
            eval_runner_target=self.eval_runner_target.value,
            migration_version=self.migration_version.value,
            optimize_for_cxas=False,
        )


class AgentResourceSelector:
    def __init__(self, cx_api):
        self.cx_api = cx_api
        self.full_agent_data = None
        self.analyzer = None
        self.checkboxes = {"playbooks": [], "flows": [], "config": []}
        self.playbook_rows = []
        self.container = widgets.Output()
        self.status_label = widgets.HTML(
            "<b>Status:</b> Waiting for agent load..."
        )
        self.analyze_btn = widgets.Button(
            description="Analyze References & Dependencies",
            button_style="warning",
            icon="search",
            layout=widgets.Layout(width="100%", margin="10px 0"),
        )
        self.analyze_btn.on_click(self._run_analysis)
        self.export_btn = widgets.Button(
            description="Export Selected JSON",
            button_style="info",
            icon="download",
            layout=widgets.Layout(width="100%", margin="0 0 10px 0"),
        )
        self.export_btn.on_click(self._export_json)
        self.analysis_output = widgets.Output()

    def load_agent(self, agent_id, default_model="gemini-2.5-flash-001"):
        with self.container:
            clear_output()
            agent_id_match = AGENT_ID_PATTERN.search(agent_id)
            if agent_id_match:
                agent_id = agent_id_match.group()
            else:
                logger.error(f"❌ Invalid agent ID: {agent_id}")
                return
            logger.info(f"Loading Agent ID: {agent_id} ...")
            try:
                self.full_agent_data = self.cx_api.fetch_full_agent_details(
                    agent_id, use_export=True
                )
                self.analyzer = DependencyAnalyzer(self.full_agent_data)
                self._build_ui(default_model)
            except Exception as e:
                logger.error(f"❌ Error loading agent: {e}")

    def load_agent_from_data(
        self, agent_data, default_model="gemini-2.5-flash-001"
    ):
        with self.container:
            clear_output()
            logger.info("⏳ Processing uploaded agent data...")
            try:
                self.full_agent_data = agent_data
                self.analyzer = DependencyAnalyzer(self.full_agent_data)
                self._build_ui(default_model)
            except Exception as e:
                logger.error(f"❌ Error processing agent data: {e}")

    def update_all_playbook_models(self, new_model):
        for row in self.playbook_rows:
            row["dropdown"].value = new_model

    def _create_checkbox(self, label, tag, data_ref):
        cb = widgets.Checkbox(
            value=True, description=label, layout=widgets.Layout(width="95%")
        )
        cb.tag = tag
        cb.data_ref = data_ref
        cb.observe(self._on_change, names="value")
        return cb

    def _create_playbook_row(self, pb_data, default_model):
        cb = widgets.Checkbox(
            value=True,
            description=pb_data.get("displayName"),
            layout=widgets.Layout(width="60%"),
        )
        cb.tag = "playbook"
        cb.data_ref = pb_data
        cb.observe(self._on_change, names="value")
        dd = widgets.Dropdown(
            options=AGENT_MODELS,
            value=default_model,
            layout=widgets.Layout(width="35%"),
        )
        row_ui = widgets.HBox(
            [cb, dd],
            layout=widgets.Layout(
                width="98%",
                justify_content="space-between",
                min_height="40px",
                margin="2px 0",
            ),
        )
        self.playbook_rows.append(
            {"checkbox": cb, "dropdown": dd, "data": pb_data, "ui": row_ui}
        )
        return cb, row_ui

    def _on_change(self, change):
        self._update_status()
        self.analysis_output.clear_output()

    def _bulk_select(self, category, value):
        if category == "playbooks":
            for row in self.playbook_rows:
                row["checkbox"].value = value
        elif category in self.checkboxes:
            for cb in self.checkboxes[category]:
                cb.value = value
        self._update_status()

    def _update_status(self):
        pb_count = sum(1 for row in self.playbook_rows if row["checkbox"].value)
        flow_count = sum(1 for cb in self.checkboxes["flows"] if cb.value)
        text = "No Resources Selected"
        color = "gray"
        warning_msg = ""
        if pb_count > 0 and flow_count == 0:
            text = "Pure Playbooks"
            color = "#28a745"
        elif flow_count > 0 and pb_count == 0:
            text = "Pure Flows"
            color = "#17a2b8"
            warning_msg = (
                "<br><span style='color:#d32f2f; font-weight:bold;'>⚠️ Note: "
                "For migrating Flows or Hybrid agents, set "
                "Logic Version to 2.0 "
                "in Configuration</span>"
            )
        elif flow_count > 0 and pb_count > 0:
            text = "Hybrid Agent"
            color = "#6f42c1"
            warning_msg = (
                "<br><span style='color:#d32f2f; font-weight:bold;'>⚠️ Note: "
                "For migrating Flows or Hybrid agents, set "
                "Logic Version to 2.0 "
                "in Configuration</span>"
            )
        self.status_label.value = (
            f"<h3>Type: <span style='color:{color}'>{text}</span></h3> "
            f"(Selected: {pb_count} Playbooks, {flow_count} Flows){warning_msg}"
        )

    def _filter_widgets(self, text):
        search_term = text.lower()
        for row in self.playbook_rows:
            if search_term in row["checkbox"].description.lower():
                row["ui"].layout.display = "flex"
            else:
                row["ui"].layout.display = "none"
        all_other_cbs = self.checkboxes["flows"] + self.checkboxes["config"]
        for cb in all_other_cbs:
            if search_term in cb.description.lower():
                cb.layout.display = "flex"
            else:
                cb.layout.display = "none"

    def _run_analysis(self, b):
        if not self.analyzer:
            return
        selected_ids = []
        for row in self.playbook_rows:
            if row["checkbox"].value:
                selected_ids.append(row["data"].get("name"))
        for cb in self.checkboxes["flows"]:
            if cb.value:
                f = cb.data_ref.get("flow", cb.data_ref)
                selected_ids.append(f.get("name"))
        outgoing, incoming = self.analyzer.get_impact(selected_ids)
        with self.analysis_output:
            clear_output()
            if outgoing:
                html = (
                    "<div style='background-color:#fff3cd; "
                    "border:1px solid #ffeeba; padding:10px; "
                    "margin-bottom:10px; border-radius:5px;'>"
                )
                html += (
                    "<h4 style='color:#856404; margin-top:0;'>Missing "
                    "Dependencies (Outgoing)</h4>"
                )
                html += (
                    "<p style='font-size:12px'>The selected resources "
                    "reference these items, but they are <b>not selected</b>:"
                    "</p><ul>"
                )
                for rid in outgoing:
                    det = self.analyzer.get_details(rid)
                    html += f"<li><b>[{det['type']}]</b> {det['name']}</li>"
                html += "</ul></div>"
                display(widgets.HTML(html))
            else:
                display(
                    widgets.HTML(
                        "<div style='color:green; padding:5px;'>✅ No missing "
                        "dependencies detected.</div>"
                    )
                )
            if incoming:
                html = (
                "<div style='background-color:#d1ecf1; "
                "border:1px solid #bee5eb; padding:10px; border-radius:5px;'>"
            )
                html += (
                    "<h4 style='color:#0c5460; margin-top:0;'>Incoming "
                    "References</h4>"
                )
                html += (
                "<p style='font-size:12px'>These unselected resources "
                "reference your selection (they might break if you migrate "
                "only the selection):</p><ul>"
            )
                for rid in incoming:
                    det = self.analyzer.get_details(rid)
                    html += f"<li><b>[{det['type']}]</b> {det['name']}</li>"
                html += "</ul></div>"
                display(widgets.HTML(html))

    def _export_json(self, b):
        data = self.get_selected_data()
        if not data:
            with self.analysis_output:
                print("⚠️ No data available to export.")
            return
        filename = f"exported_resources_{int(time.time())}.json"
        try:
            with open(filename, "w") as f:
                json.dump(data, f, indent=2)

            if files:
                files.download(filename)
            else:
                print(f"File saved locally: {filename}")
            with self.analysis_output:
                print(
                    f"✅ Successfully exported selected resources to {filename}"
                )
        except Exception as e:
            with self.analysis_output:
                print(f"❌ Error exporting JSON: {e}")

    def _build_ui(self, default_model):
        self.checkboxes = {"playbooks": [], "flows": [], "config": []}
        self.playbook_rows = []
        agent_name = self.full_agent_data.get("displayName", "Unknown Agent")
        self.checkboxes["config"].append(
            self._create_checkbox(
                f"Agent Settings ({agent_name})",
                "config",
                self.full_agent_data.get("agent", {}),
            )
        )
        playbook_ui_items = []
        for pb in self.full_agent_data.get("playbooks", []):
            cb, row_ui = self._create_playbook_row(pb, default_model)
            self.checkboxes["playbooks"].append(cb)
            playbook_ui_items.append(row_ui)
        for flow in self.full_agent_data.get("flows", []):
            actual_flow = flow.get("flow_data", flow)
            self.checkboxes["flows"].append(
                self._create_checkbox(
                    actual_flow.get("displayName", "Unnamed"), "flow", flow
                )
            )
        search_bar = widgets.Text(
            placeholder="🔍 Search resources...",
            layout=widgets.Layout(width="100%"),
        )
        search_bar.observe(
            lambda change: self._filter_widgets(change["new"]), names="value"
        )

        def create_section(title, color, items, category_key=None):
            controls = []
            if category_key:
                btn_all = widgets.Button(
                    description="Select All",
                    layout=widgets.Layout(width="48%"),
                    button_style="info",
                )
                btn_none = widgets.Button(
                    description="Clear All", layout=widgets.Layout(width="48%")
                )
                btn_all.on_click(
                    lambda b: self._bulk_select(category_key, True)
                )
                btn_none.on_click(
                    lambda b: self._bulk_select(category_key, False)
                )
                controls = [
                    widgets.HBox(
                        [btn_all, btn_none],
                        layout=widgets.Layout(margin="0 0 5px 0"),
                    )
                ]
            return widgets.VBox(
                [
                    widgets.HTML(
                        f"<div style='background-color:{color}; color:white; "
                        f"padding:5px; font-weight:bold'>{title}</div>"
                    ),
                    *controls,
                    widgets.VBox(
                        items,
                        layout=widgets.Layout(
                            max_height="200px",
                            overflow_y="scroll",
                            width="100%",
                        ),
                    ),
                ],
                layout=widgets.Layout(
                    border=f"1px solid {color}", margin="5px"
                ),
            )

        ui = widgets.VBox(
            [
                self.status_label,
                search_bar,
                create_section(
                    "Agent Configuration", "#e67e22", self.checkboxes["config"]
                ),
                create_section(
                    "Playbooks",
                    "#007bff",
                    playbook_ui_items,
                    category_key="playbooks",
                ),
                create_section(
                    "Flows",
                    "#6610f2",
                    self.checkboxes["flows"],
                    category_key="flows",
                ),
                widgets.HTML("<hr>"),
                self.analyze_btn,
                self.export_btn,
                self.analysis_output,
            ]
        )
        with self.container:
            clear_output()
            display(ui)
        self._update_status()

    def get_selected_data(self):
        if not self.full_agent_data:
            return None
        filtered_data = copy.deepcopy(self.full_agent_data)
        selected_pbs_data = []
        for row in self.playbook_rows:
            if row["checkbox"].value:
                pb_data = copy.deepcopy(row["data"])
                pb_data["_target_model"] = row["dropdown"].value
                selected_pbs_data.append(pb_data)
        filtered_data["playbooks"] = selected_pbs_data
        selected_flows = [
            cb.data_ref.get("flow", cb.data_ref).get("name")
            for cb in self.checkboxes["flows"]
            if cb.value
        ]
        filtered_data["flows"] = [
            f
            for f in filtered_data["flows"]
            if f.get("flow", f).get("name") in selected_flows
        ]
        return filtered_data

    def render(self):
        return self.container


def authenticate_colab(project_id: str):
    """Authenticates the user in a Colab environment."""
    is_colab_enterprise = os.environ.get("VERTEX_PRODUCT") == "COLAB_ENTERPRISE"

    logger.info("Authenticating user...")
    if is_colab_enterprise:
        logger.info(
            "-> Running in Colab Enterprise, skipping interactive auth."
        )
    else:
        auth.authenticate_user(project_id=project_id)
        logger.info("-> User authenticated.")

        print(
            "\n[bold yellow]Note:[/] If you encounter auth issues with "
            "GenAI, you might need to run:"
        )
        print(
            "!gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/generative-language.retriever"
        )


def render_migration_dashboard(cx_api, migration_service):
    """Renders the full interactive migration dashboard in a notebook."""

    selector_ui = AgentResourceSelector(cx_api)
    config_ui = MigrationConfigurator()

    def on_global_model_change(change):
        selector_ui.update_all_playbook_models(change["new"])

    config_ui.model.observe(on_global_model_change, names="value")

    agent_id_input = widgets.Text(
        value="",
        description="Source Agent ID:",
        placeholder="projects/<proj>/locations/<loc>/agents/<uuid>",
        layout=widgets.Layout(width="80%"),
    )
    load_btn = widgets.Button(description="Load from ID", button_style="info")

    upload_btn = widgets.FileUpload(
        accept=".zip", multiple=False, description="Upload Zip"
    )
    upload_label = widgets.Label("Or upload a local agent export (.zip):")

    viz_btn = widgets.Button(
        description="Visualize Selected",
        button_style="warning",
        layout=widgets.Layout(width="32%", height="50px"),
    )
    export_viz_btn = widgets.Button(
        description="Export Visualized Resources",
        button_style="info",
        layout=widgets.Layout(width="32%", height="50px"),
    )
    migrate_btn = widgets.Button(
        description="START MIGRATION",
        button_style="success",
        layout=widgets.Layout(width="32%", height="50px"),
    )
    button_box = widgets.HBox(
        [viz_btn, export_viz_btn, migrate_btn],
        layout=widgets.Layout(justify_content="space-between"),
    )

    output_log = widgets.Output()

    topology_log = widgets.Output()
    topology_accordion = widgets.Accordion(children=[topology_log])
    topology_accordion.set_title(0, "🗺️ High-Level Selected Resources Graph")
    topology_accordion.selected_index = None

    details_log = widgets.Output()
    details_accordion = widgets.Accordion(children=[details_log])
    details_accordion.set_title(0, "📝 Detailed Resource Visualization")
    details_accordion.selected_index = None

    def on_load_click(b):
        if not agent_id_input.value:
            with output_log:
                logger.warning("⚠️ Please enter a Source Agent ID.")
            return
        selector_ui.load_agent(
            agent_id_input.value, default_model=config_ui.model.value
        )

    def on_upload_change(change):
        if not upload_btn.value:
            return
        uploaded_files = upload_btn.value
        if isinstance(uploaded_files, tuple):
            file_info = uploaded_files[0]
        elif isinstance(uploaded_files, dict):
            key = list(uploaded_files.keys())[0]
            file_info = uploaded_files[key]
        else:
            file_info = uploaded_files[0]

        content = file_info.get("content")
        filename = file_info.get("name")

        if isinstance(content, memoryview):
            content = content.tobytes()

        try:
            if isinstance(upload_btn.value, tuple):
                upload_btn.value = ()
            elif isinstance(upload_btn.value, dict):
                upload_btn.value.clear()
        except Exception as e:
            logger.debug(f"Non-fatal error clearing widget state: {e}")

        with output_log:
            logger.info(f"📂 Processing uploaded file: {filename}...")

        agent_data = cx_api.process_local_agent_zip(content)

        if agent_data:
            selector_ui.load_agent_from_data(
                agent_data, default_model=config_ui.model.value
            )
            with output_log:
                logger.info("✅ Upload processed successfully.")
        else:
            with output_log:
                logger.error("❌ Failed to process uploaded zip.")

    def on_visualize_click(b):
        filtered_data = selector_ui.get_selected_data()
        if not filtered_data or (
            not filtered_data.get("playbooks")
            and not filtered_data.get("flows")
        ):
            with output_log:
                logger.error(
                    "❌ No Playbooks or Flows selected to visualize. "
                    "Please select some from the list above."
                )
            return

        visualizer = MainVisualizer(filtered_data)

        with topology_log:
            clear_output()
            logger.info("Rendering topology graph...")
            visualizer.visualize_topology()

        with details_log:
            clear_output()
            logger.info("Rendering detailed resource trees...")
            visualizer.visualize_details()

        topology_accordion.selected_index = 0

    def on_export_viz_click(b):
        filtered_data = selector_ui.get_selected_data()
        if not filtered_data or (
            not filtered_data.get("playbooks")
            and not filtered_data.get("flows")
        ):
            with output_log:
                logger.error(
                    "❌ No Playbooks or Flows selected to export. "
                    "Please select some from the list above."
                )
            return

        with output_log:
            logger.info("Exporting visualizations (SVG and Markdown)...")
            visualizer = MainVisualizer(filtered_data)
            config = config_ui.get_config()
            prefix = config["target_name"] if config["target_name"] else "agent"
            visualizer.export_visualizations(prefix)
            logger.info(
                "✅ Export completed. Downloads should start automatically."
            )

    def on_migrate_click(b):
        with output_log:
            clear_output()
            config = config_ui.get_config()

            if not config.target_name:
                logger.error("❌ Error: Target Agent Name is required.")
                return

            logger.info(
                f"🚀 Starting Migration to '{config.target_name}' "
                f"({config.env})..."
            )

            filtered_data = selector_ui.get_selected_data()
            if not filtered_data:
                logger.error(
                    "❌ Error: No agent data loaded. Please Load ID or "
                    "Upload Zip first."
                )
                return

            config.source_agent_data_override = filtered_data

            async def _run():
                await migration_service.run_migration(
                    source_cx_agent_id=agent_id_input.value or "uploaded-agent",
                    config=config,
                )

            asyncio.create_task(_run())

    load_btn.on_click(on_load_click)
    upload_btn.observe(on_upload_change, names="value")
    viz_btn.on_click(on_visualize_click)
    export_viz_btn.on_click(on_export_viz_click)
    migrate_btn.on_click(on_migrate_click)

    display(
        widgets.VBox(
            [
                widgets.HTML("<h2>1. Load Source Agent</h2>"),
                widgets.HBox([agent_id_input, load_btn]),
                widgets.HBox(
                    [upload_label, upload_btn],
                    layout=widgets.Layout(margin="10px 0 0 0"),
                ),
                widgets.HTML("<hr>"),
                widgets.HBox(
                    [
                        widgets.VBox(
                            [
                                widgets.HTML("<h2>2. Select Resources</h2>"),
                                selector_ui.render(),
                            ],
                            layout=widgets.Layout(width="50%"),
                        ),
                        widgets.VBox(
                            [
                                widgets.HTML("<h2>3. Configure</h2>"),
                                config_ui.render(),
                            ],
                            layout=widgets.Layout(width="50%"),
                        ),
                    ]
                ),
                widgets.HTML("<hr>"),
                button_box,
                topology_accordion,
                details_accordion,
                output_log,
            ]
        )
    )
