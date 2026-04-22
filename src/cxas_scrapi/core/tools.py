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

"""Core Tools class for CXAS Scrapi."""

from typing import Any, Dict, List, Optional

import requests
import yaml
from google.cloud.ces_v1beta import (
    AgentServiceClient,
    ToolServiceClient,
    types,
)
from google.protobuf import field_mask_pb2
from google.protobuf.json_format import MessageToDict

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.variables import Variables


class Tools(Apps):
    """Core Class for managing Tool and Toolset Resources."""

    def __init__(
        self,
        app_name: str,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        """Initializes the Tools client."""
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
        self.app_id = app_name.rsplit("/", maxsplit=1)[-1]
        self.resource_type = "tools"
        self.client = AgentServiceClient(
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )
        self.tool_client = ToolServiceClient(
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )
        self.var_client = Variables(
            app_name=app_name,
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
        )
        self.tools_map: Dict[str, str] = {}

    @staticmethod
    def _is_toolset(tool_name: str) -> bool:
        """Helper to determine if a full resource name refers to a Toolset."""
        return "/toolsets/" in tool_name

    @staticmethod
    def _parse_openapi_schema(
        schema_str: str, display_name: str, tool_name: str, reverse: bool
    ) -> Dict[str, str]:
        """Parses an OpenAPI schema to extract tool endpoints locally."""
        parsed_tools: Dict[str, str] = {}
        try:
            schema = yaml.safe_load(schema_str)
            for _path, methods in schema.get("paths", {}).items():
                if not isinstance(methods, dict):
                    continue
                for _method, details in methods.items():
                    if not isinstance(details, dict):
                        continue
                    op_id = details.get("operationId")
                    if op_id:
                        tool_display_name = f"{display_name}_{op_id}"
                        if reverse:
                            parsed_tools[tool_display_name] = (
                                f"{tool_name}/tools/{op_id}"
                            )
                        else:
                            parsed_tools[f"{tool_name}/tools/{op_id}"] = (
                                tool_display_name
                            )
        except Exception as e:
            print(
                f"[WARNING] Failed to parse OpenAPI schema for "
                f"{display_name}: {e}"
            )
        return parsed_tools

    def _get_final_variables(
        self,
        variables: Optional[Any],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Resolves the variables to pass to the tool payload."""
        # Variables logic, context takes precedence over variables
        final_variables = {}

        if context and isinstance(context, dict) and "state" in context:
            final_variables = context["state"]
        elif isinstance(variables, dict):
            final_variables = variables

        if not final_variables:
            # Fetch variables from the app and filter by this list of names.
            raw_app_vars = self.var_client.list_variables()

            app_default_vars_cache = {}
            for var in raw_app_vars:
                try:
                    var_dict = MessageToDict(var._pb)
                except AttributeError:
                    var_dict = MessageToDict(var)

                schema = var_dict.get("schema", {})
                actual_data = (
                    schema.get("default") or var_dict.get("value") or {}
                )
                app_default_vars_cache[var.name] = actual_data

            if isinstance(variables, list):
                # If variables is a list, filter the app's default variables
                # by this list
                for var_name in variables:
                    if var_name in app_default_vars_cache:
                        final_variables[var_name] = app_default_vars_cache[
                            var_name
                        ]
                    else:
                        print(
                            f"[WARNING] App variable '{var_name}' requested "
                            f"but not found in app."
                        )
            else:
                # No variables specified, use default variable values
                final_variables = app_default_vars_cache
        return final_variables

    def get_tools_map(self, reverse: bool = False) -> Dict[str, str]:
        """Creates a map of Tool and Toolset full names to display names.

        Args:
            reverse: If True, map display_name -> name.
        """
        tools = self.list_tools()
        tools_dict: Dict[str, str] = {}

        for tool in tools:
            if self._is_toolset(tool.name):
                # Try to parse OpenAPI toolsets locally to avoid excessive
                # API calls
                if getattr(tool, "open_api_toolset", None):
                    schema_str = getattr(
                        tool.open_api_toolset, "open_api_schema", None
                    )
                    if schema_str:
                        openapi_tools = self._parse_openapi_schema(
                            schema_str, tool.display_name, tool.name, reverse
                        )
                        tools_dict.update(openapi_tools)
                else:
                    toolset_tools = self.retrieve_tools(
                        tool.name.split("/")[-1]
                    )
                    for toolset_tool in toolset_tools.tools:
                        if reverse:
                            tools_dict[toolset_tool.display_name] = (
                                toolset_tool.name
                            )
                        else:
                            tools_dict[toolset_tool.name] = (
                                toolset_tool.display_name
                            )
            elif reverse:
                tools_dict[tool.display_name] = tool.name
            else:
                tools_dict[tool.name] = tool.display_name

        return tools_dict

    def _get_or_load_tools_map(self) -> Dict[str, str]:
        """Gets a reverse map of tools from cache or loads it if missing."""
        if not self.tools_map:
            self.tools_map = self.get_tools_map(reverse=True)
        return self.tools_map

    def list_tools(self) -> List[Any]:
        """Lists both tools and toolsets within a specific app."""
        tools_request = types.ListToolsRequest(parent=self.app_name)
        tools_response = self.client.list_tools(request=tools_request)

        toolsets_request = types.ListToolsetsRequest(parent=self.app_name)
        toolsets_response = self.client.list_toolsets(request=toolsets_request)

        return list(tools_response) + list(toolsets_response)

    def get_tool(self, tool_name: str) -> Any:
        """Gets a specific tool or toolset by full resource name."""
        if self._is_toolset(tool_name):
            request = types.GetToolsetRequest(name=tool_name)
            return self.client.get_toolset(request=request)
        else:
            request = types.GetToolRequest(name=tool_name)
            return self.client.get_tool(request=request)

    def create_tool(
        self,
        tool_id: str,
        display_name: str,
        payload: Dict[str, Any],
        tool_type: str = "python_function",
        description: str = "",
    ) -> Any:
        """Creates a new tool or toolset.

        If tool_type implies a toolset, it creates a Toolset wrapper
        (e.g. open_api_toolset). Otherwise it creates a standard Tool wrapper
        (e.g. python_function).
        """
        is_toolset = tool_type in [
            "open_api_toolset",
            "connector_toolset",
            "mcp_toolset",
        ]

        payload_copy = payload.copy()
        payload_copy.pop("display_name", None)

        if is_toolset:
            desc = payload_copy.pop("description", description)
            kwargs = {
                "display_name": display_name,
                "description": desc,
                tool_type: payload_copy,
            }
            toolset = types.Toolset(**kwargs)
            request = types.CreateToolsetRequest(
                parent=self.app_name, toolset_id=tool_id, toolset=toolset
            )
            return self.client.create_toolset(request=request)
        else:
            if description and "description" not in payload_copy:
                payload_copy["description"] = description

            kwargs = {"display_name": display_name, tool_type: payload_copy}
            tool = types.Tool(**kwargs)
            request = types.CreateToolRequest(
                parent=self.app_name, tool_id=tool_id, tool=tool
            )
            return self.client.create_tool(request=request)

    def update_tool(self, tool_name: str, **kwargs) -> Any:
        """Updates specific fields of an existing Tool or Toolset."""
        mask_paths = list(kwargs.keys())

        if self._is_toolset(tool_name):
            toolset = types.Toolset(name=tool_name)
            for key, value in kwargs.items():
                setattr(toolset, key, value)

            request = types.UpdateToolsetRequest(
                toolset=toolset,
                update_mask=field_mask_pb2.FieldMask(paths=mask_paths),
            )
            return self.client.update_toolset(request=request)
        else:
            tool = types.Tool(name=tool_name)
            for key, value in kwargs.items():
                setattr(tool, key, value)

            request = types.UpdateToolRequest(
                tool=tool,
                update_mask=field_mask_pb2.FieldMask(paths=mask_paths),
            )
            return self.client.update_tool(request=request)

    def delete_tool(self, tool_name: str) -> None:
        """Deletes a specific tool or toolset."""
        if self._is_toolset(tool_name):
            request = types.DeleteToolsetRequest(name=tool_name)
            self.client.delete_toolset(request=request)
        else:
            request = types.DeleteToolRequest(name=tool_name)
            self.client.delete_tool(request=request)

    def execute_tool(
        self,
        tool_display_name: str,
        args: Optional[Dict[str, Any]] = None,
        variables: Optional[Any] = None,  # Accepts Dict, List[str], or None
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Executes a tool directly via the CES API.

        Args:
            tool_display_name: The display name of the tool (or toolset key).
            args: Dictionary of arguments for the tool.
            variables: Can be:
                - None: Fetches and passes ALL variables from the app.
                - List[str]: Fetches variables from the app and filters by
                  this list of names.
                - Dict[str, Any]: Uses the provided dictionary directly
                  (e.g. from Evals).
            context: ToolContext object available to the Python Function tool.
                     If context is provided, variables will be ignored.

        Returns:
            The tool execution response (JSON or Object).
        """
        # Use HTTP REST request instead of SDK because the current SDK version
        # is missing the 'variables' field in ExecuteToolRequest proto
        url = f"https://ces.googleapis.com/v1beta/{self.app_name}:executeTool"

        headers = {
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": self.project_id,
        }

        payload = {}

        tools_map = self._get_or_load_tools_map()
        tool_name = tools_map.get(tool_display_name)

        if not tool_name:
            raise ValueError(
                f"Tool '{tool_display_name}' not found in App "
                f"'{self.app_name}'. "
            )

        if "toolsets/" in tool_name and "/tools/" in tool_name:
            toolset_name, tool_id = tool_name.split("/tools/")
            payload["toolsetTool"] = {
                "toolset": toolset_name,
                "toolId": tool_id,
            }
        else:
            payload["tool"] = tool_name

        payload["args"] = args or {}

        final_variables = self._get_final_variables(variables, context)

        # Use context if provided, otherwise use variables.
        if context:
            context_copy = context.copy()
            if "state" in context_copy:
                context_copy["state"] = final_variables
            payload["context"] = context_copy
        else:
            payload["variables"] = final_variables

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()

        return response.json()

    def retrieve_tools(self, toolset_id: str) -> Any:
        """Retrieves all tools in a toolset."""
        request = types.RetrieveToolsRequest(
            toolset=f"{self.app_name}/toolsets/{toolset_id}"
        )
        return self.tool_client.retrieve_tools(request=request)

    def retrieve_tool_schema(self, tool_name: str) -> Any:
        """Retrieves all tools in a toolset."""
        if "/toolsets/" not in tool_name:
            request = types.RetrieveToolSchemaRequest(
                parent=self.app_name, tool=tool_name
            )
            return self.tool_client.retrieve_tool_schema(request=request)
        toolset_name, tool_id = tool_name.split("/tools/")
        request = types.RetrieveToolSchemaRequest(
            parent=self.app_name,
            toolset_tool=types.ToolsetTool(
                toolset=toolset_name, tool_id=tool_id
            ),
        )
        return self.tool_client.retrieve_tool_schema(request=request)
