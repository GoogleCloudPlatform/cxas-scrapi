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

"""Minimal Sensitive Data Protection (DLP) REST client.

Only the calls needed to replay text through an app's inspect/deidentify
templates are implemented, using ``google-auth`` so no extra dependency is
required.
"""

from __future__ import annotations

import enum
import re
import time
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

DLP_ENDPOINT = "https://dlp.googleapis.com/v2"
_TEMPLATE_RE = re.compile(
    r"^(?:organizations|projects)/(?P<owner>[^/]+)/locations/(?P<loc>[^/]+)/"
    r"(?:inspectTemplates|deidentifyTemplates)/[^/]+$"
)
# Conservative per-request limits (the API allows ~0.5 MB per request).
_MAX_ROWS_PER_REQUEST = 400
_MAX_BYTES_PER_REQUEST = 400_000
_MAX_CONVERSATION_MESSAGES = 200
_RETRY_STATUS = {429, 500, 502, 503, 504}


class ReplayMode(str, enum.Enum):
    """How replayed values are packaged for DLP."""

    PLATFORM = "platform"
    """Every value inspected on its own, without keys or surrounding turns
    (mirrors per-field logging redaction)."""

    CONVERSATION = "conversation"
    """Utterances sent as one multi-turn conversation so earlier turns
    provide context; structured values are inspected on their own."""

    KEY_AWARE = "key_aware"
    """Like ``platform`` but structured values carry their field name as a
    table column header, which DLP can use as hotword context."""


class DlpError(RuntimeError):
    pass


def template_location(template_name: str) -> tuple[str, str]:
    """Returns (parent, location) for a template resource name."""
    m = _TEMPLATE_RE.match(template_name or "")
    if not m:
        raise ValueError(f"not a DLP template resource name: '{template_name}'")
    prefix = template_name.split("/locations/", maxsplit=1)[0]
    return f"{prefix}/locations/{m.group('loc')}", m.group("loc")


class DlpClient:
    """Thin wrapper around ``content:deidentify``."""

    def __init__(
        self,
        inspect_template: str,
        deidentify_template: str,
        quota_project: str | None = None,
        credentials: Any = None,
        session: Any = None,
    ) -> None:
        self.inspect_template = inspect_template
        self.deidentify_template = deidentify_template
        self.parent, self.location = template_location(
            deidentify_template or inspect_template
        )
        if quota_project is None:
            owner = self.parent.split("/")[1]
            quota_project = (
                owner if self.parent.startswith("projects/") else None
            )
        self.quota_project = quota_project
        if session is None:
            if credentials is None:
                credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            session = AuthorizedSession(credentials)
        self._session = session

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = (
            {"x-goog-user-project": self.quota_project}
            if self.quota_project
            else {}
        )
        url = f"{DLP_ENDPOINT}/{self.parent}/content:deidentify"
        payload = {
            "inspectTemplateName": self.inspect_template,
            "deidentifyTemplateName": self.deidentify_template,
            **body,
        }
        delay = 1.0
        for attempt in range(5):
            resp = self._session.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in _RETRY_STATUS and attempt < 4:
                time.sleep(delay)
                delay *= 2
                continue
            raise DlpError(
                f"DLP deidentify failed ({resp.status_code}): {resp.text[:500]}"
            )
        raise DlpError("DLP deidentify failed after retries")

    def deidentify_values(
        self, values: list[str], headers: list[str] | None = None
    ) -> list[str]:
        """Deidentifies each value independently.

        Values are sent as rows of a one-column table. With ``headers``
        (``key_aware``), each value instead becomes its own column named
        after its field so the name is available as context.
        """
        if not values:
            return []
        out: list[str] = []
        for batch_start, batch in self._batches(values):
            if headers is None:
                table = {
                    "headers": [{"name": "value"}],
                    "rows": [{"values": [{"stringValue": v}]} for v in batch],
                }
            else:
                names = _unique_headers(
                    headers[batch_start : batch_start + len(batch)]
                )
                table = {
                    "headers": [{"name": n} for n in names],
                    "rows": [{"values": [{"stringValue": v} for v in batch]}],
                }
            res = self._post({"item": {"table": table}})
            rows = res.get("item", {}).get("table", {}).get("rows", [])
            if headers is None:
                got = [
                    (r.get("values") or [{}])[0].get("stringValue", "")
                    for r in rows
                ]
            else:
                got = [
                    c.get("stringValue", "")
                    for c in ((rows or [{}])[0].get("values") or [])
                ]
            if len(got) != len(batch):
                raise DlpError(
                    f"DLP returned {len(got)} values for {len(batch)} inputs"
                )
            out.extend(got)
        return out

    def deidentify_conversation(
        self, messages: list[tuple[str, str]]
    ) -> list[str]:
        """Deidentifies ordered (participant, text) messages as one dialog."""
        out: list[str] = []
        for start in range(0, len(messages), _MAX_CONVERSATION_MESSAGES):
            batch = messages[start : start + _MAX_CONVERSATION_MESSAGES]
            body = {
                "item": {
                    "conversation": {
                        "messages": [
                            {
                                "participantId": _participant(p),
                                "messageType": "CONTENT",
                                "messageParts": [{"text": t}],
                            }
                            for p, t in batch
                        ]
                    }
                }
            }
            res = self._post(body)
            msgs = res.get("item", {}).get("conversation", {}).get("messages")
            got = [
                ((m.get("messageParts") or [{}])[0].get("text"))
                or m.get("content", "")
                for m in (msgs or [])
            ]
            if len(got) != len(batch):
                raise DlpError(
                    f"DLP returned {len(got)} messages for {len(batch)} inputs"
                )
            out.extend(got)
        return out

    @staticmethod
    def _batches(values: list[str]) -> list[tuple[int, list[str]]]:
        batches: list[tuple[int, list[str]]] = []
        start, cur, size = 0, [], 0
        for i, v in enumerate(values):
            v_size = len(v.encode("utf-8")) + 16
            if cur and (
                len(cur) >= _MAX_ROWS_PER_REQUEST
                or size + v_size > _MAX_BYTES_PER_REQUEST
            ):
                batches.append((start, cur))
                start, cur, size = i, [], 0
            cur.append(v)
            size += v_size
        if cur:
            batches.append((start, cur))
        return batches


def _unique_headers(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, n in enumerate(names):
        base = n or f"value_{i}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.append(base if count == 0 else f"{base}_{count}")
    return out


def _participant(role: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", (role or "participant").lower())
    slug = re.sub(r"-+", "-", slug).strip("-") or "participant"
    if not slug[0].isalpha():
        slug = f"p-{slug}"
    return slug[:63].rstrip("-")
