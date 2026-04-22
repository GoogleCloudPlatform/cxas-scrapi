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
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from cxas_scrapi.core.scorecards import Scorecards


class InsightsUtils:
    """Utility class for high-level operations on Insights & Scorecards."""

    def __init__(
        self, project_id: str, location: str = "us-central1", **kwargs
    ):
        self.project_id = project_id
        self.location = location
        self.scorecards_client = Scorecards(project_id, location, **kwargs)

    # --- Match logic ported from your scorecard_operations.py ---
    def _match_questions(self, q1: Dict[str, Any], q2: Dict[str, Any]) -> bool:
        """Matches questions based on core content fields."""
        fields_to_match = (
            "questionBody",
            "answerChoices",
            "answerInstructions",
        )
        for field in fields_to_match:
            if q1.get(field) != q2.get(field):
                return False
        return True

    def _sync_questions(
        self,
        target_revision_name: str,
        template_questions: List[Dict[str, Any]],
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
        scorecard_dict: Dict[str, Any],
        questions: List[Dict[str, Any]],
        target_scorecard_id: Optional[str] = None,
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
        return target_revision_name

    def analyze_conversations(
        self,
        conversations: List[str],
        scorecard_name: str,
        export_to_bq: bool = False,
    ) -> pd.DataFrame:
        """Abstracts away setting up the analysis rules and triggering
        batch evaluation.
        (Conceptual placeholder for full workflow returning DataFrames).
        """
        logging.info(
            "Triggering analysis on %d conversations using scorecard %s.",
            len(conversations),
            scorecard_name,
        )

        raise NotImplementedError(
            "Batch evaluation using Scorecards via the Insights API has not "
            "been implemented yet. "
            "Need to wire up analysis rule creation and job polling in "
            "core/insights.py."
        )
