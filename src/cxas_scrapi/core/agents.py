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

from typing import Any, Dict, List, Optional, Union

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud.ces_v1beta import AgentServiceClient, types
from google.protobuf import field_mask_pb2, json_format

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.workflows import WorkflowAgent


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
            credentials=self.creds,
            client_options=self.client_options,
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
        agent_type: str = "llm",  # llm, dfcx, workflow
        model: Optional[str] = "gemini-2.5-flash",
        instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        dfcx_agent_resource: Optional[str] = None,
        workflow_config: Union[Dict[str, Any], WorkflowAgent, None] = None,
        **kwargs: Any,
    ) -> types.Agent:
        """Creates a new agent of the specified type.

        Args:
            display_name: Human readable name.
            agent_id: Optional agent ID.
            agent_type: One of 'llm', 'dfcx', 'workflow'.
            model: (LLM) Model name to use.
            instruction: (LLM) System instruction.
            timeout: (LLM) Timeout (not standard field yet? ignoring for
                now or mapping to model_settings).
            dfcx_agent_resource: (DFCX) Full resource name of DFCX agent.
            workflow_config: (Workflow) Dict config or WorkflowAgent object.
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

        elif agent_type == "workflow":
            if workflow_config:
                if isinstance(workflow_config, WorkflowAgent):
                    agent_data["workflow_agent"] = workflow_config.to_dict()
                else:
                    agent_data["workflow_agent"] = workflow_config
            else:
                # Maybe empty workflow?
                agent_data["workflow_agent"] = {}

        else:
            raise ValueError(f"Unknown agent_type: {agent_type}")

        if agent_type == "workflow":
            # REST Fallback for Workflow Agents to bypass local proto
            # descriptor check internal "workflow_agent" field might be
            # missing in local descriptors

            # Construct URL
            # Endpoint from self.client.transport.host works but standard
            # is usually ces.googleapis.com
            # We can use Common logic or just standard "https://ces.googleapis.com"
            api_endpoint = "https://ces.googleapis.com"
            url = f"{api_endpoint}/v1beta/{self.app_name}/agents"

            # Refresh token just in case
            if self.creds.expired:
                self.creds.refresh(GoogleAuthRequest())

            headers = {
                "Authorization": f"Bearer {self.creds.token}",
                "Content-Type": "application/json",
                "x-goog-user-project": self.project_id or "",  # Best effort
            }

            response = requests.post(url, headers=headers, json=agent_data)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to create workflow agent via REST: {response.text}"
                )

            # Convert response JSON back to Agent object
            # We use ignore_unknown_fields=True just in case response
            # contains workflow_agent and we still can't parse it into the
            # local object, but at least we return a valid base object.
            return json_format.ParseDict(
                response.json(), types.Agent(), ignore_unknown_fields=True
            )

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
