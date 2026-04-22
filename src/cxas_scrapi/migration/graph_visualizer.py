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

"""High-level graphviz topology visualizer for DFCX agents."""

import re
import textwrap
from typing import Any, Dict, List

import graphviz


class HighLevelGraphVisualizer:
    """Generates a macroscopic directed graph matching the DFCX UI Topology."""

    def __init__(self, full_data: Dict[str, Any]):
        self.data = full_data
        self.uuid_to_name: Dict[str, str] = {}
        self.name_to_uuid: Dict[str, str] = {}
        self.edges_accumulator: Dict[tuple, List[str]] = {}

        for pb_wrap in self.data.get("playbooks", []):
            pb = pb_wrap.get("playbook", pb_wrap)
            uid = self._get_raw_id(pb)
            name = pb.get("displayName", uid)
            if uid:
                self.uuid_to_name[uid] = name
                self.name_to_uuid[name] = uid

        for flow_wrap in self.data.get("flows", []):
            flow = flow_wrap.get("flow", flow_wrap)
            uid = self._get_raw_id(flow)
            name = flow.get("displayName", uid)
            if uid:
                self.uuid_to_name[uid] = name
                self.name_to_uuid[name] = uid

        for tool_entry in self.data.get("tools", []):
            uid = self._get_raw_id(tool_entry)
            name = tool_entry.get("displayName", uid)
            if uid:
                self.uuid_to_name[uid] = name

        for webhook_entry in self.data.get("webhooks", []):
            webhook_data = webhook_entry.get("value", webhook_entry)
            uid = self._get_raw_id(webhook_data)
            name = webhook_data.get("displayName", uid)
            if uid:
                self.uuid_to_name[uid] = name

    @staticmethod
    def _get_raw_id(res: Any) -> str:
        """Extract the short UUID from a resource dict or resource-name
        string."""
        if isinstance(res, dict):
            return (
                res.get("playbookId")
                or res.get("flowId")
                or res.get("id")
                or str(res.get("name", "")).split("/")[-1]
            )
        return str(res).split("/")[-1]

    def _resolve_to_uuid(self, identifier: str) -> str:
        if not identifier:
            return ""
        identifier = str(identifier).split("/")[-1]
        if identifier in ("END SESSION", "END_FLOW"):
            return "END_SESSION"
        if identifier in self.uuid_to_name:
            return identifier
        if identifier in self.name_to_uuid:
            return self.name_to_uuid[identifier]
        return identifier

    def _get_intent_name(self, intent_ref: str) -> str:
        intent_id = str(intent_ref).split("/")[-1]
        for intent in self.data.get("intents", []):
            if str(intent.get("name", "")).split("/")[-1] == intent_id:
                return intent.get("displayName", intent_id)
        return intent_id

    def _get_trigger_text(self, item: Dict[str, Any]) -> str:
        if "intent" in item:
            return f"Intent: {self._get_intent_name(item['intent'])}"
        if "triggerIntentId" in item:
            return f"Intent: {self._get_intent_name(item['triggerIntentId'])}"
        if "condition" in item:
            return f"If: {item.get('conditionString', item['condition'])}"
        if "event" in item:
            return f"Event: {item['event']}"
        return "Always"

    def _accumulate_edge(
        self,
        src_uuid: str,
        dst_uuid: str,
        label: str,
        condition: str = "Always",
        is_tool: bool = False,
    ):
        dst_uuid = self._resolve_to_uuid(dst_uuid)
        if not src_uuid or not dst_uuid:
            return
        key = (src_uuid, dst_uuid, label, is_tool)
        if key not in self.edges_accumulator:
            self.edges_accumulator[key] = []
        if condition and condition not in self.edges_accumulator[key]:
            self.edges_accumulator[key].append(condition)

    def _scan_playbook_steps(
        self, steps: List[Dict[str, Any]], pb_uuid: str
    ) -> None:
        """Scan playbook instruction steps for flow, playbook, and tool refs.

        Recursively walks nested steps and accumulates graph edges for any
        ``${FLOW:...}``, ``${PLAYBOOK:...}``, or ``${TOOL:...}`` references
        found in step text.
        """
        for step in steps:
            text = step.get("text", "")
            if text:
                cond_text = text.replace('"', "'")
                if len(cond_text) > 40:
                    cond_text = cond_text[:37] + "..."
                matches = re.findall(
                    r"\${(FLOW|PLAYBOOK|AGENT|PAGE):([^}]+)}", text
                )
                for ref_type, ref_name in matches:
                    ref_clean = ref_name.strip()
                    if "END SESSION" in ref_clean or "END_FLOW" in ref_clean:
                        self._accumulate_edge(
                            pb_uuid,
                            "END_SESSION",
                            "routes to",
                            condition=cond_text,
                        )
                    elif ref_type != "PAGE":
                        self._accumulate_edge(
                            pb_uuid,
                            ref_clean,
                            "routes to",
                            condition=cond_text,
                        )
                tool_matches = re.findall(r"\${TOOL:([^}]+)}", text)
                for tool_ref in tool_matches:
                    self._accumulate_edge(
                        pb_uuid,
                        tool_ref.strip(),
                        "uses",
                        condition=cond_text,
                        is_tool=True,
                    )
            if "steps" in step:
                self._scan_playbook_steps(step["steps"], pb_uuid)

    def _search_webhook_refs(
        self, obj: Any, flow_uuid: str, trigger: str
    ) -> None:
        """Recursively search ``obj`` for webhook and code-block function refs.

        Accumulates graph edges from the given flow node to any webhook or
        flexible-webhook (code-block function) encountered in the object tree.
        """
        if isinstance(obj, dict):
            if "webhook" in obj and isinstance(obj["webhook"], str):
                wh_uuid = self._resolve_to_uuid(obj["webhook"])
                tag = obj.get("tag", "")
                if tag:
                    specific = f"{wh_uuid}_{tag}"
                    wh_name = self.uuid_to_name.get(wh_uuid, wh_uuid)
                    if specific not in self.uuid_to_name:
                        self.uuid_to_name[specific] = f"{wh_name}\\n[{tag}]"
                    self._accumulate_edge(
                        flow_uuid,
                        specific,
                        "calls",
                        condition=trigger,
                        is_tool=True,
                    )
                else:
                    self._accumulate_edge(
                        flow_uuid,
                        wh_uuid,
                        "calls",
                        condition=trigger,
                        is_tool=True,
                    )

            if (
                "function" in obj
                and isinstance(obj["function"], dict)
                and "webhookFulfillmentId" in obj["function"]
            ):
                wh_uuid = self._resolve_to_uuid(
                    obj["function"]["webhookFulfillmentId"]
                )
                tag = obj["function"].get("name", "")
                if tag:
                    specific = f"{wh_uuid}_{tag}"
                    wh_name = self.uuid_to_name.get(wh_uuid, wh_uuid)
                    if specific not in self.uuid_to_name:
                        self.uuid_to_name[specific] = f"{wh_name}\\n[{tag}]"
                    self._accumulate_edge(
                        flow_uuid,
                        specific,
                        "calls",
                        condition=trigger,
                        is_tool=True,
                    )
                else:
                    self._accumulate_edge(
                        flow_uuid,
                        wh_uuid,
                        "calls",
                        condition=trigger,
                        is_tool=True,
                    )
            for value in obj.values():
                self._search_webhook_refs(value, flow_uuid, trigger)
        elif isinstance(obj, list):
            for item in obj:
                self._search_webhook_refs(item, flow_uuid, trigger)

    def _extract_flow_routes_and_tools(
        self, obj_list: List[Dict[str, Any]], flow_uuid: str
    ) -> None:
        """Accumulate edges for all routes, events, and webhook calls in a flow.

        Walks transition routes and event handlers, recording playbook/flow
        transitions, END_SESSION edges, and webhook calls into
        ``edges_accumulator``.
        """
        for item in obj_list:
            trigger = self._get_trigger_text(item)
            handler = item.get("transitionEventHandler", item)

            for key in ("targetPlaybookId", "targetPlaybook"):
                target = handler.get(key)
                if target:
                    self._accumulate_edge(
                        flow_uuid,
                        target,
                        "transitions",
                        condition=trigger,
                    )

            for key in ("targetFlowId", "targetFlow"):
                target = handler.get(key)
                if target:
                    self._accumulate_edge(
                        flow_uuid,
                        target,
                        "transitions",
                        condition=trigger,
                    )

            target_page = (
                handler.get("targetPageId") or handler.get("targetPage") or ""
            )
            if "END_SESSION" in str(target_page) or "END_FLOW" in str(
                target_page
            ):
                self._accumulate_edge(
                    flow_uuid,
                    "END_SESSION",
                    "transitions",
                    condition=trigger,
                )

            self._search_webhook_refs(handler, flow_uuid, trigger)

    def build(self, show_code_blocks: bool = False) -> graphviz.Digraph:
        """Build and return the graphviz Digraph for the agent topology.

        Args:
            show_code_blocks: When True, playbook inline code-block
                function definitions are rendered as additional fringe nodes.

        Returns:
            A :class:`graphviz.Digraph` ready for rendering or export.
        """
        self.dot = graphviz.Digraph(comment="Agent Topology", format="svg")
        self.dot.attr(
            rankdir="LR",
            nodesep="0.3",
            ranksep="1.2",
            concentrate="true",
            splines="spline",
        )
        self.dot.attr(
            "node",
            style="filled",
            fontname="Helvetica",
            fontsize="11",
            rx="5",
            ry="5",
        )
        self.dot.attr(
            "edge",
            fontname="Helvetica",
            fontsize="9",
            color="#666666",
        )
        self.edges_accumulator = {}

        # Identify entry point
        agent_data = self.data.get("agent", {})
        entry_point = (
            agent_data.get("startPlaybook")
            or agent_data.get("startFlow")
            or self.data.get("startPlaybook")
            or self.data.get("startFlow")
        )
        entry_uuid = (
            self._resolve_to_uuid(entry_point)
            if entry_point
            else "00000000-0000-0000-0000-000000000000"
        )

        self.dot.node(
            "ENTRY_MARKER",
            "ENTRY POINT",
            shape="cds",
            fillcolor="#c8e6c9",
            color="#388e3c",
            fontcolor="#1b5e20",
            style="filled,bold",
        )
        self.dot.edge(
            "ENTRY_MARKER", entry_uuid, color="#388e3c", penwidth="2.5"
        )

        # Playbooks
        for pb_wrap in self.data.get("playbooks", []):
            pb = pb_wrap.get("playbook", pb_wrap)
            pb_uuid = self._resolve_to_uuid(self._get_raw_id(pb))
            name = self.uuid_to_name.get(pb_uuid, pb_uuid)

            pen_width = "3" if pb_uuid == entry_uuid else "2"
            border_color = "#388e3c" if pb_uuid == entry_uuid else "#1976d2"
            self.dot.node(
                pb_uuid,
                f"📘 {name}",
                shape="note",
                fillcolor="#e3f2fd",
                color=border_color,
                penwidth=pen_width,
            )

            if show_code_blocks:
                code = pb.get("codeBlock", {}).get("code", "")
                if code:
                    funcs = re.findall(
                        r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
                        code,
                        re.MULTILINE,
                    )
                    for func in funcs:
                        func_id = f"codeblock_{func}"
                        self.uuid_to_name[func_id] = f"Inline:\n{func}()"
                        self._accumulate_edge(
                            pb_uuid,
                            func_id,
                            "defines",
                            condition="Code Block",
                            is_tool=True,
                        )

            for ref in pb.get("playbookRoutes", []) + pb.get("flowRoutes", []):
                self._accumulate_edge(
                    pb_uuid, self._get_raw_id(ref), "routes to"
                )

            for ref in pb.get("referencedTools", []):
                self._accumulate_edge(
                    pb_uuid, self._get_raw_id(ref), "uses", is_tool=True
                )

            self._scan_playbook_steps(
                pb.get("instruction", {}).get("steps", []),
                pb_uuid,
            )

        # Flows
        for flow_wrap in self.data.get("flows", []):
            flow = flow_wrap.get("flow", flow_wrap)
            flow_uuid = self._resolve_to_uuid(self._get_raw_id(flow))
            name = self.uuid_to_name.get(flow_uuid, flow_uuid)

            pen_width = "3" if flow_uuid == entry_uuid else "2"
            border_color = "#388e3c" if flow_uuid == entry_uuid else "#7b1fa2"
            self.dot.node(
                flow_uuid,
                f"🔀 {name}",
                shape="component",
                fillcolor="#f3e5f5",
                color=border_color,
                penwidth=pen_width,
            )

            all_items = (
                flow.get("transitionRoutes", [])
                + flow.get("transitionEvents", [])
                + flow.get("eventHandlers", [])
                + flow.get("conversationEvents", [])
            )
            self._extract_flow_routes_and_tools(all_items, flow_uuid)

            for page_wrap in flow_wrap.get("pages", []):
                page = page_wrap.get("value", page_wrap)
                page_items = (
                    page.get("transitionRoutes", [])
                    + page.get("transitionEvents", [])
                    + page.get("eventHandlers", [])
                    + page.get("conversationEvents", [])
                )
                self._extract_flow_routes_and_tools(page_items, flow_uuid)

        # END_SESSION node (only if referenced)
        has_end = any(
            dst == "END_SESSION"
            for (src, dst, lbl, is_tool) in self.edges_accumulator
        )
        if has_end:
            self.dot.node(
                "END_SESSION",
                "END SESSION",
                shape="octagon",
                fillcolor="#ffcdd2",
                color="#d32f2f",
                fontcolor="#b71c1c",
                style="filled,bold",
                penwidth="2",
            )

        # Draw accumulated edges
        seen_fringe: set = set()
        for (
            src,
            dst,
            label,
            is_tool,
        ), conditions in self.edges_accumulator.items():
            if len(conditions) > 1 and "Always" in conditions:
                conditions.remove("Always")

            wrapped = [
                "\\n".join(textwrap.wrap(c, width=35)) for c in conditions
            ]
            cond_str = "\\nOR\\n".join(wrapped)

            if is_tool:
                unique_dst = f"{src}_tool_{dst}"
                if unique_dst not in seen_fringe:
                    name = self.uuid_to_name.get(dst, dst)
                    self.dot.node(
                        unique_dst,
                        f"🛠️ {name}",
                        shape="cds",
                        fillcolor="#ffe0b2",
                        color="#fb8c00",
                        penwidth="1.5",
                    )
                    seen_fringe.add(unique_dst)
                edge_label = (
                    f"{label}\\n({cond_str})"
                    if cond_str and cond_str != "Always"
                    else label
                )
                self.dot.edge(
                    src,
                    unique_dst,
                    label=edge_label,
                    style="dashed",
                    color="#fb8c00",
                    fontcolor="#fb8c00",
                    weight="10",
                    minlen="1",
                )
            else:
                dst_name = (
                    "END SESSION"
                    if dst == "END_SESSION"
                    else self.uuid_to_name.get(dst, dst)
                )
                base_label = label if label.endswith(" to") else f"{label} to"
                actual_label = f"{base_label} {dst_name}"
                edge_label = (
                    f"{actual_label}\\n({cond_str})"
                    if cond_str and cond_str != "Always"
                    else actual_label
                )
                self.dot.edge(src, dst, label=edge_label, weight="1")

        return self.dot
