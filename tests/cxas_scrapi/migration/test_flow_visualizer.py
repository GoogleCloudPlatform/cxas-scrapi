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

"""Unit tests for FlowDependencyResolver and FlowTreeVisualizer."""

from rich.console import Console
from rich.tree import Tree

from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

INTENT_UUID = "intent-uuid-1"
WEBHOOK_UUID = "wh-uuid-1"
ENTITY_UUID = "entity-uuid-1"
FLOW_UUID = "flow-uuid-1"
PAGE_UUID = "page-uuid-1"

MINIMAL_AGENT_DATA = {
    "intents": [
        {
            "name": f"projects/p/locations/l/agents/a/intents/{INTENT_UUID}",
            "displayName": "confirm.yes",
        }
    ],
    "entityTypes": [
        {
            "name": (
                f"projects/p/locations/l/agents/a/entityTypes/{ENTITY_UUID}"
            ),
            "displayName": "YesNo",
        }
    ],
    "tools": [],
    "webhooks": [
        {
            "name": (
                f"projects/p/locations/l/agents/a/webhooks/{WEBHOOK_UUID}"
            ),
            "displayName": "MyWebhook",
            "genericWebService": {"uri": "https://example.com"},
        }
    ],
    "playbooks": [],
    "flows": [
        {
            "flow": {
                "name": (f"projects/p/locations/l/agents/a/flows/{FLOW_UUID}"),
                "displayName": "Main Flow",
            },
            "pages": [],
        }
    ],
}

FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK = {
    "flow": {
        "name": f"projects/p/locations/l/agents/a/flows/{FLOW_UUID}",
        "displayName": "Test Flow",
        "transitionRoutes": [
            {
                "intent": (
                    f"projects/p/locations/l/agents/a/intents/{INTENT_UUID}"
                ),
                "triggerFulfillment": {
                    "webhook": (
                        f"projects/p/locations/l/agents/a/"
                        f"webhooks/{WEBHOOK_UUID}"
                    )
                },
                "targetPage": (
                    f"projects/p/locations/l/agents/a/flows/"
                    f"{FLOW_UUID}/pages/{PAGE_UUID}"
                ),
            }
        ],
        "eventHandlers": [],
    },
    "pages": [
        {
            "key": PAGE_UUID,
            "value": {
                "displayName": "Collect Info",
                "transitionRoutes": [],
                "eventHandlers": [],
            },
        }
    ],
}

CONVERSATIONAL_FLOW_WRAPPER = {
    "flow": {
        "name": f"projects/p/locations/l/agents/a/flows/{FLOW_UUID}",
        "displayName": "Conversational Flow",
        "transitionRoutes": [],
        "eventHandlers": [],
        "messages": [{"text": {"text": ["Hello!"]}}],
    },
    "pages": [],
}

LOGIC_FLOW_WRAPPER = {
    "flow": {
        "name": f"projects/p/locations/l/agents/a/flows/{FLOW_UUID}",
        "displayName": "Logic Flow",
        "transitionRoutes": [
            {
                "condition": "$session.params.flag = true",
                "conditionString": "$session.params.flag = true",
                "targetFlow": (
                    "projects/p/locations/l/agents/a/flows/other-flow"
                ),
            }
        ],
        "eventHandlers": [],
    },
    "pages": [],
}


# ---------------------------------------------------------------------------
# FlowDependencyResolver tests
# ---------------------------------------------------------------------------


