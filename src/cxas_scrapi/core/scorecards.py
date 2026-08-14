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

import time
import typing
from typing import Any

from cxas_scrapi.core.insights import Insights

QaScorecard = dict[str, Any]
QaScorecardRevision = dict[str, Any]
QaQuestion = dict[str, Any]


class Scorecards(Insights):
    """Core Class for managing CCAI Insights Scorecards."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        **kwargs: typing.Any,
    ) -> None:
        """Initializes the Scorecards client."""
        super().__init__(project_id=project_id, location=location, **kwargs)

    # --- Basic CRUDL ---

    def list_scorecards(self, parent: str | None = None) -> list[QaScorecard]:
        """Lists QA Scorecards for the configured parent."""
        parent = parent or self.parent
        return self._list_paginated(f"{parent}/qaScorecards", "qaScorecards")

    def get_scorecard(self, name: str) -> QaScorecard:
        """Gets a single QA scorecard."""
        return self._request("GET", name)

    def create_scorecard(
        self,
        scorecard_id: str,
        scorecard: QaScorecard,
        parent: str | None = None,
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

    def list_questions(self, revision_name: str) -> list[QaQuestion]:
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

    def delete_scorecard(self, name: str) -> None:
        """Deletes a scorecard resource."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/qaScorecards/{name}"
        self._request("DELETE", name)

    def create_scorecard_with_questions(
        self,
        scorecard_id: str,
        display_name: str,
        description: str,
        questions: list[QaQuestion],
        parent: str | None = None,
    ) -> tuple[QaScorecardRevision, list[QaQuestion]]:
        """Creates a scorecard, gets/creates its latest revision, and
        populates it with questions."""
        parent = parent or self.parent
        scorecard = self.create_scorecard(
            scorecard_id=scorecard_id,
            scorecard={"displayName": display_name, "description": description},
            parent=parent,
        )
        full_name = scorecard.get(
            "name", f"{parent}/qaScorecards/{scorecard_id}"
        )
        try:
            revision = self.get_latest_revision(full_name)
        except Exception:
            revision = self.create_revision(full_name)
        revision_name = revision["name"]
        created_questions = []
        for q in questions:
            created_questions.append(self.create_question(revision_name, q))
        return revision, created_questions

    def get_revision(self, name: str) -> QaScorecardRevision:
        """Gets a specific QA scorecard revision by its resource name."""
        return self._request("GET", name)

    def list_revisions(self, scorecard_name: str) -> list[QaScorecardRevision]:
        """Lists revisions for a specific scorecard resource."""
        return self._list_paginated(
            f"{scorecard_name}/revisions", "qaScorecardRevisions"
        )

    def tune_revision(self, revision_name: str) -> dict[str, Any]:
        """Triggers tuning/validation for a scorecard revision, moving it from
        EDITABLE (draft) to TRAINING and eventually READY state."""
        return self._request(
            "POST",
            f"{revision_name}:tuneQaScorecardRevision",
            data={"parent": revision_name},
        )

    def deploy_revision(self, revision_name: str) -> dict[str, Any]:
        """Deploys a READY scorecard revision so it can be actively used."""
        return self._request(
            "POST",
            f"{revision_name}:deploy",
            data={"name": revision_name},
        )

    def undeploy_revision(self, revision_name: str) -> dict[str, Any]:
        """Undeploys a deployed scorecard revision."""
        return self._request(
            "POST",
            f"{revision_name}:undeploy",
            data={"name": revision_name},
        )

    def activate_revision(
        self,
        revision_name: str,
        wait_for_ready: bool = False,
        timeout_seconds: int = 120,
        poll_interval: int = 5,
    ) -> dict[str, Any]:
        """Orchestrates activating a scorecard revision by tuning and deploying
        it.

        Args:
            revision_name: Full resource name of the QA scorecard revision.
            wait_for_ready: If True, polls the revision state after tuning up
                to timeout_seconds and calls deploy_revision once READY.
            timeout_seconds: Max seconds to wait when wait_for_ready is True.
            poll_interval: Polling interval in seconds.
        """
        rev = self.get_revision(revision_name)
        state = rev.get("state", "STATE_UNSPECIFIED")

        if state in ["EDITABLE", "STATE_UNSPECIFIED", "TRAINING_FAILED"]:
            self.tune_revision(revision_name)
            if not wait_for_ready:
                return self.get_revision(revision_name)
            state = "TRAINING"

        if wait_for_ready and state == "TRAINING":
            start_time = time.time()
            while time.time() - start_time < timeout_seconds:
                time.sleep(poll_interval)
                rev = self.get_revision(revision_name)
                state = rev.get("state")
                if state in ["READY", "TRAINING_FAILED", "EDITABLE"]:
                    break

        if state == "READY":
            try:
                return self.deploy_revision(revision_name)
            except Exception:
                pass

        return self.get_revision(revision_name)
