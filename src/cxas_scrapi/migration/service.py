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
import io
import logging
import re
import uuid
from typing import Any, Dict

from rich.console import Console

from cxas_scrapi.migration.ai_augment import AIAugment
from cxas_scrapi.migration.artifacts_builder import CXASAsyncArtifactBuilder
from cxas_scrapi.migration.code_block_migrator import CodeBlockMigrator
from cxas_scrapi.migration.cxas_topology_linker import CXASTopologyLinker
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.designer import AsyncAgentDesigner
from cxas_scrapi.migration.dfcx_exporter import ConversationalAgentsAPI
from cxas_scrapi.migration.dfcx_migration_reporter import DFCXMigrationReporter
from cxas_scrapi.migration.dfcx_parameter_extractor import (
    DFCXParameterExtractor,
)
from cxas_scrapi.migration.dfcx_playbook_converter import DFCXPlaybookConverter
from cxas_scrapi.migration.dfcx_tool_converter import DFCXToolConverter
from cxas_scrapi.migration.eval_generator import DeterministicEvalGenerator
from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


class MigrationService:
    """Orchestrates the end-to-end migration process from DFCX to CXAS."""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        credentials=None,
        default_model: str = "gemini-3.1-flash-lite-preview",
        ps_apps_client: Any = None,
        ps_agents_client: Any = None,
        ps_tools_client: Any = None,
        ps_toolsets_client: Any = None,
        secret_manager_client: Any = None,
        cx_api_client: Any = None,
    ):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        self.default_model = default_model

        self.ps_apps = ps_apps_client
        self.ps_agents = ps_agents_client
        self.ps_tools = ps_tools_client
        self.ps_toolsets = ps_toolsets_client
        self.secret_manager = secret_manager_client
        self.cx_api = cx_api_client

        # Initialize internal clients
        self.gemini_client = GeminiGenerate(
            project_id=project_id,
            location=location,
            credentials=credentials,
            model_name=default_model,
        )

        self.exporter = ConversationalAgentsAPI()
        self.designer = AsyncAgentDesigner(gemini_client=self.gemini_client)
        self.artifacts_builder = CXASAsyncArtifactBuilder(
            gemini_client=self.gemini_client
        )
        self.eval_generator = None
        self.code_block_migrator = CodeBlockMigrator(
            ps_tools_client=self.ps_tools, ai_augment_client=None
        )
        self.ai_augment = AIAugment(gemini_client=self.gemini_client)
        self.reporter = DFCXMigrationReporter(gemini_client=self.gemini_client)
        self.tool_converter = DFCXToolConverter(
            secret_manager=self.secret_manager, reporter=self.reporter
        )
        self.playbook_converter = DFCXPlaybookConverter(reporter=self.reporter)
        self.topology_linker = CXASTopologyLinker(
            ps_agents_client=self.ps_agents,
            ps_apps_client=self.ps_apps,
            reporter=self.reporter,
        )

        self.ir = {}
        self.source_agent_data = None

    async def run_migration(
        self,
        source_cx_agent_id: str,
        target_ps_app_name: str,
        migrate_dfcx_flows: bool = True,
        source_agent_data_override: Dict[str, Any] = None,
        gen_unit_tests: bool = True,
    ) -> None:
        """The comprehensive async executor for Hybrid Migration."""

        # --- 0. Data Loading & Preprocessing ---
        self.source_agent_data = (
            source_agent_data_override
            or self.exporter.fetch_full_agent_details(
                source_cx_agent_id, use_export=True
            )
        )
        if not self.source_agent_data:
            logger.error(
                "Migration failed: Could not retrieve source agent data."
            )
            return

        logger.info(f"Starting Hybrid Migration for: {target_ps_app_name}")

        # --- 1. Populate IR Metadata & Predictable IDs ---
        target_app_uuid = str(uuid.uuid4())
        target_app_resource_name = (
            f"projects/{self.project_id}/locations/{self.location}/apps/"
            f"{target_app_uuid}"
        )

        self.ir = MigrationIR(
            metadata=IRMetadata(
                app_name=target_ps_app_name,
                app_id=target_app_uuid,
                app_resource_name=target_app_resource_name,
                default_model=self.default_model,
            )
        )
        self.deployment_state = {
            "app_created": False,
            "vars_deployed": False,
        }

        self.eval_generator = DeterministicEvalGenerator(self.ir)
        self.reporter.set_app_info(
            source_cx_agent_id, target_ps_app_name, target_app_resource_name
        )

        # --- 2. Async Playbook Description Generation ---
        logger.info("Generating Playbook descriptions concurrently...")
        playbooks = self.source_agent_data.get("playbooks", [])
        playbook_descriptions = {}
        if playbooks:
            tasks = [
                self.ai_augment.generate_agent_description(pb)
                for pb in playbooks
            ]
            desc_results = await asyncio.gather(*tasks, return_exceptions=True)

            for pb, desc in zip(playbooks, desc_results, strict=True):
                pb_name = pb["displayName"]
                if isinstance(desc, Exception):
                    logger.warning(
                        f"Failed to generate description for {pb_name}: {desc}"
                    )
                    playbook_descriptions[pb_name] = ""
                else:
                    playbook_descriptions[pb_name] = desc if desc else ""

        # --- 3. Populate IR Variables ---
        final_declarations, parameter_name_map = (
            DFCXParameterExtractor.migrate_parameters(
                self.source_agent_data, self.reporter
            )
        )
        for param in final_declarations:
            self.ir.parameters[param["name"]] = param

        # --- 4. Populate Standard Tools & Webhooks into IR ---
        logger.info("Processing Standard Tools and Webhooks...")
        for cx_tool in self.source_agent_data.get("tools", []):
            res = self.tool_converter.convert_cx_tool_to_ps_resource(cx_tool)
            if res:
                self.ir.tools[res["id"]] = IRTool(
                    id=res["id"],
                    name=f"{target_app_resource_name}/tools/{res['id']}",
                    type=res["type"],
                    payload=res["payload"],
                    operation_ids=res.get("operation_ids", []),
                    status=MigrationStatus.COMPILED,
                )

        for cx_webhook in self.source_agent_data.get("webhooks", []):
            res = self.tool_converter.convert_webhook_to_openapi_toolset(
                cx_webhook
            )
            if res:
                self.ir.tools[res["id"]] = IRTool(
                    id=res["id"],
                    name=f"{target_app_resource_name}/tools/{res['id']}",
                    type=res["type"],
                    payload=res["payload"],
                    operation_ids=res.get("operation_ids", []),
                    status=MigrationStatus.COMPILED,
                )

        # --- 5. Extract Code Blocks ---
        logger.info("Extracting and rewriting Python Code Blocks into IR...")
        code_blocks = self.source_agent_data.get("codeBlocks", [])
        master_inline_action_map = {}
        existing_tool_ids = set(self.ir.tools.keys())
        migrated_function_names = set()
        function_name_to_tool_map = {}
        tool_display_name_map = {
            tool["displayName"]: tool["name"]
            for tool in self.ir.tools.values()
            if "displayName" in tool
        }

        for cb in code_blocks:
            code = cb.get("code", "")
            (
                extracted_tools,
                action_to_tool_map,
                referenced_toolsets,
            ) = self.code_block_migrator.extract_functions_to_ir(
                code,
                existing_tool_ids,
                migrated_function_names,
                function_name_to_tool_map,
                self.ir.tools,
                tool_display_name_map,
                target_app_resource_name,
            )
            for tool in extracted_tools:
                self.ir.tools[tool["id"]] = IRTool(
                    id=tool["id"],
                    name=tool["name"],
                    type=tool["type"],
                    payload=tool["payload"],
                    status=MigrationStatus.COMPILED,
                )
            master_inline_action_map.update(action_to_tool_map)

        # --- 6. Compile Playbooks into IR ---
        logger.info("Compiling Playbooks into IR payload...")
        cx_tool_display_name_to_id_map = {
            t["displayName"]: t_id
            for t_id, t in self.ir.tools.items()
            if "displayName" in t
        }

        for pb in playbooks:
            pb_name = pb["displayName"]
            generated_desc = playbook_descriptions.get(pb_name)
            agent_payload = (
                self.playbook_converter.convert_cx_playbook_to_ps_agent(
                    pb,
                    self.ir.tools,
                    generated_desc,
                    parameter_name_map,
                    cx_tool_display_name_to_id_map,
                    master_inline_action_map,
                    self.default_model,
                )
            )
            self.ir.agents[pb_name] = IRAgent(
                type="PLAYBOOK",
                display_name=pb_name,
                description=generated_desc
                or pb.get("goal", "No description provided."),
                instruction=agent_payload["instruction"],
                tools=agent_payload["tools"],
                toolsets=agent_payload["toolsets"],
                model_settings=agent_payload["modelSettings"],
                raw_data=pb,
                status=MigrationStatus.COMPILED,
            )

        # --- 7. FAST DEPLOY (Phase 1) ---
        logger.info("FAST DEPLOY: Pushing Base Resources to CXAS...")
        await self._deploy_base_resources()
        await self._deploy_pending_agents()

        # --- 8. Background Processing for Flows (Phase 2) ---
        flows = self.source_agent_data.get("flows", [])
        if flows and migrate_dfcx_flows:
            logger.info(
                f"\nLaunching parallel Analysis & Architecture for "
                f"{len(flows)} flows..."
            )

            tasks = [
                self._process_single_flow(flow, target_app_resource_name)
                for flow in flows
            ]
            await asyncio.gather(*tasks)

        # --- 7. Finalization & Topology Linking (Phase 4) ---
        logger.info("FINAL DEPLOYMENT & TOPOLOGY LINKING")
        self.topology_linker.link_and_finalize_topology(
            self.ir, self.source_agent_data
        )

        logger.info("MIGRATION COMPLETE!")
        self.reporter.export_and_download(
            f"{target_ps_app_name}_migration_report.md"
        )

    async def _deploy_base_resources(self):
        """Deploys App, Variables, and Tools from the IR."""
        app_id_uuid = self.ir.metadata.app_id
        app_name = self.ir.metadata.app_name
        full_app_name = self.ir.metadata.app_resource_name

        # 1. Create App (If not already created)
        if not self.deployment_state.get("app_created"):
            logger.info(f"\nCreating CXAS App: '{app_name}'...")
            ps_app = self.ps_apps.create_app(
                app_id=app_id_uuid, display_name=app_name
            )
            if not ps_app:
                logger.error("❌ Failed to create App. Aborting deployment.")
                return
            self.deployment_state["app_created"] = True
            logger.info(f"   -> App Created: {full_app_name}")

        # 2. Deploy Variables
        if not self.deployment_state.get("vars_deployed"):
            vars_list = list(self.ir.parameters.values())
            if vars_list:
                logger.info(f"Deploying {len(vars_list)} Global Variables...")
                self.ps_apps.update_app(
                    app_id=full_app_name, variableDeclarations=vars_list
                )
            self.deployment_state["vars_deployed"] = True

        # 3. Deploy Tools
        pending_tools = [
            tool
            for tool in self.ir.tools.values()
            if tool.status != MigrationStatus.DEPLOYED
        ]

        if pending_tools:
            logger.info(f"\nDeploying {len(pending_tools)} Tools & Toolsets...")
            for tool in pending_tools:
                res_type = tool.type
                payload = tool.payload
                tool_id = tool.id
                display_name = payload.get("displayName") or payload.get(
                    "display_name", tool_id
                )

                if res_type == "TOOLSET":
                    logger.info(f"  Creating Toolset: '{display_name}'...")
                    new_res = self.ps_toolsets.create_toolset(
                        app_id=full_app_name,
                        toolset_id=tool_id,
                        toolset_data=payload,
                    )
                else:
                    logger.info(f"  Creating Tool: '{display_name}'...")
                    new_res = self.ps_tools.create_tool(
                        app_id=full_app_name,
                        tool_id=tool_id,
                        tool_data=payload,
                    )

                if new_res and "name" in new_res:
                    tool.status = MigrationStatus.DEPLOYED
                    self.reporter.log_tool(
                        res_type, display_name, new_res["name"]
                    )
                else:
                    logger.error(
                        f"    -> Failed to create {res_type} '{display_name}'."
                    )
                    tool.status = MigrationStatus.FAILED

    @staticmethod
    def _fix_agent_ref(match, valid_display_names):
        raw_name = match.group(1).strip()
        if raw_name.upper() in ["END_SESSION", "END_FLOW"]:
            return match.group(0)

        # Remove underscores/hyphens generated by LLM to match the clean
        # display name
        normalized_name = re.sub(r"[_\\-]+", " ", raw_name).strip().lower()

        if normalized_name in valid_display_names:
            exact_name = valid_display_names[normalized_name]
            return f"{{@AGENT: {exact_name}}}"

        # Fallback: Just return it with underscores replaced by spaces
        # so CXAS routing doesn't break
        fallback_name = re.sub(r"[_]+", " ", raw_name).strip()
        return f"{{@AGENT: {fallback_name}}}"

    async def _deploy_pending_agents(self):
        """Deploys any agents in the IR that have been compiled but not yet
        deployed.
        """
        full_app_name = self.ir.metadata.app_resource_name
        default_model = self.ir.metadata.default_model

        pending_agents = [
            agent
            for agent in self.ir.agents.values()
            if agent.status
            in [MigrationStatus.COMPILED, MigrationStatus.GENERATED]
        ]

        if pending_agents:
            logger.info(f"\nDeploying {len(pending_agents)} Agents...")

            # --- Build a robust map of all actual Agent Display Names ---
            valid_display_names = {
                re.sub(r"[_\\-]+", " ", agent.display_name)
                .strip()
                .lower(): agent.display_name
                for agent in self.ir.agents.values()
            }

            for agent in pending_agents:
                display_name = agent.display_name
                logger.info(
                    f"  Deploying Agent: '{display_name}' ({agent.type})..."
                )

                # Format Callbacks if they exist
                callback_payload = {}
                if agent.callbacks:
                    for cb_type, cb_code in agent.callbacks.items():
                        if cb_code:
                            camel_key = (
                                re.sub(
                                    r"_([a-z])",
                                    lambda m: m.group(1).upper(),
                                    cb_type,
                                )
                                + "s"
                            )
                            callback_payload[camel_key] = [
                                {"pythonCode": cb_code}
                            ]

                # --- Clean Instruction Syntax & Agent Names ---
                instruction = agent.instruction

                instruction = re.sub(
                    r"{@AGENT:\s*([^}]+)}",
                    lambda m: MigrationService._fix_agent_ref(
                        m, valid_display_names
                    ),
                    instruction,
                )
                # Save corrected instruction back to IR for the linker
                agent.instruction = instruction

                # Map local IR tool IDs to full resource paths for the API
                resolved_tools = []
                deployed_tool_names = {
                    tool.name
                    for tool in self.ir.tools.values()
                    if tool.status == MigrationStatus.DEPLOYED
                }

                # --- Attach system 'end_session' tool ---
                end_session_resource = f"{full_app_name}/tools/end_session"
                if re.search(
                    r"{@TOOL:\s*end_session\s*}", instruction, re.IGNORECASE
                ):
                    if end_session_resource not in resolved_tools:
                        resolved_tools.append(end_session_resource)
                        logger.info(
                            "    - Detected 'end_session' in instructions. "
                            "Attached system tool automatically."
                        )

                for t_ref in agent.tools:
                    if t_ref in ("end_session", end_session_resource):
                        if end_session_resource not in resolved_tools:
                            resolved_tools.append(end_session_resource)
                        continue

                    if t_ref.startswith("projects/"):
                        if t_ref in deployed_tool_names:
                            resolved_tools.append(t_ref)
                        else:
                            logger.warning(
                                f"⚠️ Omitting tool {t_ref.split('/')[-1]} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    elif t_ref in self.ir.tools:
                        if (
                            self.ir.tools[t_ref].status
                            == MigrationStatus.DEPLOYED
                        ):
                            resolved_tools.append(self.ir.tools[t_ref].name)
                        else:
                            logger.warning(
                                f"⚠️ Omitting tool {t_ref} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    else:
                        logger.warning(
                            f"⚠️ Could not resolve tool reference for "
                            f"agent '{display_name}': "
                            f"{t_ref}"
                        )

                resolved_toolsets = []
                for ts in agent.toolsets:
                    ts_copy = ts.copy()
                    ts_name = ts_copy["toolset"]

                    if not ts_name.startswith("projects/"):
                        if (
                            ts_name in self.ir.tools
                            and self.ir.tools[ts_name].status
                            == MigrationStatus.DEPLOYED
                        ):
                            ts_copy["toolset"] = self.ir.tools[ts_name].name
                            resolved_toolsets.append(ts_copy)
                        else:
                            logger.warning(
                                f"⚠️ Omitting toolset {ts_name} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    elif ts_name in deployed_tool_names:
                        resolved_toolsets.append(ts_copy)
                    else:
                        logger.warning(
                            f"⚠️ Omitting toolset {ts_name.split('/')[-1]} "
                            f"from agent '{display_name}' "
                            "because it failed to deploy."
                        )

                ps_agent_payload = {
                    "display_name": display_name,
                    "description": agent.description
                    or (
                        agent.blueprint.get("agent_metadata", {}).get("role")
                        if agent.blueprint
                        else None
                    )
                    or agent.type,
                    "instruction": agent.instruction,
                    "tools": resolved_tools,
                    "toolsets": resolved_toolsets,
                    "modelSettings": agent.model_settings
                    or {"model": default_model},
                }
                ps_agent_payload.update(callback_payload)

                new_ps_agent = self.ps_agents.create_agent(
                    app_id=full_app_name, agent_obj=ps_agent_payload
                )

                if new_ps_agent and "name" in new_ps_agent:
                    logger.info("    -> Success!")
                    agent.status = MigrationStatus.DEPLOYED
                    agent.resource_name = new_ps_agent[
                        "name"
                    ]  # Save deployed API name for linking
                    self.reporter.log_agent(
                        display_name,
                        new_ps_agent["name"],
                        ps_agent_payload["description"],
                        default_model,
                    )
                else:
                    logger.error(
                        f"    -> Failed to deploy Agent '{display_name}'."
                    )

    def _sanitize_resource_id(
        self, resource_id: str, min_len: int = 5, max_len: int = 36
    ) -> str:
        """Sanitizes a string to be a valid CXAS resource ID.

        Regex requirement: [a-zA-Z0-9][a-zA-Z0-9-_]{4,35}
        """
        # Replace spaces and other invalid characters (including dots)
        # with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)

        # Ensure it starts with a letter or underscore (CXAS prefers
        # alphanumeric start)
        # We strip leading special chars to be safe
        sanitized = sanitized.lstrip("_-")

        # If it's empty or still doesn't start with a letter/number,
        # prepend 'tool_'
        if not sanitized or not re.match(r"^[a-zA-Z0-9]", sanitized):
            sanitized = "tool_" + sanitized

        # Truncate to max length
        sanitized = sanitized[:max_len]

        # Pad to min length if necessary
        while len(sanitized) < min_len:
            sanitized += "_"

        return sanitized

    def _sanitize_display_name(
        self, display_name: str, max_len: int = 85
    ) -> str:
        """Sanitizes a display name for CXAS resources."""
        # Per error message, allow alphanumeric, single spaces, dashes,
        # underscores.
        # Remove any other characters.
        sanitized = re.sub(r"[^a-zA-Z0-9_ -]", "", display_name)
        # Collapse consecutive spaces, dashes, or underscores into a
        # single space
        sanitized = re.sub(r"[ _-]+", " ", sanitized).strip()
        # Truncate to max length
        return sanitized[:max_len]

    def _preprocess_text_fields(self, data_structure: any) -> any:
        """Recursively traverses a data structure (dict or list) and replaces

        'playbook' with 'agent' in all string values. This is done in-place.
        """
        if isinstance(data_structure, dict):
            for key, value in data_structure.items():
                if isinstance(value, str):
                    # Apply replacement to string values
                    data_structure[key] = re.sub(
                        r"playbook", "agent", value, flags=re.IGNORECASE
                    )
                else:
                    # Recurse into nested structures
                    self._preprocess_text_fields(value)
        elif isinstance(data_structure, list):
            for i, item in enumerate(data_structure):
                if isinstance(item, str):
                    # Apply replacement to string items in a list
                    data_structure[i] = re.sub(
                        r"playbook", "agent", item, flags=re.IGNORECASE
                    )
                else:
                    # Recurse into nested structures
                    self._preprocess_text_fields(item)
        return data_structure

    async def _process_single_flow(
        self, flow_wrapper: Dict[str, Any], target_app_resource_name: str
    ):
        """Processes a single DFCX flow: resolves dependencies, visualizes,
        generates instructions and tools, and deploys them.
        """
        flow_name = flow_wrapper.get("flow", flow_wrapper).get(
            "displayName", "Unnamed"
        )

        logger.info(f"[{flow_name}] Starting processing...")

        resolver = FlowDependencyResolver(self.source_agent_data)
        context_data = resolver.resolve(flow_wrapper)
        viz = FlowTreeVisualizer(context_data)

        buf = io.StringIO()
        Console(
            file=buf, width=200, force_terminal=False, color_system=None
        ).print(viz.build_tree())
        tree_view = buf.getvalue()

        # Step 2A: Architecture Expert Blueprinting
        blueprint_2a = await self.designer.run_step_2a(
            flow_name=flow_name, tree_view=tree_view, target_ir=self.ir
        )

        if "error" not in blueprint_2a:
            logger.info(
                f"[{flow_name}] Launching 2B (Instructions) and "
                "2C (Tools) concurrently..."
            )
            task_2b = self.designer.run_step_2b_instructions(
                flow_name, blueprint_2a, tree_view
            )
            task_2c = self.designer.run_step_2c_tools_and_callbacks(
                flow_name, blueprint_2a, tree_view, target_ir=self.ir
            )

            instructions_xml, tools_callbacks_data = await asyncio.gather(
                task_2b, task_2c
            )

            # Robustly extract the description
            agent_meta = blueprint_2a.get("agent_metadata", {})
            flow_description = (
                agent_meta.get("role")
                or agent_meta.get("primary_goal")
                or f"Migrated Flow: {flow_name}"
            )

            self.ir.agents[flow_name] = IRAgent(
                type="FLOW",
                display_name=self._sanitize_display_name(flow_name),
                description=flow_description,
                instruction=instructions_xml,
                blueprint=blueprint_2a,
                callbacks=tools_callbacks_data.get("callbacks", {}),
                tools=[],
                toolsets=[],
                status=MigrationStatus.COMPILED,
            )

            # 1. Store & IMMEDIATELY DEPLOY Generated Python Tools
            for tool in tools_callbacks_data.get("tools", []):
                tool_name = tool.get("name")
                safe_tool_id = self._sanitize_resource_id(tool_name)
                full_tool_name = (
                    f"{target_app_resource_name}/tools/{safe_tool_id}"
                )

                tool_payload = {
                    "name": safe_tool_id,
                    "displayName": tool_name,
                    "pythonFunction": {
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "python_code": tool.get("code", ""),
                    },
                }

                self.ir.tools[safe_tool_id] = IRTool(
                    type="PYTHON",
                    id=safe_tool_id,
                    name=full_tool_name,
                    payload=tool_payload,
                    status=MigrationStatus.COMPILED,
                )

                self.ir.agents[flow_name].tools.append(full_tool_name)

                # DEPLOY THE TOOL NOW
                logger.info(
                    f"[{flow_name}] Deploying generated Python tool: "
                    f"{safe_tool_id}"
                )
                try:
                    created_tool = self.ps_tools.create_tool(
                        app_id=target_app_resource_name,
                        tool_id=safe_tool_id,
                        tool_data=tool_payload,
                    )
                    if created_tool:
                        self.ir.tools[
                            safe_tool_id
                        ].status = MigrationStatus.DEPLOYED
                except Exception as e:
                    logger.error(
                        f"[{flow_name}] Failed to deploy tool "
                        f"{safe_tool_id}: {e}"
                    )

            # 1.5 MISSING LOGIC RESTORATION
            valid_display_names = {
                re.sub(r"[_\\-]+", " ", agent["display_name"])
                .strip()
                .lower(): agent["display_name"]
                for agent in self.ir.agents.values()
            }

            instructions_xml = re.sub(
                r"{@AGENT:\s*([^}]+)}",
                lambda m: MigrationService._fix_agent_ref(
                    m, valid_display_names
                ),
                instructions_xml,
            )
            self.ir.agents[flow_name].instruction = instructions_xml

            # B. Attach System end_session Tool
            end_session_res = f"{target_app_resource_name}/tools/end_session"
            if re.search(
                r"{@TOOL:\s*end_session\s*}", instructions_xml, re.IGNORECASE
            ):
                if end_session_res not in self.ir.agents[flow_name].tools:
                    self.ir.agents[flow_name].tools.append(end_session_res)
                    logger.info(
                        f"[{flow_name}] Attached system tool: end_session"
                    )

            # C. Attach Standard Tools/Toolsets referenced directly in XML
            xml_tools = re.findall(r"{@TOOL:\s*([^}]+)}", instructions_xml)
            for t_name in xml_tools:
                t_clean = t_name.strip()
                if t_clean == "end_session":
                    continue

                matched_tool = next(
                    (
                        tool
                        for tool in self.ir.tools.values()
                        if tool.payload.get("displayName") == t_clean
                        or tool.id == t_clean
                    ),
                    None,
                )
                if matched_tool:
                    if (
                        matched_tool.type == "TOOL"
                        and matched_tool.name
                        not in self.ir.agents[flow_name].tools
                    ):
                        self.ir.agents[flow_name].tools.append(
                            matched_tool.name
                        )
                    elif matched_tool.type == "TOOLSET":
                        ts_entry = {
                            "toolset": matched_tool.name,
                            "toolIds": [],
                        }
                        if ts_entry not in self.ir.agents[flow_name].toolsets:
                            self.ir.agents[flow_name].toolsets.append(ts_entry)

            # D. Attach OpenAPI Toolsets required by the Python Wrapper Code
            for py_tool in tools_callbacks_data.get("tools", []):
                py_code = py_tool.get("code", "")
                used_ops = re.findall(r"tools\.([a-zA-Z0-9_]+)", py_code)
                for op in used_ops:
                    for tool in self.ir.tools.values():
                        if tool.type == "TOOLSET" and op in tool.operation_ids:
                            ts_entry = {
                                "toolset": tool.name,
                                "toolIds": [],
                            }
                            if (
                                ts_entry
                                not in self.ir.agents[flow_name].toolsets
                            ):
                                self.ir.agents[flow_name].toolsets.append(
                                    ts_entry
                                )
                                logger.info(
                                    f"[{flow_name}] Attached backend toolset "
                                    "dependency: "
                                    f"{tool.payload.get('displayName', op)}"
                                )

            # E. DEPLOY THE AGENT (Missing Logic Restoration)
            logger.info(f"[{flow_name}] Deploying agent...")

            # Format Callbacks if they exist
            callback_payload = {}
            for cb_type, cb_code in tools_callbacks_data.get(
                "callbacks", {}
            ).items():
                if cb_code:
                    camel_key = (
                        re.sub(
                            r"_([a-z])", lambda m: m.group(1).upper(), cb_type
                        )
                        + "s"
                    )
                    callback_payload[camel_key] = [{"pythonCode": cb_code}]

            agent_payload = {
                "display_name": self.ir.agents[flow_name].display_name,
                "description": self.ir.agents[flow_name].description,
                "instruction": self.ir.agents[flow_name].instruction,
                "tools": self.ir.agents[flow_name].tools,
                "toolsets": self.ir.agents[flow_name].toolsets,
                "modelSettings": {"model": self.default_model},
            }
            agent_payload.update(callback_payload)

            try:
                new_agent = self.ps_agents.create_agent(
                    app_id=target_app_resource_name, agent_obj=agent_payload
                )
                if new_agent and "name" in new_agent:
                    logger.info(f"[{flow_name}] -> Success! Deployed Agent.")
                    self.ir.agents[flow_name].status = MigrationStatus.DEPLOYED
                    self.ir.agents[flow_name].resource_name = new_agent["name"]
                    self.reporter.log_agent(
                        flow_name,
                        new_agent["name"],
                        agent_payload["description"],
                        self.default_model,
                    )
                else:
                    logger.error(f"[{flow_name}] ❌ Failed to deploy agent.")
            except Exception as e:
                logger.error(
                    f"[{flow_name}] ❌ API Exception during agent "
                    f"deployment: {e}"
                )
        else:
            logger.error(f"[{flow_name}] Failed to generate blueprint.")
