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

"""Exporter module for Dialogflow CX resources."""

import concurrent.futures
import io
import json
import logging
import re
import traceback
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from google.api_core import exceptions as api_exceptions
from google.cloud.dialogflowcx_v3beta1 import services as cx_services
from google.cloud.dialogflowcx_v3beta1 import types as cx_types
from google.protobuf.json_format import MessageToDict

from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    DFCXFlowModel,
    DFCXPageModel,
)

logger = logging.getLogger(__name__)


class BaseDFCXClient:
    """Base class for Dialogflow CX API clients to handle common logic."""

    def _get_client_options(self, resource_id: str) -> Optional[Dict[str, str]]:
        """Extracts region and returns client options with the regional
        endpoint."""
        if not isinstance(resource_id, str):
            return None
        match = re.search(r"projects/[^/]+/locations/([^/]+)/", resource_id)
        region = match.group(1) if match else "global"  # Default to global
        if not region:
            logger.error(
                f"Error: Could not parse region from resource ID: {resource_id}"
            )
            return None

        if region != "global":
            endpoint = {"api_endpoint": f"{region}-dialogflow.googleapis.com"}
        else:
            endpoint = {"api_endpoint": "dialogflow.googleapis.com"}
        return endpoint


@dataclass
class _ResourceProcessingContext:
    """Holds state for processing resources within a DFCX agent zip."""

    zip_file: zipfile.ZipFile
    agent_id: str
    intent_map: Dict[str, Any] = field(default_factory=dict)
    playbook_map: Dict[str, Any] = field(default_factory=dict)
    tool_map: Dict[str, Any] = field(default_factory=dict)
    entity_map: Dict[str, Any] = field(default_factory=dict)
    webhook_map: Dict[str, Any] = field(default_factory=dict)
    flow_map: Dict[str, Any] = field(default_factory=dict)
    generator_map: Dict[str, Any] = field(default_factory=dict)
    agent_trg_map: Dict[str, Any] = field(default_factory=dict)
    dir_name_to_full_name: Dict[str, Dict[str, str]] = field(
        default_factory=dict
    )
    display_name_to_id: Dict[str, str] = field(default_factory=dict)


