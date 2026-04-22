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

"""Unit tests for MainVisualizer."""

import os
from unittest.mock import MagicMock, patch

from rich.console import Console
from rich.tree import Tree

from cxas_scrapi.migration.main_visualizer import MainVisualizer

# ---------------------------------------------------------------------------
# Shared data
# ---------------------------------------------------------------------------

PB_UUID = "pb-uuid-1"
FLOW_UUID = "flow-uuid-1"

DATA_WITH_TOOLS = {
    "agent": {},
    "playbooks": [],
    "flows": [],
    "tools": [
        {
            "tool": {
                "displayName": "SearchTool",
                "description": "Searches the web.",
                "openApiSpec": {"textSchema": "openapi: 3.0\n"},
            }
        }
    ],
    "webhooks": [
        {
            "value": {
                "displayName": "AuthWebhook",
                "genericWebService": {
                    "uri": "https://auth.example.com",
                    "webhookType": "STANDARD",
                    "httpMethod": "POST",
                },
            }
        }
    ],
    "intents": [],
}

DATA_WITH_PLAYBOOK_AND_FLOW = {
    "agent": {"startPlaybook": f"projects/p/l/a/playbooks/{PB_UUID}"},
    "playbooks": [
        {
            "playbook": {
                "name": f"projects/p/l/a/playbooks/{PB_UUID}",
                "displayName": "Root PB",
                "goal": "Handle everything",
                "playbookRoutes": [],
                "flowRoutes": [],
                "referencedTools": [],
                "instruction": {"steps": [{"text": "Do the thing."}]},
            }
        }
    ],
    "flows": [
        {
            "flow": {
                "name": f"projects/p/l/a/flows/{FLOW_UUID}",
                "displayName": "Sub Flow",
                "transitionRoutes": [],
                "eventHandlers": [],
            },
            "pages": [],
        }
    ],
    "tools": [],
    "webhooks": [],
    "intents": [],
}

EMPTY_DATA = {
    "agent": {},
    "playbooks": [],
    "flows": [],
    "tools": [],
    "webhooks": [],
    "intents": [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_dot():
    """Return a mock graphviz Digraph that produces dummy SVG bytes."""
    mock_dot = MagicMock()
    mock_dot.source = "digraph {}"
    mock_dot.pipe.return_value = b"<svg><rect/></svg>"
    return mock_dot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMainVisualizerToolsTree:
    def test_tools_tree_is_tree_instance(self):
        mv = MainVisualizer(DATA_WITH_TOOLS)
        tree = mv._build_tools_tree()
        assert isinstance(tree, Tree)

    def test_tool_name_in_tree(self):
        mv = MainVisualizer(DATA_WITH_TOOLS)
        c = Console(force_terminal=False, width=200, record=True)
        c.print(mv._build_tools_tree())
        rendered = c.export_text()
        assert "SearchTool" in rendered

    def test_webhook_name_in_tree(self):
        mv = MainVisualizer(DATA_WITH_TOOLS)
        c = Console(force_terminal=False, width=200, record=True)
        c.print(mv._build_tools_tree())
        rendered = c.export_text()
        assert "AuthWebhook" in rendered

    def test_webhook_uri_in_tree(self):
        mv = MainVisualizer(DATA_WITH_TOOLS)
        c = Console(force_terminal=False, width=200, record=True)
        c.print(mv._build_tools_tree())
        rendered = c.export_text()
        assert "auth.example.com" in rendered

    def test_no_tools_message_when_empty(self):
        mv = MainVisualizer(EMPTY_DATA)
        c = Console(force_terminal=False, width=200, record=True)
        c.print(mv._build_tools_tree())
        rendered = c.export_text()
        assert "No Tools or Webhooks" in rendered


class TestMainVisualizerTopology:
    @patch("cxas_scrapi.migration.main_visualizer.display")
    @patch(
        "cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer.build"
    )
    def test_visualize_topology_calls_display(self, mock_build, mock_display):
        mock_build.return_value = _make_mock_dot()
        mv = MainVisualizer(EMPTY_DATA)
        mv.visualize_topology()
        assert mock_display.called

    @patch("cxas_scrapi.migration.main_visualizer.display")
    @patch(
        "cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer.build"
    )
    def test_visualize_topology_fallback_on_pipe_error(
        self, mock_build, mock_display
    ):
        """When pipe() raises, the fallback path displays the Digraph."""
        mock_dot = MagicMock()
        mock_dot.pipe.side_effect = Exception("graphviz binary not found")
        mock_build.return_value = mock_dot
        mv = MainVisualizer(EMPTY_DATA)
        mv.visualize_topology()
        # display() should still be called (fallback branch)
        assert mock_display.called


class TestMainVisualizerDetails:
    @patch("cxas_scrapi.migration.main_visualizer.display")
    def test_visualize_details_no_error_with_empty_data(self, mock_display):
        mv = MainVisualizer(EMPTY_DATA)
        mv.visualize_details()

    @patch("cxas_scrapi.migration.main_visualizer.display")
    def test_visualize_details_renders_playbook(self, mock_display):
        mv = MainVisualizer(DATA_WITH_PLAYBOOK_AND_FLOW)
        mv.visualize_details()
        # Console output captured internally; just ensure no exception

    @patch("cxas_scrapi.migration.main_visualizer.display")
    def test_visualize_details_renders_flow(self, mock_display):
        mv = MainVisualizer(DATA_WITH_PLAYBOOK_AND_FLOW)
        mv.visualize_details()


class TestMainVisualizerExport:
    @patch("cxas_scrapi.migration.main_visualizer.display")
    @patch(
        "cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer.build"
    )
    def test_export_writes_md_file(self, mock_build, mock_display, tmp_path):
        mock_dot = MagicMock()
        mock_dot.render = MagicMock()
        mock_build.return_value = mock_dot

        prefix = str(tmp_path / "test_agent")
        mv = MainVisualizer(EMPTY_DATA)
        mv.export_visualizations(prefix=prefix)

        md_file = f"{prefix}_detailed_resources.md"
        assert os.path.exists(md_file)

    @patch("cxas_scrapi.migration.main_visualizer.display")
    @patch(
        "cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer.build"
    )
    def test_export_calls_render_on_graph(
        self, mock_build, mock_display, tmp_path
    ):
        mock_dot = MagicMock()
        mock_build.return_value = mock_dot

        prefix = str(tmp_path / "test_agent")
        mv = MainVisualizer(EMPTY_DATA)
        mv.export_visualizations(prefix=prefix)

        mock_dot.render.assert_called_once()

    @patch("cxas_scrapi.migration.main_visualizer.display")
    @patch(
        "cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer.build"
    )
    def test_export_md_contains_tools_section(
        self, mock_build, mock_display, tmp_path
    ):
        mock_dot = MagicMock()
        mock_build.return_value = mock_dot

        prefix = str(tmp_path / "test_agent")
        mv = MainVisualizer(DATA_WITH_TOOLS)
        mv.export_visualizations(prefix=prefix)

        md_file = f"{prefix}_detailed_resources.md"
        content = open(md_file).read()
        assert "Agent Tools" in content
