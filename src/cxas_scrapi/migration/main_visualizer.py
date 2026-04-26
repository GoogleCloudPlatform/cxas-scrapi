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

"""Master visualizer coordinating topology graph and detailed Rich trees."""

import io
import uuid
from typing import Any, Dict

try:
    from IPython.display import HTML, display

    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.tree import Tree

try:
    from google.colab import files  # type: ignore[import]

    HAS_COLAB = True
except ImportError:
    HAS_COLAB = False

from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.migration.graph_visualizer import HighLevelGraphVisualizer
from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer


class MainVisualizer:
    """Coordinates topology graph and detailed Rich trees with an
    interactive zoom UI (designed for Jupyter / Colab environments).
    """

    def __init__(self, selected_data: Dict[str, Any]):
        self.data = selected_data
        self.console = Console(force_terminal=False, width=120)

    def _build_tools_tree(self) -> Tree:
        """Build a Rich Tree summarising tools and webhooks."""
        root = Tree("🛠️ [bold orange3]Agent Tools & Webhooks[/bold orange3]")

        tools = self.data.get("tools", [])
        if tools:
            tools_node = root.add("[bold]Tools[/]")
            for tool_entry in tools:
                tool_data = tool_entry.get("tool", tool_entry)
                tool_name = tool_data.get(
                    "displayName", tool_data.get("name", "Unknown")
                )
                tool_node = tools_node.add(
                    f"🔧 [bold yellow]{escape(tool_name)}[/]"
                )
                if "description" in tool_data:
                    tool_node.add(
                        f"[dim]Description:[/] "
                        f"{escape(tool_data['description'])}"
                    )
                if (
                    "openApiSpec" in tool_data
                    and "textSchema" in tool_data["openApiSpec"]
                ):
                    tool_node.add("[dim]Type:[/] OpenAPI Toolset")
                    tool_node.add(
                        f"[dim]Schema:[/] "
                        f"{escape(tool_data['openApiSpec']['textSchema'])}"
                    )
                if "dataStoreSpec" in tool_data or "dataStoreTool" in tool_data:
                    data_store = tool_data.get(
                        "dataStoreSpec"
                    ) or tool_data.get("dataStoreTool", {})
                    tool_node.add("[dim]Type:[/] Data Store")
                    if "dataStoreConnections" in data_store:
                        tool_node.add(
                            f"[dim]Connections:[/] "
                            f"{escape(str(data_store['dataStoreConnections']))}"
                        )

        webhooks = self.data.get("webhooks", [])
        if webhooks:
            wh_nodes = root.add("[bold]Webhooks[/]")
            for webhook_entry in webhooks:
                webhook_data = webhook_entry.get("value", webhook_entry)
                webhook_name = webhook_data.get(
                    "displayName", webhook_data.get("name", "Unknown")
                )
                webhook_node = wh_nodes.add(
                    f"⚡ [bold red]{escape(webhook_name)}[/]"
                )
                generic_web_service = webhook_data.get("genericWebService", {})
                if generic_web_service:
                    uri = generic_web_service.get("uri", "")
                    webhook_node.add(f"[dim]URI:[/] {escape(uri)}")
                    wt = generic_web_service.get("webhookType", "STANDARD")
                    webhook_node.add(f"[dim]Type:[/] {escape(wt)}")
                    if generic_web_service.get("httpMethod"):
                        webhook_node.add(
                            f"[dim]Method:[/] "
                            f"{escape(generic_web_service.get('httpMethod'))}"
                        )
                    if generic_web_service.get("requestBody"):
                        webhook_node.add(
                            f"[dim]Request Body:[/] "
                            f"{escape(generic_web_service.get('requestBody'))}"
                        )
                    if generic_web_service.get("parameterMapping"):
                        webhook_node.add(
                            f"[dim]Param Map:[/] "
                            f"{escape(str(generic_web_service.get('parameterMapping')))}"
                        )

        if not tools and not webhooks:
            root.add("[dim]No Tools or Webhooks configured.[/]")

        return root

    def visualize_topology(self):
        """Build and display the interactive High-Level Topology Graph."""
        dot_standard = HighLevelGraphVisualizer(self.data).build(
            show_code_blocks=False
        )
        dot_detailed = HighLevelGraphVisualizer(self.data).build(
            show_code_blocks=True
        )

        try:
            svg_std = dot_standard.pipe(format="svg").decode("utf-8")
            svg_std = svg_std[svg_std.find("<svg") :]

            svg_det = dot_detailed.pipe(format="svg").decode("utf-8")
            svg_det = svg_det[svg_det.find("<svg") :]

            uid = uuid.uuid4().hex

            html_content = f"""
            <div style="margin-bottom: 10px; padding: 8px; background: #f8f9fa;
                        border: 1px solid #dee2e6; border-radius: 4px;
                        display: flex; justify-content: space-between;
                        align-items: center;">
                <div>
                    <strong style="margin-right: 10px;
                            font-family: sans-serif;">
                        Zoom Controls:
                    </strong>
                    <button onclick="zoomIn_{uid}()"
                            style="padding: 6px 12px; margin-right: 5px;
                                   cursor: pointer; background: #e9ecef;
                                   border: 1px solid #ced4da;
                                   border-radius: 4px;">
                        ➕ In
                    </button>
                    <button onclick="zoomOut_{uid}()"
                            style="padding: 6px 12px; margin-right: 5px;
                                   cursor: pointer; background: #e9ecef;
                                   border: 1px solid #ced4da;
                                   border-radius: 4px;">
                        ➖ Out
                    </button>
                    <button onclick="resetZoom_{uid}()"
                            style="padding: 6px 12px; cursor: pointer;
                                   background: #e9ecef;
                                   border: 1px solid #ced4da;
                                   border-radius: 4px;">
                        🔄 Reset
                    </button>
                </div>
                <button id="btn_toggle_{uid}" onclick="toggleTools_{uid}()"
                        style="padding: 6px 12px; cursor: pointer;
                               background: #1976d2; color: white;
                               border: none; border-radius: 4px;
                                font-weight: bold;">
                    Show Detailed Code Blocks View
                </button>
            </div>
            <div style="overflow: auto; border: 1px solid #ccc;
                        max-height: 700px; width: 100%; background: white;">
                <div id="container_{uid}"
                     style="transform-origin: top left;
                            transition: transform 0.2s ease;
                            width: max-content; padding: 20px;">
                    <div id="svg_std_{uid}" style="display: block;">
                        {svg_std}
                    </div>
                    <div id="svg_det_{uid}" style="display: none;">
                        {svg_det}
                    </div>
                </div>
            </div>
            <script>
                var scale_{uid} = 1.0;
                var show_tools_{uid} = false;

                function zoomIn_{uid}() {{
                    scale_{uid} += 0.2;
                    document.getElementById(
                        'container_{uid}'
                    ).style.transform = 'scale(' + scale_{uid} + ')';
                }}
                function zoomOut_{uid}() {{
                    scale_{uid} -= 0.2;
                    if (scale_{uid} < 0.2) scale_{uid} = 0.2;
                    document.getElementById(
                        'container_{uid}'
                    ).style.transform = 'scale(' + scale_{uid} + ')';
                }}
                function resetZoom_{uid}() {{
                    scale_{uid} = 1.0;
                    document.getElementById(
                        'container_{uid}'
                    ).style.transform = 'scale(1.0)';
                }}
                function toggleTools_{uid}() {{
                    show_tools_{uid} = !show_tools_{uid};
                    if (show_tools_{uid}) {{
                        document.getElementById(
                            'svg_std_{uid}'
                        ).style.display = 'none';
                        document.getElementById(
                            'svg_det_{uid}'
                        ).style.display = 'block';
                        document.getElementById(
                            'btn_toggle_{uid}'
                        ).innerText = 'Hide Detailed Code Blocks View';
                        document.getElementById(
                            'btn_toggle_{uid}'
                        ).style.backgroundColor = '#d32f2f';
                    }} else {{
                        document.getElementById(
                            'svg_std_{uid}'
                        ).style.display = 'block';
                        document.getElementById(
                            'svg_det_{uid}'
                        ).style.display = 'none';
                        document.getElementById(
                            'btn_toggle_{uid}'
                        ).innerText = 'Show Detailed Code Blocks View';
                        document.getElementById(
                            'btn_toggle_{uid}'
                        ).style.backgroundColor = '#1976d2';
                    }}
                }}
            </script>
            """
            if HAS_IPYTHON:
                display(HTML(html_content))
            else:
                print(
                    "Interactive graph skipped (not in notebook). "
                    "Use export_visualizations() to save as SVG."
                )

        except Exception as e:
            print(f"Warning: Could not render interactive SVG. Error: {e}")
            if HAS_IPYTHON:
                display(dot_standard)
            else:
                print(
                    "Static image display skipped (not in notebook). "
                    "Use export_visualizations() to save as SVG."
                )

    def visualize_details(self):
        """Build and display Rich Trees for Playbooks, Flows, and Tools."""
        if HAS_IPYTHON:
            display(HTML("<h3>🛠️ Agent Tools &amp; Webhooks</h3>"))
        else:
            self.console.print("\n[bold orange3]🛠️ Agent Tools & Webhooks[/]\n")

        self.console.print(
            Panel(self._build_tools_tree(), border_style="orange3")
        )

        playbooks = self.data.get("playbooks", [])
        if playbooks:
            if HAS_IPYTHON:
                display(HTML("<hr><h3>📘 Selected Playbooks</h3>"))
            else:
                self.console.print("\n[bold blue]📘 Selected Playbooks[/]\n")

            for playbook_wrapper in playbooks:
                playbook = playbook_wrapper.get("playbook", playbook_wrapper)
                self.console.print(
                    Panel(
                        PlaybookTreeVisualizer(playbook).build_tree(),
                        border_style="blue",
                    )
                )

        flows = self.data.get("flows", [])
        if flows:
            if HAS_IPYTHON:
                display(HTML("<hr><h3>🔀 Selected Flows</h3>"))
            else:
                self.console.print("\n[bold magenta]🔀 Selected Flows[/]\n")

            resolver = FlowDependencyResolver(self.data)
            for flow_wrapper in flows:
                self.console.print(
                    Panel(
                        FlowTreeVisualizer(
                            resolver.resolve(flow_wrapper)
                        ).build_tree(),
                        border_style="magenta",
                    )
                )

    def export_visualizations(self, prefix: str = "agent"):
        """Export the topology graph as SVG and detailed trees as Markdown.

        Files are saved locally as ``{prefix}_topology.svg`` and
        ``{prefix}_detailed_resources.md``.  When running inside Google
        Colab the files are also automatically downloaded.

        Args:
            prefix: Filename prefix for exported files.
        """
        dot = HighLevelGraphVisualizer(self.data).build(show_code_blocks=False)
        svg_filename = f"{prefix}_topology.svg"
        dot.render(outfile=svg_filename, format="svg", cleanup=True)

        buf = io.StringIO()
        capture_console = Console(file=buf, force_terminal=False, width=120)

        capture_console.print("### Agent Tools & Webhooks ###\n")
        capture_console.print(
            Panel(self._build_tools_tree(), border_style="orange3")
        )

        playbooks = self.data.get("playbooks", [])
        if playbooks:
            capture_console.print("\n### Selected Playbooks ###\n")
            for playbook_wrapper in playbooks:
                playbook = playbook_wrapper.get("playbook", playbook_wrapper)
                capture_console.print(
                    Panel(
                        PlaybookTreeVisualizer(playbook).build_tree(),
                        border_style="blue",
                    )
                )

        flows = self.data.get("flows", [])
        if flows:
            capture_console.print("\n### Selected Flows ###\n")
            resolver = FlowDependencyResolver(self.data)
            for flow_wrapper in flows:
                capture_console.print(
                    Panel(
                        FlowTreeVisualizer(
                            resolver.resolve(flow_wrapper)
                        ).build_tree(),
                        border_style="magenta",
                    )
                )

        md_filename = f"{prefix}_detailed_resources.md"
        with open(md_filename, "w", encoding="utf-8") as md_file:
            md_file.write(buf.getvalue())

        if HAS_COLAB:
            files.download(svg_filename)
            files.download(md_filename)
        else:
            print(f"Files saved locally: {svg_filename}, {md_filename}")
