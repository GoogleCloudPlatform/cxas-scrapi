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

# ruff: noqa: E501
"""Early harvester and semantic categorizer of legacy conversational utterances."""

import ast
import json
import logging
import re
from typing import Any

from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    DFCXPageModel,
)
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM_PROMPT = (
    "You are an Expert Conversational Analyst specializing in reverse-engineering conversational agents. "
    "Your task is to analyze, clean, deduplicate, and categorize a list of raw conversational utterances "
    "extracted from a legacy Dialogflow CX agent into standardized conversational designer buckets."
)

CLASSIFY_TEMPLATE = """\
Analyze the following raw conversation utterances extracted from the legacy agent.

### Standardization Buckets:
1. **greeting_onboarding**: Welcome messages, initial greetings, introduction prompts, start-up intents.
2. **authentication_authorization_security**: Prompts requesting PII, account verification questions, credentials collection, authorization notices, or access role limitations.
3. **disambiguation_data_collection**: Questions designed to extract parameters, slot-filling prompts, list options selection, or narrowing down vague/overlapping intents.
4. **information_delivery_task_execution**: Dynamic readout of account balance/data, business logic execution results, transaction completions, troubleshooting steps, or status reports.
5. **digital_deflection_omnichannel**: Prompts initiating deflection to SMS, secure email links, web portals, or app downloads.
6. **disclaimers_terms_legal**: Legal compliance statements, call recording disclosures, billing/privacy terms and conditions.
7. **hold_wait_transition**: Latency masking phrases, transition announcements, instructions telling user to hold/wait.
8. **validation_conversational_repair**: Page-level parameter validation error reprompts, correction nudges, and format checks.
9. **system_errors_tool_failures**: System crash messages, failed webhook degradation responses, database read errors.
10. **escalation_handoff**: Live agent transfer announcements, handoffs, queue overflow routing, or after-hours closed messages.
11. **persona_guardrails_abuse_prevention**: LLM jailbreak prevention statements, out-of-scope boundaries definitions, toxic language checks.
12. **signoff_wrapup**: Follow-up closure prompts (e.g. "Is there anything else?"), greetings wrap-up, and final call terminations.

### Input Raw Utterances:
{raw_utterances_json}

### Instructions:
1. **Clean**: Strip any raw markdown brackets, internal legacy variable formatting (e.g., convert $session.params.X to {{X}}), and extra whitespace.
2. **Deduplicate**: If multiple utterances are semantically identical or serve the exact same purpose, keep only a single clean, consolidated representative.
3. **Categorize**: Assign each cleaned utterance to exactly ONE of the 12 standardized buckets. If unsure, default to 'information_delivery_task_execution'.
4. **Filter Out System Instructions**: You must ONLY include phrases that represent user utterances (what the user says) or agent-delivered messages/templates (what the agent says to the user). Completely EXCLUDE any agent-facing system instructions, developer goals, or playbook logic flow statements. Only extract either the actual dialogue/verbatim phrases from within those instructions if any exist.


You MUST output ONLY a valid JSON object. Do not include markdown fences (```json) or conversational filler.

### Output Schema:
{{
  "categorized_utterances": {{
    "greeting_onboarding": ["string", ...],
    "authentication_authorization_security": ["string", ...],
    "disambiguation_data_collection": ["string", ...],
    "information_delivery_task_execution": ["string", ...],
    "digital_deflection_omnichannel": ["string", ...],
    "disclaimers_terms_legal": ["string", ...],
    "hold_wait_transition": ["string", ...],
    "validation_conversational_repair": ["string", ...],
    "system_errors_tool_failures": ["string", ...],
    "escalation_handoff": ["string", ...],
    "persona_guardrails_abuse_prevention": ["string", ...],
    "signoff_wrapup": ["string", ...]
  }},
  "rationales": {{
    "utterance_text": "Brief 1-sentence explanation for classification"
  }}
}}
"""


