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

"""Dependency analyzer for DFCX resources."""

import json
import re
from typing import Any, Dict, List, Tuple


class DependencyAnalyzer:
    """Analyzes references between DFCX resources."""

    def __init__(self, agent_data: Dict[str, Any]):
        self.data = agent_data
        self.id_map = {}  # DisplayName -> FullName
        self.name_map = {}  # FullName -> DisplayName
        self.type_map = {}  # FullName -> Type (Playbook, Flow, Tool)
        self.graph = {}  # SourceID -> Set(TargetIDs)
        self.reverse_graph = {}  # TargetID -> Set(SourceIDs)

        self._build_index()
        self._build_graph()

    def _build_index(self):
        """Builds lookup maps for all resources."""

        def reg(res, type_label):
            name = res.get("name", "")
            display_name = res.get("displayName", "")
            if name:
                self.id_map[display_name] = name
                self.name_map[name] = display_name
                self.type_map[name] = type_label
                self.graph[name] = set()
                self.reverse_graph[name] = set()

        for pb in self.data.get("playbooks", []):
            reg(pb, "Playbook")
        for flow in self.data.get("flows", []):
            f = flow.get("flow_data", flow)
            reg(f, "Flow")

    def _add_edge(self, source_id: str, target_identifier: str):
        """Adds a dependency edge if target exists."""
        if target_identifier.startswith("projects/"):
            target_id = target_identifier
        else:
            target_id = self.id_map.get(target_identifier)

        if target_id and source_id and target_id in self.name_map:
            self.graph[source_id].add(target_id)
            self.reverse_graph[target_id].add(source_id)

    def _scan_text_for_refs(self, source_id: str, text: str):
        """Scans text for ${TYPE:Name} patterns."""
        if not text:
            return
        matches = re.findall(r"\${(FLOW|PLAYBOOK|TOOL|AGENT):([^}]+)}", text)
        for _, ref_name in matches:
            self._add_edge(source_id, ref_name.strip())

    def _build_graph(self):
        """Scans all resources to build dependency graph."""
        # 1. Scan Playbooks
        for pb in self.data.get("playbooks", []):
            pb_id = pb.get("name")

            # Explicit lists
            for ref in pb.get("referencedPlaybooks", []):
                self._add_edge(pb_id, ref)
            for ref in pb.get("referencedTools", []):
                self._add_edge(pb_id, ref)

            # Instructions (Steps)
            steps = pb.get("instruction", {}).get("steps", [])
            steps_str = json.dumps(steps)
            self._scan_text_for_refs(pb_id, steps_str)

        # 2. Scan Flows
        for flow_wrapper in self.data.get("flows", []):
            flow = flow_wrapper.get("flow_data", flow_wrapper)
            flow_id = flow.get("name")

            # Transition Routes (Flow Level)
            for route in flow.get("transitionRoutes", []):
                if "targetFlow" in route:
                    self._add_edge(flow_id, route["targetFlow"])

            # Event Handlers
            for handler in flow.get("eventHandlers", []):
                if "targetFlow" in handler:
                    self._add_edge(flow_id, handler["targetFlow"])

            # Pages (Transition Routes)
            for page in flow_wrapper.get("pages", []):
                p_val = page.get("value", page)
                for route in p_val.get("transitionRoutes", []):
                    if "targetFlow" in route:
                        self._add_edge(flow_id, route["targetFlow"])

    def get_impact(
        self, selected_ids: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Returns (outgoing_deps, incoming_refs) based on selection."""
        selected_set = set(selected_ids)

        # 1. Outgoing: Things selected items need, but aren't selected
        outgoing = set()
        for sid in selected_set:
            if sid in self.graph:
                for target in self.graph[sid]:
                    if target not in selected_set:
                        outgoing.add(target)

        # 2. Incoming: Things that need selected items, but aren't selected
        incoming = set()
        for sid in selected_set:
            if sid in self.reverse_graph:
                for source in self.reverse_graph[sid]:
                    if source not in selected_set:
                        incoming.add(source)

        return list(outgoing), list(incoming)

    def get_details(self, res_id: str) -> Dict[str, str]:
        return {
            "name": self.name_map.get(res_id, "Unknown"),
            "type": self.type_map.get(res_id, "Unknown"),
        }
