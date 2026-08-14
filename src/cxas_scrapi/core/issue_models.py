"""Core IssueModels (Topic Modelling) class for CXAS Scrapi."""

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
from typing import Any

from cxas_scrapi.core.insights import Insights

IssueModel = dict[str, Any]
Issue = dict[str, Any]


class IssueModels(Insights):
    """Core Class for managing CCAI Insights Topic Models (IssueModels)."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        **kwargs: typing.Any,
    ) -> None:
        """Initializes the IssueModels client."""
        super().__init__(project_id=project_id, location=location, **kwargs)

    def list_topic_models(self, parent: str | None = None) -> list[IssueModel]:
        """Lists topic models (issueModels) under the specified parent."""
        return self.list_issue_models(parent=parent)

    def get_topic_model(self, name: str) -> IssueModel:
        """Gets details of a topic model by name or ID."""
        return self.get_issue_model(name=name)

    def create_topic_model_for_app(
        self,
        display_name: str,
        app_name: str | None = None,
        filter_str: str | None = None,
        parent: str | None = None,
        issue_model_id: str | None = None,
        deploy: bool = True,
        model_type: str = "TYPE_V2",
    ) -> IssueModel:
        """Creates an issue model configured to perform topic modelling
        on a CXAS app."""
        parent = parent or self.parent
        filters = []
        if app_name:
            filters.append(f'labels.cxas_app="{app_name}"')
        if filter_str:
            filters.append(f"({filter_str})")
        combined_filter = " AND ".join(filters) if filters else 'medium="CHAT"'

        input_data_config = {
            "medium": "CHAT",
            "filter": combined_filter,
        }
        model = self.create_issue_model(
            display_name=display_name,
            input_data_config=input_data_config,
            parent=parent,
            issue_model_id=issue_model_id,
            model_type=model_type,
        )
        if deploy and model.get("name"):
            try:
                self.deploy_issue_model(model["name"])
            except Exception as e:
                logging.warning(
                    "Could not immediately deploy topic model: %s", e
                )
        return model
