"""Per-conversation Cloud Logging client.

Builds a Cloud Logging filter from a template (in `trace.yaml`) populated with
the conversation_id, time bounds, and severity threshold; merges entries
chronologically and exposes a small structured row for downstream rendering.
"""

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

import datetime
import json
import logging
from typing import Any

try:
    from google.cloud import logging_v2
except ImportError:
    logging_v2 = None

logger = logging.getLogger(__name__)


class CloudLogsClient:
    """Thin wrapper over `google.cloud.logging_v2` for conversation logs."""

    def __init__(
        self,
        project_id: str,
        filter_template: str,
        time_padding_seconds: int = 30,
        credentials: Any = None,
    ):
        if logging_v2 is None:
            raise ImportError(
                "google-cloud-logging is required for `cxas trace logs`. "
                "Install with: pip install google-cloud-logging"
            )
        self.project_id = project_id
        self.filter_template = filter_template
        self.time_padding_seconds = time_padding_seconds
        self._client = logging_v2.Client(
            project=project_id, credentials=credentials
        )

    def fetch(
        self,
        conversation_id: str,
        start_time: datetime.datetime | None,
        end_time: datetime.datetime | None,
        level: str = "WARNING",
    ) -> list[dict[str, Any]]:
        """Fetches log entries for a single conversation."""
        if start_time is None:
            start_time = datetime.datetime.utcnow() - datetime.timedelta(
                hours=24
            )
        if end_time is None:
            end_time = datetime.datetime.utcnow()

        padded_start = start_time - datetime.timedelta(
            seconds=self.time_padding_seconds
        )
        padded_end = end_time + datetime.timedelta(
            seconds=self.time_padding_seconds
        )

        log_filter = self.filter_template.format(
            level=level.upper(),
            start_time=_to_rfc3339(padded_start),
            end_time=_to_rfc3339(padded_end),
            conversation_id=conversation_id,
        )

        rows: list[dict[str, Any]] = []
        try:
            for entry in self._client.list_entries(
                filter_=log_filter, order_by="timestamp asc"
            ):
                rows.append(_entry_to_row(entry))
        except Exception as e:
            logger.warning(
                f"Cloud Logging query failed for {conversation_id}: {e}"
            )
            return []

        rows.sort(key=lambda r: r.get("timestamp") or "")
        return rows


def _to_rfc3339(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def _entry_to_row(entry: Any) -> dict[str, Any]:
    """Converts a Cloud Logging entry to a flat dict for rendering."""
    payload = getattr(entry, "payload", None)
    if isinstance(payload, dict):
        message = payload.get("message") or payload
    else:
        message = payload

    ts = getattr(entry, "timestamp", None)
    return {
        "timestamp": ts.isoformat() if ts else None,
        "severity": getattr(entry, "severity", None),
        "log_name": getattr(entry, "log_name", None),
        "resource_type": (
            getattr(entry.resource, "type", None)
            if getattr(entry, "resource", None)
            else None
        ),
        "message": _stringify(message),
        "labels": dict(getattr(entry, "labels", {}) or {}),
        "trace": getattr(entry, "trace", None),
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)
