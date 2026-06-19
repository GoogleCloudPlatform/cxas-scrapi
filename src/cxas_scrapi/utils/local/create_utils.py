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

"""
Utility class for setting up templates for local ces apps.
"""

import json
import keyword
import re
from pathlib import Path

import yaml
from google.cloud.ces_v1beta import types
from google.protobuf import json_format

AGENT_INSTRUCTION_TEMPLATE = """
<role>
  <!-- Defines the agent's core function or responsibility -->
  <!-- Note: Today's date is ${current_date}. -->
</role>

<persona>
  <primary_goal>
    <!-- Specifies the main objective the agent should achieve -->
  </primary_goal>
    <!-- Describes the agent's personality, tone, and behavioral guidelines -->
</persona>

<constraints>
  <!-- Defines the rules and restrictions the agent must follow. -->
  <text_formatting>
    <chunking>
      - Never write dense paragraphs; users scan, they do not read.
      - Limit text blocks to a maximum of 1-2 sentences.
      - Insert a line break between every distinct idea to maximize white space.
    </chunking>
    <bolding>
      - Bold the most important data points for instant visibility.
      - Always Bold: **Product Names**, **Prices**, **Dates**,
        **Order Numbers**, and **Deadlines**.
      - Example: "The **Classic Tee** is currently **${price}**."
    </bolding>
    <lists>
      - Automatically convert any mention of more than two items or steps
        into a list.
      - Use standard bullets (-) for options and numbered lists (1.) for
        instructions.
    </lists>
  </text_formatting>
</constraints>

<constraints>
  - Only answer questions about ...   - Never reveal ...
</constraints>

<taskflow>
<!-- Defines the conversational subtasks that you can take. Each subtask has a
     sequence of steps that should be taken in order. -->
    <subtask name="Initial Engagement">
    <!-- A specific part of the conversation flow, containing one or more
         steps. -->
        <step name="Greeting">
        <!-- An individual step that includes a trigger and an action. -->
            <trigger><!-- The condition or user input that initiates a step. -->
                User starts the interaction.
            </trigger>
            <action><!-- The action that the agent should take. -->
                Greet the user warmly.
            </action>
        </step>
</taskflow>
<examples>
<!--Definesfew-shot examples to guide agent behavior for specific scenarios.-->
</examples>
"""

PYTHON_CODE_TEMPLATE = """def {safe_name}() -> dict:
    \"\"\"Docstring explaining how to use {safe_name}.

    Returns:
        dict: The response or agent_action error.
    \"\"\"
    try:
        # TODO: Implement tool logic
        return {{}}
    except Exception as e:
        return {{"agent_action": f"Error in {safe_name}: {{e}}"}}
"""

LLM_POLICY_PROMPT_TEMPLATE = """\
### CRITICAL RULE
- <When you want the guardrail to trigger>
- <Additional guidance to err on the side of caution to reach a \
0% false negative goal>

### TRIGGER CRITERIA
FLAG the message if <your criteria>

**Definition of <your criteria>:** <give a definition of what your \
criteria means and what to look for>

**Explicit Claims to FLAG:**
* <list explicit examples of agent responses to flag for>

**Implicit Claims to FLAG (CRITICAL):**
You MUST flag:
* <list implicit examples of agent responses to flag for>

### DO NOT FLAG (False Positive Prevention)
Do NOT flag if <criteria to avoid false positives>. Ignore the following:

* <examples of what to avoid falsely flagging>\
"""

OPENAPI_SCHEMA_TEMPLATE = """
openapi: 3.0.0
info:
  title:
  description:
  version: 1.0.0
servers:
  - url:
    description:
paths:

"""


