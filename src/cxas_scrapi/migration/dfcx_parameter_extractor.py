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
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class DFCXParameterExtractor:
    """Production-grade Global Parameter Extraction Engine."""

    @staticmethod
    def infer_schema_from_value(value: Any) -> Dict[str, str]:
        """Infers the CXAS schema type based on a raw Python value."""
        if isinstance(value, bool):
            return {"type": "BOOLEAN"}
        elif isinstance(value, (int, float)):
            return {"type": "NUMBER"}
        elif isinstance(value, dict):
            return {"type": "OBJECT"}
        elif isinstance(value, list):
            return {"type": "ARRAY"}
        else:
            return {"type": "STRING"}

    @staticmethod
    def register_param(
        original_ref: str,
        schema: Dict[str, Any],
        description: str,
        source: str,
        unified_parameters: Dict[str, Dict[str, Any]],
        parameter_name_map: Dict[str, str],
    ):
        """Sanitizes and registers a parameter, upgrading its type if a stronger

        hint is found.
        """
        if not original_ref:
            return

        clean_name = original_ref.rsplit(".", maxsplit=1)[-1]
        sanitized_name = re.sub(r"[^a-zA-Z0-9_]", "_", clean_name)

        parameter_name_map[original_ref] = sanitized_name
        parameter_name_map[f"session.params.{clean_name}"] = sanitized_name
        parameter_name_map[f"page.params.{clean_name}"] = sanitized_name
        parameter_name_map[f"${clean_name}"] = sanitized_name

        if sanitized_name not in unified_parameters:
            unified_parameters[sanitized_name] = {
                "name": sanitized_name,
                "description": description or f"Auto-extracted from {source}.",
                "schema": schema,
                "_confidence": 1 if schema.get("type") == "STRING" else 2,
            }
        else:
            current_conf = unified_parameters[sanitized_name].get(
                "_confidence", 1
            )
            new_conf = 1 if schema.get("type") == "STRING" else 2

            if new_conf > current_conf:
                unified_parameters[sanitized_name]["schema"] = schema
                unified_parameters[sanitized_name]["_confidence"] = new_conf
                if description:
                    unified_parameters[sanitized_name]["description"] = (
                        description
                    )

    @staticmethod
    def deep_scan_for_variables(
        obj: Any,
        var_pattern,
        unified_parameters,
        parameter_name_map,
    ):
        Ext = DFCXParameterExtractor
        if isinstance(obj, dict):
            if "setParameterActions" in obj:
                actions = obj["setParameterActions"]
                if isinstance(actions, list):
                    for action in actions:
                        if isinstance(action, dict):
                            param_name = action.get("parameter")
                            value = action.get("value")
                            if param_name:
                                schema = Ext.infer_schema_from_value(value)
                                Ext.register_param(
                                    param_name,
                                    schema,
                                    "",
                                    "Set Parameter Action",
                                    unified_parameters,
                                    parameter_name_map,
                                )

            if "parameterMapping" in obj:
                mapping_data = obj["parameterMapping"]
                targets = []

                if isinstance(mapping_data, dict):
                    targets = list(mapping_data.values())
                elif isinstance(mapping_data, list):
                    for item in mapping_data:
                        if isinstance(item, dict):
                            targets.extend(
                                [v for v in item.values() if isinstance(v, str)]
                            )
                        elif isinstance(item, str):
                            targets.append(item)

                for target_param in targets:
                    if isinstance(target_param, str) and (
                        "session.params." in target_param
                        or "page.params." in target_param
                        or target_param.startswith("$")
                    ):
                        clean_target = target_param.split(".")[-1].replace(
                            "$", ""
                        )
                        if clean_target:
                            DFCXParameterExtractor.register_param(
                                clean_target,
                                {"type": "STRING"},
                                "",
                                "Webhook Mapping",
                                unified_parameters,
                                parameter_name_map,
                            )

            for _, value in obj.items():
                DFCXParameterExtractor.deep_scan_for_variables(
                    value,
                    var_pattern,
                    unified_parameters,
                    parameter_name_map,
                )

        elif isinstance(obj, list):
            for item in obj:
                DFCXParameterExtractor.deep_scan_for_variables(
                    item,
                    var_pattern,
                    unified_parameters,
                    parameter_name_map,
                )

        elif isinstance(obj, str):
            matches = var_pattern.findall(obj)
            for var_name in matches:
                if var_name.lower() not in [
                    "sys",
                    "request",
                    "intent",
                    "webhook",
                ]:
                    DFCXParameterExtractor.register_param(
                        var_name,
                        {"type": "STRING"},
                        "",
                        "Inline Text Reference",
                        unified_parameters,
                        parameter_name_map,
                    )

    @staticmethod
    def migrate_parameters(
        source_agent_data: Dict[str, Any], reporter: Any
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """Aggregates all unique parameters across the agent data."""
        logger.info(
            "  -> Running deep traversal to extract and unify global "
            "parameters..."
        )

        unified_parameters: Dict[str, Dict[str, Any]] = {}
        parameter_name_map: Dict[str, str] = {}

        var_pattern = re.compile(
            r"\$(?:session\.params\.|page\.params\.|flow\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)"
        )

        # PASS 1: EXPLICIT DECLARATIONS
        for playbook in source_agent_data.get("playbooks", []):
            for param in playbook.get(
                "inputParameterDefinitions", []
            ) + playbook.get("outputParameterDefinitions", []):
                original_name = param.get("name")
                schema = param.get("typeSchema", {}).get("inlineSchema", {})

                if schema.get(
                    "type"
                ) == "ARRAY" and "inlineSchema" in schema.get("items", {}):
                    schema["items"] = schema["items"]["inlineSchema"]

                DFCXParameterExtractor.register_param(
                    original_name,
                    schema,
                    param.get("description", ""),
                    f"Playbook ({playbook.get('displayName')})",
                    unified_parameters,
                    parameter_name_map,
                )

        for flow_wrapper in source_agent_data.get("flows", []):
            for page_wrapper in flow_wrapper.get("pages", []):
                page = page_wrapper.get("value", page_wrapper)
                for param in page.get("form", {}).get(
                    "parameters", []
                ) + page.get("slots", []):
                    original_name = param.get("displayName") or param.get(
                        "name"
                    )
                    entity_type = param.get("entityType", "").split("/")[-1]
                    schema_type = "STRING"
                    if "number" in entity_type.lower():
                        schema_type = "NUMBER"
                    elif "boolean" in entity_type.lower():
                        schema_type = "BOOLEAN"

                    schema = {
                        "type": "ARRAY" if param.get("isList") else schema_type
                    }
                    if schema["type"] == "ARRAY":
                        schema["items"] = {"type": schema_type}

                    DFCXParameterExtractor.register_param(
                        original_name,
                        schema,
                        "",
                        f"Flow Form ({page.get('displayName')})",
                        unified_parameters,
                        parameter_name_map,
                    )

        # PASS 2: DEEP AST-STYLE TRAVERSAL
        DFCXParameterExtractor.deep_scan_for_variables(
            source_agent_data,
            var_pattern,
            unified_parameters,
            parameter_name_map,
        )

        # PASS 3: FINALIZE AND CLEANUP
        final_declarations = []
        for sanitized_name, data in unified_parameters.items():
            data.pop("_confidence", None)
            if not data.get("schema") or "type" not in data["schema"]:
                data["schema"] = {"type": "STRING"}
            final_declarations.append(data)
            reporter.log_variable(
                sanitized_name, sanitized_name, data["schema"]["type"]
            )

        logger.info(
            f"  -> Successfully unified {len(final_declarations)} unique "
            f"parameters into the global variable space."
        )
        return final_declarations, parameter_name_map
