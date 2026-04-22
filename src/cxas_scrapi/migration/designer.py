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

"""Async Agent Designer for generating blueprints, instructions, and tools."""

import json
import logging
import textwrap
from typing import Any, Dict

from cxas_scrapi.migration.data_models import IRTool, MigrationIR
from cxas_scrapi.migration.prompts import Prompts

logger = logging.getLogger(__name__)


class AsyncAgentDesigner:
    """Handles Step 2: Architecture Planning, Tool Generation, and
    Instruction Formatting."""

    def __init__(self, gemini_client: Any):
        self.gemini = gemini_client

    @staticmethod
    def _validate_tree_view(tree_view: str):
        """Validates that tree_view is provided."""
        if not tree_view or not tree_view.strip():
            raise ValueError(
                "tree_view is required for generative synthesis. "
                "Please use the 'flow_visualizer.py' module to generate the "
                "tree view string for this flow before calling the designer."
            )

    @staticmethod
    def _get_available_toolsets_context(
        ir_tools_dict: Dict[str, IRTool],
    ) -> str:
        """Formats the loaded OpenAPI toolsets into a clean string for the LLM
        context."""
        toolset_summaries = []
        for t_id, t_data in ir_tools_dict.items():
            if t_data.type == "TOOLSET":
                ops = t_data.operation_ids
                name = t_data.payload.get("displayName", t_id)
                # Webhook meta might be in payload or extra field in IRTool if
                # we add it. For now, let's assume it might be in the payload
                # under 'webhook_meta' or similar.
                meta = t_data.payload.get("webhook_meta", {})

                summary = (
                    f"- Toolset: '{name}' | OpenAPI operation_id: "
                    f"'{ops[0] if ops else 'unknown'}'"
                )

                # Append DFCX specific metadata if it's a webhook
                if meta:
                    summary += f"\n  Webhook Type: {meta.get('webhook_type')}"
                    summary += f"\n  Original URI: {meta.get('original_uri')}"
                    if meta.get("request_body_template"):
                        indent_template = textwrap.indent(
                            meta.get("request_body_template"), "    "
                        )
                        summary += (
                            "\n  Request Payload Template (DFCX Format):\n"
                            f"{indent_template}"
                        )
                    if meta.get("parameter_mapping"):
                        indent_mapping = textwrap.indent(
                            json.dumps(meta.get("parameter_mapping"), indent=2),
                            "    ",
                        )
                        summary += (
                            "\n  Response Parameter Mapping (JSONPath -> "
                            f"Agent Variable):\n{indent_mapping}"
                        )

                toolset_summaries.append(summary)

        return (
            "\n\n".join(toolset_summaries)
            if toolset_summaries
            else "None available."
        )

    async def run_step_2a(
        self, flow_name: str, tree_view: str, target_ir: MigrationIR
    ) -> Dict[str, Any]:
        """Runs the Principal Architect prompt to generate the JSON
        Blueprint."""
        AsyncAgentDesigner._validate_tree_view(tree_view)
        logger.info(
            f"[{flow_name}] Starting 2A: Architecture Expert Blueprinting"
        )

        global_vars_context = json.dumps(
            {
                param_name: param_data.get("schema", {}).get("type", "UNKNOWN")
                for param_name, param_data in target_ir.parameters.items()
            },
            indent=2,
        )
        toolset_context = AsyncAgentDesigner._get_available_toolsets_context(
            target_ir.tools
        )

        prompt_2a = Prompts.STEP_2A_ARCHITECTURE_EXPERT["template"].format(
            flow_name=flow_name,
            resource_visualization=tree_view,
            global_variables=global_vars_context,
            available_backend_toolsets=toolset_context,
        )

        response_raw = await self.gemini.generate_async(
            prompt=prompt_2a,
            system_prompt=Prompts.STEP_2A_ARCHITECTURE_EXPERT["system"],
        )

        blueprint = {}
        if response_raw:
            try:
                json_str = (
                    response_raw.replace("```json", "")
                    .replace("```", "")
                    .strip()
                )
                json_start = json_str.find("{")
                if json_start != -1:
                    json_str = json_str[json_start:]
                blueprint = json.loads(json_str)
                logger.info(
                    f"[{flow_name}] ✅ 2A: Architecture Blueprint "
                    "Generated Successfully"
                )
            except Exception as e:
                logger.warning(
                    f"[{flow_name}] ⚠️ Error parsing 2A Blueprint JSON: {e}"
                )
                blueprint = {
                    "error": "JSON Parse Failure",
                    "raw_response": response_raw,
                }
        return blueprint

    async def run_step_2b_instructions(
        self, flow_name: str, blueprint: Dict[str, Any], tree_view: str
    ) -> str:
        """Runs the Instructions Expert prompt to generate the PIF XML."""
        AsyncAgentDesigner._validate_tree_view(tree_view)
        logger.info(
            f"[{flow_name}] Starting 2B: Instructions Expert (XML Generation)"
        )

        blueprint_json_str = json.dumps(blueprint, indent=2)

        prompt_2b = Prompts.STEP_2B_INSTRUCTIONS_EXPERT["template"].format(
            agent_name=flow_name,
            architecture_blueprint=blueprint_json_str,
            resource_visualization=tree_view,
        )

        response_raw = await self.gemini.generate_async(
            prompt=prompt_2b,
            system_prompt=Prompts.STEP_2B_INSTRUCTIONS_EXPERT["system"],
        )

        xml_instructions = ""
        if response_raw:
            xml_instructions = (
                response_raw.replace("```xml", "").replace("```", "").strip()
            )
            logger.info(
                f"[{flow_name}] ✅ 2B: XML Instructions Generated Successfully"
            )
        else:
            logger.error(
                f"[{flow_name}] ❌ 2B: LLM returned empty response "
                "for instructions."
            )

        return xml_instructions

    async def run_step_2c_tools_and_callbacks(
        self,
        flow_name: str,
        blueprint: Dict[str, Any],
        tree_view: str,
        target_ir: MigrationIR,
    ) -> Dict[str, Any]:
        """Runs the Tools & Callbacks Expert prompt to generate Python Code."""
        AsyncAgentDesigner._validate_tree_view(tree_view)
        logger.info(
            f"[{flow_name}] Starting 2C: Tools & Callbacks Expert "
            "(Python Generation)"
        )

        blueprint_json_str = json.dumps(blueprint, indent=2)
        global_vars_context = json.dumps(
            {
                param_name: param_data.get("schema", {}).get("type", "UNKNOWN")
                for param_name, param_data in target_ir.parameters.items()
            },
            indent=2,
        )
        toolset_context = AsyncAgentDesigner._get_available_toolsets_context(
            target_ir.tools
        )

        prompt_2c = Prompts.STEP_2C_TOOLS_AND_CALLBACKS_EXPERT[
            "template"
        ].format(
            agent_name=flow_name,
            architecture_blueprint=blueprint_json_str,
            resource_visualization=tree_view,
            global_variables=global_vars_context,
            available_backend_toolsets=toolset_context,
        )

        response_raw = await self.gemini.generate_async(
            prompt=prompt_2c,
            system_prompt=Prompts.STEP_2C_TOOLS_AND_CALLBACKS_EXPERT["system"],
        )

        tools_and_callbacks = {"tools": [], "callbacks": {}}
        if response_raw:
            try:
                json_str = (
                    response_raw.replace("```json", "")
                    .replace("```", "")
                    .strip()
                )
                json_start = json_str.find("{")
                if json_start != -1:
                    json_str = json_str[json_start:]
                tools_and_callbacks = json.loads(json_str)
                logger.info(
                    f"[{flow_name}] ✅ 2C: Python Tools & Callbacks "
                    "Generated Successfully"
                )
            except Exception as e:
                logger.warning(
                    f"[{flow_name}] ⚠️ Error parsing 2C Tools JSON: {e}"
                )
                tools_and_callbacks = {
                    "error": "JSON Parse Failure",
                    "raw_response": response_raw,
                }
        return tools_and_callbacks