class UtteranceCollector:
    """Early harvester and categorizer engine of legacy DFCX utterances."""

    def __init__(self, gemini_client: GeminiGenerate):
        self.gemini = gemini_client
        self.collected_utterances: set[str] = set()
        self.raw_metadata: list[dict[str, Any]] = []

    def _add_utterance(
        self, text: str, source_type: str, source_name: str
    ) -> None:
        """Adds a harvested utterance to the collection with metadata, protecting against empty values."""
        if not text or not text.strip():
            return
        cleaned = text.strip()
        self.collected_utterances.add(cleaned)
        self.raw_metadata.append(
            {
                "text": cleaned,
                "source_type": source_type,
                "source_name": source_name,
            }
        )

    def _harvest_fulfillment(
        self, fulfillment: dict[str, Any], source_type: str, source_name: str
    ) -> None:
        """Extracts text messages from DFCX fulfillments."""
        if not fulfillment:
            return

        # Support nested transition/handler wraps
        if "beforeTransition" in fulfillment:
            fulfillment = fulfillment["beforeTransition"]

        if "messages" in fulfillment:
            for message in fulfillment.get("messages", []):
                if "text" in message and "text" in message["text"]:
                    for text_val in message["text"]["text"]:
                        self._add_utterance(text_val, source_type, source_name)
        elif "staticUserResponse" in fulfillment:
            for candidate in fulfillment.get("staticUserResponse", {}).get(
                "candidates", []
            ):
                for response in candidate.get("responses", []):
                    if "text" in response and "variants" in response["text"]:
                        for variant in response["text"]["variants"]:
                            val = variant.get("text", "")
                            self._add_utterance(val, source_type, source_name)

    def _harvest_routes(
        self, routes: list[dict[str, Any]], source_type: str, source_name: str
    ) -> None:
        """Extracts fulfillments from transition routes."""
        for route in routes:
            fulfillment = route.get("triggerFulfillment") or route.get(
                "transitionEventHandler"
            )
            if fulfillment:
                self._harvest_fulfillment(fulfillment, source_type, source_name)

    def _harvest_events(
        self, handlers: list[dict[str, Any]], source_type: str, source_name: str
    ) -> None:
        """Extracts fulfillments from event handlers."""
        for handler in handlers:
            fulfillment = handler.get("triggerFulfillment") or handler.get(
                "handler"
            )
            if fulfillment:
                self._harvest_fulfillment(fulfillment, source_type, source_name)

    def _harvest_pages(
        self, pages: list[DFCXPageModel], flow_name: str
    ) -> None:
        """Harvests utterances across all pages of a flow."""
        for page_model in pages:
            page = page_model.page_data
            page_name = (
                page.get("displayName") or page_model.page_id.split("/")[-1]
            )
            full_source = f"{flow_name}/{page_name}"

            # 1. Entry Fulfillments
            entry_fulfillment = page.get("entryFulfillment") or page.get(
                "onLoad"
            )
            if entry_fulfillment:
                self._harvest_fulfillment(
                    entry_fulfillment, "PAGE_ENTRY", full_source
                )

            # 2. Form/Slots prompting
            form_params = page.get("form", {}).get("parameters", []) + page.get(
                "slots", []
            )
            for param in form_params:
                param_name = param.get("displayName", "UnnamedParam")
                param_source = f"{full_source}/param:{param_name}"
                fill_behavior = param.get("fillBehavior", {})
                if fill_behavior:
                    initial_prompt = fill_behavior.get(
                        "initialPromptFulfillment"
                    ) or fill_behavior.get("initialPrompt")
                    if initial_prompt:
                        self._harvest_fulfillment(
                            initial_prompt, "PARAM_PROMPT", param_source
                        )
                    reprompt_handlers = fill_behavior.get(
                        "repromptEventHandlers", []
                    )
                    if reprompt_handlers:
                        self._harvest_events(
                            reprompt_handlers, "PARAM_REPROMPT", param_source
                        )

            # 3. Transition Routes & Event Handlers
            self._harvest_routes(
                page.get("transitionRoutes", []), "PAGE_ROUTE", full_source
            )
            self._harvest_events(
                page.get("eventHandlers", []), "PAGE_EVENT", full_source
            )

    def _harvest_playbook_steps(
        self, steps: list[dict[str, Any]], playbook_name: str
    ) -> None:
        """Recursively harvest text lines from playbook steps."""
        for step in steps:
            if "text" in step:
                self._add_utterance(
                    step["text"], "PLAYBOOK_STEP", playbook_name
                )
            if step.get("steps"):
                self._harvest_playbook_steps(step["steps"], playbook_name)

    def _harvest_playbooks(self, playbooks: list[dict[str, Any]]) -> None:
        """Harvests goal, instruction, and example texts from playbooks."""
        for pb in playbooks:
            pb_data = pb.get("playbook", pb) if isinstance(pb, dict) else pb
            pb_name = pb_data.get("displayName", "UnnamedPlaybook")

            # Harvest system goals and instructions
            if pb_data.get("goal"):
                self._add_utterance(pb_data["goal"], "PLAYBOOK_GOAL", pb_name)

            if "instruction" in pb_data and "steps" in pb_data["instruction"]:
                self._harvest_playbook_steps(
                    pb_data["instruction"]["steps"], pb_name
                )

            # Harvest conversational examples
            for example in pb_data.get("examples", []):
                ex_name = (
                    example.get("displayName")
                    or example.get("name", "").split("/")[-1]
                    or "Example"
                )
                for action in example.get("actions", []):
                    if (
                        "userUtterance" in action
                        and "text" in action["userUtterance"]
                    ):
                        user_text = action["userUtterance"]["text"]
                        self._add_utterance(
                            user_text,
                            "PLAYBOOK_USER_EXAMPLE",
                            f"{pb_name} : {ex_name}",
                        )
                    if (
                        "agentUtterance" in action
                        and "text" in action["agentUtterance"]
                    ):
                        agent_text = action["agentUtterance"]["text"]
                        self._add_utterance(
                            agent_text,
                            "PLAYBOOK_AGENT_EXAMPLE",
                            f"{pb_name} : {ex_name}",
                        )

    def _harvest_code_blocks(self, code_blocks: list[dict[str, Any]]) -> None:
        """Surgically parses Python code blocks via AST to harvest print and logger literals."""
        for block in code_blocks:
            code = block.get("code", "")
            block_name = block.get("name", "UnnamedCodeBlock")
            if not code:
                continue

            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    # 1. Look for print calls or logger calls
                    if isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Name
                    ):
                        if node.func.id == "print":
                            for arg in node.args:
                                if isinstance(arg, ast.Constant) and isinstance(
                                    arg.value, str
                                ):
                                    self._add_utterance(
                                        arg.value, "CODE_PRINT", block_name
                                    )
                                elif isinstance(
                                    arg, ast.Str
                                ):  # Python < 3.8 fallback
                                    self._add_utterance(
                                        arg.s, "CODE_PRINT", block_name
                                    )
                    elif isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Attribute
                    ):
                        # E.g. logger.info("...") or logger.warning("...")
                        if (
                            isinstance(node.func.value, ast.Name)
                            and "log" in node.func.value.id
                        ):
                            for arg in node.args:
                                if isinstance(arg, ast.Constant) and isinstance(
                                    arg.value, str
                                ):
                                    self._add_utterance(
                                        arg.value, "CODE_LOG", block_name
                                    )
                                elif isinstance(arg, ast.Str):
                                    self._add_utterance(
                                        arg.s, "CODE_LOG", block_name
                                    )
            except Exception as e:
                # Resilient ast parsing fallback
                logger.warning(
                    f"AST traversal failed for CodeBlock {block_name}: {e}"
                )
                # Simple regex fallback to harvest raw double-quoted strings
                strings = re.findall(r'["\']([^"\']{10,120})["\']', code)
                for s in strings:
                    self._add_utterance(s, "CODE_REGEX_FALLBACK", block_name)

    def harvest_all(self, source_agent: DFCXAgentIR) -> list[str]:
        """Harvests all raw utterances across flows, playbooks, and code blocks.

        Args:
            source_agent: The raw DFCXAgentIR model.

        Returns:
            A list of unique harvested raw utterances.
        """
        logger.info(
            f"Starting early utterance harvesting for agent: {source_agent.display_name}"
        )

        # 1. Scrape Flows and Pages
        for flow_model in source_agent.flows:
            flow_name = (
                flow_model.flow_data.get("displayName")
                or flow_model.flow_id.split("/")[-1]
            )

            # Flow level routes & events
            self._harvest_routes(
                flow_model.flow_data.get("transitionRoutes", []),
                "FLOW_ROUTE",
                flow_name,
            )
            self._harvest_events(
                flow_model.flow_data.get("eventHandlers", []),
                "FLOW_EVENT",
                flow_name,
            )

            # Page level entry prompts, parameterized forms, and transitions
            self._harvest_pages(flow_model.pages, flow_name)

        # 2. Scrape Playbooks
        self._harvest_playbooks(source_agent.playbooks)

        # 3. Scrape Code Blocks
        self._harvest_code_blocks(source_agent.code_blocks)

        unique_raw = list(self.collected_utterances)
        logger.info(
            f"Early utterance harvesting complete. Extracted {len(unique_raw)} unique raw utterances."
        )
        return unique_raw

    async def classify_and_deduplicate(
        self, raw_utterances: list[str]
    ) -> dict[str, Any]:
        """Sends harvested raw utterances to Gemini for semantic deduplication and classification.

        Args:
            raw_utterances: A list of extracted raw utterances.

        Returns:
            A dictionary containing categorized utterances and selection rationales.
        """
        if not raw_utterances:
            logger.info(
                "No raw utterances harvested. Returning empty classification."
            )
            return {
                "categorized_utterances": {
                    "greeting_onboarding": [],
                    "authentication_authorization_security": [],
                    "disambiguation_data_collection": [],
                    "information_delivery_task_execution": [],
                    "digital_deflection_omnichannel": [],
                    "disclaimers_terms_legal": [],
                    "hold_wait_transition": [],
                    "validation_conversational_repair": [],
                    "system_errors_tool_failures": [],
                    "escalation_handoff": [],
                    "persona_guardrails_abuse_prevention": [],
                    "signoff_wrapup": [],
                },
                "rationales": {},
            }

        logger.info(
            f"Sending {len(raw_utterances)} utterances to Gemini for semantic bucketing..."
        )

        prompt = CLASSIFY_TEMPLATE.format(
            raw_utterances_json=json.dumps(raw_utterances, indent=2)
        )

        response_raw = await self.gemini.generate_async(
            prompt=prompt,
            system_prompt=CLASSIFY_SYSTEM_PROMPT,
            model_name="gemini-3.5-flash",
        )

        classified_data = {}
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
                classified_data = json.loads(json_str)
                logger.info(
                    "✅ Gemini classification and deduplication complete."
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Error parsing Gemini classification JSON: {e}. Fallbacking to rule-based classification."
                )
                classified_data = self._rule_based_fallback(raw_utterances)

        if (
            not classified_data
            or "categorized_utterances" not in classified_data
        ):
            classified_data = self._rule_based_fallback(raw_utterances)

        return classified_data

    def _rule_based_fallback(self, raw_utterances: list[str]) -> dict[str, Any]:
        """Rule-based classification fallback if Gemini analysis fails."""
        logger.info("Executing rule-based classification fallback...")
        buckets: dict[str, list[str]] = {
            "greeting_onboarding": [],
            "authentication_authorization_security": [],
            "disambiguation_data_collection": [],
            "information_delivery_task_execution": [],
            "digital_deflection_omnichannel": [],
            "disclaimers_terms_legal": [],
            "hold_wait_transition": [],
            "validation_conversational_repair": [],
            "system_errors_tool_failures": [],
            "escalation_handoff": [],
            "persona_guardrails_abuse_prevention": [],
            "signoff_wrapup": [],
        }
        rationales: dict[str, str] = {}

        for ut in raw_utterances:
            lowered = ut.lower()
            category = "information_delivery_task_execution"

            if any(
                w in lowered
                for w in ["welcome", "hello", "hi there", "greetings"]
            ):
                category = "greeting_onboarding"
            elif any(
                w in lowered
                for w in [
                    "password",
                    "username",
                    "ssn",
                    "verify",
                    "authentication",
                    "security",
                    "pin",
                ]
            ):
                category = "authentication_authorization_security"
            elif any(
                w in lowered
                for w in [
                    "sms",
                    "text link",
                    "sent you a",
                    "deflect",
                    "app download",
                ]
            ):
                category = "digital_deflection_omnichannel"
            elif any(
                w in lowered
                for w in [
                    "recorded",
                    "monitored",
                    "terms",
                    "disclosure",
                    "legal",
                ]
            ):
                category = "disclaimers_terms_legal"
            elif any(
                w in lowered
                for w in ["one moment", "wait", "hold", "transitioning"]
            ):
                category = "hold_wait_transition"
            elif any(
                w in lowered
                for w in [
                    "invalid",
                    "re-enter",
                    "try again",
                    "say it again",
                    "no speech",
                    "silence",
                ]
            ):
                category = "validation_conversational_repair"
            elif any(
                w in lowered
                for w in ["trouble", "fail", "webhook", "error", "down"]
            ):
                category = "system_errors_tool_failures"
            elif any(
                w in lowered
                for w in [
                    "transfer",
                    "escalate",
                    "human",
                    "representative",
                    "agent",
                ]
            ):
                category = "escalation_handoff"
            elif any(
                w in lowered
                for w in ["forbidden", "strictly", "jailbreak", "cannot help"]
            ):
                category = "persona_guardrails_abuse_prevention"
            elif any(
                w in lowered
                for w in [
                    "bye",
                    "goodbye",
                    "thank you",
                    "wrap up",
                    "closing",
                    "anything else",
                ]
            ):
                category = "signoff_wrapup"
            elif any(
                w in lowered for w in ["select", "which", "are you", "what is"]
            ):
                category = "disambiguation_data_collection"

            buckets[category].append(ut)
            rationales[ut] = (
                f"Fallback: Matched keyword context to category '{category}'"
            )

        return {"categorized_utterances": buckets, "rationales": rationales}
