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

from typing import Any, Dict, List, Optional

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

from cxas_scrapi.core.common import Common


class Insights(Common):
    """Core Class for managing CCAI Insights Resources and base operations."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        api_version: str = "v1",
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
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

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
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
        }

        response = requests.request(
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
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
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
