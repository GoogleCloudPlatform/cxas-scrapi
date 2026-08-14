"""Core AnalysisRules class for CXAS Scrapi."""

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

import typing
from typing import Any

from cxas_scrapi.core.insights import Insights

AnalysisRule = dict[str, Any]


class AnalysisRules(Insights):
    """Core Class for managing CCAI Insights Analysis Rules."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        **kwargs: typing.Any,
    ) -> None:
        """Initializes the AnalysisRules client."""
        super().__init__(project_id=project_id, location=location, **kwargs)

    def create_rule_for_app(
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
    ) -> AnalysisRule:
        """Creates an automated analysis rule targeting a CXAS app."""
        parent = parent or self.parent
        filters = []
        if app_name:
            filters.append(f'agent_id="{app_name}"')
        if filter_str:
            filters.append(f"({filter_str})")
        combined_filter = " AND ".join(filters) if filters else 'medium="CHAT"'

        annotator_selector: dict[str, Any] = {
            "runSentimentAnnotator": run_sentiment,
            "runIssueModelAnnotator": bool(issue_models),
        }
        if run_summarization:
            annotator_selector["runSummarizationAnnotator"] = True
            annotator_selector["summarizationConfig"] = {
                "summarizationModel": "BASELINE_MODEL"
            }
        if scorecard_revisions:
            annotator_selector["runQaAnnotator"] = True
            annotator_selector["qaConfig"] = {
                "scorecardList": {"qaScorecardRevisions": scorecard_revisions}
            }
        if issue_models:
            annotator_selector["issueModels"] = issue_models

        return self.create_analysis_rule(
            display_name=display_name,
            conversation_filter=combined_filter,
            annotator_selector=annotator_selector,
            active=active,
            parent=parent,
            analysis_rule_id=rule_id,
        )
