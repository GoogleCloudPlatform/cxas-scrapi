# ruff: noqa: E501
# Utility to predict typical Customer User Journeys (CUJs) from agent tree views.
# Adapts the dialogue synthesis rules and transcript schemas defined in the
# cxas-cuj-report-generator skill (.agents/skills/cxas-cuj-report-generator)
# to predict representative multi-turn conversations for canvas pre-population.
import io
import json
import logging
from typing import Any

from rich.console import Console

from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.migration.main_visualizer import MainVisualizer
from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Principal Conversational Designer and User Experience Architect.
Your task is to analyze the structural Tree View of a Dialogflow CX virtual agent, identify typical user scenarios (Customer User Journeys, or CUJs), and generate a detailed conversation graph for each journey.

For each journey, you must follow these rules:
1. Map the turns in chronological order.
2. Dialogue turns do not strictly have to alternate; consecutive turns by the same speaker (e.g. multiple AGENT prompt bubbles, or consecutive USER answers) are fully supported.
3. Every agent turn must define its mode (VERBATIM or GENERATIVE) and include sample tool/webhook calls if relevant.
4. Every user turn must define its mode (VERBATIM or GENERATIVE) and include a semantic intent description or sample user utterance.

"""

TEMPLATE = """Analyze the structural Tree View for the agent '{agent_name}'.

### Standard Categorized Utterances (for reference & vocabulary):
{categorized_json}

### Tree View:
{tree_view}

