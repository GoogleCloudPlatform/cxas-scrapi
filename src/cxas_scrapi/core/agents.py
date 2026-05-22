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

"""Core Agents class for CXAS Scrapi."""

from typing import Any, Dict, List, Optional

from google.cloud.ces_v1beta import AgentServiceClient, types
from google.protobuf import field_mask_pb2

from cxas_scrapi.core.apps import Apps


class Agents(Apps):
    """Core Class for managing Agent Resources."""

    def __init__(
        self,
        app_name: str,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        """Initializes the Agents client."""
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]

        super().__init__(
            project_id=project_id,
            location=location,
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )

        self.app_name = app_name
        self.resource_type = "agents"
        self.client = AgentServiceClient(
            transport=self.get_grpc_transport(AgentServiceClient),
            client_info=self.client_info,
        )

    def list_agents(self) -> List[types.Agent]:
        """Lists agents within the app."""
        request = types.ListAgentsRequest(parent=self.app_name)
        response = self.client.list_agents(request=request)
        return list(response)

    def get_agents_map(self, reverse: bool = False) -> Dict[str, str]:
        """Creates a map of Agent full names to display names.

        Args:
            reverse: If True, map display_name -> name.
        """
        agents = self.list_agents()
        agents_dict: Dict[str, str] = {}

        for agent in agents:
            display_name = agent.display_name
            name = agent.name
            if display_name and name:
                if reverse:
                    agents_dict[display_name] = name
                else:
                    agents_dict[name] = display_name
        return agents_dict

    def get_agent(self, agent_name: str) -> types.Agent:
        """Gets a specific agent by its full resource name."""
        request = types.GetAgentRequest(name=agent_name)
        return self.client.get_agent(request=request)

    def create_agent(
        self,
        display_name: str,
        agent_id: str = "",
        agent_type: str = "llm",  # llm, dfcx
        model: Optional[str] = "gemini-2.5-flash",
        instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        dfcx_agent_resource: Optional[str] = None,
        **kwargs: Any,
    ) -> types.Agent:
        """Creates a new agent of the specified type.

        Args:
            display_name: Human readable name.
            agent_id: Optional agent ID.
            agent_type: One of 'llm', 'dfcx'.
            model: (LLM) Model name to use.
            instruction: (LLM) System instruction.
            timeout: (LLM) Timeout (not standard field yet? ignoring for
                now or mapping to model_settings).
            dfcx_agent_resource: (DFCX) Full resource name of DFCX agent.
            **kwargs: Additional fields for types.Agent.
        """
        agent_data = {"display_name": display_name, **kwargs}
        if agent_type == "llm":
            # Construct LLM Agent

            if instruction:
                agent_data["instruction"] = instruction

            if model:
                # Assuming top-level model_settings
                agent_data["model_settings"] = types.ModelSettings(model=model)

            # Explicitly set llm_agent to indicate this is an LLM Agent
            agent_data["llm_agent"] = types.Agent.LlmAgent()

        elif agent_type == "dfcx":
            if not dfcx_agent_resource:
                raise ValueError(
                    "dfcx_agent_resource is required for DFCX agents."
                )

            agent_data["remote_dialogflow_agent"] = (
                types.Agent.RemoteDialogflowAgent(agent=dfcx_agent_resource)
            )

        else:
            raise ValueError(f"Unknown agent_type: {agent_type}")

        request = types.CreateAgentRequest(
            parent=self.app_name, agent=agent_data, agent_id=agent_id
        )
        return self.client.create_agent(request=request)

    def update_agent(self, agent_name: str, **kwargs: Any) -> types.Agent:
        """Updates specific fields using PATCH behavior."""
        if not kwargs:
            return self.get_agent(agent_name)

        # Construct Agent object with only updated fields (for the body)
        agent_data = kwargs.copy()
        agent_data["name"] = agent_name

        # Update Mask
        paths = list(kwargs.keys())
        mask = field_mask_pb2.FieldMask(paths=paths)

        request = types.UpdateAgentRequest(agent=agent_data, update_mask=mask)
        return self.client.update_agent(request=request)

    def delete_agent(self, agent_name: str):
        """Deletes an agent."""
        request = types.DeleteAgentRequest(name=agent_name)
        self.client.delete_agent(request=request)
