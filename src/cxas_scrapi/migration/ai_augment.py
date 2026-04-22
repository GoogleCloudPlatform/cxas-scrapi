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

import json
import logging
from typing import Any, Dict, Optional

from cxas_scrapi.migration.prompts import Prompts
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


class AIAugment:
    """Handles AI-powered augmentation tasks for the migration service."""

    def __init__(self, gemini_client: GeminiGenerate):
        """Initializes the AIAugment service.

        Args:
            gemini_client: An instance of the GeminiGenerate class.
        """
        self.gemini_client = gemini_client
        logger.info("AIAugment service initialized.")

    async def generate_agent_description(
        self, playbook_data: Dict[str, Any]
    ) -> Optional[str]:
        """Generates a concise, one-sentence description for a Polysynth agent

        based on its source DFCX Playbook's goal and instructions.

        Args:
            playbook_data: The source Dialogflow CX Playbook data as a
                dictionary.

        Returns:
            A generated one-sentence description string, or None on failure.
        """
        display_name = playbook_data.get("displayName", "Unnamed Playbook")
        goal = playbook_data.get("goal", "No goal provided.")

        # Serialize it to a string to ensure all details are captured in the
        # prompt.
        instruction_str = json.dumps(
            playbook_data.get("instruction", {}), indent=2
        )

        system_prompt = Prompts.AGENT_DESCRIPTION["system"]

        prompt = Prompts.AGENT_DESCRIPTION["template"].format(
            display_name=display_name,
            goal=goal,
            instruction_str=instruction_str,
        )

        description = await self.gemini_client.generate_async(
            prompt=prompt, system_prompt=system_prompt
        )
        logger.info(f"***Generated agent description***: {description}")

        if description:
            # Clean up the response, removing potential quotes or extra
            # whitespace
            return description.strip().strip('"')

        return None

    async def generate_eval_set(
        self, agent_data: Dict[str, Any]
    ) -> Optional[list]:
        """Generates a structured evaluation set, instructing the LLM to

        dynamically size it based on agent complexity.

        Args:
            agent_data: The complete dictionary of the source DFCX agent.

        Returns:
            A list of dictionaries representing the eval set, or None on
            failure.
        """
        system_prompt = Prompts.EVAL_GENERATION["system"]

        prompt = Prompts.EVAL_GENERATION["template"].format(
            agent_data_json=json.dumps(agent_data, indent=2)
        )

        logger.info("Requesting dynamically sized eval set from the model...")
        response_str = await self.gemini_client.generate_async(
            prompt=prompt, system_prompt=system_prompt
        )
        logger.debug(f"***Generated the eval set***: {response_str}")

        if not response_str:
            logger.error("Eval set generation failed: No response from model.")
            return None

        try:
            # Find the start of the first JSON array '[' or object '{'
            json_start_index = -1
            first_bracket = response_str.find("[")
            first_brace = response_str.find("{")

            if first_bracket != -1 and (
                first_brace == -1 or first_bracket < first_brace
            ):
                json_start_index = first_bracket
            elif first_brace != -1:
                json_start_index = first_brace

            if json_start_index == -1:
                raise json.JSONDecodeError(
                    "No JSON object/array found in the response.",
                    response_str,
                    0,
                )

            # Extract from the start of the JSON to the end of the string
            json_str = response_str[json_start_index:]

            # Clean up any trailing markdown backticks
            json_str = json_str.strip().rstrip("`")

            eval_set = json.loads(json_str)
            if isinstance(eval_set, list):
                logger.info(
                    "-> Successfully extracted and parsed an eval set with "
                    f"{len(eval_set)} turns."
                )
                return eval_set
            else:
                logger.error(
                    "Eval set generation failed: Parsed JSON is not a list. "
                    f"Got: {type(eval_set)}"
                )
                return None

        except json.JSONDecodeError as e:
            logger.error(
                "Eval set generation failed: Could not decode JSON from "
                f"model response. Error: {e}"
            )
            logger.debug(f"Raw response: {response_str}")
            return None

    async def evaluate_conversations(
        self, eval_results: list, eval_set: list
    ) -> Optional[dict]:
        """Uses an LLM to evaluate conversation results against the original

        eval set.

        Args:
            eval_results: The list of conversation results from AgentComparer.
            eval_set: The original evaluation set with expected outcomes.

        Returns:
            A dictionary containing the LLM's evaluation summary, or None on
            failure.
        """
        system_prompt = Prompts.EVALUATION["system"]

        # Group golden set by conversation_id for easier lookup in the prompt
        golden_set_by_convo = {}
        for turn in eval_set:
            convo_id = turn["conversation_id"]
            if convo_id not in golden_set_by_convo:
                golden_set_by_convo[convo_id] = {
                    "scenario": turn["scenario"],
                    "turns": [],
                }
            golden_set_by_convo[convo_id]["turns"].append(turn)

        prompt_data = {
            "golden_set": list(golden_set_by_convo.values()),
            "conversation_results": eval_results,
        }

        prompt = Prompts.EVALUATION["template"].format(
            eval_data_json=json.dumps(prompt_data, indent=2)
        )

        logger.info(
            "\n🤖 Submitting evaluation results to Gemini for analysis..."
        )
        summary = await self.gemini_client.generate_async(
            prompt=prompt, system_prompt=system_prompt
        )
        return summary
