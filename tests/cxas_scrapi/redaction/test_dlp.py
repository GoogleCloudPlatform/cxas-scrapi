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

import typing
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.redaction import dlp as dlp_mod
from cxas_scrapi.redaction.dlp import DlpClient, DlpError, template_location

IT = "projects/proj/locations/us/inspectTemplates/i"
DT = "projects/proj/locations/us/deidentifyTemplates/d"


def _resp(status: int, body: typing.Any) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def _table_echo(*_: typing.Any, json: dict, **__: typing.Any) -> MagicMock:
    table = json["item"]["table"]
    rows = [
        {
            "values": [
                {"stringValue": v["stringValue"].upper()} for v in r["values"]
            ]
        }
        for r in table["rows"]
    ]
    return _resp(200, {"item": {"table": {"rows": rows}}})


def test_template_location() -> None:
    assert template_location(DT) == ("projects/proj/locations/us", "us")
    org = "organizations/o/locations/global/inspectTemplates/x"
    assert template_location(org) == (
        "organizations/o/locations/global",
        "global",
    )
    with pytest.raises(ValueError, match="not a DLP template"):
        template_location("bogus")


def test_deidentify_values_one_column_table() -> None:
    session = MagicMock()
    session.post.side_effect = _table_echo
    client = DlpClient(IT, DT, session=session)
    assert client.deidentify_values(["a", "b"]) == ["A", "B"]
    url = session.post.call_args.args[0]
    assert url.endswith("/projects/proj/locations/us/content:deidentify")
    assert session.post.call_args.kwargs["headers"] == {
        "x-goog-user-project": "proj"
    }
    payload = session.post.call_args.kwargs["json"]
    assert payload["inspectTemplateName"] == IT
    assert payload["item"]["table"]["headers"] == [{"name": "value"}]
    assert client.deidentify_values([]) == []


def test_deidentify_values_key_aware_headers_are_unique() -> None:
    session = MagicMock()
    session.post.side_effect = _table_echo
    client = DlpClient(IT, DT, session=session)
    out = client.deidentify_values(["1", "2", "3"], headers=["cvv", "cvv", ""])
    assert out == ["1", "2", "3"]
    headers = session.post.call_args.kwargs["json"]["item"]["table"]["headers"]
    assert [h["name"] for h in headers] == ["cvv", "cvv_1", "value_2"]


def test_batching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dlp_mod, "_MAX_ROWS_PER_REQUEST", 2)
    session = MagicMock()
    session.post.side_effect = _table_echo
    client = DlpClient(IT, DT, session=session)
    assert client.deidentify_values(["a", "b", "c"]) == ["A", "B", "C"]
    assert session.post.call_count == 2


def test_deidentify_conversation() -> None:
    session = MagicMock()

    def echo(*_: typing.Any, json: dict, **__: typing.Any) -> MagicMock:
        msgs = json["item"]["conversation"]["messages"]
        return _resp(
            200,
            {
                "item": {
                    "conversation": {
                        "messages": [
                            {"messageParts": [{"text": "x"}]} for _ in msgs
                        ]
                    }
                }
            },
        )

    session.post.side_effect = echo
    client = DlpClient(IT, DT, session=session)
    assert client.deidentify_conversation([("Billing Agent", "a")]) == ["x"]
    msg = session.post.call_args.kwargs["json"]["item"]["conversation"][
        "messages"
    ][0]
    assert msg["participantId"] == "billing-agent"
    assert msg["messageType"] == "CONTENT"


def test_retries_then_raises() -> None:
    session = MagicMock()
    session.post.side_effect = [_resp(503, "busy"), _resp(400, "bad")]
    client = DlpClient(IT, DT, session=session)
    with (
        patch("cxas_scrapi.redaction.dlp.time.sleep"),
        pytest.raises(DlpError, match="400"),
    ):
        client.deidentify_values(["a"])
    assert session.post.call_count == 2


def test_mismatched_response_raises() -> None:
    session = MagicMock()
    session.post.return_value = _resp(200, {"item": {"table": {"rows": []}}})
    client = DlpClient(IT, DT, session=session)
    with pytest.raises(DlpError, match="returned 0 values"):
        client.deidentify_values(["a"])


def test_participant_slug() -> None:
    assert dlp_mod._participant("User") == "user"
    assert dlp_mod._participant("9 lives!") == "p-9-lives"
    assert dlp_mod._participant("") == "participant"
