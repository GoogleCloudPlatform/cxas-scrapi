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

import os
import json
import argparse
import glob
import concurrent.futures
from google import genai
from google.genai import types

def load_tools(output_dir):
    tools_info = {}
    agent_tools_path = os.path.join(output_dir, 'agent_tools.json')
    tools_dir = os.path.join(output_dir, 'tools')
    
    if not os.path.exists(agent_tools_path) or not os.path.exists(tools_dir):
        return tools_info

    # 1. Get the set of tools used by agents
    used_tool_resources = set()
    try:
        with open(agent_tools_path, 'r') as f:
            agent_data = json.load(f)
            for agent in agent_data.get("agents", []):
                # Add direct tools
                if agent.get("tools"):
                    used_tool_resources.update(agent["tools"])
                # Add toolset tools
                if agent.get("toolsets"):
                    for ts in agent["toolsets"]:
                        toolset_res = ts["toolset"]
                        for tool_id in ts["toolIds"]:
                            # Reconstruct the full resource name for the toolset tool
                            used_tool_resources.add(f"{toolset_res}/tools/{tool_id}")
    except Exception as e:
        print(f"Error reading agent_tools.json: {e}")
        return tools_info

    # 2. Load schemas and filter by used tools
    for filename in os.listdir(tools_dir):
        if not filename.endswith(".json"):
            continue
            
        file_path = os.path.join(tools_dir, filename)
        try:
            with open(file_path, 'r') as f:
                tool_schema = json.load(f)
                tool_res_name = tool_schema.get("toolName") or tool_schema.get("schema", {}).get("tool")
                
                if tool_res_name in used_tool_resources:
                    display_name = tool_schema.get("displayName", filename.replace(".json", ""))
                    description = tool_schema.get("description", "No description")
                    
                    tools_info[display_name] = description
        except Exception as e:
            print(f"Error loading tool file {filename}: {e}")
            
    return tools_info


def process_file(filename, source_dir, target_dir, client, tools_context, args):
    src_file = os.path.join(source_dir, filename)
    print(f"Processing {src_file}...")
    
    try:
        with open(src_file, 'r') as f:
            eval_data = json.load(f)
    except Exception as e:
        print(f"Error reading {src_file}: {e}")
        return

    prompt = f"""
You are an expert AI developer task with converting CXAS golden evaluations into test cases for the `SimulationEvals` framework.

**Target Format:**
The target format is a JSON object with `steps` and `expectations`.
Example:
{{
  "steps": [
    {{
      "static_utterance": "Hello",
      "inject_variables": {{
        "key": "value"
      }}
    }},
    {{
      "goal": "Authentication",
      "success_criteria": "The agent asks for user details for verification.",
      "response_guide": "Provide the corresponding user details when asked.",
      "max_turns": 5
    }}
  ],
  "expectations": [
    "Welcome message played.",
    "Agent should call the tool `verify_employee_id`."
  ]
}}

**Instructions:**
1.  **Analyze the Conversation History**: Read the provided CXAS evaluation JSON, specifically the `golden.turns` array.
2.  **STT Error Correction**: Fix any obvious speech-to-text errors in the user utterances to make them natural.
3.  **Group Turns into Steps**: Prefer grouping turns into semantic `goal` steps (Dynamic Simulation) rather than generating many `static_utterance` steps.
    - **Dynamic Simulation (Recommended)**: Use for standard multi-turn flows where the exact words don't matter, but the outcome does.
        - **Configuration**: Leave out `static_utterance` and `inject_variables`. Set `max_turns` > 1 (e.g., 3-5) to allow for retries or follow-ups.
    - **Static/Forced Turn**: Use ONLY for the very first turn (initiation). For all subsequent turns, you MUST use dynamic simulation with `goal` and `response_guide`.
        - **Configuration**: Set `static_utterance` to the exact string.
    - **Silence Handling**: For turns where the user remains silent (no input), instead of generating multiple empty `static_utterance` steps, prefer using a single step with a `goal` describing the silence behavior and a `response_guide` instructing the user simulator to remain silent or not provide input (e.g., "Do not provide any input").
4.  **Enrich Expectations**: Add natural language descriptions of expected behavior to the **global `expectations` list at the root of the JSON**.
    - **DO NOT** create steps containing only `expectations`.
    - **DO NOT** put `expectations` inside individual steps. All expectations must go into the single array at the root level of the output JSON.
    - **Tool Calls**: If a tool call already exists in the golden evaluation for a turn, copy it into the simulation expectations (e.g., "Agent should call the tool `tool_name`").
    - **Inferred Tool Calls**: If no tool call is present but you can infer what tool should be called based on the conversation and the available tools list, add it to expectations ONLY if you have high confidence.
    - **No Arguments**: No need to expect tool arguments and responses in the expectations.
    - Do NOT use raw agent responses as expectations. Use natural language.
    - **Agent Transfers**: Do NOT include agent transfers in the expectations. These are considered implementation details and should not block evaluation.
5.  **Extract Variables**: Extract variables ONLY from `userInput.variables` in the very first turn of the golden evaluation. Do NOT extract variables from `expectation.updatedVariables` or any other field. If `userInput.variables` is not present or empty, do NOT add `inject_variables` to the output JSON. Do not look at other turns.

**Available Tools:**
{tools_context}

**Custom Instructions:**
{args.custom_instructions}

**Input Golden Evaluation JSON:**
{json.dumps(eval_data, indent=2)}

Output ONLY the converted JSON object. Do not include any markdown formatting or other text outside the JSON.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        # Validate JSON
        result_json = json.loads(response.text)
        
        target_file = os.path.join(target_dir, os.path.basename(filename))
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, 'w') as f:
            json.dump(result_json, f, indent=2)
        print(f"Saved to {target_file}")
        
    except Exception as e:
        print(f"Error converting {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Convert CXAS evaluations to SimulationEvals test cases.")
    parser.add_argument("--output-dir", required=True, help="Base output directory containing evals/ and tools/")
    parser.add_argument("--project", default="ces-deployment-dev")
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--custom-instructions", default="")
    parser.add_argument("--parallelism", type=int, default=5, help="Number of parallel workers")
    args = parser.parse_args()

    source_dir = os.path.join(args.output_dir, 'app', 'evaluations')
    tools_dir = os.path.join(args.output_dir, 'tools')
    target_dir = os.path.join(args.output_dir, 'sim_evals')

    client = genai.Client(vertexai=True, project=args.project, location=args.location)
    tools_info = load_tools(args.output_dir)
    tools_context = json.dumps(tools_info, indent=2)

    os.makedirs(target_dir, exist_ok=True)

    filenames = []
    if os.path.exists(source_dir):
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                if file.endswith(".json"):
                    filenames.append(os.path.relpath(os.path.join(root, file), source_dir))
    
    print(f"Found {len(filenames)} evaluations to convert.")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallelism) as executor:
        futures = [executor.submit(process_file, filename, source_dir, target_dir, client, tools_context, args) for filename in filenames]
        for future in futures:
            future.result()

if __name__ == "__main__":
    main()
