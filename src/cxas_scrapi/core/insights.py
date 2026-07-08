"""Core Insights base class for CXAS Scrapi."""

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

import http
from typing import Any

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from cxas_scrapi.core.common import Common


class Insights(Common):
    """Core Class for managing CCAI Insights Resources and base operations."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        api_version: str = "v1",
        creds_path: str | None = None,
        creds_dict: dict[str, str] | None = None,
        creds: Any = None,
        scope: list[str] | None = None,
        **kwargs,
    ):
        """Initializes the Insights API base client."""
        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )
        self.project_id = project_id
        self.location = location
        self.parent = f"projects/{project_id}/locations/{location}"

        base_endpoint = "contactcenterinsights.googleapis.com"
        if location != "global":
            self._base_url = f"https://{location}-{base_endpoint}/{api_version}"
        else:
            self._base_url = f"https://{base_endpoint}/{api_version}"

        self.session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[
                http.HTTPStatus.TOO_MANY_REQUESTS,
                http.HTTPStatus.INTERNAL_SERVER_ERROR,
                http.HTTPStatus.BAD_GATEWAY,
                http.HTTPStatus.SERVICE_UNAVAILABLE,
                http.HTTPStatus.GATEWAY_TIMEOUT,
            ],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Any:
        """Makes an authenticated HTTP request to the Insights REST API."""
        url = f"{self._base_url}/{path}"

        # Refresh token if necessary using the base Common creds
        if (
            getattr(self.creds, "expired", False)
            or getattr(self.creds, "token", None) is None
        ):
            self.creds.refresh(GoogleAuthRequest())

        headers = {
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-user-project": self.project_id,
            "User-Agent": self.user_agent,
        }

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            json=data,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()

        if response.status_code == 204:
            return None

        return response.json()

    def _list_paginated(
        self,
        path: str,
        response_key: str,
        params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Helper to exhaust a paginated Insights API endpoint."""
        results = []
        page_token = None
        params = params or {}
        while True:
            if page_token:
                params["pageToken"] = page_token
            res = self._request("GET", path, params=params)
            results.extend(res.get(response_key, []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return results

    def list_conversations(
        self,
        filter_str: str | None = None,
        view: str | None = None,
        page_size: int = 100,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """Lists conversations in the configured parent location."""
        path = f"{self.parent}/conversations"
        params = {"pageSize": page_size}
        if filter_str:
            params["filter"] = filter_str
        if view:
            params["view"] = view

        results = []
        page_token = None
        pages = 0
        while pages < max_pages:
            if page_token:
                params["pageToken"] = page_token
            res = self._request("GET", path, params=params)
            results.extend(res.get("conversations", []))
            page_token = res.get("nextPageToken")
            pages += 1
            if not page_token:
                break
        return results

    def get_conversation(
        self, name: str, view: str | None = None
    ) -> dict[str, Any]:
        """Gets a single conversation by name or ID."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/conversations/{name}"
        params = {"view": view} if view else None
        return self._request("GET", name, params=params)

    def create_conversation(
        self,
        conversation: dict[str, Any],
        conversation_id: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Creates or ingests a new conversation."""
        parent = parent or self.parent
        params = (
            {"conversationId": conversation_id} if conversation_id else None
        )
        return self._request(
            "POST", f"{parent}/conversations", data=conversation, params=params
        )

    def delete_conversation(self, name: str, force: bool = True) -> None:
        """Deletes a conversation by resource name or ID."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/conversations/{name}"
        params = {"force": "true" if force else "false"}
        self._request("DELETE", name, params=params)

    def analyze_conversation(
        self,
        name: str,
        annotator_selector: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Triggers single-conversation analysis (e.g. for scorecard
        dry runs)."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/conversations/{name}"
        payload = {}
        if annotator_selector:
            payload["annotatorSelector"] = annotator_selector
        return self._request("POST", f"{name}:analyze", data=payload)

    def bulk_analyze_conversations(
        self,
        parent: str | None = None,
        filter_str: str | None = None,
        annotator_selector: dict[str, Any] | None = None,
        analysis_percentage: float = 100.0,
    ) -> dict[str, Any]:
        """Triggers batch analysis over matching conversations."""
        parent = parent or self.parent
        payload = {
            "filter": filter_str or "",
            "analysisPercentage": analysis_percentage,
        }
        if annotator_selector:
            payload["annotatorSelector"] = annotator_selector
        return self._request(
            "POST", f"{parent}/conversations:bulkAnalyze", data=payload
        )

    # --- Topic Modelling (IssueModels) Operations ---

    def list_issue_models(
        self, parent: str | None = None
    ) -> list[dict[str, Any]]:
        """Lists issue models (topic models) under the specified parent."""
        parent = parent or self.parent
        return self._list_paginated(f"{parent}/issueModels", "issueModels")

    def get_issue_model(self, name: str) -> dict[str, Any]:
        """Gets details of an issue model by name or ID."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        return self._request("GET", name)

    def create_issue_model(
        self,
        display_name: str,
        input_data_config: dict[str, Any] | None = None,
        parent: str | None = None,
        issue_model_id: str | None = None,
        model_type: str = "TYPE_V2",
    ) -> dict[str, Any]:
        """Creates a new issue model for topic modelling."""
        parent = parent or self.parent
        payload = {
            "displayName": display_name,
            "modelType": model_type,
            "inputDataConfig": input_data_config or {"medium": "CHAT"},
        }
        params = {"issueModelId": issue_model_id} if issue_model_id else None
        return self._request(
            "POST", f"{parent}/issueModels", data=payload, params=params
        )

    def update_issue_model(
        self,
        name: str,
        issue_model: dict[str, Any],
        update_mask: str = "*",
    ) -> dict[str, Any]:
        """Updates an issue model."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        params = {"updateMask": update_mask}
        return self._request("PATCH", name, data=issue_model, params=params)

    def delete_issue_model(self, name: str) -> None:
        """Deletes an issue model."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        self._request("DELETE", name)

    def deploy_issue_model(self, name: str) -> dict[str, Any]:
        """Deploys an issue model so it actively tags incoming conversations."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        return self._request("POST", f"{name}:deploy", data={})

    def undeploy_issue_model(self, name: str) -> dict[str, Any]:
        """Undeploys an active issue model."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        return self._request("POST", f"{name}:undeploy", data={})

    def calculate_issue_model_stats(self, name: str) -> dict[str, Any]:
        """Calculates and returns issue model statistics."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/issueModels/{name}"
        return self._request("GET", f"{name}:calculateIssueModelStats")

    def list_issues(self, parent_issue_model: str) -> list[dict[str, Any]]:
        """Lists issues (topics) belonging to an issue model."""
        if not parent_issue_model.startswith("projects/"):
            parent_issue_model = (
                f"{self.parent}/issueModels/{parent_issue_model}"
            )
        return self._list_paginated(f"{parent_issue_model}/issues", "issues")

    def get_issue(self, name: str) -> dict[str, Any]:
        """Gets a single issue (topic) by name."""
        return self._request("GET", name)

    # --- Analysis Rules Operations ---

    def list_analysis_rules(
        self, parent: str | None = None
    ) -> list[dict[str, Any]]:
        """Lists analysis rules in the specified parent."""
        parent = parent or self.parent
        return self._list_paginated(f"{parent}/analysisRules", "analysisRules")

    def get_analysis_rule(self, name: str) -> dict[str, Any]:
        """Gets an analysis rule by name or ID."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/analysisRules/{name}"
        return self._request("GET", name)

    def create_analysis_rule(
        self,
        display_name: str,
        conversation_filter: str,
        annotator_selector: dict[str, Any],
        active: bool = True,
        parent: str | None = None,
        analysis_rule_id: str | None = None,
    ) -> dict[str, Any]:
        """Creates a new analysis rule for automated evaluations."""
        parent = parent or self.parent
        payload = {
            "displayName": display_name,
            "conversationFilter": conversation_filter,
            "annotatorSelector": annotator_selector,
            "active": active,
        }
        params = (
            {"analysisRuleId": analysis_rule_id} if analysis_rule_id else None
        )
        return self._request(
            "POST", f"{parent}/analysisRules", data=payload, params=params
        )

    def update_analysis_rule(
        self,
        name: str,
        analysis_rule: dict[str, Any],
        update_mask: str = "*",
    ) -> dict[str, Any]:
        """Updates an analysis rule."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/analysisRules/{name}"
        params = {"updateMask": update_mask}
        return self._request("PATCH", name, data=analysis_rule, params=params)

    def activate_analysis_rule(
        self, name: str, active: bool = True
    ) -> dict[str, Any]:
        """Convenience method to activate or deactivate an analysis rule."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/analysisRules/{name}"
        params = {"updateMask": "active"}
        return self._request(
            "PATCH", name, data={"active": active}, params=params
        )

    def delete_analysis_rule(self, name: str) -> None:
        """Deletes an analysis rule."""
        if not name.startswith("projects/"):
            name = f"{self.parent}/analysisRules/{name}"
        self._request("DELETE", name)
