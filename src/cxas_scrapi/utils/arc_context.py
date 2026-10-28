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

"""Resolves the target app and session for ``cxas arc`` commands.

Accepts a CX Agent Studio console URL, a full app resource name, or a short
app id plus project/location, and extracts the ``projects/*/locations/*/apps/*``
name together with an optional ``assistantSessions/*`` id.
"""

from __future__ import annotations

import dataclasses
import re
import urllib.parse

from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT

PROD_API_ENDPOINT = "ces.googleapis.com"
DEFAULT_PROD_LOCATION = "us"
DEFAULT_NONPROD_LOCATION = "us-east1"

_APP_RE = re.compile(r"projects/([^/?#]+)/locations/([^/?#]+)/apps/([^/?#]+)")
_SESSION_RE = re.compile(r"assistantSessions/([^/?#]+)")
_CONSOLE_PATH_RE = re.compile(r"customer-engagement-ai", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class ArcTarget:
    """A resolved assistant target.

    Attributes:
        app_name: Full app resource name.
        api_endpoint: API host used for all requests.
        session_id_from_url: Session id embedded in the URL, if any.
    """

    app_name: str
    api_endpoint: str
    session_id_from_url: str | None = None

    @property
    def project(self) -> str:
        return _APP_RE.search(self.app_name).group(1)  # type: ignore[union-attr]

    @property
    def location(self) -> str:
        return _APP_RE.search(self.app_name).group(2)  # type: ignore[union-attr]

    @property
    def app_id(self) -> str:
        return _APP_RE.search(self.app_name).group(3)  # type: ignore[union-attr]


def default_location(api_endpoint: str) -> str:
    """Default location: ``us`` on prod, ``us-east1`` elsewhere."""
    if api_endpoint == PROD_API_ENDPOINT:
        return DEFAULT_PROD_LOCATION
    return DEFAULT_NONPROD_LOCATION


def session_short_id(value: str) -> str:
    """Returns the short session id from a short id or full resource name."""
    match = _SESSION_RE.search(value)
    return match.group(1) if match else value.strip()


def _parse_console_url(url: str) -> tuple[str | None, str | None]:
    """Extracts (app_name, session_id) from a console or API URL."""
    parsed = urllib.parse.urlparse(url)
    haystacks = [urllib.parse.unquote(parsed.path)]
    if _CONSOLE_PATH_RE.search(parsed.path) or parsed.query:
        # Cloud Console deep links keep the resource in query parameters.
        query = urllib.parse.parse_qs(parsed.query)
        haystacks.extend(v for vals in query.values() for v in vals)
        haystacks.append(urllib.parse.unquote(parsed.query))
    if parsed.fragment:
        haystacks.append(urllib.parse.unquote(parsed.fragment))
    app_name = None
    session_id = None
    for text in haystacks:
        app_match = _APP_RE.search(text)
        if app_match and app_name is None:
            app_name = app_match.group(0)
        sess_match = _SESSION_RE.search(text)
        if sess_match and session_id is None:
            session_id = sess_match.group(1)
    return app_name, session_id


def resolve_target(
    *,
    url: str | None = None,
    app_name: str | None = None,
    project: str | None = None,
    location: str | None = None,
    api_endpoint: str | None = None,
) -> ArcTarget:
    """Resolves the app to talk to.

    Args:
        url: Console URL (``https://ces.cloud.google.com/projects/...``) or
            Cloud Console deep link; may include ``/assistantSessions/<id>``.
        app_name: Full resource name or short app id.
        project: Project id or number (needed when ``app_name`` is short).
        location: Location (defaults per endpoint when ``app_name`` is short).
        api_endpoint: API host; defaults to ``CES_API_ENDPOINT`` or prod.

    Raises:
        ValueError: when the app cannot be determined.
    """
    endpoint = (api_endpoint or DEFAULT_API_ENDPOINT).strip()
    endpoint = endpoint.removeprefix("https://").removeprefix("http://")
    endpoint = endpoint.rstrip("/")
    session_id: str | None = None
    resolved: str | None = None

    if url:
        resolved, session_id = _parse_console_url(url)
        if resolved is None:
            raise ValueError(
                "Could not find projects/*/locations/*/apps/* in the URL: "
                f"{url}"
            )

    if app_name:
        match = _APP_RE.search(app_name)
        if match:
            resolved = resolved or match.group(0)
            sess = _SESSION_RE.search(app_name)
            if sess and session_id is None:
                session_id = sess.group(1)
        elif resolved is None:
            if not project:
                raise ValueError(
                    "--project is required when --app-name is a short app id "
                    "(or pass a full projects/*/locations/*/apps/* name)."
                )
            loc = location or default_location(endpoint)
            resolved = f"projects/{project}/locations/{loc}/apps/{app_name}"

    if resolved is None:
        raise ValueError("Provide --url or --app-name to select an app.")

    return ArcTarget(
        app_name=resolved, api_endpoint=endpoint, session_id_from_url=session_id
    )
