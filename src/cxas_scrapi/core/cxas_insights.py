"""CXAS Insights high-level facade for app-centric metrics orchestration."""

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
from typing import Any

import pandas as pd

import cxas_scrapi.utils.scorecard_template_manager as template_manager
from cxas_scrapi.utils.insights_utils import InsightsUtils
from cxas_scrapi.utils.metrics_extractor import MetricsExtractor


class CXASInsights:
    """App-centric facade for CCAI / CXAS Insights.

    This class allows developers to manage metrics, scorecards, and analysis
    rules directly linked to a CX Agent Studio App ID, abstracting away
    underlying resource naming and multi-class orchestration.
    """

    def __init__(self, app_id: str, **kwargs: Any) -> None:
        """Initializes the CXAS Insights facade.

        Args:
            app_id: The full resource name of the app (e.g.
              projects/P/locations/L/apps/A).
            **kwargs: Additional arguments passed to underlying clients.
        """
        self.app_id = app_id
        self.project_id, self.location = self._parse_app_id(app_id)
        self.agent_uuid = app_id.rsplit("/", maxsplit=1)[-1]

        # Initialize the underlying utilities
        self.utils = InsightsUtils(
            project_id=self.project_id,
            location=self.location,
            **kwargs,
        )
        self.metrics_extractor = MetricsExtractor(
            insights_client=self.utils.scorecards_client
        )
        self._templates: list[str] = []

    def _parse_app_id(self, app_id: str) -> tuple[str, str]:
        """Parses project and location from an app resource name."""
        parts = app_id.split("/")
        if len(parts) < 4 or parts[0] != "projects" or parts[2] != "locations":
            raise ValueError(
                f"Invalid app_id format: {app_id}. "
                "Expected projects/PROJ/locations/LOC/apps/APP_ID"
            )
        return parts[1], parts[3]

    def add(self, scorecard_template_path: str) -> None:
        """Adds a scorecard template to be deployed for this app."""
        self._templates.append(scorecard_template_path)

    def deploy(
        self, wait: bool = True, tune_filter: str | None = None
    ) -> list[str]:
        """Syncs, tunes, and deploys all added scorecards, and sets up analysis

        rules tied specifically to this app's traffic.
        """
        deployed_revisions = []
        app_traffic_filter = f'agent_id = "{self.agent_uuid}"'

        for template_path in self._templates:
            logging.info("Deploying scorecard template: %s", template_path)
            scorecard_dict, questions = (
                template_manager.load_scorecard_template(template_path)
            )
            display_name = scorecard_dict.get("displayName", "sc_template")
            sc_id = "sc-" + display_name.lower().replace(" ", "-")[:20]

            revision_name = self.utils.import_scorecard(
                scorecard_dict=scorecard_dict,
                questions=questions,
                target_scorecard_id=sc_id,
            )
            deployed_revisions.append(revision_name)

            # Setup Analysis Rule for this specific App
            rule_id = f"rule-{self.agent_uuid}-{sc_id}"[:63]
            logging.info(
                "Setting up analysis rule %s for app traffic...", rule_id
            )
            self.utils.setup_analysis_rule_for_app(
                display_name=f"Auto-rule for {display_name}",
                app_name=self.agent_uuid,
                filter_str=app_traffic_filter,
                scorecard_revisions=[revision_name],
                rule_id=rule_id,
            )

        return deployed_revisions

    def get_report(self, filter_str: str | None = None) -> pd.DataFrame:
        """Fetches evaluation results specifically for this app."""
        app_filter = f'agent_id = "{self.agent_uuid}"'
        if filter_str:
            app_filter = f"({app_filter}) AND ({filter_str})"

        return self.metrics_extractor.get_evaluation_results(
            filter_str=app_filter
        )
