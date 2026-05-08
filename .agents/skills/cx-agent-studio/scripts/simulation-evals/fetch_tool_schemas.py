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

import argparse
import os
import sys
import json
import yaml
from google.protobuf.json_format import MessageToDict

from cxas_scrapi import Tools

def main():
    parser = argparse.ArgumentParser(description="Fetch tool schemas for a given app.")
    parser.add_argument("--app-name", required=True, help="The full resource name of the app (e.g., projects/.../locations/.../apps/...)")
    parser.add_argument("--output-dir", required=True, help="Directory to save the tool schema files")
    
    args = parser.parse_args()
    
    app_name = args.app_name
    output_dir = os.path.join(args.output_dir, 'tools')
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    tools_client = Tools(app_name)
    print(f"Fetching tools map for app: {app_name}")
    
    # Get mapping of tool_name -> tool_display_name
    tools_map = tools_client.get_tools_map(reverse=False)
    
    print(f"Found {len(tools_map)} tools/toolsets in map.")
    
    # ----------------------------------------------------
    # Additional logic to retrieve tool descriptions
    # ----------------------------------------------------
    def extract_description(pb_obj):
        try:
            obj_dict = MessageToDict(pb_obj._pb)
        except AttributeError:
            try:
                obj_dict = MessageToDict(pb_obj)
            except Exception:
                return ""
        
        # Top-level description directly on the object   
        if "description" in obj_dict:
            return obj_dict["description"]
            
        # Nested description (e.g., inside pythonFunction, openApiToolset, etc.)
        for key, value in obj_dict.items():
            if isinstance(value, dict) and "description" in value:
                return value["description"]

            if isinstance(value, dict) and "openApiSchema" in value:
                try:
                    schema_dict = yaml.safe_load(value["openApiSchema"])
                    paths = schema_dict.get("paths", {})
                    for path, path_item in paths.items():
                        if not isinstance(path_item, dict):
                            continue
                        for method, operation in path_item.items():
                            if isinstance(operation, dict) and "operationId" in operation and "description" in operation:
                                return operation["description"]
                except Exception:
                    pass

        return ""

    print("Fetching tool descriptions...")
    descriptions = {}
    all_tools_and_toolsets = tools_client.list_tools()
    for item in all_tools_and_toolsets:
        # Check if item is a toolset (has /toolsets/ in its name)
        if "/toolsets/" in item.name:
            # Toolsets themselves might have a description
            descriptions[item.name] = extract_description(item)
            toolset_id = item.name.split("/")[-1]
            ts_tools_resp = tools_client.retrieve_tools(toolset_id)
            for t in ts_tools_resp.tools:
                descriptions[t.name] = extract_description(t)

        else:
            descriptions[item.name] = extract_description(item)

    for tool_name, display_name in tools_map.items():
        try:
            print(f"Retrieving schema for {display_name} ({tool_name})...")
            schema_pb = tools_client.retrieve_tool_schema(tool_name)
            
            # Convert protobuf message to dict
            try:
                schema_dict = MessageToDict(schema_pb._pb)
            except AttributeError:
                schema_dict = MessageToDict(schema_pb)
                
            # Combine description and schema
            desc = descriptions.get(tool_name, "Description not found.")
            
            combined_payload = {
                "toolName": tool_name,
                "displayName": display_name,
                "description": desc,
                "schema": schema_dict
            }
                
            # Create a safe filename
            safe_filename = "".join(c for c in display_name if c.isalnum() or c in (" ", "_", "-")).rstrip()
            if not safe_filename:
                safe_filename = tool_name.split("/")[-1]
                
            file_path = os.path.join(output_dir, f"{safe_filename}.json")
            
            with open(file_path, "w") as f:
                json.dump(combined_payload, f, indent=2)
                
            print(f"  -> Saved to {file_path}")
        except Exception as e:
            print(f"[ERROR] Failed to retrieve or save schema for {display_name} ({tool_name}): {e}")

if __name__ == "__main__":
    main()
