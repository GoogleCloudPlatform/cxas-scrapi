"""Scorecard Evaluation Runner for rapid prompt engineering against golden conversations."""

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
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

import cxas_scrapi.utils.scorecard_template_manager as template_manager
from cxas_scrapi.utils.gemini import GeminiGenerate


class QuestionEvalOutput(BaseModel):
    """Structured output for question evaluation."""

    answer_key: str = Field(
        description="The key or value of the selected answer choice."
    )
    rationale: str = Field(
        description="Detailed explanation and rationale for the decision, referencing specific conversation turns if applicable."
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score between 0.0 and 1.0 for the answer.",
    )


@dataclass
class QuestionEvaluationResult:
    """Individual question evaluation result for a conversation."""

    conversation_id: str
    question_id: str
    question_body: str
    predicted_answer: str
    predicted_score: float | None = None
    expected_answer: str | None = None
    expected_score: float | None = None
    is_match: bool | None = None
    rationale: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScorecardEvalReport:
    """Comprehensive evaluation report for a scorecard evaluated against golden conversations."""

    scorecard_display_name: str
    total_conversations: int
    total_evaluations: int
    overall_accuracy: float | None
    question_metrics: dict[str, dict[str, Any]]
    results: list[QuestionEvaluationResult]
    discrepancies: list[QuestionEvaluationResult]

    def to_dataframe(self) -> pd.DataFrame:
        """Converts results into a tidy pandas DataFrame."""
        rows = [asdict(r) for r in self.results]
        return pd.DataFrame(rows)

    def to_dict(self) -> dict[str, Any]:
        """Converts report into a serializable dictionary."""
        return {
            "scorecard_display_name": self.scorecard_display_name,
            "total_conversations": self.total_conversations,
            "total_evaluations": self.total_evaluations,
            "overall_accuracy": self.overall_accuracy,
            "question_metrics": self.question_metrics,
            "results": [asdict(r) for r in self.results],
            "discrepancies": [asdict(d) for d in self.discrepancies],
        }


