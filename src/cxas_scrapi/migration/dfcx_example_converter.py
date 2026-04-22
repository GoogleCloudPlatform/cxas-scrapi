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
from typing import Any, Dict

from cxas_scrapi.migration.dfcx_playbook_converter import DFCXPlaybookConverter

logger = logging.getLogger(__name__)


class DFCXExampleConverter:
    """Converts Dialogflow CX examples to CXAS examples."""

    @staticmethod
    def convert_cx_example_to_ps_example(
        cx_example: Dict[str, Any],
        ps_agent_id: str,
        ps_agent_display_name: str,
        tool_map: Dict[str, Any],  # Map of DFCX ID -> IRTool
        agent_id_map: Dict[str, str],
        cx_tool_display_name_to_id_map: Dict[str, str],
        cx_playbook_display_name_to_id_map: Dict[str, str],
        inline_action_map: Dict[str, str],
    ) -> Dict[str, Any]:
        """Converts a pre-processed DFCX Example to a CXAS Example payload."""
        messages = []

        if "actions" in cx_example:
            for action in cx_example["actions"]:
                if (
                    "userUtterance" in action
                    and "text" in action["userUtterance"]
                ):
                    messages.append(
                        {
                            "role": "user",
                            "chunks": [
                                {"text": action["userUtterance"]["text"]}
                            ],
                        }
                    )
                elif (
                    "agentUtterance" in action
                    and "text" in action["agentUtterance"]
                ):
                    messages.append(
                        {
                            "role": "agent",
                            "chunks": [
                                {"text": action["agentUtterance"]["text"]}
                            ],
                        }
                    )
                elif "toolUse" in action:
                    cx_tool_use = action["toolUse"]
                    original_tool_display_name = cx_tool_use.get("tool")
                    original_action_name = cx_tool_use.get("action")

                    tool_call_payload = {}
                    tool_response_payload = {}

                    if original_tool_display_name == "inline-action":
                        new_ps_tool_resource = inline_action_map.get(
                            original_action_name
                        )
                        if new_ps_tool_resource:
                            tool_call_payload["tool"] = new_ps_tool_resource
                            tool_response_payload["tool"] = new_ps_tool_resource
                        else:
                            logger.warning(
                                f"  Warning: Skipping inline-action call in "
                                f"example. Could not map action "
                                f"'{original_action_name}'."
                            )
                            continue

                    else:
                        original_tool_id = cx_tool_display_name_to_id_map.get(
                            original_tool_display_name
                        )

                        if not (
                            original_tool_id and original_tool_id in tool_map
                        ):
                            logger.warning(
                                f"  Warning: Skipping tool_call in example. "
                                f"Could not find original tool "
                                f"'{original_tool_display_name}' in map."
                            )
                            continue

                        tool = tool_map[original_tool_id]

                        if tool.type == "TOOLSET":
                            toolset_tool_obj = {
                                "toolset": tool.name,
                                "tool_id": original_action_name,
                            }
                            tool_call_payload["toolset_tool"] = toolset_tool_obj
                            tool_response_payload["toolset_tool"] = (
                                toolset_tool_obj
                            )
                        else:
                            tool_call_payload["tool"] = tool.name
                            tool_response_payload["tool"] = tool.name

                    if not tool_call_payload:
                        logger.warning(
                            f"  Warning: Skipping tool_call in example. "
                            f"Could not resolve ID for "
                            f"'{original_tool_display_name}'."
                        )
                        continue

                    tool_chunks = []
                    if "inputActionParameters" in cx_tool_use:
                        input_params = cx_tool_use.get(
                            "inputActionParameters", {}
                        )
                        final_input_params = input_params.get(
                            "requestBody", input_params
                        )

                        tc_chunk = {"tool_call": tool_call_payload.copy()}
                        tc_chunk["tool_call"]["args"] = final_input_params
                        tool_chunks.append(tc_chunk)

                    if "outputActionParameters" in cx_tool_use:
                        output_params = cx_tool_use.get(
                            "outputActionParameters", {}
                        )
                        response_data = output_params.get(
                            "result",
                            output_params.get(
                                "200", output_params.get("data", output_params)
                            ),
                        )

                        tr_chunk = {
                            "tool_response": tool_response_payload.copy()
                        }
                        tr_chunk["tool_response"]["response"] = {
                            "output": response_data
                        }
                        tool_chunks.append(tr_chunk)

                    if tool_chunks:
                        messages.append(
                            {"role": "agent", "chunks": tool_chunks}
                        )

                elif "playbookTransition" in action:
                    transition = action.get("playbookTransition", {})
                    if "playbook" in transition:
                        target_playbook_display_name = transition["playbook"]
                        target_playbook_id = (
                            cx_playbook_display_name_to_id_map.get(
                                target_playbook_display_name
                            )
                        )

                        if (
                            target_playbook_id
                            and target_playbook_id in agent_id_map
                        ):
                            target_agent_id = agent_id_map[target_playbook_id][
                                "name"
                            ]
                            messages.append(
                                {
                                    "role": "agent",
                                    "chunks": [
                                        {
                                            "agent_transfer": {
                                                "target_agent": target_agent_id
                                            }
                                        }
                                    ],
                                }
                            )
                        else:
                            logger.warning(
                                f"  Warning: Skipping agent_transfer. Target "
                                f"'{target_playbook_display_name}' not found."
                            )

        return {
            "display_name": DFCXPlaybookConverter.sanitize_display_name(
                f"[{ps_agent_display_name}] "
                f"{cx_example.get('displayName', 'Unnamed Example')}"
            ),
            "description": cx_example.get("description", ""),
            "entry_agent": ps_agent_id,
            "messages": messages,
        }
