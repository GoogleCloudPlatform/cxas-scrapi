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

"""Flow-level visualizers: dependency resolution and Rich tree rendering."""

from typing import Any, Dict, List

from rich.markup import escape
from rich.tree import Tree


class FlowDependencyResolver:
    """Traverses a specific Flow wrapper to find all related dependencies."""

    def __init__(self, full_agent_data: Dict[str, Any]):
        self.full_data = full_agent_data

        self.intents = {
            self._get_resource_id(intent): intent
            for intent in full_agent_data.get("intents", [])
        }
        self.entities = {
            self._get_resource_id(entity): entity
            for entity in full_agent_data.get("entityTypes", [])
        }
        self.tools = {
            self._get_resource_id(tool): tool
            for tool in full_agent_data.get("tools", [])
        }

        # Map webhooks by both UUID and DisplayName to handle DFCX export
        # inconsistencies where either form may appear in fulfillment refs.
        self.webhooks: Dict[str, Any] = {}
        for webhook_entry in full_agent_data.get("webhooks", []):
            webhook_data = (
                webhook_entry.get("value", webhook_entry)
                if isinstance(webhook_entry, dict) and "value" in webhook_entry
                else webhook_entry
            )
            uuid_id = self._get_resource_id(webhook_data)
            display_name = webhook_data.get("displayName")
            self.webhooks[uuid_id] = webhook_data
            if display_name:
                self.webhooks[display_name] = webhook_data

        self.name_map: Dict[str, str] = {}
        for playbook_entry in full_agent_data.get("playbooks", []):
            playbook_data = playbook_entry.get("playbook", playbook_entry)
            self.name_map[
                self._get_resource_id(
                    playbook_data.get("name")
                    or playbook_entry.get("playbookId")
                )
            ] = playbook_data.get("displayName", "Unknown")
        for flow_entry in full_agent_data.get("flows", []):
            flow_entry_data = flow_entry.get("flow", flow_entry)
            self.name_map[
                self._get_resource_id(
                    flow_entry_data.get("name") or flow_entry.get("flowId")
                )
            ] = flow_entry_data.get("displayName", "Unknown")

    @staticmethod
    def _get_resource_id(resource_name_or_dict) -> str:
        """Extract the last path segment (UUID) from a resource name or dict."""
        if isinstance(resource_name_or_dict, dict):
            return resource_name_or_dict.get("name", "").split("/")[-1]
        return str(resource_name_or_dict or "").split("/")[-1]

    @staticmethod
    def _is_conversational_flow(obj: Any) -> bool:
        """Recursively check whether ``obj`` contains conversational elements.

        Returns True if any dict key matches a known conversational field
        (e.g. ``intent``, ``messages``, ``form``), indicating a Type 2 flow.
        """
        conversational_keys = {
            "intent",
            "triggerIntentId",
            "messages",
            "staticUserResponse",
            "form",
            "slots",
        }
        if isinstance(obj, dict):
            if any(key in obj for key in conversational_keys):
                return True
            return any(
                FlowDependencyResolver._is_conversational_flow(value)
                for value in obj.values()
            )
        if isinstance(obj, list):
            return any(
                FlowDependencyResolver._is_conversational_flow(item)
                for item in obj
            )
        return False

    def _scan_fulfillment(
        self, fulfillment: Dict[str, Any], dependencies: Dict[str, Any]
    ) -> None:
        """Collect webhook references from a single fulfillment object."""
        if not fulfillment:
            return
        if "beforeTransition" in fulfillment:
            fulfillment = fulfillment["beforeTransition"]
        if "webhook" in fulfillment:
            webhook_id = self._get_resource_id(fulfillment["webhook"])
            if webhook_id in self.webhooks:
                dependencies["webhooks"][webhook_id] = self.webhooks[webhook_id]
        if (
            "function" in fulfillment
            and "webhookFulfillmentId" in (fulfillment["function"])
        ):
            webhook_id = self._get_resource_id(
                fulfillment["function"]["webhookFulfillmentId"]
            )
            if webhook_id in self.webhooks:
                dependencies["webhooks"][webhook_id] = self.webhooks[webhook_id]

    def _scan_routes(
        self, routes: List[Dict[str, Any]], dependencies: Dict[str, Any]
    ) -> None:
        """Collect intent and fulfillment references from transition routes."""
        for route in routes:
            if "intent" in route:
                intent_id = self._get_resource_id(route["intent"])
                if intent_id in self.intents:
                    dependencies["intents"][intent_id] = self.intents[intent_id]
            elif "triggerIntentId" in route:
                intent_id = self._get_resource_id(route["triggerIntentId"])
                if intent_id in self.intents:
                    dependencies["intents"][intent_id] = self.intents[intent_id]
            self._scan_fulfillment(
                route.get("triggerFulfillment")
                or route.get("transitionEventHandler"),
                dependencies,
            )

    def _scan_event_handlers(
        self, handlers: List[Dict[str, Any]], dependencies: Dict[str, Any]
    ) -> None:
        """Collect fulfillment references from event handlers."""
        for handler in handlers:
            self._scan_fulfillment(
                handler.get("triggerFulfillment") or handler.get("handler"),
                dependencies,
            )

    def resolve(self, flow_wrapper: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve all dependencies for a single flow wrapper.

        Args:
            flow_wrapper: A dict containing ``flow`` and ``pages`` keys as
                exported by the DFCX API.

        Returns:
            A dependency dict with ``flow``, ``pages``, ``intents``,
            ``entityTypes``, ``webhooks``, ``tools``, ``name_map``, and
            ``flow_type`` (1 = logic flow, 2 = conversational flow).
        """
        flow_data = flow_wrapper.get("flow", flow_wrapper)
        pages_data = flow_wrapper.get("pages", [])

        dependencies: Dict[str, Any] = {
            "flow": flow_data,
            "pages": pages_data,
            "intents": {},
            "entityTypes": {},
            "webhooks": {},
            "tools": {},
            "name_map": self.name_map,
            "flow_type": 1,
        }

        if self._is_conversational_flow(
            flow_data
        ) or self._is_conversational_flow(pages_data):
            dependencies["flow_type"] = 2

        self._scan_routes(
            flow_data.get("transitionRoutes", [])
            + flow_data.get("transitionEvents", []),
            dependencies,
        )
        self._scan_event_handlers(
            flow_data.get("eventHandlers", [])
            + flow_data.get("conversationEvents", []),
            dependencies,
        )

        for page_wrapper in pages_data:
            page = page_wrapper.get("value", page_wrapper)
            self._scan_fulfillment(
                page.get("entryFulfillment") or page.get("onLoad"),
                dependencies,
            )
            self._scan_routes(
                page.get("transitionRoutes", [])
                + page.get("transitionEvents", []),
                dependencies,
            )
            self._scan_event_handlers(
                page.get("eventHandlers", [])
                + page.get("conversationEvents", []),
                dependencies,
            )

            if "form" in page or "slots" in page:
                params = page.get("form", {}).get("parameters", []) + page.get(
                    "slots", []
                )
                for param in params:
                    entity_type_id = self._get_resource_id(
                        param.get("entityType")
                        or param.get("type", {}).get("className")
                    )
                    if entity_type_id in self.entities:
                        dependencies["entityTypes"][entity_type_id] = (
                            self.entities[entity_type_id]
                        )
                    if "fillBehavior" in param:
                        self._scan_fulfillment(
                            param["fillBehavior"].get(
                                "initialPromptFulfillment"
                            )
                            or param["fillBehavior"].get("initialPrompt"),
                            dependencies,
                        )
                        self._scan_event_handlers(
                            param["fillBehavior"].get(
                                "repromptEventHandlers", []
                            ),
                            dependencies,
                        )

        return {
            key: (
                list(value.values())
                if isinstance(value, dict)
                and key not in ["flow", "pages", "name_map"]
                else value
            )
            for key, value in dependencies.items()
        }


class FlowTreeVisualizer:
    """Generates a detailed Rich Tree for a single resolved Flow context."""

    def __init__(self, context_data: Dict[str, Any]):
        self.context = context_data
        self.flow = context_data["flow"]
        self.page_names: Dict[str, str] = {}
        for page_wrapper in self.context.get("pages", []):
            if "key" in page_wrapper and "displayName" in page_wrapper.get(
                "value", {}
            ):
                self.page_names[page_wrapper["key"]] = page_wrapper["value"][
                    "displayName"
                ]

    def _get_id(self, resource_name: str) -> str:
        return (
            resource_name.rsplit("/", maxsplit=1)[-1] if resource_name else ""
        )

    def _get_intent_display(self, intent_ref: str) -> str:
        intent_id = self._get_id(intent_ref)
        for intent in self.context["intents"]:
            if (
                self._get_id(
                    intent.get("name") or intent.get("meta", {}).get("id")
                )
                == intent_id
            ):
                return intent.get("displayName") or intent.get("meta", {}).get(
                    "displayName", intent_id
                )
        return f"ID:{intent_id}"

    def _get_target_display(self, route: Dict[str, Any]) -> str:
        handler = route.get("transitionEventHandler", route)
        target_page = handler.get("targetPageId") or handler.get("targetPage")
        if target_page:
            page_id = target_page.split("/")[-1]
            return (
                f"[cyan]GOTO Page: {self.page_names.get(page_id, page_id)}[/]"
            )

        target_flow = handler.get("targetFlowId") or handler.get("targetFlow")
        if target_flow:
            flow_id = target_flow.split("/")[-1]
            return (
                f"[bold magenta]GOTO Flow: "
                f"{self.context['name_map'].get(flow_id, flow_id)}[/]"
            )

        target_playbook = handler.get("targetPlaybookId") or handler.get(
            "targetPlaybook"
        )
        if target_playbook:
            playbook_id = target_playbook.split("/")[-1]
            return (
                f"[bold blue]GOTO Playbook: "
                f"{self.context['name_map'].get(playbook_id, playbook_id)}[/]"
            )

        if handler.get("triggerFulfillment") or handler.get("beforeTransition"):
            return "[dim]Stay on Page[/]"
        return "[red]End Flow[/]"

    def _render_fulfillment(self, node, fulfillment, label="Action"):
        if not fulfillment:
            return
        if "beforeTransition" in fulfillment:
            fulfillment = fulfillment["beforeTransition"]

        if "messages" in fulfillment:
            for message in fulfillment["messages"]:
                if "text" in message:
                    node.add(
                        f"🗣️ [green]Say:[/] "
                        f"{escape(' '.join(message['text'].get('text', [])))}"
                    )
        elif "staticUserResponse" in fulfillment:
            for candidate in fulfillment["staticUserResponse"].get(
                "candidates", []
            ):
                for response in candidate.get("responses", []):
                    if "text" in response and "variants" in response["text"]:
                        for variant in response["text"]["variants"]:
                            node.add(
                                f"🗣️ [green]Say:[/] "
                                f"{escape(variant.get('text', ''))}"
                            )

        wh_ref = fulfillment.get("webhook") or fulfillment.get(
            "function", {}
        ).get("webhookFulfillmentId")
        if wh_ref:
            wh_id = self._get_id(wh_ref)
            wh_def = None
            for webhook in self.context.get("webhooks", []):
                webhook_value = (
                    webhook.get("value", webhook)
                    if isinstance(webhook, dict) and "value" in webhook
                    else webhook
                )
                if (
                    self._get_id(webhook_value.get("name", "")) == wh_id
                    or webhook_value.get("displayName") == wh_ref
                ):
                    wh_def = webhook_value
                    break

            tag = fulfillment.get("tag") or fulfillment.get("function", {}).get(
                "name", ""
            )
            wh_display_name = (
                wh_def.get("displayName", wh_id) if wh_def else wh_id
            )
            tag_display = f" ({tag})" if tag else ""
            node.add(
                f"⚡ [bold red]Webhook/CodeBlock:[/] "
                f"{wh_display_name}{tag_display}"
            )

            if wh_def:
                gen_ws = wh_def.get("genericWebService", {})
                if gen_ws.get("webhookType") == "FLEXIBLE":
                    if "requestBody" in gen_ws:
                        node.add(
                            f"   [dim]Request Body:[/] "
                            f"{escape(str(gen_ws['requestBody']))}"
                        )
                    if "parameterMapping" in gen_ws:
                        node.add(
                            f"   [dim]Response Map:[/] "
                            f"{escape(str(gen_ws['parameterMapping']))}"
                        )

        if "setParameterActions" in fulfillment:
            for action in fulfillment["setParameterActions"]:
                node.add(
                    f"📝 [blue]Set Param:[/] {action.get('parameter')} = "
                    f"{escape(str(action.get('value', '')))}"
                )

    def _render_routes(self, parent_node, routes):
        if not routes:
            return
        for route in routes:
            intent_ref = route.get("intent") or route.get("triggerIntentId")
            if intent_ref:
                trigger = (
                    f"Intent: [yellow]{self._get_intent_display(intent_ref)}[/]"
                )
            elif "condition" in route:
                cond_str = route.get("conditionString", str(route["condition"]))
                trigger = f"If: [dim]{escape(cond_str)}[/]"
            else:
                trigger = "Always"

            route_node = parent_node.add(
                f"{trigger} -> {self._get_target_display(route)}"
            )
            self._render_fulfillment(
                route_node,
                route.get("triggerFulfillment")
                or route.get("transitionEventHandler"),
            )

    def _render_events(self, parent_node, handlers, label="Event"):
        if not handlers:
            return
        for handler in handlers:
            evt_node = parent_node.add(
                f"⚡ [bold red]{label}: {handler.get('event', 'Unknown')}[/]"
            )
            self._render_fulfillment(
                evt_node,
                handler.get("triggerFulfillment") or handler.get("handler"),
            )
            if any(
                k in handler
                for k in [
                    "targetPage",
                    "targetPageId",
                    "targetFlow",
                    "targetFlowId",
                ]
            ):
                evt_node.add(f"-> {self._get_target_display(handler)}")

    def build_tree(self) -> Tree:
        """Build and return the Rich Tree for this flow."""
        flow_type_label = (
            "[bold orange3][TYPE 2: CONVERSATIONAL FLOW][/]"
            if self.context.get("flow_type") == 2
            else "[bold green][TYPE 1: LOGIC FLOW][/]"
        )
        root = Tree(
            f":robot: [bold magenta]Flow Analysis: "
            f"{self.flow.get('displayName', 'Unnamed')}[/bold magenta] "
            f"{flow_type_label}"
        )
        struct_node = root.add(":outbox_tray: [bold green]Flow Logic[/]")

        start_node = struct_node.add("[bold]Start Page[/]")
        self._render_routes(
            start_node,
            self.flow.get("transitionRoutes", [])
            + self.flow.get("transitionEvents", []),
        )
        self._render_events(
            start_node,
            self.flow.get("eventHandlers", [])
            + self.flow.get("conversationEvents", []),
        )

        for page_wrap in sorted(
            self.context.get("pages", []),
            key=lambda page_entry: page_entry.get("value", page_entry).get(
                "displayName", ""
            ),
        ):
            page = page_wrap.get("value", page_wrap)
            page_node = struct_node.add(
                f":page_facing_up: [bold cyan]{page.get('displayName')}[/]"
            )
            if page.get("entryFulfillment") or page.get("onLoad"):
                self._render_fulfillment(
                    page_node,
                    page.get("entryFulfillment") or page.get("onLoad"),
                    "On Entry",
                )

            params = page.get("form", {}).get("parameters", []) + page.get(
                "slots", []
            )
            if params:
                form_node = page_node.add("[dim]Parameter Collection[/dim]")
                for param in params:
                    param_node = form_node.add(
                        f"❓ Collect: [orange3]{param.get('displayName')}[/]"
                    )
                    if "fillBehavior" in param:
                        self._render_fulfillment(
                            param_node,
                            param["fillBehavior"].get(
                                "initialPromptFulfillment"
                            )
                            or param["fillBehavior"].get("initialPrompt"),
                        )
            self._render_routes(
                page_node,
                page.get("transitionRoutes", [])
                + page.get("transitionEvents", []),
            )
            self._render_events(
                page_node,
                page.get("eventHandlers", [])
                + page.get("conversationEvents", []),
            )
        return root
