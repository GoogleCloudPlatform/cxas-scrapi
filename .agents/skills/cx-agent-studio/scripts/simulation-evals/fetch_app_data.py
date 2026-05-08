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
import json
import zipfile
import shutil
from cxas_scrapi import Evaluations, Agents, Apps
from google.protobuf.json_format import MessageToDict

def main():
    parser = argparse.ArgumentParser(description="Fetch evaluations and agent tools from CES API.")
    parser.add_argument("--app-name", required=True, help="Full resource name of the app, e.g., projects/.../locations/.../apps/...")
    parser.add_argument("--output-dir", required=True, help="Base output directory")
    
    args = parser.parse_args()
    
    app_name = args.app_name
    output_dir = args.output_dir
    
    output_file_tools = os.path.join(output_dir, 'agent_tools.json')
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract project and location for Apps
    parts = app_name.split('/')
    if len(parts) < 6:
        print(f"Invalid app_name format: {app_name}")
        return
    project_id = parts[1]
    location = parts[3]
    
    # Initialize clients
    agents_client = Agents(app_name=app_name)
    apps_client = Apps(project_id=project_id, location=location)
    
    # 1. Fetch Agent Tools
    print(f"Fetching agent tools for app: {app_name}...")
    try:
        agents = agents_client.list_agents()
        processed_agents = []
        for agent in agents:
            try:
                agent_dict = MessageToDict(agent._pb)
            except AttributeError:
                agent_dict = MessageToDict(agent)
                
            processed_agents.append({
                'name': agent_dict.get('name'),
                'displayName': agent_dict.get('displayName'),
                'tools': agent_dict.get('tools'),
                'childAgents': agent_dict.get('childAgents'),
                'toolsets': agent_dict.get('toolsets')
            })
            
        with open(output_file_tools, 'w') as f:
            json.dump({'agents': processed_agents}, f, indent=2)
        print(f"Saved agent tools to {output_file_tools}")
    except Exception as e:
        print(f"Error fetching agent tools: {e}")
        
    # 2. Export App
    print(f"Exporting app {app_name}...")
    try:
        export_zip_path = os.path.join(output_dir, 'app.zip')
        apps_client.export_app(app_name=app_name, local_path=export_zip_path)
        print(f"Exported app to {export_zip_path}")
        
        extract_dir = os.path.join(output_dir, 'app')
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(export_zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        print(f"Extracted app to {extract_dir}")
        
        # Remove top-level folder if it exists
        top_level_folders = [d for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
        if len(top_level_folders) == 1:
            top_folder = top_level_folders[0]
            top_folder_path = os.path.join(extract_dir, top_folder)
            
            # Move all files and folders from top_folder to extract_dir
            for item in os.listdir(top_folder_path):
                s = os.path.join(top_folder_path, item)
                d = os.path.join(extract_dir, item)
                shutil.move(s, d)
            
            # Remove the now empty top folder
            os.rmdir(top_folder_path)
            print(f"Moved contents of {top_folder} to {extract_dir}")
            
        os.remove(export_zip_path)
    except Exception as e:
        print(f"Error exporting app: {e}")

if __name__ == "__main__":
    main()
