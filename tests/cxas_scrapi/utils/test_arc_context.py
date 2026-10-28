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

"""Tests for resolving the target app/session of ``cxas arc`` commands."""

from __future__ import annotations

import pytest

from cxas_scrapi.utils import arc_context

_PROD = "ces.googleapis.com"
_APP = "projects/p/locations/us/apps/a"


def test_console_url() -> None:
    target = arc_context.resolve_target(
        url=f"https://ces.cloud.google.com/{_APP}", api_endpoint=_PROD
    )

    assert target.app_name == _APP
    assert target.api_endpoint == _PROD
    assert target.session_id_from_url is None


def test_console_url_with_session() -> None:
    target = arc_context.resolve_target(
        url=f"https://ces.cloud.google.com/{_APP}/assistantSessions/s-1",
        api_endpoint=_PROD,
    )

    assert target.app_name == _APP
    assert target.session_id_from_url == "s-1"


def test_console_url_with_session_and_query_and_fragment() -> None:
    target = arc_context.resolve_target(
        url=(
            f"https://ces.cloud.google.com/{_APP}/assistantSessions/s-2"
            "?project=p#tab=chat"
        ),
        api_endpoint=_PROD,
    )

    assert target.app_name == _APP
    assert target.session_id_from_url == "s-2"


def test_cloud_console_deep_link_query_param() -> None:
    url = (
        "https://console.cloud.google.com/customer-engagement-ai/apps/detail"
        "?app=projects%2Fp%2Flocations%2Fus%2Fapps%2Fa&project=p"
    )

    target = arc_context.resolve_target(url=url, api_endpoint=_PROD)

    assert target.app_name == _APP
    assert target.session_id_from_url is None


def test_cloud_console_deep_link_with_encoded_session() -> None:
    url = (
        "https://console.cloud.google.com/customer-engagement-ai/apps/chat"
        "?session=projects%2Fp%2Flocations%2Fus%2Fapps%2Fa%2F"
        "assistantSessions%2Fs-3"
    )

    target = arc_context.resolve_target(url=url, api_endpoint=_PROD)

    assert target.app_name == _APP
    assert target.session_id_from_url == "s-3"


def test_url_without_app_raises() -> None:
    with pytest.raises(ValueError, match="Could not find projects/"):
        arc_context.resolve_target(
            url="https://ces.cloud.google.com/welcome", api_endpoint=_PROD
        )


def test_full_resource_name() -> None:
    target = arc_context.resolve_target(app_name=_APP, api_endpoint=_PROD)

    assert target.app_name == _APP
    assert target.session_id_from_url is None


def test_full_session_name_as_app_name_captures_session() -> None:
    target = arc_context.resolve_target(
        app_name=f"{_APP}/assistantSessions/s-9", api_endpoint=_PROD
    )

    assert target.app_name == _APP
    assert target.session_id_from_url == "s-9"


def test_short_app_id_with_project_and_location() -> None:
    target = arc_context.resolve_target(
        app_name="a", project="p", location="europe-west1", api_endpoint=_PROD
    )

    assert target.app_name == "projects/p/locations/europe-west1/apps/a"


@pytest.mark.parametrize(
    ("endpoint", "expected_location"),
    [
        (_PROD, "us"),
        ("autopush-ces.sandbox.googleapis.com", "us-east1"),
        ("staging-ces.sandbox.googleapis.com", "us-east1"),
    ],
)
def test_short_app_id_uses_default_location_per_endpoint(
    endpoint: str, expected_location: str
) -> None:
    target = arc_context.resolve_target(
        app_name="a", project="p", api_endpoint=endpoint
    )

    assert target.app_name == f"projects/p/locations/{expected_location}/apps/a"
    assert target.api_endpoint == endpoint


def test_short_app_id_without_project_raises() -> None:
    with pytest.raises(ValueError, match="--project"):
        arc_context.resolve_target(app_name="a", api_endpoint=_PROD)


def test_no_inputs_raises() -> None:
    with pytest.raises(ValueError, match="--url or --app-name"):
        arc_context.resolve_target(api_endpoint=_PROD)


def test_url_takes_precedence_over_app_name() -> None:
    target = arc_context.resolve_target(
        url=f"https://ces.cloud.google.com/{_APP}",
        app_name="projects/other/locations/us/apps/b",
        api_endpoint=_PROD,
    )

    assert target.app_name == _APP


@pytest.mark.parametrize(
    "raw",
    [
        "https://ces.googleapis.com/",
        "http://ces.googleapis.com",
        "  ces.googleapis.com  ",
        "ces.googleapis.com//",
    ],
)
def test_api_endpoint_normalisation(raw: str) -> None:
    target = arc_context.resolve_target(app_name=_APP, api_endpoint=raw)

    assert target.api_endpoint == _PROD


def test_api_endpoint_defaults_to_module_constant() -> None:
    target = arc_context.resolve_target(app_name=_APP)

    assert target.api_endpoint == arc_context.DEFAULT_API_ENDPOINT.removeprefix(
        "https://"
    ).rstrip("/")


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (_PROD, "us"),
        ("autopush-ces.sandbox.googleapis.com", "us-east1"),
        ("localhost:8080", "us-east1"),
    ],
)
def test_default_location(endpoint: str, expected: str) -> None:
    assert arc_context.default_location(endpoint) == expected


def test_arc_target_properties() -> None:
    target = arc_context.ArcTarget(
        app_name="projects/my-proj/locations/us-east1/apps/my-app",
        api_endpoint=_PROD,
    )

    assert target.project == "my-proj"
    assert target.location == "us-east1"
    assert target.app_id == "my-app"


def test_arc_target_is_frozen() -> None:
    target = arc_context.ArcTarget(app_name=_APP, api_endpoint=_PROD)

    with pytest.raises(AttributeError, match="cannot assign"):
        target.app_name = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("s-1", "s-1"),
        ("  s-1 ", "s-1"),
        (f"{_APP}/assistantSessions/s-1", "s-1"),
        ("assistantSessions/s-1", "s-1"),
        (f"{_APP}/assistantSessions/s-1?x=1", "s-1"),
    ],
)
def test_session_short_id(value: str, expected: str) -> None:
    assert arc_context.session_short_id(value) == expected