class CreateUtils:
    """Utility for creating local templates for CXAS components."""

    def create_agent(self, display_name: str, app_dir: str) -> str:
        """Creates a template for the specified type.

        Args:
            display_name: The display name of the component.
            app_dir: The directory of the app.

        Returns:
            The path to the created directory.
        """
        self._validate_app_dir(app_dir)
        app_path = Path(app_dir)
        safe_name = self._get_safe_display_name(display_name)

        agents_dir = app_path / "agents"
        target_dir = agents_dir / safe_name

        if target_dir.exists():
            raise FileExistsError(
                f"Agent '{display_name}' already exists at '{target_dir}'."
            )
        target_dir.mkdir(parents=True, exist_ok=True)

        json_file = target_dir / f"{safe_name}.json"
        agent_obj = types.Agent(
            display_name=display_name,
            instruction=f"agents/{safe_name}/instruction.txt",
        )
        template = json_format.MessageToDict(agent_obj._pb)

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2)

        instruction_file = target_dir / "instruction.txt"
        if not instruction_file.exists():
            with open(instruction_file, "w", encoding="utf-8") as f:
                f.write(AGENT_INSTRUCTION_TEMPLATE)

        return str(target_dir)

    def create_guardrail(
        self,
        display_name: str,
        app_dir: str,
        guardrail_type: str = "llm_policy",
    ) -> str:
        """Creates a guardrail template.

        Args:
            display_name: The display name of the guardrail.
            app_dir: The directory of the app.
            guardrail_type: The type of guardrail. Currently only
                'llm_policy' is supported.

        Returns:
            The path to the created directory.
        """
        self._validate_app_dir(app_dir)
        app_path = Path(app_dir)
        safe_name = self._get_safe_display_name(display_name)

        target_dir = app_path / "guardrails" / safe_name
        if target_dir.exists():
            raise FileExistsError(
                f"Guardrail '{display_name}' already exists at '{target_dir}'."
            )

        if guardrail_type.lower() != "llm_policy":
            raise ValueError(
                f"Unsupported guardrail type: {guardrail_type}. "
                "Currently only 'llm_policy' is supported."
            )

        guardrail_data = {
            "displayName": display_name,
            "enabled": True,
            "action": {"generativeAnswer": {}},
            "llmPolicy": {
                "maxConversationMessages": 1,
                "prompt": LLM_POLICY_PROMPT_TEMPLATE,
                "policyScope": "AGENT_RESPONSE",
            },
        }

        target_dir.mkdir(parents=True, exist_ok=True)
        json_file = target_dir / f"{safe_name}.json"
        with open(json_file, "w") as f:
            json.dump(guardrail_data, f, indent=2)

        # Add guardrail to app.json if it exists
        self._add_guardrail_to_app(app_path, display_name)

        return str(target_dir)

    def create_tool(
        self,
        display_name: str,
        app_dir: str,
        tool_type: str | None = None,
        add_to_agent: str | None = None,
    ) -> str:
        """Creates a tool of a given type.

        Args:
            display_name: The display name of the tool.
            app_dir: The directory of the app.
            tool_type: The type of tool. e.g python
            add_to_agent: The agent to add the tool to (optional).

        Returns:
            The path to the created directory.
        """
        self._validate_app_dir(app_dir)
        app_path = Path(app_dir)
        add_to_agent_obj = None
        if add_to_agent:
            add_to_agent_obj = self._get_agent(add_to_agent, app_path)

        safe_name = self._get_safe_display_name(display_name)

        if tool_type and tool_type.upper() == "PYTHON":
            target_dir = app_path / "tools" / safe_name
            code_dir = app_path / "tools" / safe_name / "python_function"
            code_dir.mkdir(parents=True, exist_ok=True)
            code_file = code_dir / "python_code.py"
            tool_obj = types.Tool(display_name=safe_name)
            tool_obj.python_function.name = safe_name
            tool_obj.python_function.description = (
                f"Description for {display_name}"
            )
            tool_obj.python_function.python_code = (
                f"tools/{safe_name}/python_function/python_code.py"
            )

            with open(code_file, "w", encoding="utf-8") as f:
                f.write(PYTHON_CODE_TEMPLATE.format(safe_name=safe_name))
        elif tool_type and tool_type.upper() == "OPENAPI":
            if add_to_agent:
                raise ValueError(
                    "Open API tool cannot be added to an agent without "
                    "processing Open API schema first."
                )
            target_dir = app_path / "toolsets" / safe_name
            schema_dir = app_path / "toolsets" / safe_name / "open_api_toolset"
            schema_dir.mkdir(parents=True, exist_ok=True)
            schema_file = schema_dir / "open_api_schema.yaml"
            tool_obj = types.Toolset(display_name=safe_name)
            tool_obj.description = f"Description for {display_name}"
            tool_obj.open_api_toolset.open_api_schema = (
                f"toolsets/{safe_name}/open_api_toolset/open_api_schema.yaml"
            )
            with open(schema_file, "w", encoding="utf-8") as f:
                f.write(OPENAPI_SCHEMA_TEMPLATE)

        elif tool_type and tool_type.upper() == "GOOGLE_SEARCH":
            target_dir = app_path / "tools" / safe_name
            tool_obj = types.Tool(display_name=safe_name)
            tool_obj.google_search_tool = types.GoogleSearchTool()
            tool_obj.google_search_tool.name = safe_name
            tool_obj.google_search_tool.description = (
                f"Description for {display_name}"
            )
        elif tool_type and tool_type.upper() == "DATASTORE":
            target_dir = app_path / "tools" / safe_name
            tool_obj = types.Tool(display_name=safe_name)
            tool_obj.data_store_tool = types.DataStoreTool()
            tool_obj.data_store_tool.name = safe_name
            tool_obj.data_store_tool.description = (
                f"Description for {display_name}"
            )
            tool_obj.data_store_tool.data_store_source = (
                types.DataStoreTool.DataStoreSource()
            )
        else:
            raise ValueError(f"Unsupported tool type: {tool_type}")

        target_dir.mkdir(parents=True, exist_ok=True)
        tool_json_file = target_dir / f"{safe_name}.json"
        tool_template = json_format.MessageToDict(tool_obj._pb)

        with open(tool_json_file, "w", encoding="utf-8") as f:
            json.dump(tool_template, f, indent=2)

        if add_to_agent_obj:
            agent_safe_name = self._get_safe_display_name(add_to_agent)
            agent_json_file = (
                app_path
                / "agents"
                / agent_safe_name
                / f"{agent_safe_name}.json"
            )
            if safe_name not in add_to_agent_obj.tools:
                add_to_agent_obj.tools.append(safe_name)
            with open(agent_json_file, "w", encoding="utf-8") as f:
                json.dump(
                    json_format.MessageToDict(add_to_agent_obj._pb), f, indent=2
                )

            # Append tool reference to prevent I012 linter warning
            agent_instruction_file = (
                app_path / "agents" / agent_safe_name / "instruction.txt"
            )
            if agent_instruction_file.exists():
                with open(agent_instruction_file, encoding="utf-8") as inst_f:
                    inst_content = inst_f.read()
                tool_ref = f"{{@TOOL: {safe_name}}}"
                if tool_ref not in inst_content:
                    with open(
                        agent_instruction_file, "a", encoding="utf-8"
                    ) as inst_f:
                        inst_f.write(f"\n\n<!-- Tool ref: {tool_ref} -->")

        return str(target_dir)

    def _get_agent(self, display_name: str, app_path: Path) -> types.Agent:
        """Gets the local agent config."""
        safe_display_name = self._get_safe_display_name(display_name)
        agents_dir = app_path / "agents"
        target_dir = agents_dir / safe_display_name
        if not target_dir.exists():
            raise FileNotFoundError(
                f"Agent '{display_name}' config not found in '{agents_dir}'."
            )
        json_file = target_dir / f"{safe_display_name}.json"
        if not json_file.exists():
            raise FileNotFoundError(
                f"Agent '{display_name}' config not found at '{json_file}'."
            )
        with open(json_file) as f:
            agent_data = json.load(f)

        agent = types.Agent()
        json_format.ParseDict(agent_data, agent._pb)
        return agent

    def _get_safe_display_name(self, display_name: str) -> str:
        """Gets the directory safe display name."""
        safe_name = (
            re.sub(r"[^a-zA-Z0-9]+", "_", display_name).strip("_").lower()
        )
        if not safe_name:
            raise ValueError(
                f"Invalid display name '{display_name}': must contain "
                "at least one alphanumeric character."
            )
        if safe_name[0].isdigit():
            safe_name = "_" + safe_name
        if keyword.iskeyword(safe_name):
            raise ValueError(
                f"Invalid display name '{display_name}': "
                f"'{safe_name}' is a Python reserved keyword."
            )
        return safe_name

    def _add_guardrail_to_app(self, app_path: Path, display_name: str) -> None:
        """Adds a guardrail display name to app.json or app.yaml.

        Only modifies the file if it exists.
        """
        for ext in ("json", "yaml"):
            app_file = app_path / f"app.{ext}"
            if app_file.exists():
                break
        else:
            return

        is_yaml = app_file.suffix in (".yaml", ".yml")

        with open(app_file) as f:
            if is_yaml:
                app_data = yaml.safe_load(f) or {}
            else:
                app_data = json.load(f)

        guardrails = app_data.get("guardrails", [])
        if display_name not in guardrails:
            guardrails.append(display_name)
            app_data["guardrails"] = guardrails
            with open(app_file, "w") as f:
                if is_yaml:
                    yaml.safe_dump(app_data, f, sort_keys=False)
                else:
                    json.dump(app_data, f, indent=2)

    def _validate_app_dir(self, app_dir: str) -> None:
        """Validates that agents/ exists in the app directory.

        Args:
            app_dir: The directory of the app.

        Raises:
            FileNotFoundError: If agents/ does not exist.
        """
        app_path = Path(app_dir)
        if not (app_path / "agents").exists():
            raise FileNotFoundError(
                f"App directory '{app_dir}' must contain 'agents' subdirectory."
            )
