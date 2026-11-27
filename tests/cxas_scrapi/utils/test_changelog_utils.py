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

"""Unit tests for ChangelogUtils."""

import typing

import pytest

from cxas_scrapi.utils.changelog_utils import ChangelogUtils
from cxas_scrapi.utils.gemini import GeminiGenerate


@pytest.mark.parametrize(
    ("env_loc", "gemini_loc", "expected_loc"),
    [
        (None, None, "us-central1"),
        ("europe-west4", None, "europe-west4"),
        ("europe-west4", "   ", "europe-west4"),
        ("europe-west4", "asia-northeast1", "asia-northeast1"),
    ],
)
def test_summarize_changelogs_resolves_gemini_location(
    monkeypatch: pytest.MonkeyPatch,
    env_loc: str | None,
    gemini_loc: str | None,
    expected_loc: str,
) -> None:
    if env_loc is not None:
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", env_loc)

    captured: list[tuple[str | None, str]] = []

    def _fake_generate(self: GeminiGenerate, **_kwargs: typing.Any) -> str:
        captured.append((self.project_id, self.location))
        return "- Created tool 'lookup_order'"

    monkeypatch.setattr(GeminiGenerate, "generate", _fake_generate)

    summary = ChangelogUtils.summarize_changelogs(
        vertex_client_or_project=None,
        changelogs=[
            {
                "action": "Create",
                "resourceType": "Tool",
                "displayName": "lookup_order",
                "description": "Order lookup tool",
            }
        ],
        project_id="proj-1",
        gemini_location=gemini_loc,
    )

    assert summary == "- Created tool 'lookup_order'"
    assert captured == [("proj-1", expected_loc)]
