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

"""Unit tests for HighLevelGraphVisualizer."""

import graphviz

from cxas_scrapi.migration.graph_visualizer import HighLevelGraphVisualizer

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PB_UUID = "pb-uuid-1"
FLOW_UUID = "flow-uuid-1"
TOOL_UUID = "tool-uuid-1"
WEBHOOK_UUID = "wh-uuid-1"

MINIMAL_DATA: dict = {
    "agent": {},
    "playbooks": [],
    "flows": [],
    "tools": [],
    "webhooks": [],
    "intents": [],
}

DATA_WITH_PLAYBOOK = {
    "agent": {"startPlaybook": f"projects/p/l/a/playbooks/{PB_UUID}"},
    "playbooks": [
        {
            "playbook": {
                "name": f"projects/p/l/a/playbooks/{PB_UUID}",
                "displayName": "Welcome Playbook",
                "playbookRoutes": [],
                "referencedTools": [],
                "instruction": {"steps": []},
            }
        }
    ],
    "flows": [],
    "tools": [],
    "webhooks": [],
    "intents": [],
}

DATA_WITH_FLOW = {
    "agent": {"startFlow": f"projects/p/l/a/flows/{FLOW_UUID}"},
    "playbooks": [],
    "flows": [
        {
            "flow_data": {
                "name": f"projects/p/l/a/flows/{FLOW_UUID}",
                "displayName": "Main Flow",
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

DATA_WITH_PLAYBOOK_TO_FLOW = {
    "agent": {"startPlaybook": f"projects/p/l/a/playbooks/{PB_UUID}"},
    "playbooks": [
        {
            "playbook": {
                "name": f"projects/p/l/a/playbooks/{PB_UUID}",
                "displayName": "Root PB",
                "playbookRoutes": [],
                "flowRoutes": [{"flowId": FLOW_UUID}],
                "referencedTools": [],
                "instruction": {"steps": []},
            }
        }
    ],
    "flows": [
        {
            "flow_data": {
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHighLevelGraphVisualizer:
    def test_build_returns_digraph(self):
        dot = HighLevelGraphVisualizer(MINIMAL_DATA).build()
        assert isinstance(dot, graphviz.Digraph)

    def test_empty_data_builds_without_error(self):
        dot = HighLevelGraphVisualizer(MINIMAL_DATA).build()
        src = dot.source
        assert "ENTRY_MARKER" in src

    def test_playbook_node_in_source(self):
        dot = HighLevelGraphVisualizer(DATA_WITH_PLAYBOOK).build()
        assert "Welcome Playbook" in dot.source

    def test_flow_node_in_source(self):
        dot = HighLevelGraphVisualizer(DATA_WITH_FLOW).build()
        assert "Main Flow" in dot.source

    def test_entry_marker_always_present(self):
        for data in [MINIMAL_DATA, DATA_WITH_PLAYBOOK, DATA_WITH_FLOW]:
            dot = HighLevelGraphVisualizer(data).build()
            assert "ENTRY POINT" in dot.source

    def test_entry_point_edge_connects_to_start_node(self):
        dot = HighLevelGraphVisualizer(DATA_WITH_PLAYBOOK).build()
        src = dot.source
        # The ENTRY_MARKER must have an edge to the playbook uuid
        assert "ENTRY_MARKER" in src
        assert PB_UUID in src

    def test_flow_route_creates_edge(self):
        dot = HighLevelGraphVisualizer(DATA_WITH_PLAYBOOK_TO_FLOW).build()
        src = dot.source
        # Both nodes must appear
        assert PB_UUID in src
        assert FLOW_UUID in src

    def test_tool_node_rendered_as_dashed(self):
        data = {
            "agent": {"startPlaybook": f"projects/p/l/a/playbooks/{PB_UUID}"},
            "playbooks": [
                {
                    "playbook": {
                        "name": f"projects/p/l/a/playbooks/{PB_UUID}",
                        "displayName": "Root PB",
                        "playbookRoutes": [],
                        "flowRoutes": [],
                        "referencedTools": [
                            {"name": f"projects/p/l/a/tools/{TOOL_UUID}"}
                        ],
                        "instruction": {"steps": []},
                    }
                }
            ],
            "flows": [],
            "tools": [
                {
                    "name": f"projects/p/l/a/tools/{TOOL_UUID}",
                    "displayName": "MyTool",
                }
            ],
            "webhooks": [],
            "intents": [],
        }
        dot = HighLevelGraphVisualizer(data).build()
        assert "dashed" in dot.source
        assert "MyTool" in dot.source

    def test_end_session_node_added_when_referenced(self):
        data = {
            "agent": {"startFlow": f"projects/p/l/a/flows/{FLOW_UUID}"},
            "playbooks": [],
            "flows": [
                {
                    "flow_data": {
                        "name": f"projects/p/l/a/flows/{FLOW_UUID}",
                        "displayName": "End Flow",
                        "transitionRoutes": [
                            {
                                "condition": "true",
                                "targetPage": (
                                    "projects/p/l/a/flows/f/pages/END_SESSION"
                                ),
                            }
                        ],
                        "eventHandlers": [],
                    },
                    "pages": [],
                }
            ],
            "tools": [],
            "webhooks": [],
            "intents": [],
        }
        dot = HighLevelGraphVisualizer(data).build()
        assert "END SESSION" in dot.source

    def test_show_code_blocks_adds_inline_functions(self):
        data = {
            "agent": {"startPlaybook": f"projects/p/l/a/playbooks/{PB_UUID}"},
            "playbooks": [
                {
                    "playbook": {
                        "name": f"projects/p/l/a/playbooks/{PB_UUID}",
                        "displayName": "Code PB",
                        "playbookRoutes": [],
                        "flowRoutes": [],
                        "referencedTools": [],
                        "instruction": {"steps": []},
                        "codeBlock": {"code": "def my_helper():\n    pass\n"},
                    }
                }
            ],
            "flows": [],
            "tools": [],
            "webhooks": [],
            "intents": [],
        }
        dot_no_blocks = HighLevelGraphVisualizer(data).build(
            show_code_blocks=False
        )
        dot_with_blocks = HighLevelGraphVisualizer(data).build(
            show_code_blocks=True
        )
        assert "my_helper" not in dot_no_blocks.source
        assert "my_helper" in dot_with_blocks.source

    def test_edge_condition_deduplication(self):
        """Multiple routes to the same target should accumulate conditions."""
        viz = HighLevelGraphVisualizer(MINIMAL_DATA)
        viz.edges_accumulator = {}
        viz._accumulate_edge("A", "B", "routes to", condition="cond1")
        viz._accumulate_edge("A", "B", "routes to", condition="cond2")
        viz._accumulate_edge("A", "B", "routes to", condition="cond1")
        key = ("A", "B", "routes to", False)
        assert viz.edges_accumulator[key] == ["cond1", "cond2"]

    def test_resolve_to_uuid_handles_end_session_variants(self):
        viz = HighLevelGraphVisualizer(MINIMAL_DATA)
        assert viz._resolve_to_uuid("END SESSION") == "END_SESSION"
        assert viz._resolve_to_uuid("END_FLOW") == "END_SESSION"

    def test_resolve_to_uuid_by_display_name(self):
        viz = HighLevelGraphVisualizer(DATA_WITH_PLAYBOOK)
        assert viz._resolve_to_uuid("Welcome Playbook") == PB_UUID

    def test_webhook_in_flow_fulfillment_rendered(self):
        data = {
            "agent": {"startFlow": f"projects/p/l/a/flows/{FLOW_UUID}"},
            "playbooks": [],
            "flows": [
                {
                    "flow_data": {
                        "name": f"projects/p/l/a/flows/{FLOW_UUID}",
                        "displayName": "WH Flow",
                        "transitionRoutes": [
                            {
                                "condition": "true",
                                "triggerFulfillment": {
                                    "webhook": (
                                        f"projects/p/l/a/"
                                        f"webhooks/{WEBHOOK_UUID}"
                                    )
                                },
                            }
                        ],
                        "eventHandlers": [],
                    },
                    "pages": [],
                }
            ],
            "tools": [],
            "webhooks": [
                {
                    "name": (f"projects/p/l/a/webhooks/{WEBHOOK_UUID}"),
                    "displayName": "BackendWebhook",
                }
            ],
            "intents": [],
        }
        dot = HighLevelGraphVisualizer(data).build()
        assert "BackendWebhook" in dot.source
        assert "dashed" in dot.source