class TestFlowDependencyResolver:
    def setup_method(self):
        self.resolver = FlowDependencyResolver(MINIMAL_AGENT_DATA)

    def test_collects_intent_from_transition_route(self):
        result = self.resolver.resolve(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        intent_names = [i.get("displayName") for i in result["intents"]]
        assert "confirm.yes" in intent_names

    def test_collects_webhook_from_fulfillment(self):
        result = self.resolver.resolve(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        wh_names = [w.get("displayName") for w in result["webhooks"]]
        assert "MyWebhook" in wh_names

    def test_flow_type_1_for_logic_flow(self):
        result = self.resolver.resolve(LOGIC_FLOW_WRAPPER)
        assert result["flow_type"] == 1

    def test_flow_type_2_for_conversational_flow(self):
        result = self.resolver.resolve(CONVERSATIONAL_FLOW_WRAPPER)
        assert result["flow_type"] == 2

    def test_pages_preserved_in_result(self):
        result = self.resolver.resolve(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        assert len(result["pages"]) == 1
        assert result["pages"][0]["key"] == PAGE_UUID

    def test_name_map_contains_flow(self):
        result = self.resolver.resolve(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        assert FLOW_UUID in result["name_map"]
        assert result["name_map"][FLOW_UUID] == "Main Flow"

    def test_no_intents_when_no_routes(self):
        result = self.resolver.resolve(LOGIC_FLOW_WRAPPER)
        assert result["intents"] == []

    def test_webhook_lookup_by_display_name(self):
        """Webhooks referenced by displayName (not UUID) should be resolved."""
        agent_data = dict(MINIMAL_AGENT_DATA)
        flow_wrapper = {
            "flow": {
                "name": f"projects/p/l/a/flows/{FLOW_UUID}",
                "displayName": "Test",
                "transitionRoutes": [
                    {
                        "triggerFulfillment": {
                            "webhook": "MyWebhook"  # display name, not UUID
                        }
                    }
                ],
                "eventHandlers": [],
            },
            "pages": [],
        }
        resolver = FlowDependencyResolver(agent_data)
        result = resolver.resolve(flow_wrapper)
        assert len(result["webhooks"]) == 1

    def test_entity_collected_from_form_parameter(self):
        agent_data = {
            "intents": [],
            "entityTypes": [
                {
                    "name": (f"projects/p/l/a/entityTypes/{ENTITY_UUID}"),
                    "displayName": "YesNo",
                }
            ],
            "tools": [],
            "webhooks": [],
            "playbooks": [],
            "flows": [],
        }
        flow_with_form = {
            "flow": {
                "name": "projects/p/l/a/flows/f1",
                "displayName": "Form Flow",
                "transitionRoutes": [],
                "eventHandlers": [],
            },
            "pages": [
                {
                    "key": PAGE_UUID,
                    "value": {
                        "displayName": "Slot Page",
                        "form": {
                            "parameters": [
                                {
                                    "displayName": "answer",
                                    "entityType": (
                                        f"projects/p/l/a/"
                                        f"entityTypes/{ENTITY_UUID}"
                                    ),
                                }
                            ]
                        },
                        "transitionRoutes": [],
                        "eventHandlers": [],
                    },
                }
            ],
        }
        resolver = FlowDependencyResolver(agent_data)
        result = resolver.resolve(flow_with_form)
        entity_names = [e.get("displayName") for e in result["entityTypes"]]
        assert "YesNo" in entity_names


# ---------------------------------------------------------------------------
# FlowTreeVisualizer tests
# ---------------------------------------------------------------------------


def _render_tree_to_str(tree: Tree) -> str:
    """Render a Rich Tree to a plain string for assertion."""
    console = Console(force_terminal=False, width=200, record=True)
    console.print(tree)
    return console.export_text()


class TestFlowTreeVisualizer:
    def _make_context(self, flow_wrapper=None, extra=None):
        """Build a resolved context dict for the visualizer."""
        agent_data = dict(MINIMAL_AGENT_DATA)
        resolver = FlowDependencyResolver(agent_data)
        wrapper = flow_wrapper or FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK
        ctx = resolver.resolve(wrapper)
        if extra:
            ctx.update(extra)
        return ctx

    def test_build_tree_returns_tree_instance(self):
        ctx = self._make_context()
        tree = FlowTreeVisualizer(ctx).build_tree()
        assert isinstance(tree, Tree)

    def test_build_tree_root_contains_flow_name(self):
        ctx = self._make_context()
        tree = FlowTreeVisualizer(ctx).build_tree()
        rendered = _render_tree_to_str(tree)
        assert "Test Flow" in rendered

    def test_type1_label_present_for_logic_flow(self):
        ctx = self._make_context(LOGIC_FLOW_WRAPPER)
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "TYPE 1" in rendered

    def test_type2_label_present_for_conversational_flow(self):
        ctx = self._make_context(CONVERSATIONAL_FLOW_WRAPPER)
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "TYPE 2" in rendered

    def test_page_name_appears_in_tree(self):
        ctx = self._make_context(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "Collect Info" in rendered

    def test_intent_display_in_route(self):
        ctx = self._make_context(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "confirm.yes" in rendered

    def test_webhook_displayed_in_fulfillment(self):
        ctx = self._make_context(FLOW_WRAPPER_WITH_INTENT_AND_WEBHOOK)
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "MyWebhook" in rendered

    def test_event_handler_rendered(self):
        flow_with_event = {
            "flow": {
                "name": "projects/p/l/a/flows/f1",
                "displayName": "Event Flow",
                "transitionRoutes": [],
                "eventHandlers": [
                    {
                        "event": "sys.no-match-1",
                        "triggerFulfillment": {},
                    }
                ],
            },
            "pages": [],
        }
        ctx = FlowDependencyResolver(MINIMAL_AGENT_DATA).resolve(
            flow_with_event
        )
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "sys.no-match-1" in rendered

    def test_set_parameter_action_rendered(self):
        flow_with_set_param = {
            "flow": {
                "name": "projects/p/l/a/flows/f1",
                "displayName": "Param Flow",
                "transitionRoutes": [
                    {
                        "condition": "true",
                        "triggerFulfillment": {
                            "setParameterActions": [
                                {"parameter": "myParam", "value": "hello"}
                            ]
                        },
                    }
                ],
                "eventHandlers": [],
            },
            "pages": [],
        }
        ctx = FlowDependencyResolver(MINIMAL_AGENT_DATA).resolve(
            flow_with_set_param
        )
        rendered = _render_tree_to_str(FlowTreeVisualizer(ctx).build_tree())
        assert "myParam" in rendered
        assert "hello" in rendered

    def test_empty_flow_builds_without_error(self):
        empty_flow = {
            "flow": {
                "name": "projects/p/l/a/flows/f1",
                "displayName": "Empty",
                "transitionRoutes": [],
                "eventHandlers": [],
            },
            "pages": [],
        }
        ctx = FlowDependencyResolver(MINIMAL_AGENT_DATA).resolve(empty_flow)
        tree = FlowTreeVisualizer(ctx).build_tree()
        assert isinstance(tree, Tree)
