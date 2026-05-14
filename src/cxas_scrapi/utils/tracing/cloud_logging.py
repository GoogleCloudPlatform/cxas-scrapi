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
import re
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
        return self.batch_fetch(
            conversation_ids=[conversation_id],
            start_time=start_time,
            end_time=end_time,
            level=level,
        ).get(conversation_id, [])

    def batch_fetch(
        self,
        conversation_ids: list[str],
        start_time: datetime.datetime | None,
        end_time: datetime.datetime | None,
        level: str = "WARNING",
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetches logs for many conversations in a single Cloud Logging
        query and groups the rows by conversation_id.

        Cuts down N round-trips to 1 when computing aggregate trace stats
        across a time window.
        """
        if not conversation_ids:
            return {}
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

        # Render one filter per conversation against the template, then OR
        # them together so a single Cloud Logging query returns all rows.
        per_conv_filters = [
            "("
            + self.filter_template.format(
                level=level.upper(),
                start_time=padded_start.isoformat(),
                end_time=padded_end.isoformat(),
                conversation_id=cid,
            )
            + ")"
            for cid in conversation_ids
        ]
        combined = " OR ".join(per_conv_filters)

        rows: list[dict[str, Any]] = []
        try:
            for entry in self._client.list_entries(
                filter_=combined, order_by="timestamp asc"
            ):
                rows.append(_entry_to_row(entry))
        except Exception as e:
            logger.warning(f"Cloud Logging query failed: {e}")
            return {cid: [] for cid in conversation_ids}

        grouped: dict[str, list[dict[str, Any]]] = {
            cid: [] for cid in conversation_ids
        }
        # Single-conversation queries don't need attribution — assign every
        # returned row to that conversation. For multi-conversation batch
        # queries, attribute by the conversation_id label (or, as a fallback,
        # by scanning the message body).
        if len(conversation_ids) == 1:
            grouped[conversation_ids[0]] = rows
        else:
            for r in rows:
                cid = _row_conversation_id(r)
                if cid in grouped:
                    grouped[cid].append(r)
        for v in grouped.values():
            v.sort(key=lambda r: r.get("timestamp") or "")
        return grouped


def _row_conversation_id(row: dict[str, Any]) -> str | None:
    """Best-effort extraction of `conversation_id` from a log row's labels
    or its message body, for grouping batch results back per conversation.
    """
    labels = row.get("labels") or {}
    if isinstance(labels, dict) and labels.get("conversation_id"):
        return labels["conversation_id"]
    msg = row.get("message")
    if isinstance(msg, str):
        # Heuristic match for jsonPayload.conversation_id="..."; fall back to
        # any UUID-like substring.
        m = re.search(r"conversation_id[\"']?\s*[:=]\s*[\"']?([\w-]{8,})", msg)
        if m:
            return m.group(1)
    return None


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
