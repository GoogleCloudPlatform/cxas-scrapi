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

import logging
import re
from typing import Any, Dict, Set

from cxas_scrapi.migration.data_models import MigrationIR, MigrationStatus

logger = logging.getLogger(__name__)


class CXASTopologyLinker:
    """Extracts routing dependencies and sets parent/child links with
    circular reference protection.
    """

    def __init__(
        self, ps_agents_client: Any, ps_apps_client: Any, reporter: Any
    ):
        self.ps_agents = ps_agents_client
        self.ps_apps = ps_apps_client
        self.reporter = reporter

    @staticmethod
    def link_children_recursive(
        ir_key: str,
        ancestors: set,
        processed_nodes: Set[str],
        ir: MigrationIR,
        deployed_agent_map: Dict[str, str],
        dfcx_id_to_display_name: Dict[str, str],
        source_agent_data: Dict[str, Any],
        ps_agents: Any,
        reporter: Any,
    ):
        if ir_key in processed_nodes:
            return

        agent_data = ir.agents.get(ir_key)
        if not agent_data or agent_data.status != MigrationStatus.DEPLOYED:
            return

        parent_resource = agent_data.resource_name
        current_path_ancestors = ancestors.union({ir_key})
        child_resources_to_add = set()
        children_to_recurse = set()

        # --- 1. RESOLVE EXPLICIT DEPENDENCIES ---
        if agent_data.type == "PLAYBOOK":
            pb_raw = agent_data.raw_data or {}
            refs = pb_raw.get("referencedPlaybooks", []) + pb_raw.get(
                "referencedFlows", []
            )

            for child_dfcx_id in refs:
                child_display_name = dfcx_id_to_display_name.get(child_dfcx_id)

                if not child_display_name:
                    child_uuid = child_dfcx_id.split("/")[-1]
                    for k, v in dfcx_id_to_display_name.items():
                        if k.endswith(child_uuid):
                            child_display_name = v
                            break

                if not child_display_name:
                    child_uuid = child_dfcx_id.split("/")[-1]
                    flow_obj = next(
                        (
                            f
                            for f in source_agent_data.get("flows", [])
                            if f.get("flow", f)
                            .get("name", "")
                            .endswith(child_uuid)
                        ),
                        None,
                    )
                    if flow_obj:
                        child_display_name = flow_obj.get("flow", flow_obj).get(
                            "displayName"
                        )

                if not child_display_name:
                    logger.warning(
                        f"  ⚠️ Warning: Could not resolve display name for "
                        f"child reference ID: {child_dfcx_id}"
                    )
                    continue

                if child_display_name in current_path_ancestors:
                    logger.info(
                        f"  INFO: Skipping circular reference from '{ir_key}' "
                        f"back to ancestor '{child_display_name}'."
                    )
                    continue

                if child_display_name in deployed_agent_map:
                    child_resources_to_add.add(
                        deployed_agent_map[child_display_name]
                    )
                    children_to_recurse.add(child_display_name)
                else:
                    logger.warning(
                        f"  ⚠️ Warning: Explicit child '{child_display_name}' "
                        f"was not deployed."
                    )

        # --- 2. RESOLVE GENERATIVE DEPENDENCIES ---
        instruction = agent_data.instruction
        gen_refs = re.findall(r"{@AGENT:\s*([^}]+)}", instruction)

        def normalize_name(name):
            return re.sub(r"[_\\-]+", " ", name).strip().lower()

        for child_name in gen_refs:
            child_clean = child_name.strip()
            if child_clean.upper() in ["END_SESSION", "END_FLOW"]:
                continue

            normalized_child = normalize_name(child_clean)
            matched_ir_key = next(
                (
                    k
                    for k, agent in ir.agents.items()
                    if normalize_name(k) == normalized_child
                    or normalize_name(agent.display_name) == normalized_child
                ),
                None,
            )

            if not matched_ir_key:
                logger.warning(
                    f"  ⚠️ Warning: '{ir_key}' references '{child_clean}', "
                    f"but it couldn't be resolved in IR."
                )
                continue

            if matched_ir_key in current_path_ancestors:
                logger.info(
                    f"  INFO: Skipping circular reference from '{ir_key}' "
                    f"back to ancestor '{matched_ir_key}'."
                )
                continue

            if matched_ir_key in deployed_agent_map:
                child_resources_to_add.add(deployed_agent_map[matched_ir_key])
                children_to_recurse.add(matched_ir_key)
            else:
                logger.warning(
                    f"  ⚠️ Warning: '{ir_key}' references '{child_clean}' "
                    f"(mapped to '{matched_ir_key}'), but it wasn't deployed."
                )

        # --- EXECUTE LINKING ---
        if child_resources_to_add:
            logger.info(
                f"  Updating agent '{ir_key}' with "
                f"{len(child_resources_to_add)} child(ren)..."
            )
            ps_agents.update_agent(
                agent_id=parent_resource,
                child_agents=list(child_resources_to_add),
            )
            reporter.log_action(
                "Linking",
                f"Linked {len(child_resources_to_add)} children to {ir_key}",
            )

        processed_nodes.add(ir_key)

        for child in children_to_recurse:
            CXASTopologyLinker.link_children_recursive(
                child,
                current_path_ancestors,
                processed_nodes,
                ir,
                deployed_agent_map,
                dfcx_id_to_display_name,
                source_agent_data,
                ps_agents,
                reporter,
            )

    def link_and_finalize_topology(
        self, ir: MigrationIR, source_agent_data: Dict[str, Any]
    ):
        """Extracts routing dependencies and sets parent/child links with
        circular reference protection.
        """
        full_app_name = ir.metadata.app_resource_name
        logger.info("\nLinking Agent Topology (Parent/Child Routes)...")

        # 1. Map IR Keys (Original Names) to Deployed Resource Names
        deployed_agent_map = {
            ir_key: agent.resource_name
            for ir_key, agent in ir.agents.items()
            if agent.status == MigrationStatus.DEPLOYED and agent.resource_name
        }

        # Create a reverse map for DFCX ID -> Display Name
        dfcx_id_to_display_name = {}
        for pb in source_agent_data.get("playbooks", []):
            dfcx_id_to_display_name[pb["name"]] = pb["displayName"]

        processed_nodes = set()

        # Trigger recursive linking for all deployed agents using IR keys
        for ir_key in deployed_agent_map.keys():
            if ir_key not in processed_nodes:
                CXASTopologyLinker.link_children_recursive(
                    ir_key,
                    set(),
                    processed_nodes,
                    ir,
                    deployed_agent_map,
                    dfcx_id_to_display_name,
                    source_agent_data,
                    self.ps_agents,
                    self.reporter,
                )

        # 3. Set Root Agent
        logger.info("\nConfiguring Root Agent...")
        start_playbook_id = source_agent_data.get("startPlaybook")
        start_flow_id = source_agent_data.get("startFlow")

        root_display_name = None
        if start_playbook_id:
            start_uuid = start_playbook_id.split("/")[-1]
            pb_obj = next(
                (
                    pb
                    for pb in source_agent_data.get("playbooks", [])
                    if pb["name"].split("/")[-1] == start_uuid
                ),
                None,
            )
            if pb_obj:
                root_display_name = pb_obj.get("displayName")
        elif start_flow_id:
            start_uuid = start_flow_id.split("/")[-1]
            flow_wrapper = next(
                (
                    f
                    for f in source_agent_data.get("flows", [])
                    if f.get("flow", f).get("name", "").split("/")[-1]
                    == start_uuid
                ),
                None,
            )
            if flow_wrapper:
                root_display_name = flow_wrapper.get("flow", flow_wrapper).get(
                    "displayName"
                )

        root_agent_resource = None
        if root_display_name:
            root_agent_resource = next(
                (
                    res
                    for name, res in deployed_agent_map.items()
                    if name.lower() == root_display_name.lower()
                ),
                None,
            )

        if root_agent_resource:
            logger.info(f"Setting '{root_display_name}' as the Root Agent...")
            self.ps_apps.update_app(
                app_id=full_app_name, rootAgent=root_agent_resource
            )
            self.reporter.log_action(
                "Routing", f"Set Root Agent to {root_display_name}"
            )
        else:
            logger.warning(
                "⚠️ Could not determine Root Agent. You will need to set "
                "this manually in the CXAS console."
            )
