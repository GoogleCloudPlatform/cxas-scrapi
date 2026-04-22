"""Core Scorecards class for CXAS Scrapi."""

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

from typing import Any, Dict, List

from cxas_scrapi.core.insights import Insights

QaScorecard = Dict[str, Any]
QaScorecardRevision = Dict[str, Any]
QaQuestion = Dict[str, Any]


class Scorecards(Insights):
    """Core Class for managing CCAI Insights Scorecards."""

    def __init__(
        self, project_id: str, location: str = "us-central1", **kwargs
    ):
        """Initializes the Scorecards client."""
        super().__init__(project_id=project_id, location=location, **kwargs)

    # --- Basic CRUDL ---

    def list_scorecards(self, parent: str = None) -> List[QaScorecard]:
        """Lists QA Scorecards for the configured parent."""
        parent = parent or self.parent
        return self._list_paginated(f"{parent}/qaScorecards", "qaScorecards")

    def get_scorecard(self, name: str) -> QaScorecard:
        """Gets a single QA scorecard."""
        return self._request("GET", name)

    def create_scorecard(
        self, scorecard_id: str, scorecard: QaScorecard, parent: str = None
    ) -> QaScorecard:
        """Creates a QA scorecard."""
        parent = parent or self.parent
        params = {"qaScorecardId": scorecard_id}
        return self._request(
            "POST", f"{parent}/qaScorecards", data=scorecard, params=params
        )

    # --- Revisions and Questions ---

    def get_latest_revision(self, scorecard_name: str) -> QaScorecardRevision:
        """Convenience method to get the latest revision of a scorecard."""
        revision_name = f"{scorecard_name}/revisions/latest"
        return self._request("GET", revision_name)

    def create_revision(self, scorecard_name: str) -> QaScorecardRevision:
        """Creates a new editable revision for a scorecard."""
        return self._request("POST", f"{scorecard_name}/revisions", data={})

    def list_questions(self, revision_name: str) -> List[QaQuestion]:
        """Lists questions for a specific scorecard revision."""
        return self._list_paginated(
            f"{revision_name}/qaQuestions", "qaQuestions"
        )

    def patch_question(
        self, name: str, question: QaQuestion, update_mask: str = "*"
    ) -> QaQuestion:
        params = {"updateMask": update_mask}
        return self._request("PATCH", name, data=question, params=params)

    def create_question(
        self, revision_name: str, question: QaQuestion
    ) -> QaQuestion:
        return self._request(
            "POST", f"{revision_name}/qaQuestions", data=question
        )

    def delete_question(self, name: str) -> None:
        self._request("DELETE", name)
