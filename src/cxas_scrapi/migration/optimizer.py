import asyncio
import json
import logging
import re
from typing import Any

from cxas_scrapi.migration.data_models import (
    IRTool,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.instruction_lint import lint_instruction_text
from cxas_scrapi.migration.prompts import Prompts
from cxas_scrapi.utils.gemini import GeminiGenerate
from cxas_scrapi.utils.linter import LintResult

logger = logging.getLogger(__name__)


def _format_diagnostic(d: LintResult) -> str:
    return f"[{d.rule_id}] {d.message}"


def _build_validator_feedback(diagnostics: list[LintResult]) -> str:
    diag_block = "\n".join(f"- {_format_diagnostic(d)}" for d in diagnostics)
    return (
        "### YOUR PREVIOUS RESPONSE FAILED CANONICAL-XML VALIDATION\n\n"
        "Diagnostics:\n"
        f"{diag_block}\n\n"
        "CORRECT THESE ISSUES AND REGENERATE THE FULL INSTRUCTION SET. "
        "Do not abbreviate. Do not include the original response in your "
        "reply. Emit only the corrected XML."
    )


class CXASOptimizer:
    """
    The Hybrid Optimization Module for CXAS migrations.
    Executes a 5-stage pipeline to optimize variables, consolidate graphs,
    enforce CXAS best practices, and dynamically repair instructions.
    """

    def __init__(self, ir: MigrationIR, gemini_client: GeminiGenerate):
        self.ir = ir
        self.gemini = gemini_client
        self.dependency_map: dict[str, list[dict[str, str]]] = {}
        self.optimization_logs: list[dict[str, Any]] = []

    def log_action(self, stage: str, action: str, details: str):
        """Logs an optimization action for the post-migration report."""
        log_entry = {"stage": stage, "action": action, "details": details}
        self.optimization_logs.append(log_entry)
        logger.info(f"[{stage}] {action}: {details}")

    async def optimize_stage_1(self):
        """Executes Stage 1 Variable Optimization."""
        logger.info("Starting Stage 1 Variable Optimization...")
        await self._stage_1_variable_optimization()

    async def optimize_stage_2(self):
        """Executes Stage 2 Instructions and Tool Mocks Optimization
        in parallel.
        """
        logger.info(
            "Executing Stage 2 Parallelized Playbook Instruction & "
            "Tool Mock Optimization..."
        )
        # 1. Parallel first pass for both instructions and tool mocks
        await asyncio.gather(
            self._stage_2_instruction_optimization(),
            self._stage_2_tool_mock_optimization(),
        )

        # 2. Run the Unified Self-Healing Gate
        await self._self_heal_instructions()

    @staticmethod
    def _sanitize_variable_name(name: str) -> str:
        if name == "DELETE":
            return name
        # Replace any invalid characters (not alphanumeric, underscore, or
        # dash) with underscores
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        # Ensure it starts with a letter or underscore (dash/digits at start
        # are invalid)
        if clean and (clean[0].isdigit() or clean[0] == "-"):
            clean = "_" + clean
        return clean or "_var"

    async def _stage_1_variable_optimization(self):
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
            # Sanitize all target variable names to ensure they comply with
            # CXAS database standards
            variable_mapping = {
                old_k: CXASOptimizer._sanitize_variable_name(new_v)
                for old_k, new_v in variable_mapping.items()
            }
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
                param_def = self.ir.parameters[old_v].copy()
            else:
                param_def = {
                    "schema": {"type": "STRING"},
                }

            param_def["name"] = new_v

            # Resolve description (carry over existing, merge, or use fallback)
            existing_desc = new_parameters.get(new_v, {}).get("description", "")
            incoming_desc = param_def.get("description", "")
            param_def["description"] = (
                existing_desc
                or incoming_desc
                or f"Optimized parameter: {new_v}"
            )

            new_parameters[new_v] = param_def
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
            sample_agent = next(iter(self.ir.agents.values()))
            print(
                f"Sample Agent ({sample_agent.display_name}) Instruction:\n"
                f"{sample_agent.instruction[:500]}...\n"
            )
        if self.ir.tools:
            sample_tool = next(iter(self.ir.tools.values()))
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

    async def _stage_2_instruction_optimization(self):
        """
        Stage 2 Instructions: Playbook State Machine Optimizer.
        Restructures instructions into structured XML State Machines.
        """
        self.log_action(
            "Stage 2 Instructions",
            "Start",
            "Restructuring instructions to State Machine XML.",
        )

        all_playbook_flows = [
            agent
            for agent in self.ir.agents.values()
            if agent.type in ("PLAYBOOK", "FLOW")
        ]

        playbook_agents = [
            agent
            for agent in all_playbook_flows
            if "<Agent>" not in (agent.instruction or "")
        ]

        if not playbook_agents:
            details = (
                (
                    "All Playbook and Flow sub-agents are already optimized "
                    "XML State Machines. Skipping Stage 2 Instructions."
                )
                if all_playbook_flows
                else (
                    "No Playbook or Flow sub-agents found. "
                    "Skipping Stage 2 Instructions."
                )
            )
            self.log_action(
                "Stage 2 Instructions",
                "Complete",
                details,
            )
            return

        xprs_config = getattr(self.ir, "xprs_designer_data", None) or {}
        designed_transcript = xprs_config.get(
            "compiled_yaml", "No designed transcript provided."
        )

        async def optimize_single_agent(agent):
            logger.info(
                f"  Optimizing instructions for sub-agent: "
                f"'{agent.display_name}'..."
            )
            reverse_tool_map = {}
            for tool_id, tool in self.ir.tools.items():
                display_name = (
                    tool.payload.get("displayName")
                    or tool.payload.get("display_name")
                    or tool_id
                )
                if tool.name:
                    reverse_tool_map[tool.name] = display_name
                reverse_tool_map[tool_id] = display_name
            all_tools = []
            for t in agent.tools:
                if t in reverse_tool_map:
                    all_tools.append(reverse_tool_map[t])
                else:
                    all_tools.append(t.split("/")[-1])

            for gt in ["set_session_variables"]:
                if gt not in all_tools:
                    all_tools.append(gt)

            prompt = Prompts.STAGE_2_INSTRUCTION_OPTIMIZATION[
                "template"
            ].format(
                agent_name=agent.display_name,
                instruction=agent.instruction,
                tools=", ".join(all_tools),
                designed_transcript=designed_transcript,
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

                response_clean = re.sub(
                    r"^```(?:xml)?", "", response, flags=re.MULTILINE
                )
                response_clean = re.sub(
                    r"```$", "", response_clean, flags=re.MULTILINE
                )
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

    async def _stage_2_tool_mock_optimization(self):
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

    async def _self_heal_instructions(self):
        """
        Tier 2 Quality Gate: Self-heals agent instructions in-memory.
        Validates all optimized playbook/flow instructions and re-prompts
        Gemini once if they fail canonical-XML schema rules.
        """
        from cxas_scrapi.migration.data_models import IRAgent  # noqa: PLC0415

        failed_agents = []
        for agent_name, agent in self.ir.agents.items():
            if agent.type in ("PLAYBOOK", "FLOW"):
                diagnostics = lint_instruction_text(
                    agent.instruction, agent_name
                )
                if diagnostics:
                    failed_agents.append((agent_name, agent, diagnostics))

        if not failed_agents:
            self.log_action(
                "Stage 2 Instructions",
                "Self-Healing Gate",
                "All optimized playbooks passed canonical-schema validation. "
                "No healing required.",
            )
            return

        self.log_action(
            "Stage 2 Instructions",
            "Self-Healing Engaged",
            f"Found {len(failed_agents)} agent(s) failing schema. "
            "Starting parallel retries.",
        )

        async def _heal_one(
            agent_name: str, agent: IRAgent, diagnostics: list[LintResult]
        ):
            diag_block = "\n".join(
                f"  - {_format_diagnostic(d)}" for d in diagnostics
            )
            logger.warning(
                "Optimized XML for %s failed canonical-schema validation "
                "(%d issue(s)):\n%s\nRe-prompting Gemini once for repair...",
                agent_name,
                len(diagnostics),
                diag_block,
            )
            feedback = _build_validator_feedback(diagnostics)

            # Re-construct the Stage 2 prompt with the feedback
            reverse_tool_map = {}
            for tool_id, tool in self.ir.tools.items():
                display_name = (
                    tool.payload.get("displayName")
                    or tool.payload.get("display_name")
                    or tool_id
                )
                if tool.name:
                    reverse_tool_map[tool.name] = display_name
                reverse_tool_map[tool_id] = display_name
            all_tools = []
            for t in agent.tools:
                if t in reverse_tool_map:
                    all_tools.append(reverse_tool_map[t])
                else:
                    all_tools.append(t.split("/")[-1])
            for gt in ["set_session_variables"]:
                if gt not in all_tools:
                    all_tools.append(gt)

            prompt = Prompts.STAGE_2_INSTRUCTION_OPTIMIZATION[
                "template"
            ].format(
                agent_name=agent_name,
                instruction=agent.instruction,
                tools=", ".join(all_tools),
            )

            # Append feedback for propagation
            prompt = f"{prompt}\n\n{feedback}"
            system_prompt = Prompts.STAGE_2_INSTRUCTION_OPTIMIZATION["system"]

            try:
                response = await self.gemini.generate_async(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=1.0,
                )
                if not response:
                    raise ValueError(
                        "LLM returned empty instruction response during retry."
                    )

                response_clean = re.sub(
                    r"^```(?:xml)?", "", response, flags=re.MULTILINE
                )
                response_clean = re.sub(
                    r"```$", "", response_clean, flags=re.MULTILINE
                )
                response_clean = response_clean.strip()

                # Second-Pass Validation (Warning Only)
                final_diagnostics = lint_instruction_text(
                    response_clean, agent_name
                )
                if final_diagnostics:
                    diag_block = "\n".join(
                        f"  - {_format_diagnostic(d)}"
                        for d in final_diagnostics
                    )
                    logger.warning(
                        "Optimized XML for %s still fails schema validation "
                        "after retry (%d issue(s)). Proceeding anyway.\n%s",
                        agent_name,
                        len(final_diagnostics),
                        diag_block,
                    )

                # Save the corrected instruction
                agent.instruction = response_clean
                agent.status = MigrationStatus.COMPILED
                logger.info(
                    "Successfully healed agent instructions for '%s'.",
                    agent_name,
                )

            except Exception as e:
                logger.error(
                    "Failed to self-heal agent instructions for '%s': %s",
                    agent_name,
                    e,
                )

        # Run all self-healing tasks concurrently
        await asyncio.gather(
            *(
                _heal_one(name, agent, diags)
                for name, agent, diags in failed_agents
            )
        )
