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

"""Rubric-based scoring for agent benchmarking."""

import asyncio
import dataclasses
import json
from collections.abc import Sequence
from typing import Any, Final

from google import genai

from skill_eval import exceptions, scenario

_DEFAULT_MODEL: Final[str] = "gemini-3.1-pro-preview"


@dataclasses.dataclass(frozen=True)
class RubricCriterion:
    """A single scored criterion."""

    criteria: str
    score: int
    reasoning: str


@dataclasses.dataclass(frozen=True)
class ScorerTurn:
    """A single turn in the conversation for scoring."""

    user_message: str
    agent_response: str
    tool_interactions: list[Any]
    events: list[Any] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ScoreResult:
    """The result of rubric grading."""

    scores: list[RubricCriterion]
    total_score: int
    max_score: int
    summary: str
    error: str | None = None


class Scorer:
    """Grades agent performance against a rubric using Vertex AI."""

    def __init__(
        self,
        project: str,
        location: str = "global",
        model_name: str = _DEFAULT_MODEL,
    ) -> None:
        self._project = project
        self._location = location
        self._model_name = model_name
        self._client = genai.Client(
            vertexai=True, project=project, location=location
        )

    async def grade_conversation(
        self,
        scen: scenario.Scenario,
        turns: Sequence[ScorerTurn],
    ) -> ScoreResult:
        """Grades the conversation history against the rubric."""
        criteria = scen.rubric
        if not criteria:
            return ScoreResult(
                scores=[],
                total_score=0,
                max_score=0,
                summary="No rubric section found in scenario file.",
            )

        transcript_parts = []
        for turn in turns:
            transcript_parts.append(f"USER: {turn.user_message}")
            agent_part = f"AGENT: {turn.agent_response}"
            events = getattr(turn, "events", None)
            if events:
                agent_part += "\n\n[Agent Execution Trajectory:]"
                for event in events:
                    ev_type = type(event).__name__
                    if ev_type == "ToolCallEvent":
                        agent_part += (
                            f"\n- [Tool Call | {event.source}] CallID: {event.call_id} |"
                            f" {event.name}({event.args})"
                        )
                    elif ev_type == "ToolResultEvent":
                        agent_part += (
                            f"\n- [Tool Result | {event.source}] CallID: {event.call_id} |"
                            f" Status: {'Error' if event.is_error else 'Success'} | Output:"
                            f" {event.output}"
                        )
                    elif ev_type == "AgentMessageEvent" and getattr(
                        event, "is_thought", False
                    ):
                        agent_part += (
                            f"\n- [Thought | {event.source}] {event.text}"
                        )
                    elif ev_type == "SubagentEvent":
                        agent_part += (
                            f"\n- [Subagent | {event.source}] {event.action} | Prompt:"
                            f" {event.prompt}"
                        )
            elif turn.tool_interactions:
                agent_part += (
                    "\n\n[Agent used the following tools during this turn:]"
                )
                for ti in turn.tool_interactions:
                    agent_part += f"\n- Tool: {ti.name}\n  Args: {ti.args}\n"
                    agent_part += f"  Output: {ti.output}"
            transcript_parts.append(agent_part)

        transcript = "\n\n".join(transcript_parts)

        rubric_json = json.dumps(
            [dataclasses.asdict(c) for c in criteria], indent=2
        )
        prompt = (
            "\nYou are an expert QA Evaluator.\n"
            "Your task is to grade an AI Agent's performance based on a "
            "conversation transcript and a specific Rubric.\n\n"
            "**SCORING SYSTEM:**\n"
            "- **+2 POINTS (Perfect)**: Criterion fulfilled WITHOUT ANY "
            "nudging from the user AND without significant errors. The agent "
            "acted autonomously and correctly.\n"
            "- **+1 POINT (Good/Nudged)**: Criterion fulfilled, BUT the user "
            "had to nudge/correct the agent, OR the agent made multiple "
            "errors (e.g. API failures, syntax errors) before succeeding.\n"
            "- **0 POINTS (Failed)**: Criterion failed. The agent did not "
            "achieve the goal despite potential help, or the outcome was "
            "incorrect.\n\n"
            "**RUBRIC CRITERIA:**\n"
            f"{rubric_json}\n\n"
            "**TRANSCRIPT:**\n"
            f"{transcript}\n\n"
            "**INSTRUCTIONS:**\n"
            "For EACH criterion in the rubric:\n"
            "1. Determine the score (0, 1, or 2).\n"
            '2. Provide a brief "Reasoning" explaining why this score was '
            "given, citing specific events.\n"
            "3. Ensure all strings are properly escaped and the JSON is "
            "strictly valid with no trailing commas.\n\n"
            "Output PURE JSON in the following format:\n"
            "{\n"
            '  "scores": [\n'
            '    { "criteria": "Criteria Name", "score": 2, '
            '"reasoning": "..." },\n'
            "    ...\n"
            "  ],\n"
            '  "summary": "Overall assessment..."\n'
            "}\n"
        )

        loop = asyncio.get_running_loop()
        last_error = None

        def _generate() -> Any:
            client = genai.Client(
                vertexai=True, project=self._project, location=self._location
            )
            return client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )

        for _attempt in range(3):
            try:
                response = await loop.run_in_executor(None, _generate)

                if not response.text:
                    raise ValueError("Empty response from Gemini Scorer")

                text = response.text.strip()
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    text = text[start : end + 1]

                data = json.loads(text)

                scores = [
                    RubricCriterion(
                        criteria=s["criteria"],
                        score=int(s["score"]),
                        reasoning=s["reasoning"],
                    )
                    for s in data.get("scores", [])
                ]

                total_score = sum(s.score for s in scores)
                max_score = len(criteria) * 2

                return ScoreResult(
                    scores=scores,
                    total_score=total_score,
                    max_score=max_score,
                    summary=data.get("summary", ""),
                )

            except Exception as e:
                last_error = e
                await asyncio.sleep(1)

        raise exceptions.ScorerError(
            "Grading failed due to error"
        ) from last_error
