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

"""Converts DFCX test cases to CXAS TurnTestCase format."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from cxas_scrapi.evals.turn_evals import (
    TurnExpectation,
    TurnOperator,
    TurnStep,
    TurnTestCase,
)
from cxas_scrapi.migration.data_models import DFCXAgentIR, MigrationIR

if TYPE_CHECKING:
    from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)

SSML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
FUZZY_MAX_LEN = 100
RESPONSE_MIN_LEN = 10

SUMMARIZE_RESPONSE_PROMPT = (
    "Summarize what this agent response accomplishes in one short sentence. "
    "Focus on the BEHAVIOR or ACTION, not exact wording. "
    "The summary will be used to verify that a different version of this "
    "agent behaves equivalently.\n\n"
    'User said: "{user_input}"\n'
    'Agent responded: "{agent_response}"\n\n'
    'Write one sentence starting with "Agent " that describes what the agent '
    "does in this turn."
)

BATCH_SUMMARIZE_PROMPT = (
    "For each numbered interaction below, summarize what the agent "
    "response accomplishes in one short sentence. Focus on the "
    "BEHAVIOR or ACTION, not exact wording. Each summary will be "
    "used to verify that a different version of this agent behaves "
    "equivalently.\n\n"
    "{interactions}\n\n"
    "Return exactly one summary per interaction, numbered to match. "
    'Each summary must start with "Agent ". Example format:\n'
    "1. Agent greets the user and offers help.\n"
    "2. Agent transfers the call to billing.\n"
)

BATCH_SIZE = 20


class DFCXTestConverter:
    """Converts DFCX test cases into CXAS TurnTestCase objects.

    Args:
        ir: The MigrationIR with compiled agents.
        flow_to_agent_map: Optional explicit mapping from source flow names
            to target agent names. If not provided, uses ir.agents keys
            directly (correct for pre-consolidation Stage 0).
    """

    def __init__(
        self,
        ir: MigrationIR,
        flow_to_agent_map: dict[str, str] | None = None,
        gemini_client: GeminiGenerate | None = None,
    ):
        self.ir = ir
        self._agent_names = set(ir.agents.keys())
        self._flow_map = flow_to_agent_map or {}
        self._gemini = gemini_client
        self._summarize_cache: dict[str, str] = {}

    def convert_all(
        self, source: DFCXAgentIR
    ) -> tuple[dict[str, list[TurnTestCase]], dict[str, Any]]:
        """Convert all DFCX test cases and return (tests_by_agent, report)."""
        if self._gemini is not None:
            self._batch_summarize(source.test_cases)

        tests_by_agent: dict[str, list[TurnTestCase]] = {}
        skipped: list[dict[str, str]] = []
        seen_names: dict[str, int] = {}
        dtmf_count = 0
        empty_text_count = 0
        transfer_count = 0
        behavioral_count = 0
        fuzzy_match_count = 0

        for tc in source.test_cases:
            agent_name = self._route_test_to_agent(tc)
            if agent_name is None:
                skipped.append(
                    {
                        "name": tc.get("displayName", "unknown"),
                        "reason": self._skip_reason(tc),
                    }
                )
                continue

            result = self._convert_single_test(tc)
            if result is None:
                skipped.append(
                    {
                        "name": tc.get("displayName", "unknown"),
                        "reason": "no_assertions",
                    }
                )
                continue

            test_case, notes = result
            base_name = test_case.name
            key = f"{agent_name}::{base_name}"
            if key in seen_names:
                seen_names[key] += 1
                test_case.name = f"{base_name} ({seen_names[key]})"
            else:
                seen_names[key] = 1

            tests_by_agent.setdefault(agent_name, []).append(test_case)
            dtmf_count += notes.get("dtmf_turns", 0)
            empty_text_count += notes.get("empty_text_turns", 0)
            transfer_count += notes.get("transfer_assertions", 0)
            behavioral_count += notes.get("behavioral_assertions", 0)
            fuzzy_match_count += notes.get("fuzzy_match_assertions", 0)

        report = {
            "total_source_tests": len(source.test_cases),
            "converted": sum(len(v) for v in tests_by_agent.values()),
            "skipped": len(skipped),
            "skipped_details": skipped[:20],
            "tests_per_agent": {k: len(v) for k, v in tests_by_agent.items()},
            "dtmf_as_text_count": dtmf_count,
            "empty_text_turns_collapsed": empty_text_count,
            "agent_transfer_assertions": transfer_count,
            "behavioral_assertions": behavioral_count,
            "fuzzy_match_assertions": fuzzy_match_count,
        }

        logger.info(
            "[TestConverter] Converted %d/%d tests (%d skipped)",
            report["converted"],
            report["total_source_tests"],
            report["skipped"],
        )

        return tests_by_agent, report

    def _route_test_to_agent(self, test_case: dict) -> str | None:
        flow_name = test_case.get("testConfig", {}).get("flow")
        if not flow_name:
            return None
        if flow_name in self._flow_map:
            return self._flow_map[flow_name]
        if flow_name in self._agent_names:
            return flow_name
        sanitized = self._sanitize_name(flow_name)
        for agent_name in self._agent_names:
            if self._sanitize_name(agent_name) == sanitized:
                return agent_name
        return None

    def _skip_reason(self, test_case: dict) -> str:
        flow = test_case.get("testConfig", {}).get("flow")
        if not flow:
            return "no_flow"
        return f"unknown_flow:{flow}"

    def _convert_single_test(
        self, test_case: dict
    ) -> tuple[TurnTestCase, dict[str, int]] | None:
        display_name = test_case.get("displayName", "unnamed")
        source_tags = test_case.get("tags", [])
        turns_data = test_case.get("testCaseConversationTurns", [])
        if not turns_data:
            return None

        steps: list[TurnStep] = []
        notes: dict[str, int] = {
            "dtmf_turns": 0,
            "transfer_assertions": 0,
            "behavioral_assertions": 0,
            "fuzzy_match_assertions": 0,
        }
        prev_flow: str | None = None
        conversion_tags: set[str] = set()

        for i, turn in enumerate(turns_data):
            user_input = turn.get("userInput", {})
            agent_output = turn.get("virtualAgentOutput", {})

            if self._is_empty_text_turn(user_input):
                notes["empty_text_turns"] = notes.get("empty_text_turns", 0) + 1
                current_flow = agent_output.get("currentFlow", {}).get("name")
                prev_flow = current_flow or prev_flow
                continue

            user, event, variables = self._map_user_input(user_input, i)
            expectations = self._map_expectations(
                agent_output, prev_flow, user_text=user or ""
            )

            if user_input.get("input", {}).get("dtmf"):
                notes["dtmf_turns"] += 1
                conversion_tags.add("#dtmf-as-text")

            for exp in expectations:
                if isinstance(exp, str):
                    notes["behavioral_assertions"] += 1
                    conversion_tags.add("#behavioral")
                elif isinstance(exp, TurnExpectation):
                    if exp.type == TurnOperator.AGENT_TRANSFER:
                        notes["transfer_assertions"] += 1
                    elif exp.type == TurnOperator.FUZZY_MATCH:
                        notes["fuzzy_match_assertions"] += 1

            current_flow = agent_output.get("currentFlow", {}).get("name")
            prev_flow = current_flow or prev_flow

            step = TurnStep(
                turn=f"Turn {len(steps) + 1}",
                user=user,
                event=event,
                variables=variables,
                expectations=expectations,
            )
            steps.append(step)

        has_assertions = any(step.expectations for step in steps)
        if not has_assertions:
            return None

        tags = list({*source_tags, "#migrated-dfcx", *conversion_tags})

        test = TurnTestCase(
            name=display_name,
            tags=tags,
            turns=steps,
        )
        return test, notes

    @staticmethod
    def _is_empty_text_turn(user_input: dict) -> bool:
        """Detect DFCX auto-advance turns: {"text": {}, "languageCode": "en"}.
        These have no user input and are used to advance flows in DFCX."""
        inp = user_input.get("input", {})
        if "event" in inp or "dtmf" in inp:
            return False
        text_obj = inp.get("text")
        if not isinstance(text_obj, dict):
            return False
        return not text_obj.get("text")

    def _map_user_input(
        self, user_input: dict, turn_index: int
    ) -> tuple[str | None, str | None, dict[str, Any]]:
        inp = user_input.get("input", {})
        user: str | None = None
        event: str | None = None
        variables: dict[str, Any] = {}

        if "text" in inp:
            text_obj = inp["text"]
            user = (
                text_obj.get("text", "") if isinstance(text_obj, dict) else ""
            )
        if "event" in inp:
            event = (
                inp["event"].get("event")
                if isinstance(inp["event"], dict)
                else None
            )
        if "dtmf" in inp:
            dtmf = inp["dtmf"]
            digits = dtmf.get("digits", "") if isinstance(dtmf, dict) else ""
            if digits:
                user = digits

        if turn_index == 0:
            injected = user_input.get("injectedParameters", {})
            if injected:
                variables = dict(injected)

        return user, event, variables

    def _map_expectations(
        self,
        output: dict,
        prev_flow: str | None,
        user_text: str = "",
    ) -> list[TurnExpectation | str]:
        expectations: list[TurnExpectation | str] = []

        # Tier 1: AGENT_TRANSFER (structural)
        current_flow = output.get("currentFlow", {}).get("name")
        if current_flow and prev_flow and current_flow != prev_flow:
            target_agent = self._flow_to_agent(current_flow)
            if target_agent:
                expectations.append(
                    TurnExpectation(
                        type=TurnOperator.AGENT_TRANSFER,
                        value=target_agent,
                    )
                )

        # Tier 2 / 3: response text → behavioral string or FUZZY_MATCH
        response_text = self._extract_response_text(output)
        if response_text:
            if self._gemini is not None:
                summary = self._summarize_response(response_text, user_text)
                expectations.append(summary)
            else:
                expectations.append(
                    TurnExpectation(
                        type=TurnOperator.FUZZY_MATCH,
                        value=response_text[:FUZZY_MAX_LEN],
                    )
                )

        return expectations

    def _extract_response_text(self, output: dict) -> str:
        """Extract the first usable response text from a DFCX turn output."""
        for resp_group in output.get("textResponses", []):
            for text in resp_group.get("text", []):
                clean = self._strip_ssml(text)
                if clean and len(clean) >= RESPONSE_MIN_LEN:
                    return clean
        return ""

    def _summarize_response(
        self, response_text: str, user_text: str = ""
    ) -> str:
        """Use Gemini to summarize a DFCX response into a behavioral
        description suitable for LLM-judged evaluation."""
        cache_key = f"{user_text}|||{response_text}"
        if cache_key in self._summarize_cache:
            return self._summarize_cache[cache_key]

        prompt = SUMMARIZE_RESPONSE_PROMPT.format(
            user_input=user_text or "(conversation start)",
            agent_response=response_text,
        )
        result = self._gemini.generate(prompt, temperature=0.0)
        summary = (
            result.strip()
            if result
            else f"Agent responds appropriately to: {user_text or 'user'}"
        )
        self._summarize_cache[cache_key] = summary
        return summary

    def _collect_summarization_pairs(
        self, test_cases: list[dict]
    ) -> list[tuple[str, str]]:
        """Extract unique (user_text, response_text) pairs from all
        test case turns that would need Gemini summarization."""
        seen: set[str] = set()
        pairs: list[tuple[str, str]] = []
        for tc in test_cases:
            turns = tc.get("testCaseConversationTurns", [])
            for turn in turns:
                user_input = turn.get("userInput", {})
                if self._is_empty_text_turn(user_input):
                    continue
                output = turn.get("virtualAgentOutput", {})
                response_text = self._extract_response_text(output)
                if not response_text:
                    continue
                inp = user_input.get("input", {})
                user_text = ""
                if "text" in inp:
                    text_obj = inp["text"]
                    user_text = (
                        text_obj.get("text", "")
                        if isinstance(text_obj, dict)
                        else ""
                    )
                elif "dtmf" in inp:
                    dtmf = inp["dtmf"]
                    user_text = (
                        dtmf.get("digits", "") if isinstance(dtmf, dict) else ""
                    )
                cache_key = f"{user_text}|||{response_text}"
                if cache_key not in seen:
                    seen.add(cache_key)
                    pairs.append((user_text, response_text))
        return pairs

    def _batch_summarize(self, test_cases: list[dict]) -> None:
        """Pre-fill the summarization cache using batched Gemini
        calls. Each call summarizes up to BATCH_SIZE interactions."""
        pairs = self._collect_summarization_pairs(test_cases)
        if not pairs:
            return

        total_batches = (len(pairs) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(
            "[TestConverter] Batch-summarizing %d unique responses "
            "in %d calls (batch_size=%d)",
            len(pairs),
            total_batches,
            BATCH_SIZE,
        )

        for batch_idx in range(0, len(pairs), BATCH_SIZE):
            batch = pairs[batch_idx : batch_idx + BATCH_SIZE]
            interactions = "\n".join(
                f'{i + 1}. User: "{u or "(conversation start)"}"\n'
                f'   Agent: "{r}"'
                for i, (u, r) in enumerate(batch)
            )
            prompt = BATCH_SUMMARIZE_PROMPT.format(interactions=interactions)
            result = self._gemini.generate(prompt, temperature=0.0)
            summaries = self._parse_batch_response(result, len(batch))
            for (user_text, response_text), summary in zip(
                batch, summaries, strict=True
            ):
                cache_key = f"{user_text}|||{response_text}"
                self._summarize_cache[cache_key] = summary

        logger.info(
            "[TestConverter] Cache pre-filled with %d summaries",
            len(self._summarize_cache),
        )

    @staticmethod
    def _parse_batch_response(
        response: str | None, expected_count: int
    ) -> list[str]:
        """Parse numbered lines from a batch summarization response.
        Falls back to generic summaries for unparseable lines."""
        if not response:
            return ["Agent responds appropriately to the user"] * expected_count

        lines = response.strip().splitlines()
        summaries: list[str] = []
        for line in lines:
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line.strip())
            if cleaned:
                summaries.append(cleaned)

        while len(summaries) < expected_count:
            summaries.append("Agent responds appropriately to the user")
        return summaries[:expected_count]

    def _flow_to_agent(self, flow_name: str) -> str | None:
        if flow_name in self._flow_map:
            return self._flow_map[flow_name]
        if flow_name in self._agent_names:
            return flow_name
        sanitized = self._sanitize_name(flow_name)
        for agent_name in self._agent_names:
            if self._sanitize_name(agent_name) == sanitized:
                return agent_name
        return None

    @staticmethod
    def _strip_ssml(text: str) -> str:
        stripped = SSML_TAG_RE.sub("", text)
        return WHITESPACE_RE.sub(" ", stripped).strip()

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]", "", name).lower()

    @staticmethod
    def serialize_to_yaml(
        tests_by_agent: dict[str, list[TurnTestCase]],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for agent_name, tests in tests_by_agent.items():
            data = {
                "tests": [
                    t.model_dump(
                        mode="json", exclude_none=True, exclude_defaults=True
                    )
                    for t in tests
                ]
            }
            result[agent_name] = yaml.dump(
                data,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        return result

    @staticmethod
    def reroute_after_consolidation(
        test_cases: dict[str, Any],
        grouping: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-route test cases from pre-consolidation agent names to
        post-consolidation group names. Works with both TurnTestCase
        objects and serialized dicts."""
        flow_to_group = {
            flow: group_name
            for group_name, entry in grouping.items()
            for flow in entry.get("agents", [])
        }
        new_tests: dict[str, list] = {}
        for agent_name, tests in test_cases.items():
            target = flow_to_group.get(agent_name, agent_name)
            new_tests.setdefault(target, []).extend(tests)
        return new_tests
