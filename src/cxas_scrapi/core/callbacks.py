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

"""Core Callbacks class for CXAS Scrapi."""

import inspect
import re
import textwrap
import traceback
from typing import Any, Callable, Dict, List, Optional, Union

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.agents import Agents


class Callbacks(Agents):
    """Core Class for managing Agent Callback Resources."""

    def __init__(self, app_name: str, **kwargs):
        """Initializes the Callbacks client.

        Args:
            app_name: The full resource name of the parent App
                (projects/PROJECT_ID/locations/LOCATION/apps/APP_ID).
        """
        super().__init__(app_name=app_name, **kwargs)
        self.resource_type = "callbacks"

        # Maps shorthand callback types to the exact field names in the
        # Agent proto
        self.callback_map = {
            "before_model": "before_model_callbacks",
            "after_model": "after_model_callbacks",
            "before_tool": "before_tool_callbacks",
            "after_tool": "after_tool_callbacks",
            "before_agent": "before_agent_callbacks",
            "after_agent": "after_agent_callbacks",
        }

    def _format_python_code(
        self, callback_type: str, code: Union[Callable, str]
    ) -> str:
        """Parses a Callable into the properly named string expected by CES."""
        if not isinstance(code, Callable):
            return code

        original_name = code.__name__
        # Convert "before_model" to "beforeModelCallback"
        parts = callback_type.split("_")
        new_name = (
            parts[0] + "".join(p.capitalize() for p in parts[1:]) + "Callback"
        )

        try:
            code_as_string = textwrap.dedent(inspect.getsource(code))
            # Replace the def signature
            code_as_string = code_as_string.replace(
                f"def {original_name}", f"def {new_name}", 1
            )
            return code_as_string
        except OSError as e:
            raise ValueError(
                "Could not extract Python source code from the provided "
                "Callable."
            ) from e

    def list_callbacks(self, agent_id: str) -> Dict[str, List[types.Callback]]:
        """Lists callbacks attached to a specific agent.

        Returns:
            A dictionary mapping the callback field name to a list of its
            callbacks.
        """
        agent = self.get_agent(agent_id)

        return {
            "before_model_callbacks": list(agent.before_model_callbacks),
            "after_model_callbacks": list(agent.after_model_callbacks),
            "before_tool_callbacks": list(agent.before_tool_callbacks),
            "after_tool_callbacks": list(agent.after_tool_callbacks),
            "before_agent_callbacks": list(agent.before_agent_callbacks),
            "after_agent_callbacks": list(agent.after_agent_callbacks),
        }

    def get_callback(
        self, agent_id: str, callback_type: str, index: int = 0
    ) -> Optional[types.Callback]:
        """Gets a specific callback from an agent by its index."""
        field_name = self.callback_map.get(callback_type)
        if not field_name:
            raise ValueError(f"Invalid callback type: {callback_type}")

        agent = self.get_agent(agent_id)
        callbacks = list(getattr(agent, field_name))

        if 0 <= index < len(callbacks):
            return callbacks[index]
        return None

    def create_callback(
        self,
        agent_id: str,
        callback_type: str,
        code: Union[Callable, str],
        description: str = "",
        disabled: bool = False,
    ) -> types.Agent:
        """Appends a new callback to the specific callback field on the
        Agent."""
        field_name = self.callback_map.get(callback_type)
        if not field_name:
            raise ValueError(f"Invalid callback type: {callback_type}")

        # Fetch the entire agent
        agent = self.get_agent(agent_id)

        # Parse the code into a string
        python_code = self._format_python_code(callback_type, code)

        # Create the new callback proto
        new_callback = types.Callback(
            python_code=python_code, description=description, disabled=disabled
        )

        # We must append the proto to the specific repeated field list
        # Since protobuf handles assignment, we can extend the existing list
        getattr(agent, field_name).append(new_callback)

        # Use field mask so we only update the specific callback field
        mask = types.field_mask_pb2.FieldMask(paths=[field_name])
        request = types.UpdateAgentRequest(agent=agent, update_mask=mask)
        return self.client.update_agent(request=request)

    def update_callback(
        self,
        agent_id: str,
        callback_type: str,
        index: int,
        code: Union[Callable, str] = None,
        description: str = None,
        disabled: bool = None,
    ) -> types.Agent:
        """Updates an existing callback on the agent by its index."""
        field_name = self.callback_map.get(callback_type)
        if not field_name:
            raise ValueError(f"Invalid callback type: {callback_type}")

        agent = self.get_agent(agent_id)
        callbacks = getattr(agent, field_name)

        if not (0 <= index < len(callbacks)):
            raise IndexError(
                f"Callback index {index} out of range for {field_name}"
            )

        if code is not None:
            callbacks[index].python_code = self._format_python_code(
                callback_type, code
            )
        if description is not None:
            callbacks[index].description = description
        if disabled is not None:
            callbacks[index].disabled = disabled

        mask = types.field_mask_pb2.FieldMask(paths=[field_name])
        request = types.UpdateAgentRequest(agent=agent, update_mask=mask)
        return self.client.update_agent(request=request)

    def delete_callback(
        self, agent_id: str, callback_type: str, index: int
    ) -> types.Agent:
        """Deletes a callback from the agent by its index."""
        field_name = self.callback_map.get(callback_type)
        if not field_name:
            raise ValueError(f"Invalid callback type: {callback_type}")

        agent = self.get_agent(agent_id)
        callbacks = getattr(agent, field_name)

        if not (0 <= index < len(callbacks)):
            raise IndexError(
                f"Callback index {index} out of range for {field_name}"
            )

        # Remove the specific element
        del callbacks[index]

        mask = types.field_mask_pb2.FieldMask(paths=[field_name])
        request = types.UpdateAgentRequest(agent=agent, update_mask=mask)
        return self.client.update_agent(request=request)

    @staticmethod
    def execute_callback(
        callback_func: Union[Callable, str], mock_session_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Executes a localized python callback function hermetically against
        a mock session.

        Args:
            callback_func: The logic to execute. Either a Callable or a string.
            mock_session_input: The dummy session state/input
                corresponding to what the CES engine usually provides.

        Returns:
            A dictionary containing either the returned dictionary update or
            error trace.
        """
        if isinstance(callback_func, Callable):
            try:
                code_str = textwrap.dedent(inspect.getsource(callback_func))
            except OSError as e:
                raise ValueError(
                    "Could not extract Python source code. Pass as a string "
                    "instead."
                ) from e
            func_name = callback_func.__name__
        else:
            code_str = callback_func
            # Basic parsing to find the "def FuncName(...):" signature
            match = re.search(r"def (\w+)\s*\(", code_str)
            if match:
                func_name = match.group(1)
            else:
                raise ValueError(
                    "Could not determine the function name from the provided "
                    "code string."
                )

        # Prepare a restricted execution environment
        exec_globals = {}

        try:
            # Execute the string into our globals namespace
            exec(code_str, exec_globals)
        except Exception as e:
            return {"error": f"Compilation failed: {str(e)}"}

        if func_name not in exec_globals:
            return {
                "error": f"Function {func_name} not found after executing "
                f"the code block."
            }

        # Call the function hermetically
        try:
            # The execution signature usually accepts a `session` argument
            # containing the state dict
            result = exec_globals[func_name](mock_session_input)
            return {"success": True, "result": result}
        except Exception as e:
            return {
                "error": f"Execution failed: {str(e)}",
                "traceback": traceback.format_exc(),
            }