### Instructions:
1. Predict the typical user scenarios (CUJs) users would execute for the components shown in this tree.
2. For each CUJ, compile a detailed conversation graph conforming to the requested schema.
3. You MUST output ONLY a valid JSON list of graph scenarios. Do not include markdown fences (```json) or conversational filler.

### Output Schema:
[
  {{
    "graph_id": "unique_graph_identifier",
    "category": "Core CUJ",
    "description": "Descriptive summary of the conversation scenario",
    "nodes": [
      {{
        "node_id": "node_001",
        "type": "AGENT",
        "speaker": "Agent_Name",
        "mode": "VERBATIM",
        "utterance": "Hello! I can assist you with your billing questions today. First, may I please have your account number?",
        "category": "Greeting",
        "transitions": [
          {{
            "next_node": "node_002"
          }}
        ]
      }},
      {{
        "node_id": "node_002",
        "type": "USER",
        "speaker": "End_User",
        "mode": "GENERATIVE",
        "intent_description": "User provides a valid numeric account identifier",
        "transitions": [
          {{
            "next_node": "node_003"
          }}
        ]
      }}
    ]
  }}
]
"""

CONSOLIDATE_SYSTEM_PROMPT = """You are a Principal Conversational Designer and User Experience Architect.
Your task is to review multiple batches of predicted user scenarios (Customer User Journeys, or CUJs) from a Dialogflow CX agent, deduplicate them, and consolidate them into a final list of the top 5 to 7 most representative core scenarios.

Ensure all outputs conform to the conversation graph schema.
"""

CONSOLIDATE_TEMPLATE = """We have processed the agent's playbooks and flows in separate batches. Here are the predicted user scenarios generated for each batch:

{batches_scenarios_json}

### Instructions:
1. Review all predicted scenarios.
2. Group, merge, and deduplicate them.
3. Select and output the top 5 to 7 most representative core scenarios that cover the widest range of agent functionality.
4. Output ONLY a valid JSON list of graph scenarios. Do not include markdown fences (```json) or conversational filler.
"""


class CUJGenerator:
    """Predicts typical customer user journeys (CUJs) using Gemini."""

    def __init__(self, gemini_client: GeminiGenerate):
        self.gemini = gemini_client

    def get_tree_text(self, tree_obj) -> str:
        """Helper to render Rich Tree structure into plain text."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        console.print(tree_obj)
        return buf.getvalue()

    async def predict_cujs(
        self,
        agent_name: str,
        source_agent_data: Any,
        categorized_utterances: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Calls Gemini to predict 5 to 7 typical user conversation paths.

        Handles large agent trees by batching them by component (playbook/flow).

        Args:
            agent_name: Name of the agent.
            source_agent_data: DFCX source agent metadata object.
            categorized_utterances: Dict of harvested categorized utterances.

        Returns:
            A list of predicted scenario graphs conforming to the conversation graph schema.
        """
        logger.info(
            "\nPredicting Customer User Journeys (CUJs): Parsing playbook and "
            "flow tree structures..."
        )
        # 1. Compile individual tree structures
        viz = MainVisualizer(source_agent_data)

        tools_tree = viz._build_tools_tree()
        tools_str = self.get_tree_text(tools_tree)

        playbook_strs = []
        for p in getattr(source_agent_data, "playbooks", []) or []:
            p_data = p.get("playbook", p) if isinstance(p, dict) else p
            p_tree = PlaybookTreeVisualizer(p_data).build_tree()
            playbook_strs.append(self.get_tree_text(p_tree))

        flow_strs = []
        resolver = FlowDependencyResolver(source_agent_data)
        for f in getattr(source_agent_data, "flows", []) or []:
            f_tree = FlowTreeVisualizer(resolver.resolve(f)).build_tree()
            flow_strs.append(self.get_tree_text(f_tree))

        # 2. Group components into batches below a 400k character threshold
        batches = []
        current_batch = [tools_str]
        current_size = len(tools_str)

        all_components = playbook_strs + flow_strs
        for comp in all_components:
            if current_size + len(comp) > 400000:
                batches.append("\n".join(current_batch))
                current_batch = [comp]
                current_size = len(comp)
            else:
                current_batch.append(comp)
                current_size += len(comp)
        if current_batch:
            batches.append("\n".join(current_batch))

        logger.info(
            f"Successfully compiled agent tree views. Split structure into "
            f"{len(batches)} batch(es) for processing."
        )

        # 3. Predict scenarios for each batch
        batch_scenarios = []
        for idx, batch_tree in enumerate(batches):
            logger.info(
                f"   [Batch {idx + 1}/{len(batches)}] Querying Gemini for predicted CUJ graphs "
                "(this may take a few moments)..."
            )

            prompt = TEMPLATE.format(
                agent_name=agent_name,
                categorized_json=json.dumps(categorized_utterances, indent=2),
                tree_view=batch_tree,
            )
            try:
                response_raw = await self.gemini.generate_async(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=1.0,
                    model_name="gemini-3.5-flash",
                )
                if response_raw:
                    json_str = (
                        response_raw.replace("```json", "")
                        .replace("```", "")
                        .strip()
                    )
                    json_start = json_str.find("[")
                    if json_start != -1:
                        json_str = json_str[json_start:]
                    scenarios = json.loads(json_str)
                    batch_scenarios.extend(scenarios)
            except Exception as e:
                logger.warning(
                    f"⚠️ Batch {idx + 1} scenario prediction failed: {e}"
                )

        # 4. Consolidate results if we have multiple batches
        if len(batches) <= 1:
            # If only 1 batch, it already fits. Trim to top 7 if needed.
            logger.info(f"✅ Predicted {len(batch_scenarios)} CUJ scenarios.")
            return batch_scenarios[:7]

        logger.info(
            f"Consolidating {len(batch_scenarios)} raw predicted scenarios "
            "across all batches to extract the top 5-7 core CUJs..."
        )

        consolidate_prompt = CONSOLIDATE_TEMPLATE.format(
            batches_scenarios_json=json.dumps(batch_scenarios, indent=2)
        )
        try:
            response_raw = await self.gemini.generate_async(
                prompt=consolidate_prompt,
                system_prompt=CONSOLIDATE_SYSTEM_PROMPT,
                temperature=1.0,
                model_name="gemini-3.5-flash",
            )
            if response_raw:
                json_str = (
                    response_raw.replace("```json", "")
                    .replace("```", "")
                    .strip()
                )
                json_start = json_str.find("[")
                if json_start != -1:
                    json_str = json_str[json_start:]
                consolidated = json.loads(json_str)
                logger.info(
                    f"✅ Successfully consolidated into {len(consolidated)} core CUJ scenarios."
                )
                return consolidated
        except Exception as e:
            logger.warning(
                f"⚠️ Scenario consolidation failed, returning fallback batch results: {e}"
            )

        return batch_scenarios[:7]
