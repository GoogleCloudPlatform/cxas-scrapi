import asyncio
import json
import logging
import re
from typing import Any, Dict, List

from cxas_scrapi.migration.data_models import (
    IRTool,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.prompts import Prompts
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


class CXASOptimizer:
    """
    The Hybrid Optimization Module for CXAS migrations.
    Executes a 5-stage pipeline to optimize variables, consolidate graphs,
    enforce CXAS best practices, and dynamically repair instructions.
    """

    def __init__(self, ir: MigrationIR, gemini_client: GeminiGenerate):
        self.ir = ir
        self.gemini = gemini_client
        self.dependency_map: Dict[str, List[Dict[str, str]]] = {}
        self.optimization_logs: List[Dict[str, Any]] = []

    def log_action(self, stage: str, action: str, details: str):
        """Logs an optimization action for the post-migration report."""
        log_entry = {"stage": stage, "action": action, "details": details}
        self.optimization_logs.append(log_entry)
        logger.info(f"[{stage}] {action}: {details}")

    async def optimize_stage1(self):
        """Executes Stage 1 Variable Optimization."""
        logger.info("Starting Stage 1 Variable Optimization...")
        await self._stage1_variable_optimization()

    async def optimize_stage2(self):
        """Executes Stage 2 Instructions and Tool Mocks Optimization
        in parallel.
        """
        logger.info(
            "Executing Stage 2 Parallelized Playbook Instruction & "
            "Tool Mock Optimization..."
        )
        await asyncio.gather(
            self._stage2_instruction_optimization(),
            self._stage2_tool_mock_optimization(),
        )

    async def _stage1_variable_optimization(self):
        """
        Stage 1: Granular Variable Deduplication
        Scans all instructions, tools, and callbacks to build a dependency map.
        Uses an LLM to deduplicate variables and keep the app under 95 limit.
        """
        self.log_action(
            "Stage 1", "Start", "Building global variable dependency map."
        )

        # 1. Deep Scan: Identify all variables
        all_vars = set(self.ir.parameters.keys())
        if "unregistered_parameters" in self.ir.optimization_logs:
            all_vars.update(
                self.ir.optimization_logs["unregistered_parameters"]
            )

        for var in all_vars:
            self.dependency_map[var] = []

        # Regex patterns for tracking variables
        # {variable_name}, `variable_name`, or $variable_name in prompts
        prompt_var_regex = re.compile(
            r"\{([a-zA-Z0-9_]+)\}|`([a-zA-Z0-9_]+)`|\$([a-zA-Z0-9_]+)"
        )
        # Python state accessors
        python_var_regex = re.compile(
            r"(?:get_variable\s*\(\s*|set_variable\s*\(\s*|"
            r"(?:state|variables|payload|kwargs)"
            r"(?:\.get\s*\(\s*|\.set\s*\(\s*|\s*\[\s*))"
            r'["\']([a-zA-Z0-9_]+)["\']'
        )

        # 1a. Scan Agents (Instructions & Callbacks)
        for agent in self.ir.agents.values():
            if agent.instruction:
                lines = agent.instruction.split("\n")
                for i, line in enumerate(lines):
                    for match_tuple in prompt_var_regex.findall(line):
                        v = next((g for g in match_tuple if g), None)
                        if not v:
                            continue
                        if v not in self.dependency_map:
                            self.dependency_map[v] = []
                        self.dependency_map[v].append(
                            {
                                "location_type": "Instruction",
                                "name": agent.display_name,
                                "line": i + 1,
                                "context": line.strip(),
                            }
                        )

            if agent.callbacks:
                for cb_name in [
                    "before_model_callback",
                    "after_model_callback",
                ]:
                    cb_code = agent.callbacks.get(cb_name, None)
                    if cb_code:
                        lines = cb_code.split("\n")
                        for i, line in enumerate(lines):
                            matches = python_var_regex.findall(line)
                            for v in matches:
                                if v not in self.dependency_map:
                                    self.dependency_map[v] = []
                                self.dependency_map[v].append(
                                    {
                                        "location_type": (
                                            f"Callback ({cb_name})"
                                        ),
                                        "name": agent.display_name,
                                        "line": i + 1,
                                        "context": line.strip(),
                                    }
                                )

        # 1b. Scan Tools
        for tool in self.ir.tools.values():
            tool_name = tool.payload.get("displayName", tool.id)
            if tool.type == "PYTHON" and "pythonFunction" in tool.payload:
                python_code = tool.payload["pythonFunction"].get(
                    "python_code", ""
                )
                if python_code:
                    lines = python_code.split("\n")
                    for i, line in enumerate(lines):
                        matches = python_var_regex.findall(line)
                        for v in matches:
                            if v not in self.dependency_map:
                                self.dependency_map[v] = []
                            self.dependency_map[v].append(
                                {
                                    "location_type": "Tool",
                                    "name": tool_name,
                                    "line": i + 1,
                                    "context": line.strip(),
                                }
                            )

        # 2. Generative Consolidation (LLM Pass)
        prompt = Prompts.STAGE_1_VARIABLE_OPTIMIZATION["template"].format(
            num_vars=len(self.dependency_map),
            dependency_map=json.dumps(self.dependency_map, indent=2),
        )
        system_prompt = Prompts.STAGE_1_VARIABLE_OPTIMIZATION["system"]

        self.log_action(
            "Stage 1",
            "LLM Processing",
            f"Requesting deduplication mapping for "
            f"{len(self.dependency_map)} variables.",
        )
        try:
            mapping_response = await self.gemini.generate_async(
                prompt=prompt,
                system_prompt=system_prompt,
                response_mime_type="application/json",
                temperature=1.0,
            )
            if not mapping_response:
                raise ValueError("LLM returned empty mapping response.")

            variable_mapping = json.loads(mapping_response)
        except Exception as e:
            self.log_action(
                "Stage 1",
                "Error",
                f"LLM generation failed: {e}. Aborting Stage 1.",
            )
            return

        unique_new_vars = set(
            v for v in variable_mapping.values() if v != "DELETE"
        )
        self.log_action(
            "Stage 1",
            "LLM Success",
            f"Reduced {len(self.dependency_map)} variables to "
            f"{len(unique_new_vars)}.",
        )

        # --- DEBUG LOGGING ---
        print("\n" + "=" * 50)
        print("STAGE 1 DEBUG: VARIABLE DEPENDENCY MAP (Input to LLM)")
        print(json.dumps(self.dependency_map, indent=2))
        print("STAGE 1 DEBUG: LLM OUTPUT MAPPING")
        print(json.dumps(variable_mapping, indent=2))
        print("=" * 50 + "\n")

        # 3. Apply the Mappings Globally
        self.log_action(
            "Stage 1",
            "Applying",
            "Rewriting instructions, tools, and callbacks globally.",
        )

        # 3a. Update Global Parameters IR
        new_parameters = {}
        for old_v, new_v in variable_mapping.items():
            if new_v == "DELETE":
                print(f"  [Optimizer] Pruning unused variable: {old_v}")
                continue
            if old_v != new_v:
                print(f"  [Optimizer] Merging {old_v} -> {new_v}")

            if old_v in self.ir.parameters:
                # Copy the old parameter definition and update its name
                param_def = self.ir.parameters[old_v].copy()
                param_def["name"] = new_v
                new_parameters[new_v] = param_def
            elif new_v not in new_parameters:
                new_parameters[new_v] = {
                    "name": new_v,
                    "schema": {"type": "STRING"},
                }
        self.ir.parameters = new_parameters

        # Helper to replace full word matches
        def replace_in_text(text: str, is_python: bool = False) -> str:
            if not text:
                return text
            for old_v, new_v in variable_mapping.items():
                if old_v == new_v:
                    continue
                if new_v == "DELETE":
                    continue
                # Escape `old_v` so variable names containing regex
                # metacharacters (e.g. an LLM-emitted '15' which Python's
                # `re` would interpret as a `{n}` quantifier) don't blow up
                # `re.sub` with "nothing to repeat".
                old_v_re = re.escape(old_v)

                if is_python:
                    pattern = (
                        rf"(get_variable\s*\(\s*|set_variable\s*\(\s*|"
                        rf"(?:state|variables|payload|kwargs)"
                        rf"(?:\.get\s*\(\s*|\.set\s*\(\s*|\s*\[\s*))"
                        rf"([\'\"]){old_v_re}([\'\"])"
                    )
                    text = re.sub(pattern, rf"\g<1>\g<2>{new_v}\g<3>", text)
                else:
                    # Escape the literal braces around the variable name —
                    # otherwise Python's `re` parses `{15}` as the {n}
                    # quantifier (which has nothing to repeat).
                    text = re.sub(
                        rf"\{{{old_v_re}\}}|`{old_v_re}`|\${old_v_re}\b",
                        f"{{{new_v}}}",
                        text,
                    )
            return text

        # 3b. Rewrite Agents
        for agent in self.ir.agents.values():
            modified = False

            new_instruction = replace_in_text(
                agent.instruction, is_python=False
            )
            if new_instruction != agent.instruction:
                agent.instruction = new_instruction
                modified = True

            if agent.callbacks:
                for cb_name in [
                    "before_model_callback",
                    "after_model_callback",
                ]:
                    old_code = agent.callbacks.get(cb_name, "")
                    if old_code:
                        new_code = replace_in_text(old_code, is_python=True)
                        if new_code != old_code:
                            agent.callbacks[cb_name] = new_code
                            modified = True

            if modified:
                agent.status = MigrationStatus.COMPILED

        # 3c. Rewrite Tools
        for tool in self.ir.tools.values():
            modified = False

            if tool.type == "PYTHON" and "pythonFunction" in tool.payload:
                old_code = tool.payload["pythonFunction"].get("python_code", "")
                new_code = replace_in_text(old_code, is_python=True)
                if new_code != old_code:
                    tool.payload["pythonFunction"]["python_code"] = new_code
                    modified = True

            old_desc = tool.payload.get("description", "")
            if old_desc:
                new_desc = replace_in_text(old_desc, is_python=False)
                if new_desc != old_desc:
                    tool.payload["description"] = new_desc
                    modified = True

            if modified:
                tool.status = MigrationStatus.COMPILED

        print("\n" + "=" * 50)
        print("STAGE 1 DEBUG: POST-REPLACEMENT VERIFICATION")
        if self.ir.agents:
            sample_agent = list(self.ir.agents.values())[0]
            print(
                f"Sample Agent ({sample_agent.display_name}) Instruction:\n"
                f"{sample_agent.instruction[:500]}...\n"
            )
        if self.ir.tools:
            sample_tool = list(self.ir.tools.values())[0]
            sample_tool_name = sample_tool.payload.get(
                "displayName", sample_tool.id
            )
            sample_tool_code = sample_tool.payload.get(
                "pythonFunction", {}
            ).get("python_code", "")
            print(
                f"Sample Tool ({sample_tool_name}) Python:\n"
                f"{sample_tool_code[:500]}...\n"
            )
        print("=" * 50 + "\n")

        self.log_action(
            "Stage 1",
            "Complete",
            "Global Variable Deduplication finished successfully.",
        )

    async def _stage2_instruction_optimization(self):
        """
        Stage 2 Instructions: Playbook State Machine Optimizer.
        Restructures instructions into structured XML State Machines.
        """
        self.log_action(
            "Stage 2 Instructions",
            "Start",
            "Restructuring instructions to State Machine XML.",
        )

        playbook_agents = [
            agent
            for agent in self.ir.agents.values()
            if agent.type == "PLAYBOOK"
        ]

        if not playbook_agents:
            self.log_action(
                "Stage 2 Instructions",
                "Complete",
                "No Playbook sub-agents found. Skipping Stage 2 Instructions.",
            )
            return

        async def optimize_single_agent(agent):
            logger.info(
                f"  Optimizing instructions for sub-agent: "
                f"'{agent.display_name}'..."
            )
            prompt = Prompts.STAGE_2_INSTRUCTION_OPTIMIZATION[
                "template"
            ].format(
                agent_name=agent.display_name,
                instruction=agent.instruction,
                tools=", ".join(agent.tools),
            )
            system_prompt = Prompts.STAGE_2_INSTRUCTION_OPTIMIZATION["system"]
            try:
                response = await self.gemini.generate_async(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=1.0,
                )
                if not response:
                    raise ValueError("LLM returned empty instruction response.")

                # Strip conversational fluff if LLM ignored constraints
                response_clean = response.strip()
                if response_clean.startswith("```xml"):
                    response_clean = response_clean[6:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()

                if "set_session_variables" in response_clean:
                    self._register_set_session_variables_tool()
                    set_vars_resource = (
                        f"{self.ir.metadata.app_resource_name}/tools/"
                        f"set_session_variables"
                    )
                    if set_vars_resource not in agent.tools:
                        agent.tools.append(set_vars_resource)
                        logger.info(
                            f"Attached 'set_session_variables' to agent "
                            f"'{agent.display_name}' tools list."
                        )

                agent.instruction = response_clean
                agent.status = MigrationStatus.COMPILED
                logger.info(
                    f"  Successfully restructured agent: "
                    f"'{agent.display_name}'."
                )
            except Exception as e:
                logger.error(
                    f"  Failed to optimize agent instructions for "
                    f"'{agent.display_name}': {e}"
                )

        # Run all playbook agent optimizations concurrently
        await asyncio.gather(
            *(optimize_single_agent(agent) for agent in playbook_agents)
        )
        self.log_action(
            "Stage 2 Instructions",
            "Complete",
            f"Restructured {len(playbook_agents)} Playbook agents "
            f"successfully.",
        )

    async def _stage2_tool_mock_optimization(self):
        """
        Stage 2 Tool Mocks: Tool Mock Optimizer.
        Concurrently injects highly realistic happy-path mock_mode return paths.
        """
        self.log_action(
            "Stage 2 Tool Mocks",
            "Start",
            "Injecting native mock_mode branches into Python tools.",
        )

        python_tools = [
            tool
            for tool in self.ir.tools.values()
            if tool.type == "PYTHON" and "pythonFunction" in tool.payload
        ]

        if not python_tools:
            self.log_action(
                "Stage 2 Tool Mocks",
                "Complete",
                "No Python tools found. Skipping Stage 2 Tool Mocks.",
            )
            return

        async def optimize_single_tool(tool):
            tool_name = tool.payload.get("displayName", tool.id)
            python_code = tool.payload["pythonFunction"].get("python_code", "")
            if not python_code:
                return

            # Find all agents referencing this tool in self.ir.agents
            referencing_agents = []
            for agent in self.ir.agents.values():
                is_referenced = False
                for t_ref in agent.tools:
                    if (
                        tool.id in t_ref
                        or tool.name in t_ref
                        or t_ref == tool.id
                    ):
                        is_referenced = True
                        break
                if is_referenced:
                    referencing_agents.append(agent)

            # Collect referencing agents' instructions and callbacks
            context_blocks = []
            for agent in referencing_agents:
                agent_ctx = f"### Agent: '{agent.display_name}'\n"
                if agent.instruction:
                    agent_ctx += (
                        f"**Instructions (XML Schema)**:\n{agent.instruction}\n"
                    )
                if agent.callbacks:
                    agent_ctx += "**Callbacks (Python Interceptors)**:\n"
                    for cb_name, cb_code in agent.callbacks.items():
                        if cb_code:
                            agent_ctx += f"- {cb_name}:\n{cb_code}\n"
                context_blocks.append(agent_ctx)

            agents_context = (
                "\n".join(context_blocks)
                if context_blocks
                else "No explicit agent instructions reference this tool."
            )

            logger.info(
                f"  Injecting mock branch into Python tool: '{tool_name}' "
                f"using calling agent context..."
            )
            prompt = Prompts.STAGE_2_TOOL_MOCK_OPTIMIZATION["template"].format(
                agents_context=agents_context, python_code=python_code
            )
            system_prompt = Prompts.STAGE_2_TOOL_MOCK_OPTIMIZATION["system"]
            try:
                response = await self.gemini.generate_async(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=1.0,
                )
                if not response:
                    raise ValueError("LLM returned empty python code response.")

                response_clean = response.strip()
                if response_clean.startswith("```python"):
                    response_clean = response_clean[9:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()

                tool.payload["pythonFunction"]["python_code"] = response_clean
                tool.status = MigrationStatus.COMPILED
                logger.info(
                    f"  Successfully injected mock mode into tool: "
                    f"'{tool_name}'."
                )
            except Exception as e:
                logger.error(
                    f"  Failed to inject mock mode into tool '{tool_name}': {e}"
                )

        # Run all python tool optimizations concurrently
        await asyncio.gather(
            *(optimize_single_tool(tool) for tool in python_tools)
        )
        self.log_action(
            "Stage 2 Tool Mocks",
            "Complete",
            f"Injected native mock_mode into {len(python_tools)} Python tools.",
        )

    def _register_set_session_variables_tool(self):
        """Helper to dynamically read and register set_session_variables."""
        if "set_session_variables" in self.ir.tools:
            return

        try:
            tool_code = (
                "def set_session_variables(variables: dict) -> dict:\n"
                '    """Set or update multiple session variables.\n'
                "    \n"
                "    Args:\n"
                "        variables: Key-value dictionary of variables.\n"
                '    """\n'
                "    for name, value in variables.items():\n"
                "        set_variable(name, value)\n"
                '    return {"status": "Variables successfully set/updated"}\n'
            )

            safe_tool_id = "set_session_variables"
            full_tool_name = (
                f"{self.ir.metadata.app_resource_name}/tools/{safe_tool_id}"
            )

            tool_payload = {
                "name": safe_tool_id,
                "displayName": "set_session_variables",
                "pythonFunction": {
                    "name": "set_session_variables",
                    "description": (
                        "Set or update multiple session variables."
                    ),
                    "python_code": tool_code,
                },
            }

            self.ir.tools[safe_tool_id] = IRTool(
                type="PYTHON",
                id=safe_tool_id,
                name=full_tool_name,
                payload=tool_payload,
                status=MigrationStatus.COMPILED,
            )
            logger.info("Registered 'set_session_variables' in migration IR.")
        except Exception as e:
            logger.error(
                f"Failed to register 'set_session_variables' tool: {e}"
            )