class ScorecardEvalRunner:
    """Evaluates scorecard questions and prompt instructions directly against

    golden conversation transcripts without waiting for asynchronous cloud
    tuning.
    """

    def __init__(
        self,
        project_id: str = "default-project",
        location: str = "global",
        model_name: str = "gemini-2.5-flash",
        gemini_client: GeminiGenerate | None = None,
        **kwargs: Any,
    ) -> None:
        self.project_id = project_id
        self.location = location
        self.model_name = model_name
        self.gemini_client = gemini_client or GeminiGenerate(
            project_id=project_id,
            location=location,
            model_name=model_name,
            **kwargs,
        )

    def _format_conversation_transcript(self, conversation_data: Any) -> str:
        """Formats various conversation formats (string, dict, turns list) into a readable transcript."""
        if isinstance(conversation_data, str):
            return conversation_data

        if isinstance(conversation_data, dict):
            # If standard CCAI Insights conversation JSON
            transcript = conversation_data.get("transcript")
            if transcript:
                return transcript

            turns = conversation_data.get("turns") or conversation_data.get(
                "conversationTurns"
            )
            if turns and isinstance(turns, list):
                formatted_turns = []
                for idx, t in enumerate(turns):
                    speaker = (
                        t.get("speaker")
                        or t.get("role")
                        or t.get("participantRole", "USER")
                    )
                    text = (
                        t.get("text")
                        or t.get("content")
                        or t.get("userContent")
                        or t.get("agentContent", "")
                    )
                    formatted_turns.append(
                        f"Turn {idx + 1} [{speaker}]: {text}"
                    )
                return "\n".join(formatted_turns)

            # Fallback to json dump of text content
            return json.dumps(conversation_data, indent=2)

        if isinstance(conversation_data, list):
            formatted_turns = []
            for idx, t in enumerate(conversation_data):
                if isinstance(t, dict):
                    speaker = t.get("speaker") or t.get("role", "UNKNOWN")
                    text = t.get("text") or t.get("content", "")
                    formatted_turns.append(
                        f"Turn {idx + 1} [{speaker}]: {text}"
                    )
                else:
                    formatted_turns.append(f"Turn {idx + 1}: {t}")
            return "\n".join(formatted_turns)

        return str(conversation_data)

    def _build_question_prompt(
        self,
        question: dict[str, Any],
        conversation_transcript: str,
    ) -> str:
        """Builds an evaluation prompt for a single scorecard question."""
        body = question.get("questionBody") or question.get("body", "")
        instructions = question.get("answerInstructions") or question.get(
            "instructions", ""
        )
        answer_choices = question.get("answerChoices") or question.get(
            "choices", []
        )

        choices_desc = []
        for choice in answer_choices:
            key = choice.get("key") or choice.get("value", "")
            choice_body = choice.get("body") or choice.get("description", "")
            score = choice.get("score")
            score_str = f" (Score: {score})" if score is not None else ""
            choices_desc.append(f"- Choice '{key}': {choice_body}{score_str}")

        formatted_choices = (
            "\n".join(choices_desc)
            if choices_desc
            else "Standard Yes / No / NA"
        )

        prompt = f"""You are an expert QA evaluation annotator reviewing customer support conversations according to a QA Scorecard.

### Scorecard Question:
{body}

### Answer Choices:
{formatted_choices}

### Specific Evaluation Instructions & Guidelines:
{instructions or "Evaluate the conversation objectively based on whether the criteria was met."}

### Conversation Transcript to Evaluate:
\"\"\"
{conversation_transcript}
\"\"\"

### Task:
Evaluate the conversation transcript and determine the single most accurate answer choice.
Return a valid JSON object matching the following structure:
{{
  "answer_key": "<The exact key or string of the selected answer choice>",
  "rationale": "<Detailed explanation citing evidence or specific turn numbers from the transcript>",
  "confidence": 1.0
}}
"""
        return prompt

    def evaluate_question(
        self,
        question: dict[str, Any],
        conversation: Any,
        conversation_id: str = "conv_1",
        expected_answer: str | None = None,
    ) -> QuestionEvaluationResult:
        """Evaluates a single question against a single conversation."""
        transcript = self._format_conversation_transcript(conversation)
        prompt = self._build_question_prompt(question, transcript)

        question_body = question.get("questionBody") or question.get("body", "")
        question_id = (
            question.get("name")
            or question.get("abbreviation")
            or question_body[:30]
        )

        try:
            response = self.gemini_client.generate(
                prompt=prompt,
                response_schema=QuestionEvalOutput,
                temperature=0.0,
            )
            data = (
                json.loads(response) if isinstance(response, str) else response
            )
            predicted_answer = data.get("answer_key", "").strip()
            rationale = data.get("rationale", "")
            confidence = data.get("confidence", 1.0)
        except Exception as e:
            logging.warning("Error evaluating question %s: %s", question_id, e)
            # Fallback to standard text generation if structured output fails
            try:
                raw_text = self.gemini_client.generate(
                    prompt=prompt, temperature=0.0
                )
                predicted_answer = raw_text.strip()
                rationale = "Generated from unstructured output."
                confidence = 0.5
            except Exception as inner_e:
                predicted_answer = "ERROR"
                rationale = f"Evaluation failed: {inner_e}"
                confidence = 0.0

        # Calculate score matching if choices define scores
        predicted_score = None
        expected_score = None
        for choice in question.get("answerChoices", []):
            choice_key = str(
                choice.get("key") or choice.get("value", "")
            ).lower()
            if (
                choice_key == predicted_answer.lower()
                or str(choice.get("body", "")).lower()
                == predicted_answer.lower()
            ):
                predicted_score = choice.get("score")
            if expected_answer and (
                choice_key == str(expected_answer).lower()
                or str(choice.get("body", "")).lower()
                == str(expected_answer).lower()
            ):
                expected_score = choice.get("score")

        is_match = None
        if expected_answer is not None:
            is_match = (
                predicted_answer.strip().lower()
                == str(expected_answer).strip().lower()
            )

        return QuestionEvaluationResult(
            conversation_id=conversation_id,
            question_id=question_id,
            question_body=question_body,
            predicted_answer=predicted_answer,
            predicted_score=predicted_score,
            expected_answer=expected_answer,
            expected_score=expected_score,
            is_match=is_match,
            rationale=rationale,
            confidence=confidence,
        )

    def evaluate_scorecard_on_calibration_set(
        self,
        scorecard_template: str | dict[str, Any],
        calibration_dataset: list[dict[str, Any]],
    ) -> ScorecardEvalReport:
        """Evaluates a full scorecard template against a list of QA calibration conversation cases.

        Args:
            scorecard_template: Path to a YAML/JSON scorecard file or a dict
              containing 'qaScorecard' and 'qaQuestions'.
            calibration_dataset: List of calibration cases. Each item should have:
              - 'conversation_id' or 'id'
              - 'conversation' or 'transcript' or 'turns'
              - Optional 'expected_answers': dict of {question_identifier: expected_key}

        Returns:
            A ScorecardEvalReport with accuracy, confusion matrices, and discrepancies.
        """
        if isinstance(scorecard_template, str):
            scorecard_dict, questions = (
                template_manager.load_scorecard_template(scorecard_template)
            )
        else:
            scorecard_dict = scorecard_template.get("qaScorecard", {})
            questions = scorecard_template.get("qaQuestions", [])

        display_name = scorecard_dict.get("displayName", "Scorecard")
        results: list[QuestionEvaluationResult] = []
        discrepancies: list[QuestionEvaluationResult] = []

        for case_idx, case in enumerate(calibration_dataset):
            convo_id = (
                case.get("conversation_id")
                or case.get("id")
                or f"case_{case_idx + 1}"
            )
            convo_data = (
                case.get("conversation")
                or case.get("transcript")
                or case.get("turns")
                or case
            )
            expected_answers = (
                case.get("expected_answers")
                or case.get("expectedAnswers")
                or case.get("ground_truth")
                or {}
            )

            for question in questions:
                q_body = question.get("questionBody") or question.get(
                    "body", ""
                )
                q_key = (
                    question.get("abbreviation")
                    or question.get("name")
                    or q_body
                )

                # Look up expected answer if provided
                expected = (
                    expected_answers.get(q_key)
                    or expected_answers.get(q_body)
                    or expected_answers.get(question.get("abbreviation", ""))
                )

                res = self.evaluate_question(
                    question=question,
                    conversation=convo_data,
                    conversation_id=convo_id,
                    expected_answer=expected,
                )
                results.append(res)
                if res.is_match is False:
                    discrepancies.append(res)

        # Compute Question Metrics
        question_metrics: dict[str, dict[str, Any]] = {}
        total_matches = 0
        total_with_expected = 0

        for question in questions:
            q_body = question.get("questionBody") or question.get("body", "")
            q_key = (
                question.get("abbreviation") or question.get("name") or q_body
            )

            q_results = [r for r in results if r.question_body == q_body]
            q_with_exp = [r for r in q_results if r.expected_answer is not None]
            q_matches = [r for r in q_with_exp if r.is_match is True]

            accuracy = len(q_matches) / len(q_with_exp) if q_with_exp else None
            total_matches += len(q_matches)
            total_with_expected += len(q_with_exp)

            question_metrics[q_key] = {
                "question_body": q_body,
                "total_evaluated": len(q_results),
                "total_with_ground_truth": len(q_with_exp),
                "accuracy": accuracy,
                "discrepancies_count": len(q_with_exp) - len(q_matches),
            }

        overall_accuracy = (
            total_matches / total_with_expected
            if total_with_expected > 0
            else None
        )

        return ScorecardEvalReport(
            scorecard_display_name=display_name,
            total_conversations=len(calibration_dataset),
            total_evaluations=len(results),
            overall_accuracy=overall_accuracy,
            question_metrics=question_metrics,
            results=results,
            discrepancies=discrepancies,
        )

    def evaluate_scorecard_on_goldens(
        self,
        scorecard_template: str | dict[str, Any],
        golden_dataset: list[dict[str, Any]],
    ) -> ScorecardEvalReport:
        """Deprecated alias for evaluate_scorecard_on_calibration_set."""
        return self.evaluate_scorecard_on_calibration_set(
            scorecard_template=scorecard_template,
            calibration_dataset=golden_dataset,
        )
