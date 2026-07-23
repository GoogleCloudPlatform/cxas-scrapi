"""Insights Utilities class for CXAS Scrapi."""

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

import logging
import typing
import uuid
from typing import Any

import pandas as pd

from cxas_scrapi.core.analysis_rules import AnalysisRules
from cxas_scrapi.core.issue_models import IssueModels
from cxas_scrapi.core.scorecards import Scorecards


class InsightsUtils:
    """Utility class for high-level operations on Insights & Scorecards."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        **kwargs: typing.Any,
    ) -> None:
        self.project_id = project_id
        self.location = location
        self.scorecards_client = Scorecards(project_id, location, **kwargs)
        self.issue_models_client = IssueModels(project_id, location, **kwargs)
        self.analysis_rules_client = AnalysisRules(
            project_id, location, **kwargs
        )

    # --- Match logic ported from your scorecard_operations.py ---
    def _match_questions(self, q1: dict[str, Any], q2: dict[str, Any]) -> bool:
        """Matches questions based on core content fields."""
        fields_to_match = (
            "questionBody",
            "answerChoices",
            "answerInstructions",
        )
        return all(q1.get(field) == q2.get(field) for field in fields_to_match)

    def _sync_questions(
        self,
        target_revision_name: str,
        template_questions: list[dict[str, Any]],
    ) -> None:
        """Syncs questions to the target revision non-destructively."""
        existing_questions = self.scorecards_client.list_questions(
            target_revision_name
        )

        matched_existing_names = set()
        matched_template_indices = set()

        for template_idx, template_q in enumerate(template_questions):
            for existing_q in existing_questions:
                if existing_q["name"] in matched_existing_names:
                    continue
                if self._match_questions(existing_q, template_q):
                    matched_existing_names.add(existing_q["name"])
                    matched_template_indices.add(template_idx)
                    logging.info(
                        "Updating matched question: %s", existing_q["name"]
                    )
                    self.scorecards_client.patch_question(
                        existing_q["name"], template_q
                    )
                    break

        # Delete existing questions that are not in the template.
        for existing_q in existing_questions:
            if existing_q["name"] not in matched_existing_names:
                logging.info(
                    "Deleting obsolete question: %s", existing_q["name"]
                )
                self.scorecards_client.delete_question(existing_q["name"])

        # Add new questions from the template.
        for template_idx, template_q in enumerate(template_questions):
            if template_idx not in matched_template_indices:
                q_desc = (
                    template_q.get("abbreviation")
                    or template_q.get("questionBody", "")[:30]
                )
                logging.info("Creating new question: %s", q_desc)
                self.scorecards_client.create_question(
                    target_revision_name, template_q
                )

    def import_scorecard(
        self,
        scorecard_dict: dict[str, Any],
        questions: list[dict[str, Any]],
        target_scorecard_id: str | None = None,
    ) -> str:
        """High level abstraction to import or update a scorecard and its
        questions from dictionaries."""
        scorecard_id = target_scorecard_id or f"sc-{uuid.uuid4()}"
        full_scorecard_name = (
            f"{self.scorecards_client.parent}/qaScorecards/{scorecard_id}"
        )

        try:
            # Check if exists
            latest_revision = self.scorecards_client.get_latest_revision(
                full_scorecard_name
            )
            state = latest_revision.get("state")
            if state == "EDITABLE":
                target_revision_name = latest_revision["name"]
            elif state == "TRAINING":
                raise ValueError(
                    "Scorecard revision is currently TRAINING. Cannot import."
                )
            else:
                target_revision_name = self.scorecards_client.create_revision(
                    full_scorecard_name
                )["name"]

        except Exception:  # 404
            logging.info("Assuming scorecard does not exist. Creating...")
            self.scorecards_client.create_scorecard(
                scorecard_id, scorecard_dict
            )
            target_revision_name = self.scorecards_client.get_latest_revision(
                full_scorecard_name
            )["name"]

        self._sync_questions(target_revision_name, questions)
        try:
            self.scorecards_client.tune_revision(target_revision_name)
            logging.info(
                "Triggered tuning on %s to move out of EDITABLE (draft) state.",
                target_revision_name,
            )
        except Exception as e:
            logging.debug("Could not trigger tuning after sync: %s", e)
        return target_revision_name

    def analyze_conversations(
        self,
        conversations: list[str],
        scorecard_name: str,
        export_to_bq: bool = False,
    ) -> pd.DataFrame:
        """Triggers batch evaluation on conversations using a scorecard
        and extracts results into a DataFrame."""
        logging.info(
            "Triggering analysis on %d conversations using scorecard %s.",
            len(conversations),
            scorecard_name,
        )
        annotator_selector = {
            "qaConfig": {
                "scorecardList": {"qaScorecardRevisions": [scorecard_name]}
            }
        }
        if conversations:
            name_filters = [f'name="{c}"' for c in conversations]
            filter_str = " OR ".join(name_filters)
        else:
            filter_str = ""

        try:
            self.scorecards_client.bulk_analyze_conversations(
                filter_str=filter_str, annotator_selector=annotator_selector
            )
        except Exception as e:
            logging.warning(
                "bulk_analyze_conversations returned or failed: %s", e
            )

        rows = []
        for conv_name in conversations:
            try:
                full_conv = self.scorecards_client.get_conversation(
                    conv_name, view="FULL"
                )
                qa_answers = list(full_conv.get("qaAnswers", []))
                if "latestAnalysis" in full_conv:
                    qa_scorecard_results = (
                        full_conv.get("latestAnalysis", {})
                        .get("analysisResult", {})
                        .get("callAnalysisMetadata", {})
                        .get("qaScorecardResults", [])
                    )
                    if isinstance(qa_scorecard_results, list):
                        qa_answers.extend(qa_scorecard_results)

                if isinstance(qa_answers, list):
                    for ans in qa_answers:
                        for q_ans in ans.get("qaQuestions", []) or ans.get(
                            "qaAnswers", []
                        ):
                            val_dict = q_ans.get("answerValue", {})
                            raw_score = (
                                val_dict.get("score")
                                if isinstance(val_dict, dict)
                                and val_dict.get("score") is not None
                                else q_ans.get("score")
                            )
                            raw_pot = (
                                val_dict.get("potentialScore")
                                if isinstance(val_dict, dict)
                                and val_dict.get("potentialScore") is not None
                                else q_ans.get("potentialScore")
                            )
                            rows.append(
                                {
                                    "conversation_name": conv_name,
                                    "scorecard_revision": ans.get(
                                        "qaScorecardRevision"
                                    ),
                                    "question_name": q_ans.get("qaQuestion"),
                                    "score": raw_score,
                                    "potential_score": raw_pot,
                                    "answer_value": q_ans.get(
                                        "answerValue", {}
                                    ).get("strValue"),
                                }
                            )
                elif isinstance(qa_answers, dict):
                    for rev, ans in qa_answers.items():
                        for q_ans in ans.get("qaQuestions", []) or ans.get(
                            "qaAnswers", []
                        ):
                            val_dict = q_ans.get("answerValue", {})
                            raw_score = (
                                val_dict.get("score")
                                if isinstance(val_dict, dict)
                                and val_dict.get("score") is not None
                                else q_ans.get("score")
                            )
                            raw_pot = (
                                val_dict.get("potentialScore")
                                if isinstance(val_dict, dict)
                                and val_dict.get("potentialScore") is not None
                                else q_ans.get("potentialScore")
                            )
                            rows.append(
                                {
                                    "conversation_name": conv_name,
                                    "scorecard_revision": rev,
                                    "question_name": q_ans.get("qaQuestion"),
                                    "score": raw_score,
                                    "potential_score": raw_pot,
                                    "answer_value": q_ans.get(
                                        "answerValue", {}
                                    ).get("strValue"),
                                }
                            )
            except Exception as e:
                logging.debug(
                    "Could not retrieve qaAnswers for %s: %s", conv_name, e
                )

        return pd.DataFrame(rows)

    def perform_topic_modelling(
        self,
        display_name: str,
        app_name: str | None = None,
        filter_str: str | None = None,
        deploy: bool = True,
        issue_model_id: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Performs topic modelling for a CXAS app by creating and optionally
        deploying an issue model."""
        return self.issue_models_client.create_topic_model_for_app(
            display_name=display_name,
            app_name=app_name,
            filter_str=filter_str,
            parent=parent,
            issue_model_id=issue_model_id,
            deploy=deploy,
        )

    def create_or_update_scorecard(
        self,
        scorecard_id: str,
        display_name: str,
        description: str,
        questions: list[dict[str, Any]],
        parent: str | None = None,
    ) -> str:
        """Helper to create or update a scorecard and its questions."""
        scorecard_dict = {
            "displayName": display_name,
            "description": description,
        }
        return self.import_scorecard(
            scorecard_dict=scorecard_dict,
            questions=questions,
            target_scorecard_id=scorecard_id,
        )

    def setup_analysis_rule_for_app(
        self,
        display_name: str,
        app_name: str | None = None,
        filter_str: str | None = None,
        scorecard_revisions: list[str] | None = None,
        issue_models: list[str] | None = None,
        run_summarization: bool = True,
        run_sentiment: bool = True,
        active: bool = True,
        parent: str | None = None,
        rule_id: str | None = None,
    ) -> dict[str, Any]:
        """Creates and activates an analysis rule targeting a CXAS app."""
        if scorecard_revisions:
            for rev in scorecard_revisions:
                try:
                    r_obj = self.scorecards_client.get_revision(rev)
                    state = r_obj.get("state")
                    if state == "EDITABLE":
                        logging.warning(
                            "Scorecard revision %s is currently in state "
                            "'EDITABLE' (Draft/Not Ready). Automatically "
                            "triggering tuning/activation before applying rule...",
                            rev,
                        )
                        self.scorecards_client.tune_revision(rev)
                    elif state != "READY":
                        logging.warning(
                            "Scorecard revision %s is in state '%s'. Ensure "
                            "it reaches 'READY' before relying on automated analysis.",
                            rev,
                            state,
                        )
                except Exception as e:
                    logging.debug(
                        "Validation check for revision %s failed: %s", rev, e
                    )

        return self.analysis_rules_client.create_rule_for_app(
            display_name=display_name,
            app_name=app_name,
            filter_str=filter_str,
            scorecard_revisions=scorecard_revisions,
            issue_models=issue_models,
            run_summarization=run_summarization,
            run_sentiment=run_sentiment,
            active=active,
            parent=parent,
            rule_id=rule_id,
        )

    def smoke_test_scorecard(
        self,
        scorecard_revision: str,
        conversations: list[Any],
        parent: str | None = None,
        simulate_if_dict: bool = True,
    ) -> list[dict[str, Any]]:
        """Dry-runs and smoke tests a scorecard revision against real or
        simulated conversations."""
        parent = parent or self.scorecards_client.parent
        results = []
        annotator_selector = {
            "qaConfig": {
                "scorecardList": {"qaScorecardRevisions": [scorecard_revision]}
            }
        }
        for idx, conv in enumerate(conversations):
            conv_name = None
            if isinstance(conv, dict):
                if not simulate_if_dict:
                    continue
                logging.info(
                    "Simulating conversation #%d for smoke test...", idx + 1
                )
                try:
                    created = self.scorecards_client.create_conversation(
                        conv, parent=parent
                    )
                    conv_name = created.get("name")
                except Exception as e:
                    results.append(
                        {
                            "conversation_index": idx,
                            "status": "ERROR",
                            "error": f"Failed to simulate conversation: {e}",
                        }
                    )
                    continue
            elif isinstance(conv, str):
                conv_name = conv
            else:
                continue

            if not conv_name:
                continue

            logging.info(
                "Analyzing %s with scorecard %s...",
                conv_name,
                scorecard_revision,
            )
            try:
                self.scorecards_client.analyze_conversation(
                    conv_name, annotator_selector=annotator_selector
                )
                full_conv = self.scorecards_client.get_conversation(
                    conv_name, view="FULL"
                )
                qa_answers = full_conv.get("qaAnswers", [])
                matched_answer = None
                if isinstance(qa_answers, list):
                    for ans in qa_answers:
                        if ans.get("qaScorecardRevision") == scorecard_revision:
                            matched_answer = ans
                            break
                elif isinstance(qa_answers, dict):
                    matched_answer = qa_answers.get(scorecard_revision) or next(
                        iter(qa_answers.values()), None
                    )

                results.append(
                    {
                        "conversation_name": conv_name,
                        "status": "PASSED" if matched_answer else "ANALYZED",
                        "qa_answer": matched_answer or {},
                        "turn_count": full_conv.get("turnCount"),
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "conversation_name": conv_name,
                        "status": "ERROR",
                        "error": str(e),
                    }
                )
        return results