class DFCXAgentExporter(BaseDFCXClient):
    """Client for exporting Dialogflow CX Agents."""

    @staticmethod
    def _get_full_name(
        agent_id: str, resource_type: str, resource_id: str
    ) -> str:
        """Constructs the full resource name."""
        return f"{agent_id}/{resource_type}/{resource_id}"

    @staticmethod
    def _process_flat_resource(
        ctx: "_ResourceProcessingContext", path_parts: List[str], filename: str
    ):
        """Processes flat resources like webhooks and
        agentTransitionRouteGroups."""
        res_type = path_parts[0]
        with ctx.zip_file.open(filename) as f:
            content = json.load(f)
        res_id = content.get("name") or path_parts[1].replace(".json", "")
        full_name = DFCXAgentExporter._get_full_name(
            ctx.agent_id, res_type, res_id
        )
        content["name"] = full_name
        if res_type == "webhooks":
            ctx.webhook_map[full_name] = content
        elif res_type == "agentTransitionRouteGroups":
            ctx.agent_trg_map[full_name] = content
        ctx.dir_name_to_full_name.setdefault(res_type, {})[
            path_parts[1].replace(".json", "")
        ] = full_name
        if content.get("displayName"):
            ctx.display_name_to_id[content["displayName"]] = res_id

    @staticmethod
    def _process_standard_resource(
        ctx: "_ResourceProcessingContext", path_parts: List[str], filename: str
    ):
        """Processes standard resources with type/name/name.json structure."""
        res_type = path_parts[0]
        with ctx.zip_file.open(filename) as f:
            content = json.load(f)
        res_id = content.get("name") or content.get("flowId")
        if not res_id:
            res_id = path_parts[-2]
        full_name = DFCXAgentExporter._get_full_name(
            ctx.agent_id, res_type, res_id
        )
        content["name"] = full_name
        if res_type == "intents":
            ctx.intent_map[full_name] = content
        elif res_type == "playbooks":
            ctx.playbook_map[full_name] = content
        elif res_type == "tools":
            ctx.tool_map[full_name] = content
        elif res_type == "entityTypes":
            ctx.entity_map[full_name] = content
        elif res_type == "flows":
            ctx.flow_map[full_name] = {
                "flow": content,
                "pages": [],
            }
        elif res_type == "generators":
            ctx.generator_map[full_name] = content
        ctx.dir_name_to_full_name.setdefault(res_type, {})[path_parts[-2]] = (
            full_name
        )
        if content.get("displayName"):
            ctx.display_name_to_id[content["displayName"]] = res_id

    @staticmethod
    def _process_generative_settings(
        zip_file: zipfile.ZipFile,
        filename: str,
        generative_settings: Dict[str, Any],
    ) -> None:
        """Processes a generative settings file from the zip."""
        lang = filename.rsplit("/", maxsplit=1)[-1].replace(".json", "")
        with zip_file.open(filename) as f:
            generative_settings[lang] = json.load(f)

    @staticmethod
    def _process_test_cases(
        zip_file: zipfile.ZipFile,
        filename: str,
        test_cases_list: List[Dict[str, Any]],
    ) -> None:
        """Processes a test case file from the zip."""
        with zip_file.open(filename) as f:
            test_cases_list.append(json.load(f))

    @staticmethod
    def _process_intent_training_phrases(
        zip_file: zipfile.ZipFile,
        filename: str,
        intent_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes intent training phrases from the zip."""
        with zip_file.open(filename) as f:
            tp_content = json.load(f)
        intent_map.setdefault(full_resource_name, {}).setdefault(
            "trainingPhrases", []
        ).extend(tp_content.get("trainingPhrases", []))

    @staticmethod
    def _process_entity_type_entities(
        zip_file: zipfile.ZipFile,
        filename: str,
        entity_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes entity type entities from the zip."""
        with zip_file.open(filename) as f:
            entity_content = json.load(f)
        entity_map.setdefault(full_resource_name, {}).setdefault(
            "entities", []
        ).extend(entity_content.get("entities", []))

    @staticmethod
    def _process_flow_pages(
        zip_file: zipfile.ZipFile,
        filename: str,
        flow_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes flow pages from the zip."""
        with zip_file.open(filename) as f:
            page_content = json.load(f)
        page_key = page_content.get("name")
        if page_key:
            flow_map.setdefault(full_resource_name, {}).setdefault(
                "pages", []
            ).append({"key": page_key, "value": page_content})

    @staticmethod
    def _process_playbook_examples(
        zip_file: zipfile.ZipFile,
        filename: str,
        playbook_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes playbook examples from the zip."""
        if full_resource_name in playbook_map:
            with zip_file.open(filename) as f:
                ex_content = json.load(f)
            if "name" in ex_content:
                ex_content["name"] = (
                    f"{full_resource_name}/examples/{ex_content['name']}"
                )
            playbook_map[full_resource_name].setdefault("examples", []).append(
                ex_content
            )

    @staticmethod
    def _process_tool_schema(
        zip_file: zipfile.ZipFile,
        filename: str,
        tool_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes tool schema from the zip."""
        with zip_file.open(filename) as f:
            schema_content = f.read().decode("utf-8")
        tool_map.setdefault(full_resource_name, {}).setdefault(
            "openApiSpec", {}
        )["textSchema"] = schema_content

    @staticmethod
    def _process_generator_phrases(
        zip_file: zipfile.ZipFile,
        filename: str,
        generator_map: Dict[str, Any],
        full_resource_name: str,
    ) -> None:
        """Processes generator phrases from the zip."""
        lang = filename.rsplit("/", maxsplit=1)[-1].replace(".json", "")
        with zip_file.open(filename) as f:
            phrase_content = json.load(f)
        generator_map.setdefault(full_resource_name, {}).setdefault(
            "phrases", {}
        )[lang] = phrase_content

    def process_zip_content(
        self, zip_content: bytes, agent_id_fallback: str
    ) -> Optional[DFCXAgentIR]:
        """Parses raw ZIP bytes into the full agent IR structure."""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zip_file:
                logger.info("Zip file opened in memory. Parsing contents...")
                namelist = zip_file.namelist()

                # 1. Detect Root
                agent_json_path = None
                for name in namelist:
                    parts = name.split("/")
                    if parts[-1] == "agent.json" and len(parts) <= 2:
                        agent_json_path = name
                        break

                if not agent_json_path:
                    logger.error("agent.json not found in zip.")
                    return None

                base_path = agent_json_path[: -len("agent.json")]
                logger.info(f"Detected base path in zip: '{base_path}'")

                with zip_file.open(agent_json_path) as f:
                    agent_data = json.load(f)

                if "name" not in agent_data or not agent_data["name"]:
                    agent_data["name"] = agent_id_fallback

                agent_id = agent_data["name"]

                # Initialize processing context
                ctx = _ResourceProcessingContext(
                    zip_file=zip_file,
                    agent_id=agent_id,
                )
                test_cases_list = []
                generative_settings = {}

                # First pass: Load main components
                for filename in sorted(namelist):
                    if not filename.startswith(base_path):
                        continue

                    rel_path = filename[len(base_path) :]
                    if (
                        not rel_path.endswith(".json")
                        or rel_path == "agent.json"
                    ):
                        continue

                    path_parts = rel_path.split("/")

                    # Flat resources: webhooks, agentTransitionRouteGroups
                    if len(path_parts) == 2 and path_parts[0] in [
                        "webhooks",
                        "agentTransitionRouteGroups",
                    ]:
                        DFCXAgentExporter._process_flat_resource(
                            ctx, path_parts, filename
                        )

                    # Standard resources: type/name/name.json
                    elif len(path_parts) >= 2 and path_parts[-2] == path_parts[
                        -1
                    ].replace(".json", ""):
                        DFCXAgentExporter._process_standard_resource(
                            ctx, path_parts, filename
                        )

                # Second pass: Merge sub-components and handle special folders
                for filename in sorted(namelist):
                    if not filename.startswith(base_path):
                        continue

                    rel_path = filename[len(base_path) :]

                    # Generative Settings
                    if rel_path.startswith("generativeSettings/"):
                        if rel_path.endswith(".json"):
                            DFCXAgentExporter._process_generative_settings(
                                zip_file, filename, generative_settings
                            )
                        continue

                    # Test Cases
                    if rel_path.startswith("testCases/"):
                        if rel_path.endswith(".json"):
                            DFCXAgentExporter._process_test_cases(
                                zip_file, filename, test_cases_list
                            )
                        continue

                    path_parts = rel_path.split("/")
                    if len(path_parts) < 2:
                        continue

                    res_type = path_parts[0]
                    resource_dir_name = path_parts[1]
                    full_resource_name = ctx.dir_name_to_full_name.get(
                        res_type, {}
                    ).get(resource_dir_name)
                    if not full_resource_name:
                        continue

                    # Intents -> trainingPhrases
                    if (
                        res_type == "intents"
                        and path_parts[-2] == "trainingPhrases"
                        and rel_path.endswith(".json")
                    ):
                        DFCXAgentExporter._process_intent_training_phrases(
                            zip_file,
                            filename,
                            ctx.intent_map,
                            full_resource_name,
                        )

                    # EntityTypes -> entities
                    elif (
                        res_type == "entityTypes"
                        and path_parts[-2] == "entities"
                        and rel_path.endswith(".json")
                    ):
                        DFCXAgentExporter._process_entity_type_entities(
                            zip_file,
                            filename,
                            ctx.entity_map,
                            full_resource_name,
                        )

                    # Flows -> pages
                    elif (
                        res_type == "flows"
                        and path_parts[-2] == "pages"
                        and rel_path.endswith(".json")
                    ):
                        DFCXAgentExporter._process_flow_pages(
                            zip_file, filename, ctx.flow_map, full_resource_name
                        )

                    # Playbooks -> examples
                    elif (
                        res_type == "playbooks"
                        and path_parts[-2] == "examples"
                        and rel_path.endswith(".json")
                    ):
                        DFCXAgentExporter._process_playbook_examples(
                            zip_file,
                            filename,
                            ctx.playbook_map,
                            full_resource_name,
                        )

                    # Tools -> schema.yaml
                    elif (
                        res_type == "tools" and path_parts[-1] == "schema.yaml"
                    ):
                        DFCXAgentExporter._process_tool_schema(
                            zip_file, filename, ctx.tool_map, full_resource_name
                        )

                    # Generators -> phrases
                    elif (
                        res_type == "generators"
                        and path_parts[-2] == "phrases"
                        and rel_path.endswith(".json")
                    ):
                        DFCXAgentExporter._process_generator_phrases(
                            zip_file,
                            filename,
                            ctx.generator_map,
                            full_resource_name,
                        )

                # Resolve Playbook references
                processed_playbooks = []
                for _pb_name, pb_data in ctx.playbook_map.items():
                    if "referencedPlaybooks" in pb_data:
                        resolved_refs = [
                            DFCXAgentExporter._get_full_name(
                                agent_id,
                                "playbooks",
                                ctx.display_name_to_id[dn],
                            )
                            for dn in pb_data["referencedPlaybooks"]
                            if dn in ctx.display_name_to_id
                        ]
                        pb_data["referencedPlaybooks"] = resolved_refs
                    if "referencedTools" in pb_data:
                        resolved_refs = [
                            DFCXAgentExporter._get_full_name(
                                agent_id, "tools", ctx.display_name_to_id[dn]
                            )
                            for dn in pb_data["referencedTools"]
                            if dn in ctx.display_name_to_id
                        ]
                        pb_data["referencedTools"] = resolved_refs
                    processed_playbooks.append(pb_data)

                # Reorder playbooks to put start playbook first
                start_pb_display_name = agent_data.get("startPlaybook")
                if (
                    start_pb_display_name
                    and start_pb_display_name in ctx.display_name_to_id
                ):
                    start_playbook_full_name = DFCXAgentExporter._get_full_name(
                        agent_id,
                        "playbooks",
                        ctx.display_name_to_id[start_pb_display_name],
                    )
                    try:
                        start_pb_index = next(
                            i
                            for i, pb in enumerate(processed_playbooks)
                            if pb["name"] == start_playbook_full_name
                        )
                        start_pb_obj = processed_playbooks.pop(start_pb_index)
                        processed_playbooks.insert(0, start_pb_obj)
                    except StopIteration:
                        pass

                # Build DFCXAgentIR
                flows_list = []
                for flow_full_name, flow_stuff in ctx.flow_map.items():
                    pages_list = [
                        DFCXPageModel(page_id=p["key"], page_data=p["value"])
                        for p in flow_stuff["pages"]
                    ]
                    flows_list.append(
                        DFCXFlowModel(
                            flow_id=flow_full_name,
                            flow_data=flow_stuff["flow"],
                            pages=pages_list,
                        )
                    )

                agent_ir = DFCXAgentIR(
                    name=agent_id,
                    display_name=agent_data.get("displayName", ""),
                    default_language_code=agent_data.get(
                        "defaultLanguageCode", ""
                    ),
                    supported_language_codes=agent_data.get(
                        "supportedLanguageCodes", []
                    ),
                    time_zone=agent_data.get("timeZone"),
                    description=agent_data.get("description"),
                    start_flow=DFCXAgentExporter._get_full_name(
                        agent_id,
                        "flows",
                        ctx.display_name_to_id.get(
                            agent_data.get("startFlow"), ""
                        ),
                    )
                    if agent_data.get("startFlow") in ctx.display_name_to_id
                    else agent_data.get("startFlow"),
                    start_playbook=DFCXAgentExporter._get_full_name(
                        agent_id,
                        "playbooks",
                        ctx.display_name_to_id.get(
                            agent_data.get("startPlaybook"), ""
                        ),
                    )
                    if agent_data.get("startPlaybook") in ctx.display_name_to_id
                    else agent_data.get("startPlaybook"),
                    intents=list(ctx.intent_map.values()),
                    tools=list(ctx.tool_map.values()),
                    entity_types=list(ctx.entity_map.values()),
                    webhooks=list(ctx.webhook_map.values()),
                    flows=flows_list,
                    playbooks=processed_playbooks,
                    test_cases=test_cases_list,
                    generative_settings=generative_settings,
                    generators=list(ctx.generator_map.values()),
                    agent_transition_route_groups=list(
                        ctx.agent_trg_map.values()
                    ),
                )

                return agent_ir

        except Exception as e:
            logger.error(f"Error processing zip content: {e}")
            traceback.print_exc()
            return None

    def export_agent_to_json(self, agent_id: str) -> Optional[DFCXAgentIR]:
        """Exports the agent and returns its contents as a DFCXAgentIR
        object."""
        client_options = self._get_client_options(agent_id)
        if not client_options:
            return None
        client = cx_services.agents.AgentsClient(client_options=client_options)
        request = cx_types.ExportAgentRequest(
            name=agent_id,
            data_format=cx_types.ExportAgentRequest.DataFormat.JSON_PACKAGE,
        )
        logger.info(f"Initiating agent export for {agent_id}...")
        operation = client.export_agent(request=request)

        logger.info("Waiting for export operation to complete...")
        response = operation.result(timeout=300)
        logger.info("Export operation finished.")

        if not response.agent_content:
            raise Exception("Agent export returned empty content.")

        logger.info(
            f"Agent export completed. Size: {len(response.agent_content)} "
            f"bytes."
        )

        # Delegate to the shared processing method
        return self.process_zip_content(response.agent_content, agent_id)


class DFCXAgents(BaseDFCXClient):
    """Client for interacting with Dialogflow CX Agents."""

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the full details of a Dialogflow CX Agent."""
        client_options = self._get_client_options(agent_id)
        if not client_options:
            return None
        try:
            client = cx_services.agents.AgentsClient(
                client_options=client_options
            )
            request = cx_types.GetAgentRequest(name=agent_id)
            response = client.get_agent(request=request)
            return MessageToDict(response._pb)
        except Exception as e:
            logger.error(f"Error getting agent '{agent_id}': {e}")
            return None


class DFCXPlaybooks(BaseDFCXClient):
    """Client for interacting with Dialogflow CX Playbooks."""

    def list_playbooks(self, agent_id: str) -> List[Dict[str, Any]]:
        """Lists all playbooks for a given agent."""
        client_options = self._get_client_options(agent_id)
        if not client_options:
            return []
        try:
            client = cx_services.playbooks.PlaybooksClient(
                client_options=client_options
            )
            request = cx_types.ListPlaybooksRequest(parent=agent_id)
            playbooks = client.list_playbooks(request=request)
            return [MessageToDict(pb._pb) for pb in playbooks]
        except Exception as e:
            logger.error(f"Error listing playbooks for agent '{agent_id}': {e}")
            return []


class DFCXTools(BaseDFCXClient):
    """Client for interacting with Dialogflow CX Tools."""

    def list_tools(self, agent_id: str) -> List[Dict[str, Any]]:
        """Lists all tools for a given agent."""
        client_options = self._get_client_options(agent_id)
        if not client_options:
            return []
        try:
            client = cx_services.tools.ToolsClient(
                client_options=client_options
            )
            request = cx_types.ListToolsRequest(parent=agent_id)
            tools = client.list_tools(request=request)
            return [MessageToDict(t._pb) for t in tools]
        except Exception as e:
            logger.error(f"Error listing tools for agent '{agent_id}': {e}")
            return []


class DFCXGenerativeSettings(BaseDFCXClient):
    """Client for interacting with Dialogflow CX Agent GenerativeSettings."""

    def get_generative_settings(
        self, agent_id: str, language_code: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieves the generative settings for a given agent."""
        client_options = self._get_client_options(agent_id)
        if not client_options:
            return None
        try:
            settings_name = f"{agent_id}/generativeSettings"
            client = cx_services.agents.AgentsClient(
                client_options=client_options
            )
            request = cx_types.GetGenerativeSettingsRequest(
                name=settings_name, language_code=language_code
            )
            response = client.get_generative_settings(request=request)
            return MessageToDict(response._pb)
        except api_exceptions.NotFound:
            logger.info(
                "No custom generative settings found for this agent. "
                "Using defaults."
            )
            return None
        except Exception as e:
            logger.error(
                f"Error getting generative settings for agent '{agent_id}': {e}"
            )
            return None


class ConversationalAgentsAPI:
    """Facade class to access all Dialogflow CX resources for migration."""

    def __init__(self):
        self.agents = DFCXAgents()
        self.playbooks = DFCXPlaybooks()
        self.tools = DFCXTools()
        self.generative_settings = DFCXGenerativeSettings()
        self.export_agent = DFCXAgentExporter()

    def fetch_full_agent_details(
        self, agent_id: str, use_export: bool = True
    ) -> Optional[DFCXAgentIR]:
        """Fetches the complete agent configuration, including all nested
        resources.

        Uses either parallel API calls or the ExportAgent method.
        """
        if use_export:
            logger.info(
                f"Starting import for agent via ExportAgent: {agent_id}..."
            )
            return self.export_agent.export_agent_to_json(agent_id)

        logger.info(f"Starting import for agent via API calls: {agent_id}...")
        # TODO: If using use_export=False, this method currently does not
        # include Flows and Pages.
        # This is a work in progress. Use use_export=True for full agent
        # extraction.
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_agent = executor.submit(self.agents.get_agent, agent_id)
            future_tools = executor.submit(self.tools.list_tools, agent_id)
            future_playbooks = executor.submit(
                self.playbooks.list_playbooks, agent_id
            )

            agent_details = future_agent.result()
            if not agent_details:
                logger.error(
                    "Failed to fetch core agent details. Aborting migration."
                )
                return None

            language_code = agent_details.get("defaultLanguageCode", "en")
            future_gen_settings = executor.submit(
                self.generative_settings.get_generative_settings,
                agent_id,
                language_code,
            )

            tools_list = future_tools.result()
            playbooks_list = future_playbooks.result()
            gen_settings = future_gen_settings.result()

            agent_ir = DFCXAgentIR(
                name=agent_details.get("name", ""),
                display_name=agent_details.get("displayName", ""),
                default_language_code=language_code,
                supported_language_codes=agent_details.get(
                    "supportedLanguageCodes", []
                ),
                time_zone=agent_details.get("timeZone"),
                description=agent_details.get("description"),
                start_flow=agent_details.get("startFlow"),
                start_playbook=agent_details.get("startPlaybook"),
                intents=[],
                tools=tools_list,
                entity_types=[],
                webhooks=[],
                flows=[],
                playbooks=playbooks_list,
                test_cases=[],
                generative_settings={language_code: gen_settings}
                if gen_settings
                else {},
                generators=[],
                agent_transition_route_groups=[],
            )

            logger.info(
                "Successfully imported available agent components via API."
            )
            return agent_ir

    def process_local_agent_zip(
        self, zip_bytes: bytes
    ) -> Optional[DFCXAgentIR]:
        """Processes a local zip file without calling the API."""
        dummy_id = (
            "projects/local-upload/locations/global/agents/uploaded-agent"
        )
        return self.export_agent.process_zip_content(zip_bytes, dummy_id)
